from __future__ import annotations

import json
import logging
import ssl
import sys
import traceback
from pathlib import Path
from urllib.error import HTTPError, URLError


def _failure_phase(stage: str, errors: list[dict]) -> str:
    if stage == "read":
        return "read_response"
    if stage == "http":
        return "http_response"

    # Classify from the captured frames, without adding another SSL context or
    # changing urllib's opener, certificate validation or proxy selection.
    # Prefer the current exception's frames over earlier exception context.
    for error in errors:
        if error["http_status"] is not None:
            return "http_response"
        calls = {(frame["module"], frame["function"]) for frame in error["frames"]}
        if (("http.client", "_create_https_context") in calls
            or any(module == "ssl" and function in {
                "create_default_context", "_create_unverified_context", "load_default_certs",
                "_load_windows_store_certs", "__new__",
            } for module, function in calls)):
            return "ssl_init"
        if any(module == "ssl" and function in {"do_handshake", "wrap_socket", "wrap_bio", "_create"}
               for module, function in calls):
            return "ssl_handshake"
        if any((module == "http.client" and function in {"getresponse", "begin", "_read_status", "read"})
               or (module == "ssl" and function in {"read", "recv", "recv_into"})
               for module, function in calls):
            return "read_response"
        if any((module == "http.client" and function in {"connect", "_tunnel", "send"})
               or (module == "socket" and function in {"create_connection", "getaddrinfo"})
               for module, function in calls):
            return "connect"
    return stage


def log_transport_failure(logger: logging.Logger, exc: BaseException, stage: str) -> None:
    # Do not log exception text, URLs, headers, payloads or locals: they may
    # contain API keys. Frame locations still identify the failing SSL call.
    errors = []
    seen = set()
    while isinstance(exc, BaseException) and id(exc) not in seen:
        seen.add(id(exc))
        errors.append({
            "type": type(exc).__name__,
            "errno": getattr(exc, "errno", None),
            "winerror": getattr(exc, "winerror", None),
            "http_status": exc.code if isinstance(exc, HTTPError) else None,
            "ssl_library": getattr(exc, "library", None) if isinstance(exc, ssl.SSLError) else None,
            "ssl_reason": getattr(exc, "reason", None) if isinstance(exc, ssl.SSLError) else None,
            "verify_code": getattr(exc, "verify_code", None) if isinstance(exc, ssl.SSLError) else None,
            "frames": [
                {"module": frame.f_globals.get("__name__", ""),
                 "file": Path(frame.f_code.co_filename).name, "line": lineno,
                 "function": frame.f_code.co_name}
                for frame, lineno in traceback.walk_tb(exc.__traceback__)
            ],
        })
        cause = exc.__cause__
        if cause is None and isinstance(exc, URLError):
            cause = exc.reason
        if not isinstance(cause, BaseException) and not exc.__suppress_context__:
            cause = exc.__context__
        exc = cause
    logger.error("NETWORK_TRANSPORT_FAILURE %s", json.dumps({
        "stage": stage,
        "phase": _failure_phase(stage, errors),
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "errors": errors,
    }, ensure_ascii=True))
