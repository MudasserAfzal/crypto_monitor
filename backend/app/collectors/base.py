"""Shared HTTP client with rate limiting, retries, and caching."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from app.core.cache import cache
from app.core.logging import get_logger
from app.core.rate_limiter import rate_limiter, with_backoff

logger = get_logger(__name__)


class BaseCollector:
    """Base class for all API collectors.

    Subclasses set ``provider`` and implement fetch methods.
    Automatically applies rate limiting, retries, and optional Redis caching.
    """

    provider: str = "default"
    base_url: str = ""
    default_headers: Dict[str, str] = {}
    cache_ttl: int = 300
    enabled: bool = True

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        if base_url:
            self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "BaseCollector":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "CryptoSignal/1.0"}
        headers.update(self.default_headers)
        return headers

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(f"{self.provider} client not started — use async with")
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        cache_key: Optional[str] = None,
        ttl: Optional[int] = None,
        use_cache: bool = True,
    ) -> Any:
        if not self.enabled:
            raise RuntimeError(f"{self.provider} collector is disabled")

        if use_cache and cache_key:
            cached = await cache.get(cache_key)
            if cached is not None:
                return cached

        async def _do():
            resp = await self.client.request(method, path, params=params, json=json_body)
            if resp.status_code == 429:
                raise httpx.HTTPStatusError("Rate limited", request=resp.request, response=resp)
            resp.raise_for_status()
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return resp.text

        data = await with_backoff(_do, provider=self.provider, retries=3, base_delay=1.5)

        if use_cache and cache_key:
            await cache.set(cache_key, data, ttl=ttl or self.cache_ttl)
        return data

    async def get(self, path: str, **kwargs) -> Any:
        return await self.request("GET", path, **kwargs)

    def is_configured(self) -> bool:
        """Override when an API key is required."""
        return True
