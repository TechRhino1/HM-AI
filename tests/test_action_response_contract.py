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
    """Every status the action path can emit must be **classified**, and every
    refusal must be one the UI actually treats as a refusal.

    This is the drift that produced the bug: the backend had two refusal
    statuses and the frontend knew one. The first version of this guard could
    not catch that drift — it read the emitted statuses and then filtered them
    through the very set it was validating::

        emitted  = set(re.findall(..., mt5_client.py))
        refusals = {s for s in emitted if s in {"FAILED", "BLOCKED", ...}}

    `refusals` is a subset of that literal *by construction*, so the assertion
    that followed was a tautology, and a new refusal status would have been
    filtered out before it could fail anything. The sets below are instead
    exhaustive classifications: an unclassified status fails the test and forces
    a decision, which is the only way a guard like this earns its place.

    Why the UI can use a deny-list at all: a refusal that arrives with HTTP 200
    is the only case `res.ok` cannot catch, and on the action path those are
    exactly `FAILED` and `BLOCKED` (the route passes the broker's dict through
    `_send_json`, whose default is 200). Everything else the server refuses with
    — `UNAUTHORIZED` 401, `FORBIDDEN` 403, `BAD_REQUEST` 400, the 500 path —
    carries a non-2xx code, so the UI rejects it before the body is read.
    """

    # The action did NOT happen.
    REFUSALS = {"FAILED", "BLOCKED", "REJECTED", "ERROR"}
    # The action DID happen.
    COMPLETIONS = {"PLACED", "FILLED", "MODIFIED", "CANCELLED", "CLOSED",
                   "PARTIALLY_CLOSED", "SUCCESS", "OK"}
    # Refusals the server only ever pairs with a non-2xx code, so `res.ok`
    # already rejects them and the body-level guard never sees them.
    NON_2XX_ONLY = {"UNAUTHORIZED", "FORBIDDEN", "BAD_REQUEST", "NOT_FOUND", "LOCKED"}
    # We genuinely do not know whether the action happened -- the broker call
    # timed out and the order may have filled. This is its own class on
    # purpose: calling it a COMPLETION would render an unconfirmed order as a
    # filled trade, and calling it a REFUSAL would invite a blind retry that
    # can double a position. It must be surfaced as "not confirmed".
    INDETERMINATE = {"UNKNOWN"}

    def _ui_refusal_set(self):
        src = DASHBOARD_JS.read_text(encoding="utf-8")
        m = re.search(r"var\s+ACTION_REFUSED\s*=\s*\{([^}]*)\}", src)
        self.assertIsNotNone(m, "ACTION_REFUSED is gone from dashboard.js")
        # The literal is a lookup map — `{ FAILED: 1, BLOCKED: 1 }` — so take the
        # key of each entry and drop the value.
        return {k.split(":")[0].strip().strip("'\"").upper()
                for k in m.group(1).split(",") if k.strip()}

    def _statuses(self, path):
        return set(re.findall(r'"status":\s*"([A-Z_]+)"',
                              path.read_text(encoding="utf-8")))

    def _action_route_statuses(self):
        """The status literals inside `do_POST`'s `/api/action/` dispatch.

        Scoped to that method rather than the whole file: the status endpoint
        emits `SAFE_MODE` / `OPERATIONAL` to describe the *system*, which is a
        different vocabulary from "did your order happen", and letting those in
        would make the classification meaningless.
        """
        src = (Path(__file__).resolve().parents[1]
               / "jarvis/api/server.py").read_text(encoding="utf-8")
        start = src.index('path.startswith("/api/action/")')
        rest = src[start:]
        end = re.search(r"\n    def ", rest)
        block = rest[:end.start()] if end else rest
        return set(re.findall(r'"status":\s*"([A-Z_]+)"', block))

    def test_every_status_the_action_path_can_emit_is_classified(self):
        """The guard that can actually fail.

        Add a refusal status to the broker or a route and this goes red until
        someone decides which set it belongs to — instead of it silently
        rendering as a completed trade.
        """
        emitted = (self._statuses(Path(__file__).resolve().parents[1]
                                  / "jarvis/execution/mt5_client.py")
                   | self._action_route_statuses())
        self.assertTrue(emitted, "expected to find status literals to classify")
        known = self.REFUSALS | self.COMPLETIONS | self.NON_2XX_ONLY | self.INDETERMINATE
        unclassified = emitted - known
        self.assertFalse(
            unclassified,
            f"the action path can emit {sorted(unclassified)}, which is not "
            f"classified as a refusal, a completion or a non-2xx-only status. "
            f"Decide which it is — an unclassified failure renders as success.",
        )

    def test_an_indeterminate_outcome_is_never_rendered_as_success(self):
        """`UNKNOWN` (broker timeout) must not be classified as a completion.

        An order we cannot confirm is not an order that happened. If this drifted
        into COMPLETIONS the dashboard would show a filled trade that may not
        exist, and the caller would release the risk reservation and retry --
        potentially opening a second position.
        """
        self.assertFalse(
            self.INDETERMINATE & self.COMPLETIONS,
            "an indeterminate status must never be classified as a completion",
        )
        ui_refusals = self._ui_refusal_set()
        missing = self.INDETERMINATE - ui_refusals
        self.assertFalse(
            missing,
            f"the UI must treat {sorted(missing)} as 'not confirmed'; otherwise "
            f"an unconfirmed order renders as a completed trade",
        )

    def test_every_refusal_the_action_path_can_emit_is_treated_as_one(self):
        emitted = (self._statuses(Path(__file__).resolve().parents[1]
                                  / "jarvis/execution/mt5_client.py")
                   | self._action_route_statuses())
        refusals = emitted & self.REFUSALS
        self.assertTrue(refusals, "expected the action path to have refusal statuses")
        handled = self._ui_refusal_set() | self.NON_2XX_ONLY
        self.assertTrue(
            refusals <= handled,
            f"the action path can refuse with {sorted(refusals)} but the UI only "
            f"knows {sorted(self._ui_refusal_set())} and {sorted(self.NON_2XX_ONLY)} "
            f"arrive with a non-2xx code — an unhandled refusal renders as success",
        )

    def test_the_two_classifications_do_not_overlap(self):
        """A status cannot mean both "it happened" and "it did not"."""
        self.assertFalse(self.REFUSALS & self.COMPLETIONS)

    def test_a_completed_action_is_never_treated_as_a_refusal(self):
        """The opposite mistake, which would turn a filled order into an error."""
        self.assertFalse(
            self.COMPLETIONS & self._ui_refusal_set(),
            "a completed status is in the UI's refusal set",
        )


class BacktestCancelContractTest(unittest.TestCase):
    """`/api/backtest/cancel` answers **200** for a job it did not cancel.

    `_post_cancel` replies `{"status": "NOOP", "cancelled": false}` with status
    200 when the job had already finished, so the UI cannot read the outcome
    from the HTTP code here either — it branches on `cancelled`. That is the
    only signal distinguishing "cancelled" from "there was nothing to cancel",
    so it is pinned.
    """

    def _cancel(self, cancel_result):
        from jarvis.api import intelligence_api as ia

        class _Job:
            def to_dict(self):
                return {"job_id": "bt-1", "status": "DONE"}

        class _Jobs:
            def cancel(self, job_id):
                return cancel_result

            def get(self, job_id):
                return _Job()

        class _Handler:
            def __init__(self):
                self.sent = []

            def _send_json(self, payload, status_code=200, cookies=None):
                self.sent.append((status_code, payload))

        handler = _Handler()
        real_jobs = ia.INTELLIGENCE.jobs
        ia.INTELLIGENCE.jobs = _Jobs()
        try:
            handled = ia.INTELLIGENCE.handle_post(
                "/api/backtest/cancel", {"job_id": "bt-1"}, handler)
        finally:
            ia.INTELLIGENCE.jobs = real_jobs

        self.assertTrue(handled)
        return handler.sent[-1]

    def test_a_job_that_was_not_cancelled_still_answers_200(self):
        status, payload = self._cancel(False)
        self.assertEqual(status, 200, "the UI's res.ok would be true here")
        self.assertEqual(payload["status"], "NOOP")
        self.assertIs(payload["cancelled"], False)

    def test_a_job_that_was_cancelled_reports_it(self):
        status, payload = self._cancel(True)
        self.assertEqual(status, 200)
        self.assertIs(payload["cancelled"], True)


if __name__ == "__main__":
    unittest.main()
