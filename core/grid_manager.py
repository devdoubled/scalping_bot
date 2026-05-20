import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GridOrder:
    ticket: int
    entry_price: float
    lot: float


@dataclass
class GridBasket:
    direction: str
    orders: List[GridOrder] = field(default_factory=list)

    @property
    def level_count(self) -> int:
        return len(self.orders)

    @property
    def last_add_price(self) -> float:
        return self.orders[-1].entry_price if self.orders else 0.0

    @property
    def net_lots(self) -> float:
        return round(sum(o.lot for o in self.orders), 2)

    def avg_entry(self) -> float:
        if not self.orders:
            return 0.0
        weighted = sum(o.entry_price * o.lot for o in self.orders)
        return weighted / self.net_lots

    def floating_pnl(self, current_price: float, contract_size: float = 100.0) -> float:
        avg = self.avg_entry()
        if self.direction == "LONG":
            return (current_price - avg) * self.net_lots * contract_size
        else:
            return (avg - current_price) * self.net_lots * contract_size

    def next_add_price(self, grid_step: float) -> float:
        if self.direction == "LONG":
            return self.last_add_price - grid_step
        else:
            return self.last_add_price + grid_step


class GridManager:
    def __init__(self, config: dict, connector, risk_manager):
        self.cfg = config
        self.connector = connector
        self.risk_manager = risk_manager
        self.basket: Optional[GridBasket] = None

    # ── Lot sizing ────────────────────────────────────────────────────────────

    def calculate_lot_size(self, balance: float) -> float:
        risk_pct     = self.cfg.get("risk_per_level_percent", 0.1) / 100.0
        grid_step    = self.cfg["grid_step"]
        contract_size = self.cfg.get("contract_size", 100.0)
        volume_step  = self.cfg.get("volume_step", 0.01)

        risk_amount = balance * risk_pct
        raw_lot = risk_amount / (grid_step * contract_size)
        lot = math.floor(raw_lot / volume_step) * volume_step
        return max(volume_step, round(lot, 2))

    # ── Direction ─────────────────────────────────────────────────────────────

    def get_direction(self, df) -> str:
        mode = self.cfg.get("direction_mode", "last_candle")
        if mode == "long_only":
            return "LONG"
        if mode == "short_only":
            return "SHORT"
        if df is not None and len(df) >= 2:
            last = df.iloc[-2]
            return "LONG" if last["close"] >= last["open"] else "SHORT"
        return "LONG"

    # ── Basket lifecycle ──────────────────────────────────────────────────────

    def start_basket(self, direction: str, balance: float) -> bool:
        lot    = self.calculate_lot_size(balance)
        symbol = self.cfg["symbol"]
        magic  = self.cfg["magic_number"]

        tick = self.connector.get_current_price(symbol)
        if tick is None:
            return False

        ticket = self.connector.place_order(symbol, direction, lot, 0.0, 0.0, magic)
        if ticket is None:
            return False

        entry = tick["ask"] if direction == "LONG" else tick["bid"]
        self.basket = GridBasket(direction=direction)
        self.basket.orders.append(GridOrder(ticket=ticket, entry_price=entry, lot=lot))
        logger.info(
            f"Basket started [{direction}] L1 @ {entry:.2f} lot={lot:.2f} "
            f"risk/level=${balance * self.cfg.get('risk_per_level_percent', 0.1) / 100:.2f}"
        )
        return True

    def check_should_add(self, current_price: float) -> bool:
        if self.basket is None:
            return False
        if self.basket.level_count >= self.cfg["max_levels"]:
            return False
        step = self.cfg["grid_step"]
        last = self.basket.last_add_price
        if self.basket.direction == "LONG":
            return current_price <= last - step
        else:
            return current_price >= last + step

    def add_level(self, balance: float) -> bool:
        if self.basket is None:
            return False
        lot    = self.calculate_lot_size(balance)
        symbol = self.cfg["symbol"]
        magic  = self.cfg["magic_number"]
        direction = self.basket.direction

        tick = self.connector.get_current_price(symbol)
        if tick is None:
            return False

        ticket = self.connector.place_order(symbol, direction, lot, 0.0, 0.0, magic)
        if ticket is None:
            return False

        entry = tick["ask"] if direction == "LONG" else tick["bid"]
        self.basket.orders.append(GridOrder(ticket=ticket, entry_price=entry, lot=lot))
        logger.info(
            f"DCA level {self.basket.level_count} [{direction}] @ {entry:.2f} "
            f"avg={self.basket.avg_entry():.2f} net={self.basket.net_lots:.2f}L"
        )
        return True

    def close_basket(self, reason: str, current_price: float) -> float:
        if self.basket is None:
            return 0.0

        symbol        = self.cfg["symbol"]
        direction     = self.basket.direction
        contract_size = self.cfg.get("contract_size", 100.0)
        avg_entry     = self.basket.avg_entry()
        net_lots      = self.basket.net_lots
        total_pnl     = 0.0

        for order in self.basket.orders:
            self.connector.close_partial(order.ticket, order.lot, symbol, direction)
            if direction == "LONG":
                pnl = (current_price - order.entry_price) * order.lot * contract_size
            else:
                pnl = (order.entry_price - current_price) * order.lot * contract_size
            total_pnl += pnl

        logger.info(
            f"Basket closed [{reason}] levels={self.basket.level_count} "
            f"net={net_lots:.2f}L avg={avg_entry:.2f} exit={current_price:.2f} "
            f"pnl={total_pnl:+.2f}"
        )
        self.risk_manager.record_trade_result(
            total_pnl,
            direction=direction,
            entry=avg_entry,
            exit_price=current_price,
            lots=net_lots,
        )
        self.basket = None
        return total_pnl

    # ── Monitoring helpers ────────────────────────────────────────────────────

    def get_basket_pnl(self, current_price: float) -> float:
        if self.basket is None:
            return 0.0
        return self.basket.floating_pnl(current_price, self.cfg.get("contract_size", 100.0))

    def get_profit_target_dollars(self) -> float:
        if self.basket is None:
            return float("inf")
        pts           = self.cfg["profit_target_pts"]
        contract_size = self.cfg.get("contract_size", 100.0)
        return pts * self.basket.net_lots * contract_size

    def get_bail_out_dollars(self, balance: float) -> float:
        return -(balance * self.cfg["max_basket_loss_percent"] / 100.0)
