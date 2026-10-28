from __future__ import annotations

import json
import socket
import ssl
import subprocess
import sys
import threading
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch
from urllib import error, request

import pytest

from backend.ai_service import _chat_completion
from backend.workshop import AuthorProfileError, WorkshopMetadataService
from backend.workshop_collections import fetch_workshop_collection


def _call_source(source, tmp_path, endpoint="https://example.invalid/v1"):
    if source == "ai":
        _chat_completion({
            "ai_enabled": True, "ai_base_url": endpoint,
            "ai_model": "diagnostic-model", "ai_api_key": "do-not-log-this-key",
        }, "private-system-prompt", "private-user-prompt")
    elif source == "collection":
        with patch("backend.workshop_collections.COLLECTION_DETAILS_ENDPOINT", endpoint):
            fetch_workshop_collection("123456", app_id=1142710)
    elif source == "metadata":
        with patch("backend.workshop.DETAILS_ENDPOINT", endpoint):
            service = WorkshopMetadataService(tmp_path / "workshop.json")
            service._refresh_english_details({}, ["123456"])
    else:
        with patch("backend.workshop.PROFILE_ENDPOINT", endpoint):
            with patch("backend.workshop.AUTHOR_HTTP_MAX_ATTEMPTS", 1):
                WorkshopMetadataService._fetch_author_profile("123456")


def _diagnostic(caplog):
    records = [record for record in caplog.records
               if record.getMessage().startswith("NETWORK_TRANSPORT_FAILURE ")]
    assert len(records) == 1
    assert records[0].exc_info is None
    return json.loads(records[0].getMessage().split(" ", 1)[1])


@pytest.mark.parametrize("source", ["ai", "collection", "metadata", "author"])
def test_missing_ca_at_real_ssl_boundary_is_logged_for_each_feature(tmp_path, caplog, source):
    # Inject a missing CA only at SSL initialization. This verifies the shared
    # failure boundary, not a claim about the affected user's environment.
    create_context = ssl.create_default_context

    def missing_ca_context(*args, **kwargs):
        return create_context(*args, cafile=str(tmp_path / "missing-ca.pem"), **kwargs)

    with patch.object(request, "_opener", None):
        with patch("ssl._create_default_https_context", side_effect=missing_ca_context):
            with pytest.raises((ValueError, FileNotFoundError, AuthorProfileError),
                               match=r"\[Errno 2\] No such file or directory"):
                _call_source(source, tmp_path)

    diagnostic = _diagnostic(caplog)
    assert diagnostic["stage"] == "open"
    assert diagnostic["phase"] == "ssl_init"
    assert diagnostic["errors"][0]["type"] == "FileNotFoundError"
    assert diagnostic["errors"][0]["errno"] == 2
    assert any(frame["module"] == "ssl" for frame in diagnostic["errors"][0]["frames"])
    for sensitive in (str(tmp_path), "do-not-log-this-key", "private-system-prompt", "private-user-prompt"):
        assert sensitive not in caplog.text


@pytest.mark.parametrize("source", ["ai", "collection", "metadata", "author"])
def test_socket_connection_failure_keeps_wrapped_python_stack(tmp_path, caplog, source):
    opener = request.build_opener(request.ProxyHandler({}))
    with patch.object(request, "_opener", opener):
        with patch("socket.create_connection", side_effect=FileNotFoundError(2, "No such file or directory")):
            with pytest.raises((ValueError, OSError, AuthorProfileError), match=r"\[Errno 2\]"):
                _call_source(source, tmp_path)

    diagnostic = _diagnostic(caplog)
    assert diagnostic["phase"] == "connect"
    assert diagnostic["errors"][-1]["errno"] == 2
    assert any(frame["module"] == "http.client" and frame["function"] == "connect"
               for error in diagnostic["errors"] for frame in error["frames"])


@pytest.mark.parametrize("source", ["ai", "collection", "metadata", "author"])
def test_response_read_failure_is_not_classified_as_ssl_initialization(tmp_path, caplog, source):
    class Response(BytesIO):
        def read(self, *_args):
            raise FileNotFoundError(2, "No such file or directory")

    with patch.object(request, "urlopen", return_value=Response()):
        with pytest.raises((ValueError, FileNotFoundError, AuthorProfileError), match=r"\[Errno 2\]"):
            _call_source(source, tmp_path)

    diagnostic = _diagnostic(caplog)
    assert diagnostic["stage"] == "read"
    assert diagnostic["phase"] == "read_response"
    assert diagnostic["errors"][0]["errno"] == 2


@contextmanager
def _server_closing_during_tls_handshake():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(3)

        def close_on_client_hello():
            connection, _ = server.accept()
            with connection:
                connection.settimeout(3)
                connection.recv(8192)

        worker = threading.Thread(target=close_on_client_hello, daemon=True)
        worker.start()
        try:
            yield f"https://127.0.0.1:{server.getsockname()[1]}/v1"
        finally:
            worker.join(timeout=4)
            assert not worker.is_alive()


@pytest.mark.parametrize("source", ["ai", "collection", "metadata", "author"])
def test_actual_tls_handshake_failure_is_distinct_from_tcp_connection(tmp_path, caplog, source):
    opener = request.build_opener(request.ProxyHandler({}))
    with patch.object(request, "_opener", opener):
        with _server_closing_during_tls_handshake() as endpoint:
            with pytest.raises((ValueError, OSError, AuthorProfileError)):
                _call_source(source, tmp_path, endpoint)

    diagnostic = _diagnostic(caplog)
    assert diagnostic["phase"] == "ssl_handshake"
    assert any(frame["function"] == "do_handshake"
               for error in diagnostic["errors"] for frame in error["frames"])


def test_errno2_stack_reaches_the_real_application_log(tmp_path):
    source = """
import ssl
import sys
from pathlib import Path
from unittest.mock import patch
from backend.ai_service import _chat_completion
from main import configure_logging

data_dir = Path(sys.argv[1])
configure_logging(data_dir)
create_context = ssl.create_default_context
def missing_ca_context(*args, **kwargs):
    return create_context(*args, cafile=str(data_dir / 'missing-ca.pem'), **kwargs)
with patch('ssl._create_default_https_context', side_effect=missing_ca_context):
    try:
        _chat_completion({
            'ai_enabled': True, 'ai_base_url': 'https://example.invalid/private-url-token/v1',
            'ai_model': 'diagnostic-model', 'ai_api_key': 'private-key',
        }, 'private-system-prompt', 'private-user-prompt')
    except ValueError:
        pass
    else:
        raise AssertionError('Expected an SSL initialization failure')
"""
    subprocess.run([sys.executable, "-c", source, str(tmp_path)], check=True,
                   capture_output=True, timeout=15)
    log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    line = next(line for line in log.splitlines() if "NETWORK_TRANSPORT_FAILURE " in line)
    diagnostic = json.loads(line.split("NETWORK_TRANSPORT_FAILURE ", 1)[1])
    assert diagnostic["phase"] == "ssl_init"
    assert diagnostic["errors"][0]["errno"] == 2
    assert any(frame["module"] == "ssl" for frame in diagnostic["errors"][0]["frames"])
    for sensitive in (str(tmp_path), "private-key", "private-url-token", "private-system-prompt", "private-user-prompt"):
        assert sensitive not in log


def test_ssl_failure_after_an_http_error_uses_the_current_stack(tmp_path, caplog):
    create_context = ssl.create_default_context

    def missing_ca_context(*args, **kwargs):
        return create_context(*args, cafile=str(tmp_path / "missing-ca.pem"), **kwargs)

    with patch.object(request, "_opener", None):
        with patch("ssl._create_default_https_context", side_effect=missing_ca_context):
            with pytest.raises(ValueError, match=r"\[Errno 2\]"):
                try:
                    raise error.HTTPError("https://example.invalid/v1", 401, "Unauthorized", {}, None)
                except error.HTTPError:
                    _call_source("ai", tmp_path)

    diagnostic = _diagnostic(caplog)
    assert diagnostic["phase"] == "ssl_init"
    assert diagnostic["errors"][0]["type"] == "FileNotFoundError"
    assert any(item["http_status"] == 401 for item in diagnostic["errors"])
