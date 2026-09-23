"""
HM Algo 2.0 — Optional LLM provider for the copilot.

The rule-based copilot in ``copilot.py`` is the floor, not a placeholder: it
answers every question about the trader's own book from live state, offline and
free. What it cannot do is open-ended phrasing it has no intent for — those fall
through to the help text.

This module adds an *optional* second layer for exactly that gap. Four
properties are deliberate and load-bearing:

* **Inert unless configured.** With no key, ``available`` is False, the copilot
  never calls out, and behaviour is byte-identical to the rule-based copilot.
  Constructing the provider performs no I/O and reads no file.
* **Additive only.** The caller invokes it *only* when the rule-based router
  produced the help text. A grounded rule-based answer can never be replaced by
  a model's prose — which is what keeps a number the trader acts on coming from
  the state manager rather than from a completion.
* **The key never leaks.** It goes in the ``Authorization`` header and nowhere
  else: not in the body, not in a log line, not in an error string. Every place
  this module renders the key it goes through :func:`redact`.
* **Failure is silent and total.** Any transport, auth or shape error returns
  ``None``, and the caller keeps the rule-based answer. A copilot that answers
  "no key configured" mid-conversation is worse than one that answers the help
  text.

Provider-agnostic on purpose: any OpenAI-compatible ``/chat/completions``
endpoint works, so choosing a vendor is a configuration decision, not a code
change.
"""
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("JARVIS_CopilotProvider")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 20.0

ENV_KEY = "JARVIS_COPILOT_API_KEY"
ENV_BASE_URL = "JARVIS_COPILOT_BASE_URL"
ENV_MODEL = "JARVIS_COPILOT_MODEL"
ENV_TIMEOUT = "JARVIS_COPILOT_TIMEOUT"

# A hard ceiling on the answer, and on what we will accept back. The copilot
# answers in a chat bubble, not a report.
MAX_TOKENS = 700
MAX_ANSWER_CHARS = 4000
MAX_MESSAGES = 12


def redact(value: Any) -> str:
    """Render a secret so it cannot be reconstructed from a log.

    Three leading characters and two trailing ones is enough to tell two keys
    apart while debugging, and not enough to use. Nothing in this module logs a
    raw key, and this function exists so that stays true when someone adds a
    log line later.
    """
    if not value:
        return "<unset>"
    s = str(value)
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:3]}…{s[-2:]} ({len(s)} chars)"


class CopilotProvider:
    """An OpenAI-compatible chat client that is inert until given a key."""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 timeout: Optional[float] = None,
                 session: Any = None):
        raw_key = api_key if api_key is not None else os.environ.get(ENV_KEY, "")
        self.api_key = (raw_key or "").strip()
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.model = (model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL).strip()
        if timeout is None:
            try:
                timeout = float(os.environ.get(ENV_TIMEOUT) or DEFAULT_TIMEOUT)
            except (TypeError, ValueError):
                timeout = DEFAULT_TIMEOUT
        self.timeout = timeout
        # Injectable so a test can drive the transport without a socket.
        self._session = session

    @classmethod
    def from_env(cls) -> "CopilotProvider":
        return cls()

    @property
    def available(self) -> bool:
        """True only when a key is configured. Everything else is optional."""
        return bool(self.api_key)

    def describe(self) -> str:
        """A log-safe description. Contains no secret."""
        return (f"provider(base_url={self.base_url}, model={self.model}, "
                f"key={redact(self.api_key)}, timeout={self.timeout:g}s)")

    def _post(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Any:
        """One POST. Split out so the test can substitute a transport."""
        if self._session is not None:
            return self._session.post(self.base_url + "/chat/completions",
                                      json=payload, headers=headers, timeout=self.timeout)
        import requests  # local import: the request path must not require it
        return requests.post(self.base_url + "/chat/completions",
                             json=payload, headers=headers, timeout=self.timeout)

    def complete(self, system_prompt: str,
                 messages: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
        """Return the model's answer, or None if anything at all went wrong.

        Never raises: every caller is a request handler whose only sensible
        fallback is the rule-based answer it already has.
        """
        if not self.available:
            return None

        convo: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for m in (messages or [])[-MAX_MESSAGES:]:
            role = str(m.get("role") or "").strip()
            content = str(m.get("content") or "").strip()
            # Only the two roles a chat endpoint accepts; anything else is
            # dropped rather than forwarded.
            if role in ("user", "assistant") and content:
                convo.append({"role": role, "content": content[:MAX_ANSWER_CHARS]})

        payload = {
            "model": self.model,
            "messages": convo,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.2,
        }
        # The key is in the header and ONLY the header. Note there is no
        # "api_key" field in the payload above, and there must never be one:
        # request bodies end up in provider logs and in error messages.
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            res = self._post(payload, headers)
            status = getattr(res, "status_code", 0)
            if status != 200:
                # Deliberately does not echo the body: a provider error body can
                # quote the request back, and the request carries the key.
                logger.warning("copilot provider returned HTTP %s (%s)", status, self.describe())
                return None
            data = res.json()
            choices = data.get("choices") or []
            if not choices:
                logger.warning("copilot provider returned no choices (%s)", self.describe())
                return None
            message = choices[0].get("message") or {}
            text = (message.get("content") or "").strip()
            if not text:
                return None
            return text[:MAX_ANSWER_CHARS]
        except Exception as exc:  # noqa: BLE001 - a provider outage must not break the chat
            # exc is rendered, not the payload or headers, so the key cannot
            # appear here even if the transport quotes the request.
            logger.warning("copilot provider call failed (%s): %s", self.describe(),
                           type(exc).__name__)
            return None
