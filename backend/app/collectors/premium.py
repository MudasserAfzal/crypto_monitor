"""Optional premium API collectors — activate when keys are present."""

from __future__ import annotations

from typing import List, Optional

from app.collectors.base import BaseCollector
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import OnChainMetric, SentimentData

logger = get_logger(__name__)


class LunarCrushCollector(BaseCollector):
    provider = "lunarcrush"
    cache_ttl = 300

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.lunarcrush_api_key, base_url=settings.lunarcrush_base_url)
        self.enabled = bool(settings.lunarcrush_api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self):
        h = super()._headers()
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def get_coin_sentiment(self, symbol: str) -> Optional[SentimentData]:
        if not self.is_configured():
            return None
        try:
            data = await self.get(
                f"/public/coins/{symbol.upper()}/v1",
                cache_key=f"lc:{symbol}",
            )
            d = data.get("data") or data
            galaxy = float(d.get("galaxy_score") or 50)
            score = (galaxy - 50) / 50.0
            return SentimentData(
                symbol=symbol.upper(),
                source="lunarcrush",
                score=max(-1.0, min(1.0, score)),
                magnitude=abs(score),
                label="galaxy_score",
                raw=d,
            )
        except Exception as exc:
            logger.warning("LunarCrush failed for %s: %s", symbol, exc)
            return None


class GlassnodeCollector(BaseCollector):
    provider = "glassnode"
    cache_ttl = 600

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.glassnode_api_key, base_url=settings.glassnode_base_url)
        self.enabled = bool(settings.glassnode_api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_exchange_net_position(self, symbol: str = "BTC") -> Optional[OnChainMetric]:
        if not self.is_configured():
            return None
        asset = symbol.upper()
        try:
            data = await self.get(
                "/metrics/distribution/exchange_net_position_change",
                params={"a": asset, "api_key": self.api_key, "i": "24h"},
                cache_key=f"gn:exflow:{asset}",
            )
            # Typically list of {t, v}
            val = float(data[-1]["v"]) if isinstance(data, list) and data else 0.0
            return OnChainMetric(
                symbol=asset,
                metric="exchange_net_position_change",
                value=val,
                source="glassnode",
                raw={"latest": data[-1] if isinstance(data, list) and data else data},
            )
        except Exception as exc:
            logger.warning("Glassnode failed: %s", exc)
            return None


class CovalentCollector(BaseCollector):
    provider = "covalent"
    cache_ttl = 600

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.covalent_api_key, base_url=settings.covalent_base_url)
        self.enabled = bool(settings.covalent_api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_token_holders(self, chain_id: int = 1, token_address: str = "") -> Optional[OnChainMetric]:
        if not self.is_configured() or not token_address:
            return None
        try:
            data = await self.get(
                f"/{chain_id}/tokens/{token_address}/token_holders_v2/",
                params={"key": self.api_key},
                cache_key=f"cov:holders:{token_address}",
            )
            items = (data.get("data") or {}).get("items") or []
            return OnChainMetric(
                symbol="ETH",
                metric="token_holders",
                value=float(len(items)),
                source="covalent",
                raw={"count": len(items)},
            )
        except Exception as exc:
            logger.warning("Covalent failed: %s", exc)
            return None


class DuneCollector(BaseCollector):
    provider = "dune"
    cache_ttl = 1800

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.dune_api_key, base_url=settings.dune_base_url)
        self.enabled = bool(settings.dune_api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self):
        h = super()._headers()
        if self.api_key:
            h["X-Dune-API-Key"] = self.api_key
        return h

    async def get_query_result(self, query_id: int) -> Optional[dict]:
        if not self.is_configured():
            return None
        try:
            return await self.get(f"/query/{query_id}/results", cache_key=f"dune:{query_id}")
        except Exception as exc:
            logger.warning("Dune query %s failed: %s", query_id, exc)
            return None


class BlockchairCollector(BaseCollector):
    provider = "blockchair"
    cache_ttl = 300

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.blockchair_api_key, base_url=settings.blockchair_base_url)

    async def get_bitcoin_stats(self) -> Optional[OnChainMetric]:
        try:
            params = {}
            if self.api_key:
                params["key"] = self.api_key
            data = await self.get("/bitcoin/stats", params=params or None, cache_key="bc:btc:stats")
            stats = (data.get("data") or {})
            txs = float(stats.get("transactions_24h") or 0)
            return OnChainMetric(symbol="BTC", metric="tx_24h", value=txs, source="blockchair", raw=stats)
        except Exception as exc:
            logger.warning("Blockchair failed: %s", exc)
            return None


class FredCollector(BaseCollector):
    provider = "fred"
    cache_ttl = 86400

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.fred_api_key, base_url=settings.fred_base_url)
        self.enabled = bool(settings.fred_api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_fed_funds_rate(self) -> Optional[float]:
        if not self.is_configured():
            return None
        try:
            data = await self.get(
                "/series/observations",
                params={
                    "series_id": "FEDFUNDS",
                    "api_key": self.api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
                cache_key="fred:fedfunds",
            )
            obs = (data.get("observations") or [{}])[0]
            return float(obs.get("value"))
        except Exception as exc:
            logger.warning("FRED failed: %s", exc)
            return None
