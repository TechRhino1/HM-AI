"""Copilot intent routing and honest-mode rules.

`jarvis/api/copilot.py` is 391 lines of dispatch logic with no tests. Both bugs
it has had were found by hand-probing a live session, which means nothing would
have caught a regression — and it is the one module in the codebase whose stated
contract is that it *never invents a number*:

    A missing figure is reported as missing. An invented fill price or a
    "roughly" P&L is worse than "not synced yet", because the trader acts on it.

The tests below are in two groups. The routing tests pin which handler answers
which phrasing, because a wrong-but-plausible answer is this module's failure
mode: "how are my trades doing?" was answered with "you have no open position on
XAUUSD" — true, and not what was asked. The honest-mode tests pin that a missing
figure is reported as missing rather than guessed.

Everything here is deterministic: the journal is patched, and no test opens a
broker connection or reads the real database.
"""

import re
import unittest
from unittest.mock import patch

from jarvis.api.copilot import JarvisCopilot
from jarvis.data.database import TRADE_DB


class _NS:
    """Attribute bag.

    Deliberately explicit rather than a magic object that invents attributes for
    anything asked of it: a stub that answers every attribute access would let a
    renamed field pass these tests, which is the opposite of the point.
    """

    def __init__(self, **kw):
        self.__dict__.update(kw)


# Distinguishes which handler produced an answer. Each marker is a string only
# that handler emits, so a routing change shows up as the wrong marker rather
# than as a similar-looking reply.
MARK = {
    "positions": "Open positions — ",
    "no_positions": "no open position",
    "performance": "**How you stand**",
    "history": "closed trades",
    "pending": "Working orders",
    "pending_unavailable": "no broker client is attached",
    "help": "Intelligence Copilot",
    "decision": "Decision Status for",
    "risk": "Risk & Account Telemetry",
    "radar": "Multi-Symbol Scanner Opportunities",
    "context": "Market Context for",
}


def _position(ticket=12345, symbol="XAUUSD", side="BUY", volume=0.10,
              open_price=2380.0, current=2390.0, sl=2370.0, tp=2400.0, profit=12.5):
    return _NS(ticket=ticket, symbol=symbol, type=side, volume=volume,
               open_price=open_price, current_price=current, sl=sl, tp=tp,
               profit=profit, open_time="2026-09-16T09:00:00+00:00")


def _account():
    return _NS(server="MetaQuotes-Demo", login=5012345, balance=10000.0,
               equity=10050.0, free_margin=9000.0, margin=500.0, currency="USD")


def _decision(symbol="XAUUSD", decision="WAIT"):
    """Every field the copilot *and* ReasoningEngine actually read.

    An earlier version omitted `symbol`, `regime`, `bull_case`, `bear_case` and
    `quality_gate.passed`. That mattered: `ReasoningEngine.generate_explanation`
    reads all five, so the copilot's `analyze <symbol>` branch — the one that
    renders a full decision explanation — could never be reached by a test, and
    would have raised the first time anything did reach it. A stub that is
    missing a field the code reads is a coverage hole, not a simplification.
    """
    from jarvis.data.schemas import MarketRegime

    return _NS(symbol=symbol, decision=decision, bias="BULLISH", strategy="MOMENTUM_BREAKOUT",
               probabilities={"bullish": 0.61},
               regime=_NS(primary_regime=MarketRegime.TREND_BULL, confidence=0.72),
               bull_case=["HTF trend intact"],
               bear_case=["Momentum divergence"],
               quality_gate=_NS(failing_reasons=["SPREAD_TOO_WIDE"], passed=False),
               risk_factors=["Chasing an extended candle"],
               adversarial_penalty=3.5,
               invalidation_levels=["2370.00", "2362.50"],
               expected_value=18.4, risk_reward_ratio=1.8)


def _context(symbol="EURUSD"):
    return _NS(current_price=1.0925,
               volatility=_NS(current_spread_pips=0.8, state="NORMAL", atr=0.0041),
               structure=_NS(bias="BEARISH", discount_premium_zone="PREMIUM"),
               momentum=_NS(trend_score=-0.42, adx=27.3, rsi=41.2),
               session=_NS(current_session="LONDON"))


# A journal with the three cases the closed-trade filter has to tell apart.
JOURNAL = [
    # closed_at present, flat P&L -> closed (the close time is the evidence)
    {"ticket": 501, "symbol": "XAUUSD", "action": "BUY", "volume": 0.2,
     "realized_pnl": 0.0, "executor": "BOT (AI)",
     "timestamp": "2026-09-10T08:00:00+00:00",
     "closed_at": "2026-09-12T15:30:00+00:00"},
    # no closed_at but a non-zero result -> closed
    {"ticket": 502, "symbol": "EURUSD", "action": "SELL", "volume": 0.1,
     "realized_pnl": -20.0, "executor": "MANUAL",
     "timestamp": "2026-09-13T09:00:00+00:00", "closed_at": None},
    # neither -> never closed, must not be listed
    {"ticket": 503, "symbol": "GBPUSD", "action": "BUY", "volume": 0.1,
     "realized_pnl": 0.0, "executor": "MANUAL",
     "timestamp": "2026-09-14T09:00:00+00:00", "closed_at": None},
]


class _StubBroker:
    def __init__(self, orders):
        self._orders = orders
        self.calls = []

    def get_pending_orders(self, symbol=None):
        self.calls.append(symbol)
        return self._orders


def _copilot(positions=(), decisions=None, contexts=None, account=None,
             radar=(), broker=None, safe_mode=False, execution_mode="AUTO"):
    state = _NS(
        positions=list(positions),
        latest_decisions=dict(decisions or {}),
        market_contexts=dict(contexts or {}),
        account=account,
        radar_opportunities=list(radar),
        execution_mode=_NS(value=execution_mode),
        is_safe_mode=safe_mode,
    )
    return JarvisCopilot(state_manager=state, mt5_client=broker)


class CopilotRoutingTest(unittest.TestCase):
    """Which handler answers which phrasing."""

    def setUp(self):
        # _history_answer and _performance_answer read the journal. Patch it so
        # the suite never depends on the machine's live trade database.
        self._journal = patch.object(TRADE_DB, "fetch_recent_trades",
                                     return_value=list(JOURNAL))
        self._journal.start()

    def tearDown(self):
        self._journal.stop()

    def test_empty_query_returns_help(self):
        for q in ("", "   ", None):
            self.assertIn(MARK["help"], _copilot().ask(q))

    def test_every_phrasing_the_help_text_suggests_actually_routes_somewhere(self):
        """The help text is a promise about the router.

        This is not a hypothetical: the help text suggested "how is my symbol
        doing?" while that exact sentence fell through to the help text again.
        Extracting the examples from the reply and asserting each one reaches a
        handler keeps the two in step.
        """
        bot = _copilot(positions=[_position(symbol="XAUUSD")],
                       decisions={"XAUUSD": _decision()},
                       contexts={"EURUSD": _context()})
        help_text = bot.ask("")
        examples = re.findall(r"\*'([^']+)'\*", help_text)
        self.assertGreaterEqual(len(examples), 5, "help text stopped listing examples")
        for phrase in examples:
            answer = bot.ask(phrase, context={"symbol": "XAUUSD"})
            self.assertNotIn(
                MARK["help"], answer,
                f"the help text suggests {phrase!r} but it falls through to the help text")

    def test_a_book_level_performance_question_is_not_answered_by_the_position_handler(self):
        """Regression: "how are my trades doing?" was answered with a single
        instrument's position status, because `_POSITION_WORDS` contains
        "my trade" and matched first."""
        answer = _copilot(positions=[_position(symbol="XAUUSD")],
                          account=_account()).ask("how are my trades doing?")
        self.assertIn(MARK["performance"], answer)
        self.assertNotIn(MARK["no_positions"], answer)

    def test_a_named_instrument_routes_to_that_instrument(self):
        """Regression: this phrasing fell through to the help text, because no
        entry in `_POSITION_WORDS` appears in the sentence."""
        answer = _copilot(positions=[_position(symbol="XAUUSD")]).ask("how is my EURUSD doing?")
        self.assertIn("no open position on EURUSD", answer)

    def test_the_on_screen_focus_does_not_override_a_book_level_question(self):
        """The discriminator is whether an instrument was NAMED. A focus alone
        must not turn "how are my trades doing?" back into a position answer."""
        answer = _copilot(positions=[_position(symbol="XAUUSD")],
                          account=_account()).ask("how are my trades doing?",
                                                  context={"symbol": "XAUUSD"})
        self.assertIn(MARK["performance"], answer)
        self.assertNotIn(MARK["no_positions"], answer)

    def test_a_named_instrument_beats_the_on_screen_focus(self):
        answer = _copilot(positions=[_position(symbol="XAUUSD")]).ask(
            "how is my EURUSD doing?", context={"symbol": "XAUUSD"})
        self.assertIn("EURUSD", answer)
        self.assertNotIn("XAUUSD", answer)

    def test_the_focus_answers_a_bare_why(self):
        """A bare "why?" is only answerable because the client says which symbol
        is on screen — that is the whole reason the focus is sent."""
        answer = _copilot(decisions={"XAUUSD": _decision()}).ask(
            "why?", context={"symbol": "XAUUSD"})
        self.assertIn(MARK["decision"], answer)

    def test_a_bare_why_without_a_focus_does_not_invent_a_symbol(self):
        answer = _copilot(decisions={"XAUUSD": _decision()}).ask("why?")
        self.assertIn(MARK["help"], answer)
        self.assertNotIn(MARK["decision"], answer)

    def test_position_question_with_no_positions_says_so(self):
        answer = _copilot().ask("what positions do I have open?")
        self.assertIn("You have no open positions right now.", answer)

    def test_position_question_lists_the_book(self):
        answer = _copilot(positions=[_position(ticket=777, symbol="XAUUSD")]).ask(
            "what positions do I have open?")
        self.assertIn("#777 XAUUSD BUY", answer)
        self.assertIn("Floating P&L", answer)

    def test_market_context_question(self):
        answer = _copilot(contexts={"EURUSD": _context()}).ask("analyze EURUSD")
        self.assertIn(MARK["context"], answer)
        self.assertIn("LONDON", answer)

    def test_analyze_with_a_recorded_decision_renders_the_full_explanation(self):
        """The `analyze` branch that reaches ReasoningEngine.

        Uncovered until now: the branch only fires when the symbol has a
        recorded decision, and no test supplied one. It is a different renderer
        from the market-context answer below it.
        """
        answer = _copilot(decisions={"XAUUSD": _decision("XAUUSD")}).ask("analyze XAUUSD")
        self.assertIn("DECISION EXPLANATION [XAUUSD]", answer)
        self.assertIn("SPREAD_TOO_WIDE", answer)
        self.assertIn("TREND_BULL", answer)

    def test_risk_question(self):
        answer = _copilot(account=_account(), positions=[_position()]).ask(
            "show current risk and exposure")
        self.assertIn(MARK["risk"], answer)
        self.assertIn("10,000.00", answer)

    def test_radar_question(self):
        answer = _copilot(radar=[{"symbol": "XAUUSD", "action": "BUY", "score": 82,
                                  "ev": 21.5, "regime": "TREND_BULL",
                                  "decision": "WAIT"}]).ask("show today's best setups")
        self.assertIn(MARK["radar"], answer)
        self.assertIn("XAUUSD", answer)


class CopilotHistoryTest(unittest.TestCase):
    """The closed-trade answer, and what counts as closed."""

    def setUp(self):
        self._journal = patch.object(TRADE_DB, "fetch_recent_trades",
                                     return_value=list(JOURNAL))
        self._journal.start()

    def tearDown(self):
        self._journal.stop()

    def test_history_question_is_reached(self):
        self.assertIn(MARK["history"], _copilot().ask("show my last closed trades"))

    def test_a_trade_that_never_closed_is_not_listed(self):
        answer = _copilot().ask("show my last closed trades")
        self.assertNotIn("GBPUSD", answer)
        self.assertIn("Last 2 closed trades", answer)

    def test_the_close_time_is_used_not_the_logged_time(self):
        """Same disambiguation as the history table: `timestamp` is the entry
        for a journal row, so quoting it as the close is wrong."""
        answer = _copilot().ask("show my last closed trades")
        self.assertIn("2026-09-12 15:30:00", answer)
        self.assertNotIn("2026-09-10 08:00:00", answer)

    def test_the_net_is_totalled(self):
        self.assertIn("Net over these trades: -20.00", _copilot().ask("show my last closed trades"))

    def test_no_closed_trades_is_stated_rather_than_left_blank(self):
        with patch.object(TRADE_DB, "fetch_recent_trades", return_value=[]):
            self.assertIn("No closed trades", _copilot().ask("show my last closed trades"))

    def test_an_unreachable_journal_is_reported_as_unreachable(self):
        with patch.object(TRADE_DB, "fetch_recent_trades",
                          side_effect=RuntimeError("db locked")):
            answer = _copilot().ask("show my last closed trades")
        self.assertIn("not reachable", answer)


class CopilotHonestModeTest(unittest.TestCase):
    """A missing figure must be reported as missing, never estimated."""

    def setUp(self):
        self._journal = patch.object(TRADE_DB, "fetch_recent_trades",
                                     return_value=list(JOURNAL))
        self._journal.start()

    def tearDown(self):
        self._journal.stop()

    def test_a_missing_account_is_not_guessed(self):
        answer = _copilot(account=None).ask("how am I doing today?")
        self.assertIn("will not guess the balance", answer)

    def test_the_realised_count_does_not_claim_more_trades_than_it_counted(self):
        """It reports the trades it actually summed.

        Two of the three journal rows are closed, but only one carries a
        non-zero realised result. The line used to read "over the last 1 closed
        trade(s)", which is a claim about the journal that the journal
        contradicts — the trader sees "1 closed trade" here and two rows in the
        history table.
        """
        answer = _copilot(account=_account()).ask("how am I doing today?")
        self.assertIn("with a recorded result", answer)
        self.assertNotIn("closed trade(s)", answer)

    def test_the_win_rate_is_reported_with_its_denominator(self):
        answer = _copilot(account=_account()).ask("how am I doing today?")
        self.assertIn("0W / 1L", answer)
        self.assertIn("win rate 0%", answer)

    def test_working_orders_say_so_when_no_broker_client_is_attached(self):
        """Silence here would read as "you have no orders", which is a
        different statement from "I cannot see your orders"."""
        answer = _copilot(broker=None).ask("what working orders do I have?")
        self.assertIn(MARK["pending_unavailable"], answer)
        self.assertNotIn("You have no working orders", answer)

    def test_working_orders_are_listed_with_their_kind(self):
        broker = _StubBroker([{"ticket": 90001, "symbol": "XAUUSD", "type": 2,
                               "volume": 0.2, "price": 2375.0, "sl": 2365.0, "tp": 2400.0}])
        answer = _copilot(broker=broker).ask("what working orders do I have?")
        self.assertIn("BUY LIMIT", answer)
        self.assertIn("#90001", answer)

    def test_an_unreadable_broker_is_reported_as_unreadable(self):
        class _Broken:
            def get_pending_orders(self, symbol=None):
                raise RuntimeError("terminal gone")
        answer = _copilot(broker=_Broken()).ask("what working orders do I have?")
        self.assertIn("could not read working orders", answer)


class CopilotFormattingTest(unittest.TestCase):
    """Price precision is a property of the instrument, not of the value."""

    def test_precision_is_per_symbol(self):
        self.assertEqual(JarvisCopilot._px("XAUUSD", 2380.5), "2380.50")
        self.assertEqual(JarvisCopilot._px("USDJPY", 147.2), "147.200")
        self.assertEqual(JarvisCopilot._px("EURUSD", 1.0925), "1.09250")

    def test_a_value_that_is_not_a_number_is_a_dash_not_a_crash(self):
        for bad in ("abc", None, ""):
            self.assertEqual(JarvisCopilot._px("XAUUSD", bad), "—")


if __name__ == "__main__":
    unittest.main()
