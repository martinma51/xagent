"""Request URLs are logged without credential-bearing query values."""

import logging
from logging.config import dictConfig

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xagent.web.app import global_exception_handler, validation_exception_handler
from xagent.web.logging_config import setup_logging
from xagent.web.request_log_redaction import (
    REDACTED,
    RequestTargetRedactionFilter,
    install_request_log_redaction,
    redact_request_target,
)

SECRET = "guest.jwt.SECRET-VALUE"


@pytest.mark.parametrize(
    "target, expected",
    [
        ("/api/files/public/preview/abc", "/api/files/public/preview/abc"),
        (
            f"/api/widget/chat/ws/1?token={SECRET}",
            f"/api/widget/chat/ws/1?token={REDACTED}",
        ),
        (
            f"/api/files/public/preview/abc?relative_path=a.png&ticket={SECRET}",
            f"/api/files/public/preview/abc?relative_path=a.png&ticket={REDACTED}",
        ),
        (
            f"/widget/chat/x?guest_id=g1&widget_key={SECRET}&embed_ticket={SECRET}",
            f"/widget/chat/x?guest_id=g1&widget_key={REDACTED}&embed_ticket={REDACTED}",
        ),
        (
            f"/cb?code={SECRET}&state={SECRET}&access_token={SECRET}",
            f"/cb?code={REDACTED}&state={REDACTED}&access_token={REDACTED}",
        ),
        (
            f"https://host.example/a?api_key={SECRET}&page=2",
            f"https://host.example/a?api_key={REDACTED}&page=2",
        ),
        ("/a?token=", "/a?token="),
        ("/a?validation_only=true", "/a?validation_only=true"),
    ],
)
def test_redact_request_target(target, expected):
    assert redact_request_target(target) == expected


def test_access_log_record_args_are_redacted():
    # uvicorn.access: '%s - "%s %s HTTP/%s" %d' with the raw path+query.
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", f"/api/share/chat/ws/1?token={SECRET}", "1.1", 101),
        None,
    )
    assert RequestTargetRedactionFilter().filter(record) is True
    assert SECRET not in record.getMessage()
    assert f"?token={REDACTED}" in record.getMessage()


def test_server_loggers_keep_the_filter_across_logging_reconfiguration():
    setup_logging(force=True)
    # The ASGI server applies its own dictConfig after ours.
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {
                "uvicorn.access": {"level": "INFO"},
                "uvicorn.error": {"level": "INFO"},
            },
        }
    )
    install_request_log_redaction()  # idempotent
    for name in ("uvicorn.access", "uvicorn.error"):
        filters = [
            f
            for f in logging.getLogger(name).filters
            if isinstance(f, RequestTargetRedactionFilter)
        ]
        assert len(filters) == 1

    # The server attaches its own non-propagating handlers; capture the same way.
    records: list[logging.LogRecord] = []
    capture = logging.Handler()
    capture.emit = records.append  # type: ignore[method-assign]
    server_logger = logging.getLogger("uvicorn.error")
    server_logger.addHandler(capture)
    previous_level = server_logger.level
    server_logger.setLevel(logging.INFO)
    try:
        server_logger.info(
            '%s - "WebSocket %s" [accepted]',
            "127.0.0.1:1",
            f"/api/widget/chat/ws/1?token={SECRET}",
        )
    finally:
        server_logger.removeHandler(capture)
        server_logger.setLevel(previous_level)
    message = records[-1].getMessage()
    assert SECRET not in message
    assert f"/api/widget/chat/ws/1?token={REDACTED}" in message


def _app_with_handlers() -> FastAPI:
    app = FastAPI()
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    @app.get("/v1/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    @app.get("/api/typed")
    def typed(count: int) -> dict[str, int]:
        return {"count": count}

    return app


def test_exception_handlers_log_the_path_not_the_query(caplog):
    client = TestClient(_app_with_handlers(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="xagent.web.app"):
        assert client.get(f"/v1/boom?token={SECRET}").status_code == 500
        # The rejected input value itself is a potential secret too.
        assert (
            client.get(f"/api/typed?count={SECRET}&ticket={SECRET}").status_code == 422
        )
    assert SECRET not in caplog.text
    assert "GET /v1/boom" in caplog.text
    assert "GET /api/typed" in caplog.text
