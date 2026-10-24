"""Small HTTP helpers with bounded retries.

Network calls in the manager must not hammer Steam or an AI endpoint. These
helpers retry only transient failures and never retry a definitive 4xx.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request
from urllib.request import urlopen

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class Response:
    """Minimal response wrapper used by tests and callers that need raw bytes."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self, *_args: Any) -> bytes:
        return self.payload


def request_bytes(
    http_request: request.Request,
    *,
    timeout: float = 20,
    retries: int = 2,
    backoff: float = 0.5,
    urlopen_func: Any = None,
) -> bytes:
    """Fetch a URL with bounded exponential backoff for transient failures."""
    opener = urlopen_func or urlopen
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with opener(http_request, timeout=timeout) as response:
                return response.read()
        except error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_STATUS or attempt == attempts - 1:
                raise
        except (error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise
        if backoff > 0:
            time.sleep(backoff * (attempt + 1))
    raise last_error or RuntimeError("HTTP request failed")


def request_json(
    http_request: request.Request,
    *,
    timeout: float = 20,
    retries: int = 2,
    backoff: float = 0.5,
    urlopen_func: Any = None,
) -> Any:
    payload = request_bytes(
        http_request,
        timeout=timeout,
        retries=retries,
        backoff=backoff,
        urlopen_func=urlopen_func,
    )
    return json.loads(payload.decode("utf-8", errors="replace"))
