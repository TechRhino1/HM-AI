"""The stocks/india candle path must never call `mt5.initialize()` on a request thread.

`/api/stocks/candles` -> `STOCK_ENGINE.generate_candles` -> `_try_mt5` ->
`_resolve_mt5_symbol` -> `_ensure_mt5_connected`, which ended in a bare
``return bool(mt5.initialize())``.

With no terminal running, `initialize()` blocks inside native code **holding the GIL** and never
returns — measured: a child process calling ``generate_candles("NVDA")`` was still alive after 25s
and had to be killed (`.scratch/prove_mt5_block.py`). Because the GIL is held, no other thread runs
either, so ONE request wedged the entire server: the process stayed alive and kept logging while
answering nothing. The same child now exits in ~4s.

These tests pin the MECHANISM rather than a wall-clock bound: the reason the path is bounded is
that `initialize()` is never called. Asserting "it did not call initialize" is both exact and
non-flaky, and it fails for the right reason if the guard is removed. A timing assertion would
merely hang the suite when the guard regresses.
"""

from types import SimpleNamespace

import pytest

from jarvis.data import broker_symbols as bs
from jarvis.data import market_data_provider as mdp

# `broker_symbols._mt5()` imports the package lazily and returns the real module, so the
# only place an `initialize` call can be intercepted is the module itself.
import MetaTrader5 as real_mt5


@pytest.fixture
def no_terminal(monkeypatch):
    """No terminal process, and no live terminal_info() to short-circuit on."""
    monkeypatch.setattr(mdp, "MT5_AVAILABLE", True)
    monkeypatch.setattr(mdp, "mt5", SimpleNamespace(terminal_info=lambda: None), raising=False)
    monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)
    bs.reset_cache()
    yield
    bs.reset_cache()


@pytest.fixture
def spy_initialize(monkeypatch):
    """Record every `mt5.initialize()` call without performing one."""
    called = []
    monkeypatch.setattr(real_mt5, "initialize", lambda *a, **k: called.append((a, k)) or True)
    return called


class TestEnsureMt5ConnectedNeverLaunches:
    def test_no_terminal_means_no_initialize_call(self, monkeypatch, no_terminal, spy_initialize):
        """The fix. Pre-fix this called `initialize()` and the process never returned."""
        assert mdp._ensure_mt5_connected() is False
        assert spy_initialize == [], (
            "initialize() was called with no terminal running: that call blocks inside native "
            "code holding the GIL and never returns"
        )

    def test_it_delegates_to_the_read_path_gate(self, monkeypatch, no_terminal):
        """The gate owns the retry throttle and the once-per-outage logging."""
        monkeypatch.setattr(bs, "ensure_mt5_terminal", lambda *a, **k: True)
        assert mdp._ensure_mt5_connected() is True

    def test_the_gate_is_never_asked_to_launch(self, monkeypatch, no_terminal):
        """Launching a terminal is a BOOT-TIME decision (HM_start.py), never a read."""
        seen = {}

        def _gate(*args, **kwargs):
            seen.update(kwargs)
            seen["args"] = args
            return False

        monkeypatch.setattr(bs, "ensure_mt5_terminal", _gate)
        mdp._ensure_mt5_connected()
        assert seen.get("allow_launch") in (None, False), (
            "a read path must not pass allow_launch=True"
        )

    def test_a_connected_terminal_short_circuits_without_initializing(self, monkeypatch, spy_initialize):
        monkeypatch.setattr(mdp, "MT5_AVAILABLE", True)
        monkeypatch.setattr(
            mdp, "mt5",
            SimpleNamespace(terminal_info=lambda: SimpleNamespace(connected=True)),
            raising=False,
        )
        assert mdp._ensure_mt5_connected() is True
        assert spy_initialize == []

    def test_mt5_unavailable_returns_false(self, monkeypatch):
        monkeypatch.setattr(mdp, "MT5_AVAILABLE", False)
        assert mdp._ensure_mt5_connected() is False

    def test_a_raising_terminal_info_returns_false(self, monkeypatch, no_terminal):
        """A broken probe must not propagate out of the candle path."""
        def _boom():
            raise RuntimeError("terminal_info exploded")

        monkeypatch.setattr(mdp, "mt5", SimpleNamespace(terminal_info=_boom), raising=False)
        assert mdp._ensure_mt5_connected() is False

    def test_a_real_call_with_no_terminal_returns_false(self):
        """No monkeypatching: the real path must answer, and answer False, here.

        Pre-fix this line never returned. Skipped when a terminal IS running, since then
        the honest answer is True and the point of the test is the no-terminal case.
        """
        bs.reset_cache()
        if bs._terminal_process_running() is True:
            pytest.skip("a MetaTrader terminal is running; the no-terminal case is not reachable")
        assert mdp._ensure_mt5_connected() is False


class TestTheGateIsTheOneThatDecides:
    def test_an_unanswerable_process_check_falls_through_rather_than_inventing_a_dead_terminal(
        self, monkeypatch, spy_initialize
    ):
        """`_terminal_process_running()` returns None when psutil is missing.

        The gate must then proceed as it did before the check existed, so a machine without
        psutil is no worse off. Asserted at the gate, which is where the decision lives.
        """
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: None)
        bs.reset_cache()
        bs.ensure_mt5_terminal()
        assert spy_initialize, "an unanswerable check must not be treated as 'no terminal'"
        bs.reset_cache()
