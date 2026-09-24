"""A remote read must be bounded by BYTES, not only by time.

Every remote fetch in this codebase set a socket timeout but called `resp.read()`
with no argument, which reads until EOF. A timeout bounds time, not size: an
endpoint that streams continuously can return hundreds of megabytes inside a
5-6 second window, on a request or scheduler path. These tests pin the cap.

The `read(limit + 1)` detail is load-bearing and is asserted here: reading
exactly `limit` cannot distinguish "a body of exactly `limit` bytes" from "a
larger body we truncated", and `Content-Length` is not trustworthy (a hostile
server sets it, and a chunked response omits it).
"""

from __future__ import annotations

import io

import pytest

from jarvis.common.http import (
    DEFAULT_MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    read_bounded,
)


class TestReadBounded:
    def test_a_normal_payload_is_returned_whole(self):
        payload = b'{"events": [1, 2, 3]}'
        assert read_bounded(io.BytesIO(payload)) == payload

    def test_an_empty_response_is_returned(self):
        assert read_bounded(io.BytesIO(b"")) == b""

    def test_a_body_exactly_at_the_limit_is_accepted(self):
        """The boundary: `limit` bytes is not "too large"."""
        payload = b"x" * 512
        assert read_bounded(io.BytesIO(payload), limit=512) == payload

    def test_a_body_one_byte_over_the_limit_is_refused(self):
        with pytest.raises(ResponseTooLargeError):
            read_bounded(io.BytesIO(b"x" * 513), limit=512)

    def test_an_oversized_body_is_refused_not_truncated(self):
        """Truncating would hand back half a document that then fails to parse
        somewhere further away, which is a worse failure than a clean refusal."""
        with pytest.raises(ResponseTooLargeError):
            read_bounded(io.BytesIO(b"x" * 100_000), limit=1024)

    def test_at_most_limit_plus_one_bytes_are_read(self):
        """The over-read is one byte, so a huge body is never buffered."""
        class CountingStream:
            def __init__(self, size):
                self.size = size
                self.read_bytes = 0

            def read(self, n=-1):
                self.read_bytes += min(n, self.size)
                return b"x" * min(n, self.size)

        stream = CountingStream(50_000_000)
        with pytest.raises(ResponseTooLargeError):
            read_bounded(stream, limit=4096)
        assert stream.read_bytes == 4097, (
            f"buffered {stream.read_bytes} bytes; expected exactly limit+1"
        )

    def test_a_non_positive_limit_is_rejected(self):
        for bad in (0, -1):
            with pytest.raises(ValueError):
                read_bounded(io.BytesIO(b"x"), limit=bad)

    def test_the_default_cap_is_generous(self):
        """A safety net, not a limit anyone should have to tune: a typical
        economic-calendar feed is 20-200 KB."""
        assert DEFAULT_MAX_RESPONSE_BYTES >= 1024 * 1024
        assert read_bounded(io.BytesIO(b"x" * 200_000)) == b"x" * 200_000

    def test_it_is_a_value_error_so_existing_handlers_catch_it(self):
        """All three call sites catch `Exception`; subclassing ValueError means
        an over-sized response degrades exactly like a malformed payload."""
        assert issubclass(ResponseTooLargeError, ValueError)


class TestTheCallSitesUseIt:
    """A primitive nothing calls is not a fix. Asserted against the source
    because all three sites are inside `try` blocks that need a live network to
    reach."""

    def _source(self, module: str) -> str:
        import inspect
        import importlib
        return inspect.getsource(importlib.import_module(module))

    @pytest.mark.parametrize("module", [
        "jarvis.market.news",
        "jarvis.data.tradingview_provider",
    ])
    def test_no_unbounded_remote_read_remains(self, module):
        src = self._source(module)
        assert "resp.read()" not in src, (
            f"{module} still reads a remote response with no size cap"
        )
        assert "read_bounded(resp" in src, (
            f"{module} does not use the bounded read"
        )
