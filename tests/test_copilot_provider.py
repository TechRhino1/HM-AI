"""The optional copilot provider: inert by default, and safe when it is not.

This module has never had a key configured in this repository, so every test
here drives a *fake* transport — nothing in this file touches the network and
none of it is marked ``network`` (pytest deselects those by default).

The properties that matter are the four in the module docstring, and each has a
test below: inert without a key, additive only, the key never leaks, and failure
falls back silently.
"""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_copilot_intents as T  # noqa: E402  (shared stubs, same directory)

from jarvis.api.copilot import JarvisCopilot  # noqa: E402
from jarvis.api.copilot_provider import (  # noqa: E402
    CopilotProvider,
    redact,
    DEFAULT_BASE_URL,
)

KEY = "sk-live-abcdefghijklmnop1234567890"


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Transport:
    """Records what would have gone out, so a test can assert on it."""

    def __init__(self, status=200, body=None, exc=None):
        self.status, self.body, self.exc = status, body, exc
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "payload": json,
                           "headers": dict(headers or {}), "timeout": timeout})
        if self.exc is not None:
            raise self.exc

        def as_json():
            return self.body if self.body is not None else {}

        return _NS(status_code=self.status, json=as_json)


def _choices(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _provider(transport, key=KEY):
    return CopilotProvider(api_key=key, session=transport)


def _copilot(provider):
    state = _NS(positions=[T._position()],
                latest_decisions={"XAUUSD": T._decision("XAUUSD")},
                market_contexts={},
                account=T._account(),
                radar_opportunities=[],
                execution_mode=_NS(value="AUTO"),
                is_safe_mode=False)
    return JarvisCopilot(state_manager=state, mt5_client=T._StubBroker([]),
                         provider=provider)


class InertWithoutAKeyTest(unittest.TestCase):
    def test_an_empty_key_makes_it_unavailable(self):
        self.assertFalse(CopilotProvider(api_key="").available)
        self.assertFalse(CopilotProvider(api_key="   ").available)
        self.assertFalse(CopilotProvider().available)

    def test_complete_without_a_key_never_touches_the_transport(self):
        transport = _Transport()
        p = CopilotProvider(api_key="", session=transport)
        self.assertIsNone(p.complete("system"))
        self.assertEqual(transport.calls, [])

    def test_the_default_copilot_has_no_provider_enabled(self):
        # Constructing the copilot must not make an answering model appear.
        c = _copilot(CopilotProvider())
        self.assertFalse(c.provider.available)
        # An unmatched question falls through to the help text, unchanged.
        self.assertIn(T.MARK["help"], c.ask("what is the capital of France"))


class KeyNeverLeaksTest(unittest.TestCase):
    def test_the_key_is_in_the_header_and_not_in_the_body(self):
        transport = _Transport(body=_choices("answer"))
        _provider(transport).complete("system", [{"role": "user", "content": "hi"}])
        call = transport.calls[0]
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {KEY}")
        body = repr(call["payload"])
        self.assertNotIn(KEY, body)
        self.assertNotIn("api_key", body)

    def test_the_key_is_never_logged_on_a_failure(self):
        transport = _Transport(exc=RuntimeError("boom"))
        p = _provider(transport)
        with self.assertLogs("JARVIS_CopilotProvider", level="WARNING") as captured:
            p.complete("system")
        for record in captured.records:
            self.assertNotIn(KEY, record.getMessage())
            self.assertNotIn(KEY, (record.args or {}).__str__())

    def test_the_key_is_never_logged_on_a_non_200(self):
        transport = _Transport(status=500, body={})
        p = _provider(transport)
        with self.assertLogs("JARVIS_CopilotProvider", level="WARNING") as captured:
            p.complete("system")
        for record in captured.records:
            self.assertNotIn(KEY, record.getMessage())

    def test_describe_renders_the_key_redacted(self):
        self.assertNotIn(KEY, _provider(_Transport()).describe())

    def test_redact_leaves_nothing_to_reconstruct(self):
        self.assertEqual(redact(""), "<unset>")
        self.assertEqual(redact(None), "<unset>")
        out = redact(KEY)
        self.assertNotIn(KEY, out)
        self.assertIn("chars", out)
        self.assertTrue(out.startswith("sk-"))

    def test_the_url_is_derived_from_the_base(self):
        transport = _Transport(body=_choices("x"))
        _provider(transport).complete("system")
        self.assertEqual(transport.calls[0]["url"],
                         DEFAULT_BASE_URL + "/chat/completions")


class FailureFallsBackTest(unittest.TestCase):
    def _assert_none_and_one_call(self, transport, expect_warning=True):
        p = _provider(transport)
        if expect_warning:
            with self.assertLogs("JARVIS_CopilotProvider", level="WARNING"):
                out = p.complete("system")
        else:
            out = p.complete("system")
        self.assertIsNone(out)
        self.assertEqual(len(transport.calls), 1)

    def test_a_transport_exception_returns_none(self):
        self._assert_none_and_one_call(_Transport(exc=RuntimeError("socket exploded")))

    def test_a_non_200_returns_none(self):
        self._assert_none_and_one_call(_Transport(status=401, body={}))

    def test_no_choices_returns_none(self):
        self._assert_none_and_one_call(_Transport(status=200, body={"choices": []}))

    def test_an_empty_message_returns_none(self):
        # Not every fallback logs: an empty completion is a provider answering
        # with nothing, which is worth returning from silently rather than
        # emitting a warning for every well-formed but empty reply.
        self._assert_none_and_one_call(
            _Transport(status=200, body={"choices": [{"message": {"content": "  "}}]}),
            expect_warning=False)

    def test_a_malformed_body_returns_none(self):
        self._assert_none_and_one_call(_Transport(status=200, body={"unexpected": True}),
                                       expect_warning=False)

    def test_a_failure_does_not_raise_into_the_caller(self):
        # The whole point: the route has a rule-based answer already and must
        # not lose it to a provider outage.
        c = _copilot(_provider(_Transport(exc=RuntimeError("down"))))
        self.assertIn(T.MARK["help"], c.ask("what is the capital of France"))


class AdditiveOnlyTest(unittest.TestCase):
    """The model may fill a gap. It may never replace a grounded answer."""

    def test_a_grounded_question_never_calls_the_provider(self):
        transport = _Transport(body=_choices("model prose"))
        c = _copilot(_provider(transport))
        out = c.ask("what positions do I have open?", session_id="s")
        self.assertEqual(transport.calls, [])
        self.assertIn(T.MARK["positions"], out)

    def test_an_unmatched_question_is_answered_by_the_model(self):
        transport = _Transport(body=_choices("model prose"))
        c = _copilot(_provider(transport))
        out = c.ask("what is the capital of France", session_id="s")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(out, "model prose")

    def test_the_model_cannot_override_a_grounded_answer(self):
        transport = _Transport(body=_choices("model prose"))
        c = _copilot(_provider(transport))
        # A risk question has a grounded answer, so the model is not consulted
        # and the trader's real balance survives.
        out = c.ask("show current risk and exposure")
        self.assertEqual(transport.calls, [])
        self.assertIn("10,000.00", out)

    def test_with_no_provider_an_unmatched_question_is_the_help_text(self):
        c = _copilot(CopilotProvider())
        self.assertIn(T.MARK["help"], c.ask("what is the capital of France"))


class GroundedPromptTest(unittest.TestCase):
    def test_the_prompt_carries_the_live_state(self):
        transport = _Transport(body=_choices("ok"))
        c = _copilot(_provider(transport))
        c.ask("hello there", session_id="s")
        system = transport.calls[0]["payload"]["messages"][0]["content"]
        self.assertIn("5012345", system)          # the real account number
        self.assertIn("10,000.00", system)        # the real balance
        self.assertIn("Safe mode", system)
        self.assertIn("12345", system)            # the open ticket

    def test_the_prompt_forbids_inventing_figures(self):
        transport = _Transport(body=_choices("ok"))
        c = _copilot(_provider(transport))
        c.ask("hello there", session_id="s")
        system = transport.calls[0]["payload"]["messages"][0]["content"]
        self.assertIn("Never estimate", system)
        self.assertIn("advisory only", system)

    def test_the_previous_topic_is_passed_as_context(self):
        transport = _Transport(body=_choices("ok"))
        c = _copilot(_provider(transport))
        c.ask("why aren't you entering XAUUSD?", session_id="s")
        transport.calls.clear()
        c.ask("what is the capital of France", session_id="s")
        system = transport.calls[0]["payload"]["messages"][0]["content"]
        self.assertIn("CONTEXT", system)
        self.assertIn("decision", system)
        self.assertIn("XAUUSD", system)

    def test_only_user_and_assistant_roles_are_forwarded(self):
        transport = _Transport(body=_choices("ok"))
        p = _provider(transport)
        p.complete("system", [{"role": "user", "content": "hi"},
                              {"role": "tool", "content": "internal"},
                              {"role": "assistant", "content": "hello"}])
        roles = [m["role"] for m in transport.calls[0]["payload"]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant"])


if __name__ == "__main__":
    unittest.main()
