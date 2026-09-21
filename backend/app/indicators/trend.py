"""Trend-following indicators: SMA, EMA, WMA, HMA, MACD, Ichimoku, PSAR, ADX."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.base import Indicator, IndicatorResult, cross_over, cross_under, safe_last
from app.models.schemas import SignalType


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)

    def _wma(x):
        return np.dot(x, weights) / weights.sum()

    return series.rolling(period).apply(_wma, raw=True)


def hma(series: pd.Series, period: int) -> pd.Series:
    half = max(1, period // 2)
    sqrt_p = max(1, int(np.sqrt(period)))
    return wma(2 * wma(series, half) - wma(series, period), sqrt_p)


class SMAIndicator(Indicator):
    def __init__(self, periods: tuple = (5, 10, 20, 50, 100, 200)) -> None:
        self.periods = periods
        self.name = "SMA"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        values = {}
        for p in self.periods:
            if len(close) >= p:
                values[f"sma_{p}"] = safe_last(sma(close, p))
        if "sma_50" not in values or "sma_200" not in values:
            # Fallback short/long
            short_p = next((p for p in self.periods if f"sma_{p}" in values), None)
            if short_p is None:
                return self._hold(values, "insufficient data")
            price = float(close.iloc[-1])
            if price > values[f"sma_{short_p}"]:
                return self._buy(0.5, values, f"price above SMA{short_p}")
            return self._sell(0.5, values, f"price below SMA{short_p}")

        price = float(close.iloc[-1])
        s50, s200 = values["sma_50"], values["sma_200"]
        golden = cross_over(sma(close, 50), sma(close, 200))
        death = cross_under(sma(close, 50), sma(close, 200))
        if golden or (s50 > s200 and price > s50):
            strength = 0.9 if golden else 0.65
            return self._buy(strength, values, "golden cross / bullish stack")
        if death or (s50 < s200 and price < s50):
            strength = 0.9 if death else 0.65
            return self._sell(strength, values, "death cross / bearish stack")
        return self._hold(values)


class EMAIndicator(Indicator):
    def __init__(self, periods: tuple = (9, 12, 21, 26, 50, 200)) -> None:
        self.periods = periods
        self.name = "EMA"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        values = {}
        for p in self.periods:
            if len(close) >= p:
                values[f"ema_{p}"] = safe_last(ema(close, p))
        if "ema_9" in values and "ema_21" in values:
            e9, e21 = ema(close, 9), ema(close, 21)
            if cross_over(e9, e21):
                return self._buy(0.8, values, "EMA9 crossed above EMA21")
            if cross_under(e9, e21):
                return self._sell(0.8, values, "EMA9 crossed below EMA21")
            if values["ema_9"] > values["ema_21"]:
                return self._buy(0.55, values, "EMA bullish")
            return self._sell(0.55, values, "EMA bearish")
        return self._hold(values)


class WMAIndicator(Indicator):
    name = "WMA"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 20:
            return self._hold({}, "insufficient data")
        w = wma(close, 20)
        values = {"wma_20": safe_last(w)}
        price = float(close.iloc[-1])
        if price > values["wma_20"]:
            return self._buy(0.5, values)
        return self._sell(0.5, values)


class HMAIndicator(Indicator):
    name = "HMA"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 21:
            return self._hold({}, "insufficient data")
        h = hma(close, 21)
        values = {"hma_21": safe_last(h)}
        if len(h.dropna()) < 2:
            return self._hold(values)
        slope = float(h.iloc[-1] - h.iloc[-2])
        if slope > 0 and close.iloc[-1] > h.iloc[-1]:
            return self._buy(0.7, values, "HMA rising")
        if slope < 0 and close.iloc[-1] < h.iloc[-1]:
            return self._sell(0.7, values, "HMA falling")
        return self._hold(values)


class MACDIndicator(Indicator):
    name = "MACD"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 35:
            return self._hold({}, "insufficient data")
        macd_line = ema(close, 12) - ema(close, 26)
        signal = ema(macd_line, 9)
        hist = macd_line - signal
        values = {
            "macd": safe_last(macd_line),
            "signal": safe_last(signal),
            "hist": safe_last(hist),
        }
        if cross_over(macd_line, signal):
            return self._buy(0.85, values, "MACD bullish crossover")
        if cross_under(macd_line, signal):
            return self._sell(0.85, values, "MACD bearish crossover")
        if values["hist"] > 0:
            return self._buy(0.55, values, "MACD hist positive")
        if values["hist"] < 0:
            return self._sell(0.55, values, "MACD hist negative")
        return self._hold(values)


class IchimokuIndicator(Indicator):
    name = "Ichimoku"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 52:
            return self._hold({}, "insufficient data")
        high, low, close = df["high"], df["low"], df["close"]
        tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
        kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
        senkou_a = ((tenkan + kijun) / 2).shift(26)
        senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
        chikou = close.shift(-26)

        values = {
            "tenkan": safe_last(tenkan),
            "kijun": safe_last(kijun),
            "senkou_a": safe_last(senkou_a),
            "senkou_b": safe_last(senkou_b),
        }
        price = float(close.iloc[-1])
        cloud_top = max(values["senkou_a"], values["senkou_b"]) if not np.isnan(values["senkou_a"]) else price
        cloud_bot = min(values["senkou_a"], values["senkou_b"]) if not np.isnan(values["senkou_a"]) else price

        if price > cloud_top and values["tenkan"] > values["kijun"]:
            return self._buy(0.8, values, "price above cloud + TK cross bullish")
        if price < cloud_bot and values["tenkan"] < values["kijun"]:
            return self._sell(0.8, values, "price below cloud + TK cross bearish")
        return self._hold(values, "inside / mixed cloud")


class ParabolicSARIndicator(Indicator):
    name = "ParabolicSAR"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 10:
            return self._hold({}, "insufficient data")
        high, low, close = df["high"].values, df["low"].values, df["close"].values
        af_step, af_max = 0.02, 0.2
        bull = True
        af = af_step
        ep = low[0]
        sar = high[0]
        sars = [sar]
        for i in range(1, len(df)):
            prev_sar = sar
            if bull:
                sar = prev_sar + af * (ep - prev_sar)
                sar = min(sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
                if low[i] < sar:
                    bull = False
                    sar = ep
                    ep = low[i]
                    af = af_step
                else:
                    if high[i] > ep:
                        ep = high[i]
                        af = min(af + af_step, af_max)
            else:
                sar = prev_sar + af * (ep - prev_sar)
                sar = max(sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
                if high[i] > sar:
                    bull = True
                    sar = ep
                    ep = high[i]
                    af = af_step
                else:
                    if low[i] < ep:
                        ep = low[i]
                        af = min(af + af_step, af_max)
            sars.append(sar)
        last_sar = sars[-1]
        price = float(close[-1])
        values = {"sar": float(last_sar), "bullish": float(bull)}
        if price > last_sar:
            return self._buy(0.7, values, "price above SAR")
        return self._sell(0.7, values, "price below SAR")


class ADXIndicator(Indicator):
    name = "ADX"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 30:
            return self._hold({}, "insufficient data")
        high, low, close = df["high"], df["low"], df["close"]
        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        plus_dm[plus_dm < minus_dm] = 0
        minus_dm[minus_dm < plus_dm] = 0
        tr = pd.concat(
            [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(span=14, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr)
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).replace([np.inf, -np.inf], np.nan)
        adx = dx.ewm(span=14, adjust=False).mean()
        values = {
            "adx": safe_last(adx),
            "plus_di": safe_last(plus_di),
            "minus_di": safe_last(minus_di),
        }
        if values["adx"] < 20:
            return self._hold(values, "weak trend")
        if values["plus_di"] > values["minus_di"]:
            return self._buy(min(0.9, values["adx"] / 50), values, "ADX bullish trend")
        return self._sell(min(0.9, values["adx"] / 50), values, "ADX bearish trend")


TREND_INDICATORS = [
    SMAIndicator(),
    EMAIndicator(),
    WMAIndicator(),
    HMAIndicator(),
    MACDIndicator(),
    IchimokuIndicator(),
    ParabolicSARIndicator(),
    ADXIndicator(),
]
