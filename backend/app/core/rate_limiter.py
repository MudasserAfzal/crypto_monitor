"""Intelligent rate limiter with token bucket + exponential backoff."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitConfig:
    """Requests per window for a given API provider."""

    max_requests: int
    window_seconds: float
    min_interval: float = 0.0  # hard floor between calls


@dataclass
class _Bucket:
    tokens: float
    last_refill: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Conservative defaults — overridden per-provider when known
DEFAULT_LIMITS: Dict[str, RateLimitConfig] = {
    "coingecko": RateLimitConfig(max_requests=10, window_seconds=60, min_interval=1.2),
    "coinmarketcap": RateLimitConfig(max_requests=30, window_seconds=60, min_interval=0.5),
    "binance": RateLimitConfig(max_requests=1100, window_seconds=60, min_interval=0.05),
    "kraken": RateLimitConfig(max_requests=15, window_seconds=60, min_interval=1.0),
    "kucoin": RateLimitConfig(max_requests=100, window_seconds=60, min_interval=0.2),
    "lunarcrush": RateLimitConfig(max_requests=10, window_seconds=60, min_interval=1.0),
    "santiment": RateLimitConfig(max_requests=30, window_seconds=60, min_interval=0.5),
    "glassnode": RateLimitConfig(max_requests=20, window_seconds=60, min_interval=1.0),
    "covalent": RateLimitConfig(max_requests=40, window_seconds=60, min_interval=0.5),
    "dune": RateLimitConfig(max_requests=10, window_seconds=60, min_interval=2.0),
    "etherscan": RateLimitConfig(max_requests=5, window_seconds=1, min_interval=0.25),
    "blockchair": RateLimitConfig(max_requests=30, window_seconds=60, min_interval=0.5),
    "newsapi": RateLimitConfig(max_requests=50, window_seconds=86400, min_interval=1.0),
    "cryptopanic": RateLimitConfig(max_requests=30, window_seconds=60, min_interval=1.0),
    "finnhub": RateLimitConfig(max_requests=60, window_seconds=60, min_interval=0.5),
    "fred": RateLimitConfig(max_requests=100, window_seconds=60, min_interval=0.2),
    "github": RateLimitConfig(max_requests=30, window_seconds=60, min_interval=0.5),
    "defillama": RateLimitConfig(max_requests=60, window_seconds=60, min_interval=0.3),
    "fear_greed": RateLimitConfig(max_requests=30, window_seconds=60, min_interval=1.0),
    "reddit": RateLimitConfig(max_requests=60, window_seconds=60, min_interval=1.0),
    "twitter": RateLimitConfig(max_requests=15, window_seconds=60, min_interval=2.0),
    "default": RateLimitConfig(max_requests=30, window_seconds=60, min_interval=0.5),
}


class RateLimiter:
    """Token-bucket rate limiter shared across collectors."""

    def __init__(self, limits: Optional[Dict[str, RateLimitConfig]] = None) -> None:
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._buckets: Dict[str, _Bucket] = {}
        self._last_call: Dict[str, float] = defaultdict(float)

    def _config(self, provider: str) -> RateLimitConfig:
        return self.limits.get(provider, self.limits["default"])

    def _bucket(self, provider: str) -> _Bucket:
        if provider not in self._buckets:
            cfg = self._config(provider)
            self._buckets[provider] = _Bucket(
                tokens=float(cfg.max_requests),
                last_refill=time.monotonic(),
            )
        return self._buckets[provider]

    async def acquire(self, provider: str) -> None:
        """Block until a request token is available for *provider*."""
        cfg = self._config(provider)
        bucket = self._bucket(provider)

        async with bucket.lock:
            while True:
                now = time.monotonic()
                # Refill
                elapsed = now - bucket.last_refill
                refill = (elapsed / cfg.window_seconds) * cfg.max_requests
                bucket.tokens = min(cfg.max_requests, bucket.tokens + refill)
                bucket.last_refill = now

                # Min interval between calls
                since_last = now - self._last_call[provider]
                if since_last < cfg.min_interval:
                    await asyncio.sleep(cfg.min_interval - since_last)
                    continue

                if bucket.tokens >= 1.0:
                    bucket.tokens -= 1.0
                    self._last_call[provider] = time.monotonic()
                    return

                # Wait for next token
                wait = (1.0 - bucket.tokens) * (cfg.window_seconds / cfg.max_requests)
                logger.debug("Rate limit wait %.2fs for %s", wait, provider)
                await asyncio.sleep(max(wait, 0.05))


rate_limiter = RateLimiter()


async def with_backoff(coro_factory, *, retries: int = 3, base_delay: float = 1.0, provider: str = "default"):
    """Execute an async callable with exponential backoff on failure."""
    import httpx

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            await rate_limiter.acquire(provider)
            return await coro_factory()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            # Do not retry client errors except 429
            if exc.response is not None and exc.response.status_code in (401, 403, 404):
                raise
            if attempt >= retries:
                break
            delay = base_delay * (2**attempt)
            logger.warning(
                "%s attempt %d failed: %s — retrying in %.1fs",
                provider,
                attempt + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
        except Exception as exc:
            last_exc = exc
            if attempt >= retries:
                break
            delay = base_delay * (2**attempt)
            logger.warning(
                "%s attempt %d failed: %s — retrying in %.1fs",
                provider,
                attempt + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
