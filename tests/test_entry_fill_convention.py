"""The entry-fill convention: one spread, charged in the correct direction.

`engine.py` charged the spread on both sides. `tools/scan_signals.py` charged it
on **BUY only**, so every SELL candidate in the cached audit tables entered at
the mid. `trade_simulator` applies no spread on exits either, so half of ~95,000
audited trades carried no spread cost at all — and the audit's whole conclusion
rests on those tables.

Measured on the stored tables before the fix, `(fill - next_open) / pip` divided
by the row's own spread was **+1.000 for BUY and 0.000 for SELL**, on every
symbol checked. These tests pin the convention so a third copy cannot drift.

The lesson is not "fix the second copy" but "delete it": both callers import
`entry_fill`, and `test_both_callers_share_one_convention` fails if anyone
re-inlines the arithmetic.
"""

import inspect

import pytest

from jarvis.backtesting.fills import entry_fill, spread_price


class TestEntryFill:
    def test_buy_pays_the_ask(self):
        # open 2000.0, spread 2 pips of 0.1 = 0.2 -> ask 2000.2
        assert entry_fill(2000.0, "BUY", 2.0, 0.1) == pytest.approx(2000.2)

    def test_sell_is_filled_at_the_bid(self):
        # The defect: this used to return the mid, 2000.0.
        assert entry_fill(2000.0, "SELL", 2.0, 0.1) == pytest.approx(1999.8)

    def test_both_sides_pay_exactly_one_spread(self):
        """The invariant that actually matters: |fill - open| == spread, both ways."""
        for bias in ("BUY", "SELL"):
            fill = entry_fill(1.2345, bias, 1.9, 0.0001)
            assert abs(fill - 1.2345) == pytest.approx(spread_price(1.9, 0.0001))

    def test_the_two_sides_are_symmetric_about_the_open(self):
        buy = entry_fill(100.0, "BUY", 3.0, 0.01)
        sell = entry_fill(100.0, "SELL", 3.0, 0.01)
        assert buy - 100.0 == pytest.approx(100.0 - sell)
        assert (buy + sell) / 2 == pytest.approx(100.0)

    def test_round_trip_cost_is_one_spread_not_two(self):
        """A long and a short at the same bar must cost the same amount."""
        buy_cost = entry_fill(100.0, "BUY", 3.0, 0.01) - 100.0
        sell_cost = 100.0 - entry_fill(100.0, "SELL", 3.0, 0.01)
        assert buy_cost == pytest.approx(sell_cost)

    def test_zero_spread_is_the_reference_price(self):
        assert entry_fill(100.0, "BUY", 0.0, 0.01) == pytest.approx(100.0)
        assert entry_fill(100.0, "SELL", 0.0, 0.01) == pytest.approx(100.0)

    def test_bias_is_case_and_whitespace_insensitive(self):
        assert entry_fill(100.0, " sell ", 3.0, 0.01) == pytest.approx(99.97)
        assert entry_fill(100.0, "buy", 3.0, 0.01) == pytest.approx(100.03)

    def test_unknown_bias_defaults_to_long(self):
        """Matches the codebase convention: anything not an explicit SELL is a BUY."""
        assert entry_fill(100.0, "", 3.0, 0.01) == pytest.approx(100.03)
        assert entry_fill(100.0, None, 3.0, 0.01) == pytest.approx(100.03)


class TestBothCallersShareOneConvention:
    """Guard the real failure mode: a second, divergent copy of the arithmetic."""

    def test_scanner_delegates_to_entry_fill(self):
        from jarvis.backtesting import signal_scan

        src = inspect.getsource(signal_scan)
        assert "entry_fill(" in src, "the scanner stopped calling entry_fill"
        # An inline `entry_price += spread` means the arithmetic came back.
        assert "entry_price += spread_pips" not in src
        assert "entry_price -= spread_pips" not in src

    def test_engine_delegates_to_entry_fill(self):
        from jarvis.backtesting import engine

        src = inspect.getsource(engine)
        assert "entry_fill(" in src, "the engine stopped calling entry_fill"
        assert "entry_price += spread_pips" not in src
        assert "entry_price -= spread_pips" not in src

    def test_no_module_reimplements_the_fill(self):
        """Only `fills.py` may contain the arithmetic."""
        import pathlib

        repo = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in list(repo.glob("jarvis/**/*.py")) + list(repo.glob("tools/**/*.py")):
            if path.name == "fills.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "spread_pips * spec.pip_size" in text and "entry_fill" not in text:
                offenders.append(str(path.relative_to(repo)))
        assert not offenders, (
            "these reimplement the spread arithmetic instead of calling entry_fill: "
            f"{offenders}"
        )
