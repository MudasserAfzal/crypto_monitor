"""Multi-timeframe technical analysis engine — runs all indicators."""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from app.indicators.base import Indicator, IndicatorResult, candles_to_df
from app.indicators.momentum import MOMENTUM_INDICATORS
from app.indicators.patterns import PATTERN_INDICATORS
from app.indicators.trend import TREND_INDICATORS
from app.indicators.volatility import VOLATILITY_INDICATORS, VOLUME_INDICATORS
from app.core.logging import get_logger
from app.models.schemas import IndicatorSignal, OHLCV, SignalType

logger = get_logger(__name__)

ALL_INDICATORS: List[Indicator] = (
    TREND_INDICATORS
    + MOMENTUM_INDICATORS
    + VOLATILITY_INDICATORS
    + VOLUME_INDICATORS
    + PATTERN_INDICATORS
)


class TechnicalEngine:
    """Compute the full indicator suite across one or more timeframes."""

    def __init__(self, indicators: Optional[List[Indicator]] = None) -> None:
        self.indicators = indicators or ALL_INDICATORS

    def analyze_df(self, df: pd.DataFrame, timeframe: str = "1d") -> List[IndicatorResult]:
        results: List[IndicatorResult] = []
        if df.empty or len(df) < 5:
            return results
        for ind in self.indicators:
            try:
                results.append(ind.compute(df))
            except Exception as exc:
                logger.debug("Indicator %s failed: %s", ind.name, exc)
        return results

    def analyze_candles(self, candles: List[OHLCV], timeframe: str = "1d") -> List[IndicatorSignal]:
        df = candles_to_df(candles)
        return [r.to_signal(timeframe) for r in self.analyze_df(df, timeframe)]

    def analyze_multi_timeframe(
        self,
        candles_by_tf: Dict[str, List[OHLCV]],
    ) -> Dict[str, List[IndicatorSignal]]:
        out: Dict[str, List[IndicatorSignal]] = {}
        for tf, candles in candles_by_tf.items():
            out[tf] = self.analyze_candles(candles, timeframe=tf)
        return out

    def score_signals(self, signals: List[IndicatorSignal]) -> Dict[str, float]:
        """Aggregate indicator votes into a technical score in [-1, 1]."""
        if not signals:
            return {"score": 0.0, "buy_weight": 0.0, "sell_weight": 0.0, "hold_weight": 0.0}

        buy_w = sum(s.strength for s in signals if s.signal == SignalType.BUY)
        sell_w = sum(s.strength for s in signals if s.signal == SignalType.SELL)
        hold_w = sum(s.strength for s in signals if s.signal == SignalType.HOLD)
        total = buy_w + sell_w + hold_w + 1e-9
        # Net score: positive = bullish
        score = (buy_w - sell_w) / total
        return {
            "score": float(score),
            "buy_weight": float(buy_w),
            "sell_weight": float(sell_w),
            "hold_weight": float(hold_w),
            "n_indicators": float(len(signals)),
        }

    def multi_tf_score(self, by_tf: Dict[str, List[IndicatorSignal]]) -> Dict[str, float]:
        # Higher timeframes carry more weight for swing/position signals
        weights = {"1m": 0.03, "5m": 0.05, "15m": 0.08, "1h": 0.12, "4h": 0.20, "1d": 0.32, "1w": 0.20}
        weighted = 0.0
        w_sum = 0.0
        breakdown = {}
        for tf, sigs in by_tf.items():
            s = self.score_signals(sigs)
            w = weights.get(tf, 0.15)
            weighted += s["score"] * w
            w_sum += w
            breakdown[tf] = s["score"]
        return {"score": weighted / (w_sum or 1), "by_timeframe": breakdown}  # type: ignore[dict-item]


engine = TechnicalEngine()
