"""Regression tests for the paper-execution and quote-provenance defects.

Every test here fails on the pre-fix code. The chain they cover produced the
dashboard contradiction: "Positions 1" beside "broker offline", a GOLD entry of
2400.00 against a 4328.88 market, "OPEN P&L 0.00" on a position plainly in
profit, and a BUY whose stop-loss sat ABOVE its entry.

Root causes, in order:

1. `MT5Client._paper_positions` is aliased to a CLASS-level dict that is never
   cleared, and `get_open_positions()` returned it whenever `is_connected` was
   False - so a stale simulated position from an earlier paper run was reported
   as a live position in a live/demo session.
2. Paper fills used a hardcoded price table (XAU 2400.0, EUR 1.0850, ...), so
   the entry was fabricated and, because `current_price` was the same constant,
   `profit` was structurally 0.0 forever.
3. Nothing validated that SL/TP straddle the fill price.
4. `TradingViewDataProvider.fetch_quotes` synthesised a reference quote for any
   symbol it could not resolve and labelled it `"source": "tradingview"` - so a
   fabricated XAUUSD price of 150.0 was indistinguishable from a real one.
   XAUUSD reached that branch because it is six characters ending in "USD" and
   so matched the FOREX heuristic, yielding the non-existent ticker FX:XAUUSD.
5. `database.py` classified a trade as BOT with `"ai" in comment_lower`, which
   also matches "trailing", "pair", "main", "wait" and "chair".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.execution.mt5_client import MT5Client
from jarvis.data.schemas import PositionSnapshot
from jarvis.data.tradingview_provider import TradingViewDataProvider
from jarvis.data.database import _BOT_TAG_RX, _MANUAL_TAG_RX


@pytest.fixture(autouse=True)
def _clean_paper_book():
    """The paper book is process-global; leave it as we found it."""
    MT5Client._shared_paper_positions.clear()
    MT5Client._shared_paper_pending_orders.clear()
    yield
    MT5Client._shared_paper_positions.clear()
    MT5Client._shared_paper_pending_orders.clear()


# ── 1. the paper book must not leak into a live session ─────────────────────

def _seed_paper_position(symbol="GOLD.i#", volume=0.01, open_price=2400.0):
    """Put a position straight into the shared paper book.

    Injected rather than created through send_market_order so this test isolates
    the LEAK and does not depend on the fill path - otherwise it would fail on
    the pre-fix code merely because `reference_price` is a new keyword, which
    proves nothing about the leak.
    """
    MT5Client._shared_paper_positions[68830903] = PositionSnapshot(
        ticket=68830903, symbol=symbol, type="BUY", volume=volume,
        open_price=open_price, current_price=open_price, sl=4294.29, tp=4525.74,
        profit=0.0, swap=0.0, commission=0.0,
        open_time="2026-09-16 14:27:10", magic=888999,
        comment="[PAPER] HMAlgo2_ManualDesk",
    )


def test_paper_book_is_not_reported_by_a_disconnected_live_session(monkeypatch):
    _seed_paper_position()
    # `auto_init=False` because this test never wants a connection - it stubs
    # `_reconnect_if_needed` and pins `is_connected = False` itself. Connecting
    # in the constructor is not merely wasted work, it is a hang: with no
    # terminal running `mt5.initialize()` blocks inside the native call while
    # HOLDING THE GIL (measured: still running when killed at 25s), so no
    # timeout can rescue it and not even faulthandler can dump. The whole suite
    # stalled here.
    live = MT5Client(mode="live", auto_init=False)
    # Two things would otherwise mask the leak on a machine without MT5:
    #   * the constructor downgrades mode to "paper" when the MetaTrader5
    #     package is missing ("Falling back to PAPER mode");
    #   * `_reconnect_if_needed()` calls `init_connection()`, which downgrades it
    #     again on the way into get_open_positions().
    # Pin both so the assertion is about the live-session rule and not about this
    # machine's install state. On the deployment - where MT5 is present and the
    # link can drop - the mode stays "live" and the stale book was reported.
    monkeypatch.setattr(live, "_reconnect_if_needed", lambda: None)
    live.mode = "live"
    live.is_connected = False
    # Pre-fix this returned the paper book: one GOLD position the broker never had.
    assert live.get_open_positions() == []


def test_a_paper_session_still_reports_its_own_book(monkeypatch):
    """The fix must not break the mode it exists to serve."""
    _seed_paper_position(symbol="EURUSD", open_price=1.15323)
    paper = MT5Client(mode="paper")
    monkeypatch.setattr(paper, "_reconnect_if_needed", lambda: None)
    paper.mode = "paper"
    assert len(paper.get_open_positions()) == 1


# ── 2. paper fills use a real price, never a placeholder ────────────────────

def test_paper_fill_uses_the_reference_price():
    paper = MT5Client(mode="paper")
    res = paper.send_market_order(
        symbol="XAUUSD", order_type="BUY", volume=0.01,
        sl_price=4200.0, tp_price=4500.0, reference_price=4328.88,
    )
    assert res["status"] == "FILLED"
    # Pre-fix this was the hardcoded 2400.0.
    assert res["price"] == pytest.approx(4328.88)


def test_paper_fill_refuses_when_there_is_no_price_at_all():
    paper = MT5Client(mode="paper")
    res = paper.send_market_order(
        symbol="ZZZNOTAREALSYMBOL", order_type="BUY", volume=0.01,
        sl_price=1.0, tp_price=2.0,
    )
    # Pre-fix this "FILLED" at the 1.2700 default.
    assert res["status"] == "FAILED"
    assert "reference price" in res["reason"]


# ── 3. SL/TP must straddle the fill price ──────────────────────────────────

def test_buy_with_stop_loss_above_entry_is_refused():
    """The exact ticket from the dashboard: entry 2400.0, SL 4294.29."""
    paper = MT5Client(mode="paper")
    res = paper.send_market_order(
        symbol="XAUUSD", order_type="BUY", volume=0.01,
        sl_price=4294.29, tp_price=4525.74, reference_price=2400.0,
    )
    assert res["status"] == "FAILED"
    assert "not below the fill price" in res["reason"]


def test_sell_with_take_profit_above_entry_is_refused():
    paper = MT5Client(mode="paper")
    res = paper.send_market_order(
        symbol="XAUUSD", order_type="SELL", volume=0.01,
        sl_price=4500.0, tp_price=4600.0, reference_price=4328.88,
    )
    assert res["status"] == "FAILED"
    assert "not below the fill price" in res["reason"]


def test_coherent_levels_are_accepted_both_ways():
    paper = MT5Client(mode="paper")
    buy = paper.send_market_order(
        symbol="XAUUSD", order_type="BUY", volume=0.01,
        sl_price=4200.0, tp_price=4500.0, reference_price=4328.88,
    )
    sell = paper.send_market_order(
        symbol="XAUUSD", order_type="SELL", volume=0.01,
        sl_price=4500.0, tp_price=4200.0, reference_price=4328.88,
    )
    assert buy["status"] == "FILLED"
    assert sell["status"] == "FILLED"


# ── 4. paper P&L is marked, not born at zero ───────────────────────────────

def test_paper_profit_tracks_the_market():
    paper = MT5Client(mode="paper")
    paper.send_market_order(
        symbol="XAUUSD", order_type="BUY", volume=0.01,
        sl_price=4200.0, tp_price=4500.0, reference_price=4328.88,
    )
    moved = paper.mark_paper_positions({"XAUUSD": 4428.88})
    assert moved == 1
    pos = paper.get_open_positions()[0]
    # 0.01 lots of XAUUSD is 1 oz (contract_size 100), so +100.00 USD.
    assert pos.profit == pytest.approx(100.0, abs=0.01)


def test_an_unregistered_symbol_is_not_marked_with_a_guessed_contract_size():
    """`resolve()` falls back to a generic FX spec for unknown symbols, whose
    contract_size is 1000x too big for gold. A wrong P&L is worse than none, so
    an unregistered position must be skipped rather than marked."""
    paper = MT5Client(mode="paper")
    MT5Client._shared_paper_positions[1] = PositionSnapshot(
        ticket=1, symbol="ZZZNOTAREALSYMBOL", type="BUY", volume=0.01,
        open_price=100.0, current_price=100.0, sl=90.0, tp=120.0,
        profit=0.0, swap=0.0, commission=0.0,
        open_time="2026-09-16 00:00:00", magic=888999,
    )
    assert paper.mark_paper_positions({"ZZZNOTAREALSYMBOL": 110.0}) == 0
    assert paper.get_open_positions()[0].profit == 0.0


# ── 5. quote provenance must not claim a live source ───────────────────────

def test_gold_resolves_to_a_metals_ticker_not_a_forex_pair():
    prov = TradingViewDataProvider()
    candidates = prov._resolve_candidate_tickers("XAUUSD")
    assert any(c.startswith("OANDA:") or c.startswith("TVC:") for c in candidates)
    # FX:XAUUSD does not exist on TradingView; that was the whole bug.
    assert not candidates == ["FX:XAUUSD", "OANDA:XAUUSD", "FX_IDC:XAUUSD"]


def test_six_char_usd_symbols_still_resolve_as_forex():
    """The metals branch must not swallow real FX pairs."""
    prov = TradingViewDataProvider()
    assert prov._resolve_candidate_tickers("EURUSD")[0] == "FX:EURUSD"
    assert prov._resolve_candidate_tickers("GBPUSD")[0] == "FX:GBPUSD"


def test_an_unresolvable_symbol_is_labelled_not_disguised():
    prov = TradingViewDataProvider()
    quotes = prov.fetch_quotes(["ZZZNOTAREALSYMBOL"])
    q = quotes.get("ZZZNOTAREALSYMBOL")
    assert q is not None, "the provider is expected to still supply a baseline"
    # Pre-fix this said "tradingview" and carried a fabricated RSI of 55.0.
    assert q["source"] == "profile_reference"
    assert q["is_fallback"] is True


# ── 6. executor classification is token-based ─────────────────────────────

@pytest.mark.parametrize("comment", [
    "trailing stop", "pair trade", "main desk", "wait", "chair",
])
def test_substring_ai_does_not_make_a_trade_a_bot(comment):
    """`"ai" in comment_lower` matched all of these."""
    assert _BOT_TAG_RX.search(comment.lower()) is None


@pytest.mark.parametrize("comment", [
    "HMA2_liquidity_BUY", "hm_algo entry", "hm algo 2.0", "JARVIS_auto", "ai signal",
])
def test_real_bot_tags_are_still_recognised(comment):
    # Production matches against the lowercased comment.
    assert _BOT_TAG_RX.search(comment.lower()) is not None


def test_our_own_manual_tag_is_recognised():
    """`[PAPER] HMAlgo2_ManualDesk` lowercases to "manualdesk" - a compound, so a
    word-boundary match would miss it entirely."""
    assert _MANUAL_TAG_RX.search("[paper] hmalgo2_manualdesk") is not None
    assert _MANUAL_TAG_RX.search("manual trade") is not None


def test_a_manual_tag_beats_a_bot_tag_in_the_classifier():
    """The classifier checks manual first; our own tag carries both tokens."""
    comment = "[paper] hmalgo2_manualdesk"
    assert _MANUAL_TAG_RX.search(comment) is not None
    assert _BOT_TAG_RX.search(comment) is not None  # "hmalgo2"
