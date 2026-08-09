"""
trade_executor.py — Orchestrates trade execution: signal → risk check → order → log.
This is the core decision-making module that combines LLM signals with risk management.
"""
import json
import os
from datetime import datetime, timezone
from loguru import logger
from config import (
    DRY_RUN,
    TRADE_LOG_FILE,
    LEVERAGE,
    SYMBOL_TO_PAIR,
    MUDREX_TRADE_CURRENCY,
)
from mudrex_client import (
    get_wallet_balance,
    get_current_price,
    get_open_positions,
    set_leverage,
    place_market_order,
    place_stop_loss_order,
    place_take_profit_order,
    cancel_all_orders,
    close_position,
)
from risk_manager import RiskManager


class TradeExecutor:
    """Handles complete trade lifecycle from signal to execution to logging."""

    def __init__(self, risk_manager: RiskManager):
        self.risk = risk_manager
        self.trade_log: list[dict] = []
        self._load_trade_log()

    # ─────────────────────────────────────────────
    #  MAIN EXECUTION ENTRY POINT
    # ─────────────────────────────────────────────

    def process_signals(self, signals: list[dict]) -> list[dict]:
        """
        Process a list of LLM signals and execute actionable ones.
        Returns list of executed trade records.
        """
        executed = []

        if not signals:
            return executed

        # Filter to actionable signals only
        actionable = [s for s in signals if s.get("actionable", False)]
        logger.info(f"[Executor] {len(actionable)}/{len(signals)} signals are actionable")

        if not actionable:
            return executed

        # Get current state
        wallet = get_wallet_balance()
        balance = wallet["available"]
        open_positions = get_open_positions()

        # Check daily loss limit
        if not self.risk.check_daily_loss_limit(balance):
            logger.warning("[Executor] Trading paused — daily loss limit hit")
            return executed

        # Process each signal (sorted by confidence, highest first)
        actionable.sort(key=lambda x: x["confidence"], reverse=True)

        for signal in actionable:
            result = self._execute_single_signal(signal, balance, open_positions)
            if result:
                executed.append(result)
                # Refresh position list after each trade
                open_positions = get_open_positions()
                # Update balance estimate
                balance = max(0, balance - result.get("position_value", 0) / LEVERAGE)

        return executed

    def _execute_single_signal(
        self,
        signal: dict,
        balance: float,
        open_positions: list[dict],
    ) -> dict | None:
        """
        Execute a single trading signal through the full pipeline.
        Returns trade record or None if skipped.
        """
        coin = signal["coin"]
        side = signal["signal"]  # "BUY" or "SELL"
        confidence = signal["confidence"]
        reasoning = signal["reasoning"]

        pair = SYMBOL_TO_PAIR.get(coin, f"{coin}/{MUDREX_TRADE_CURRENCY}")

        # Pre-trade risk checks
        can_trade, reason = self.risk.can_open_trade(coin, open_positions, balance)
        if not can_trade:
            logger.info(f"[Executor] Skipping {coin} {side}: {reason}")
            return None

        # Get current price
        current_price = get_current_price(pair)
        if not current_price:
            logger.error(f"[Executor] Cannot get price for {pair} — skipping")
            return None

        # Calculate position size
        quantity = self.risk.calculate_position_size(balance, current_price, coin)
        if quantity <= 0:
            logger.error(f"[Executor] Invalid quantity for {coin} — skipping")
            return None

        # Calculate SL/TP
        sl_price, tp_price = self.risk.calculate_sl_tp(current_price, side, coin)

        # Estimate position value for logging
        position_value = quantity * current_price

        trade_record = {
            "id": f"{coin}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "coin": coin,
            "pair": pair,
            "side": side,
            "quantity": quantity,
            "entry_price": current_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "position_value": position_value,
            "leverage": LEVERAGE,
            "confidence": confidence,
            "reasoning": reasoning,
            "news_basis": signal.get("news_basis", ""),
            "urgency": signal.get("urgency", ""),
            "dry_run": DRY_RUN,
            "status": "pending",
            "exit_price": None,
            "pnl": None,
        }

        if DRY_RUN:
            logger.info(
                f"\n{'='*60}\n"
                f"[DRY RUN] Would execute: {side} {quantity} {pair}\n"
                f"  Entry: {current_price:.4f} | SL: {sl_price:.4f} | TP: {tp_price:.4f}\n"
                f"  Confidence: {confidence}% | Urgency: {signal.get('urgency')}\n"
                f"  Reason: {reasoning}\n"
                f"{'='*60}"
            )
            trade_record["status"] = "dry_run"
            self._save_trade(trade_record)
            return trade_record

        # ─── LIVE TRADING ───
        logger.info(
            f"[Executor] 🚀 EXECUTING: {side} {quantity} {pair} @ {current_price:.4f} | "
            f"Confidence: {confidence}%"
        )

        # 1. Set leverage
        set_leverage(pair, LEVERAGE)

        # 2. Place main market order
        order_result = place_market_order(pair, side, quantity)
        if not order_result:
            logger.error(f"[Executor] ❌ Order FAILED for {coin} {side}")
            trade_record["status"] = "failed"
            self._save_trade(trade_record)
            return None

        order_id = order_result.get("order_id", "unknown")
        trade_record["order_id"] = order_id
        logger.info(f"[Executor] ✅ Order placed: {order_id}")

        # 3. Place Stop Loss
        sl_side = "SELL" if side == "BUY" else "BUY"
        sl_result = place_stop_loss_order(pair, sl_side, quantity, sl_price)
        if sl_result:
            trade_record["sl_order_id"] = sl_result.get("order_id", "unknown")

        # 4. Place Take Profit
        tp_result = place_take_profit_order(pair, sl_side, quantity, tp_price)
        if tp_result:
            trade_record["tp_order_id"] = tp_result.get("order_id", "unknown")

        trade_record["status"] = "open"
        self._save_trade(trade_record)

        logger.info(
            f"[Executor] 📊 Trade OPEN: {side} {quantity} {pair}\n"
            f"  Entry: {current_price:.4f} | SL: {sl_price:.4f} | TP: {tp_price:.4f}\n"
            f"  Order ID: {order_id}"
        )

        return trade_record

    # ─────────────────────────────────────────────
    #  POSITION MANAGEMENT
    # ─────────────────────────────────────────────

    def monitor_and_close_positions(self):
        """
        Check all open positions against SL/TP.
        Called every minute as a safety net.
        """
        open_positions = get_open_positions()
        if not open_positions:
            return

        for position in open_positions:
            symbol = position.get("symbol", "")
            current_price = get_current_price(symbol)
            if not current_price:
                continue

            should_close, reason = self.risk.should_close_position(position, current_price)
            if should_close:
                logger.warning(f"[Executor] Closing {symbol}: {reason}")
                if not DRY_RUN:
                    close_position(
                        symbol,
                        position["size"],
                        position["side"],
                    )
                pnl = self._calculate_pnl(position, current_price)
                self.risk.record_trade_result(pnl)
                self._update_trade_log(symbol, current_price, pnl, reason)

    # ─────────────────────────────────────────────
    #  TRADE LOG
    # ─────────────────────────────────────────────

    def _calculate_pnl(self, position: dict, exit_price: float) -> float:
        """Calculate realized PnL for a closed position."""
        entry = position.get("entry_price", 0)
        size = position.get("size", 0)
        side = position.get("side", "LONG")
        leverage = position.get("leverage", LEVERAGE)

        if side == "LONG":
            pnl = (exit_price - entry) * size * leverage
        else:
            pnl = (entry - exit_price) * size * leverage

        return round(pnl, 2)

    def _save_trade(self, trade: dict):
        """Save trade to log file."""
        self.trade_log.append(trade)
        self._write_trade_log()

    def _update_trade_log(self, symbol: str, exit_price: float, pnl: float, reason: str):
        """Update an open trade in the log when it closes."""
        for trade in self.trade_log:
            if (
                trade.get("status") == "open"
                and symbol.lower() in trade.get("pair", "").lower()
            ):
                trade["status"] = "closed"
                trade["exit_price"] = exit_price
                trade["pnl"] = pnl
                trade["close_reason"] = reason
                trade["closed_at"] = datetime.now(timezone.utc).isoformat()
                break
        self._write_trade_log()

    def _load_trade_log(self):
        """Load existing trade log from file."""
        if os.path.exists(TRADE_LOG_FILE):
            try:
                with open(TRADE_LOG_FILE, "r") as f:
                    self.trade_log = json.load(f)
                logger.info(f"[Executor] Loaded {len(self.trade_log)} trades from log")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[Executor] Could not load trade log: {e}")
                self.trade_log = []
        else:
            self.trade_log = []

    def _write_trade_log(self):
        """Persist trade log to JSON file."""
        try:
            with open(TRADE_LOG_FILE, "w") as f:
                json.dump(self.trade_log, f, indent=2)
        except IOError as e:
            logger.error(f"[Executor] Failed to write trade log: {e}")

    def get_recent_trades(self, n: int = 10) -> list[dict]:
        """Get the N most recent trades for dashboard."""
        return list(reversed(self.trade_log[-n:]))

    def get_open_trades_from_log(self) -> list[dict]:
        """Get currently open trades from the log."""
        return [t for t in self.trade_log if t.get("status") == "open"]
