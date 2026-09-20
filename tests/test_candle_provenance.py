"""Every candle series must declare whether its bars were OBSERVED or GENERATED (D5).

`stock_engine.generate_candles` and `india_engine.generate_candles` built every bar from
`np.random.RandomState(stable_seed(f"{symbol}_{tf}_{hour}"))` and then reported
`data_source: "live"` whenever the ANCHOR PRICE came from a live source
(`stock_engine.py:59`, `india_engine.py:46`). Measured (`.scratch/repro_d5_bar_provenance.py`):
a series rebuilt from the same seed matched the returned bars to within the 2dp rounding, i.e.
no bar was ever observed — and the last 12% of bars was a *guaranteed* monotonic surge
(`stock_engine.py:98`). The label described the anchor, not the series.

The provenance now travels WITH the series (`CandleSeries.source`), which also removes a race:
the engines are module-level singletons and the stocks screener drives them from a 16-thread
pool (`stock_service.py:57`), so a per-instance `_last_data_source` could be overwritten by
another symbol between the call and the read.
"""

import json

import pandas as pd
import pytest

from jarvis.data.market_data_provider import (
    CandleSeries,
    SOURCE_LIVE,
    SOURCE_SYNTHETIC_ANCHORED,
    SOURCE_CALIBRATED_FEED,
    SYNTHETIC_SOURCES,
)
from jarvis.stocks import stock_engine as se
from jarvis.stocks.stock_engine import StockIntelligenceEngine

LIVE_ANCHOR = {"price": 250.0, "source": "tradingview", "beta": 1.2, "name": "TESTCO"}
STATIC_ANCHOR = {"price": 250.0, "source": "calibrated", "beta": 1.2, "name": "TESTCO"}


def _bars(n=120, start=100.0):
    """A well-formed series the downstream math can consume."""
    out = []
    for i in range(n):
        c = start * (1.0 + 0.001 * i)
        out.append({
            "time": 1_700_000_000 + i * 86_400,
            "open": c - 0.5, "high": c + 1.0, "low": c - 1.0,
            "close": c, "volume": 1_000_000 + i,
        })
    return out


@pytest.fixture
def no_real_feed(monkeypatch):
    """`generate_candles` asks MT5 first; keep every test off the broker."""
    import jarvis.data.market_data_provider as mdp
    monkeypatch.setattr(mdp, "_try_mt5", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# The CandleSeries contract
# ---------------------------------------------------------------------------

class TestCandleSeriesContract:
    def test_it_is_still_a_list(self):
        """Every existing consumer treats it as one; it must not become an object."""
        s = CandleSeries(_bars(3))
        assert isinstance(s, list)
        assert len(s) == 3
        assert [b["close"] for b in s] == [b["close"] for b in _bars(3)]
        assert s[0] is _bars(1)[0] or s[0]["close"] == 100.0

    def test_it_carries_its_source(self):
        s = CandleSeries(_bars(3), source=SOURCE_SYNTHETIC_ANCHORED, anchor_source="tradingview")
        assert s.source == SOURCE_SYNTHETIC_ANCHORED
        assert s.anchor_source == "tradingview"

    def test_the_default_source_is_live(self):
        """A series built without a claim is the only one allowed to default to live."""
        assert CandleSeries(_bars(3)).source == SOURCE_LIVE

    @pytest.mark.parametrize("source,expected", [
        (SOURCE_LIVE, False),
        (SOURCE_SYNTHETIC_ANCHORED, True),
        (SOURCE_CALIBRATED_FEED, True),
    ])
    def test_is_synthetic(self, source, expected):
        assert CandleSeries(_bars(1), source=source).is_synthetic is expected

    def test_the_synthetic_set_is_exactly_the_two_generated_labels(self):
        assert SYNTHETIC_SOURCES == frozenset({SOURCE_SYNTHETIC_ANCHORED, SOURCE_CALIBRATED_FEED})
        assert SOURCE_LIVE not in SYNTHETIC_SOURCES

    def test_json_serialises_as_a_plain_array(self):
        """`_send_json({"candles": candles})` must not change shape."""
        payload = json.dumps({"candles": CandleSeries(_bars(2), source=SOURCE_LIVE)})
        assert json.loads(payload)["candles"] == _bars(2)

    def test_dataframe_construction_is_unchanged(self):
        df = pd.DataFrame(CandleSeries(_bars(4), source=SOURCE_SYNTHETIC_ANCHORED))
        assert len(df) == 4
        assert {"open", "high", "low", "close", "volume"} <= set(df.columns)

    def test_slicing_and_addition_still_work(self):
        s = CandleSeries(_bars(5), source=SOURCE_SYNTHETIC_ANCHORED)
        assert len(s[1:3]) == 2
        assert len(s + _bars(2)) == 7


# ---------------------------------------------------------------------------
# The stock engine
# ---------------------------------------------------------------------------

class TestStockEngineProvenance:
    def test_a_generated_series_is_not_labelled_live(self, monkeypatch, no_real_feed):
        """The defect: the anchor was live, so the generated series claimed `live`."""
        monkeypatch.setattr(se, "get_stock_profile", lambda sym: dict(LIVE_ANCHOR))
        candles = StockIntelligenceEngine().generate_candles("TESTCO", "1D", 120)
        assert candles.source != SOURCE_LIVE
        assert candles.source == SOURCE_SYNTHETIC_ANCHORED

    def test_the_anchor_provenance_is_kept_separately(self, monkeypatch, no_real_feed):
        monkeypatch.setattr(se, "get_stock_profile", lambda sym: dict(LIVE_ANCHOR))
        candles = StockIntelligenceEngine().generate_candles("TESTCO", "1D", 120)
        assert candles.anchor_source == "tradingview"
        assert candles.is_synthetic is True

    def test_a_static_anchor_is_calibrated_not_anchored(self, monkeypatch, no_real_feed):
        monkeypatch.setattr(se, "get_stock_profile", lambda sym: dict(STATIC_ANCHOR))
        candles = StockIntelligenceEngine().generate_candles("TESTCO", "1D", 120)
        assert candles.source == SOURCE_CALIBRATED_FEED

    def test_real_bars_are_labelled_live(self, monkeypatch):
        """The one branch that may say `live`."""
        import jarvis.data.market_data_provider as mdp
        monkeypatch.setattr(mdp, "_try_mt5", lambda *a, **k: _bars(120))
        candles = StockIntelligenceEngine().generate_candles("TESTCO", "1D", 120)
        assert candles.source == SOURCE_LIVE
        assert candles.is_synthetic is False

    def test_the_series_still_has_the_expected_bars(self, monkeypatch, no_real_feed):
        monkeypatch.setattr(se, "get_stock_profile", lambda sym: dict(LIVE_ANCHOR))
        candles = StockIntelligenceEngine().generate_candles("TESTCO", "1D", 120)
        assert len(candles) == 120
        assert candles[-1]["close"] == pytest.approx(250.0, abs=0.01)
        assert all(b["close"] > 0 for b in candles)

    def test_the_response_reads_the_series_not_the_instance(self, monkeypatch):
        """`_last_data_source` is per-INSTANCE on a singleton driven by 16 threads.

        Simulate the race directly: another symbol has just set the instance attribute to
        `live`. The response for THIS symbol must still report this symbol's own series.
        """
        engine = StockIntelligenceEngine()
        series = CandleSeries(_bars(120), source=SOURCE_CALIBRATED_FEED)
        monkeypatch.setattr(engine, "generate_candles", lambda *a, **k: series)
        monkeypatch.setattr(se, "get_stock_profile", lambda sym: dict(STATIC_ANCHOR))
        engine._last_data_source = SOURCE_LIVE          # <- the other thread's value

        result = engine.analyze_stock("RACETEST", "1D")
        assert result["data_source"] == SOURCE_CALIBRATED_FEED
        assert result["data_source"] != SOURCE_LIVE

    def test_the_response_never_claims_live_for_generated_bars(self, monkeypatch, no_real_feed):
        monkeypatch.setattr(se, "get_stock_profile", lambda sym: dict(LIVE_ANCHOR))
        result = StockIntelligenceEngine().analyze_stock("TESTCO", "1D")
        assert result["data_source"] in SYNTHETIC_SOURCES
        assert result["data_source"] != SOURCE_LIVE


# ---------------------------------------------------------------------------
# The India engine
# ---------------------------------------------------------------------------

class TestIndiaEngineProvenance:
    def _engine(self):
        from jarvis.india.india_engine import IndiaTechnicalEngine
        return IndiaTechnicalEngine()

    def test_a_generated_series_is_never_live(self, monkeypatch):
        """This engine has no live-bar branch at all, so `live` was never right."""
        import jarvis.india.india_engine as ie
        monkeypatch.setattr(ie, "get_india_profile", lambda sym: {"price": 22000.0, "source": "tradingview"})
        candles = self._engine().generate_candles("NIFTY", "1D", 120)
        assert candles.source != SOURCE_LIVE
        assert candles.source == SOURCE_SYNTHETIC_ANCHORED

    def test_a_static_anchor_is_calibrated(self, monkeypatch):
        import jarvis.india.india_engine as ie
        monkeypatch.setattr(ie, "get_india_profile", lambda sym: {"price": 22000.0, "source": "calibrated"})
        candles = self._engine().generate_candles("NIFTY", "1D", 120)
        assert candles.source == SOURCE_CALIBRATED_FEED

    def test_the_delegate_reports_the_same_provenance(self, monkeypatch):
        """`_generate_synthetic_candles` is a documented delegate to `generate_candles`."""
        import jarvis.india.india_engine as ie
        monkeypatch.setattr(ie, "get_india_profile", lambda sym: {"price": 22000.0, "source": "tradingview"})
        engine = self._engine()
        assert engine._generate_synthetic_candles("NIFTY", "1D", 30).source == (
            engine.generate_candles("NIFTY", "1D", 30).source
        )


# ---------------------------------------------------------------------------
# The providers
# ---------------------------------------------------------------------------

class TestProviderProvenance:
    def test_the_tradingview_series_declares_synthetic_anchored(self, monkeypatch):
        """Only the LAST bar is observed; the rest is a backward random walk."""
        from jarvis.data.tradingview_provider import TradingViewDataProvider
        provider = TradingViewDataProvider()
        quote = {
            "price": 1.0850, "open": 1.0845, "high": 1.0860, "low": 1.0840,
            "close": 1.0850, "change_pct": 0.05, "volume": 12_000, "source": "tradingview",
        }
        monkeypatch.setattr(provider, "fetch_quotes", lambda symbols, *a, **k: {"EURUSD": quote})
        candles = provider.fetch_candles("EURUSD", timeframe="1H", num_bars=50)
        assert candles.source == SOURCE_SYNTHETIC_ANCHORED
        assert candles.anchor_source == "tradingview"
        assert len(candles) == 50

    def test_the_calibrated_baseline_declares_synthetic_calibrated(self, monkeypatch):
        import jarvis.data.market_data_provider as mdp
        import jarvis.stocks.universe as su
        monkeypatch.setattr(su, "get_stock_profile", lambda sym: {"base_price": 150.0, "beta": 1.2})
        candles = mdp.get_calibrated_baseline_candles("AAPL", "1D", 20, market="US")
        assert candles.source == SOURCE_CALIBRATED_FEED
        assert candles.is_synthetic is True
