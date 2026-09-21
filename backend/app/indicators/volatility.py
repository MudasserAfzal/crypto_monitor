"""Volatility & volume indicators: BB, ATR, Keltner, Donchian, CMF, A/D, VPT, VWAP."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.base import Indicator, IndicatorResult, safe_last
from app.indicators.trend import ema, sma


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)


class BollingerBandsIndicator(Indicator):
    name = "BollingerBands"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 20:
            return self._hold({}, "insufficient data")
        mid = sma(close, 20)
        std = close.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        # Also compute 1-std and 3-std bands for multi-deviation analysis
        upper1, lower1 = mid + std, mid - std
        upper3, lower3 = mid + 3 * std, mid - 3 * std
        price = float(close.iloc[-1])
        values = {
            "mid": safe_last(mid),
            "upper": safe_last(upper),
            "lower": safe_last(lower),
            "upper_1std": safe_last(upper1),
            "lower_1std": safe_last(lower1),
            "upper_3std": safe_last(upper3),
            "lower_3std": safe_last(lower3),
            "bandwidth": safe_last((upper - lower) / mid),
        }
        if price <= values["lower"]:
            return self._buy(0.8, values, "touch lower band")
        if price >= values["upper"]:
            return self._sell(0.8, values, "touch upper band")
        pct_b = (price - values["lower"]) / (values["upper"] - values["lower"] + 1e-12)
        if pct_b < 0.3:
            return self._buy(0.45, values)
        if pct_b > 0.7:
            return self._sell(0.45, values)
        return self._hold(values)


class ATRIndicator(Indicator):
    name = "ATR"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 15:
            return self._hold({}, "insufficient data")
        atr = true_range(df).ewm(span=14, adjust=False).mean()
        atr_val = safe_last(atr)
        price = float(df["close"].iloc[-1])
        atr_pct = atr_val / price * 100 if price else 0
        values = {"atr": atr_val, "atr_pct": atr_pct}
        # ATR itself is not directional — HOLD with volatility context
        return self._hold(values, f"ATR {atr_pct:.2f}% of price")


class KeltnerChannelsIndicator(Indicator):
    name = "KeltnerChannels"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20:
            return self._hold({}, "insufficient data")
        mid = ema(df["close"], 20)
        atr = true_range(df).ewm(span=20, adjust=False).mean()
        upper = mid + 2 * atr
        lower = mid - 2 * atr
        price = float(df["close"].iloc[-1])
        values = {"mid": safe_last(mid), "upper": safe_last(upper), "lower": safe_last(lower)}
        if price > values["upper"]:
            return self._buy(0.65, values, "Keltner breakout up")
        if price < values["lower"]:
            return self._sell(0.65, values, "Keltner breakout down")
        return self._hold(values)


class DonchianChannelsIndicator(Indicator):
    name = "DonchianChannels"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20:
            return self._hold({}, "insufficient data")
        upper = df["high"].rolling(20).max()
        lower = df["low"].rolling(20).min()
        mid = (upper + lower) / 2
        price = float(df["close"].iloc[-1])
        values = {"upper": safe_last(upper), "lower": safe_last(lower), "mid": safe_last(mid)}
        if price >= values["upper"] * 0.998:
            return self._buy(0.7, values, "Donchian high breakout")
        if price <= values["lower"] * 1.002:
            return self._sell(0.7, values, "Donchian low breakout")
        return self._hold(values)


class StdDevIndicator(Indicator):
    name = "StdDev"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        close = df["close"]
        if len(close) < 20:
            return self._hold({}, "insufficient data")
        std = close.rolling(20).std()
        mean = close.rolling(20).mean()
        z = (close - mean) / std.replace(0, np.nan)
        values = {"std": safe_last(std), "zscore": safe_last(z)}
        if values["zscore"] < -2:
            return self._buy(0.7, values, "mean reversion buy")
        if values["zscore"] > 2:
            return self._sell(0.7, values, "mean reversion sell")
        return self._hold(values)


class VolumeMAIndicator(Indicator):
    name = "VolumeMA"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20 or df["volume"].sum() == 0:
            return self._hold({}, "no volume")
        vma = sma(df["volume"], 20)
        ratio = float(df["volume"].iloc[-1] / (vma.iloc[-1] + 1e-12))
        price_up = df["close"].iloc[-1] > df["close"].iloc[-2]
        values = {"vol_ma": safe_last(vma), "vol_ratio": ratio}
        if ratio > 1.5 and price_up:
            return self._buy(0.65, values, "volume surge on green candle")
        if ratio > 1.5 and not price_up:
            return self._sell(0.65, values, "volume surge on red candle")
        return self._hold(values)


class CMFIndicator(Indicator):
    name = "CMF"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20 or df["volume"].sum() == 0:
            return self._hold({}, "no volume")
        mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"]).replace(0, np.nan)
        mfv = mfm * df["volume"]
        cmf = mfv.rolling(20).sum() / df["volume"].rolling(20).sum().replace(0, np.nan)
        val = safe_last(cmf)
        values = {"cmf": val}
        if val > 0.1:
            return self._buy(0.6, values)
        if val < -0.1:
            return self._sell(0.6, values)
        return self._hold(values)


class ADLineIndicator(Indicator):
    name = "ADLine"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20 or df["volume"].sum() == 0:
            return self._hold({}, "no volume")
        clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"]).replace(0, np.nan)
        ad = (clv.fillna(0) * df["volume"]).cumsum()
        ad_ema = ema(ad, 20)
        values = {"ad": safe_last(ad), "ad_ema": safe_last(ad_ema)}
        if values["ad"] > values["ad_ema"]:
            return self._buy(0.55, values)
        return self._sell(0.55, values)


class VPTIndicator(Indicator):
    name = "VPT"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 20 or df["volume"].sum() == 0:
            return self._hold({}, "no volume")
        vpt = (df["close"].pct_change().fillna(0) * df["volume"]).cumsum()
        vpt_ema = ema(vpt, 20)
        values = {"vpt": safe_last(vpt), "vpt_ema": safe_last(vpt_ema)}
        if values["vpt"] > values["vpt_ema"]:
            return self._buy(0.55, values)
        return self._sell(0.55, values)


class VWAPIndicator(Indicator):
    name = "VWAP"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        if len(df) < 5 or df["volume"].sum() == 0:
            return self._hold({}, "no volume")
        tp = (df["high"] + df["low"] + df["close"]) / 3
        # Rolling session-like VWAP over last 24 bars as approximation
        window = min(24, len(df))
        recent = df.tail(window)
        tp_r = (recent["high"] + recent["low"] + recent["close"]) / 3
        vwap = float((tp_r * recent["volume"]).sum() / (recent["volume"].sum() + 1e-12))
        price = float(df["close"].iloc[-1])
        values = {"vwap": vwap, "price": price}
        if price > vwap:
            return self._buy(0.5, values, "price above VWAP")
        return self._sell(0.5, values, "price below VWAP")


VOLATILITY_INDICATORS = [
    BollingerBandsIndicator(),
    ATRIndicator(),
    KeltnerChannelsIndicator(),
    DonchianChannelsIndicator(),
    StdDevIndicator(),
]

VOLUME_INDICATORS = [
    VolumeMAIndicator(),
    CMFIndicator(),
    ADLineIndicator(),
    VPTIndicator(),
    VWAPIndicator(),
]
