"""The "no entry price without an observed price" invariant (backlog item C2).

An entry price must derive from a price the market actually printed. When the
primary frame is empty — feed down, cold symbol — `market_context.build_context`
produces

    current_price = 0.0                 # market_context.py:78
    bid           = 0.0                 # :79
    ask           = 0.0 + spread*pip    # :80

and every entry price in the system is minted from `context.ask` (BUY) or
`context.bid` (SELL). Before this invariant existed, that unobserved price became a
real-looking order. Measured for BTCUSD: `entry_price = 0.02`, `stop_loss = -0.04`
— and the sizer, reading a risk distance of 0.06 against a 65 000 instrument,
returned **100 lots**, i.e. 6.5M USD of exposure for a 50 USD risk budget. The
last gate approved it, because `_is_finite(0.0)` is True and the inverted-geometry
comparisons (`stop_loss >= entry_price` for a BUY) are False when entry is ~0.

Three layers are pinned here, at the three places the invariant can be broken:

* `schemas.is_observed_price`  — the predicate itself (finite AND strictly positive)
* `dynamic_levels.calculate_levels` — refuses to MINT an entry price
* `decision_engine._compute_bias_and_levels` — refuses to emit a direction
* `trade_guard.validate_pre_execution` — refuses to AUTHORIZE the order

`tests/test_trade_guard.py::TestNonPositivePrices` covers the guard's own
behaviour; this module covers the predicate and the two producers, plus the chain.
"""

from datetime import datetime, timezone

import pytest

from jarvis.data.schemas import (
    MarketContext, StructureContext, LiquidityContext, VolatilityContext,
    MomentumContext, SessionContext, RegimeOutput, MarketRegime,
    DecisionObject, AccountSnapshot, is_observed_price,
)
from jarvis.data.symbol_registry import resolve
from jarvis.intelligence.dynamic_levels import DynamicRiskAndLevelsEngine
from jarvis.intelligence.decision_engine import DecisionEngine
from jarvis.intelligence.self_learning import SelfLearningEngine
from jarvis.intelligence.realtime_optimizer import RealtimeOptimizer
from jarvis.risk.trade_guard import TradeGuard

UTC = timezone.utc
SPREAD_PIPS = 2.0


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------

class TestIsObservedPrice:
    """Finite AND strictly positive. Both halves were separately missing."""

    @pytest.mark.parametrize("value", [1.0, 0.0001, 65000.0, 1e-9])
    def test_a_real_positive_price_is_observed(self, value):
        assert is_observed_price(value) is True

    @pytest.mark.parametrize("value", [0, 0.0, -0.0, -1.0, -0.0001])
    def test_zero_and_negative_are_not_observed(self, value):
        """`0.0` is finite, so a finiteness check alone admits it."""
        assert is_observed_price(value) is False

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_nan_and_infinity_are_not_observed(self, value):
        assert is_observed_price(value) is False

    @pytest.mark.parametrize("value", [None, "", "abc", [], {}])
    def test_a_non_number_is_not_observed(self, value):
        assert is_observed_price(value) is False

    def test_a_numeric_string_is_accepted(self):
        """`float("1.0850")` is a real number; the predicate coerces like the guard does."""
        assert is_observed_price("1.0850") is True


# ---------------------------------------------------------------------------
# Builders — an unobserved market, exactly as market_context produces it
# ---------------------------------------------------------------------------

def unobserved_context(symbol="EURUSD", price=0.0, bid=None, ask=None):
    """The context shape of an EMPTY primary frame.

    `bid` defaults to `price` and `ask` to `price + spread*pip`, mirroring
    `market_context.build_context` lines 78-80.
    """
    spec = resolve(symbol)
    return MarketContext(
        symbol=symbol,
        timestamp=datetime.now(UTC),
        current_price=price,
        bid=price if bid is None else bid,
        ask=(price + SPREAD_PIPS * spec.pip_size) if ask is None else ask,
        structure=StructureContext(bias="NEUTRAL", demand_zone=(0.0, 0.0), supply_zone=(0.0, 0.0)),
        liquidity=LiquidityContext(buy_side_liquidity=0.0, sell_side_liquidity=0.0),
        volatility=VolatilityContext(atr=0.0, current_spread_pips=SPREAD_PIPS, state="NORMAL"),
        momentum=MomentumContext(trend_score=0.0, adx=0.0),
        session=SessionContext(is_prime_session=True),
    )


def observed_context(symbol="EURUSD", price=1.0850):
    """A normal, observed market — the control case."""
    spec = resolve(symbol)
    return MarketContext(
        symbol=symbol,
        timestamp=datetime.now(UTC),
        current_price=price,
        bid=price - (SPREAD_PIPS * spec.pip_size) / 2.0,
        ask=price + (SPREAD_PIPS * spec.pip_size) / 2.0,
        structure=StructureContext(
            bias="BULLISH", demand_zone=(price * 0.99, price * 0.995),
            supply_zone=(price * 1.005, price * 1.01),
        ),
        liquidity=LiquidityContext(buy_side_liquidity=price * 1.01, sell_side_liquidity=price * 0.99),
        volatility=VolatilityContext(atr=price * 0.005, current_spread_pips=SPREAD_PIPS, state="NORMAL"),
        momentum=MomentumContext(trend_score=40.0, adx=28.0),
        session=SessionContext(is_prime_session=True),
    )


def regime():
    return RegimeOutput(primary_regime=MarketRegime.TREND_BULL, probabilities={}, confidence=0.85)


# ---------------------------------------------------------------------------
# Layer 1 — the levels engine refuses to mint an entry price
# ---------------------------------------------------------------------------

class TestLevelsRefuseAnUnobservedPrice:
    @pytest.mark.parametrize("symbol", ["EURUSD", "XAUUSD", "BTCUSD"])
    @pytest.mark.parametrize("bias", ["BUY", "SELL"])
    def test_no_levels_are_minted(self, symbol, bias):
        levels = DynamicRiskAndLevelsEngine().calculate_levels(
            unobserved_context(symbol), regime(), tentative_bias=bias
        )
        assert levels["data_unavailable"] is True
        assert levels["bias"] == "HOLD"
        assert levels["entry_price"] == 0.0
        assert levels["sl_price"] == 0.0
        assert levels["tp_price"] == 0.0
        assert levels["risk_dist"] == 0.0
        assert levels["rr_ratio"] == 0.0

    def test_the_refusal_keeps_the_result_shape_callers_index(self):
        """Both callers read keys off this dict, so a refusal cannot be a bare None."""
        levels = DynamicRiskAndLevelsEngine().calculate_levels(
            unobserved_context(), regime(), tentative_bias="BUY"
        )
        for key in (
            "bias", "entry_price", "sl_price", "tp_price", "tp1_price", "tp2_price",
            "risk_dist", "tp_dist", "rr_ratio", "first_target_price",
            "first_target_volume_pct", "runner_trail_distance_atr", "entry_type",
        ):
            assert key in levels, key

    def test_the_refusal_says_why(self):
        levels = DynamicRiskAndLevelsEngine().calculate_levels(
            unobserved_context(), regime(), tentative_bias="BUY"
        )
        assert levels["protocol_details"]["protocol"] == "NO_OBSERVED_PRICE"

    @pytest.mark.parametrize("price", [float("nan"), float("inf"), -1.0])
    def test_a_non_finite_or_negative_price_is_refused_too(self, price):
        levels = DynamicRiskAndLevelsEngine().calculate_levels(
            unobserved_context(price=price), regime(), tentative_bias="BUY"
        )
        assert levels["data_unavailable"] is True
        assert levels["entry_price"] == 0.0

    def test_a_zero_bid_alone_is_enough_to_refuse(self):
        """A SELL entry is minted from `bid`, so a zero bid is not tradeable either."""
        ctx = unobserved_context(price=1.0850, bid=0.0)
        levels = DynamicRiskAndLevelsEngine().calculate_levels(ctx, regime(), tentative_bias="SELL")
        assert levels["data_unavailable"] is True

    def test_an_observed_market_still_produces_real_levels(self):
        """The control: the guard must not refuse a normal context."""
        levels = DynamicRiskAndLevelsEngine().calculate_levels(
            observed_context(), regime(), tentative_bias="BUY"
        )
        assert "data_unavailable" not in levels
        assert levels["entry_price"] > 0.0
        assert levels["sl_price"] > 0.0
        assert levels["tp_price"] > 0.0
        assert levels["risk_dist"] > 0.0
        assert levels["sl_price"] < levels["entry_price"] < levels["tp_price"]

    def test_the_missing_price_warning_is_logged_once_per_symbol(self, caplog):
        """This runs per symbol per radar cycle; a per-fetch warning floods the log."""
        engine = DynamicRiskAndLevelsEngine()
        ctx = unobserved_context("EURUSD")
        with caplog.at_level("WARNING", logger="JARVIS_DynamicLevels"):
            for _ in range(25):
                engine.calculate_levels(ctx, regime(), tentative_bias="BUY")
        hits = [r for r in caplog.records if "No observed price for EURUSD" in r.getMessage()]
        assert len(hits) == 1

    def test_a_different_symbol_still_warns(self, caplog):
        engine = DynamicRiskAndLevelsEngine()
        with caplog.at_level("WARNING", logger="JARVIS_DynamicLevels"):
            engine.calculate_levels(unobserved_context("EURUSD"), regime(), tentative_bias="BUY")
            engine.calculate_levels(unobserved_context("XAUUSD"), regime(), tentative_bias="BUY")
        assert any("EURUSD" in r.getMessage() for r in caplog.records)
        assert any("XAUUSD" in r.getMessage() for r in caplog.records)

    def test_recovery_is_announced_and_re_arms_the_warning(self, caplog):
        engine = DynamicRiskAndLevelsEngine()
        with caplog.at_level("INFO", logger="JARVIS_DynamicLevels"):
            engine.calculate_levels(unobserved_context("EURUSD"), regime(), tentative_bias="BUY")
            caplog.clear()
            engine.calculate_levels(observed_context("EURUSD"), regime(), tentative_bias="BUY")
            assert any("restored" in r.getMessage() for r in caplog.records)
            caplog.clear()
            # A later outage must warn again, not stay silent.
            engine.calculate_levels(unobserved_context("EURUSD"), regime(), tentative_bias="BUY")
            assert any("No observed price for EURUSD" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Layer 2 — the decision engine refuses to emit a direction
# ---------------------------------------------------------------------------

@pytest.fixture
def decision_engine():
    return DecisionEngine(
        self_learning=SelfLearningEngine(db_path=":memory:"),
        realtime_optimizer=RealtimeOptimizer(db_path=":memory:"),
    )


class TestDecisionRefusesAnUnobservedPrice:
    """The bias is decided in the decision engine, not in the levels engine.

    `decision_action` becomes EXECUTE on `gate_passed and bias in (BUY, SELL)`, so a
    BUY/SELL verdict paired with a zero entry would have been marked executable. The
    direction has to stand down in the same breath as the prices.
    """

    @pytest.mark.parametrize("symbol", ["EURUSD", "XAUUSD", "BTCUSD"])
    def test_the_bias_is_forced_to_hold(self, decision_engine, symbol):
        bias, entry, sl, tp, risk_dist, rr, target_p, target_vol = (
            decision_engine._compute_bias_and_levels(
                unobserved_context(symbol), regime(), {}
            )
        )
        assert bias == "HOLD"
        assert (entry, sl, tp, risk_dist, rr, target_vol) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert target_p is None

    def test_the_return_shape_is_unchanged(self, decision_engine):
        """Callers unpack exactly 8 values; the refusal must not change the arity."""
        result = decision_engine._compute_bias_and_levels(unobserved_context(), regime(), {})
        assert isinstance(result, tuple) and len(result) == 8

    def test_a_nan_price_also_forces_hold(self, decision_engine):
        bias, entry, *_ = decision_engine._compute_bias_and_levels(
            unobserved_context(price=float("nan")), regime(), {}
        )
        assert (bias, entry) == ("HOLD", 0.0)

    def test_an_observed_market_is_unaffected(self, decision_engine):
        """The control: a real context still reaches a directional decision."""
        ctx = observed_context()
        ctx.structure = StructureContext(bias="BULLISH", bos=True, choch=False)
        ctx.momentum = MomentumContext(trend_score=30.0, adx=30.0)
        bias, entry, *_ = decision_engine._compute_bias_and_levels(ctx, regime(), {})
        assert bias == "BUY"
        assert entry > 0.0

    def test_the_warning_is_logged_once_per_symbol(self, decision_engine, caplog):
        ctx = unobserved_context("EURUSD")
        with caplog.at_level("WARNING", logger="JARVIS_DecisionEngine"):
            for _ in range(20):
                decision_engine._compute_bias_and_levels(ctx, regime(), {})
        hits = [r for r in caplog.records if "No observed price for EURUSD" in r.getMessage()]
        assert len(hits) == 1


# ---------------------------------------------------------------------------
# The provider must not launder a fabricated anchor into a candle series
# ---------------------------------------------------------------------------

def _quote(price=1.0850, source="tradingview", is_fallback=None):
    """A quote dict shaped like the provider's own payload."""
    try:
        num = float(price)
    except (TypeError, ValueError):
        num = 1.0850
    q = {
        "price": price, "open": num - 0.0005, "high": num + 0.0010,
        "low": num - 0.0010, "close": num, "change_val": 0.0005,
        "change_pct": 0.05, "volume": 12_000, "rsi": 52.0, "macd": 0.001,
        "recommendation": 0.2, "description": "EUR/USD", "source": source,
    }
    if is_fallback is not None:
        q["is_fallback"] = is_fallback
    return q


class TestProviderRefusesToBuildOnAFabricatedAnchor:
    """`fetch_candles` must not turn a fallback quote into an unlabelled series.

    `fetch_quotes` legitimately returns a LABELLED fallback (source
    "profile_reference", `is_fallback` True) when the network is down, and the
    display path and the execution path both refuse it. But the candle series is
    anchored to that quote — every bar, the "genuine live OHLCV" last bar included
    — so returning candles would be a fabrication indistinguishable from real data,
    and `fetch_real_candles` logs it as "Live TradingView candles". Returning None
    lets the tier hierarchy fall through to a source that labels itself.
    """

    @pytest.fixture
    def provider(self):
        from jarvis.data.tradingview_provider import TradingViewDataProvider
        return TradingViewDataProvider()

    def test_a_fallback_anchor_yields_no_candles(self, provider, monkeypatch):
        monkeypatch.setattr(
            provider, "fetch_quotes",
            lambda symbols, *a, **k: {"EURUSD": _quote(source="profile_reference", is_fallback=True)},
        )
        assert provider.fetch_candles("EURUSD", timeframe="1H", num_bars=50) is None

    def test_a_live_anchor_still_yields_candles(self, provider, monkeypatch):
        """The control: a real quote must still produce a full series."""
        monkeypatch.setattr(
            provider, "fetch_quotes",
            lambda symbols, *a, **k: {"EURUSD": _quote()},
        )
        candles = provider.fetch_candles("EURUSD", timeframe="1H", num_bars=50)
        assert candles is not None and len(candles) == 50
        assert candles[-1]["close"] == pytest.approx(1.0850)
        assert all(bar["close"] > 0 for bar in candles)

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), None])
    def test_a_non_positive_anchor_yields_no_candles(self, provider, monkeypatch, bad):
        """The old guard was `price <= 0.0`, which NaN slips through."""
        monkeypatch.setattr(
            provider, "fetch_quotes",
            lambda symbols, *a, **k: {"EURUSD": _quote(price=bad)},
        )
        assert provider.fetch_candles("EURUSD", timeframe="1H", num_bars=50) is None

    def test_a_missing_quote_yields_no_candles(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "fetch_quotes", lambda symbols, *a, **k: {})
        assert provider.fetch_candles("EURUSD", timeframe="1H", num_bars=50) is None


# ---------------------------------------------------------------------------
# The chain — an unobserved market cannot reach an authorized order
# ---------------------------------------------------------------------------

class TestTheChainRefuses:
    def test_an_unobserved_market_cannot_produce_an_authorized_order(self):
        """levels refuse -> decision says HOLD -> the gate rejects the geometry.

        Each layer is asserted separately above; this pins that they compose. The
        decision object is built by hand from the refusal, as a caller that ignored
        `data_unavailable` would, to prove the last gate holds on its own.
        """
        ctx = unobserved_context("BTCUSD")
        levels = DynamicRiskAndLevelsEngine().calculate_levels(ctx, regime(), tentative_bias="BUY")
        assert levels["data_unavailable"] is True

        dec = DecisionObject(
            symbol="BTCUSD", timestamp=datetime.now(UTC), regime=regime(),
            bias="BUY", probabilities={}, strategy="TEST",
            entry_price=levels["entry_price"], stop_loss=levels["sl_price"],
            take_profit=levels["tp_price"], risk_reward_ratio=levels["rr_ratio"],
            calculated_risk_percent=1.0, expected_value=1.0, model_confidence=0.7,
            adversarial_penalty=0.0, invalidation_levels=[], bull_case=[], bear_case=[],
            risk_factors=[], quality_gate=None, decision="EXECUTE", context=ctx,
        )
        acct = AccountSnapshot(
            login=1, server="XM", balance=10_000.0, equity=10_000.0, margin=0.0,
            free_margin=10_000.0, margin_level=0.0, leverage=500, trade_allowed=True,
        )
        guard = TradeGuard.validate_pre_execution(
            dec, acct, [], max_spread_pips=35.0, current_spread_pips=SPREAD_PIPS,
        )
        assert guard["passed"] is False
        assert any("positive" in r for r in guard["reasons"])
