"""
risk_manager.py — Position sizing, risk checks, and SL/TP calculations.
All trading decisions pass through this module before execution.
"""
from loguru import logger
from config import (
    RISK_PER_TRADE_PCT,
    LEVERAGE,
    MAX_OPEN_POSITIONS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    MAX_DAILY_LOSS_PCT,
    SYMBOL_TO_PAIR,
    COIN_SYMBOLS,
)


class RiskManager:
    """Manages all risk calculations and pre-trade checks."""

    def __init__(self):
        self.daily_realized_pnl = 0.0      # Tracks PnL for the day
        self.daily_loss_limit_hit = False   # Flag: bot pauses if True
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0

    def reset_daily_stats(self):
        """Call at start of each trading day (midnight)."""
        self.daily_realized_pnl = 0.0
        self.daily_loss_limit_hit = False
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        logger.info("[RiskManager] Daily stats reset")

    def record_trade_result(self, pnl: float):
        """Record completed trade PnL."""
        self.daily_realized_pnl += pnl
        self.trades_today += 1
        if pnl > 0:
            self.wins_today += 1
        else:
            self.losses_today += 1

    def check_daily_loss_limit(self, wallet_balance: float) -> bool:
        """
        Returns True if trading is ALLOWED, False if daily loss limit hit.
        """
        if wallet_balance <= 0:
            return True

        daily_loss_pct = (self.daily_realized_pnl / wallet_balance) * 100
        if daily_loss_pct <= -MAX_DAILY_LOSS_PCT:
            if not self.daily_loss_limit_hit:
                logger.warning(
                    f"[RiskManager] 🚨 DAILY LOSS LIMIT HIT: {daily_loss_pct:.2f}% "
                    f"(limit: -{MAX_DAILY_LOSS_PCT}%). Bot pausing."
                )
            self.daily_loss_limit_hit = True
            return False

        self.daily_loss_limit_hit = False
        return True

    def can_open_trade(
        self,
        coin: str,
        open_positions: list[dict],
        wallet_balance: float,
    ) -> tuple[bool, str]:
        """
        Comprehensive pre-trade check.
        Returns (can_trade: bool, reason: str)
        """
        # 1. Check daily loss limit
        if self.daily_loss_limit_hit:
            return False, "Daily loss limit reached — bot paused until tomorrow"

        # 2. Check max positions
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            return False, f"Max positions reached ({MAX_OPEN_POSITIONS})"

        # 3. Check if already in this coin
        coin_pair = SYMBOL_TO_PAIR.get(coin, f"{coin}/INR")
        for pos in open_positions:
            if coin.lower() in pos.get("symbol", "").lower():
                return False, f"Already in position for {coin}"

        # 4. Check wallet balance
        if wallet_balance < 100:  # Minimum ₹100 or $1 to trade
            return False, f"Insufficient balance: {wallet_balance}"

        return True, "OK"

    def calculate_position_size(
        self,
        wallet_balance: float,
        entry_price: float,
        coin: str,
    ) -> float:
        """
        Calculate safe position size based on risk % and leverage.
        
        Formula:
        risk_amount = wallet * RISK_PCT / 100
        position_value = risk_amount * leverage
        quantity = position_value / entry_price
        """
        if entry_price <= 0 or wallet_balance <= 0:
            return 0.0

        risk_amount = wallet_balance * (RISK_PER_TRADE_PCT / 100)
        position_value = risk_amount * LEVERAGE
        quantity = position_value / entry_price

        logger.info(
            f"[RiskManager] {coin}: wallet={wallet_balance:.2f}, "
            f"risk={risk_amount:.2f} ({RISK_PER_TRADE_PCT}%), "
            f"position_value={position_value:.2f}, "
            f"quantity={quantity:.6f}"
        )
        return round(quantity, 6)

    def calculate_sl_tp(
        self,
        entry_price: float,
        side: str,
        coin: str = "",
    ) -> tuple[float, float]:
        """
        Calculate Stop Loss and Take Profit prices.
        
        For BUY (LONG):
          SL = entry * (1 - SL_PCT/100)
          TP = entry * (1 + TP_PCT/100)
          
        For SELL (SHORT):
          SL = entry * (1 + SL_PCT/100)
          TP = entry * (1 - TP_PCT/100)
        """
        if side == "BUY":
            sl_price = entry_price * (1 - STOP_LOSS_PCT / 100)
            tp_price = entry_price * (1 + TAKE_PROFIT_PCT / 100)
        else:  # SELL / SHORT
            sl_price = entry_price * (1 + STOP_LOSS_PCT / 100)
            tp_price = entry_price * (1 - TAKE_PROFIT_PCT / 100)

        logger.info(
            f"[RiskManager] {coin} {side}: Entry={entry_price:.4f} | "
            f"SL={sl_price:.4f} (-{STOP_LOSS_PCT}%) | "
            f"TP={tp_price:.4f} (+{TAKE_PROFIT_PCT}%)"
        )
        return round(sl_price, 4), round(tp_price, 4)

    def should_close_position(
        self,
        position: dict,
        current_price: float,
    ) -> tuple[bool, str]:
        """
        Manual SL/TP check for positions (safety net if exchange orders fail).
        Returns (should_close: bool, reason: str)
        """
        if not position or current_price <= 0:
            return False, ""

        entry = position.get("entry_price", 0)
        if entry <= 0:
            return False, ""

        side = position.get("side", "LONG")

        if side == "LONG":
            pct_change = ((current_price - entry) / entry) * 100
            if pct_change <= -STOP_LOSS_PCT:
                return True, f"Stop Loss hit: {pct_change:.2f}%"
            if pct_change >= TAKE_PROFIT_PCT:
                return True, f"Take Profit hit: {pct_change:.2f}%"
        else:  # SHORT
            pct_change = ((entry - current_price) / entry) * 100
            if pct_change <= -STOP_LOSS_PCT:
                return True, f"Stop Loss hit: {pct_change:.2f}%"
            if pct_change >= TAKE_PROFIT_PCT:
                return True, f"Take Profit hit: {pct_change:.2f}%"

        return False, ""

    def get_stats(self) -> dict:
        """Return current risk stats for dashboard."""
        win_rate = (
            self.wins_today / self.trades_today * 100
            if self.trades_today > 0 else 0
        )
        return {
            "daily_pnl": self.daily_realized_pnl,
            "trades_today": self.trades_today,
            "wins": self.wins_today,
            "losses": self.losses_today,
            "win_rate": round(win_rate, 1),
            "daily_loss_limit_hit": self.daily_loss_limit_hit,
        }
