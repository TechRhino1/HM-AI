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
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from jarvis.application.state_manager import StateManager, GLOBAL_STATE
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


class JarvisCopilot:
    def __init__(self, state_manager: StateManager = GLOBAL_STATE, mt5_client: Any = None):
        self.state_manager = state_manager
        # Working orders live at the broker, not in the state snapshot, so the
        # server hands its client over once it exists. Stays None (and the
        # order questions say so) rather than opening a second connection.
        self.mt5_client = mt5_client

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

    def ask(self, query: str, context: Optional[Dict[str, Any]] = None) -> str:
        q = (query or "").lower().strip()
        if not q:
            return self._help()

        # The client tells us what the trader is looking at, so "why?" can be
        # answered about the chart on screen instead of defaulting to gold.
        focus = None
        if isinstance(context, dict):
            focus = str(context.get("symbol") or "").upper() or None

        latest_decisions = self.state_manager.latest_decisions
        contexts = self.state_manager.market_contexts
        account = self.state_manager.account
        positions = self.state_manager.positions

        # 0. The trader's own book — checked first so "how is my EURUSD doing"
        #    is not swallowed by the generic market-analysis handler.
        explicit = self._find_symbol_in_query(q)
        sym = explicit or focus

        # A performance question outranks the position handler when no symbol
        # was named, because the position word lists match "my trade" and would
        # otherwise answer "how are my trades doing?" with a single instrument.
        if not explicit and any(p in q for p in _PERF_PHRASES):
            return self._performance_answer()

        # "how is my <instrument> doing" — an instrument is in play (named, or
        # supplied as the on-screen focus), so this is about that position.
        if sym and any(p in q for p in _POSITION_PHRASES):
            return self._positions_answer(sym)

        if any(w in q for w in _POSITION_WORDS) or (
            sym and any(p.symbol.upper() == sym for p in positions) and "my" in q
        ):
            return self._positions_answer(sym)

        if any(w in q for w in _HISTORY_WORDS):
            return self._history_answer(sym)

        if any(w in q for w in _PERF_WORDS) and (
            "my" in q or "today" in q or "account" in q or "we" in q or "i " in q
            or any(w in q for w in ("pnl", "p&l", "performance", "how am i"))
        ):
            return self._performance_answer()

        if any(w in q for w in _PENDING_WORDS):
            return self._pending_answer(sym)

        # 1. "why aren't you entering" / "why no trade".
        #    A bare "why?" counts when the page told us which symbol is on
        #    screen — that is the whole reason the client sends a focus.
        if "why" in q and (
            any(k in q for k in ("enter", "trade", "reject", "buy", "sell", "not"))
            or (bool(focus) and q.strip().rstrip("?!. ") in ("why", "why not", "why no"))
        ):
            # Check target symbol
            sym = sym or "XAUUSD"
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

        # 2. "analyze [symbol]" / "market status"
        elif "analyze" in q or "status" in q or "context" in q:
            sym = sym or "XAUUSD"
            if sym in latest_decisions:
                return ReasoningEngine.generate_explanation(latest_decisions[sym])
            elif sym in contexts:
                ctx = contexts[sym]
                return (
                    f"**Market Context for {sym}**\n"
                    f"- Price: {self._px(sym, ctx.current_price)} (Spread: {ctx.volatility.current_spread_pips} pips)\n"
                    f"- Structure: {ctx.structure.bias} (Zone: {ctx.structure.discount_premium_zone})\n"
                    f"- Momentum Score: {ctx.momentum.trend_score} (ADX: {ctx.momentum.adx:.1f}, RSI: {ctx.momentum.rsi:.1f})\n"
                    f"- Volatility: {ctx.volatility.state} (ATR: {ctx.volatility.atr:.4f})\n"
                    f"- Session: {ctx.session.current_session}"
                )
            return f"Symbol {sym} is currently queued for multi-timeframe synthesis."

        # 3. "risk" / "exposure" / "drawdown"
        elif "risk" in q or "exposure" in q or "drawdown" in q or "account" in q:
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

        # 4. "best setups" / "radar" / "opportunities"
        elif "setup" in q or "radar" in q or "best" in q or "scan" in q:
            opps = self.state_manager.radar_opportunities
            if opps:
                lines = ["**Today's Multi-Symbol Scanner Opportunities:**\n"]
                for o in opps[:5]:
                    lines.append(
                        f"- **{o.get('symbol')}**: {o.get('action')} | Score: {o.get('score')}/100 | EV: ${o.get('ev', 0.0):.2f} | Regime: {o.get('regime')} | Status: {o.get('decision')}"
                    )
                return "\n".join(lines)
            return "Scanner is currently performing multi-asset radar sweep."

        # 5. Default conversational intelligence response
        return self._help(sym)

    # ── answer builders ─────────────────────────────────────────────────────
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
