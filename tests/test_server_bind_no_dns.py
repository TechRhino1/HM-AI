"""Binding the listener must not perform a reverse-DNS lookup.

Why this exists
---------------
`http.server.HTTPServer.server_bind` finishes with
`self.server_name = socket.getfqdn(host)`. `getfqdn` is a REVERSE DNS lookup:
where the resolver does not answer it blocks for as long as the resolver takes,
and it does so BEFORE the socket is usable — so `run_web_server` never returns
and the platform never listens.

Measured on `HM_start.py live` with the broker unreachable: `py-spy dump` put
MainThread in

    getfqdn (socket.py:811)
    server_bind (http\\server.py:142)
    __init__ (socketserver.py:457)
    run_web_server (jarvis\\api\\server.py)

while `netstat` showed no listener on :8501. Every *other* symptom of a wedged
start was present — banner printed, both tunnels up, orchestrator and watchdog
logging normally — so this looked exactly like the earlier GIL-starvation hang
and would have been misdiagnosed as one. The two defects share a symptom and
nothing else: that one is native code holding the GIL, this one is a blocking
name lookup on the bind path.

`server_name` only populates the `Server:` response header, so the literal host
is a correct substitute.
"""

from __future__ import annotations

import socket
import unittest
from unittest import mock

from http.server import ThreadingHTTPServer

from jarvis.api import server as server_module


class ServerBindTest(unittest.TestCase):
    def test_binding_does_not_resolve_reverse_dns(self):
        """The bind path must never call `getfqdn`."""
        with mock.patch("socket.getfqdn",
                        side_effect=AssertionError("reverse DNS on the bind path")):
            srv = server_module._build_server("127.0.0.1", 0)
        try:
            self.assertEqual(srv.server_name, "127.0.0.1")
            self.assertEqual(srv.server_address[0], "127.0.0.1")
            # A bound socket has a real ephemeral port; port 0 was the request.
            self.assertGreater(srv.server_address[1], 0)
        finally:
            srv.server_close()

    def test_the_stock_class_does_call_it(self):
        """Proves the assertion above can fail — the defect is real, not
        hypothetical. If a future Python stops resolving on bind, this test goes
        red and the override in `server.py` can be reconsidered rather than
        silently kept."""
        with mock.patch("socket.getfqdn",
                        side_effect=AssertionError("reverse DNS on the bind path")):
            with self.assertRaises(AssertionError):
                ThreadingHTTPServer(("127.0.0.1", 0), server_module.JarvisRequestHandler)

    def test_the_listener_accepts_a_connection(self):
        """A server that binds but does not serve is still a broken start."""
        srv = server_module._build_server("127.0.0.1", 0)
        try:
            port = srv.server_address[1]
            conn = socket.create_connection(("127.0.0.1", port), timeout=5)
            conn.close()
        finally:
            srv.server_close()

    def test_getfqdn_is_not_used_by_the_override_at_all(self):
        """Belt and braces: patch it to a sentinel and assert it is untouched."""
        calls = []
        with mock.patch("socket.getfqdn", lambda *a, **k: calls.append(a)):
            srv = server_module._build_server("127.0.0.1", 0)
        try:
            self.assertEqual(calls, [], "server_bind still resolved a hostname")
        finally:
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
