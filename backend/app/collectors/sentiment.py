"""Sentiment, news, on-chain, and alternative data collectors."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.collectors.base import BaseCollector
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import NewsItem, OnChainMetric, SentimentData

logger = get_logger(__name__)

# Simple lexicon for fallback NLP when cloud NLP APIs unavailable
BULLISH = {
    "bull", "bullish", "moon", "rally", "surge", "breakout", "accumulate", "buy",
    "long", "pump", "ath", "adoption", "upgrade", "partnership", "etf", "inflow",
}
BEARISH = {
    "bear", "bearish", "crash", "dump", "sell", "short", "hack", "ban", "sec",
    "lawsuit", "outflow", "liquidation", "fear", "rug", "scam", "exploit",
}


def parse_published_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def lexicon_sentiment(text: str) -> float:
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    bull = len(tokens & BULLISH)
    bear = len(tokens & BEARISH)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


class FearGreedCollector(BaseCollector):
    provider = "fear_greed"
    cache_ttl = 3600

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(base_url=settings.fear_greed_base_url)

    async def get_index(self) -> SentimentData:
        data = await self.get("/", params={"limit": 1}, cache_key="fg:latest", ttl=1800)
        item = (data.get("data") or [{}])[0]
        value = int(item.get("value") or 50)
        # Map 0-100 → -1 to +1
        score = (value - 50) / 50.0
        return SentimentData(
            symbol=None,
            source="fear_greed",
            score=max(-1.0, min(1.0, score)),
            magnitude=abs(score),
            label=item.get("value_classification"),
            raw=item,
        )


class CryptoPanicCollector(BaseCollector):
    provider = "cryptopanic"
    cache_ttl = 300

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.cryptopanic_api_key, base_url=settings.cryptopanic_base_url)
        self.enabled = True  # public filter works with limited results

    async def get_news(self, currencies: Optional[List[str]] = None) -> List[NewsItem]:
        params: Dict[str, Any] = {"public": "true"}
        if self.api_key:
            params["auth_token"] = self.api_key
        if currencies:
            params["currencies"] = ",".join(currencies)
        try:
            data = await self.get("/posts/", params=params, cache_key=f"cp:news:{params.get('currencies','all')}")
        except Exception as exc:
            logger.warning("CryptoPanic failed: %s", exc)
            return []
        items: List[NewsItem] = []
        for post in data.get("results") or []:
            title = post.get("title") or ""
            votes = post.get("votes") or {}
            pos = int(votes.get("positive") or 0)
            neg = int(votes.get("negative") or 0)
            total = pos + neg
            sent = ((pos - neg) / total) if total else lexicon_sentiment(title)
            currs = [c.get("code", "").upper() for c in (post.get("currencies") or [])]
            items.append(
                NewsItem(
                    title=title,
                    url=post.get("url"),
                    source="cryptopanic",
                    published_at=parse_published_at(post.get("published_at") or post.get("created_at")),
                    sentiment=sent,
                    currencies=currs,
                    raw=post,
                )
            )
        return items


class NewsAPICollector(BaseCollector):
    provider = "newsapi"
    cache_ttl = 600

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.newsapi_key, base_url=settings.newsapi_base_url)
        self.enabled = bool(settings.newsapi_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_news(self, query: str = "cryptocurrency OR bitcoin OR ethereum") -> List[NewsItem]:
        if not self.is_configured():
            return []
        data = await self.get(
            "/everything",
            params={"q": query, "language": "en", "sortBy": "publishedAt", "pageSize": 30, "apiKey": self.api_key},
            cache_key=f"newsapi:{query}",
        )
        items: List[NewsItem] = []
        for a in data.get("articles") or []:
            title = a.get("title") or ""
            items.append(
                NewsItem(
                    title=title,
                    url=a.get("url"),
                    source="newsapi",
                    published_at=parse_published_at(a.get("publishedAt")),
                    sentiment=lexicon_sentiment(f"{title} {a.get('description') or ''}"),
                    raw=a,
                )
            )
        return items


class DefiLlamaCollector(BaseCollector):
    provider = "defillama"
    cache_ttl = 300

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(base_url=settings.defillama_base_url)

    async def get_tvl(self) -> float:
        data = await self.get("/v2/historicalChainTvl", cache_key="llama:tvl", ttl=600)
        if isinstance(data, list) and data:
            return float(data[-1].get("tvl") or 0)
        return 0.0

    async def get_protocol_tvl(self, protocol: str) -> Optional[OnChainMetric]:
        try:
            data = await self.get(f"/protocol/{protocol}", cache_key=f"llama:proto:{protocol}", ttl=600)
            tvl = float(data.get("currentChainTvls", {}).get("Ethereum") or data.get("tvl") or 0)
            if isinstance(data.get("tvl"), list) and data["tvl"]:
                tvl = float(data["tvl"][-1].get("totalLiquidityUSD") or tvl)
            return OnChainMetric(symbol=protocol.upper(), metric="tvl", value=tvl, source="defillama", raw=data)
        except Exception as exc:
            logger.warning("DefiLlama protocol %s failed: %s", protocol, exc)
            return None


class EtherscanCollector(BaseCollector):
    provider = "etherscan"
    cache_ttl = 120

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.etherscan_api_key, base_url=settings.etherscan_base_url)
        self.enabled = bool(settings.etherscan_api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_gas_oracle(self) -> Optional[OnChainMetric]:
        if not self.is_configured():
            return None
        data = await self.get(
            "",
            params={"module": "gastracker", "action": "gasoracle", "apikey": self.api_key},
            cache_key="etherscan:gas",
            ttl=60,
        )
        result = data.get("result") or {}
        gwei = float(result.get("ProposeGasPrice") or 0)
        return OnChainMetric(symbol="ETH", metric="gas_gwei", value=gwei, source="etherscan", raw=result)


class GitHubCollector(BaseCollector):
    provider = "github"
    cache_ttl = 3600

    # Well-known repos for major coins
    REPOS = {
        "BTC": "bitcoin/bitcoin",
        "ETH": "ethereum/go-ethereum",
        "SOL": "solana-labs/solana",
        "DOT": "paritytech/polkadot-sdk",
        "ADA": "IntersectMBO/cardano-node",
        "AVAX": "ava-labs/avalanchego",
        "NEAR": "near/nearcore",
        "ATOM": "cosmos/cosmos-sdk",
        "LINK": "smartcontractkit/chainlink",
    }

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.github_token, base_url=settings.github_base_url)

    def _headers(self) -> Dict[str, str]:
        headers = super()._headers()
        headers["Accept"] = "application/vnd.github+json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def get_dev_activity(self, symbol: str) -> Optional[OnChainMetric]:
        repo = self.REPOS.get(symbol.upper())
        if not repo:
            return None
        try:
            data = await self.get(f"/repos/{repo}", cache_key=f"gh:repo:{repo}", ttl=3600)
            # Composite: stars + forks + open issues (normalized later)
            score = float(data.get("stargazers_count") or 0) + float(data.get("forks_count") or 0) * 2
            return OnChainMetric(
                symbol=symbol.upper(),
                metric="github_activity",
                value=score,
                source="github",
                raw={"stars": data.get("stargazers_count"), "forks": data.get("forks_count"), "pushed_at": data.get("pushed_at")},
            )
        except Exception as exc:
            logger.warning("GitHub activity for %s failed: %s", symbol, exc)
            return None


class FinnhubCollector(BaseCollector):
    provider = "finnhub"
    cache_ttl = 300

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(api_key=settings.finnhub_api_key, base_url=settings.finnhub_base_url)
        self.enabled = bool(settings.finnhub_api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_market_news(self) -> List[NewsItem]:
        if not self.is_configured():
            return []
        data = await self.get(
            "/news",
            params={"category": "crypto", "token": self.api_key},
            cache_key="finnhub:crypto_news",
        )
        items: List[NewsItem] = []
        for a in data or []:
            headline = a.get("headline") or ""
            items.append(
                NewsItem(
                    title=headline,
                    url=a.get("url"),
                    source="finnhub",
                    published_at=parse_published_at(a.get("datetime")),
                    sentiment=lexicon_sentiment(headline),
                    raw=a,
                )
            )
        return items


class RedditCollector(BaseCollector):
    """Reddit collector via public JSON endpoints (no OAuth required for read)."""

    provider = "reddit"
    cache_ttl = 300
    SUBREDDITS = ["CryptoCurrency", "Bitcoin", "ethtrader", "CryptoMarkets"]

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(base_url="https://www.reddit.com")
        self.client_id = settings.reddit_client_id
        self.client_secret = settings.reddit_client_secret

    def _headers(self) -> Dict[str, str]:
        headers = super()._headers()
        headers["User-Agent"] = get_settings().reddit_user_agent
        return headers

    async def get_subreddit_sentiment(self, subreddit: str = "CryptoCurrency", limit: int = 50) -> SentimentData:
        data = await self.get(
            f"/r/{subreddit}/hot.json",
            params={"limit": limit},
            cache_key=f"reddit:{subreddit}:hot",
            ttl=300,
        )
        posts = (data.get("data") or {}).get("children") or []
        scores = []
        for p in posts:
            d = p.get("data") or {}
            title = d.get("title") or ""
            scores.append(lexicon_sentiment(title))
        avg = sum(scores) / len(scores) if scores else 0.0
        return SentimentData(
            source="reddit",
            score=max(-1.0, min(1.0, avg)),
            magnitude=abs(avg),
            label=subreddit,
            raw={"count": len(scores), "avg": avg},
        )

    async def get_aggregate_sentiment(self) -> SentimentData:
        scores = []
        for sub in self.SUBREDDITS:
            try:
                s = await self.get_subreddit_sentiment(sub)
                scores.append(s.score)
            except Exception as exc:
                logger.warning("Reddit %s skipped: %s", sub, exc)
                # If blocked, don't burn rate limit on remaining subs
                if "403" in str(exc):
                    break
        avg = sum(scores) / len(scores) if scores else 0.0
        return SentimentData(source="reddit", score=avg, magnitude=abs(avg), label="aggregate")


class YahooFinanceCollector:
    """Yahoo Finance via yfinance (sync library wrapped for async usage)."""

    provider = "yahoo"

    def __init__(self) -> None:
        self.enabled = get_settings().yahoo_finance_enabled

    async def get_sp500_change(self) -> Optional[float]:
        if not self.enabled:
            return None
        try:
            import asyncio
            import yfinance as yf

            def _fetch():
                t = yf.Ticker("^GSPC")
                hist = t.history(period="5d")
                if len(hist) < 2:
                    return None
                return float((hist["Close"].iloc[-1] / hist["Close"].iloc[-2] - 1) * 100)

            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.warning("Yahoo Finance failed: %s", exc)
            return None
