"""A10 — the SSE stream must not carry the explainability payload.

Measured on the live snapshot: `/api/telemetry_state` returned **63,276 bytes** with 15 radar rows and
5 decisions, and `/api/stream/telemetry` re-sent all of it on every state version change, up to once a
second — ~63 KB/s per client, and ~144 KB/s with two dashboards open. The bulk is prose: `checks`,
`reasons`, `risk_factors`, `quality_gate`. Those belong on a detail view, not on an event stream.

Two invariants are pinned:

* **The digest is materially smaller** — a byte budget, because the denylist cannot catch a NEW heavy
  field. If someone adds one, this fails instead of shipping a surprise.
* **Everything actionable survives** — prices, levels, scores, grades. A digest that saves bytes by
  dropping `current_price` is worse than useless.

And one that protects the UI: `/api/telemetry_state` must stay COMPLETE. The dashboard, terminal and
console all read `radar_opportunities` and `latest_decisions` from it, so the reduction is confined to
the stream.
"""

import json
from types import SimpleNamespace

import pytest

from jarvis.application.state_manager import (
    STREAM_DECISION_LIMIT,
    STREAM_EXPLAINABILITY_KEYS,
    STREAM_LOG_LIMIT,
    STREAM_RADAR_LIMIT,
    StateManager,
    _slim_explainability,
)

# Shape of a real radar row / decision object, as measured off /api/telemetry_state.
RADAR_ROW = {
    "symbol": "BTCUSD", "action": "NO TRADE: SELL", "decision": "NO_TRADE",
    "current_price": 90073.60223398934, "entry_price": 90000.0, "stop_loss": 91000.0,
    "take_profit": 88000.0, "score": 41.2, "confluence_score": 3, "confluence_tier": "TIER_3",
    "win_prob": 0.48, "ml_prob": 0.51, "ev": 0.02, "risk_reward_ratio": 1.8,
    "setup_grade": "GRADE C", "status_label": "NO TRADE: SELL", "regime": "TREND_BEAR",
    "timeframe": "M15", "strategy": "TREND_FOLLOWING", "is_actionable": False,
    # ── explainability: 905 + 252 + 212 + 77 + 69 bytes of prose ──
    "checks": {"trend": {"passed": False, "detail": "x" * 400, "note": "y" * 400}},
    "rejection_reasons": ["lower-highs structure not confirmed", "spread above session median"],
    "failing_reasons": ["mtf_alignment"], "waiting_reasons": ["pullback to supply"],
    "risk_factors": {"news_risk": "elevated", "liquidity": "thin"},
    "invalidation_levels": {"structural": 91200.0, "atr": 90500.0},
    "mtf_alignment": {"M5": False, "M15": True, "H1": False},
}

DECISION_ROW = {
    "symbol": "EURUSD", "action": "BUY", "confidence": 0.62, "entry_price": 1.1005,
    "sl_distance": 0.0012, "tp_distance": 0.0024, "model_confidence": 0.58,
    "master_confluence_tier": "TIER_2", "dissection_tier": "STANDARD",
    "strategy": "TREND_FOLLOWING", "timestamp": "2026-09-20 14:09:11",
    # ── explainability: 1030 + 386 + 299 + 273 + 269 + 215 + 202 bytes ──
    "quality_gate": {"passed": True, "breakdown": {"a": "z" * 300, "b": "z" * 300}},
    "regime": {"label": "TREND_UP", "strength": 0.7, "detail": {"note": "q" * 200}},
    "risk_factors": {"spread": "wide"}, "honest_base_rate": {"n": 120, "rate": 0.41},
    "rejection_reasons": ["spread"], "bull_case": {"summary": "p" * 180},
    "invalidation_levels": {"structural": 1.0980},
}


def _decision():
    """`get_state_snapshot` serialises decisions via `to_dict()` or `__dict__`,
    and the store really holds DecisionObjects — so stand one up, not a dict."""
    return SimpleNamespace(**DECISION_ROW)


def _nbytes(obj):
    return len(json.dumps(obj, default=str).encode("utf-8"))


def _keys_at_any_depth(obj, acc=None):
    acc = acc if acc is not None else set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _keys_at_any_depth(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _keys_at_any_depth(v, acc)
    return acc


@pytest.fixture
def sm():
    """A StateManager with a realistically-shaped state, isolated from the singleton."""
    m = StateManager.__new__(StateManager)
    m._init_state()
    m.radar_opportunities = [dict(RADAR_ROW, symbol=f"S{i}") for i in range(15)]
    m.latest_decisions = {f"D{i}": _decision() for i in range(5)}
    m.logs = [{"message": f"line {i}"} for i in range(80)]
    return m


class TestTheDigestDropsTheProse:
    def test_no_explainability_key_survives_at_any_depth(self, sm):
        digest = sm.get_state_digest()
        found = _keys_at_any_depth(digest) & STREAM_EXPLAINABILITY_KEYS
        assert not found, f"explainability leaked into the stream: {sorted(found)}"

    def test_the_digest_names_what_it_omitted(self, sm):
        """Omission must be declared, not silent."""
        digest = sm.get_state_digest()
        assert digest["digest"] is True
        assert digest["full_endpoint"] == "/api/telemetry_state"
        assert set(digest["omitted_fields"]) == set(STREAM_EXPLAINABILITY_KEYS)

    def test_every_actionable_field_survives(self, sm):
        """Saving bytes by dropping the price would be worse than doing nothing."""
        row = sm.get_state_digest()["radar_opportunities"][0]
        for k in ("symbol", "action", "decision", "current_price", "entry_price",
                  "stop_loss", "take_profit", "score", "win_prob", "ml_prob",
                  "setup_grade", "status_label", "regime", "timeframe", "is_actionable"):
            assert k in row, f"the stream dropped {k}"

    def test_the_byte_budget(self, sm):
        """THE MEASURE. The denylist cannot catch a NEW heavy field; this does."""
        full = _nbytes(sm.get_state_snapshot())
        digest = _nbytes(sm.get_state_digest())
        assert digest < full * 0.45, (
            f"digest is {digest:,} bytes vs {full:,} ({digest / full:.0%}) — "
            "either a heavy field was added and is not in the denylist, or the "
            "slimming stopped working"
        )

    def test_the_saving_is_actually_large(self, sm):
        """Guards the other direction: a no-op 'digest' would still pass 0.45."""
        full = _nbytes(sm.get_state_snapshot())
        digest = _nbytes(sm.get_state_digest())
        assert digest < full - 1024, f"digest only saved {full - digest} bytes"


class TestTheDigestIsBounded:
    def test_radar_is_capped(self, sm):
        sm.radar_opportunities = [dict(RADAR_ROW, symbol=f"S{i}") for i in range(200)]
        digest = sm.get_state_digest()
        assert len(digest["radar_opportunities"]) == STREAM_RADAR_LIMIT
        assert digest["dropped"]["radar_items"] == 200 - STREAM_RADAR_LIMIT

    def test_decisions_are_capped(self, sm):
        sm.latest_decisions = {f"D{i}": _decision() for i in range(200)}
        digest = sm.get_state_digest()
        assert len(digest["latest_decisions"]) == STREAM_DECISION_LIMIT
        assert digest["dropped"]["decisions"] == 200 - STREAM_DECISION_LIMIT

    def test_logs_are_capped(self, sm):
        digest = sm.get_state_digest()
        assert len(digest["recent_logs"]) == STREAM_LOG_LIMIT

    def test_nothing_is_dropped_when_under_the_limit(self, sm):
        digest = sm.get_state_digest()
        assert digest["dropped"] == {"radar_items": 0, "decisions": 0}


class TestTheFullSnapshotIsUntouched:
    """The dashboard, terminal and console read /api/telemetry_state. Reducing
    that would silently truncate their radar."""

    def test_the_snapshot_still_carries_the_prose(self, sm):
        snap = sm.get_state_snapshot()
        row = snap["radar_opportunities"][0]
        assert "checks" in row
        assert "rejection_reasons" in row
        assert "invalidation_levels" in row

    def test_the_snapshot_is_not_capped(self, sm):
        sm.radar_opportunities = [dict(RADAR_ROW) for _ in range(200)]
        assert len(sm.get_state_snapshot()["radar_opportunities"]) == 200

    def test_the_snapshot_has_no_digest_marker(self, sm):
        assert "digest" not in sm.get_state_snapshot()

    def test_the_store_is_not_evicted(self, sm):
        """`copilot.py` looks up ARBITRARY symbols in `latest_decisions`, so the
        cap is applied to the digest only — evicting here would make a real query
        answer "no decision" for a symbol that has one."""
        sm.latest_decisions = {f"D{i}": _decision() for i in range(200)}
        sm.get_state_digest()
        assert len(sm.latest_decisions) == 200


class TestSlimmingIsStructural:
    def test_it_recurses_into_lists(self):
        value = [{"checks": {"a": 1}}, {"checks": {"b": 2}}]
        assert _slim_explainability(value) == [{}, {}]

    def test_it_leaves_unrelated_keys_alone(self):
        value = {"symbol": "EURUSD", "nested": {"keep": 1}}
        assert _slim_explainability(value) == {"symbol": "EURUSD", "nested": {"keep": 1}}

    def test_scalars_pass_through(self):
        assert _slim_explainability(1.5) == 1.5
        assert _slim_explainability("x") == "x"
        assert _slim_explainability(None) is None
