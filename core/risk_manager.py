import math
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    pnl: float
    direction: str
    entry: float
    exit: float
    lots: float


class RiskManager:
    def __init__(self, config: dict):
        self.risk_percent = config["risk_percent"] / 100.0
        self.max_daily_loss_pct = config["max_daily_loss_percent"] / 100.0
        self.max_consecutive_losses = config["max_consecutive_losses"]
        self.max_concurrent_positions = config["max_concurrent_positions"]

        self._daily_loss = 0.0
        self._daily_profit = 0.0
        self._consecutive_losses = 0
        self._session_start_balance = None
        self._trade_records: List[TradeRecord] = []

    def set_session_balance(self, balance: float):
        if self._session_start_balance is None:
            self._session_start_balance = balance

    def calculate_lot_size(self, balance: float, sl_distance: float, tick_value: float, volume_step: float = 0.01) -> float:
        """
        Calculate lot size such that the dollar loss at SL equals risk_percent of balance.
        sl_distance: in price units (e.g. 1.50 means $1.50 for XAUUSD where 1 pip = $1)
        tick_value: value of 1 pip per 0.01 lot (from broker)
        """
        if sl_distance <= 0 or tick_value <= 0:
            return volume_step

        risk_amount = balance * self.risk_percent
        # sl_distance in price / pip_size gives pips; tick_value per 0.01 lot
        # lot = risk_amount / (sl_pips * tick_value_per_lot)
        # For XAUUSD: 1 pip = $1 per 0.01 lot, so tick_value_per_lot = tick_value * 100
        sl_pips = sl_distance / 0.01  # XAUUSD pip = 0.01
        pip_value_per_lot = tick_value * 100  # scale from 0.01 lot to 1 lot

        raw_lot = risk_amount / (sl_pips * pip_value_per_lot)
        lot = math.floor(raw_lot / volume_step) * volume_step
        lot = max(volume_step, round(lot, 2))

        logger.debug(f"Lot calc: balance={balance:.2f}, risk={risk_amount:.2f}, sl_pips={sl_pips:.1f}, raw_lot={raw_lot:.4f}, lot={lot:.2f}")
        return lot

    def check_daily_limits(self) -> tuple[bool, str]:
        """Return (can_trade, reason). Checks daily loss and consecutive loss limits."""
        if self._session_start_balance and self._daily_loss > 0:
            daily_loss_pct = self._daily_loss / self._session_start_balance
            if daily_loss_pct >= self.max_daily_loss_pct:
                return False, f"Daily loss limit reached: {daily_loss_pct*100:.1f}% >= {self.max_daily_loss_pct*100:.1f}%"

        if self._consecutive_losses >= self.max_consecutive_losses:
            return False, f"Consecutive loss limit reached: {self._consecutive_losses}"

        return True, ""

    def record_trade_result(self, pnl: float, direction: str = "",
                             entry: float = 0.0, exit_price: float = 0.0,
                             lots: float = 0.0):
        record = TradeRecord(pnl=pnl, direction=direction,
                             entry=entry, exit=exit_price, lots=lots)
        self._trade_records.append(record)

        if pnl < 0:
            self._daily_loss += abs(pnl)
            self._consecutive_losses += 1
            logger.info(f"Loss: {pnl:.2f} | Daily loss: {self._daily_loss:.2f} | Streak: {self._consecutive_losses}")
        else:
            self._daily_profit += pnl
            self._consecutive_losses = 0
            logger.info(f"Win: {pnl:.2f} | Daily profit: {self._daily_profit:.2f} | Streak reset")

    def reset_daily(self):
        self._daily_loss = 0.0
        self._daily_profit = 0.0
        self._consecutive_losses = 0
        self._session_start_balance = None
        self._trade_records.clear()
        logger.info("Daily stats reset")

    def get_daily_stats(self) -> dict:
        records = self._trade_records
        wins   = [r for r in records if r.pnl >= 0]
        losses = [r for r in records if r.pnl < 0]
        total_pnl = self._daily_profit - self._daily_loss
        win_rate  = len(wins) / len(records) * 100 if records else 0.0
        best  = max((r.pnl for r in records), default=0.0)
        worst = min((r.pnl for r in records), default=0.0)
        avg   = total_pnl / len(records) if records else 0.0
        return {
            "total_pnl":   total_pnl,
            "total_trades":len(records),
            "wins":        len(wins),
            "losses":      len(losses),
            "win_rate":    win_rate,
            "best_trade":  best,
            "worst_trade": worst,
            "avg_trade":   avg,
        }

    @property
    def daily_loss(self) -> float:
        return self._daily_loss

    @property
    def daily_pnl(self) -> float:
        return self._daily_profit - self._daily_loss

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses
