"""
HM Algo 2.0 — Conversational AI Copilot Layer.

Answers discretionary trader queries from verified in-memory system state,
market context and decision records. Every number it quotes comes from the
state manager or the trade journal — it never estimates a price, a P&L or a
probability, and it says so when the answer is not available yet.

Two honest-mode rules, learned the hard way elsewhere in this codebase:

* A missing figure is reported as missing. An invented fill price or a
  "roughly" P&L is worse than "not synced yet", because the trader acts on it.
* Symbol detection walks the *live* universe plus the trader's own open
  positions. A hardcoded watchlist silently stopped matching the moment an
  instrument was added to the registry.
"""
import logging
import threading
import time
from collections import OrderedDict
from typing import Dict, Any, List, Optional

from jarvis.application.state_manager import StateManager, GLOBAL_STATE
from jarvis.api.copilot_provider import CopilotProvider
from jarvis.intelligence.reasoning_engine import ReasoningEngine

logger = logging.getLogger("JARVIS_Copilot")

# Phrases that mean the trader is talking about their own book rather than
# about the market. Checked before the market intents so "how is my EURUSD
# doing" reaches the position handler instead of the generic analyse handler.
_POSITION_WORDS = ("position", "open trade", "my trade", "holding", "in profit",
                   "losing", "loser", "winner", "close it", "should i close")
_HISTORY_WORDS = ("history", "closed", "last trade", "recent trade", "yesterday",
                  "realised", "realized", "track record")
_PERF_WORDS = ("how am i", "performance", "pnl", "p&l", "profit", "loss",
               "how much", "up or down")
_PENDING_WORDS = ("pending", "working order", "limit order", "stop order",
                  "unfilled", "my order")

# "how is my EURUSD doing?" names an instrument and asks about THAT position.
# The comment above always claimed this reached the position handler, but no
# word in _POSITION_WORDS appears in the sentence, so it fell through to the
# help text - as did "how is my symbol doing?", which is a question the help
# text itself suggests. A phrase match plus a known instrument is the trigger.
_POSITION_PHRASES = ("how is my", "how's my", "how is it doing", "how is that doing",
                     "how is my position")

# "How are my trades doing?" is a performance question that happens to contain
# "my trade", so the position handler used to answer it with "you have no open
# position on <symbol>" - true, and not what was asked. The discriminator is
# whether the trader NAMED an instrument: "how is my EURUSD doing?" is about
# that position, "how are my trades doing?" is about the book as a whole.
_PERF_PHRASES = ("how are my trades", "how are my positions", "how am i doing",
                 "how am i going", "how are we doing", "how is it going",
                 "how's it going", "how are things", "how have i been",
                 "how did i do", "how am i trading")

# ── intent vocabulary ───────────────────────────────────────────────────────
#
# Every branch of the router declares which intent it answered, so the
# conversation memory can carry it into the next turn. Strings rather than an
# enum because they end up in a log line and in the response body.
INTENT_HELP = "help"
INTENT_POSITIONS = "positions"
INTENT_HISTORY = "history"
INTENT_PERFORMANCE = "performance"
INTENT_PENDING = "pending"
INTENT_DECISION = "decision"
INTENT_MARKET = "market"
INTENT_RISK = "risk"
INTENT_RADAR = "radar"
# Answered by the optional model rather than by a rule. Recorded for the
# instrument it was about, but not re-runnable — `_dispatch` returns None for
# it, so a later follow-up asks again instead of replaying a completion.
INTENT_LLM = "llm"

# A query with none of these is treated as a candidate follow-up. Deliberately
# short and concrete: a broad list would hijack genuinely new questions, which
# is a worse failure than not resolving a follow-up.
_FOLLOWUP_PHRASES = ("what about", "how about", "and the", "and what", "then",
                     "and it", "that one", "the other", "same", "why", "and?")

# ── conversation memory ─────────────────────────────────────────────────────
#
# The copilot is a router, not a language model: it matches a query to an
# intent and answers from live state. That means a *follow-up* had nothing to
# resolve against — "what about EURUSD?" names an instrument and no intent, and
# "why?" names neither, so both fell through to the help text.
#
# Memory therefore carries a REFERENT, never a fact. It records which intent was
# answered about which symbol; a follow-up may re-use those two. It never stores
# an answer, a price or a P&L — every number the trader sees is still read from
# the state manager at answer time. That is the honest-mode rule from the module
# docstring kept intact: a remembered price would be exactly the "invented
# figure" it forbids.
_MEMORY_MAX_SESSIONS = 200
_MEMORY_TTL_SECONDS = 30 * 60.0
_MEMORY_MAX_TURNS = 8


class ConversationMemory:
    """Bounded, TTL'd, thread-safe record of the last turn per session.

    Three bounds, because this is reachable from a surface that local requests
    reach without a token and it must not become a way to grow the process
    without limit: sessions are capped (LRU-evicted), each session keeps only
    the last ``max_turns`` turns, and every entry expires.

    The server is threaded, so every read and write takes the lock.
    """

    def __init__(self, max_sessions: int = _MEMORY_MAX_SESSIONS,
                 ttl_seconds: float = _MEMORY_TTL_SECONDS,
                 max_turns: int = _MEMORY_MAX_TURNS):
        self._lock = threading.Lock()
        self._turns: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns

    def _live(self, session_id: str, now: float) -> List[Dict[str, Any]]:
        turns = self._turns.get(session_id) or []
        return [t for t in turns if now - t["at"] <= self.ttl_seconds]

    def remember(self, session_id: Optional[str], intent: Optional[str],
                 symbol: Optional[str]) -> None:
        """Record a turn. A help answer records nothing — there is no referent."""
        if not session_id or not intent or intent == INTENT_HELP:
            return
        now = time.time()
        with self._lock:
            turns = self._live(session_id, now)
            turns.append({"intent": intent, "symbol": symbol, "at": now})
            self._turns[session_id] = turns[-self.max_turns:]
            self._turns.move_to_end(session_id)
            while len(self._turns) > self.max_sessions:
                self._turns.popitem(last=False)

    def recall(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        now = time.time()
        with self._lock:
            turns = self._live(session_id, now)
            if turns:
                self._turns[session_id] = turns
                self._turns.move_to_end(session_id)
            else:
                self._turns.pop(session_id, None)
            if not turns:
                return None
            last = turns[-1]
            # The most recent turn that actually named a symbol is the better
            # referent: "why?" then "and the spread?" then "why?" should still
            # resolve to the instrument the first question was about.
            sym = last.get("symbol")
            if not sym:
                for t in reversed(turns):
                    if t.get("symbol"):
                        sym = t["symbol"]
                        break
            return {"intent": last["intent"], "symbol": sym}

    def clear(self, session_id: Optional[str] = None) -> None:
        with self._lock:
            if session_id is None:
                self._turns.clear()
            else:
                self._turns.pop(session_id, None)

    def session_count(self) -> int:
        with self._lock:
            return len(self._turns)


class JarvisCopilot:
    def __init__(self, state_manager: StateManager = GLOBAL_STATE, mt5_client: Any = None,
                 memory: Optional["ConversationMemory"] = None,
                 provider: Optional[CopilotProvider] = None):
        self.state_manager = state_manager
        # Working orders live at the broker, not in the state snapshot, so the
        # server hands its client over once it exists. Stays None (and the
        # order questions say so) rather than opening a second connection.
        self.mt5_client = mt5_client
        # Injectable so a test can drive memory without reaching into the
        # instance, and so the route can share one store across requests.
        self.memory = memory if memory is not None else ConversationMemory()
        # Optional and inert without a key. Constructing it reads the
        # environment and nothing else — no socket, no file.
        self.provider = provider if provider is not None else CopilotProvider.from_env()

    # ── formatting ──────────────────────────────────────────────────────────
    @staticmethod
    def _digits(symbol: str) -> int:
        """Price precision is a property of the instrument, not of the value.

        Gold quotes to 2 decimals and a JPY pair to 3 whatever the magnitude;
        printing every level with five decimals made "81.90000 from target"
        unreadable and disagreed with the ticket, which resolves per symbol.
        """
        try:
            from jarvis.data.symbol_registry import resolve
            spec = resolve(symbol)
            if spec is not None:
                return int(spec.digits)
        except Exception:
            logger.debug("could not resolve digits for %s", symbol, exc_info=True)
        return 5

    @classmethod
    def _px(cls, symbol: str, value: Any) -> str:
        try:
            return f"{float(value):.{cls._digits(symbol)}f}"
        except (TypeError, ValueError):
            return "—"

    # ── symbol resolution ───────────────────────────────────────────────────
    def _known_symbols(self) -> List[str]:
        """Every symbol this session can actually talk about, longest first.

        Longest-first matters: "US30" must win over "US" and "XAUUSD" over any
        shorter prefix, otherwise the first substring match wins by accident.
        """
        syms = set()
        try:
            from jarvis.data.symbol_registry import all_symbols
            syms.update(str(s.canonical).upper() for s in all_symbols())
        except Exception:
            logger.debug("symbol registry unavailable for copilot lookup", exc_info=True)
        syms.update(str(p.symbol).upper() for p in (self.state_manager.positions or []))
        syms.update(str(s).upper() for s in (self.state_manager.latest_decisions or {}).keys())
        return sorted(syms, key=len, reverse=True)

    def _find_symbol_in_query(self, query: str) -> Optional[str]:
        q = (query or "").upper()
        for sym in self._known_symbols():
            if sym and sym in q:
                return sym
        # Spoken forms that are not themselves canonical symbols.
        aliases = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "OIL": "USOIL",
                   "BITCOIN": "BTCUSD", "NASDAQ": "NAS100", "DOW": "US30"}
        for word, canonical in aliases.items():
            if word in q and canonical in self._known_symbols():
                return canonical
        return None

    # ── entry point ─────────────────────────────────────────────────────────
    def ask(self, query: str, context: Optional[Dict[str, Any]] = None,
            session_id: Optional[str] = None) -> str:
        """Answer a trader's question, optionally continuing a conversation.

        ``session_id`` is the auth token when there is one, else a stable
        per-client key. Without it the call is stateless and behaves exactly as
        it did before memory existed — which is what the one-line curl and the
        existing test suite rely on.
        """
        q = (query or "").lower().strip()

        focus = None
        if isinstance(context, dict):
            focus = str(context.get("symbol") or "").upper() or None

        carried = self.memory.recall(session_id) if session_id else None
        intent, text, sym = self._route(q, focus=focus, carried=carried)

        # The optional model is strictly additive: it is consulted ONLY when the
        # router had nothing grounded to say. An answer that came from live state
        # is never replaced by prose, so no figure the trader acts on can
        # originate from a completion. With no key configured `available` is
        # False and this whole block is skipped.
        if intent == INTENT_HELP and self.provider.available:
            llm = self._llm_answer(q, sym or focus, carried)
            if llm:
                intent, text = INTENT_LLM, llm

        self.memory.remember(session_id, intent, sym)
        return text

    def _route(self, q: str, focus: Optional[str] = None,
               carried: Optional[Dict[str, Any]] = None):
        """Map a query to ``(intent, answer, symbol)``.

        Returning the intent alongside the text is what makes conversation
        memory possible: the router already knows which branch answered, and
        that label plus the symbol is the only thing worth carrying forward.
        """
        if not q:
            return INTENT_HELP, self._help(), None

        positions = self.state_manager.positions

        # 0. The trader's own book — checked first so "how is my EURUSD doing"
        #    is not swallowed by the generic market-analysis handler.
        explicit = self._find_symbol_in_query(q)
        sym = explicit or focus

        # A performance question outranks the position handler when no symbol
        # was named, because the position word lists match "my trade" and would
        # otherwise answer "how are my trades doing?" with a single instrument.
        if not explicit and any(p in q for p in _PERF_PHRASES):
            return INTENT_PERFORMANCE, self._performance_answer(), None

        # "how is my <instrument> doing" — an instrument is in play (named, or
        # supplied as the on-screen focus), so this is about that position.
        if sym and any(p in q for p in _POSITION_PHRASES):
            return INTENT_POSITIONS, self._positions_answer(sym), sym

        if any(w in q for w in _POSITION_WORDS) or (
            sym and any(p.symbol.upper() == sym for p in positions) and "my" in q
        ):
            return INTENT_POSITIONS, self._positions_answer(sym), sym

        if any(w in q for w in _HISTORY_WORDS):
            return INTENT_HISTORY, self._history_answer(sym), sym

        if any(w in q for w in _PERF_WORDS) and (
            "my" in q or "today" in q or "account" in q or "we" in q or "i " in q
            or any(w in q for w in ("pnl", "p&l", "performance", "how am i"))
        ):
            return INTENT_PERFORMANCE, self._performance_answer(), None

        if any(w in q for w in _PENDING_WORDS):
            return INTENT_PENDING, self._pending_answer(sym), sym

        # 1. "why aren't you entering" / "why no trade".
        #    A bare "why?" counts when the page told us which symbol is on
        #    screen — that is the whole reason the client sends a focus.
        if "why" in q and (
            any(k in q for k in ("enter", "trade", "reject", "buy", "sell", "not"))
            or (bool(focus) and q.strip().rstrip("?!. ") in ("why", "why not", "why no"))
        ):
            sym = sym or "XAUUSD"
            return INTENT_DECISION, self._decision_answer(sym), sym

        # 2. "analyze [symbol]" / "market status"
        elif "analyze" in q or "status" in q or "context" in q:
            sym = sym or "XAUUSD"
            return INTENT_MARKET, self._market_answer(sym), sym

        # 3. "risk" / "exposure" / "drawdown"
        elif "risk" in q or "exposure" in q or "drawdown" in q or "account" in q:
            return INTENT_RISK, self._risk_answer(), None

        # 4. "best setups" / "radar" / "opportunities"
        elif "setup" in q or "radar" in q or "best" in q or "scan" in q:
            return INTENT_RADAR, self._radar_answer(), None

        # 5. Nothing matched. A follow-up can still be resolvable: the previous
        #    turn said which intent was in play, and this query either names a
        #    new instrument for it ("what about EURUSD?") or names nothing at
        #    all ("why?"). The phrase list is deliberately short — a broad one
        #    would hijack genuinely new questions, and answering a new question
        #    with the old topic is worse than not resolving the follow-up.
        if carried and carried.get("intent"):
            follow_sym = explicit or focus or carried.get("symbol")
            if explicit or (follow_sym and any(p in q for p in _FOLLOWUP_PHRASES)):
                text = self._dispatch(carried["intent"], follow_sym)
                if text is not None:
                    return carried["intent"], text, follow_sym

        # 6. Default conversational intelligence response
        return INTENT_HELP, self._help(sym), None

    def _dispatch(self, intent: str, sym: Optional[str]) -> Optional[str]:
        """Re-run a remembered intent against the referent of a follow-up.

        Only the referent travels — the answer is rebuilt from live state, so a
        follow-up can never quote a stale price. That is the honest-mode rule:
        memory supplies *what* is being asked about, never *what the answer is*.
        Returns None for an intent that cannot be re-run; the caller then falls
        back to the help text rather than guessing.
        """
        if intent == INTENT_POSITIONS:
            return self._positions_answer(sym)
        if intent == INTENT_HISTORY:
            return self._history_answer(sym)
        if intent == INTENT_PENDING:
            return self._pending_answer(sym)
        if intent == INTENT_PERFORMANCE:
            return self._performance_answer()
        if intent == INTENT_DECISION:
            return self._decision_answer(sym or "XAUUSD")
        if intent == INTENT_MARKET:
            return self._market_answer(sym or "XAUUSD")
        if intent == INTENT_RISK:
            return self._risk_answer()
        if intent == INTENT_RADAR:
            return self._radar_answer()
        return None

    # ── optional model path ─────────────────────────────────────────────────
    def _system_prompt(self, sym: Optional[str] = None) -> str:
        """Ground the model in exactly the state the rules read.

        The same honest-mode contract as the rule-based answers: quote what is
        below, say "not available" for anything that is not. A model given no
        figures will produce plausible ones, so it is given the real ones and
        told the difference.
        """
        state = self.state_manager
        account = state.account
        positions = list(state.positions or [])
        decisions = state.latest_decisions or {}

        lines = [
            "You are the HM Algo 2.0 trading copilot, embedded in a trader's terminal.",
            "",
            "NON-NEGOTIABLE RULES:",
            "1. Quote ONLY the figures listed below. Never estimate, recall or invent a price,",
            "   a P&L, a probability, a level or a ticket number. If a figure is not below, say",
            "   it is not available yet — the trader acts on these numbers.",
            "2. You are advisory only. You cannot place, modify or cancel an order, and you must",
            "   never imply that you did.",
            "3. Answer briefly and concretely, in the trader's register. No preamble, no filler,",
            "   no restating the question.",
            "4. Output is rendered in a chat bubble: **bold** labels and short bullets. No tables.",
            "",
            "LIVE STATE:",
            f"- Execution mode: {state.execution_mode.value}"
            f" | Safe mode (trading paused): {'ON' if state.is_safe_mode else 'OFF'}",
        ]
        if sym:
            lines.append(f"- The trader is currently looking at: {sym}")
        if account:
            lines.append(
                f"- Account #{account.login} on {account.server}: balance "
                f"${account.balance:,.2f}, equity ${account.equity:,.2f}, "
                f"free margin ${account.free_margin:,.2f}"
            )
        else:
            lines.append("- Account data: NOT AVAILABLE (still syncing with the MT5 gateway)")

        if positions:
            lines.append(f"- Open positions ({len(positions)}):")
            for p in positions[:10]:
                lines.append(
                    f"    #{p.ticket} {p.symbol} {p.type} {float(p.volume):.2f}L "
                    f"@ {p.open_price} -> now {p.current_price}, "
                    f"SL {p.sl}, TP {p.tp}, P&L {float(p.profit or 0.0):+.2f}"
                )
        else:
            lines.append("- Open positions: none")

        if decisions:
            lines.append("- Latest engine decisions:")
            for name, d in list(decisions.items())[:10]:
                lines.append(f"    {name}: {d.decision} (bias {d.bias}, strategy {d.strategy})")
        else:
            lines.append("- Latest engine decisions: none recorded yet")

        lines.append("")
        lines.append("If the question is outside trading and this book, say so plainly.")
        return "\n".join(lines)

    def _llm_answer(self, q: str, sym: Optional[str],
                    carried: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Consult the optional model. Returns None on any failure.

        The previous topic is passed as context rather than as a faked assistant
        turn, so the transcript stays a truthful record of what was asked.
        """
        system = self._system_prompt(sym)
        if carried and carried.get("intent"):
            system += (f"\n\nCONTEXT: the previous question was a "
                       f"{carried['intent']} question about "
                       f"{carried.get('symbol') or 'the book as a whole'}. "
                       f"Resolve any follow-up against that.")
        try:
            return self.provider.complete(system, [{"role": "user", "content": q}])
        except Exception:  # noqa: BLE001 - the rule-based answer is the floor
            logger.warning("copilot provider path failed", exc_info=True)
            return None

    # ── answer builders ─────────────────────────────────────────────────────

    def _decision_answer(self, sym: str) -> str:
        """The decision branch of the router, lifted out so a follow-up can
        re-run it against the instrument the trader has just named."""
        latest_decisions = self.state_manager.latest_decisions
        if sym in latest_decisions:
            d = latest_decisions[sym]
            if d.decision != "EXECUTE":
                reasons = d.quality_gate.failing_reasons
                adv_threats = d.risk_factors
                return (
                    f"**HM Algo 2.0 Decision Status for {sym}: {d.decision}**\n\n"
                    f"- **Current Bias**: {d.bias} ({d.strategy})\n"
                    f"- **Calibrated Win Probability**: {d.probabilities.get(d.bias.lower(), 0.5)*100:.1f}%\n"
                    f"- **Quality Gate Failing Checks**: {', '.join(reasons) if reasons else 'Waiting on Lower Timeframe Trigger'}\n"
                    f"- **Devil's Advocate Adversarial Objections** (Penalty: -{d.adversarial_penalty:.1f} pts):\n"
                    + "\n".join([f"  • {t}" for t in adv_threats[:3]]) + "\n\n"
                    f"- **Invalidation Trigger**: {', '.join(d.invalidation_levels[:2])}"
                )
            else:
                return f"**HM Algo 2.0 has APPROVED execution for {sym}**: Bias={d.bias}, EV=${d.expected_value:.2f}, R:R=1:{d.risk_reward_ratio:.2f}."
        return f"No active decision recorded yet for {sym}. Radar is currently scanning market conditions."


    def _market_answer(self, sym: str) -> str:
        latest_decisions = self.state_manager.latest_decisions
        contexts = self.state_manager.market_contexts
        if sym in latest_decisions:
            return ReasoningEngine.generate_explanation(latest_decisions[sym])
        elif sym in contexts:
            ctx = contexts[sym]
            _live = getattr(ctx, "live_spread_pips", None)
            _spread_txt = (
                f"{_live:.2f} pips (live)" if _live is not None
                else f"{ctx.volatility.current_spread_pips} pips"
            )
            return (
                f"**Market Context for {sym}**\n"
                f"- Price: {self._px(sym, ctx.current_price)} (Spread: {_spread_txt})\n"
                f"- Structure: {ctx.structure.bias} (Zone: {ctx.structure.discount_premium_zone})\n"
                f"- Momentum Score: {ctx.momentum.trend_score} (ADX: {ctx.momentum.adx:.1f}, RSI: {ctx.momentum.rsi:.1f})\n"
                f"- Volatility: {ctx.volatility.state} (ATR: {ctx.volatility.atr:.4f})\n"
                f"- Session: {ctx.session.current_session}"
            )
        return f"Symbol {sym} is currently queued for multi-timeframe synthesis."


    def _risk_answer(self) -> str:
        account = self.state_manager.account
        positions = self.state_manager.positions
        if account:
            return (
                f"**HM Algo 2.0 Risk & Account Telemetry**\n"
                f"- Server: {account.server} (#{account.login})\n"
                f"- Balance: ${account.balance:,.2f} | Equity: ${account.equity:,.2f}\n"
                f"- Free Margin: ${account.free_margin:,.2f} | Open Margin: ${account.margin:,.2f}\n"
                f"- Active Positions: {len(positions)} open trades\n"
                f"- Execution Mode: **{self.state_manager.execution_mode.value}**\n"
                f"- Safe Mode Lock: {'🟡 ACTIVE (Trading Paused)' if self.state_manager.is_safe_mode else '🟢 OFF'}"
            )
        return "Account data is currently synchronizing with MT5 gateway."


    def _radar_answer(self) -> str:
        opps = self.state_manager.radar_opportunities
        if opps:
            lines = ["**Today's Multi-Symbol Scanner Opportunities:**\n"]
            for o in opps[:5]:
                lines.append(
                    f"- **{o.get('symbol')}**: {o.get('action')} | Score: {o.get('score')}/100 | EV: ${o.get('ev', 0.0):.2f} | Regime: {o.get('regime')} | Status: {o.get('decision')}"
                )
            return "\n".join(lines)
        return "Scanner is currently performing multi-asset radar sweep."


    def _help(self, sym: Optional[str] = None) -> str:
        focus = f" (watching {sym})" if sym else ""
        return (
            "**HM Algo 2.0 Intelligence Copilot**\n"
            f"I read live MT5 state, multi-timeframe market context, your open book and the trade journal{focus}.\n\n"
            "Ask me about **your trades**:\n"
            "- *'What positions do I have open?'*\n"
            "- *'How is my EURUSD doing?'*\n"
            "- *'Show my last closed trades'*\n"
            "- *'How am I doing today?'*\n"
            "- *'What working orders do I have?'*\n\n"
            "…or about **the market**:\n"
            "- *'Why aren't you entering XAUUSD?'*\n"
            "- *'Analyze EURUSD'*\n"
            "- *'Show today's best setups'*\n"
            "- *'Show current risk and exposure'*"
        )

    def _positions_answer(self, sym: Optional[str] = None) -> str:
        positions = list(self.state_manager.positions or [])
        if sym:
            positions = [p for p in positions if str(p.symbol).upper() == sym]

        if not positions:
            return (f"You have no open position on {sym}."
                    if sym else "You have no open positions right now.")

        lines = [f"**Open positions — {len(positions)}**\n"]
        total = 0.0
        for p in positions:
            total += float(p.profit or 0.0)
            sym_p = str(p.symbol)
            # Distance to target and stop, in price, so the trader can see how
            # close the trade is to resolving either way.
            dist = ""
            if p.tp and p.tp > 0:
                dist += f" · {self._px(sym_p, abs(float(p.current_price) - float(p.tp)))} from target"
            if p.sl and p.sl > 0:
                dist += f" · {self._px(sym_p, abs(float(p.current_price) - float(p.sl)))} from stop"
            lines.append(
                f"- **#{p.ticket} {sym_p} {p.type}** {float(p.volume):.2f}L "
                f"@ {self._px(sym_p, p.open_price)} → now {self._px(sym_p, p.current_price)}\n"
                f"  P&L **{float(p.profit):+.2f}**{dist}\n"
                f"  SL {self._px(sym_p, p.sl)} · TP {self._px(sym_p, p.tp)} · opened {p.open_time}"
            )
        lines.append(f"\n**Floating P&L across these: {total:+.2f}**")

        if sym and sym in self.state_manager.latest_decisions:
            d = self.state_manager.latest_decisions[sym]
            lines.append(f"\nEngine's current read on {sym}: **{d.decision}**, bias {d.bias}.")
        return "\n".join(lines)

    def _history_answer(self, sym: Optional[str] = None) -> str:
        try:
            from jarvis.data.database import TRADE_DB
            rows = TRADE_DB.fetch_recent_trades(limit=200) or []
        except Exception:
            logger.warning("copilot could not read the trade journal", exc_info=True)
            return "The trade journal is not reachable right now, so I cannot list closed trades."

        closed = [r for r in rows if r.get("closed_at") or float(r.get("realized_pnl") or 0.0) != 0.0]
        if sym:
            closed = [r for r in closed if str(r.get("symbol", "")).upper() == sym]
        closed = closed[:10]

        if not closed:
            return (f"No closed trades recorded for {sym} yet."
                    if sym else "No closed trades in the journal yet.")

        lines = [f"**Last {len(closed)} closed trades**\n"]
        net = 0.0
        for r in closed:
            pnl = float(r.get("realized_pnl") or 0.0)
            net += pnl
            when = str(r.get("closed_at") or r.get("timestamp") or "")[:19].replace("T", " ")
            lines.append(
                f"- **#{r.get('ticket')} {r.get('symbol')} {r.get('action')}** "
                f"{float(r.get('volume') or 0):.2f}L · P&L **{pnl:+.2f}**"
                f" · {r.get('executor') or '—'} · {when or 'time not recorded'}"
            )
        lines.append(f"\n**Net over these trades: {net:+.2f}**")
        return "\n".join(lines)

    def _performance_answer(self) -> str:
        account = self.state_manager.account
        positions = list(self.state_manager.positions or [])
        floating = sum(float(p.profit or 0.0) for p in positions)

        parts = ["**How you stand**\n"]
        if account:
            parts.append(
                f"- Balance **{float(account.balance):,.2f}** · Equity **{float(account.equity):,.2f}**"
                f" · Free margin **{float(account.free_margin):,.2f}** ({account.currency or ''})".rstrip()
            )
        else:
            parts.append("- Account snapshot is not synced yet — I will not guess the balance.")
        parts.append(f"- Floating P&L on {len(positions)} open position(s): **{floating:+.2f}**")

        try:
            from jarvis.data.database import TRADE_DB
            rows = TRADE_DB.fetch_recent_trades(limit=200) or []
            realised = [float(r.get("realized_pnl") or 0.0)
                        for r in rows if float(r.get("realized_pnl") or 0.0) != 0.0]
            if realised:
                wins = [p for p in realised if p > 0]
                # "over the last N closed trade(s)" was a claim about the
                # journal that the journal contradicted: `realised` holds only
                # the trades with a NON-ZERO result, so a break-even trade made
                # this say "1 closed trade" while the history answer listed two.
                # The count is honest about what it counted instead.
                parts.append(
                    f"- Realised on {len(realised)} trade(s) with a recorded result: "
                    f"**{sum(realised):+.2f}** · {len(wins)}W / {len(realised) - len(wins)}L "
                    f"· win rate {len(wins) / len(realised) * 100:.0f}%"
                )
            else:
                parts.append("- No realised P&L recorded in the journal yet.")
        except Exception:
            logger.warning("copilot could not summarise realised P&L", exc_info=True)
            parts.append("- Could not read the trade journal for realised P&L.")

        parts.append(
            f"\nExecution mode **{self.state_manager.execution_mode.value}** · "
            f"safe mode {'ON — trading paused' if self.state_manager.is_safe_mode else 'off'}"
            f" · {len(self.state_manager.latest_decisions)} symbol(s) under analysis"
        )
        return "\n".join(parts)

    def _pending_answer(self, sym: Optional[str] = None) -> str:
        client = self.mt5_client
        if client is None:
            return ("Working orders are read from the broker, and no broker client is "
                    "attached to this session — start the engine to see them.")
        try:
            orders = client.get_pending_orders(symbol=sym) or []
        except Exception:
            logger.warning("copilot could not read working orders", exc_info=True)
            return "I could not read working orders just now."

        if not orders:
            return (f"No working orders on {sym}." if sym else "You have no working orders.")

        lines = [f"**Working orders — {len(orders)}**\n"]
        for o in orders:
            kind = o.get("type")
            if isinstance(kind, int):
                kind = {2: "BUY LIMIT", 3: "SELL LIMIT", 4: "BUY STOP", 5: "SELL STOP"}.get(kind, str(kind))
            sym_o = str(o.get("symbol") or "")
            lines.append(
                f"- **#{o.get('ticket')} {sym_o} {kind}** "
                f"{float(o.get('volume') or 0):.2f}L @ {self._px(sym_o, o.get('price'))}"
                f" · SL {self._px(sym_o, o.get('sl'))} · TP {self._px(sym_o, o.get('tp'))}"
            )
        return "\n".join(lines)
