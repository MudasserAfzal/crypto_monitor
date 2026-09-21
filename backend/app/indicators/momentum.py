"""Momentum oscillators: RSI, Stochastic, CCI, MFI, OBV, ROC, Williams %R, UO, Trix."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.base import Indicator, IndicatorResult, safe_last
from app.indicators.trend import ema


class RSIIndicator(Indicator):
    def __init__(self, periods: tuple = (7, 14, 21)) -> None:
        self.periods = periods
        self.name = "RSI"

    def _rsi(self, close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        values = {}
        for p in self.periods:
            if len(close) >= p + 1:
                values[f"rsi_{p}"] = safe_last(self._rsi(close, p))
        rsi = values.get("rsi_14", values.get("rsi_7"))
        if rsi is None:
            return self._hold(values, "insufficient data")
        if rsi < 30:
            return self._buy(min(1.0, (30 - rsi) / 30 + 0.5), values, "oversold")
        if rsi > 70:
            return self._sell(min(1.0, (rsi - 70) / 30 + 0.5), values, "overbought")
        if rsi < 45:
            return self._buy(0.4, values, "mildly bullish RSI")
        if rsi > 55:
            return self._sell(0.4, values, "mildly bearish RSI")
        return self._hold(values)


class StochasticIndicator(Indicator):
    name = "Stochastic"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20:
            return self._hold({}, "insufficient data")
        low_n = df["low"].rolling(14).min()
        high_n = df["high"].rolling(14).max()
        k = 100 * (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan)
        d = k.rolling(3).mean()
        # Slow stochastic
        slow_k = d
        slow_d = slow_k.rolling(3).mean()
        values = {
            "k": safe_last(k),
            "d": safe_last(d),
            "slow_k": safe_last(slow_k),
            "slow_d": safe_last(slow_d),
        }
        if values["k"] < 20 and values["k"] > values["d"]:
            return self._buy(0.75, values, "stoch oversold turn up")
        if values["k"] > 80 and values["k"] < values["d"]:
            return self._sell(0.75, values, "stoch overbought turn down")
        if values["k"] < 30:
            return self._buy(0.5, values)
        if values["k"] > 70:
            return self._sell(0.5, values)
        return self._hold(values)


class CCIIndicator(Indicator):
    name = "CCI"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20:
            return self._hold({}, "insufficient data")
        tp = (df["high"] + df["low"] + df["close"]) / 3
        sma_tp = tp.rolling(20).mean()
        mad = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        cci = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))
        val = safe_last(cci)
        values = {"cci": val}
        if val < -100:
            return self._buy(0.7, values, "CCI oversold")
        if val > 100:
            return self._sell(0.7, values, "CCI overbought")
        return self._hold(values)


class MFIIndicator(Indicator):
    name = "MFI"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20 or df["volume"].sum() == 0:
            return self._hold({}, "insufficient data")
        tp = (df["high"] + df["low"] + df["close"]) / 3
        rmf = tp * df["volume"]
        delta = tp.diff()
        pos = rmf.where(delta > 0, 0.0).rolling(14).sum()
        neg = rmf.where(delta < 0, 0.0).rolling(14).sum()
        mfi = 100 - (100 / (1 + pos / neg.replace(0, np.nan)))
        val = safe_last(mfi)
        values = {"mfi": val}
        if val < 20:
            return self._buy(0.75, values, "MFI oversold")
        if val > 80:
            return self._sell(0.75, values, "MFI overbought")
        return self._hold(values)


class OBVIndicator(Indicator):
    name = "OBV"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20 or df["volume"].sum() == 0:
            return self._hold({}, "no volume")
        direction = np.sign(df["close"].diff()).fillna(0)
        obv = (direction * df["volume"]).cumsum()
        obv_ema = ema(obv, 20)
        obv_val = safe_last(obv)
        ema_val = safe_last(obv_ema)
        values = {"obv": obv_val, "obv_ema": ema_val}
        # Divergence from EMA must be meaningful — hold when signal is weak
        diff_pct = abs(obv_val - ema_val) / (abs(ema_val) + 1e-9)
        if diff_pct < 0.02:
            return self._hold(values, "OBV near EMA — weak signal")
        strength = min(0.85, diff_pct * 5)
        if obv_val > ema_val:
            return self._buy(strength, values, "OBV above EMA")
        return self._sell(strength, values, "OBV below EMA")


class ROCIndicator(Indicator):
    name = "ROC"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 14:
            return self._hold({}, "insufficient data")
        roc = close.pct_change(12) * 100
        val = safe_last(roc)
        values = {"roc": val}
        if val > 5:
            return self._buy(min(0.9, abs(val) / 20), values, "strong momentum up")
        if val < -5:
            return self._sell(min(0.9, abs(val) / 20), values, "strong momentum down")
        return self._hold(values)


class WilliamsRIndicator(Indicator):
    name = "WilliamsR"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 14:
            return self._hold({}, "insufficient data")
        highest = df["high"].rolling(14).max()
        lowest = df["low"].rolling(14).min()
        wr = -100 * (highest - df["close"]) / (highest - lowest).replace(0, np.nan)
        val = safe_last(wr)
        values = {"williams_r": val}
        if val < -80:
            return self._buy(0.7, values, "Williams %R oversold")
        if val > -20:
            return self._sell(0.7, values, "Williams %R overbought")
        return self._hold(values)


class UltimateOscillator(Indicator):
    name = "UltimateOscillator"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 30:
            return self._hold({}, "insufficient data")
        prev_close = df["close"].shift(1)
        bp = df["close"] - pd.concat([df["low"], prev_close], axis=1).min(axis=1)
        tr = pd.concat([df["high"], prev_close], axis=1).max(axis=1) - pd.concat(
            [df["low"], prev_close], axis=1
        ).min(axis=1)

        def avg(period):
            return bp.rolling(period).sum() / tr.rolling(period).sum().replace(0, np.nan)

        uo = 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7
        val = safe_last(uo)
        values = {"uo": val}
        if val < 30:
            return self._buy(0.7, values)
        if val > 70:
            return self._sell(0.7, values)
        return self._hold(values)


class TrixIndicator(Indicator):
    name = "Trix"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 45:
            return self._hold({}, "insufficient data")
        e1 = ema(close, 15)
        e2 = ema(e1, 15)
        e3 = ema(e2, 15)
        trix = e3.pct_change() * 100
        signal = ema(trix, 9)
        values = {"trix": safe_last(trix), "signal": safe_last(signal)}
        if values["trix"] > values["signal"]:
            return self._buy(0.6, values)
        return self._sell(0.6, values)


MOMENTUM_INDICATORS = [
    RSIIndicator(),
    StochasticIndicator(),
    CCIIndicator(),
    MFIIndicator(),
    OBVIndicator(),
    ROCIndicator(),
    WilliamsRIndicator(),
    UltimateOscillator(),
    TrixIndicator(),
]
