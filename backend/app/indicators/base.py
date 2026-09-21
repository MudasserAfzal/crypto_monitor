"""Technical indicator framework — base types and utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.models.schemas import IndicatorSignal, OHLCV, SignalType


def candles_to_df(candles: List[OHLCV]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame([c.model_dump() for c in candles])
    df["open_time"] = pd.to_datetime(df["open_time"])
    df = df.sort_values("open_time").reset_index(drop=True)
    df = df.set_index("open_time")
    df = df[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def safe_last(series: pd.Series, default: float = np.nan) -> float:
    if series is None or len(series) == 0:
        return default
    val = series.iloc[-1]
    if pd.isna(val):
        return default
    return float(val)


def cross_over(a: pd.Series, b: pd.Series) -> bool:
    if len(a) < 2 or len(b) < 2:
        return False
    return bool(a.iloc[-2] <= b.iloc[-2] and a.iloc[-1] > b.iloc[-1])


def cross_under(a: pd.Series, b: pd.Series) -> bool:
    if len(a) < 2 or len(b) < 2:
        return False
    return bool(a.iloc[-2] >= b.iloc[-2] and a.iloc[-1] < b.iloc[-1])


@dataclass
class IndicatorResult:
    name: str
    values: Dict[str, float]
    signal: SignalType
    strength: float
    detail: str = ""

    def to_signal(self, timeframe: str = "1d") -> IndicatorSignal:
        return IndicatorSignal(
            name=self.name,
            signal=self.signal,
            value=next(iter(self.values.values()), None) if self.values else None,
            strength=self.strength,
            timeframe=timeframe,
            detail=self.detail or str(self.values),
        )


class Indicator:
    """Base indicator — subclasses implement ``compute``."""

    name: str = "base"

    def compute(self, df: pd.DataFrame) -> IndicatorResult:
        raise NotImplementedError

    def _hold(self, values: Optional[Dict[str, float]] = None, detail: str = "") -> IndicatorResult:
        return IndicatorResult(self.name, values or {}, SignalType.HOLD, 0.3, detail)

    def _buy(self, strength: float, values: Dict[str, float], detail: str = "") -> IndicatorResult:
        return IndicatorResult(self.name, values, SignalType.BUY, min(1.0, max(0.0, strength)), detail)

    def _sell(self, strength: float, values: Dict[str, float], detail: str = "") -> IndicatorResult:
        return IndicatorResult(self.name, values, SignalType.SELL, min(1.0, max(0.0, strength)), detail)
