"""Binance market data collector — OHLCV primary source with volume."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.collectors.base import BaseCollector
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import OHLCV, Ticker

logger = get_logger(__name__)

INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}


class BinanceCollector(BaseCollector):
    provider = "binance"
    cache_ttl = 60

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.binance_api_key, base_url=settings.binance_base_url)

    def _pair(self, symbol: str) -> str:
        s = symbol.upper().replace("USDT", "").replace("/", "")
        return f"{s}USDT"

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        pair = self._pair(symbol)
        try:
            data = await self.get(
                "/api/v3/ticker/24hr",
                params={"symbol": pair},
                cache_key=f"bn:ticker:{pair}",
                ttl=30,
            )
            return Ticker(
                symbol=symbol.upper(),
                price=float(data["lastPrice"]),
                volume_24h=float(data.get("quoteVolume") or 0),
                change_24h=float(data.get("priceChangePercent") or 0),
                high_24h=float(data.get("highPrice") or 0),
                low_24h=float(data.get("lowPrice") or 0),
                source="binance",
                raw=data,
            )
        except Exception as exc:
            logger.warning("Binance ticker failed for %s: %s", symbol, exc)
            return None

    async def get_tickers(self, symbols: List[str]) -> List[Ticker]:
        results: List[Ticker] = []
        for sym in symbols:
            t = await self.get_ticker(sym)
            if t:
                results.append(t)
        return results

    async def get_all_usdt_tickers(self) -> List[Ticker]:
        """Fetch every USDT spot pair from Binance 24hr ticker endpoint."""
        try:
            data = await self.get(
                "/api/v3/ticker/24hr",
                cache_key="bn:ticker:all",
                ttl=60,
            )
        except Exception as exc:
            logger.warning("Binance all tickers failed: %s", exc)
            return []
        tickers: List[Ticker] = []
        for item in data or []:
            pair = item.get("symbol") or ""
            if not pair.endswith("USDT"):
                continue
            # Skip leveraged tokens / weird suffixes when obvious
            base = pair[:-4]
            if not base or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue
            try:
                tickers.append(
                    Ticker(
                        symbol=base,
                        price=float(item.get("lastPrice") or 0),
                        volume_24h=float(item.get("quoteVolume") or 0),
                        change_24h=float(item.get("priceChangePercent") or 0),
                        high_24h=float(item.get("highPrice") or 0),
                        low_24h=float(item.get("lowPrice") or 0),
                        source="binance",
                        raw={"pair": pair},
                    )
                )
            except (TypeError, ValueError):
                continue
        logger.info("Binance USDT tickers: %d", len(tickers))
        return tickers

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 500,
    ) -> List[OHLCV]:
        pair = self._pair(symbol)
        interval = INTERVAL_MAP.get(timeframe, "1d")
        data = await self.get(
            "/api/v3/klines",
            params={"symbol": pair, "interval": interval, "limit": min(limit, 1000)},
            cache_key=f"bn:klines:{pair}:{interval}:{limit}",
            ttl=60,
        )
        candles: List[OHLCV] = []
        for row in data or []:
            candles.append(
                OHLCV(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    open_time=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    source="binance",
                )
            )
        return candles

    async def get_order_book(self, symbol: str, limit: int = 20) -> Dict:
        pair = self._pair(symbol)
        return await self.get(
            "/api/v3/depth",
            params={"symbol": pair, "limit": limit},
            cache_key=f"bn:depth:{pair}:{limit}",
            ttl=15,
        )
