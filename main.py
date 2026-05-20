"""
XAUUSD DCA Grid Bot — main entry point.
Strategy: No-signal DCA basket — add levels as price moves against, close on recovery.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from core.mt5_connector import MT5Connector
from core.grid_manager import GridManager
from core.risk_manager import RiskManager
from core.session_filter import (
    get_active_session, get_active_session_name,
    is_session_ending_soon, utc_now,
)
from gui.dashboard import Dashboard

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")

# ── States ────────────────────────────────────────────────────────────────────
STATE_IDLE         = "IDLE"
STATE_BASKET_OPEN  = "BASKET_OPEN"
STATE_COOLING_DOWN = "COOLING_DOWN"
STATE_STOPPED      = "STOPPED"


def _mid(tick: dict) -> float:
    return (tick["bid"] + tick["ask"]) / 2.0


class DCAGridBot:
    def __init__(self, config: dict):
        self.cfg         = config
        self.symbol      = config["symbol"]
        self.magic       = config["magic_number"]
        self.paper_mode  = config.get("paper_mode", False)

        self.connector    = MT5Connector("mt5_credentials.json", paper_mode=self.paper_mode)
        self.risk_manager = RiskManager(config)
        self.grid_manager = GridManager(config, self.connector, self.risk_manager)

        self._state      = STATE_STOPPED
        self._running    = False
        self._cool_start: datetime = None
        self._last_balance: float  = 0.0
        self._symbol_info: dict    = {}
        self._dashboard: Dashboard = None

    def attach_dashboard(self, dashboard: Dashboard):
        self._dashboard = dashboard

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        logger.info("Bot starting...")
        if not self.connector.is_connected():
            connected = self.connector.connect()
            if not connected and not self.paper_mode:
                self._log("Failed to connect to MT5.", "ERROR")
                return

        if not self._symbol_info:
            self._symbol_info = self.connector.get_symbol_info(self.symbol) or {}

        acc = self.connector.get_account_info()
        if acc:
            self._last_balance = acc["balance"]
            self.risk_manager.set_session_balance(acc["balance"])

        # Inject volume_step from MT5 into config so grid_manager can use it
        if "volume_step" not in self.cfg:
            self.cfg["volume_step"] = self._symbol_info.get("volume_step", 0.01)

        self._state   = STATE_IDLE
        self._running = True
        self._update_dashboard_state()
        self._log(
            f"Bot started | Symbol: {self.symbol} | "
            f"Mode: {'PAPER' if self.paper_mode else 'LIVE'} | "
            f"Grid: {self.cfg['grid_step']}pts x{self.cfg['max_levels']} levels",
            "INFO",
        )
        self._loop()

    def stop(self):
        self._running = False
        self._state   = STATE_STOPPED
        self.connector.disconnect()
        self._update_dashboard_state()
        self._log("Bot stopped.", "WARN")

    def emergency_close_all(self):
        if self.grid_manager.basket is not None:
            tick  = self.connector.get_current_price(self.symbol)
            price = _mid(tick) if tick else 0.0
            pnl   = self.grid_manager.close_basket("EMERGENCY", price)
            self._log(f"Emergency close all | pnl={pnl:+.2f}", "WARN")
        self._log("Emergency close executed.", "WARN")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"Tick error: {e}")
                self._log(f"Error: {e}", "ERROR")
            time.sleep(self.cfg.get("polling_interval_ms", 500) / 1000.0)

    def _tick(self):
        now = utc_now()
        if self._dashboard:
            self._dashboard.update_time(now)

        acc = self.connector.get_account_info()
        if acc:
            self._last_balance = acc["balance"]
            if self._dashboard:
                self._dashboard.update_account(
                    acc["balance"], acc["equity"],
                    self.risk_manager.daily_pnl,
                    self.risk_manager.consecutive_losses,
                )
                self._dashboard.update_performance(self.risk_manager.get_daily_stats())

        session     = get_active_session(now, self.cfg["sessions"])
        session_name = session["name"] if session else "CLOSED"
        in_session  = session is not None
        ending_soon = is_session_ending_soon(
            now, session, self.cfg.get("session_close_buffer_min", 5)
        )
        if self._dashboard:
            self._dashboard.update_session(session_name)

        # ── COOLING_DOWN ──────────────────────────────────────────────────────
        if self._state == STATE_COOLING_DOWN:
            elapsed = (now - self._cool_start).total_seconds()
            if elapsed >= self.cfg.get("cooling_down_seconds", 60):
                self._state = STATE_IDLE
                self._update_dashboard_state()
                self._log("Cooling done — ready for next basket.", "INFO")
            self._push_dashboard_status(session_name)
            return

        # ── STOPPED ───────────────────────────────────────────────────────────
        if self._state == STATE_STOPPED:
            self._push_dashboard_status(session_name)
            return

        # ── DAILY LIMITS ──────────────────────────────────────────────────────
        can_trade, reason = self.risk_manager.check_daily_limits()
        if not can_trade:
            if self.grid_manager.basket is not None:
                tick  = self.connector.get_current_price(self.symbol)
                price = _mid(tick) if tick else 0.0
                pnl   = self.grid_manager.close_basket("DAILY_LIMIT", price)
                self._log(f"Basket force-closed [DAILY_LIMIT] pnl={pnl:+.2f}", "WARN")
            self._log(f"Trading halted: {reason}", "WARN")
            self._state = STATE_STOPPED
            self._update_dashboard_state()
            return

        # ── BASKET_OPEN ───────────────────────────────────────────────────────
        if self._state == STATE_BASKET_OPEN:
            self._monitor_basket(now, in_session, ending_soon, session_name)
            return

        # ── IDLE ──────────────────────────────────────────────────────────────
        if self._state == STATE_IDLE:
            if not in_session or ending_soon:
                self._push_dashboard_status(session_name)
                return
            self._try_start_basket(session_name)

    # ── Basket logic ──────────────────────────────────────────────────────────

    def _monitor_basket(self, now, in_session, ending_soon, session_name):
        basket = self.grid_manager.basket
        if basket is None:
            self._enter_cooling_down(now)
            return

        tick = self.connector.get_current_price(self.symbol)
        if not tick:
            return
        price   = _mid(tick)
        pnl     = self.grid_manager.get_basket_pnl(price)
        profit  = self.grid_manager.get_profit_target_dollars()
        bail    = self.grid_manager.get_bail_out_dollars(self._last_balance)

        self._push_dashboard_status(session_name, price, pnl, bail)

        # Session ending / outside session — close basket cleanly
        if not in_session or ending_soon:
            closed_pnl = self.grid_manager.close_basket("SESSION_END", price)
            self._log(f"Basket closed [SESSION_END] pnl={closed_pnl:+.2f}", "TRADE")
            self._enter_cooling_down(now)
            return

        # Profit target hit
        if pnl >= profit:
            closed_pnl = self.grid_manager.close_basket("PROFIT", price)
            self._log(
                f"Basket closed [PROFIT] levels={basket.level_count} "
                f"pnl={closed_pnl:+.2f}",
                "TRADE",
            )
            self._enter_cooling_down(now)
            return

        # Bail-out
        if pnl <= bail:
            closed_pnl = self.grid_manager.close_basket("BAIL_OUT", price)
            self._log(
                f"Basket closed [BAIL_OUT] levels={basket.level_count} "
                f"pnl={closed_pnl:+.2f}",
                "WARN",
            )
            self._enter_cooling_down(now)
            return

        # Add next DCA level if price moved grid_step against us
        spread = self.connector.get_current_spread(self.symbol) or 999
        if spread <= self.cfg["max_spread_points"] and self.grid_manager.check_should_add(price):
            if not self.connector.is_algo_trading_enabled():
                self._log("Algo Trading disabled — cannot add level.", "ERROR")
                return
            if self.grid_manager.add_level(self._last_balance):
                b = self.grid_manager.basket
                self._log(
                    f"Level {b.level_count} added @ {price:.2f} | "
                    f"avg={b.avg_entry():.2f} | net={b.net_lots:.2f}L",
                    "TRADE",
                )
                if self._dashboard:
                    self._dashboard.update_positions(
                        self._basket_to_rows(b, price, pnl, bail)
                    )

    def _try_start_basket(self, session_name):
        spread = self.connector.get_current_spread(self.symbol) or 999
        if spread > self.cfg["max_spread_points"]:
            return

        if not self.connector.is_algo_trading_enabled():
            self._log("Order blocked: enable Algo Trading in MT5 toolbar.", "ERROR")
            self._state = STATE_STOPPED
            self._update_dashboard_state()
            return

        df = self.connector.get_ohlcv(
            self.symbol, self.cfg["timeframe"], self.cfg["ohlcv_bars"]
        )
        direction = self.grid_manager.get_direction(df)

        if self.grid_manager.start_basket(direction, self._last_balance):
            self._state = STATE_BASKET_OPEN
            self._update_dashboard_state()
            b = self.grid_manager.basket
            tick  = self.connector.get_current_price(self.symbol)
            price = _mid(tick) if tick else 0.0
            self._log(
                f"Basket started [{direction}] @ {price:.2f} | "
                f"lot={b.orders[0].lot:.2f} | session={session_name}",
                "TRADE",
            )
        else:
            self._log("Failed to start basket.", "ERROR")

    def _enter_cooling_down(self, now):
        self._state      = STATE_COOLING_DOWN
        self._cool_start = now
        self._update_dashboard_state()
        secs = self.cfg.get("cooling_down_seconds", 60)
        self._log(f"Cooling down {secs}s before next basket.", "INFO")
        if self._dashboard:
            self._dashboard.update_positions([])

    # ── Dashboard helpers ─────────────────────────────────────────────────────

    def _push_dashboard_status(self, session_name, price=0.0, pnl=0.0,
                                bail=0.0):
        if not self._dashboard:
            return
        basket = self.grid_manager.basket
        if basket:
            profit = self.grid_manager.get_profit_target_dollars()
            rows = self._basket_to_rows(basket, price, pnl, bail)
            self._dashboard.update_positions(rows)
            self._dashboard.update_grid_status({
                "basket_active": (True,  "OPEN"),
                "direction":     (True,  basket.direction),
                "levels":        (basket.level_count < self.cfg["max_levels"],
                                  f"{basket.level_count} / {self.cfg['max_levels']}"),
                "float_pnl":     (pnl >= 0, f"${pnl:+,.2f}"),
                "next_add":      (True,
                                  f"@ {basket.next_add_price(self.cfg['grid_step']):.2f}"),
                "session":       (True,  session_name),
            })
            self._dashboard.update_grid_metrics({
                "basket_open":    True,
                "level":          f"{basket.level_count}/{self.cfg['max_levels']}",
                "direction":      basket.direction,
                "avg_entry":      basket.avg_entry(),
                "float_pnl":      pnl,
                "next_add":       basket.next_add_price(self.cfg["grid_step"]),
                "bail_out":       bail,
                "profit_target":  profit,
            })
        else:
            self._dashboard.update_grid_status({
                "basket_active": (False, "NO BASKET"),
                "direction":     (False, "--"),
                "levels":        (True,  f"0 / {self.cfg['max_levels']}"),
                "float_pnl":     (True,  "$0.00"),
                "next_add":      (False, "--"),
                "session":       (bool(session_name != "CLOSED"), session_name),
            })
            self._dashboard.update_grid_metrics({
                "basket_open": False,
                "level": "--", "direction": "--", "avg_entry": 0,
                "float_pnl": 0, "next_add": 0, "bail_out": 0, "profit_target": 0,
            })

    def _basket_to_rows(self, basket, price: float, total_pnl: float,
                         bail: float) -> list:
        if not basket or not basket.orders:
            return []
        avg = basket.avg_entry()
        contract_size = self.cfg.get("contract_size", 100.0)
        rows = []
        for i, order in enumerate(basket.orders, 1):
            if basket.direction == "LONG":
                order_pnl = (price - order.entry_price) * order.lot * contract_size
            else:
                order_pnl = (order.entry_price - price) * order.lot * contract_size
            rows.append({
                "level":      i,
                "ticket":     order.ticket,
                "direction":  basket.direction,
                "lot":        order.lot,
                "entry":      order.entry_price,
                "avg_entry":  avg,
                "pnl":        order_pnl,
            })
        return rows

    def _update_dashboard_state(self):
        if self._dashboard:
            self._dashboard.update_state(self._state)

    def _log(self, msg: str, level: str = "INFO"):
        logger.info(msg)
        if self._dashboard:
            self._dashboard.log(msg, level)

    # ── Price ticker (200 ms, separate thread) ────────────────────────────────

    def _start_price_ticker(self):
        def _ticker():
            while self.connector.is_connected():
                try:
                    tick   = self.connector.get_current_price(self.symbol)
                    spread = self.connector.get_current_spread(self.symbol) or 0
                    if tick and self._dashboard:
                        self._dashboard.update_price(tick["bid"], tick["ask"], spread)
                        basket = self.grid_manager.basket
                        if basket and tick:
                            price = _mid(tick)
                            pnl   = self.grid_manager.get_basket_pnl(price)
                            bail  = self.grid_manager.get_bail_out_dollars(
                                self._last_balance
                            )
                            self._dashboard.update_position_pnl(
                                {order.ticket: pnl / basket.level_count
                                 for order in basket.orders}
                            )
                except Exception:
                    pass
                time.sleep(0.2)

        threading.Thread(target=_ticker, daemon=True).start()


def load_config() -> dict:
    with open("config.json") as f:
        return json.load(f)


def main():
    os.chdir(Path(__file__).parent)
    cfg = load_config()
    bot = DCAGridBot(cfg)

    dashboard = Dashboard(
        on_start=bot.start,
        on_stop=bot.stop,
        on_close_all=bot.emergency_close_all,
    )
    bot.attach_dashboard(dashboard)

    def auto_connect():
        dashboard.log("Connecting to MT5...", "INFO")
        connected = bot.connector.connect()
        if connected:
            acc = bot.connector.get_account_info()
            dashboard.update_connection(True)
            if acc:
                bot.risk_manager.set_session_balance(acc["balance"])
                bot._last_balance = acc["balance"]
                bot._symbol_info  = bot.connector.get_symbol_info(bot.symbol) or {}
                bot.cfg["volume_step"] = bot._symbol_info.get("volume_step", 0.01)
                mode_tag = " | PAPER MODE" if cfg.get("paper_mode") else " | LIVE TRADING"
                dashboard.log(
                    f"Connected | Account: {acc['login']} | "
                    f"Balance: {acc['currency']} {acc['balance']:,.2f}{mode_tag}",
                    "TRADE" if not cfg.get("paper_mode") else "WARN",
                )
                dashboard.update_account(acc["balance"], acc["equity"], 0.0, 0)
                bot._start_price_ticker()
        else:
            dashboard.update_connection(False)
            dashboard.log(
                "MT5 connection failed. Check mt5_credentials.json "
                "and ensure MT5 terminal is running.",
                "ERROR",
            )

    dashboard.schedule(
        500,
        lambda: threading.Thread(target=auto_connect, daemon=True).start(),
    )
    dashboard.log("Starting — auto-connecting to MT5 terminal.", "INFO")
    dashboard.run()


if __name__ == "__main__":
    main()
