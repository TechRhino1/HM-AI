"""
HM Algo 2.0 — Master Execution Engine.
Orchestrates order dispatch, mode verification (LIVE/PAPER/DEMO), and execution logging.
"""
import logging
from typing import Dict, Any
from jarvis.data.schemas import DecisionObject
from jarvis.execution.mt5_client import MT5Client
from jarvis.application.state_manager import StateManager, GLOBAL_STATE

logger = logging.getLogger("JARVIS_ExecutionEngine")

class ExecutionEngine:
    def __init__(self, mt5_client: MT5Client, state_manager: StateManager = GLOBAL_STATE):
        self.mt5_client = mt5_client
        self.state_manager = state_manager

    def _price_origin(self, res: Dict[str, Any]) -> str:
        """Where the fill price came from — `broker`, `paper` or `synthetic` (D1).

        Paper and live fills land in the same table and were indistinguishable,
        so every realised-P&L number read from it mixed simulated money with
        real. A fallback price is worse than paper: it means the quote was
        missing and a stand-in was used, and the row must not be counted as a
        market result at all.
        """
        if res.get("is_fallback"):
            return "synthetic"
        mode = str(getattr(self.mt5_client, "mode", "") or "").lower()
        if mode == "paper":
            return "paper"
        if mode in ("live", "demo"):
            # A live client that could not reach the terminal falls back too;
            # `not MT5_AVAILABLE` in send_market_order takes the same branch as
            # paper, so ask whether the client actually connected.
            return "broker" if getattr(self.mt5_client, "is_connected", True) else "synthetic"
        return "unknown"

    def _execution_mode(self, res: Dict[str, Any] = None) -> str:
        """The execution mode this fill was produced under (D1, completed).

        Deliberately NOT derived from :meth:`_price_origin`. A live client that
        has lost the terminal takes the same branch as paper inside
        `send_market_order`, so its price is a stand-in — but the order was
        still routed to a real account, and the row has to say so. Conflating
        "the price is a stand-in" with "this was simulated money" is what left
        paper and live fills indistinguishable in the first place.
        """
        mode = str(getattr(self.mt5_client, "mode", "") or "").lower()
        return mode if mode in ("live", "paper", "demo") else "unknown"

    def execute_decision(self, decision: DecisionObject, lots: float) -> Dict[str, Any]:
        """Dispatches authorized decision to MT5 or Paper Simulator."""
        if not decision.execution_authorized or lots <= 0:
            logger.warning(f"Execution rejected for {decision.symbol}: Not authorized or invalid lot size ({lots}).")
            return {"status": "BLOCKED", "reason": "Execution unauthorized"}

        if self.state_manager.is_safe_mode:
            logger.warning(f"Execution blocked for {decision.symbol}: SAFE MODE is ACTIVE.")
            return {"status": "BLOCKED", "reason": "Safe mode active"}

        mode = self.state_manager.execution_mode
        comment = f"HMA2_{decision.strategy[:6]}_{mode.value}"

        logger.info(f"DISPATCHING ORDER [{mode.value}]: {decision.bias} {lots} {decision.symbol} @ Entry={decision.entry_price} SL={decision.stop_loss} TP={decision.take_profit}")

        order_kind = getattr(decision, "order_type", "MARKET").upper()
        use_limit = "LIMIT" in order_kind or "PENDING" in order_kind

        if use_limit:
            # Task 2b: Direction-vs-price sanity check for resting limit orders
            ctx = getattr(decision, "context", None)
            cur_price = getattr(ctx, "current_price", decision.entry_price) if ctx else decision.entry_price
            bid_price = getattr(ctx, "bid", cur_price) if ctx else cur_price
            ask_price = getattr(ctx, "ask", cur_price) if ctx else cur_price

            is_sane_limit = True
            if decision.bias == "BUY" and decision.entry_price >= bid_price:
                logger.warning(
                    f"⚠️ BUY_LIMIT price ({decision.entry_price}) is NOT below current bid ({bid_price}) for {decision.symbol}. "
                    f"Falling back safely to MARKET order dispatch."
                )
                is_sane_limit = False
            elif decision.bias == "SELL" and decision.entry_price <= ask_price:
                logger.warning(
                    f"⚠️ SELL_LIMIT price ({decision.entry_price}) is NOT above current ask ({ask_price}) for {decision.symbol}. "
                    f"Falling back safely to MARKET order dispatch."
                )
                is_sane_limit = False

            if is_sane_limit:
                limit_dir = "BUY_LIMIT" if decision.bias == "BUY" else "SELL_LIMIT"
                res = self.mt5_client.place_pending_order(
                    symbol=decision.symbol,
                    order_type=limit_dir,
                    price=decision.entry_price,
                    volume=lots,
                    sl_price=decision.stop_loss,
                    tp_price=decision.take_profit,
                    comment=comment
                )
            else:
                res = self.mt5_client.send_market_order(
                    symbol=decision.symbol,
                    order_type=decision.bias,
                    volume=lots,
                    sl_price=decision.stop_loss,
                    tp_price=decision.take_profit,
                    comment=comment,
                    reference_price=decision.entry_price
                )
        else:
            res = self.mt5_client.send_market_order(
                symbol=decision.symbol,
                order_type=decision.bias,
                volume=lots,
                sl_price=decision.stop_loss,
                tp_price=decision.take_profit,
                comment=comment,
                reference_price=decision.entry_price
            )

        # §2: Re-anchor SL/TP to actual fill price if slippage occurred
        if res and res.get("status") == "FILLED":
            ticket = res.get("ticket")
            fill_price = float(res.get("price", decision.entry_price))
            sl_dist = getattr(decision, "sl_distance", 0.0)
            tp_dist = getattr(decision, "tp_distance", 0.0)

            if ticket and sl_dist > 0 and abs(fill_price - decision.entry_price) > 1e-5:
                if decision.bias == "BUY":
                    actual_sl = fill_price - sl_dist
                    actual_tp = (fill_price + tp_dist) if tp_dist > 0 else decision.take_profit
                else:
                    actual_sl = fill_price + sl_dist
                    actual_tp = (fill_price - tp_dist) if tp_dist > 0 else decision.take_profit

                actual_rr = round(tp_dist / (sl_dist + 1e-9), 2)
                logger.info(
                    f"⚓ RE-ANCHORING SL/TP to real fill: Ticket=#{ticket} Fill={fill_price} "
                    f"(planned {decision.entry_price}) -> Real SL={actual_sl:.4f}, TP={actual_tp:.4f} (R:R={actual_rr})"
                )
                planned_dist = abs(decision.entry_price - decision.stop_loss)
                if planned_dist > 0 and sl_dist > planned_dist * 1.25:
                    logger.warning(
                        f"Post-fill stop on #{ticket} is {sl_dist / planned_dist:.1f}x the planned "
                        f"distance — the broker minimum stop distance likely widened it. Lots are "
                        f"not recomputed after fill, so realised risk exceeds the sizer's target."
                    )
                mod_res = self.mt5_client.modify_position(ticket=ticket, sl=actual_sl, tp=actual_tp)
                if mod_res and mod_res.get("status") == "MODIFIED":
                    res["sl"] = actual_sl
                    res["tp"] = actual_tp
                    res["real_fill_anchored"] = True
                    
            try:
                import json
                from jarvis.data.database import TRADE_DB
                
                # Extract rich feature vector from MarketContext and DecisionObject
                ctx = getattr(decision, "context", None)
                if not ctx and hasattr(self.state_manager, "get_market_context"):
                    ctx = self.state_manager.get_market_context(decision.symbol)
                if not ctx and hasattr(self.state_manager, "market_contexts") and isinstance(self.state_manager.market_contexts, dict):
                    ctx = self.state_manager.market_contexts.get(decision.symbol)

                session_name = ctx.session.current_session if ctx and ctx.session else "UNKNOWN"
                is_prime = ctx.session.is_prime_session if ctx and ctx.session else True
                adx_val = float(ctx.momentum.adx) if ctx and ctx.momentum else 0.0
                plus_di = float(ctx.momentum.plus_di) if ctx and ctx.momentum else 0.0
                minus_di = float(ctx.momentum.minus_di) if ctx and ctx.momentum else 0.0
                spread_pips = float(ctx.volatility.current_spread_pips) if ctx and ctx.volatility else 0.0
                mtf_str = json.dumps(ctx.mtf_alignment) if ctx and ctx.mtf_alignment else ""
                threats_json = json.dumps(decision.risk_factors or [])
                features_json = json.dumps({
                    "strategy": decision.strategy,
                    "adversarial_penalty": decision.adversarial_penalty,
                    "expected_value": decision.expected_value,
                    "rr_ratio": decision.risk_reward_ratio,
                    "model_confidence": decision.model_confidence
                })

                TRADE_DB.log_trade(
                    ticket=ticket,
                    symbol=decision.symbol,
                    action=decision.bias,
                    entry=fill_price,
                    sl=res.get("sl", decision.stop_loss),
                    tp=res.get("tp", decision.take_profit),
                    volume=lots,
                    score=decision.model_confidence * 100.0,
                    regime=decision.regime.primary_regime.value if decision.regime else "UNKNOWN",
                    ev=decision.expected_value,
                    executor="BOT (AI)",
                    session_name=session_name,
                    is_prime_session=is_prime,
                    adx=adx_val,
                    plus_di=plus_di,
                    minus_di=minus_di,
                    spread_pips=spread_pips,
                    mtf_alignment=mtf_str,
                    threats_json=threats_json,
                    features_json=features_json,
                    # D2: the position id, not the order ticket, is what the exit
                    # deal will be keyed on. Without it the row can never close.
                    position_id=res.get("position_id"),
                    # D1: say where the fill price came from, so a statistic over
                    # this table can separate real money from simulated.
                    origin=self._price_origin(res),
                    # D1 (completed): and say whether it WAS real money. These
                    # are different questions -- a live fill whose client had
                    # lost the terminal is origin='synthetic' but
                    # execution_mode='live', and that row used to be
                    # unclassifiable.
                    execution_mode=self._execution_mode(res),
                )
            except Exception as e:
                logger.error(f"Failed to log trade to DB: {e}")

        return res
