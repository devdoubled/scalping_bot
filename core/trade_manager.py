import logging
import math
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class ManagedTrade:
    ticket: int
    symbol: str
    direction: str
    entry_price: float
    lot_total: float
    sl: float
    tp1: float
    tp2: float
    tp3: float

    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    breakeven_set: bool = False

    lot_remaining: float = field(init=False)

    def __post_init__(self):
        self.lot_remaining = self.lot_total

    def unrealized_pnl(self, current_price: float) -> float:
        if self.direction == "LONG":
            return (current_price - self.entry_price) * self.lot_remaining * 100
        else:
            return (self.entry_price - current_price) * self.lot_remaining * 100


class TradeManager:
    def __init__(self, config: dict, connector, risk_manager):
        self.cfg = config
        self.connector = connector
        self.risk_manager = risk_manager
        self._trades: List[ManagedTrade] = []

    def add_trade(self, trade: ManagedTrade):
        self._trades.append(trade)
        logger.info(f"Tracking trade: ticket={trade.ticket} {trade.direction} {trade.lot_total:.2f}L")

    def remove_trade(self, ticket: int):
        self._trades = [t for t in self._trades if t.ticket != ticket]

    def get_trades(self) -> List[ManagedTrade]:
        return list(self._trades)

    def trade_count(self) -> int:
        return len(self._trades)

    def monitor(self, current_price: float, df=None, signal_engine=None) -> List[dict]:
        """
        Check all managed trades against current price.
        Returns list of events (partial closes, SL moves, reversals).
        """
        events = []
        for trade in list(self._trades):
            trade_events = self._check_trade(trade, current_price, df, signal_engine)
            events.extend(trade_events)
        return events

    def _check_trade(self, trade: ManagedTrade, price: float, df, signal_engine) -> List[dict]:
        events = []
        symbol = trade.symbol
        direction = trade.direction

        # TP1
        if not trade.tp1_hit and self._tp_hit(price, trade.tp1, direction):
            close_vol = self._partial_volume(trade, self.cfg["tp1_close_percent"])
            if close_vol > 0 and self.connector.close_partial(trade.ticket, close_vol, symbol, direction):
                trade.tp1_hit = True
                trade.lot_remaining = round(trade.lot_remaining - close_vol, 2)
                logger.info(f"TP1 hit ticket={trade.ticket} closed={close_vol:.2f}L remaining={trade.lot_remaining:.2f}L")
                events.append({"type": "TP1", "ticket": trade.ticket, "volume": close_vol})

        # Early breakeven: move SL to entry once price moves early_breakeven_r × sl_dist
        early_be_r = self.cfg.get("early_breakeven_r", 0.0)
        if early_be_r > 0 and not trade.breakeven_set:
            sl_dist = abs(trade.entry_price - trade.sl)
            trigger_dist = sl_dist * early_be_r
            in_profit = (
                (direction == "LONG"  and price >= trade.entry_price + trigger_dist) or
                (direction == "SHORT" and price <= trade.entry_price - trigger_dist)
            )
            if in_profit:
                if self.connector.modify_sl(trade.ticket, trade.entry_price):
                    trade.sl = trade.entry_price
                    trade.breakeven_set = True
                    logger.info(f"Early breakeven ticket={trade.ticket} SL→{trade.entry_price:.2f} (price={price:.2f})")
                    events.append({"type": "BREAKEVEN", "ticket": trade.ticket})

        # TP2 + breakeven (fallback if early BE not triggered)
        if trade.tp1_hit and not trade.tp2_hit and self._tp_hit(price, trade.tp2, direction):
            close_vol = self._partial_volume(trade, self.cfg["tp2_close_percent"])
            if close_vol > 0 and self.connector.close_partial(trade.ticket, close_vol, symbol, direction):
                trade.tp2_hit = True
                trade.lot_remaining = round(trade.lot_remaining - close_vol, 2)
                logger.info(f"TP2 hit ticket={trade.ticket} closed={close_vol:.2f}L remaining={trade.lot_remaining:.2f}L")
                events.append({"type": "TP2", "ticket": trade.ticket, "volume": close_vol})

            if not trade.breakeven_set:
                if self.connector.modify_sl(trade.ticket, trade.entry_price):
                    trade.sl = trade.entry_price
                    trade.breakeven_set = True
                    logger.info(f"Breakeven set ticket={trade.ticket} SL={trade.entry_price:.2f}")
                    events.append({"type": "BREAKEVEN", "ticket": trade.ticket})

        # TP3 — close all remaining
        if trade.tp2_hit and not trade.tp3_hit and self._tp_hit(price, trade.tp3, direction):
            if trade.lot_remaining > 0:
                if self.connector.close_partial(trade.ticket, trade.lot_remaining, symbol, direction):
                    trade.tp3_hit = True
                    logger.info(f"TP3 hit ticket={trade.ticket} — fully closed")
                    events.append({"type": "TP3", "ticket": trade.ticket})
                    self._finalize_trade(trade, price)

        # EMA reversal exit
        if df is not None and signal_engine is not None and not trade.tp3_hit:
            if signal_engine.check_ema_reversal(df, direction):
                if trade.lot_remaining > 0:
                    if self.connector.close_partial(trade.ticket, trade.lot_remaining, symbol, direction):
                        logger.info(f"EMA reversal exit ticket={trade.ticket} price={price:.2f}")
                        events.append({"type": "EMA_REVERSAL", "ticket": trade.ticket})
                        self._finalize_trade(trade, price)

        return events

    def _tp_hit(self, price: float, tp: float, direction: str) -> bool:
        if direction == "LONG":
            return price >= tp
        return price <= tp

    def _partial_volume(self, trade: ManagedTrade, percent: int) -> float:
        vol = math.floor((trade.lot_total * percent / 100) / 0.01) * 0.01
        return min(vol, trade.lot_remaining)

    def _finalize_trade(self, trade: ManagedTrade, exit_price: float):
        if trade.direction == "LONG":
            pnl = (exit_price - trade.entry_price) * trade.lot_total * 100
        else:
            pnl = (trade.entry_price - exit_price) * trade.lot_total * 100
        self.risk_manager.record_trade_result(
            pnl,
            direction=trade.direction,
            entry=trade.entry_price,
            exit_price=exit_price,
            lots=trade.lot_total,
        )
        self.remove_trade(trade.ticket)

    def handle_sl_hit(self, ticket: int, exit_price: float):
        for trade in list(self._trades):
            if trade.ticket == ticket:
                self._finalize_trade(trade, exit_price)
                break

    def close_all(self):
        for trade in list(self._trades):
            if trade.lot_remaining > 0:
                self.connector.close_partial(trade.ticket, trade.lot_remaining, trade.symbol, trade.direction)
                logger.info(f"Emergency close: ticket={trade.ticket}")
        self._trades.clear()
