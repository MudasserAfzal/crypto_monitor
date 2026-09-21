"""Unit tests for indicators and signal scoring."""

import numpy as np
import pandas as pd
import pytest

from app.indicators.engine import TechnicalEngine
from app.indicators.momentum import RSIIndicator, StochasticIndicator
from app.indicators.trend import MACDIndicator, SMAIndicator, ema, sma
from app.indicators.volatility import BollingerBandsIndicator, ATRIndicator
from app.models.schemas import SignalType


def _ohlcv_df(n: int = 250, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.001, 0.02, n)
    close = 100 * np.cumprod(1 + rets)
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = rng.uniform(1e6, 5e6, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_sma_ema_helpers():
    s = pd.Series(range(1, 21), dtype=float)
    assert abs(sma(s, 5).iloc[-1] - 18.0) < 1e-9
    assert not np.isnan(ema(s, 5).iloc[-1])


def test_rsi_bounds():
    df = _ohlcv_df()
    result = RSIIndicator().compute(df)
    assert result.name == "RSI"
    assert "rsi_14" in result.values
    assert 0 <= result.values["rsi_14"] <= 100
    assert result.signal in (SignalType.BUY, SignalType.SELL, SignalType.HOLD)


def test_macd_runs():
    df = _ohlcv_df()
    result = MACDIndicator().compute(df)
    assert "macd" in result.values
    assert "hist" in result.values


def test_bollinger_and_atr():
    df = _ohlcv_df()
    bb = BollingerBandsIndicator().compute(df)
    atr = ATRIndicator().compute(df)
    assert bb.values["upper"] > bb.values["lower"]
    assert atr.values["atr"] > 0


def test_stochastic():
    df = _ohlcv_df()
    result = StochasticIndicator().compute(df)
    assert 0 <= result.values["k"] <= 100


def test_full_engine():
    df = _ohlcv_df()
    engine = TechnicalEngine()
    results = engine.analyze_df(df, "1d")
    assert len(results) >= 20  # all major indicators
    score = engine.score_signals([r.to_signal() for r in results])
    assert -1.0 <= score["score"] <= 1.0


def test_sma_indicator_signal():
    df = _ohlcv_df()
    result = SMAIndicator().compute(df)
    assert result.signal in (SignalType.BUY, SignalType.SELL, SignalType.HOLD)
