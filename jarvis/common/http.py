"""Bounded reads for remote HTTP responses.

WHY THIS EXISTS
---------------
Every remote fetch in this codebase set a socket timeout but never a size cap,
and `resp.read()` with no argument reads until EOF. A timeout bounds *time*, not
*bytes*: an endpoint that streams continuously can still return hundreds of
megabytes inside a 5-6 second window, and each of these fetches runs on a
request or scheduler path. The result is a memory-exhaustion vector reachable
from any upstream feed the platform trusts — a news RSS feed, a JSON API.

A news or quote payload is kilobytes. The default cap is deliberately far above
any legitimate response so that this is a safety net rather than a limit anyone
has to tune; the callers already wrap these fetches in `try/except` with a
labelled fallback, so exceeding it degrades exactly like a network error.

This module is a leaf: it imports nothing from `jarvis.*`, per the package
contract in `jarvis/common/__init__.py`.
"""
from __future__ import annotations

from typing import Any

#: Generous ceiling for a news/JSON API response. A typical economic-calendar
#: feed is 20-200 KB; 8 MiB leaves ~40x headroom.
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """Raised when a response exceeds the size cap.

    A `ValueError` subclass so a caller that already catches `Exception` (all of
    them do) treats it exactly like a malformed payload.
    """


def read_bounded(resp: Any, limit: int = DEFAULT_MAX_RESPONSE_BYTES) -> bytes:
    """Read at most `limit` bytes from an open HTTP response.

    Reads one byte past the cap so an exactly-`limit`-sized body is accepted and
    an over-sized one is detectable without trusting `Content-Length` (which a
    hostile server controls and which is absent on a chunked response).

    Raises `ResponseTooLargeError` rather than truncating: a truncated XML or
    JSON document would fail to parse anyway, and silently returning half a
    document is the kind of partial success this codebase has been bitten by
    before.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit!r}")
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise ResponseTooLargeError(
            f"response exceeds the {limit}-byte cap; refusing to buffer it"
        )
    return data
