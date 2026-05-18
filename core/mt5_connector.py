import json
import logging
import time
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 library not available — paper mode only")

TIMEFRAME_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}


class MT5Connector:
    def __init__(self, credentials_path: str = "mt5_credentials.json", paper_mode: bool = False):
        self._credentials_path = credentials_path
        self._paper_mode = paper_mode
        self._connected = False
        self._credentials: Dict[str, Any] = {}

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            logger.warning("MT5 library missing — running in paper mode")
            self._connected = False
            return False

        # Strategy 1: attach to already-running MT5 terminal (no credentials needed)
        # This is the same approach as mt5_live_trading_bot — just call initialize()
        # with no args and MT5 connects to whatever account is currently logged in.
        logger.info("Attempting to connect to running MT5 terminal...")
        if mt5.initialize():
            info = mt5.account_info()
            if info is not None:
                logger.info(f"Auto-connected to MT5 | Account: {info.login} | Balance: {info.balance:.2f} {info.currency}")
                self._connected = True
                return True
            mt5.shutdown()

        # Strategy 2: launch MT5 using credentials file (fallback)
        logger.info("No running MT5 found — trying credentials file...")
        creds = self._load_credentials()
        if creds is None:
            logger.error("No running MT5 and no valid credentials file. Open MT5 terminal and log in first.")
            return False

        path = creds.get("path", "")
        ok = mt5.initialize(path=path) if path else mt5.initialize()
        if not ok:
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False

        logged_in = mt5.login(
            login=creds["login"],
            password=creds["password"],
            server=creds["server"],
        )
        if not logged_in:
            logger.error(f"MT5 login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False

        info = mt5.account_info()
        logger.info(f"Connected via credentials | Account: {info.login} | Balance: {info.balance:.2f} {info.currency}")
        self._connected = True
        return True

    def _load_credentials(self) -> Optional[Dict]:
        try:
            with open(self._credentials_path) as f:
                creds = json.load(f)
            # Only use if the file has been filled in (not template values)
            if str(creds.get("login", "")) in ("", "12345678", "YOUR_MT5_ACCOUNT_NUMBER"):
                logger.warning("Credentials file has template values — skipping")
                return None
            if creds.get("password", "") in ("", "your_password_here"):
                logger.warning("Credentials file password not set — skipping")
                return None
            return creds
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"Could not read credentials file: {e}")
            return None

    def disconnect(self):
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("MT5 disconnected")

    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> Optional[Dict]:
        if not self._connected:
            return None
        info = mt5.account_info()
        if info is None:
            return None
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "currency": info.currency,
            "login": info.login,
        }

    def get_ohlcv(self, symbol: str, timeframe: str, count: int = 100) -> Optional[pd.DataFrame]:
        if not self._connected:
            return None

        tf = TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None

        # M1–M30 use mt5.TIMEFRAME_M* constants directly
        tf_const = self._get_tf_const(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning(f"No data for {symbol} {timeframe}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    def get_current_spread(self, symbol: str) -> Optional[float]:
        if not self._connected:
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return info.spread

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        if not self._connected:
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return {
            "point": info.point,
            "digits": info.digits,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "volume_max": info.volume_max,
            "trade_tick_value": info.trade_tick_value,
            "trade_contract_size": info.trade_contract_size,
        }

    def get_current_price(self, symbol: str) -> Optional[Dict]:
        if not self._connected:
            return None
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}

    def place_order(
        self,
        symbol: str,
        direction: str,
        lot: float,
        sl: float,
        tp: float,
        magic: int = 20260518,
        comment: str = "XAUUSD_SCALPER",
    ) -> Optional[int]:
        if self._paper_mode or not self._connected:
            logger.info(f"[PAPER] {direction} {lot:.2f}L {symbol} | SL={sl:.2f} TP={tp:.2f}")
            return -1

        order_type = mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if direction == "LONG" else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result}")
            return None

        logger.info(f"Order placed: ticket={result.order} {direction} {lot:.2f} @ {price:.2f} SL={sl:.2f} TP={tp:.2f}")
        return result.order

    def close_partial(self, ticket: int, volume: float, symbol: str, direction: str, magic: int = 20260518) -> bool:
        if self._paper_mode or not self._connected:
            logger.info(f"[PAPER] Close {volume:.2f}L ticket={ticket}")
            return True

        order_type = mt5.ORDER_TYPE_SELL if direction == "LONG" else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(symbol)
        price = tick.bid if direction == "LONG" else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": magic,
            "comment": "PARTIAL_CLOSE",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Partial close failed ticket={ticket}: {result}")
            return False

        logger.info(f"Partial close: ticket={ticket} volume={volume:.2f}")
        return True

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        if self._paper_mode or not self._connected:
            logger.info(f"[PAPER] Move SL ticket={ticket} to {new_sl:.2f}")
            return True

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"SL modify failed ticket={ticket}: {result}")
            return False

        logger.info(f"SL moved: ticket={ticket} new_sl={new_sl:.2f}")
        return True

    def close_position(self, ticket: int, symbol: str, direction: str, volume: float, magic: int = 20260518) -> bool:
        return self.close_partial(ticket, volume, symbol, direction, magic)

    def get_open_positions(self, symbol: str, magic: int) -> list:
        if not self._connected:
            return []
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        return [p for p in positions if p.magic == magic]

    def _get_tf_const(self, timeframe: str):
        if not MT5_AVAILABLE:
            return None
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        return mapping.get(timeframe, mt5.TIMEFRAME_M5)
