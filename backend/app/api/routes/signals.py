"""FastAPI route handlers — live fetch + local JSON retrieval."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.backtesting.engine import backtester
from app.collectors.aggregator import aggregator
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.storage import store
from app.models.schemas import DailyReport, LiveQuote, TradeSignal
from app.signals.live_plan import annotate_quotes, build_live_report, fetch_live_quotes

logger = get_logger(__name__)
router = APIRouter()


def _engine():
    from app.signals.engine import signal_engine

    return signal_engine


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": get_settings().app_name,
        "storage": str(store.root),
        "coins_stored": len(store.load_symbols()),
    }


@router.post("/market/extract")
async def extract_all_coins():
    """Fetch all coins from CoinGecko + Binance and save under data/tickers/."""
    tickers = await aggregator.fetch_and_store_all_coins()
    return {
        "count": len(tickers),
        "path": str(store.path("tickers/all_tickers.json")),
        "symbols_sample": [t.symbol for t in tickers[:20]],
    }


@router.get("/market/stored")
async def stored_tickers(
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """Retrieve previously saved coin tickers from local disk."""
    tickers = store.load_tickers()
    return {
        "count": len(tickers),
        "offset": offset,
        "limit": limit,
        "tickers": tickers[offset : offset + limit],
    }


@router.get("/market/symbols")
async def stored_symbols():
    symbols = store.load_symbols()
    return {"count": len(symbols), "symbols": symbols}


@router.get("/market/overview")
async def market_overview(refresh: bool = False):
    if not refresh:
        cached = store.read_json("overview/latest.json")
        if cached:
            return cached
    return await aggregator.get_market_overview()


@router.get("/market/tickers")
async def market_tickers(
    symbols: Optional[str] = Query(None, description="Comma-separated symbols"),
    refresh: bool = False,
):
    if not refresh and not symbols:
        stored = store.load_tickers()
        if stored:
            return stored
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    if syms is None:
        tickers = await aggregator.fetch_and_store_all_coins()
    else:
        tickers = await aggregator.get_tickers(syms)
    return [t.model_dump(mode="json") for t in tickers]


@router.get("/market/ohlcv/{symbol}")
async def market_ohlcv(symbol: str, timeframe: str = "1d", limit: int = 200, refresh: bool = False):
    if not refresh:
        cached = store.load_ohlcv(symbol.upper(), timeframe)
        if cached:
            return cached[-limit:]
    candles = await aggregator.get_ohlcv(symbol.upper(), timeframe=timeframe, limit=limit)
    return [c.model_dump(mode="json") for c in candles]


@router.get("/sentiment")
async def sentiment(symbols: Optional[str] = None, refresh: bool = False):
    if not refresh:
        cached = store.read_json("sentiment/latest.json")
        if cached:
            return cached.get("items") or cached
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    data = await aggregator.get_sentiment(syms)
    return [s.model_dump(mode="json") for s in data]


@router.get("/signals/stored")
async def stored_signals():
    data = store.read_json("signals/all_signals.json")
    if not data:
        raise HTTPException(status_code=404, detail="No stored signals — run /signals/report first")
    return data


@router.get("/market/live", response_model=List[LiveQuote])
async def market_live():
    """Cheap live EUR prices + 24h change, annotated with buy/sell vs last plan."""
    try:
        quotes = await fetch_live_quotes()
    except Exception as exc:
        logger.warning("live quotes unavailable: %s", exc)
        return []
    cached = store.load_report()
    report = DailyReport.model_validate(cached) if cached else None
    return annotate_quotes(quotes, report)


@router.get("/signals/analyze/{symbol}", response_model=TradeSignal)
async def analyze_symbol(symbol: str):
    try:
        return await _engine().analyze_symbol(symbol.upper())
    except Exception as exc:
        logger.exception("analyze failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/signals/run", response_model=DailyReport)
async def run_signals(
    symbols: Optional[List[str]] = None,
    signal_limit: Optional[int] = None,
    budget_eur: float = 100.0,
):
    """Extract all coins, then generate signals for top N (or provided symbols)."""
    try:
        return await _engine().run_universe(
            symbols, signal_limit=signal_limit, budget_eur=budget_eur
        )
    except Exception as exc:
        logger.exception("signal run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/signals/report", response_model=DailyReport)
async def signals_report(
    refresh: bool = Query(False, description="Force re-run; otherwise return latest local report"),
    signal_limit: Optional[int] = None,
    budget_eur: float = Query(100.0, ge=25, le=10000),
    fast: bool = Query(True, description="Live news+price plan (skip full ML universe)"),
):
    if not refresh:
        cached = store.load_report()
        if cached:
            return DailyReport.model_validate(cached)
    if fast:
        try:
            return await build_live_report(budget_eur)
        except Exception as exc:
            logger.exception("live plan failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return await _engine().run_universe(signal_limit=signal_limit, budget_eur=budget_eur)


@router.post("/backtest/{symbol}")
async def backtest_symbol(symbol: str, timeframe: str = "1d", hold_bars: int = 5):
    candles = await aggregator.get_ohlcv(symbol.upper(), timeframe=timeframe, limit=500)
    if len(candles) < 80:
        raise HTTPException(status_code=400, detail="Insufficient historical data")
    result = backtester.run(candles, symbol.upper(), timeframe=timeframe, hold_bars=hold_bars)
    payload = {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "profit_factor": result.profit_factor,
        "equity_curve": result.equity_curve[-100:],
        "recent_trades": [
            {
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl_pct": t.pnl_pct,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
            }
            for t in result.trades[-20:]
        ],
    }
    store.write_json(f"reports/backtest_{symbol.upper()}_{timeframe}.json", payload)
    return payload
