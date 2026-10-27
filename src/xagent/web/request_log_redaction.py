"""Keep credential-bearing query values out of request logs.

Server and proxy logs record request targets verbatim. Any credential a client
still puts in a query string (a stale client's retired ``?token=``, a
short-lived file ``ticket``, an OAuth ``code``) would otherwise be written to
disk and shipped to log storage. Application handlers log the path only; the
ASGI server's own access and WebSocket handshake lines go through
:class:`RequestTargetRedactionFilter`.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, quote, urlsplit

REDACTED = "[REDACTED]"

# Exact names (lower-cased) whose values are credentials or one-time proofs.
_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "code",
        "credential",
        "id_token",
        "jwt",
        "key",
        "password",
        "refresh_token",
        "secret",
        "session",
        "sig",
        "signature",
        "state",
        "ticket",
        "token",
    }
)
# Any name ending in one of these (``widget_key``, ``share_token``,
# ``embed_ticket``, ``client_secret``) is treated as a credential too.
_SENSITIVE_QUERY_SUFFIXES = (
    "_key",
    "-key",
    "_token",
    "-token",
    "_ticket",
    "-ticket",
    "_secret",
    "-secret",
    "_password",
    "_signature",
)
_LOGGER_NAMES = ("uvicorn.access", "uvicorn.error")


def is_sensitive_query_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SENSITIVE_QUERY_NAMES or lowered.endswith(
        _SENSITIVE_QUERY_SUFFIXES
    )


def redact_request_target(target: str) -> str:
    """Return ``target`` with credential-like query values replaced.

    Accepts a request target (``/path?query``) or an absolute URL. Parameter
    names and non-sensitive values are kept so logs stay useful.
    """
    if "?" not in target:
        return target
    try:
        parsed = urlsplit(target)
    except ValueError:
        # Unparsable: never risk echoing the query.
        return target.split("?", 1)[0] + "?" + REDACTED
    if not parsed.query:
        return target
    parts = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        shown = REDACTED if value and is_sensitive_query_name(name) else value
        parts.append(f"{quote(name, safe='')}={quote(shown, safe='[]')}")
    prefix = target.split("?", 1)[0]
    suffix = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{prefix}?{'&'.join(parts)}{suffix}"


class RequestTargetRedactionFilter(logging.Filter):
    """Redact credential query values in server access/handshake log args."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and args:
            record.args = tuple(
                redact_request_target(arg)
                if isinstance(arg, str) and "?" in arg
                else arg
                for arg in args
            )
        elif not args and isinstance(record.msg, str) and "?" in record.msg:
            record.msg = " ".join(
                redact_request_target(word) if "?" in word else word
                for word in record.msg.split(" ")
            )
        return True


_FILTER = RequestTargetRedactionFilter()


def install_request_log_redaction() -> None:
    """Attach the filter to the ASGI server loggers (idempotent).

    Logger-level filters survive later ``dictConfig`` calls (for example the
    server applying its own logging config), which only replace handlers.
    """
    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
