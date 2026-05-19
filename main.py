"""
XAUUSD Scalping Bot — main entry point.
Strategy: M5 EMA Momentum (8/13/21 EMA + RSI + ATR)
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from core.mt5_connector import MT5Connector
from core.signal_engine import SignalEngine, NEUTRAL
from core.risk_manager import RiskManager
from core.trade_manager import TradeManager, ManagedTrade
from core.session_filter import is_trading_session, get_active_session_name, utc_now
from gui.dashboard import Dashboard

# ── Logging setup ─────────────────────────────────────────────────────────────
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

# ── State machine states ──────────────────────────────────────────────────────
STATE_SCANNING = "SCANNING"
STATE_ENTRY_READY = "ENTRY_READY"
STATE_IN_TRADE = "IN_TRADE"
STATE_COOLING_DOWN = "COOLING_DOWN"
STATE_STOPPED = "STOPPED"


class ScalpingBot:
    def __init__(self, config: dict):
        self.cfg = config
        self.symbol = config["symbol"]
        self.magic = config["magic_number"]
        self.paper_mode = config.get("paper_mode", False)

        self.connector = MT5Connector("mt5_credentials.json", paper_mode=self.paper_mode)
        self.signal_engine = SignalEngine(config)
        self.risk_manager = RiskManager(config)
        self.trade_manager: TradeManager = None  # init after connector

        self._state = STATE_STOPPED
        self._running = False
        self._cooling_bars = 0
        self._last_signal: dict = {}
        self._dashboard: Dashboard = None
        self._paper_forced_trade_done = False

        self._symbol_info: dict = {}

    def attach_dashboard(self, dashboard: Dashboard):
        self._dashboard = dashboard

    def start(self):
        logger.info("Bot starting...")
        # Reuse existing connection from auto-connect; only connect if not already connected
        if not self.connector.is_connected():
            connected = self.connector.connect()
            if not connected:
                mode_note = " Paper mode still needs MT5 market data." if self.paper_mode else ""
                self._log(f"Failed to connect to MT5. Check credentials and MT5 terminal.{mode_note}", "ERROR")
                return

        if self.trade_manager is None:
            self.trade_manager = TradeManager(self.cfg, self.connector, self.risk_manager)
        if not self._symbol_info:
            self._symbol_info = self.connector.get_symbol_info(self.symbol) or {
                "trade_tick_value": 1.0,
                "trade_tick_size": 0.01,
                "volume_step": 0.01,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "trade_contract_size": 100.0,
            }

        acc = self.connector.get_account_info()
        if acc:
            self.risk_manager.set_session_balance(acc["balance"])

        self._state = STATE_SCANNING
        self._running = True
        self._paper_forced_trade_done = False
        self._update_dashboard_state()
        self._log(f"Bot started | Symbol: {self.symbol} | Mode: {'PAPER' if self.paper_mode else 'LIVE'}", "INFO")

        self._loop()

    def stop(self):
        self._running = False
        self._state = STATE_STOPPED
        self.connector.disconnect()
        self._update_dashboard_state()
        self._log("Bot stopped.", "WARN")

    def emergency_close_all(self):
        if self.trade_manager:
            self.trade_manager.close_all()
            if self._dashboard:
                self._dashboard.update_positions(self.trade_manager.get_trades())
        self._log("Emergency close all executed.", "WARN")

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"Tick error: {e}")
                self._log(f"Error: {e}", "ERROR")

            interval = (
                self.cfg["polling_interval_trade_ms"]
                if self._state == STATE_IN_TRADE
                else self.cfg["polling_interval_scan_ms"]
            )
            time.sleep(interval / 1000.0)

    def _tick(self):
        now = utc_now()
        if self._dashboard:
            self._dashboard.update_time(now)

        # Update account info
        acc = self.connector.get_account_info()
        if acc and self._dashboard:
            self._dashboard.update_account(
                acc["balance"], acc["equity"],
                self.risk_manager.daily_pnl,
                self.risk_manager.consecutive_losses,
            )
            self._dashboard.update_performance(self.risk_manager.get_daily_stats())

        session_name = get_active_session_name(now, self.cfg["sessions"])
        if self._dashboard:
            self._dashboard.update_session(session_name)

        # ── COOLING_DOWN ──────────────────────────────────────────────
        if self._state == STATE_COOLING_DOWN:
            self._cooling_bars -= 1
            if self._cooling_bars <= 0:
                self._state = STATE_SCANNING
                self._update_dashboard_state()
            return

        # ── IN_TRADE — monitor positions ──────────────────────────────
        if self._state == STATE_IN_TRADE:
            if self.trade_manager.trade_count() == 0:
                self._state = STATE_COOLING_DOWN
                self._cooling_bars = self.cfg["cooling_down_candles"]
                self._update_dashboard_state()
                self._log("All positions closed → cooling down", "INFO")
                return

            tick = self.connector.get_current_price(self.symbol)
            if tick:
                price = (tick["bid"] + tick["ask"]) / 2
                df = self.connector.get_ohlcv(self.symbol, self.cfg["timeframe"], self.cfg["ohlcv_bars"])
                trades = self.trade_manager.get_trades()
                price_by_ticket = self._closeable_prices_by_ticket(trades, tick)
                price_by_ticket.update(self._paper_auto_tp_prices(trades))
                events = self.trade_manager.monitor(price, df, self.signal_engine, price_by_ticket=price_by_ticket)
                for ev in events:
                    pnl = f" pnl={ev['pnl']:+.2f}" if "pnl" in ev else ""
                    self._log(f"Trade event: {ev['type']} ticket={ev['ticket']}{pnl}", "TRADE")

                if self._dashboard:
                    self._dashboard.update_positions(self.trade_manager.get_trades())

            # Sync with MT5 open positions to detect SL hits
            self._sync_positions()
            return

        # ── SESSION GATE ──────────────────────────────────────────────
        force_entry_available = self._paper_force_entry_available()
        if not is_trading_session(now, self.cfg["sessions"]):
            if not (force_entry_available and self.cfg.get("paper_force_ignore_session", True)):
                return
            self._log("Paper force entry bypassing session gate for test trade.", "WARN")

        # Do not open a new test trade while a managed trade is active.
        if force_entry_available and self.trade_manager and self.trade_manager.trade_count() > 0:
            return

        # ── DAILY LIMIT CHECK ─────────────────────────────────────────
        can_trade, reason = self.risk_manager.check_daily_limits()
        if not can_trade:
            self._log(f"Trading halted: {reason}", "WARN")
            self._state = STATE_STOPPED
            self._running = False
            self._update_dashboard_state()
            return

        # ── MAX POSITIONS CHECK ───────────────────────────────────────
        if self.trade_manager and self.trade_manager.trade_count() >= self.cfg["max_concurrent_positions"]:
            return

        # ── SCANNING — fetch data and signals ─────────────────────────
        spread = self.connector.get_current_spread(self.symbol) or 999
        df = self.connector.get_ohlcv(self.symbol, self.cfg["timeframe"], self.cfg["ohlcv_bars"])

        if df is None or len(df) < 30:
            self._log("Insufficient data", "WARN")
            return

        signal = self.signal_engine.get_signal(df, spread)
        if signal.get("direction", NEUTRAL) == NEUTRAL and force_entry_available:
            signal = self.signal_engine.get_forced_signal(
                df,
                spread,
                self.cfg.get("paper_force_direction", "AUTO"),
            )
            self._log(f"Paper forced signal generated: {signal['direction']}", "WARN")
        self._last_signal = signal

        if self._dashboard:
            tick = self.connector.get_current_price(self.symbol)
            price = (tick["bid"] + tick["ask"]) / 2 if tick else 0
            self._dashboard.update_indicators({
                "ema_fast": signal.get("ema_fast", 0),
                "ema_medium": signal.get("ema_medium", 0),
                "ema_slow": signal.get("ema_slow", 0),
                "rsi": signal.get("rsi", 50),
                "atr": signal.get("atr", 0),
                "spread": spread,
                "price": price,
                "direction": signal.get("direction", "NEUTRAL"),
            })
            self._dashboard.update_filters(signal.get("filters", {}))

        direction = signal.get("direction", NEUTRAL)
        if direction == NEUTRAL:
            if self._state == STATE_ENTRY_READY:
                self._state = STATE_SCANNING
                self._update_dashboard_state()
            return

        # ── ENTRY_READY ───────────────────────────────────────────────
        if self._state == STATE_SCANNING:
            self._state = STATE_ENTRY_READY
            self._update_dashboard_state()
            self._log(f"Entry ready: {direction} | RSI={signal['rsi']:.1f} | ATR={signal['atr']:.2f}", "INFO")
            if signal.get("forced") or self.cfg.get("entry_confirmation_ticks", 1) <= 1:
                self._execute_entry(signal, acc)
            return

        if self._state == STATE_ENTRY_READY:
            self._execute_entry(signal, acc)

    def _execute_entry(self, signal: dict, acc: dict):
        direction = signal["direction"]
        atr = signal["atr"]
        sl = signal["sl"]
        tp3 = signal["tp3"]

        balance = acc["balance"] if acc else 10000.0
        tick_value = self._symbol_info.get("trade_tick_value", 1.0)
        tick_size = self._symbol_info.get("trade_tick_size", 0.01)
        volume_step = self._symbol_info.get("volume_step", 0.01)
        volume_min = self._symbol_info.get("volume_min", 0.01)
        volume_max = self._symbol_info.get("volume_max")
        sl_distance = signal["sl_distance"]

        lot = self.risk_manager.calculate_lot_size(
            balance,
            sl_distance,
            tick_value,
            volume_step,
            tick_size,
            volume_min,
            volume_max,
        )

        self._log(f"Entering {direction} | Lot={lot:.2f} | SL={sl:.2f} | TP3={tp3:.2f}", "TRADE")

        ticket = self.connector.place_order(
            self.symbol, direction, lot, sl, tp3, self.magic
        )

        if ticket is None:
            self._log("Order placement failed", "ERROR")
            self._state = STATE_SCANNING
            self._update_dashboard_state()
            return

        trade = ManagedTrade(
            ticket=ticket,
            symbol=self.symbol,
            direction=direction,
            entry_price=signal["close"],
            lot_total=lot,
            sl=sl,
            tp1=signal["tp1"],
            tp2=signal["tp2"],
            tp3=tp3,
            tick_size=tick_size,
            tick_value=tick_value,
            contract_size=self._symbol_info.get("trade_contract_size", 100.0),
        )
        self.trade_manager.add_trade(trade)
        if signal.get("forced"):
            self._paper_forced_trade_done = True

        self._state = STATE_IN_TRADE
        self._update_dashboard_state()

        if self._dashboard:
            self._dashboard.update_positions(self.trade_manager.get_trades())

    def _sync_positions(self):
        if self.paper_mode:
            return

        open_tickets = {p.ticket for p in self.connector.get_open_positions(self.symbol, self.magic)}
        managed_tickets = {t.ticket for t in self.trade_manager.get_trades()}

        closed = managed_tickets - open_tickets
        for ticket in closed:
            tick = self.connector.get_current_price(self.symbol)
            exit_price = (tick["bid"] + tick["ask"]) / 2 if tick else 0
            self.trade_manager.handle_sl_hit(ticket, exit_price)
            self._log(f"SL hit detected: ticket={ticket}", "WARN")

    def _paper_force_entry_available(self) -> bool:
        if not self.paper_mode or not self.cfg.get("paper_force_entry", False):
            return False
        if self.cfg.get("paper_force_once_per_start", True) and self._paper_forced_trade_done:
            return False
        return True

    def _closeable_prices_by_ticket(self, trades, tick: dict) -> dict:
        prices = {}
        for trade in trades:
            prices[trade.ticket] = tick["bid"] if trade.direction == "LONG" else tick["ask"]
        return prices

    def _paper_auto_tp_prices(self, trades) -> dict:
        if not self.paper_mode or not self.cfg.get("paper_auto_take_profit", False):
            return {}

        delay = float(self.cfg.get("paper_auto_take_profit_after_seconds", 5))
        now = time.time()
        prices = {}
        for trade in trades:
            if now - trade.opened_at >= delay:
                prices[trade.ticket] = trade.tp3
        return prices

    def _start_price_ticker(self):
        def _ticker():
            while self.connector.is_connected():
                try:
                    tick = self.connector.get_current_price(self.symbol)
                    spread = self.connector.get_current_spread(self.symbol) or 0
                    if tick and self._dashboard:
                        mid = (tick["bid"] + tick["ask"]) / 2
                        self._dashboard.update_price(tick["bid"], tick["ask"], spread)
                        if self.trade_manager:
                            pnls = {t.ticket: t.unrealized_pnl(mid)
                                    for t in self.trade_manager.get_trades()}
                            if pnls:
                                self._dashboard.update_position_pnl(pnls)
                except Exception:
                    pass
                time.sleep(0.2)

        t = threading.Thread(target=_ticker, daemon=True)
        t.start()

    def _update_dashboard_state(self):
        if self._dashboard:
            self._dashboard.update_state(self._state)

    def _log(self, msg: str, level: str = "INFO"):
        logger.info(msg)
        if self._dashboard:
            self._dashboard.log(msg, level)


def load_config() -> dict:
    with open("config.json") as f:
        return json.load(f)


def main():
    os.chdir(Path(__file__).parent)
    cfg = load_config()
    bot = ScalpingBot(cfg)

    def on_start():
        bot.start()

    def on_stop():
        bot.stop()

    def on_close_all():
        bot.emergency_close_all()

    dashboard = Dashboard(on_start=on_start, on_stop=on_stop, on_close_all=on_close_all)
    bot.attach_dashboard(dashboard)

    def auto_connect():
        dashboard.log("Connecting to MT5...", "INFO")
        connected = bot.connector.connect()
        if connected:
            acc = bot.connector.get_account_info()
            dashboard.update_connection(True)
            if acc:
                bot.risk_manager.set_session_balance(acc["balance"])
                bot.trade_manager = TradeManager(cfg, bot.connector, bot.risk_manager)
                bot._symbol_info = bot.connector.get_symbol_info(bot.symbol) or {
                    "trade_tick_value": 1.0,
                    "trade_tick_size": 0.01,
                    "volume_step": 0.01,
                    "volume_min": 0.01,
                    "volume_max": 100.0,
                    "trade_contract_size": 100.0,
                }
                mode_tag = " | ⚠ PAPER MODE — no real orders" if cfg.get("paper_mode") else " | LIVE TRADING"
                dashboard.log(
                    f"Connected | Account: {acc['login']} | Balance: {acc['currency']} {acc['balance']:,.2f}{mode_tag}",
                    "TRADE" if not cfg.get("paper_mode") else "WARN",
                )
                dashboard.update_account(acc["balance"], acc["equity"], 0.0, 0)
                bot._start_price_ticker()
        else:
            dashboard.update_connection(False)
            mode_note = " Paper mode still needs MT5 market data." if cfg.get("paper_mode") else ""
            dashboard.log(
                f"MT5 connection failed. Check mt5_credentials.json and ensure MT5 terminal is running.{mode_note}",
                "ERROR",
            )

    # Auto-connect in background thread after GUI is shown
    dashboard.schedule(500, lambda: threading.Thread(target=auto_connect, daemon=True).start())
    dashboard.log("Starting... auto-connecting to MT5 terminal.", "INFO")

    dashboard.run()


if __name__ == "__main__":
    main()
