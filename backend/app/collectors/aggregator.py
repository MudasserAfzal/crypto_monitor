"""Multi-source data aggregator with automatic fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from app.collectors.binance import BinanceCollector
from app.collectors.coingecko import CoinGeckoCollector
from app.collectors.exchanges import CoinMarketCapCollector, KrakenCollector, KuCoinCollector
from app.collectors.premium import (
    BlockchairCollector,
    FredCollector,
    GlassnodeCollector,
    LunarCrushCollector,
)
from app.collectors.sentiment import (
    CryptoPanicCollector,
    DefiLlamaCollector,
    EtherscanCollector,
    FearGreedCollector,
    FinnhubCollector,
    GitHubCollector,
    NewsAPICollector,
    RedditCollector,
    YahooFinanceCollector,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.storage import store
from app.models.schemas import NewsItem, OHLCV, OnChainMetric, SentimentData, Ticker

logger = get_logger(__name__)


class DataAggregator:
    """Fetches market/sentiment/on-chain data with cascading fallbacks."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def fetch_and_store_all_coins(self) -> List[Ticker]:
        """Pull the full coin universe from CoinGecko + Binance and persist locally."""
        merged: Dict[str, Ticker] = {}

        # 1) CoinGecko ranked markets (paginated)
        try:
            async with CoinGeckoCollector() as c:
                cg = await c.get_all_markets(pages=self.settings.coingecko_market_pages)
                for t in cg:
                    merged[t.symbol.upper()] = t
                logger.info("CoinGecko contributed %d coins", len(cg))
        except Exception as exc:
            logger.warning("CoinGecko all-markets failed: %s", exc)

        # 2) Binance all USDT pairs (fills gaps / adds volume)
        try:
            async with BinanceCollector() as c:
                bn = await c.get_all_usdt_tickers()
                for t in bn:
                    key = t.symbol.upper()
                    if key in merged:
                        # Prefer CoinGecko mcap/rank; enrich volume/price from Binance if missing
                        existing = merged[key]
                        merged[key] = Ticker(
                            symbol=key,
                            price=t.price or existing.price,
                            market_cap=existing.market_cap,
                            volume_24h=t.volume_24h or existing.volume_24h,
                            change_24h=t.change_24h if t.change_24h is not None else existing.change_24h,
                            change_7d=existing.change_7d,
                            rank=existing.rank,
                            high_24h=t.high_24h or existing.high_24h,
                            low_24h=t.low_24h or existing.low_24h,
                            source="coingecko+binance",
                            raw={**(existing.raw or {}), "binance": t.raw},
                        )
                    else:
                        merged[key] = t
                logger.info("Binance contributed %d USDT pairs (merged total %d)", len(bn), len(merged))
        except Exception as exc:
            logger.warning("Binance all-tickers failed: %s", exc)

        tickers = sorted(
            merged.values(),
            key=lambda t: (t.market_cap or 0, t.volume_24h or 0),
            reverse=True,
        )
        store.save_tickers([t.model_dump(mode="json") for t in tickers], source="coingecko+binance")
        return tickers

    def load_stored_tickers(self) -> List[Ticker]:
        raw = store.load_tickers()
        out: List[Ticker] = []
        for item in raw:
            try:
                out.append(Ticker.model_validate(item))
            except Exception:
                continue
        return out

    def top_symbols_for_signals(self, limit: Optional[int] = None) -> List[str]:
        """Pick top coins by volume/mcap from local store for signal generation."""
        limit = limit if limit is not None else self.settings.signal_universe_size
        tickers = self.load_stored_tickers()
        if not tickers:
            return self.settings.symbol_list[:limit]
        # Prefer clean symbols (no underscore disambiguation) that Binance can trade
        ranked = sorted(
            tickers,
            key=lambda t: (t.volume_24h or 0, t.market_cap or 0),
            reverse=True,
        )
        symbols: List[str] = []
        for t in ranked:
            sym = t.symbol.upper()
            if "_" in sym:
                continue
            if sym not in symbols:
                symbols.append(sym)
            if len(symbols) >= limit:
                break
        return symbols or self.settings.symbol_list[:limit]

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> List[Ticker]:
        if symbols is None and self.settings.fetch_all_coins:
            stored = self.load_stored_tickers()
            if stored:
                return stored
            return await self.fetch_and_store_all_coins()

        symbols = symbols or self.settings.symbol_list
        sources = [
            ("coingecko", self._tickers_coingecko),
            ("coinmarketcap", self._tickers_cmc),
            ("binance", self._tickers_binance),
        ]
        last_err: Optional[Exception] = None
        for name, fn in sources:
            try:
                tickers = await fn(symbols)
                if tickers:
                    logger.info("Tickers loaded from %s (%d symbols)", name, len(tickers))
                    store.save_tickers([t.model_dump(mode="json") for t in tickers], source=name)
                    return tickers
            except Exception as exc:
                last_err = exc
                logger.warning("Ticker source %s failed: %s", name, exc)
        if last_err:
            logger.error("All ticker sources failed: %s", last_err)
        # Fall back to local disk
        return self.load_stored_tickers()

    async def _tickers_coingecko(self, symbols: List[str]) -> List[Ticker]:
        async with CoinGeckoCollector() as c:
            return await c.get_tickers(symbols)

    async def _tickers_cmc(self, symbols: List[str]) -> List[Ticker]:
        async with CoinMarketCapCollector() as c:
            if not c.is_configured():
                return []
            return await c.get_tickers(symbols)

    async def _tickers_binance(self, symbols: List[str]) -> List[Ticker]:
        async with BinanceCollector() as c:
            return await c.get_tickers(symbols)

    # How long cached OHLCV stays valid before a live fetch is triggered
    _OHLCV_MAX_AGE: Dict[str, int] = {
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
        "4h": 14400, "1d": 86400, "1w": 604800,
    }

    def _ohlcv_is_fresh(self, symbol: str, timeframe: str) -> bool:
        raw = store.read_json(f"ohlcv/{symbol.upper()}_{timeframe}.json", {})
        updated_at_str = raw.get("updated_at")
        if not updated_at_str:
            return False
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated_at).total_seconds()
            max_age = self._OHLCV_MAX_AGE.get(timeframe, 86400)
            return age < max_age
        except Exception:
            return False

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 300,
        *,
        use_cache_file: bool = True,
    ) -> List[OHLCV]:
        # Serve from disk only when the cache is still within one timeframe period
        if use_cache_file and self._ohlcv_is_fresh(symbol, timeframe):
            cached = store.load_ohlcv(symbol, timeframe)
            if cached and len(cached) >= min(30, limit):
                try:
                    candles = [OHLCV.model_validate(c) for c in cached[-limit:]]
                    if candles:
                        return candles
                except Exception:
                    pass

        sources = [
            ("binance", BinanceCollector),
            ("kucoin", KuCoinCollector),
            ("kraken", KrakenCollector),
            ("coingecko", CoinGeckoCollector),
        ]
        for name, cls in sources:
            try:
                async with cls() as c:
                    candles = await c.get_ohlcv(symbol, timeframe=timeframe, limit=limit)
                    if candles and len(candles) >= 30:
                        logger.info("OHLCV %s/%s from %s (%d bars)", symbol, timeframe, name, len(candles))
                        if self.settings.persist_ohlcv:
                            store.save_ohlcv(
                                symbol,
                                timeframe,
                                [c.model_dump(mode="json") for c in candles],
                            )
                        return candles
            except Exception as exc:
                logger.warning("OHLCV source %s failed for %s: %s", name, symbol, exc)
        logger.error("No OHLCV available for %s %s", symbol, timeframe)
        return []

    async def get_fear_greed(self) -> SentimentData:
        try:
            async with FearGreedCollector() as c:
                return await c.get_index()
        except Exception as exc:
            logger.warning("Fear & Greed failed: %s", exc)
            return SentimentData(source="fear_greed", score=0.0, label="unknown")

    async def get_sentiment(self, symbols: Optional[List[str]] = None) -> List[SentimentData]:
        results: List[SentimentData] = []
        try:
            async with FearGreedCollector() as c:
                results.append(await c.get_index())
        except Exception as exc:
            logger.warning("FearGreed in sentiment bundle failed: %s", exc)

        try:
            async with RedditCollector() as c:
                results.append(await c.get_aggregate_sentiment())
        except Exception as exc:
            logger.warning("Reddit sentiment failed: %s", exc)

        if symbols:
            try:
                async with LunarCrushCollector() as c:
                    if c.is_configured():
                        for sym in symbols[:5]:
                            s = await c.get_coin_sentiment(sym)
                            if s:
                                results.append(s)
            except Exception as exc:
                logger.warning("LunarCrush sentiment failed: %s", exc)

        # News-derived market sentiment
        news = await self.get_news(symbols)
        if news:
            scores = [n.sentiment for n in news if n.sentiment is not None]
            if scores:
                avg = sum(scores) / len(scores)
                results.append(
                    SentimentData(source="news_aggregate", score=avg, magnitude=abs(avg), label="news")
                )
        store.save_sentiment([s.model_dump(mode="json") for s in results])
        return results

    async def get_news(self, symbols: Optional[List[str]] = None) -> List[NewsItem]:
        items: List[NewsItem] = []
        try:
            async with CryptoPanicCollector() as c:
                items.extend(await c.get_news(currencies=symbols[:5] if symbols else None))
        except Exception as exc:
            logger.warning("CryptoPanic news failed: %s", exc)
        try:
            async with NewsAPICollector() as c:
                if c.is_configured():
                    items.extend(await c.get_news())
        except Exception as exc:
            logger.warning("NewsAPI failed: %s", exc)
        try:
            async with FinnhubCollector() as c:
                if c.is_configured():
                    items.extend(await c.get_market_news())
        except Exception as exc:
            logger.warning("Finnhub news failed: %s", exc)
        if items:
            store.save_news([n.model_dump(mode="json") for n in items])
        return items

    async def get_eur_per_usd(self) -> float:
        """EUR per 1 USD from CoinGecko BTC cross; fallback ~0.87."""
        try:
            async with CoinGeckoCollector() as c:
                data = await c.get("/simple/price", params={"ids": "bitcoin", "vs_currencies": "eur,usd"}, cache_key="fx:eurusd", ttl=600)
                btc = (data or {}).get("bitcoin") or {}
                eur = float(btc.get("eur") or 0)
                usd = float(btc.get("usd") or 0)
                if eur > 0 and usd > 0:
                    return eur / usd
        except Exception as exc:
            logger.warning("EUR/USD cross failed: %s", exc)
        return 0.87

    async def get_onchain(self, symbols: Optional[List[str]] = None) -> List[OnChainMetric]:
        symbols = symbols or self.settings.symbol_list
        metrics: List[OnChainMetric] = []

        try:
            async with DefiLlamaCollector() as c:
                tvl = await c.get_tvl()
                metrics.append(OnChainMetric(symbol="MARKET", metric="defi_tvl", value=tvl, source="defillama"))
        except Exception as exc:
            logger.warning("DefiLlama TVL failed: %s", exc)

        try:
            async with EtherscanCollector() as c:
                gas = await c.get_gas_oracle()
                if gas:
                    metrics.append(gas)
        except Exception as exc:
            logger.warning("Etherscan gas failed: %s", exc)

        try:
            async with GitHubCollector() as c:
                for sym in symbols[:8]:
                    m = await c.get_dev_activity(sym)
                    if m:
                        metrics.append(m)
        except Exception as exc:
            logger.warning("GitHub activity failed: %s", exc)

        try:
            async with GlassnodeCollector() as c:
                if c.is_configured():
                    flow = await c.get_exchange_net_position("BTC")
                    if flow:
                        metrics.append(flow)
        except Exception as exc:
            logger.warning("Glassnode failed: %s", exc)

        try:
            async with BlockchairCollector() as c:
                btc_stats = await c.get_bitcoin_stats()
                if btc_stats:
                    metrics.append(btc_stats)
        except Exception as exc:
            logger.warning("Blockchair failed: %s", exc)

        store.save_onchain([m.model_dump(mode="json") for m in metrics])
        return metrics

    async def get_macro(self) -> Dict[str, Any]:
        macro: Dict[str, Any] = {}
        yahoo = YahooFinanceCollector()
        spx = await yahoo.get_sp500_change()
        if spx is not None:
            macro["sp500_change_pct"] = spx
            # Risk-on if SPX up
            macro["risk_score"] = max(-1.0, min(1.0, spx / 2.0))
        else:
            macro["risk_score"] = 0.0
        try:
            async with FredCollector() as c:
                rate = await c.get_fed_funds_rate()
                if rate is not None:
                    macro["fed_funds_rate"] = rate
                    # Higher rates → mild risk-off for crypto
                    macro["risk_score"] = float(
                        np.clip(macro.get("risk_score", 0.0) - max(0, rate - 2) * 0.05, -1, 1)
                    )
        except Exception as exc:
            logger.warning("FRED macro failed: %s", exc)
        return macro

    async def get_market_overview(self) -> Dict[str, Any]:
        overview: Dict[str, Any] = {}
        try:
            async with CoinGeckoCollector() as c:
                overview["global"] = await c.get_global()
                overview["trending"] = await c.get_trending()
        except Exception as exc:
            logger.warning("Market overview failed: %s", exc)
            overview["global"] = {}
            overview["trending"] = []
        fg = await self.get_fear_greed()
        overview["fear_greed"] = fg.model_dump()
        overview["macro"] = await self.get_macro()
        store.save_overview(overview)
        return overview


aggregator = DataAggregator()
