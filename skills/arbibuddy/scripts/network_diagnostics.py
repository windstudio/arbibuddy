"""Shared, non-sensitive diagnostics for controlled network access."""

from __future__ import annotations

from typing import Any
from urllib.error import HTTPError, URLError


RETRY_AFTER_NETWORK_PERMISSION = (
    "request-network-permission-and-retry-same-command"
)
TRY_NEXT_OFFICIAL_SOURCE = "try-next-official-source"
REPORT_FAILURE = "report_failure_and_wait_for_user"


def classify_network_error(error: BaseException) -> dict[str, Any]:
    """Classify a URL failure without exposing URLs, headers, or credentials."""
    reason = getattr(error, "reason", None)
    cause = reason if isinstance(error, URLError) and reason is not None else error
    details: dict[str, Any] = {"cause_type": type(error).__name__}
    if cause is not error:
        details["reason_type"] = type(cause).__name__

    if isinstance(error, HTTPError):
        details["http_status"] = error.code
        return {
            "kind": "http_error",
            "error_code": "official_source_http_error",
            "recoverable": False,
            "next_action": REPORT_FAILURE,
            "details": details,
        }

    if isinstance(cause, TimeoutError):
        return {
            "kind": "timeout",
            "error_code": "official_source_timeout",
            "recoverable": True,
            "next_action": TRY_NEXT_OFFICIAL_SOURCE,
            "details": details,
        }

    if isinstance(cause, PermissionError):
        return {
            "kind": "network_permission",
            "error_code": "network_permission_required",
            "recoverable": True,
            "next_action": RETRY_AFTER_NETWORK_PERMISSION,
            "details": details,
        }

    if isinstance(cause, (OSError, URLError)):
        return {
            "kind": "network_unavailable",
            "error_code": "official_source_network_unavailable",
            "recoverable": True,
            "next_action": TRY_NEXT_OFFICIAL_SOURCE,
            "details": details,
        }

    return {
        "kind": "request_invalid",
        "error_code": "official_source_request_invalid",
        "recoverable": False,
        "next_action": REPORT_FAILURE,
        "details": details,
    }
