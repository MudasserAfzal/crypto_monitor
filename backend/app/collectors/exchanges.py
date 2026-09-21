"""Kraken, KuCoin, CoinMarketCap collectors — redundancy / fallback sources."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.collectors.base import BaseCollector
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import OHLCV, Ticker

logger = get_logger(__name__)

KRAKEN_PAIRS = {
    "BTC": "XXBTZUSD",
    "ETH": "XETHZUSD",
    "SOL": "SOLUSD",
    "XRP": "XXRPZUSD",
    "ADA": "ADAUSD",
    "DOGE": "XDGUSD",
    "DOT": "DOTUSD",
    "LINK": "LINKUSD",
    "LTC": "XLTCZUSD",
    "ATOM": "ATOMUSD",
    "AVAX": "AVAXUSD",
}


class KrakenCollector(BaseCollector):
    provider = "kraken"
    cache_ttl = 60

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.kraken_api_key, base_url=settings.kraken_base_url)

    def _pair(self, symbol: str) -> str:
        return KRAKEN_PAIRS.get(symbol.upper(), f"{symbol.upper()}USD")

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        pair = self._pair(symbol)
        try:
            data = await self.get(
                "/0/public/Ticker",
                params={"pair": pair},
                cache_key=f"kr:ticker:{pair}",
                ttl=30,
            )
            result = data.get("result", {})
            # Kraken may rename the pair key
            ticker_data = next(iter(result.values()), None)
            if not ticker_data:
                return None
            return Ticker(
                symbol=symbol.upper(),
                price=float(ticker_data["c"][0]),
                volume_24h=float(ticker_data["v"][1]) * float(ticker_data["c"][0]),
                high_24h=float(ticker_data["h"][1]),
                low_24h=float(ticker_data["l"][1]),
                source="kraken",
                raw=ticker_data,
            )
        except Exception as exc:
            logger.warning("Kraken ticker failed for %s: %s", symbol, exc)
            return None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 200) -> List[OHLCV]:
        pair = self._pair(symbol)
        interval_map = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
        interval = interval_map.get(timeframe, 1440)
        data = await self.get(
            "/0/public/OHLC",
            params={"pair": pair, "interval": interval},
            cache_key=f"kr:ohlc:{pair}:{interval}",
            ttl=60,
        )
        result = data.get("result", {})
        rows = next((v for k, v in result.items() if k != "last"), [])
        candles: List[OHLCV] = []
        for row in rows[-limit:]:
            candles.append(
                OHLCV(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    open_time=datetime.fromtimestamp(row[0], tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[6]),
                    source="kraken",
                )
            )
        return candles


class KuCoinCollector(BaseCollector):
    provider = "kucoin"
    cache_ttl = 60

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.kucoin_api_key, base_url=settings.kucoin_base_url)

    def _pair(self, symbol: str) -> str:
        return f"{symbol.upper()}-USDT"

    async def get_ticker(self, symbol: str) -> Optional[Ticker]:
        pair = self._pair(symbol)
        try:
            data = await self.get(
                "/api/v1/market/stats",
                params={"symbol": pair},
                cache_key=f"kc:ticker:{pair}",
                ttl=30,
            )
            d = data.get("data", data)
            return Ticker(
                symbol=symbol.upper(),
                price=float(d.get("last") or 0),
                volume_24h=float(d.get("volValue") or 0),
                change_24h=float(d.get("changeRate") or 0) * 100,
                high_24h=float(d.get("high") or 0),
                low_24h=float(d.get("low") or 0),
                source="kucoin",
                raw=d,
            )
        except Exception as exc:
            logger.warning("KuCoin ticker failed for %s: %s", symbol, exc)
            return None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 200) -> List[OHLCV]:
        pair = self._pair(symbol)
        tf_map = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "1day", "1w": "1week"}
        data = await self.get(
            "/api/v1/market/candles",
            params={"symbol": pair, "type": tf_map.get(timeframe, "1day")},
            cache_key=f"kc:candles:{pair}:{timeframe}",
            ttl=60,
        )
        rows = data.get("data", [])
        candles: List[OHLCV] = []
        # KuCoin returns newest first: [time, open, close, high, low, volume, turnover]
        for row in reversed(rows[:limit]):
            candles.append(
                OHLCV(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    open_time=datetime.fromtimestamp(int(row[0]), tz=timezone.utc),
                    open=float(row[1]),
                    close=float(row[2]),
                    high=float(row[3]),
                    low=float(row[4]),
                    volume=float(row[5]),
                    source="kucoin",
                )
            )
        return candles


class CoinMarketCapCollector(BaseCollector):
    provider = "coinmarketcap"
    cache_ttl = 120

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.coinmarketcap_api_key, base_url=settings.coinmarketcap_base_url)
        self.enabled = bool(settings.coinmarketcap_api_key)

    def _headers(self) -> Dict[str, str]:
        headers = super()._headers()
        if self.api_key:
            headers["X-CMC_PRO_API_KEY"] = self.api_key
        return headers

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_tickers(self, symbols: List[str]) -> List[Ticker]:
        if not self.is_configured():
            return []
        data = await self.get(
            "/cryptocurrency/quotes/latest",
            params={"symbol": ",".join(s.upper() for s in symbols), "convert": "USD"},
            cache_key=f"cmc:quotes:{','.join(sorted(symbols))}",
        )
        tickers: List[Ticker] = []
        for sym, entries in (data.get("data") or {}).items():
            item = entries[0] if isinstance(entries, list) else entries
            quote = (item.get("quote") or {}).get("USD") or {}
            tickers.append(
                Ticker(
                    symbol=sym.upper(),
                    price=float(quote.get("price") or 0),
                    market_cap=quote.get("market_cap"),
                    volume_24h=quote.get("volume_24h"),
                    change_24h=quote.get("percent_change_24h"),
                    change_7d=quote.get("percent_change_7d"),
                    rank=item.get("cmc_rank"),
                    source="coinmarketcap",
                    raw=item,
                )
            )
        return tickers
