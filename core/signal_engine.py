import logging
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

NEUTRAL = "NEUTRAL"
LONG = "LONG"
SHORT = "SHORT"
BULLISH = "BULLISH"
BEARISH = "BEARISH"


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    return prices.ewm(span=period, adjust=False).mean()


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def get_ema_alignment(ema_fast: float, ema_medium: float, ema_slow: float) -> str:
    if ema_fast > ema_medium > ema_slow:
        return BULLISH
    if ema_fast < ema_medium < ema_slow:
        return BEARISH
    return NEUTRAL


def get_ema_slope(ema_series: pd.Series, lookback: int = 3) -> float:
    """Returns the average slope (price change per bar) over the last `lookback` bars."""
    if len(ema_series) < lookback + 1:
        return 0.0
    recent = ema_series.iloc[-lookback:]
    return float(recent.diff().mean())


def check_pullback(df: pd.DataFrame, ema_medium: pd.Series, ema_slow: pd.Series, alignment: str) -> bool:
    """
    True if price has pulled back to touch EMA13 or EMA21 in the last 3 bars
    before the current (last) bar, indicating a healthy retracement rather than chase.
    """
    if len(df) < 4:
        return False

    lows = df["low"].iloc[-4:-1]
    highs = df["high"].iloc[-4:-1]
    ema_m = ema_medium.iloc[-4:-1]
    ema_s = ema_slow.iloc[-4:-1]

    if alignment == BULLISH:
        touched_medium = (lows <= ema_m).any()
        touched_slow = (lows <= ema_s).any()
    else:
        touched_medium = (highs >= ema_m).any()
        touched_slow = (highs >= ema_s).any()

    return touched_medium or touched_slow


def check_candle_confirmation(df: pd.DataFrame, direction: str, min_body_ratio: float = 0.5) -> bool:
    """Last closed candle must close in trend direction with body > min_body_ratio of total range."""
    if len(df) < 2:
        return False

    candle = df.iloc[-2]
    candle_range = candle["high"] - candle["low"]
    if candle_range == 0:
        return False

    body = abs(candle["close"] - candle["open"])
    body_ratio = body / candle_range

    if body_ratio < min_body_ratio:
        return False

    if direction == LONG:
        return candle["close"] > candle["open"]
    else:
        return candle["close"] < candle["open"]


class SignalEngine:
    def __init__(self, config: dict):
        self.cfg = config

    def analyse(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Run all indicator calculations on the OHLCV dataframe.
        Returns a dict with indicator values and signal state.
        """
        ema_fast = calculate_ema(df["close"], self.cfg["ema_fast"])
        ema_medium = calculate_ema(df["close"], self.cfg["ema_medium"])
        ema_slow = calculate_ema(df["close"], self.cfg["ema_slow"])
        rsi = calculate_rsi(df["close"], self.cfg["rsi_period"])
        atr = calculate_atr(df, self.cfg["atr_period"])

        ef = float(ema_fast.iloc[-1])
        em = float(ema_medium.iloc[-1])
        es = float(ema_slow.iloc[-1])
        rsi_val = float(rsi.iloc[-1])
        atr_val = float(atr.iloc[-1])
        current_close = float(df["close"].iloc[-1])

        alignment = get_ema_alignment(ef, em, es)
        slope = get_ema_slope(ema_fast)

        return {
            "ema_fast": ef,
            "ema_medium": em,
            "ema_slow": es,
            "rsi": rsi_val,
            "atr": atr_val,
            "close": current_close,
            "alignment": alignment,
            "slope": slope,
            "ema_fast_series": ema_fast,
            "ema_medium_series": ema_medium,
            "ema_slow_series": ema_slow,
            "df": df,
        }

    def get_signal(self, df: pd.DataFrame, spread: float) -> Dict[str, Any]:
        """
        Run all entry filters. Returns signal dict with direction and price levels.
        """
        data = self.analyse(df)
        filters = {}

        # Filter 1: ATR activity
        filters["atr_active"] = data["atr"] >= self.cfg["atr_min_threshold"]

        # Filter 2: Spread
        filters["spread_ok"] = spread <= self.cfg["max_spread_points"]

        # Filter 3: EMA alignment
        alignment = data["alignment"]
        filters["ema_aligned"] = alignment in (BULLISH, BEARISH)

        # Filter 4: EMA slope
        slope = data["slope"]
        min_slope = self.cfg["ema_slope_min"]
        filters["slope_ok"] = abs(slope) >= min_slope

        # Determine direction from alignment
        direction = LONG if alignment == BULLISH else SHORT if alignment == BEARISH else NEUTRAL

        # Filter 5: Pullback
        filters["pullback"] = check_pullback(
            data["df"],
            data["ema_medium_series"],
            data["ema_slow_series"],
            alignment,
        ) if direction != NEUTRAL else False

        # Filter 6: RSI zone
        if direction == LONG:
            filters["rsi_zone"] = self.cfg["rsi_long_min"] <= data["rsi"] <= self.cfg["rsi_long_max"]
        elif direction == SHORT:
            filters["rsi_zone"] = self.cfg["rsi_short_min"] <= data["rsi"] <= self.cfg["rsi_short_max"]
        else:
            filters["rsi_zone"] = False

        # Filter 7: Candle confirmation
        filters["candle_confirm"] = check_candle_confirmation(
            data["df"], direction, self.cfg["candle_body_min_ratio"]
        ) if direction != NEUTRAL else False

        all_pass = all(filters.values())

        result = {
            "direction": direction if all_pass else NEUTRAL,
            "filters": filters,
            "all_pass": all_pass,
            **{k: v for k, v in data.items() if k not in ("ema_fast_series", "ema_medium_series", "ema_slow_series", "df")},
        }

        if all_pass and direction != NEUTRAL:
            atr = data["atr"]
            sl_dist = atr * self.cfg["atr_sl_multiplier"]
            price = data["close"]

            if direction == LONG:
                sl = price - sl_dist
                tp1 = price + sl_dist * self.cfg["tp1_multiplier"]
                tp2 = price + sl_dist * self.cfg["tp2_multiplier"]
                tp3 = price + sl_dist * self.cfg["tp3_multiplier"]
            else:
                sl = price + sl_dist
                tp1 = price - sl_dist * self.cfg["tp1_multiplier"]
                tp2 = price - sl_dist * self.cfg["tp2_multiplier"]
                tp3 = price - sl_dist * self.cfg["tp3_multiplier"]

            result["sl"] = sl
            result["tp1"] = tp1
            result["tp2"] = tp2
            result["tp3"] = tp3
            result["sl_distance"] = sl_dist

        return result

    def check_ema_reversal(self, df: pd.DataFrame, trade_direction: str) -> bool:
        """True if EMA8 has crossed EMA13 against the trade direction (exit signal)."""
        ema_fast = calculate_ema(df["close"], self.cfg["ema_fast"])
        ema_medium = calculate_ema(df["close"], self.cfg["ema_medium"])

        ef_now = float(ema_fast.iloc[-1])
        em_now = float(ema_medium.iloc[-1])
        ef_prev = float(ema_fast.iloc[-2])
        em_prev = float(ema_medium.iloc[-2])

        if trade_direction == LONG:
            crossed_down = ef_prev >= em_prev and ef_now < em_now
            return crossed_down
        else:
            crossed_up = ef_prev <= em_prev and ef_now > em_now
            return crossed_up
