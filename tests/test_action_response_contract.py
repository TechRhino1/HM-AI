"""The action routes report a refusal with HTTP 200 and a status of their own.

`dashboard.js` cannot decide an order's outcome from the HTTP status code. The
broker answers a *refused* order with **200** and puts the outcome in `status` —
`FAILED` when the broker said no, `BLOCKED` when execution is disabled so nothing
was ever sent — and the route passes that dict straight through. The response
**body**, not the code, is therefore the contract the UI branches on, and nothing
asserted it.

These tests pin that contract by driving the real `do_POST` body with the real
paper `MT5Client`. They exist because the UI got it wrong in a way no test could
see: `manual_trade` branched on `res.ok` alone, so a rejected market order was
announced to the trader as *submitted*, and the three pending-order handlers
tested for `FAILED` only, so a *blocked* order was announced as *placed*.

If a route is ever changed to signal a refusal with a 4xx/5xx instead, the UI's
guard is still correct and the last test here is what keeps the two in step.
"""
import io
import json
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from jarvis.api.server import JarvisRequestHandler
from jarvis.execution.mt5_client import MT5Client


DASHBOARD_JS = Path(__file__).resolve().parents[1] / "jarvis/ui/static/js/dashboard.js"


class _RouteHarness(JarvisRequestHandler):
    """Drives the real do_POST body without a socket.

    Only the four request attributes do_POST reads are supplied, and `_send_json`
    is captured instead of written — so the assertions see exactly the
    (status_code, payload) pair the route would have put on the wire.
    """

    def __init__(self, path, payload, client):
        self.path = path
        self.headers = {"Content-Length": str(len(payload))}
        self.rfile = io.BytesIO(payload)
        self.client_address = ("127.0.0.1", 12345)
        self.mt5_client = client
        self.sent = []

    def _send_json(self, data, status_code=200, cookies=None):
        self.sent.append((status_code, data))

    def _require_role(self, *allowed_roles):
        return True, None


def _post(path, body, client):
    payload = json.dumps(body).encode("utf-8")
    handler = _RouteHarness(path, payload, client)
    handler.do_POST()
    assert handler.sent, f"{path} answered nothing"
    return handler.sent[-1]


class ActionResponseContractTest(unittest.TestCase):
    def setUp(self):
        # Coherent levels for a BUY at 2000, so the route's own validation
        # passes and the broker layer is what decides the outcome.
        self.coherent = {
            "symbol": "XAUUSD", "side": "BUY", "volume": 0.10,
            "price": 2000.0, "sl": 1900.0, "tp": 2100.0,
        }

    def test_a_rejected_market_order_answers_200_with_a_failed_status(self):
        """The case the UI misreported: HTTP 200, and the order did not happen."""
        client = MT5Client(mode="paper")
        body = dict(self.coherent, sl=2100.0, tp=1900.0)   # stops on the wrong side
        status, payload = _post("/api/action/manual_trade", body, client)

        self.assertEqual(status, 200, "the UI's res.ok would be true here")
        self.assertEqual(payload["status"], "FAILED")
        # The reason the UI must surface. The broker sends `reason`, not `error`.
        self.assertIn("reason", payload)
        self.assertTrue(payload["reason"], "a refusal must say why")

    def test_a_blocked_market_order_answers_200_with_a_blocked_status(self):
        """`BLOCKED` is real and distinct from `FAILED`.

        `init_connection` accepts "offline" and "backtest" as healthy simulated
        modes, but the action gate only allows live/demo/paper — so a client in
        one of those modes reports itself connected and refuses every order.
        """
        client = MT5Client(mode="offline")
        status, payload = _post("/api/action/manual_trade", dict(self.coherent), client)

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertIn("Execution is disabled", payload["reason"])

    def test_a_broker_refusal_of_a_pending_order_answers_200_with_a_failed_status(self):
        """Paper mode always fills, so the broker layer is stubbed to refuse."""
        client = MagicMock(spec=MT5Client)
        client.place_pending_order.return_value = {
            "status": "FAILED", "reason": "Symbol metadata unavailable for XAUUSD",
        }
        status, payload = _post("/api/action/place_pending_order", {
            "symbol": "XAUUSD", "order_type": "BUY_LIMIT",
            "price": 1900.0, "volume": 0.10,
        }, client)

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "FAILED")
        self.assertIn("reason", payload)

    def test_the_routes_own_validation_answers_400_with_an_error(self):
        """Distinct from a broker refusal, and the one case the code identifies."""
        client = MT5Client(mode="paper")
        status, payload = _post("/api/action/place_pending_order", {
            "symbol": "XAUUSD", "order_type": "NOT_A_TYPE",
            "price": 1900.0, "volume": 0.10,
        }, client)

        self.assertEqual(status, 400)
        self.assertEqual(payload["status"], "FAILED")
        self.assertIn("error", payload)


class RefusalStatusesAgreeWithTheUiTest(unittest.TestCase):
    """The set of statuses the server can refuse with must be the set the UI
    treats as a refusal.

    This is the drift that produced the bug: the backend had two refusal
    statuses and the frontend knew one. A new refusal status added to
    `mt5_client` without being added to the UI's guard would otherwise be
    rendered as a completed trade.
    """

    def _ui_refusal_set(self):
        src = DASHBOARD_JS.read_text(encoding="utf-8")
        m = re.search(r"var\s+ACTION_REFUSED\s*=\s*\{([^}]*)\}", src)
        self.assertIsNotNone(m, "ACTION_REFUSED is gone from dashboard.js")
        # The literal is a lookup map — `{ FAILED: 1, BLOCKED: 1 }` — so take the
        # key of each entry and drop the value.
        return {k.split(":")[0].strip().strip("'\"").upper()
                for k in m.group(1).split(",") if k.strip()}

    def test_every_refusal_status_the_broker_can_return_is_treated_as_one(self):
        # Read from the broker layer rather than hard-coded, so a new one shows up.
        src = (Path(__file__).resolve().parents[1]
               / "jarvis/execution/mt5_client.py").read_text(encoding="utf-8")
        emitted = set(re.findall(r'"status":\s*"([A-Z_]+)"', src))
        refusals = {s for s in emitted if s in {"FAILED", "BLOCKED", "REJECTED", "ERROR"}}
        self.assertTrue(refusals, "expected the broker layer to have refusal statuses")
        self.assertTrue(
            refusals <= self._ui_refusal_set(),
            f"the broker can refuse with {sorted(refusals)} but the UI only knows "
            f"{sorted(self._ui_refusal_set())} — an unhandled refusal renders as success",
        )

    def test_a_completed_action_is_never_treated_as_a_refusal(self):
        """The opposite mistake, which would turn a filled order into an error."""
        completed = {"PLACED", "FILLED", "MODIFIED", "CANCELLED", "CLOSED",
                     "PARTIALLY_CLOSED"}
        self.assertFalse(
            completed & self._ui_refusal_set(),
            "a completed status is in the UI's refusal set",
        )


if __name__ == "__main__":
    unittest.main()
