from __future__ import annotations

import json
import time
from html.parser import HTMLParser
from threading import Event
from typing import Any, Callable
from urllib import error, parse, request

from .workshop import DETAILS_ENDPOINT


class AnalysisCancelled(Exception):
    pass


class WorkshopCommentsError(ValueError):
    pass


def check_cancelled(cancel: Event) -> None:
    if cancel.is_set():
        raise AnalysisCancelled()


class CommentParser(HTMLParser):
    """Read Steam's comment fragment, retaining nested text and Unix timestamps."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.comments: list[dict[str, Any]] = []
        self._depth = 0
        self._root_depth = 0
        self._text_depth = 0
        self._author = False
        self._comment: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = str(values.get("class") or "").split()
        if tag == "div":
            self._depth += 1
        if "commentthread_comment" in classes:
            comment_id = str(values.get("id") or "").removeprefix("comment_")
            if tag != "div" or self._comment is not None or not comment_id.isdigit():
                raise WorkshopCommentsError("aiAnalysis.invalidComments")
            self._root_depth = self._depth
            self._comment = {"id": comment_id, "author": "", "posted_at": 0, "text": ""}
        if self._comment is None:
            return
        if "commentthread_comment_text" in classes:
            self._text_depth = self._depth
        if "commentthread_author_link" in classes:
            self._author = True
        timestamp = values.get("data-timestamp")
        if "commentthread_comment_timestamp" in classes and timestamp:
            try:
                self._comment["posted_at"] = int(timestamp) * 1000
            except ValueError as exc:
                raise WorkshopCommentsError("aiAnalysis.invalidComments") from exc
        if self._text_depth:
            if tag == "br":
                self._comment["text"] += "\n"
            elif tag == "img":
                self._comment["text"] += str(values.get("alt") or values.get("title") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._author = False
        if tag != "div":
            return
        if self._text_depth == self._depth:
            self._text_depth = 0
        if self._comment is not None and self._root_depth == self._depth:
            record = self._comment
            self._comment = None
            if not record["posted_at"] or not record["text"].strip():
                raise WorkshopCommentsError("aiAnalysis.invalidComments")
            record["text"] = record["text"].strip()
            record["author"] = record["author"].strip()
            self.comments.append(record)
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._comment is not None:
            if self._text_depth:
                self._comment["text"] += data
            elif self._author:
                self._comment["author"] += data

    def records(self, fragment: str) -> list[dict[str, Any]]:
        self.feed(fragment)
        self.close()
        if self._comment is not None:
            raise WorkshopCommentsError("aiAnalysis.invalidComments")
        return self.comments


class WorkshopCommentsClient:
    PAGE_SIZE = 100

    def _json(self, http_request: request.Request, cancel: Event) -> dict[str, Any]:
        for attempt in range(3):
            check_cancelled(cancel)
            try:
                with request.urlopen(http_request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise WorkshopCommentsError("aiAnalysis.workshopFailed")
                return payload
            except error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise WorkshopCommentsError("aiAnalysis.workshopFailed") from exc
            except (error.URLError, OSError, UnicodeError, json.JSONDecodeError) as exc:
                if attempt == 2:
                    raise WorkshopCommentsError("aiAnalysis.workshopFailed") from exc
            if cancel.wait(0.5 * (attempt + 1)):
                raise AnalysisCancelled()
        raise WorkshopCommentsError("aiAnalysis.workshopFailed")

    def recent_comments(
        self,
        workshop_id: str,
        app_id: int,
        cancel: Event,
        progress: Callable[[int], None],
    ) -> dict[str, Any]:
        if not workshop_id.isdigit():
            raise WorkshopCommentsError("aiAnalysis.invalidItem")
        data = parse.urlencode({"itemcount": "1", "publishedfileids[0]": workshop_id}).encode("ascii")
        details = self._json(request.Request(
            DETAILS_ENDPOINT, data=data,
            headers={"User-Agent": "WycccModManager/1.2", "Content-Type": "application/x-www-form-urlencoded"},
        ), cancel).get("response", {}).get("publishedfiledetails", [])
        detail = next((item for item in details if str(item.get("publishedfileid")) == workshop_id), {})
        if int(detail.get("result") or 0) != 1 or int(detail.get("consumer_app_id") or 0) != app_id:
            raise WorkshopCommentsError("aiAnalysis.invalidItem")
        updated_at = int(detail.get("time_updated") or 0) * 1000
        creator = str(detail.get("creator") or "")
        if updated_at <= 0 or not creator.isdigit():
            raise WorkshopCommentsError("aiAnalysis.missingUpdateTime")

        records: dict[str, dict[str, Any]] = {}
        start = 0
        page = 0
        while True:
            check_cancelled(cancel)
            query = parse.urlencode({"start": start, "count": self.PAGE_SIZE, "sort": "desc"})
            endpoint = f"https://steamcommunity.com/comment/PublishedFile_Public/render/{creator}/{workshop_id}/?{query}"
            payload = self._json(request.Request(endpoint, headers={"User-Agent": "WycccModManager/1.2"}), cancel)
            if payload.get("success") is not True or "total_count" not in payload:
                raise WorkshopCommentsError("aiAnalysis.workshopFailed")
            total = int(payload["total_count"])
            if int(payload.get("start", -1)) != start or total < 0:
                raise WorkshopCommentsError("aiAnalysis.incompleteComments")
            comments = CommentParser().records(str(payload.get("comments_html") or ""))
            if not comments and total > start:
                raise WorkshopCommentsError("aiAnalysis.incompleteComments")
            timestamps = [comment["posted_at"] for comment in comments]
            if timestamps != sorted(timestamps, reverse=True):
                raise WorkshopCommentsError("aiAnalysis.invalidComments")
            for comment in comments:
                if comment["posted_at"] > updated_at:
                    comment["url"] = f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}#comment_{comment['id']}"
                    records[comment["id"]] = comment
            page += 1
            progress(len(records))
            start += len(comments)
            if any(timestamp <= updated_at for timestamp in timestamps) or start >= total:
                break
            if not comments:
                raise WorkshopCommentsError("aiAnalysis.incompleteComments")
            # Steam's offset pagination is paced to avoid hammering the community service.
            if cancel.wait(0.2):
                raise AnalysisCancelled()
        return {
            "title": str(detail.get("title") or ""),
            "updated_at": updated_at,
            "fetched_at": int(time.time() * 1000),
            "pages": page,
            "comments": list(records.values()),
        }
