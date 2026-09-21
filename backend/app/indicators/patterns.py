"""Support/resistance, patterns, Fibonacci, pivots, Heikin-Ashi helpers."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.indicators.base import Indicator, IndicatorResult, safe_last
from app.models.schemas import SignalType


FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786)


class FibonacciIndicator(Indicator):
    name = "Fibonacci"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 50:
            return self._hold({}, "insufficient data")
        window = df.tail(60)
        swing_high = float(window["high"].max())
        swing_low = float(window["low"].min())
        diff = swing_high - swing_low
        levels = {f"fib_{lvl}": swing_high - diff * lvl for lvl in FIB_LEVELS}
        price = float(df["close"].iloc[-1])
        values = {"swing_high": swing_high, "swing_low": swing_low, **levels}
        # Near support (higher fib from low) → buy; near resistance → sell
        nearest = min(levels.items(), key=lambda kv: abs(kv[1] - price))
        dist_pct = abs(nearest[1] - price) / price * 100
        if dist_pct < 1.5:
            if price <= levels["fib_0.618"] + diff * 0.05:
                return self._buy(0.65, values, f"near {nearest[0]}")
            return self._sell(0.65, values, f"near {nearest[0]}")
        if price > levels["fib_0.382"]:
            return self._buy(0.4, values, "above 38.2% retracement")
        return self._sell(0.4, values, "below 38.2% retracement")


class PivotPointsIndicator(Indicator):
    name = "PivotPoints"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 2:
            return self._hold({}, "insufficient data")
        prev = df.iloc[-2]
        h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
        # Standard
        pp = (h + l + c) / 3
        r1, s1 = 2 * pp - l, 2 * pp - h
        r2, s2 = pp + (h - l), pp - (h - l)
        # Fibonacci pivots
        fib_r1, fib_s1 = pp + 0.382 * (h - l), pp - 0.382 * (h - l)
        # Woodie
        woodie_pp = (h + l + 2 * c) / 4
        # Camarilla
        cam_r1 = c + (h - l) * 1.1 / 12
        cam_s1 = c - (h - l) * 1.1 / 12
        # Demark
        if c < float(prev.get("open", c)):
            x = h + 2 * l + c
        elif c > float(prev.get("open", c)):
            x = 2 * h + l + c
        else:
            x = h + l + 2 * c
        demark_pp = x / 4

        price = float(df["close"].iloc[-1])
        values = {
            "pp": pp, "r1": r1, "s1": s1, "r2": r2, "s2": s2,
            "fib_r1": fib_r1, "fib_s1": fib_s1,
            "woodie_pp": woodie_pp,
            "cam_r1": cam_r1, "cam_s1": cam_s1,
            "demark_pp": demark_pp,
        }
        if price <= s1:
            return self._buy(0.7, values, "at/below S1")
        if price >= r1:
            return self._sell(0.7, values, "at/above R1")
        if price > pp:
            return self._buy(0.45, values, "above pivot")
        return self._sell(0.45, values, "below pivot")


class MAEnvelopeIndicator(Indicator):
    name = "MAEnvelope"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 20:
            return self._hold({}, "insufficient data")
        mid = close.rolling(20).mean()
        upper = mid * 1.025
        lower = mid * 0.975
        price = float(close.iloc[-1])
        values = {"mid": safe_last(mid), "upper": safe_last(upper), "lower": safe_last(lower)}
        if price <= values["lower"]:
            return self._buy(0.65, values)
        if price >= values["upper"]:
            return self._sell(0.65, values)
        return self._hold(values)


class TrendlineIndicator(Indicator):
    """Automatic trendline via linear regression on recent swing points."""

    name = "Trendline"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 30:
            return self._hold({}, "insufficient data")
        closes = df["close"].tail(40).values
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, closes, 1)
        projected = slope * (len(closes) - 1) + intercept
        price = float(closes[-1])
        values = {"slope": float(slope), "trendline": float(projected)}
        if slope > 0 and price >= projected * 0.99:
            return self._buy(0.6, values, "uptrend intact")
        if slope < 0 and price <= projected * 1.01:
            return self._sell(0.6, values, "downtrend intact")
        return self._hold(values)


class ChartPatternIndicator(Indicator):
    """Heuristic detection for H&S, triangles, flags, wedges, cup & handle."""

    name = "ChartPatterns"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 60:
            return self._hold({}, "insufficient data")
        closes = df["close"].tail(60).values
        highs = df["high"].tail(60).values
        lows = df["low"].tail(60).values
        patterns: List[str] = []
        signal = SignalType.HOLD
        strength = 0.4

        # Simple H&S: three peaks, middle highest
        peaks = self._find_peaks(highs, order=3)
        if len(peaks) >= 3:
            p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
            if highs[p2] > highs[p1] and highs[p2] > highs[p3] and abs(highs[p1] - highs[p3]) / highs[p2] < 0.03:
                patterns.append("head_and_shoulders")
                signal = SignalType.SELL
                strength = 0.75

        # Inverse H&S
        troughs = self._find_peaks(-lows, order=3)
        if len(troughs) >= 3:
            t1, t2, t3 = troughs[-3], troughs[-2], troughs[-1]
            if lows[t2] < lows[t1] and lows[t2] < lows[t3] and abs(lows[t1] - lows[t3]) / abs(lows[t2]) < 0.03:
                patterns.append("inverse_head_and_shoulders")
                signal = SignalType.BUY
                strength = 0.75

        # Triangle: contracting highs and lows
        recent_h = highs[-20:]
        recent_l = lows[-20:]
        h_slope = np.polyfit(np.arange(20), recent_h, 1)[0]
        l_slope = np.polyfit(np.arange(20), recent_l, 1)[0]
        if h_slope < 0 and l_slope > 0:
            patterns.append("triangle_symmetrical")
            # Breakout direction by last close vs mid
            mid = (recent_h[-1] + recent_l[-1]) / 2
            if closes[-1] > mid:
                signal, strength = SignalType.BUY, 0.55
            else:
                signal, strength = SignalType.SELL, 0.55

        # Flag: sharp move then consolidation
        ret_10 = (closes[-1] / closes[-11] - 1) if closes[-11] else 0
        vol_consol = np.std(closes[-8:]) / (np.mean(closes[-8:]) + 1e-12)
        if abs(ret_10) > 0.08 and vol_consol < 0.02:
            patterns.append("flag")
            signal = SignalType.BUY if ret_10 > 0 else SignalType.SELL
            strength = 0.6

        # Wedge
        if h_slope < 0 and l_slope < 0 and abs(h_slope) > abs(l_slope):
            patterns.append("falling_wedge")
            signal, strength = SignalType.BUY, 0.6
        elif h_slope > 0 and l_slope > 0 and abs(l_slope) > abs(h_slope):
            patterns.append("rising_wedge")
            signal, strength = SignalType.SELL, 0.6

        # Cup & handle: U-shape then small dip
        if len(closes) >= 40:
            left = closes[:15].mean()
            bottom = closes[15:30].min()
            right = closes[30:40].mean()
            handle = closes[40:].min() if len(closes) > 40 else right
            if bottom < left * 0.92 and abs(right - left) / left < 0.05 and handle < right * 0.98:
                patterns.append("cup_and_handle")
                signal, strength = SignalType.BUY, 0.7

        values = {"patterns": float(len(patterns))}
        detail = ",".join(patterns) if patterns else "none"
        if signal == SignalType.BUY:
            return self._buy(strength, values, detail)
        if signal == SignalType.SELL:
            return self._sell(strength, values, detail)
        return self._hold(values, detail)

    @staticmethod
    def _find_peaks(arr: np.ndarray, order: int = 3) -> List[int]:
        peaks = []
        for i in range(order, len(arr) - order):
            if arr[i] == max(arr[i - order : i + order + 1]):
                peaks.append(i)
        return peaks


class HeikinAshiIndicator(Indicator):
    name = "HeikinAshi"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 5:
            return self._hold({}, "insufficient data")
        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
        ha_open = pd.Series(index=df.index, dtype=float)
        ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
        for i in range(1, len(df)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
        bullish = int(ha_close.iloc[-1] > ha_open.iloc[-1])
        streak = 0
        for i in range(len(df) - 1, -1, -1):
            if (ha_close.iloc[i] > ha_open.iloc[i]) == bool(bullish):
                streak += 1
            else:
                break
        values = {"ha_bullish": float(bullish), "streak": float(streak)}
        if bullish and streak >= 2:
            return self._buy(min(0.9, 0.4 + streak * 0.1), values, f"{streak} HA green candles")
        if not bullish and streak >= 2:
            return self._sell(min(0.9, 0.4 + streak * 0.1), values, f"{streak} HA red candles")
        return self._hold(values)


class RenkoHintIndicator(Indicator):
    """Simplified Renko brick direction from ATR-sized bricks."""

    name = "Renko"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20:
            return self._hold({}, "insufficient data")
        atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
        if atr <= 0:
            return self._hold({})
        closes = df["close"].values
        brick = float(atr)
        direction = 0
        last_brick = closes[0]
        for price in closes[1:]:
            while price >= last_brick + brick:
                last_brick += brick
                direction = 1
            while price <= last_brick - brick:
                last_brick -= brick
                direction = -1
        values = {"direction": float(direction), "brick_size": brick}
        if direction > 0:
            return self._buy(0.55, values)
        if direction < 0:
            return self._sell(0.55, values)
        return self._hold(values)


PATTERN_INDICATORS = [
    FibonacciIndicator(),
    PivotPointsIndicator(),
    MAEnvelopeIndicator(),
    TrendlineIndicator(),
    ChartPatternIndicator(),
    HeikinAshiIndicator(),
    RenkoHintIndicator(),
]
