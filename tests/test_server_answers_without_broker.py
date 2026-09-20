"""The web server must answer even when the broker is unreachable.

Why this exists
---------------
Every blocker found on the live platform this session had the same shape: a READ
path that quietly paid for a broker connect. The symptom was always "this
endpoint hangs", and the cause was never the endpoint:

* `/api/history` -> `fetch_recent_trades` -> `sync_mt5_history` -> a direct
  `mt5.initialize()` with no terminal to attach to: 60-100s inside native code
  holding the GIL, on the request thread.
* `/api/telemetry_state` -> `get_account_snapshot()` -> `init_connection()` with
  1+2+4+8+16s backoff: ~31s parked on the request thread.

Measured before the fixes: `/`, `/classic`, `/api/diagnostics` answered in
~0.15s while `/api/history`, `/api/telemetry_state`, `/api/market-status` and
`/api/radar` all timed out at 20s. That pattern points at the endpoints, which
is why it survived so long.

This test starts the REAL server against a client that refuses to connect and
asserts every public endpoint answers quickly. It is deliberately an end-to-end
HTTP test: the defects were all in wiring, and only the wired-up server can
prove the wiring.
"""

from __future__ import annotations

import threading
import unittest
import urllib.error
import urllib.request

from jarvis.api import server as server_module


class _RefusingClient:
    """A broker client that is NOT connected and would hang if asked to connect.

    `get_account_snapshot` / `get_open_positions` raise rather than returning:
    a read path reaching them while disconnected is the defect, and a silent
    empty return would let it pass unnoticed.
    """

    mode = "live"
    is_connected = False

    def get_account_snapshot(self):
        raise AssertionError(
            "a read path called get_account_snapshot() while disconnected — "
            "this is the call that retries with 1+2+4+8+16s backoff on a "
            "request thread"
        )

    def get_open_positions(self):
        raise AssertionError("a read path asked the broker for positions while disconnected")

    def get_pending_orders(self):
        return []

    def resolve_symbol_name(self, symbol):
        return symbol


class ServerAnswersWithoutBrokerTest(unittest.TestCase):
    #: Public GET endpoints the dashboard/terminal poll. Generous but far below
    #: the 20s the endpoints used to hang for.
    ENDPOINTS = [
        "/",
        "/classic",
        "/api/diagnostics",
        "/api/history?limit=10",
        "/api/history?limit=10&origin=broker",
        "/api/telemetry_state",
        "/api/market-status",
        "/api/radar",
        "/api/pending_orders",
        "/api/tunnel_info",
    ]

    def setUp(self):
        self.client = _RefusingClient()
        self.server = server_module.start_server(
            host="127.0.0.1", port=0, mt5_client=self.client)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        # The sandbox exports a proxy; a proxied localhost request hangs and
        # would make this test blame the server.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _get(self, path, timeout=5.0):
        return self.opener.open(f"http://127.0.0.1:{self.port}{path}", timeout=timeout)

    def test_every_public_endpoint_answers(self):
        failures = []
        for path in self.ENDPOINTS:
            try:
                with self._get(path) as resp:
                    if resp.status != 200:
                        failures.append(f"{path} -> HTTP {resp.status}")
                    resp.read()
            except AssertionError as exc:
                failures.append(f"{path} -> {exc}")
            except Exception as exc:                       # noqa: BLE001 - report it
                failures.append(f"{path} -> {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "endpoints did not answer:\n  " + "\n  ".join(failures))

    def test_the_read_path_never_reaches_the_broker(self):
        """No 200 is enough if the price was a 31s stall inside the handler."""
        for path in self.ENDPOINTS:
            with self.subTest(path=path):
                with self._get(path, timeout=5.0) as resp:
                    self.assertEqual(resp.status, 200)
                    resp.read()


if __name__ == "__main__":
    unittest.main()
