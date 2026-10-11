from __future__ import annotations

import json
import threading
import time
from unittest.mock import PropertyMock, patch
from urllib.error import HTTPError

import pytest

from backend.ai_service import analyze_mod_comment_batch, summarize_mod_feedback
from backend.api import API
from backend.mod_ai_analysis import ModAIAnalysisService, analysis_batches
from backend.workshop_comments import CommentParser, WorkshopCommentsClient, WorkshopCommentsError
from tests.helpers import make_asset


SETTINGS = {
    "ai_enabled": True, "ai_base_url": "https://example.invalid/v1", "ai_model": "configured-model",
    "ai_api_key": "private-test-key", "ai_temperature": 0.2, "language": "zh-CN",
}
APP_ID = 1142710


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def html_comment(comment_id, timestamp, text="crashes <b>on load</b>"):
    return (
        f'<div class="commentthread_comment responsive_body_text" id="comment_{comment_id}">'
        '<div class="commentthread_comment_content"><div class="commentthread_comment_author">'
        '<a class="commentthread_author_link"><bdi>Player &amp; Author</bdi></a>'
        f'<div class="commentthread_comment_timestamp" data-timestamp="{timestamp}">date</div></div>'
        f'<div class="commentthread_comment_text">{text}</div></div></div>'
    )


def metadata(updated_at=1000, **values):
    return {"response": {"publishedfiledetails": [{
        "publishedfileid": "123", "creator": "76561198000000000", "consumer_app_id": APP_ID,
        "result": 1, "time_updated": updated_at, "title": "Sample MOD", **values,
    }]}}


def comments_data(count=3):
    return {
        "title": "Sample MOD", "updated_at": 1_000_000, "fetched_at": 1_500_000,
        "comments": [{"id": str(index), "author": "Player", "posted_at": 1_100_000 + index,
                      "text": "crashes" if index == 0 else "works fine",
                      "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id=123#comment_{index}"}
                     for index in range(count)],
    }


def ai_response(http_request, **_kwargs):
    body = json.loads(http_request.data)
    data = json.loads(body["messages"][1]["content"])
    if "comments" in data:
        content = {"comments": [{
            "id": comment["id"], "translation": "加载时崩溃" if comment["text"] == "crashes" else "运行正常",
            "category": "issue" if comment["text"] == "crashes" else "positive",
            "summary": "玩家报告加载崩溃" if comment["text"] == "crashes" else "",
        } for comment in data["comments"]]}
    else:
        content = {"summary": "Sample MOD：玩家报告加载时崩溃，尚未验证。"}
    return Response({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]})


def finished(service):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = service.status()
        if not state["running"]:
            return state
        time.sleep(0.005)
    pytest.fail("Analysis worker did not finish")


def test_parser_reads_nested_text_entities_linebreaks_and_exact_unix_time():
    comments = CommentParser().records(html_comment("99", 1100, "Crash &amp; freeze<br>after <b>loading</b>"))
    assert comments == [{"id": "99", "posted_at": 1_100_000, "author": "Player & Author",
                         "text": "Crash & freeze\nafter loading"}]


def test_paginates_newest_first_deduplicates_and_excludes_equal_or_older_comments():
    pages = [metadata(),
             {"success": True, "start": 0, "total_count": 5, "comments_html": html_comment(4, 1200) + html_comment(3, 1100)},
             {"success": True, "start": 2, "total_count": 5, "comments_html": html_comment(3, 1100) + html_comment(2, 1000)}]
    client = WorkshopCommentsClient()
    client.PAGE_SIZE = 2
    progress = []
    cancel = threading.Event()
    with patch.object(client, "_json", side_effect=pages) as fetch, patch.object(cancel, "wait", return_value=False):
        result = client.recent_comments("123", APP_ID, cancel, progress.append)
    assert result["updated_at"] == 1_000_000
    assert [comment["id"] for comment in result["comments"]] == ["4", "3"]
    assert result["pages"] == 2
    assert progress == [2, 2]
    assert "start=2&count=2&sort=desc" in fetch.call_args.args[0].full_url
    assert result["comments"][0]["url"].endswith("#comment_4")
    assert fetch.call_count == 3


@pytest.mark.parametrize("detail, page, code", [
    (metadata(), {"success": True, "start": 0, "total_count": 1, "comments_html": ""}, "incompleteComments"),
    (metadata(), {"success": False}, "workshopFailed"),
    (metadata(), {"success": True, "start": 0, "total_count": 2, "comments_html": html_comment(1, 1100) + html_comment(2, 1200)}, "invalidComments"),
    (metadata(), {"success": True, "start": 0, "total_count": 1, "comments_html": html_comment(1, 0)}, "invalidComments"),
    (metadata(0), None, "missingUpdateTime"),
    (metadata(consumer_app_id=999), None, "invalidItem"),
    (metadata(result=9), None, "invalidItem"),
])
def test_inaccessible_or_incomplete_workshop_data_is_not_treated_as_no_issues(detail, page, code):
    client = WorkshopCommentsClient()
    with patch.object(client, "_json", side_effect=[detail, page]), pytest.raises(WorkshopCommentsError, match=code):
        client.recent_comments("123", APP_ID, threading.Event(), lambda _count: None)


def test_transient_workshop_error_retries_same_request():
    client = WorkshopCommentsClient()
    cancel = threading.Event()
    failure = HTTPError("https://steamcommunity.com", 503, "busy", {}, None)
    with patch("backend.workshop_comments.request.urlopen", side_effect=[failure, Response({"success": True})]) as fetch, \
            patch.object(cancel, "wait", return_value=False):
        from urllib.request import Request
        http_request = Request("https://steamcommunity.com")
        assert client._json(http_request, cancel) == {"success": True}
    assert fetch.call_count == 2
    assert all(call.args[0] is http_request for call in fetch.call_args_list)


@pytest.mark.parametrize("language, name", [("zh-CN", "Simplified Chinese"), ("en-US", "English"),
                                            ("ko-KR", "Korean"), ("ru-RU", "Russian"), ("ja-JP", "Japanese"), ("es-ES", "Spanish")])
def test_uses_configured_model_and_language_and_translates_all_comments(language, name):
    comments = comments_data()["comments"]
    settings = {**SETTINGS, "language": language}
    with patch("backend.ai_service.request.urlopen", side_effect=ai_response) as fetch:
        result = analyze_mod_comment_batch({"name": "Sample MOD"}, comments, settings)
    assert len(result) == 3
    assert result[0]["is_issue"] is True
    assert result[0]["category"] == "issue"
    assert result[1]["is_issue"] is False and result[1]["translation"]
    assert result[0]["url"] == comments[0]["url"]
    sent = fetch.call_args.args[0]
    assert sent.full_url == "https://example.invalid/v1/chat/completions"
    assert sent.headers["Authorization"] == "Bearer private-test-key"
    body = json.loads(sent.data)
    assert body["model"] == "configured-model" and body["temperature"] == 0.2
    assert name in body["messages"][0]["content"]
    assert "untrusted data" in body["messages"][0]["content"]
    assert "Translate EVERY" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"])["comments"] == comments


@pytest.mark.parametrize("items", [[],
    [{"id": "invented", "translation": "测试", "category": "issue", "summary": "错误"}],
    [{"id": "0", "translation": "测试", "category": "invalid", "summary": "错误"}],
    [{"id": "0", "translation": "测试", "category": ["issue"], "summary": "错误"}],
    [{"id": "0", "translation": "测试", "is_issue": True, "summary": "错误"}],
    [{"id": "0", "translation": "", "category": "issue", "summary": "错误"}],
    [{"id": "0", "translation": "测试", "category": "issue", "summary": ""}],
])
def test_rejects_missing_comments_hallucinated_ids_and_invalid_issue_classification(items):
    with patch("backend.ai_service._chat_completion", return_value=json.dumps({"comments": items})), \
            pytest.raises(ValueError, match="invalidAIResponse"):
        analyze_mod_comment_batch({}, comments_data(1)["comments"], SETTINGS)


@pytest.mark.parametrize("category, text", [
    ("question", "恐惧之王军团MOD，派系名显示PLACEHOLD是正常的吗？"),
    ("positive", "Thanks! Updated and everything works fine now."),
    ("other", "Bhashiva is now ready to try."),
])
def test_non_issue_categories_cannot_be_promoted_to_reports(category, text):
    comment = {**comments_data(1)["comments"][0], "text": text}
    response = {"comments": [{"id": "0", "translation": text, "category": category,
                              "is_issue": True, "summary": "Should not appear"}]}
    with patch("backend.ai_service._chat_completion", return_value=json.dumps(response)) as chat:
        result = analyze_mod_comment_batch({}, [comment], SETTINGS)
    assert result[0]["category"] == category
    assert result[0]["is_issue"] is False and result[0]["summary"] == ""
    prompt = chat.call_args.args[1]
    assert "PLACEHOLD" in prompt
    assert "experienced malfunction" in prompt


def test_only_issue_comments_and_affected_mods_reach_summaries(tmp_path):
    service = ModAIAnalysisService()
    assets = [make_asset(tmp_path / f"{key}.pack", key, "workshop", str(index))
              for index, key in enumerate(["Bug MOD", "Question MOD", "Positive MOD", "Empty MOD"], 1)]
    bug, question, positive = [
        {**comments_data(1)["comments"][0], "id": str(index), "text": text}
        for index, text in enumerate([
            "With this mod enabled I get 6 building slots instead of 8; disabling it restores 8.",
            "恐惧之王军团MOD，派系名显示PLACEHOLD是正常的吗？",
            "Thanks, the latest version works fine!",
        ])
    ]
    pages = [{**comments_data(), "comments": [bug, question, positive]},
             {**comments_data(), "comments": [question]},
             {**comments_data(), "comments": [positive]}, comments_data(0)]

    def response(http_request, **_kwargs):
        data = json.loads(json.loads(http_request.data)["messages"][1]["content"])
        categories = {"0": "issue", "1": "question", "2": "positive"}
        return Response({"choices": [{"message": {"content": json.dumps({"comments": [
            {"id": item["id"], "translation": item["text"], "category": categories[item["id"]],
             "is_issue": item["id"] == "0", "summary": "Building slots reduced from 8 to 6"}
            for item in data["comments"]
        ]})}}]})

    with patch.object(service.client, "recent_comments", side_effect=lambda wid, *_args: pages[int(wid) - 1]), \
            patch("backend.ai_service.request.urlopen", side_effect=response), \
            patch("backend.mod_ai_analysis.summarize_mod_feedback", return_value="Building slots reduced from 8 to 6") as summarize:
        service.start(assets, SETTINGS, "warhammer3", APP_ID)
        result = finished(service)
    assert result["status"] == "completed" and result["analyzed_comments"] == 5
    assert [item["status"] for item in result["results"]] == ["issues", "clean", "clean", "no_comments"]
    assert [item["id"] for item in result["results"][0]["comments"]] == ["0"]
    assert all(not item["comments"] and not item["summary"] for item in result["results"][1:])
    summarize.assert_called_once()
    per_mod = summarize.call_args.args[0]
    assert len(per_mod) == 1
    assert per_mod[0]["comment"] == bug["text"]
    assert per_mod[0]["mod"] == "Bug MOD"
    assert "PLACEHOLD" not in json.dumps(per_mod)
    assert "Thanks" not in json.dumps(per_mod)
    assert "summary" not in result and "summary_error" not in result


def test_questions_and_positive_feedback_do_not_trigger_summary_requests(tmp_path):
    service = ModAIAnalysisService()
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    page = comments_data(2)
    page["comments"][0]["text"] = "Is this compatible with SFO?"
    page["comments"][1]["text"] = "Works fine, thanks!"
    response = {"comments": [
        {"id": item["id"], "translation": item["text"],
         "category": "question" if item["id"] == "0" else "positive", "summary": ""}
        for item in page["comments"]
    ]}
    with patch.object(service.client, "recent_comments", return_value=page), \
            patch("backend.ai_service._chat_completion", return_value=json.dumps(response)), \
            patch("backend.mod_ai_analysis.summarize_mod_feedback") as summarize:
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        result = finished(service)
    assert result["status"] == "completed" and result["analyzed_comments"] == 2
    assert "summary" not in result
    assert result["results"][0]["status"] == "clean"
    assert result["results"][0]["comments"] == []
    summarize.assert_not_called()


def test_batches_keep_every_record_and_do_not_truncate_long_comments():
    records = [{"id": str(index), "text": "x" * (20000 if index == 1 else 1000)} for index in range(80)]
    batches = list(analysis_batches(records))
    assert len(batches) > 3
    assert [item for batch in batches for item in batch] == records
    assert any(len(batch) == 1 and len(batch[0]["text"]) == 20000 for batch in batches)


def test_api_analyzes_only_snapshot_selection_deduplicates_workshop_and_never_saves_mods(tmp_path):
    api = API(tmp_path / "state")
    api.settings_service.save(SETTINGS)
    assets = [make_asset(tmp_path / f"{key}.pack", key, "data", wid) for key, wid in
              [("a", "123"), ("b", "123"), ("local", ""), ("disabled", "456")]]
    assets[0].file_updated_at = 9_999_999_999_999
    api._assets = {asset.id: asset for asset in assets}
    before = api.state_repository.get_current_playset()
    try:
        with patch.object(api.mod_ai_analysis.client, "recent_comments", return_value=comments_data()) as fetch, \
                patch("backend.ai_service.request.urlopen", side_effect=ai_response):
            start = api.call("start_mod_ai_analysis", [["a", "b", "local", "a"]])
            assert start["ok"], start
            api._assets["a"].alias = "changed while running"
            result = finished(api.mod_ai_analysis)
        assert result["status"] == "completed" and result["progress"] == 100
        assert result["total_mods"] == result["completed_mods"] == 3
        assert result["fetched_comments"] == result["analyzed_comments"] == 3
        assert result["results"][0]["status"] == "issues"
        assert result["results"][0]["updated_at"] == 1_000_000
        assert result["results"][0]["mod_ids"] == ["a", "b"]
        assert result["results"][0]["name"] == "a / b"
        assert result["results"][1]["status"] == "skipped"
        assert result["results"][0]["summary"]
        fetch.assert_called_once()
        assert fetch.call_args.args[:2] == ("123", APP_ID)
        assert "private-test-key" not in json.dumps(result)
        assert api.state_repository.get_current_playset() == before
        assert not api.state_repository.list_user_mod_data()
        assert api.call("get_mod_ai_analysis", [result["id"]])["data"] == result
    finally:
        api.close()


def test_mod_failure_continues_other_mods_and_only_summarizes_their_own_issue_reports(tmp_path):
    service = ModAIAnalysisService()
    assets = [make_asset(tmp_path / f"{key}.pack", key, "workshop", wid) for key, wid in [("broken", "123"), ("ok", "456")]]
    def fetch(workshop_id, *_args):
        if workshop_id == "123":
            raise WorkshopCommentsError("aiAnalysis.workshopFailed")
        return comments_data()

    with patch.object(service.client, "recent_comments", side_effect=fetch), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response) as ai:
        service.start(assets, SETTINGS, "warhammer3", APP_ID)
        result = finished(service)
    assert result["status"] == "partial" and result["completed_mods"] == 2
    assert [item["status"] for item in result["results"]] == ["error", "issues"]
    prompt = json.loads(json.loads(ai.call_args.args[0].data)["messages"][1]["content"])
    assert len(prompt["results"]) == 1
    assert prompt["results"][0]["mod"] == "ok" and prompt["results"][0]["is_issue"]
    assert result["results"][1]["summary"]
    assert ai.call_count == 2


def test_cancellation_waits_for_inflight_request_and_prevents_further_ai_calls(tmp_path):
    service = ModAIAnalysisService()
    entered, release = threading.Event(), threading.Event()

    def blocked(*_args):
        entered.set()
        assert release.wait(2)
        return []

    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    with patch.object(service.client, "recent_comments", return_value=comments_data()), \
            patch("backend.mod_ai_analysis.analyze_mod_comment_batch", side_effect=blocked) as analyze, \
            patch("backend.mod_ai_analysis.summarize_mod_feedback") as summarize:
        started = service.start([asset], SETTINGS, "warhammer3", APP_ID)
        assert entered.wait(2)
        with pytest.raises(ValueError, match="alreadyRunning"):
            service.start([asset], SETTINGS, "warhammer3", APP_ID)
        with pytest.raises(ValueError, match="staleSession"):
            service.cancel("obsolete")
        assert service.cancel(started["id"])["phase"] == "cancelling"
        release.set()
        result = finished(service)
    assert result["status"] == "cancelled" and result["progress"] < 100
    assert result["results"][0]["status"] == "cancelled"
    analyze.assert_called_once()
    summarize.assert_not_called()


def test_single_mod_uses_five_concurrent_configured_model_requests_without_losing_order(tmp_path):
    service = ModAIAnalysisService()
    asset = make_asset(tmp_path / "large.pack", "large", "workshop", "123")
    page = comments_data(125)
    for index, comment in enumerate(page["comments"]):
        comment["text"] = "crashes" if index % 25 == 0 else "works fine"
    lock = threading.Lock()
    all_five, release = threading.Event(), threading.Event()
    active, peak = 0, 0
    inputs, models = [], []

    def response(http_request, **kwargs):
        nonlocal active, peak
        body = json.loads(http_request.data)
        data = json.loads(body["messages"][1]["content"])
        with lock:
            active += 1
            peak = max(peak, active)
            models.append(body["model"])
            if "comments" in data:
                inputs.extend(comment["id"] for comment in data["comments"])
                if active == 5:
                    all_five.set()
        try:
            if "comments" in data:
                assert release.wait(5)
                # Finish later batches first to exercise stable source ordering.
                time.sleep((125 - int(data["comments"][0]["id"])) / 10000)
            return ai_response(http_request, **kwargs)
        finally:
            with lock:
                active -= 1

    with patch.object(service.client, "recent_comments", return_value=page), \
            patch("backend.ai_service.request.urlopen", side_effect=response) as ai:
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        try:
            assert all_five.wait(5)
            state = service.status()
            assert state["active_workers"] == state["max_workers"] == 5
            assert state["phase"] == "analyzing"
        finally:
            release.set()
            result = finished(service)
    assert result["status"] == "completed" and result["progress"] == 100
    assert result["analyzed_comments"] == result["fetched_comments"] == 125
    assert [comment["id"] for comment in result["results"][0]["comments"]] == ["0", "25", "50", "75", "100"]
    assert sorted(inputs, key=int) == [str(index) for index in range(125)]
    assert peak == 5 and active == result["active_workers"] == 0
    assert ai.call_count == 6 and models == [SETTINGS["ai_model"]] * 6


def test_mod_summaries_and_other_mod_batches_share_five_request_slots(tmp_path):
    service = ModAIAnalysisService()
    assets = [make_asset(tmp_path / f"mod-{index}.pack", f"mod-{index}", "workshop", str(index))
              for index in range(1, 7)]
    lock = threading.Lock()
    first_five, mixed_requests, release = threading.Event(), threading.Event(), threading.Event()
    active, peak = 0, 0
    active_types = []

    def response(http_request, **kwargs):
        nonlocal active, peak
        data = json.loads(json.loads(http_request.data)["messages"][1]["content"])
        kind = "comment" if "comments" in data else "summary"
        with lock:
            active += 1
            peak = max(peak, active)
            active_types.append(kind)
            if active == 5 and active_types.count("comment") == 5:
                first_five.set()
            if active == 5 and "summary" in active_types and "comment" in active_types:
                mixed_requests.set()
        try:
            if kind == "comment" and data["mod"]["workshop_id"] == "1":
                assert first_five.wait(5)
            else:
                assert release.wait(5)
            return ai_response(http_request, **kwargs)
        finally:
            with lock:
                active -= 1
                active_types.remove(kind)

    with patch.object(service.client, "recent_comments", return_value=comments_data(1)), \
            patch("backend.ai_service.request.urlopen", side_effect=response):
        service.start(assets, SETTINGS, "warhammer3", APP_ID)
        try:
            assert mixed_requests.wait(5)
            assert service.status()["active_workers"] == 5
        finally:
            release.set()
            result = finished(service)
    assert peak == 5 and active == result["active_workers"] == 0
    assert result["status"] == "completed" and result["completed_mods"] == 6
    assert [item["mod_ids"] for item in result["results"]] == [[asset.id] for asset in assets]
    assert all(item["summary"] and item["issue_count"] == 1 for item in result["results"])


def test_cancelling_five_requests_skips_queued_batches_and_keeps_completed_text_in_cache(tmp_path):
    service = ModAIAnalysisService(tmp_path / "cache")
    asset = make_asset(tmp_path / "large.pack", "large", "workshop", "123")
    page = comments_data(150)
    lock = threading.Lock()
    all_five, release = threading.Event(), threading.Event()
    inputs = []

    def response(http_request, **kwargs):
        data = json.loads(json.loads(http_request.data)["messages"][1]["content"])
        with lock:
            inputs.extend(comment["id"] for comment in data["comments"])
            if len(inputs) == 125:
                all_five.set()
        assert release.wait(5)
        return ai_response(http_request, **kwargs)

    with patch.object(service.client, "recent_comments", return_value=page), \
            patch("backend.ai_service.request.urlopen", side_effect=response) as ai, \
            patch("backend.mod_ai_analysis.summarize_mod_feedback") as summarize:
        started = service.start([asset], SETTINGS, "warhammer3", APP_ID)
        try:
            assert all_five.wait(5)
            assert service.cancel(started["id"])["phase"] == "cancelling"
            assert service.status()["running"] and service.status()["active_workers"] == 5
        finally:
            release.set()
            result = finished(service)
    assert ai.call_count == 5 and sorted(inputs, key=int) == [str(index) for index in range(125)]
    assert result["status"] == "cancelled" and result["active_workers"] == 0
    summarize.assert_not_called()
    with patch.object(service.client, "recent_comments", return_value=page), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response) as ai:
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        restarted = finished(service)
    batches = [json.loads(json.loads(call.args[0].data)["messages"][1]["content"])
               for call in ai.call_args_list]
    assert [comment["id"] for batch in batches if "comments" in batch for comment in batch["comments"]] == [
        str(index) for index in range(125, 150)
    ]
    assert restarted["cached_comments"] == 125 and restarted["analyzed_comments"] == 150
    assert restarted["status"] == "completed" and restarted["active_workers"] == 0


def test_partial_batch_failure_retains_analyzed_count_without_non_issue_comments(tmp_path):
    service = ModAIAnalysisService()
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    def batch(mod, comments, settings):
        if comments[0]["id"] == "25":
            raise ValueError("aiAnalysis.invalidAIResponse")
        return [{**comment, "translation": "评论", "is_issue": False, "summary": ""} for comment in comments]

    with patch.object(service.client, "recent_comments", return_value=comments_data(50)), \
            patch("backend.mod_ai_analysis.analyze_mod_comment_batch", side_effect=batch), \
            patch("backend.mod_ai_analysis.summarize_mod_feedback") as summarize:
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        result = finished(service)
    assert result["status"] == "partial"
    item = result["results"][0]
    assert item["status"] == "error" and item["comment_count"] == 50 and item["analyzed_count"] == 25
    assert item["comments"] == [] and result["analyzed_comments"] == 25
    summarize.assert_not_called()


def test_partial_batch_failure_retains_only_completed_issue_reports_without_an_overall_summary(tmp_path):
    service = ModAIAnalysisService()
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    def batch(_mod, comments, _settings):
        if comments[0]["id"] == "25":
            raise ValueError("aiAnalysis.invalidAIResponse")
        return [{**comment, "translation": "Crash on load" if index == 0 else "Works fine",
                 "category": "issue" if index == 0 else "positive", "is_issue": index == 0,
                 "summary": "Crash on load" if index == 0 else ""}
                for index, comment in enumerate(comments)]

    with patch.object(service.client, "recent_comments", return_value=comments_data(50)), \
            patch("backend.mod_ai_analysis.analyze_mod_comment_batch", side_effect=batch), \
            patch("backend.mod_ai_analysis.summarize_mod_feedback", return_value="Crash on load") as summarize:
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        result = finished(service)
    item = result["results"][0]
    assert result["status"] == "partial" and result["analyzed_comments"] == 25
    assert item["status"] == "error" and item["issue_count"] == len(item["comments"]) == 1
    assert item["comments"][0]["id"] == "0"
    assert "summary" not in result
    summarize.assert_not_called()


def test_no_recent_comments_does_not_make_ai_requests(tmp_path):
    service = ModAIAnalysisService()
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    with patch.object(service.client, "recent_comments", return_value=comments_data(0)), \
            patch("backend.ai_service.request.urlopen") as ai:
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        result = finished(service)
    assert result["status"] == "completed" and result["results"][0]["status"] == "no_comments"
    ai.assert_not_called()


def test_large_mod_summary_reduces_batches_without_omitting_reports():
    service = ModAIAnalysisService()
    records = [{"mod": "large MOD", "comment": f"report-{index}", "is_issue": True,
                "issue": f"issue-{index}"} for index in range(80)]
    with patch("backend.mod_ai_analysis.summarize_mod_feedback", return_value="Summary") as summarize:
        assert service._summarize(records, SETTINGS, threading.Event()) == "Summary"
    assert [record for call in summarize.call_args_list[:4] for record in call.args[0]] == records
    assert summarize.call_count == 5


def test_cached_classification_translation_and_summaries_are_reused_after_restart(tmp_path):
    cache_dir = tmp_path / "cache"
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    first = ModAIAnalysisService(cache_dir)
    with patch.object(first.client, "recent_comments", return_value=comments_data()), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response) as ai:
        first.start([asset], SETTINGS, "warhammer3", APP_ID)
        previous = finished(first)
    assert previous["status"] == "completed" and previous["cached_comments"] == 0
    assert ai.call_count == 2 and previous["cache_bytes"] > 0
    first.close()
    restored = ModAIAnalysisService(cache_dir)
    with patch.object(restored.client, "recent_comments", return_value=comments_data()) as workshop, \
            patch("backend.ai_service.request.urlopen") as ai:
        restored.start([asset], SETTINGS, "warhammer3", APP_ID)
        current = finished(restored)
    assert current["status"] == "completed" and current["cached_comments"] == 3
    assert current["cached_summaries"] == 1
    assert current["results"] == previous["results"]
    workshop.assert_called_once()
    ai.assert_not_called()
    assert all("private-test-key" not in path.read_text(encoding="utf-8") for path in cache_dir.glob("*.json"))


def test_new_comments_use_cached_old_text_and_only_send_new_text_to_ai(tmp_path):
    service = ModAIAnalysisService(tmp_path / "cache")
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    with patch.object(service.client, "recent_comments", return_value=comments_data()), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response):
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        assert finished(service)["status"] == "completed"
    with patch.object(service.client, "recent_comments", return_value=comments_data(4)), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response) as ai:
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        current = finished(service)
    assert current["status"] == "completed" and current["analyzed_comments"] == 4
    assert current["cached_comments"] == 3 and current["cached_summaries"] == 1
    ai.assert_called_once()
    sent = json.loads(json.loads(ai.call_args.args[0].data)["messages"][1]["content"])
    assert [item["id"] for item in sent["comments"]] == ["3"]


@pytest.mark.parametrize("change", ["edited_comment", "mod_update", "language", "model"])
def test_changed_text_update_language_or_model_cannot_reuse_stale_analysis(tmp_path, change):
    service = ModAIAnalysisService(tmp_path / "cache")
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    with patch.object(service.client, "recent_comments", return_value=comments_data()), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response):
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        assert finished(service)["status"] == "completed"
    page = comments_data()
    settings = dict(SETTINGS)
    if change == "edited_comment":
        page["comments"][1]["text"] = "Now works, thanks!"
    elif change == "mod_update":
        page["updated_at"] += 100
    elif change == "language":
        settings["language"] = "en-US"
    else:
        settings["ai_model"] = "other-model"
    with patch.object(service.client, "recent_comments", return_value=page), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response) as ai:
        service.start([asset], settings, "warhammer3", APP_ID)
        current = finished(service)
    assert current["status"] == "completed"
    assert current["cached_comments"] == (2 if change == "edited_comment" else 0)
    sent = json.loads(json.loads(ai.call_args_list[0].args[0].data)["messages"][1]["content"])
    assert [item["id"] for item in sent["comments"]] == (["1"] if change == "edited_comment" else ["0", "1", "2"])


def test_mod_summary_failure_is_explicit_and_preserves_individual_reports(tmp_path):
    service = ModAIAnalysisService()
    asset = make_asset(tmp_path / "a.pack", "a", "workshop", "123")
    with patch.object(service.client, "recent_comments", return_value=comments_data()), \
            patch("backend.ai_service.request.urlopen", side_effect=ai_response), \
            patch("backend.mod_ai_analysis.summarize_mod_feedback", side_effect=ValueError("bad response")):
        service.start([asset], SETTINGS, "warhammer3", APP_ID)
        result = finished(service)
    assert result["status"] == "partial"
    item = result["results"][0]
    assert item["status"] == "error" and item["error"] == "aiAnalysis.aiFailed"
    assert item["summary"] == "" and item["issue_count"] == len(item["comments"]) == 1


def test_invalid_ai_config_and_selection_fail_before_any_network_request(tmp_path):
    api = API(tmp_path / "state")
    try:
        api._assets = {"a": make_asset(tmp_path / "a.pack", "a", "data", "123")}
        with patch("backend.workshop_comments.request.urlopen") as network:
            assert api.call("start_mod_ai_analysis", [["a"]])["error"]["message"] == "aiAnalysis.configureAI"
            api.settings_service.save(SETTINGS)
            assert api.call("start_mod_ai_analysis", [[]])["error"]["message"] == "aiAnalysis.noEnabledMods"
            assert api.call("start_mod_ai_analysis", ["a"])["error"]["message"] == "aiAnalysis.invalidSelection"
        network.assert_not_called()
    finally:
        api.close()


def test_summary_prompt_distinguishes_user_reports_from_proven_conflicts():
    with patch("backend.ai_service._chat_completion", return_value='{"summary":"result"}') as chat:
        assert summarize_mod_feedback([{"mod": "a", "status": "error"}], SETTINGS) == "result"
    assert "not verified game tests" in chat.call_args.args[1]
    assert "Do not invent conflicts" in chat.call_args.args[1]
    assert "unassessed, not healthy" in chat.call_args.args[1]
    assert "Include ONLY concrete malfunction reports" in chat.call_args.args[1]
    assert "Exclude inquiries, praise, thanks, positive or neutral feedback" in chat.call_args.args[1]


def test_progress_and_cancellation_remain_available_during_launch_diagnostics(tmp_path):
    api = API(tmp_path / "state")
    api.settings_service.save(SETTINGS)
    api._assets = {"a": make_asset(tmp_path / "a.pack", "a", "workshop", "123")}
    entered, release = threading.Event(), threading.Event()

    def blocked(*_args):
        entered.set()
        assert release.wait(2)
        return comments_data(0)

    try:
        with patch.object(api.mod_ai_analysis.client, "recent_comments", side_effect=blocked):
            start = api.call("start_mod_ai_analysis", [["a"]])["data"]
            assert entered.wait(2)
            with patch.object(type(api.diagnostics), "running", new_callable=PropertyMock, return_value=True):
                assert api.call("get_mod_ai_analysis", [start["id"]])["ok"]
                assert api.call("cancel_mod_ai_analysis", [start["id"]])["ok"]
            release.set()
            assert finished(api.mod_ai_analysis)["status"] == "cancelled"
    finally:
        release.set()
        api.close()
