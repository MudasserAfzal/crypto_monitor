"""Backtesting engine with Sharpe, drawdown, win rate metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.indicators.base import candles_to_df
from app.indicators.engine import TechnicalEngine
from app.models.schemas import OHLCV, SignalType

logger = get_logger(__name__)


@dataclass
class Trade:
    symbol: str
    side: str  # long/short
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    total_return_pct: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0


# Bars per year by timeframe, used for Sharpe annualization
_BARS_PER_YEAR: Dict[str, float] = {
    "1m": 252 * 390, "5m": 252 * 78, "15m": 252 * 26,
    "1h": 252 * 24, "4h": 252 * 6, "1d": 252.0, "1w": 52.0,
}


class Backtester:
    def __init__(self, initial_capital: float = 10_000.0, fee_bps: float = 10.0) -> None:
        self.initial_capital = initial_capital
        self.fee = fee_bps / 10_000
        self.ta = TechnicalEngine()

    def run(
        self,
        candles: List[OHLCV],
        symbol: str,
        timeframe: str = "1d",
        lookback: int = 60,
        hold_bars: int = 5,
        min_confidence_proxy: float = 0.2,
        sl_atr_mult: float = 1.5,
        tp_atr_mult: float = 2.5,
    ) -> BacktestResult:
        df = candles_to_df(candles)
        if len(df) < lookback + hold_bars + 10:
            return BacktestResult(symbol=symbol, timeframe=timeframe)

        trades: List[Trade] = []
        equity = self.initial_capital
        curve = [equity]
        i = lookback
        while i < len(df) - hold_bars:
            window = df.iloc[: i + 1]
            results = self.ta.analyze_df(window, timeframe)
            buy_w = sum(r.strength for r in results if r.signal == SignalType.BUY)
            sell_w = sum(r.strength for r in results if r.signal == SignalType.SELL)
            total = buy_w + sell_w + 1e-9
            score = (buy_w - sell_w) / total

            side = None
            if score >= min_confidence_proxy:
                side = "long"
            elif score <= -min_confidence_proxy:
                side = "short"

            if side:
                entry_price = float(df["close"].iloc[i])
                entry_time = df.index[i].to_pydatetime()

                # ATR-based stop-loss and take-profit
                atr_window = df["high"].iloc[max(0, i - 14):i] - df["low"].iloc[max(0, i - 14):i]
                atr = float(atr_window.mean()) if len(atr_window) else entry_price * 0.02
                if side == "long":
                    stop_price = entry_price - sl_atr_mult * atr
                    tp_price = entry_price + tp_atr_mult * atr
                else:
                    stop_price = entry_price + sl_atr_mult * atr
                    tp_price = entry_price - tp_atr_mult * atr

                # Walk forward bar-by-bar to check stop/TP hit
                exit_price = float(df["close"].iloc[min(i + hold_bars, len(df) - 1)])
                exit_time = df.index[min(i + hold_bars, len(df) - 1)].to_pydatetime()
                for j in range(i + 1, min(i + hold_bars + 1, len(df))):
                    bar_low = float(df["low"].iloc[j])
                    bar_high = float(df["high"].iloc[j])
                    if side == "long":
                        if bar_low <= stop_price:
                            exit_price = stop_price
                            exit_time = df.index[j].to_pydatetime()
                            break
                        if bar_high >= tp_price:
                            exit_price = tp_price
                            exit_time = df.index[j].to_pydatetime()
                            break
                    else:
                        if bar_high >= stop_price:
                            exit_price = stop_price
                            exit_time = df.index[j].to_pydatetime()
                            break
                        if bar_low <= tp_price:
                            exit_price = tp_price
                            exit_time = df.index[j].to_pydatetime()
                            break

                raw = (exit_price / entry_price - 1) if side == "long" else (entry_price / exit_price - 1)
                pnl = raw - 2 * self.fee
                equity *= 1 + pnl
                trades.append(
                    Trade(
                        symbol=symbol,
                        side=side,
                        entry_time=entry_time,
                        entry_price=entry_price,
                        exit_time=exit_time,
                        exit_price=exit_price,
                        pnl_pct=pnl * 100,
                    )
                )
                curve.append(equity)
                i += hold_bars
            else:
                i += 1
                curve.append(equity)

        return self._metrics(symbol, timeframe, trades, curve, hold_bars)

    def _metrics(
        self,
        symbol: str,
        timeframe: str,
        trades: List[Trade],
        curve: List[float],
        hold_bars: int = 5,
    ) -> BacktestResult:
        pnls = [t.pnl_pct / 100 for t in trades if t.pnl_pct is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls) if pnls else 0.0
        total_return = (curve[-1] / self.initial_capital - 1) * 100 if curve else 0.0

        # Sharpe annualized by actual timeframe + hold period
        if len(pnls) > 1:
            bars_per_year = _BARS_PER_YEAR.get(timeframe, 252.0)
            annualization = np.sqrt(bars_per_year / max(1, hold_bars))
            sharpe = float(np.mean(pnls) / (np.std(pnls) + 1e-12) * annualization)
        else:
            sharpe = 0.0

        # Max drawdown
        peak = curve[0]
        max_dd = 0.0
        for v in curve:
            peak = max(peak, v)
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 1e-12
        profit_factor = gross_profit / gross_loss

        return BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            trades=trades,
            equity_curve=curve,
            total_return_pct=total_return,
            win_rate=win_rate,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd * 100,
            total_trades=len(trades),
            profit_factor=profit_factor,
        )

    def run_multi(self, candle_map: Dict[str, List[OHLCV]], timeframe: str = "1d") -> Dict[str, BacktestResult]:
        return {sym: self.run(candles, sym, timeframe) for sym, candles in candle_map.items()}


backtester = Backtester()
