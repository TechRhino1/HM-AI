"""Copilot conversation memory.

The copilot is a router, not a language model: it matches a query to an intent
and answers from live state. Before memory existed a *follow-up* had nothing to
resolve against, so "what about EURUSD?" (an instrument, no intent) and "why?"
(neither) both fell through to the help text.

Two properties matter more than the feature itself, and each has its own test
class below:

* **Statelessness is preserved.** A call with no session id must behave exactly
  as it did before memory existed. The one-line curl and the 27 tests in
  ``test_copilot_intents.py`` both depend on that.
* **Memory carries a referent, never a fact.** It remembers *which intent was
  asked about which instrument* — never an answer, a price or a P&L. The test
  that proves this changes the live state between the two turns and asserts the
  follow-up reflects the NEW value: a cached answer would fail it.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_copilot_intents as T  # noqa: E402  (shared stubs, same directory)

from jarvis.api.copilot import (  # noqa: E402
    JarvisCopilot,
    ConversationMemory,
    INTENT_HELP,
)


def _copilot(positions=(), decisions=None, account=None, broker=None):
    state = T._NS(
        positions=list(positions),
        latest_decisions=dict(decisions or {}),
        market_contexts={},
        account=account,
        radar_opportunities=[],
        execution_mode=T._NS(value="AUTO"),
        is_safe_mode=False,
    )
    return JarvisCopilot(state_manager=state, mt5_client=broker or T._StubBroker([]))


class StatelessnessIsPreservedTest(unittest.TestCase):
    """No session id => exactly the pre-memory behaviour."""

    def setUp(self):
        self.c = _copilot(positions=[T._position()],
                          decisions={"XAUUSD": T._decision("XAUUSD"),
                                     "EURUSD": T._decision("EURUSD")},
                          account=T._account())

    def test_a_follow_up_without_a_session_is_the_help_text(self):
        self.assertIn(T.MARK["help"], self.c.ask("what about EURUSD?"))

    def test_a_follow_up_without_a_session_does_not_invent_a_topic(self):
        self.c.ask("why aren't you entering XAUUSD?")
        # The previous call carried no session, so nothing was recorded for it.
        self.assertIn(T.MARK["help"], self.c.ask("why?"))

    def test_the_default_construction_has_memory_but_no_session_uses_it(self):
        self.assertIsInstance(self.c.memory, ConversationMemory)
        self.assertEqual(self.c.memory.session_count(), 0)


class FollowUpResolutionTest(unittest.TestCase):
    def setUp(self):
        self.c = _copilot(
            positions=[T._position(), T._position(ticket=777, symbol="EURUSD", side="SELL")],
            decisions={"XAUUSD": T._decision("XAUUSD"),
                       "EURUSD": T._decision("EURUSD"),
                       "GBPUSD": T._decision("GBPUSD")},
            account=T._account(),
        )
        self.s = "s1"

    def ask(self, q, ctx=None):
        return self.c.ask(q, context=ctx, session_id=self.s)

    def test_a_named_instrument_in_a_follow_up_re_runs_the_previous_intent(self):
        self.assertIn("Decision Status for XAUUSD", self.ask("why aren't you entering XAUUSD?"))
        out = self.ask("what about EURUSD?")
        self.assertIn("Decision Status for EURUSD", out)
        self.assertNotIn("Decision Status for XAUUSD", out)

    def test_the_topic_keeps_moving_with_each_follow_up(self):
        self.ask("why aren't you entering XAUUSD?")
        self.assertIn("Decision Status for EURUSD", self.ask("what about EURUSD?"))
        self.assertIn("Decision Status for GBPUSD", self.ask("and GBPUSD?"))

    def test_a_book_level_follow_up_re_runs_the_book_intent(self):
        self.assertIn(T.MARK["positions"], self.ask("what positions do I have open?"))
        out = self.ask("what about EURUSD?")
        # The positions intent, re-run for the instrument just named.
        self.assertIn("Open positions", out)
        self.assertIn("EURUSD", out)

    def test_a_bare_why_reuses_the_remembered_instrument(self):
        self.ask("why aren't you entering XAUUSD?")
        out = self.ask("why?")
        self.assertIn("Decision Status for XAUUSD", out)

    def test_a_follow_up_with_no_referent_at_all_still_gets_help(self):
        self.assertIn(T.MARK["help"], self.ask("what about it?"))

    def test_two_sessions_do_not_share_a_thread(self):
        self.ask("why aren't you entering XAUUSD?")
        # A different session has no topic, so the same follow-up is help.
        self.assertIn(T.MARK["help"], self.c.ask("why?", session_id="other"))

    def test_a_genuinely_new_question_is_not_hijacked(self):
        self.ask("why aren't you entering XAUUSD?")
        # "analyze EURUSD" carries its own intent, so it routes to the market
        # answer — not to the decision-status answer the previous turn used.
        out = self.ask("analyze EURUSD")
        self.assertIn("DECISION EXPLANATION [EURUSD]", out)
        self.assertNotIn("Decision Status for", out)

    def test_an_unknown_question_is_not_hijacked_into_the_old_topic(self):
        self.ask("why aren't you entering XAUUSD?")
        # No follow-up phrase and no instrument: must not become a decision
        # answer just because the last turn was one.
        out = self.ask("what is the capital of France")
        self.assertIn(T.MARK["help"], out)


class MemoryCarriesAReferentNotAFactTest(unittest.TestCase):
    """The honest-mode guard. A remembered answer would be an invented figure."""

    def test_the_follow_up_reflects_state_that_changed_since_the_first_turn(self):
        c = _copilot(positions=[T._position()],
                     decisions={"XAUUSD": T._decision("XAUUSD", "WAIT")},
                     account=T._account())
        first = c.ask("why aren't you entering XAUUSD?", session_id="s")
        self.assertIn("WAIT", first)

        # The engine changes its mind between the two turns.
        c.state_manager.latest_decisions["XAUUSD"] = T._decision("XAUUSD", "EXECUTE")

        second = c.ask("why?", session_id="s")
        self.assertIn("APPROVED", second,
                      "the follow-up replayed a remembered answer instead of "
                      "re-reading live state")

    def test_a_follow_up_answer_matches_a_fresh_answer_for_the_same_pair(self):
        c = _copilot(positions=[T._position()],
                     decisions={"XAUUSD": T._decision("XAUUSD"),
                                "EURUSD": T._decision("EURUSD")},
                     account=T._account())
        c.ask("why aren't you entering XAUUSD?", session_id="s")
        follow_up = c.ask("what about EURUSD?", session_id="s")
        fresh = c.ask("why aren't you entering EURUSD?", session_id="fresh")
        self.assertEqual(follow_up, fresh)


class MemoryStoreTest(unittest.TestCase):
    def test_help_is_not_remembered_as_a_topic(self):
        m = ConversationMemory()
        m.remember("s", INTENT_HELP, "XAUUSD")
        self.assertIsNone(m.recall("s"))

    def test_a_session_with_no_id_records_nothing(self):
        m = ConversationMemory()
        m.remember(None, "positions", "XAUUSD")
        self.assertEqual(m.session_count(), 0)
        self.assertIsNone(m.recall(None))

    def test_sessions_are_capped_and_the_oldest_is_evicted(self):
        m = ConversationMemory(max_sessions=3)
        for i in range(10):
            m.remember(f"s{i}", "positions", "XAUUSD")
        self.assertEqual(m.session_count(), 3)
        self.assertIsNone(m.recall("s0"))
        self.assertIsNotNone(m.recall("s9"))

    def test_turns_per_session_are_capped(self):
        m = ConversationMemory(max_turns=2)
        m.remember("s", "positions", "XAUUSD")
        m.remember("s", "history", "EURUSD")
        m.remember("s", "radar", None)
        with m._lock:
            self.assertEqual(len(m._turns["s"]), 2)

    def test_entries_expire(self):
        m = ConversationMemory(ttl_seconds=0.0)
        m.remember("s", "positions", "XAUUSD")
        time.sleep(0.01)
        self.assertIsNone(m.recall("s"))

    def test_an_expired_session_is_dropped_from_the_store(self):
        m = ConversationMemory(ttl_seconds=0.0)
        m.remember("s", "positions", "XAUUSD")
        time.sleep(0.01)
        m.recall("s")
        self.assertEqual(m.session_count(), 0)

    def test_the_most_recent_turn_that_named_an_instrument_is_the_referent(self):
        m = ConversationMemory()
        m.remember("s", "decision", "XAUUSD")
        m.remember("s", "radar", None)       # names no instrument
        self.assertEqual(m.recall("s")["symbol"], "XAUUSD")
        self.assertEqual(m.recall("s")["intent"], "radar")

    def test_clear_removes_one_session_or_all(self):
        m = ConversationMemory()
        m.remember("a", "positions", "XAUUSD")
        m.remember("b", "positions", "EURUSD")
        m.clear("a")
        self.assertIsNone(m.recall("a"))
        self.assertIsNotNone(m.recall("b"))
        m.clear()
        self.assertEqual(m.session_count(), 0)

    def test_a_recall_does_not_resurrect_an_evicted_session(self):
        m = ConversationMemory(max_sessions=2)
        m.remember("a", "positions", "XAUUSD")
        m.remember("b", "positions", "XAUUSD")
        m.remember("c", "positions", "XAUUSD")
        self.assertIsNone(m.recall("a"))
        self.assertEqual(m.session_count(), 2)


if __name__ == "__main__":
    unittest.main()
