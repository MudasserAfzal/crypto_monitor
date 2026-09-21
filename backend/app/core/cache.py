"""Async Redis cache client with TTL helpers."""

from __future__ import annotations

import json
from typing import Any, Optional

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CacheClient:
    """Thin Redis wrapper used by collectors and the API layer."""

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        settings = get_settings()
        self._client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await self._client.ping()
        logger.info("Redis cache connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("Cache not connected. Call connect() first.")
        return self._client

    async def get(self, key: str) -> Optional[Any]:
        if self._client is None:
            return None
        try:
            raw = await self.client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.debug("Cache get failed for %s: %s", key, exc)
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if self._client is None:
            return
        settings = get_settings()
        ttl = ttl if ttl is not None else settings.redis_cache_ttl
        try:
            await self.client.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception as exc:
            logger.debug("Cache set failed for %s: %s", key, exc)

    async def delete(self, key: str) -> None:
        try:
            await self.client.delete(key)
        except Exception as exc:
            logger.warning("Cache delete failed for %s: %s", key, exc)

    async def get_or_set(self, key: str, factory, ttl: Optional[int] = None) -> Any:
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await factory() if callable(factory) else factory
        if hasattr(value, "__await__"):
            value = await value
        await self.set(key, value, ttl=ttl)
        return value


cache = CacheClient()
