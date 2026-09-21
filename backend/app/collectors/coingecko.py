"""CoinGecko market data collector (primary free-tier source)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.collectors.base import BaseCollector
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import OHLCV, Ticker

logger = get_logger(__name__)

# Common symbol → CoinGecko id mapping
COINGECKO_IDS: Dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "LTC": "litecoin",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUI": "sui",
    "PEPE": "pepe",
    "SHIB": "shiba-inu",
    "TRX": "tron",
    "TON": "the-open-network",
}

TF_TO_DAYS = {"1h": 7, "4h": 30, "1d": 90, "1w": 365}


class CoinGeckoCollector(BaseCollector):
    provider = "coingecko"
    cache_ttl = 120

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.coingecko_api_key, base_url=settings.coingecko_base_url)

    def _headers(self) -> Dict[str, str]:
        headers = super()._headers()
        if self.api_key:
            headers["x-cg-demo-api-key"] = self.api_key
        return headers

    def resolve_id(self, symbol: str) -> str:
        return COINGECKO_IDS.get(symbol.upper(), symbol.lower())

    async def get_tickers(self, symbols: List[str]) -> List[Ticker]:
        ids = [self.resolve_id(s) for s in symbols]
        # CoinGecko caps ~250 ids per request — batch
        tickers: List[Ticker] = []
        id_to_symbol = {self.resolve_id(s): s.upper() for s in symbols}
        for i in range(0, len(ids), 200):
            batch = ids[i : i + 200]
            data = await self.get(
                "/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(batch),
                    "order": "market_cap_desc",
                    "sparkline": "false",
                    "price_change_percentage": "24h,7d",
                },
                cache_key=f"cg:markets:{','.join(sorted(batch))}",
            )
            for item in data or []:
                sym = id_to_symbol.get(item.get("id"), (item.get("symbol") or "").upper())
                tickers.append(self._item_to_ticker(item, sym))
        return tickers

    def _item_to_ticker(self, item: Dict[str, Any], symbol: Optional[str] = None) -> Ticker:
        sym = symbol or (item.get("symbol") or "").upper()
        return Ticker(
            symbol=sym,
            price=float(item.get("current_price") or 0),
            market_cap=item.get("market_cap"),
            volume_24h=item.get("total_volume"),
            change_24h=item.get("price_change_percentage_24h"),
            change_7d=item.get("price_change_percentage_7d_in_currency"),
            rank=item.get("market_cap_rank"),
            high_24h=item.get("high_24h"),
            low_24h=item.get("low_24h"),
            source="coingecko",
            raw={
                "id": item.get("id"),
                "name": item.get("name"),
                "symbol": item.get("symbol"),
                "image": item.get("image"),
                "ath": item.get("ath"),
                "atl": item.get("atl"),
                "circulating_supply": item.get("circulating_supply"),
                "total_supply": item.get("total_supply"),
            },
        )

    async def get_all_markets(self, pages: int = 4, per_page: int = 250) -> List[Ticker]:
        """Paginate CoinGecko /coins/markets — extracts the full ranked universe."""
        settings = get_settings()
        pages = pages or settings.coingecko_market_pages
        all_tickers: List[Ticker] = []
        seen_bases: set[str] = set()
        for page in range(1, pages + 1):
            try:
                data = await self.get(
                    "/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": per_page,
                        "page": page,
                        "sparkline": "false",
                        "price_change_percentage": "24h,7d",
                    },
                    cache_key=f"cg:markets:page:{page}:{per_page}",
                    ttl=180,
                )
            except Exception as exc:
                logger.warning("CoinGecko markets page %d failed: %s", page, exc)
                break
            if not data:
                break
            for item in data:
                base = (item.get("symbol") or "").upper()
                if not base:
                    continue
                if base in seen_bases:
                    sym = f"{base}_{(item.get('id') or base)}"
                else:
                    sym = base
                    seen_bases.add(base)
                all_tickers.append(self._item_to_ticker(item, sym))
            logger.info("CoinGecko page %d → %d coins (total %d)", page, len(data), len(all_tickers))
            if len(data) < per_page:
                break
        return all_tickers

    async def get_trending(self) -> List[Dict[str, Any]]:
        data = await self.get("/search/trending", cache_key="cg:trending", ttl=300)
        return data.get("coins", []) if isinstance(data, dict) else []

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 200) -> List[OHLCV]:
        coin_id = self.resolve_id(symbol)
        days = TF_TO_DAYS.get(timeframe, 90)
        data = await self.get(
            f"/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": days},
            cache_key=f"cg:ohlc:{coin_id}:{days}",
            ttl=180,
        )
        candles: List[OHLCV] = []
        for row in (data or [])[-limit:]:
            # [timestamp_ms, open, high, low, close]
            ts = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc)
            candles.append(
                OHLCV(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    open_time=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=0.0,
                    source="coingecko",
                )
            )
        return candles

    async def get_global(self) -> Dict[str, Any]:
        data = await self.get("/global", cache_key="cg:global", ttl=300)
        return data.get("data", data) if isinstance(data, dict) else {}
