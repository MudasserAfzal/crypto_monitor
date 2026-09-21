"""Fast live plan — Binance + CoinGecko, no ML universe scan."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from app.core.logging import get_logger
from app.core.storage import store
from app.models.schemas import (
    DailyReport,
    Direction,
    LiveQuote,
    NewsDipContext,
    NewsItem,
    OHLCV,
    SignalType,
    TimeHorizon,
    TradeSignal,
)
from app.signals.news_dips import (
    build_playbook_text,
    build_short_term_book,
    build_starter_plan,
    category_mix,
    detect_dips,
    dominant_category,
    headlines_from_news,
    historical_analogs,
    resolve_verdict,
    seven_day_bounce,
)

logger = get_logger(__name__)

WATCH = {
    "BTC": "BTCEUR",
    "ETH": "ETHEUR",
    "SOL": "SOLEUR",
    "XRP": "XRPEUR",
    "BNB": "BNBEUR",
    "DOGE": "DOGEEUR",
}

WATCH_USD = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "BNB": "BNBUSDT",
    "DOGE": "DOGEUSDT",
}

KRAKEN_EUR = {
    "BTC": "XXBTZEUR",
    "ETH": "XETHZEUR",
    "SOL": "SOLEUR",
    "XRP": "XXRPZEUR",
    "BNB": "BNBEUR",
    "DOGE": "XDGEUR",
}

_QUOTE_CACHE: List[LiveQuote] = []
_QUOTE_CACHE_AT = 0.0
_HTTP_HEADERS = {"User-Agent": "CryptoSignal/1.0"}


async def _binance_quotes() -> List[LiveQuote]:
    pairs = list(WATCH.values()) + list(WATCH_USD.values())
    symbols = "[" + ",".join(f'"{p}"' for p in pairs) + "]"
    async with httpx.AsyncClient(timeout=15.0, headers=_HTTP_HEADERS) as client:
        r = await client.get("https://api.binance.com/api/v3/ticker/24hr", params={"symbols": symbols})
        r.raise_for_status()
        rows = r.json()
    by_pair = {row.get("symbol"): row for row in rows}
    quotes: List[LiveQuote] = []
    for symbol, pair in WATCH.items():
        eur = by_pair.get(pair) or {}
        usd = by_pair.get(WATCH_USD[symbol]) or {}
        last_eur = float(eur.get("lastPrice") or 0)
        last_usd = float(usd.get("lastPrice") or 0)
        chg = float(eur.get("priceChangePercent") or usd.get("priceChangePercent") or 0)
        if last_eur <= 0 and last_usd <= 0:
            continue
        quotes.append(
            LiveQuote(symbol=symbol, price_eur=last_eur, price_usd=last_usd, change_24h_pct=chg)
        )
    if len(quotes) < 3:
        raise RuntimeError("Binance returned too few quotes")
    return quotes


async def _kraken_quotes() -> List[LiveQuote]:
    async with httpx.AsyncClient(timeout=15.0, headers=_HTTP_HEADERS) as client:
        r = await client.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": ",".join(KRAKEN_EUR.values())},
        )
        r.raise_for_status()
        payload = r.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    data = payload.get("result") or {}
    quotes: List[LiveQuote] = []
    for symbol, pair in KRAKEN_EUR.items():
        row = data.get(pair)
        if row is None:
            for key, val in data.items():
                if symbol in key.upper() or pair in key:
                    row = val
                    break
        if not row:
            continue
        last = float((row.get("c") or [0])[0])
        open_px = float(row.get("o") or 0)
        chg = ((last / open_px) - 1.0) * 100 if open_px else 0.0
        if last <= 0:
            continue
        quotes.append(LiveQuote(symbol=symbol, price_eur=last, price_usd=0.0, change_24h_pct=chg))
    if len(quotes) < 3:
        raise RuntimeError("Kraken returned too few quotes")
    return quotes


async def fetch_live_quotes() -> List[LiveQuote]:
    """EUR tape from Binance, then Kraken. Never CoinGecko (429s)."""
    import time

    global _QUOTE_CACHE, _QUOTE_CACHE_AT
    now = time.time()
    if _QUOTE_CACHE and now - _QUOTE_CACHE_AT < 8:
        return list(_QUOTE_CACHE)

    last_err: Optional[Exception] = None
    for loader in (_binance_quotes, _kraken_quotes):
        try:
            quotes = await loader()
            _QUOTE_CACHE = quotes
            _QUOTE_CACHE_AT = now
            return quotes
        except Exception as exc:
            last_err = exc
            logger.warning("%s failed: %s", loader.__name__, exc)
    if _QUOTE_CACHE:
        return list(_QUOTE_CACHE)
    if last_err:
        raise last_err
    return []

FALLBACK_HEADLINES = [
    "Senate blocks CLARITY Act 49-50 as Bitcoin slides toward $75,000",
    "Federal Reserve raises rates 25 bps to 3.75%-4.00%, first hike since 2023",
    "Hopes of US-Iran war de-escalation and falling oil prices lift risk assets",
    "SEC issues five-year innovation exemption for tokenized-stock trading",
    "Spot Bitcoin ETFs swing back to inflows after last week's outflows",
    "Short squeeze liquidates hundreds of millions as Bitcoin reclaims $85,000",
]


def _candles_from_klines(symbol: str, rows: list) -> List[OHLCV]:
    out: List[OHLCV] = []
    for row in rows:
        out.append(
            OHLCV(
                symbol=symbol,
                timeframe="1d",
                open_time=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                source="binance",
            )
        )
    return out


def annotate_quotes(quotes: List[LiveQuote], report: Optional[DailyReport]) -> List[LiveQuote]:
    by_sym = {}
    if report and report.short_term_book:
        by_sym = {leg.symbol.upper(): leg for leg in report.short_term_book.legs}
    out: List[LiveQuote] = []
    for q in quotes:
        leg = by_sym.get(q.symbol)
        action = "WATCH"
        vs_limit = vs_stop = vs_tp = None
        if leg and q.price_eur and leg.entry_price_eur:
            vs_limit = (q.price_eur / leg.entry_price_eur - 1.0) * 100
            if leg.stop_loss_eur:
                vs_stop = (q.price_eur / leg.stop_loss_eur - 1.0) * 100
            if leg.take_profit_eur:
                vs_tp = (q.price_eur / leg.take_profit_eur - 1.0) * 100
            if q.price_eur <= (leg.stop_loss_eur or 0):
                action = "STOP"
            elif q.price_eur <= leg.entry_price_eur * 1.005:
                action = "BUY"
            elif leg.take_profit_eur and q.price_eur >= leg.take_profit_eur:
                action = "SELL"
            else:
                action = "WAIT"
        q.action_now = action
        q.vs_limit_pct = None if vs_limit is None else round(vs_limit, 2)
        q.vs_stop_pct = None if vs_stop is None else round(vs_stop, 2)
        q.vs_tp_pct = None if vs_tp is None else round(vs_tp, 2)
        out.append(q)
    return out


async def build_live_report(budget_eur: float = 100.0) -> DailyReport:
    quotes = await fetch_live_quotes()
    by_sym = {q.symbol.upper(): q for q in quotes}
    async with httpx.AsyncClient(timeout=25.0, headers=_HTTP_HEADERS) as client:
        try:
            fg_r = await client.get("https://api.alternative.me/fng/", params={"limit": 1})
        except Exception:
            fg_r = None
        try:
            news_r = await client.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"public": "true", "currencies": "BTC,ETH,SOL,XRP"},
            )
        except Exception:
            news_r = None
        kline_syms = ["BTC", "ETH", "SOL", "XRP"]
        kline_res = {}
        for s in kline_syms:
            try:
                kline_res[s] = await client.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": f"{s}USDT", "interval": "1d", "limit": 120},
                )
            except Exception as exc:
                logger.warning("klines %s failed: %s", s, exc)

    btc_q = by_sym.get("BTC")
    btc_eur = float(btc_q.price_eur) if btc_q else 0.0
    btc_usd = float(btc_q.price_usd) if btc_q else 0.0
    fx = btc_eur / btc_usd if btc_usd else 0.92

    candles_by_symbol: Dict[str, List[OHLCV]] = {}
    for s, resp in kline_res.items():
        try:
            resp.raise_for_status()
            candles_by_symbol[s] = _candles_from_klines(s, resp.json())
            store.save_ohlcv(s, "1d", [c.model_dump(mode="json") for c in candles_by_symbol[s]])
        except Exception as exc:
            logger.warning("klines parse %s failed: %s", s, exc)

    news_items: List[NewsItem] = []
    if news_r is not None:
        try:
            news_r.raise_for_status()
            for post in news_r.json().get("results") or []:
                news_items.append(
                    NewsItem(
                        title=post.get("title") or "",
                        url=post.get("url"),
                        source="cryptopanic",
                        currencies=[c.get("code", "") for c in (post.get("currencies") or [])],
                    )
                )
        except Exception as exc:
            logger.warning("CryptoPanic failed: %s", exc)
    if not news_items:
        news_items = [NewsItem(title=t, source="manual_wire", currencies=["BTC"]) for t in FALLBACK_HEADLINES]

    fg = None
    if fg_r is not None:
        try:
            fg_r.raise_for_status()
            fg = int((fg_r.json().get("data") or [{}])[0].get("value") or 50)
        except Exception:
            fg = None

    btc_candles = candles_by_symbol.get("BTC") or []
    headlines = headlines_from_news(news_items)
    category = dominant_category(headlines)
    analogs = historical_analogs(category)
    bounce = seven_day_bounce(btc_candles)
    verdict = resolve_verdict(category, bounce_pct=bounce, fear_greed=fg)
    playbook = build_playbook_text(category, analogs, bounce)
    recent = detect_dips(btc_candles)

    watch_syms = ["BTC", "ETH", "SOL", "XRP"]
    signals: List[TradeSignal] = []
    last_eur: Dict[str, float] = {q.symbol.upper(): q.price_eur for q in quotes if q.price_eur}
    for sym in watch_syms:
        q = by_sym.get(sym)
        px = float(q.price_usd or 0) if q else 0.0
        chg = float(q.change_24h_pct or 0) if q else 0.0
        lean = SignalType.HOLD
        if verdict == "BUY_NOW":
            lean = SignalType.BUY
        elif verdict in ("AVOID", "SELL"):
            lean = SignalType.SELL
        signals.append(
            TradeSignal(
                symbol=sym,
                primary_signal=lean,
                secondary_signal=Direction.LONG if lean == SignalType.BUY else Direction.NONE,
                confidence=35.0,
                entry_price=px,
                time_horizon=TimeHorizon.MEDIUM,
                rationale=f"24h {chg:+.1f}%; news verdict {verdict}",
            )
        )

    context = NewsDipContext(
        dominant_category=category,
        headline_mix=category_mix(headlines),
        current_headlines=headlines,
        recent_dips=recent,
        historical_analogs=analogs,
        playbook=playbook,
        verdict=verdict,
    )
    plan = build_starter_plan(
        budget_eur=budget_eur,
        signals=signals,
        btc_candles=btc_candles,
        verdict=verdict,
        category=category,
        playbook_text=playbook,
        eur_per_usd=fx,
    )
    book = build_short_term_book(
        budget_eur=budget_eur,
        candles_by_symbol=candles_by_symbol,
        last_eur=last_eur,
        category=category,
        eur_per_usd=fx,
    )
    buys = [s for s in signals if s.primary_signal == SignalType.BUY]
    sells = [s for s in signals if s.primary_signal == SignalType.SELL]
    chg_btc = float(btc_q.change_24h_pct) if btc_q else 0.0
    raw_quotes = list(quotes)
    report = DailyReport(
        market_summary=(
            f"Live snapshot {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
            f"BTC €{btc_eur:,.0f} ({chg_btc:+.1f}% 24h). "
            f"News bucket: {category}. 7d bounce: {bounce*100:.1f}%. "
            f"Fear & Greed: {fg}."
        ),
        overall_sentiment=0.0 if fg is None else (fg - 50) / 50.0,
        fear_greed_index=fg,
        top_buys=buys,
        top_sells=sells,
        news_context=context,
        starter_plan=plan,
        short_term_book=book,
        live_quotes=raw_quotes,
        risk_notes=[
            playbook,
            f"€{budget_eur:.0f} starter: {plan.action} {plan.symbol}. Stop €{plan.stop_loss_eur}.",
            "Short-term sleeve: " + ", ".join(f"{l.symbol} €{l.alloc_eur:.0f}" for l in book.legs),
        ],
    )
    report.live_quotes = annotate_quotes(report.live_quotes, report)
    store.save_report(report.model_dump(mode="json"))
    return report
