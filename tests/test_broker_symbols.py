"""Tests for jarvis.data.broker_symbols.

This module decides whether the platform reads the instrument you asked for or a
different one — and a wrong instrument that *looks* healthy is the worst outcome
in the system, because every downstream gate (freshness, LIVE_MT5 stamp, the
execution gate) then certifies it. Two past incidents are encoded in its
comments and are pinned here as regression tests:

* **A bare substring scan resolved COPPER to `SouthernCopper`** — Southern Copper
  Corp, an equity — and handed it back as the copper commodity. The fuzzy scan is
  now PREFIX-ONLY (`test_a_substring_match_is_never_used`).
* **A failed probe used to be cached even when the terminal was down**, so one
  call before the terminal was ready pinned every symbol to None for the life of
  the process. Now the terminal is asked for FIRST and a "terminal down" result
  is never cached (`test_a_failed_resolution_is_not_cached_when_the_terminal_is_down`).

Why `resolve_broker_symbol` returns None instead of the input symbol:
None means "this broker does not offer it" and callers skip the symbol. Two
OTHER resolvers in this codebase return the input unchanged on failure
(`mt5_history.resolve_broker_symbol`, `MT5Client.resolve_symbol_name`), which
makes "unresolved" indistinguishable from "resolves to itself" — and in
`MT5Client`'s case returns the canonical name in PAPER MODE, which is the
execution-mode-gates-market-data trap. They are not fixed here; see the module
docstring of this file and the note at the bottom of this suite.

ALSO PINNED
-----------
* `terminal_ready()` latches True on success and never re-checks. Deliberate:
  consumers use it to decide whether an EMPTY frame means "broker does not offer
  this symbol", and staying True keeps that check firing (fail closed). It is not
  a liveness probe.
* The fuzzy scan probes candidates in the order `mt5.symbols_get()` returns them,
  so which alias wins is the broker's business, not ours.
* `_INIT_RETRY_SEC` exists so a 1 Hz poll loop cannot hammer `initialize()`.
"""

import sys
import time

import pytest

import jarvis.data.broker_symbols as bs

H1 = 16385


# ---------------------------------------------------------------------------
# A fake MetaTrader5. `_mt5()` imports it lazily, so putting it in sys.modules
# is all that is needed; None makes the import raise ImportError.
# ---------------------------------------------------------------------------

class FakeSymbol:
    def __init__(self, name):
        self.name = name


class FakeMT5:
    TIMEFRAME_H1 = H1

    def __init__(self, known=(), with_bars=None, initialize=True, terminal_info=True,
                 symbols=None, raise_on=(), zero_bars=()):
        self.known = set(known)                       # symbol_info() answers these
        self.with_bars = set(with_bars if with_bars is not None else known)
        self.zero_bars = set(zero_bars)               # answers, but with 0 rows
        self._initialize = initialize
        self._terminal_info = terminal_info
        self.symbols = list(symbols if symbols is not None else known)
        self.raise_on = set(raise_on)
        self.calls = {"initialize": 0, "terminal_info": 0, "symbol_info": [],
                      "symbol_select": [], "copy_rates": [], "symbols_get": 0}
        self.init_timeouts = []

    def initialize(self, **kw):
        # Mirrors the real signature: `initialize(timeout=...)` is the ONLY way
        # to stop a dead terminal blocking for 60s while holding the GIL, so a
        # fake that swallows the kwarg would hide the defect entirely.
        self.calls["initialize"] += 1
        self.init_timeouts.append(kw.get("timeout"))
        return self._initialize

    def last_error(self):
        return (-1, "fake terminal error")

    def terminal_info(self):
        self.calls["terminal_info"] += 1
        return object() if self._terminal_info else None

    def symbol_info(self, name):
        self.calls["symbol_info"].append(name)
        if name in self.raise_on:
            raise RuntimeError("boom")
        return FakeSymbol(name) if name in self.known else None

    def symbol_select(self, name, enable):
        self.calls["symbol_select"].append(name)
        return True

    def copy_rates_from_pos(self, name, tf, start, count):
        self.calls["copy_rates"].append(name)
        if name not in self.with_bars:
            return None
        if name in self.zero_bars:
            return []
        return [{"time": 0}] * count

    def symbols_get(self):
        self.calls["symbols_get"] += 1
        return [FakeSymbol(n) for n in self.symbols]


@pytest.fixture
def broker(monkeypatch):
    """Install a fake MT5 and return a factory; state is cleared around each test."""
    bs.reset_cache()
    monkeypatch.delenv("JARVIS_BACKTEST_MODE", raising=False)
    installed = {}

    def install(**kw):
        fake = FakeMT5(**kw)
        monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
        installed["mt5"] = fake
        return fake

    install(known=(), initialize=True)
    yield install
    bs.reset_cache()


# ---------------------------------------------------------------------------
# ensure_mt5_terminal
# ---------------------------------------------------------------------------

class TestEnsureTerminal:
    def test_a_healthy_terminal_initializes_once(self, broker):
        mt5 = broker(known=["XAUUSD"])
        assert bs.ensure_mt5_terminal() is True
        assert bs.ensure_mt5_terminal() is True          # cached, no second attach
        assert mt5.calls["initialize"] == 1

    def test_terminal_info_is_consulted(self, broker):
        mt5 = broker(known=[])
        bs.ensure_mt5_terminal()
        assert mt5.calls["terminal_info"] == 1

    def test_a_failed_initialize_is_not_ready(self, broker):
        broker(initialize=False)
        assert bs.ensure_mt5_terminal() is False
        assert bs.terminal_ready() is False

    def test_initialize_ok_but_no_terminal_info_is_not_ready(self, broker):
        """initialize() succeeding is not the same as a terminal being there."""
        broker(initialize=True, terminal_info=False)
        assert bs.ensure_mt5_terminal() is False
        assert bs.terminal_ready() is False

    def test_a_failure_is_retried_only_after_the_retry_window(self, broker, monkeypatch):
        mt5 = broker(initialize=False)
        assert bs.ensure_mt5_terminal() is False
        assert bs.ensure_mt5_terminal() is False         # inside the 30s window
        assert mt5.calls["initialize"] == 1
        monkeypatch.setattr(bs, "_LAST_INIT_ATTEMPT", time.time() - bs._INIT_RETRY_SEC - 1)
        assert bs.ensure_mt5_terminal() is False
        assert mt5.calls["initialize"] == 2

    def test_backtest_mode_never_touches_a_terminal(self, broker, monkeypatch):
        monkeypatch.setenv("JARVIS_BACKTEST_MODE", "1")
        mt5 = broker(known=["XAUUSD"])
        assert bs.ensure_mt5_terminal() is False
        assert mt5.calls["initialize"] == 0
        assert bs.resolve_broker_symbol("XAUUSD") is None

    def test_a_missing_metatrader5_package_is_not_fatal(self, broker, monkeypatch):
        monkeypatch.setitem(sys.modules, "MetaTrader5", None)   # import raises
        assert bs.ensure_mt5_terminal() is False
        assert bs.terminal_ready() is False

    def test_an_exception_from_initialize_is_swallowed(self, broker, monkeypatch):
        class Boom:
            TIMEFRAME_H1 = H1
            def initialize(self, **kw):
                raise RuntimeError("terminal on fire")
            def last_error(self):
                return (-1, "boom")
        monkeypatch.setitem(sys.modules, "MetaTrader5", Boom())
        assert bs.ensure_mt5_terminal() is False

    def test_initialize_is_always_bounded(self, broker):
        """A dead terminal must cost seconds, not the package's 60s default.

        Measured consequence of the default: six workers inside `initialize()`
        held the GIL almost continuously, so `Thread.start()` could not complete
        and the main thread never built `ThreadingHTTPServer` — HM stayed up for
        40+ minutes with no listener on :8501. A Python-side timeout cannot
        interrupt the native call, so this kwarg is the only lever there is.
        """
        mt5 = broker(initialize=False)
        bs.ensure_mt5_terminal()
        assert mt5.init_timeouts == [bs._INIT_TIMEOUT_MS]
        assert 0 < bs._INIT_TIMEOUT_MS <= 30_000, (
            f"_INIT_TIMEOUT_MS={bs._INIT_TIMEOUT_MS} is not a useful bound; the "
            f"point is to be far below the package's 60s default"
        )

    def test_the_retry_still_pays_the_bound_every_time(self, broker, monkeypatch):
        """A bounded call must not become an unbounded one on the retry path."""
        mt5 = broker(initialize=False)
        bs.ensure_mt5_terminal()
        monkeypatch.setattr(bs, "_LAST_INIT_ATTEMPT", time.time() - bs._INIT_RETRY_SEC - 1)
        bs.ensure_mt5_terminal()
        assert mt5.init_timeouts == [bs._INIT_TIMEOUT_MS, bs._INIT_TIMEOUT_MS]

    def test_a_stand_in_without_the_kwarg_is_still_used(self, broker, monkeypatch):
        """An injected module that predates `timeout=` must not lose the attempt."""
        class Old:
            TIMEFRAME_H1 = H1
            def initialize(self):
                return True
            def terminal_info(self):
                return object()
            def last_error(self):
                return (-1, "n/a")
        assert bs.ensure_mt5_terminal(mt5_module=Old()) is True


class TestNoTerminalProcess:
    """`initialize()` must not be called when there is nothing to attach to.

    Measured: with no terminal running, `initialize()` tries to LAUNCH one and
    blocks 60-100s inside native code holding the GIL, and the `timeout=`
    argument does not bound it — `initialize(timeout=8000)` had still not
    returned after 100s. On `HM_start.py live` that leaves a process that has
    bound :8501 and never answers a request, because the accept loop can never
    take the GIL. Asking the OS whether the terminal is running costs
    microseconds and cannot block.
    """

    def _real_looking(self, broker, **kw):
        mt5 = broker(**kw)
        # Only the imported package is gated on the machine's process list.
        mt5.__file__ = "C:/fake/MetaTrader5/__init__.py"
        return mt5

    def test_no_terminal_process_means_no_in_process_initialize(self, broker, monkeypatch):
        mt5 = self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)
        assert bs.ensure_mt5_terminal() is False
        assert mt5.calls["initialize"] == 0, (
            "called initialize() with no terminal running — that is the call that "
            "freezes the whole process for 60-100s"
        )
        assert bs.terminal_ready() is False

    def test_a_running_terminal_process_still_initializes(self, broker, monkeypatch):
        mt5 = self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: True)
        assert bs.ensure_mt5_terminal() is True
        assert mt5.calls["initialize"] == 1

    def test_a_stand_in_is_never_gated_on_the_desktop(self, broker, monkeypatch):
        """A test fake must not depend on whether a terminal runs on this machine."""
        mt5 = broker(known=["XAUUSD"])          # no __file__ -> not the real package
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)
        assert bs.ensure_mt5_terminal() is True
        assert mt5.calls["initialize"] == 1

    def test_an_unanswerable_process_check_is_not_a_veto(self, broker, monkeypatch):
        """No psutil -> no opinion -> behave as before this check existed."""
        mt5 = self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: None)
        assert bs.ensure_mt5_terminal() is True
        assert mt5.calls["initialize"] == 1

    def test_allow_launch_restores_the_old_behaviour(self, broker, monkeypatch):
        mt5 = self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)
        monkeypatch.setenv("JARVIS_MT5_ALLOW_LAUNCH", "1")
        assert bs.ensure_mt5_terminal() is True
        assert mt5.calls["initialize"] == 1

    def test_the_boot_path_may_launch_the_terminal(self, broker, monkeypatch):
        """`HM_start` is allowed to LAUNCH the terminal; read paths are not.

        Launching used to be a side effect of `initialize()`, so refusing it on
        every path left `HM_start.bat live` booting, serving, and reporting
        SYNTHETIC bars with no way to trade.
        """
        mt5 = self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)

        assert bs.ensure_mt5_terminal(allow_launch=True) is True
        assert mt5.calls["initialize"] == 1, "the boot path must be allowed to launch"

    def test_a_read_path_still_refuses_to_launch(self, broker, monkeypatch):
        """Control for the above: the default must keep the freeze fixed."""
        mt5 = self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)

        assert bs.ensure_mt5_terminal() is False
        assert mt5.calls["initialize"] == 0

    def test_a_cheap_refusal_does_not_open_a_retry_window(self, broker, monkeypatch):
        """The throttle must bound only the call that can actually block.

        Regression: the retry throttle ran BEFORE the cheap process check, so a
        single "no terminal" answer refused *every* caller for the next 30s -
        even though the answer was already known and the refusal cost nothing.
        """
        self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)

        assert bs.ensure_mt5_terminal() is False
        assert bs._LAST_INIT_ATTEMPT == 0.0, (
            "a refusal that cost nothing opened a 30s window that blocks everyone"
        )

    def test_a_stand_in_caller_survives_an_earlier_refusal(self, broker, monkeypatch):
        """The victim shape, reproduced directly.

        Refuse the real package (no terminal), then serve a stand-in caller - the
        way the suite drives `sync_mt5_history`. Before the fix the second call
        was refused from the first call's retry window, which is what made 3
        `test_position_id_join` tests and 1 `test_fill_origin` test fail in a full
        run while each passed in isolation.
        """
        self._real_looking(broker, known=["XAUUSD"])
        monkeypatch.setattr(bs, "_terminal_process_running", lambda: False)
        assert bs.ensure_mt5_terminal() is False

        stand_in = broker(known=["XAUUSD"])   # no __file__ -> cannot block
        assert bs.ensure_mt5_terminal() is True, (
            "the earlier refusal blacked out a caller that could not block"
        )
        assert stand_in.calls["initialize"] == 1

    def test_the_real_package_is_recognised(self):
        """`_is_real_mt5_package` must not be trivially True or False.

        The whole gate hangs off this: if it returned True for a test fake, the
        fake's outcome would depend on the developer's desktop; if it returned
        False for the real package, the 60-100s freeze would come back.
        """
        import types
        real = types.ModuleType("MetaTrader5")
        real.__file__ = "C:/.../site-packages/MetaTrader5/__init__.py"
        assert bs._is_real_mt5_package(real) is True
        assert bs._is_real_mt5_package(object()) is False
        assert bs._is_real_mt5_package(None) is False

    def test_the_installed_package_actually_has_a_file(self):
        """Guards the guard: the real module must expose `__file__`, or
        `_is_real_mt5_package` would silently disable the process check."""
        import MetaTrader5
        assert isinstance(getattr(MetaTrader5, "__file__", None), str)

    def test_the_first_call_always_attempts(self, broker, monkeypatch):
        """_LAST_INIT_ATTEMPT starts at 0, so a fresh process is never throttled."""
        monkeypatch.setattr(bs, "_LAST_INIT_ATTEMPT", 0.0)
        mt5 = broker(known=[])
        bs.ensure_mt5_terminal()
        assert mt5.calls["initialize"] == 1


# ---------------------------------------------------------------------------
# terminal_ready
# ---------------------------------------------------------------------------

class TestTerminalReady:
    def test_false_before_any_initialization(self, broker):
        assert bs.terminal_ready() is False

    def test_true_after_a_successful_initialization(self, broker):
        broker(known=[])
        bs.ensure_mt5_terminal()
        assert bs.terminal_ready() is True

    def test_it_is_read_only_and_never_attaches(self, broker):
        mt5 = broker(known=[])
        bs.terminal_ready()
        bs.terminal_ready()
        assert mt5.calls["initialize"] == 0

    def test_it_latches_true_and_is_not_a_liveness_probe(self, broker):
        """Documented: it stays True after the terminal dies, so callers that use
        it to distrust an empty frame keep distrusting them (fail closed)."""
        broker(known=[])
        bs.ensure_mt5_terminal()
        assert bs.terminal_ready() is True


# ---------------------------------------------------------------------------
# probe_symbol
# ---------------------------------------------------------------------------

class TestProbeSymbol:
    def test_a_known_symbol_with_bars(self, broker):
        broker(known=["EURUSD"], with_bars=["EURUSD"])
        assert bs.probe_symbol("EURUSD") is True

    def test_an_unknown_symbol(self, broker):
        """No symbol_info means unusable — even if copy_rates would answer."""
        broker(known=[], with_bars=["EURUSD"])
        assert bs.probe_symbol("EURUSD") is False

    def test_a_known_symbol_that_returns_no_bars(self, broker):
        """In Market Watch but not streaming — not usable."""
        broker(known=["EURUSD"], with_bars=[])
        assert bs.probe_symbol("EURUSD") is False

    def test_a_known_symbol_that_returns_zero_bars(self, broker):
        """An empty array is not the same as None, and is still unusable."""
        broker(known=["EURUSD"], with_bars=["EURUSD"], zero_bars=["EURUSD"])
        assert bs.probe_symbol("EURUSD") is False

    def test_it_selects_the_symbol_into_market_watch(self, broker):
        mt5 = broker(known=["EURUSD"])
        bs.probe_symbol("EURUSD")
        assert "EURUSD" in mt5.calls["symbol_select"]

    def test_it_fetches_h1_bars(self, broker):
        mt5 = broker(known=["EURUSD"])
        bs.probe_symbol("EURUSD")
        assert mt5.calls["copy_rates"] == ["EURUSD"]

    def test_an_exception_is_not_fatal(self, broker):
        broker(known=["EURUSD"], raise_on=["EURUSD"])
        assert bs.probe_symbol("EURUSD") is False

    def test_a_missing_package_is_not_fatal(self, broker, monkeypatch):
        monkeypatch.setitem(sys.modules, "MetaTrader5", None)
        assert bs.probe_symbol("EURUSD") is False


# ---------------------------------------------------------------------------
# resolve_broker_symbol
# ---------------------------------------------------------------------------

class TestResolve:
    def test_the_canonical_name_wins_when_the_broker_has_it(self, broker):
        """Even when every alias is also available — canonical is tried first.

        Two valid candidates is what makes this a real ordering test; with only
        one, the fuzzy scan would return it anyway and the order is invisible.
        """
        broker(known=["XAUUSD", "GOLD.i#"], symbols=["XAUUSD", "GOLD.i#"])
        assert bs.resolve_broker_symbol("XAUUSD") == "XAUUSD"

    def test_the_first_working_alias_wins(self, broker):
        broker(known=["XAUUSD#"], symbols=["XAUUSD#", "XAUUSD"])
        assert bs.resolve_broker_symbol("XAUUSD") == "XAUUSD#"

    def test_aliases_are_tried_in_order(self, broker):
        """BROKER_ALIASES['XAUUSD'][0] is GOLD.i#, so GOLD.i# must beat XAUUSD#.

        Again both aliases are valid — otherwise reversing the list changes
        nothing and the configured order is untested.
        """
        broker(known=["GOLD.i#", "XAUUSD#"], symbols=["GOLD.i#", "XAUUSD#"])
        assert bs.resolve_broker_symbol("XAUUSD") == "GOLD.i#"

    def test_the_input_is_upper_cased(self, broker):
        broker(known=["EURUSD"])
        assert bs.resolve_broker_symbol("eurusd") == "EURUSD"

    @pytest.mark.parametrize("value", ["", None])
    def test_an_empty_symbol_resolves_to_none(self, broker, value):
        """And does so without asking the broker anything."""
        mt5 = broker(known=["XAUUSD"], symbols=["XAUUSD"])
        assert bs.resolve_broker_symbol(value) is None
        assert mt5.calls["symbol_info"] == []

    def test_results_are_cached(self, broker):
        mt5 = broker(known=["EURUSD"])
        assert bs.resolve_broker_symbol("EURUSD") == "EURUSD"
        before = len(mt5.calls["symbol_info"])
        assert bs.resolve_broker_symbol("EURUSD") == "EURUSD"
        assert len(mt5.calls["symbol_info"]) == before   # no second probe

    def test_an_unresolvable_symbol_returns_none(self, broker):
        broker(known=[], symbols=[])
        assert bs.resolve_broker_symbol("NOSUCHSYM") is None

    def test_a_failed_resolution_is_cached_too(self, broker):
        """Avoid rescanning the broker's whole symbol list on every poll."""
        mt5 = broker(known=[], symbols=[])
        assert bs.resolve_broker_symbol("NOSUCHSYM") is None
        scans = mt5.calls["symbols_get"]
        assert bs.resolve_broker_symbol("NOSUCHSYM") is None
        assert mt5.calls["symbols_get"] == scans

    def test_a_failed_resolution_is_not_cached_when_the_terminal_is_down(self, broker, monkeypatch):
        """The incident this module exists for.

        A failed probe is indistinguishable from "the broker has no such symbol",
        so caching one taken while the terminal was down pins every symbol to
        None for the life of the process — long after the terminal came up.
        """
        broker(initialize=False)
        assert bs.resolve_broker_symbol("XAUUSD") is None
        assert "XAUUSD" not in bs._FAILED
        broker(known=["GOLD.i#"], symbols=["GOLD.i#"])      # terminal comes up
        # Clear the init throttle, not the failure cache: the point is that
        # nothing was recorded against the symbol, so once the terminal is up
        # the very next call resolves it.
        monkeypatch.setattr(bs, "_LAST_INIT_ATTEMPT", 0.0)
        assert bs.resolve_broker_symbol("XAUUSD") == "GOLD.i#"

    def test_a_failed_initialization_throttles_resolution_for_the_window(self, broker):
        """Consequence of the retry window: nothing resolves for 30s after a
        failed attach, even once the terminal recovers. Cheap `initialize()`
        calls matter more than a fast recovery."""
        broker(initialize=False)
        assert bs.resolve_broker_symbol("XAUUSD") is None
        broker(known=["XAUUSD"])                            # terminal recovers
        assert bs.resolve_broker_symbol("XAUUSD") is None
        assert bs._INIT_RETRY_SEC > 0

    def test_the_terminal_is_asked_for_before_any_probe(self, broker):
        mt5 = broker(initialize=False)
        bs.resolve_broker_symbol("XAUUSD")
        assert mt5.calls["initialize"] == 1
        assert mt5.calls["symbol_info"] == []

    # -- the fuzzy scan -----------------------------------------------------

    def test_a_discovered_prefix_match_is_used(self, broker):
        broker(known=["GOLDPRO"], symbols=["GOLDPRO"])
        assert bs.resolve_broker_symbol("GOLD") == "GOLDPRO"

    def test_a_substring_match_is_never_used(self, broker):
        """COPPER must not resolve to SouthernCopper (an equity).

        This is the documented incident: a bare substring scan handed back
        Southern Copper Corp as the copper commodity, and nothing downstream
        could tell — the frame was stamped LIVE_MT5 and certified FRESH.
        """
        broker(known=["SouthernCopper"], symbols=["SouthernCopper"])
        assert bs.resolve_broker_symbol("COPPER") is None

    def test_a_suffix_match_is_never_used(self, broker):
        broker(known=["XCOPPER"], symbols=["XCOPPER"])
        assert bs.resolve_broker_symbol("COPPER") is None

    def test_a_discovery_only_counts_with_bars(self, broker):
        broker(known=[], with_bars=[], symbols=["GOLDPRO"])
        assert bs.resolve_broker_symbol("GOLD") is None

    def test_short_symbols_are_not_fuzzy_matched(self, broker):
        """len(sym) >= 3 — two letters would prefix-match half the catalogue."""
        broker(known=["GBPUSD"], symbols=["GBPUSD"])
        assert bs.resolve_broker_symbol("GB") is None

    def test_a_discovery_is_logged_loudly(self, broker, caplog):
        broker(known=["GOLDPRO"], symbols=["GOLDPRO"])
        with caplog.at_level("WARNING", logger="JARVIS_BrokerSymbols"):
            bs.resolve_broker_symbol("GOLD")
        assert any("Fuzzy broker-symbol match" in r.message for r in caplog.records)

    def test_the_canonical_name_is_tried_before_the_fuzzy_scan(self, broker):
        broker(known=["GOLD"], symbols=["GOLD", "GOLDPRO"])
        assert bs.resolve_broker_symbol("GOLD") == "GOLD"

    # -- verbose ------------------------------------------------------------

    def test_verbose_reports_a_renamed_symbol(self, broker, capsys):
        broker(known=["GOLD.i#"], symbols=["GOLD.i#"])
        bs.resolve_broker_symbol("XAUUSD", verbose=True)
        assert "XAUUSD -> GOLD.i#" in capsys.readouterr().out

    def test_verbose_reports_a_discovery(self, broker, capsys):
        broker(known=["GOLDPRO"], symbols=["GOLDPRO"])
        bs.resolve_broker_symbol("GOLD", verbose=True)
        out = capsys.readouterr().out
        assert "discovered" in out

    def test_verbose_reports_a_failure(self, broker, capsys):
        broker(known=[], symbols=[])
        bs.resolve_broker_symbol("NOSUCHSYM", verbose=True)
        assert "NOT AVAILABLE" in capsys.readouterr().out

    def test_verbose_is_silent_by_default(self, broker, capsys):
        broker(known=["GOLD.i#"], symbols=["GOLD.i#"])
        bs.resolve_broker_symbol("XAUUSD")
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Cache lifecycle
# ---------------------------------------------------------------------------

class TestResetCache:
    def test_it_forgets_resolutions(self, broker):
        broker(known=["EURUSD"])
        bs.resolve_broker_symbol("EURUSD")
        bs.reset_cache()
        assert bs._CACHE == {}
        assert bs._FAILED == {}

    def test_it_forgets_the_terminal_so_it_is_reinitialized(self, broker):
        mt5 = broker(known=[])
        bs.ensure_mt5_terminal()
        bs.reset_cache()
        assert bs.terminal_ready() is False
        bs.ensure_mt5_terminal()
        assert mt5.calls["initialize"] == 2

    def test_it_clears_the_retry_window(self, broker):
        broker(initialize=False)
        bs.ensure_mt5_terminal()
        bs.reset_cache()
        assert bs._LAST_INIT_ATTEMPT == 0.0


# ---------------------------------------------------------------------------
# The alias table
# ---------------------------------------------------------------------------

class TestAliasTable:
    def test_every_entry_is_a_non_empty_list_of_strings(self):
        for canonical, aliases in bs.BROKER_ALIASES.items():
            assert isinstance(canonical, str) and canonical
            assert isinstance(aliases, list) and aliases, canonical
            assert all(isinstance(a, str) and a for a in aliases), canonical

    def test_canonical_names_are_upper_case(self):
        for canonical in bs.BROKER_ALIASES:
            assert canonical == canonical.upper(), canonical

    def test_no_alias_is_another_canonical_name(self):
        """An alias pointing at another canonical would shadow that entry's cache."""
        canonicals = set(bs.BROKER_ALIASES)
        for canonical, aliases in bs.BROKER_ALIASES.items():
            for a in aliases:
                assert a not in canonicals or a == canonical, (canonical, a)

    def test_the_documented_xm_mappings_are_present(self):
        assert bs.BROKER_ALIASES["XAUUSD"][0] == "GOLD.i#"
        assert bs.BROKER_ALIASES["NAS100"][0] == "US100Cash#"
        assert bs.BROKER_ALIASES["GER40"][0] == "GER40Cash#"


# ---------------------------------------------------------------------------
# NOTE: two other resolvers exist and are NOT fixed here
# ---------------------------------------------------------------------------

class TestOtherResolversExist:
    """Pins the divergence so it cannot be forgotten; see the module docstring.

    `broker_symbols` is the hardened resolver (prefix-only fuzzy, returns None on
    failure, never caches a result taken while the terminal was down). The other
    two do not share those properties, and the historical-acquisition path —
    which builds the parquet that backtests replay — uses `MT5Client`'s, which
    returns the canonical name unchanged in PAPER MODE. That is the
    execution-mode-gates-market-data trap: in paper mode every
    `copy_rates_range` is made with a name the broker does not have.
    """

    def test_mt5_history_has_its_own_resolver(self):
        from jarvis.data.mt5_history import resolve_broker_symbol as other
        assert other is not bs.resolve_broker_symbol

    def test_mt5_client_has_its_own_resolver(self):
        from jarvis.execution.mt5_client import MT5Client
        assert hasattr(MT5Client, "resolve_symbol_name")

    def test_only_the_hardened_one_returns_none_on_failure(self):
        """Hard to test directly, but the contracts differ: this one returns None,
        the other two return the input symbol, so callers cannot tell
        'unresolved' from 'resolves to itself'."""
        import inspect
        assert "None" in inspect.getdoc(bs.resolve_broker_symbol)
