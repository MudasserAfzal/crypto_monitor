"""Fetch live BTC candles + news and emit the €100 starter plan (no ML stack)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.schemas import (  # noqa: E402
    DailyReport,
    Direction,
    NewsItem,
    OHLCV,
    SignalType,
    TimeHorizon,
    TradeSignal,
)
from app.signals.news_dips import (  # noqa: E402
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


def _klines(symbol: str) -> list[OHLCV]:
    url = "https://api.binance.com/api/v3/klines"
    r = httpx.get(url, params={"symbol": f"{symbol}USDT", "interval": "1d", "limit": 400}, timeout=30)
    r.raise_for_status()
    out: list[OHLCV] = []
    for row in r.json():
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


FALLBACK_HEADLINES = [
    "Senate blocks CLARITY Act 49-50 as Bitcoin slides toward $75,000",
    "Federal Reserve raises rates 25 bps to 3.75%-4.00%, first hike since 2023",
    "Hopes of US-Iran war de-escalation and falling oil prices lift risk assets",
    "SEC issues five-year innovation exemption for tokenized-stock trading",
    "Spot Bitcoin ETFs swing back to inflows after last week's outflows",
    "Short squeeze liquidates hundreds of millions as Bitcoin reclaims $85,000",
]


def _news() -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        r = httpx.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={"public": "true", "currencies": "BTC,ETH,SOL"},
            timeout=30,
            headers={"User-Agent": "CryptoSignal/1.0"},
        )
        r.raise_for_status()
        for post in r.json().get("results") or []:
            items.append(
                NewsItem(
                    title=post.get("title") or "",
                    url=post.get("url"),
                    source="cryptopanic",
                    published_at=None,
                    currencies=[c.get("code", "") for c in (post.get("currencies") or [])],
                )
            )
    except Exception as exc:
        print("CryptoPanic failed:", exc)
    if not items:
        for title in FALLBACK_HEADLINES:
            items.append(NewsItem(title=title, source="manual_wire", currencies=["BTC"]))
    return items


def _prices() -> dict:
    r = httpx.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "bitcoin,ethereum,solana,ripple", "vs_currencies": "eur,usd", "include_24hr_change": "true"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _fg() -> int | None:
    try:
        r = httpx.get("https://api.alternative.me/fng/", params={"limit": 1}, timeout=20)
        r.raise_for_status()
        return int((r.json().get("data") or [{}])[0].get("value") or 50)
    except Exception:
        return None


def main() -> int:
    prices = _prices()
    btc_usd = float(prices["bitcoin"]["usd"])
    btc_eur = float(prices["bitcoin"]["eur"])
    fx = btc_eur / btc_usd
    candles = _klines("BTC")
    candles_by_symbol = {"BTC": candles}
    for extra in ("ETH", "SOL", "XRP"):
        try:
            candles_by_symbol[extra] = _klines(extra)
        except Exception as exc:
            print("klines failed", extra, exc)
    news = _news()
    fg = _fg()
    headlines = headlines_from_news(news)
    category = dominant_category(headlines)
    analogs = historical_analogs(category)
    bounce = seven_day_bounce(candles)
    verdict = resolve_verdict(category, bounce_pct=bounce, fear_greed=fg)
    playbook = build_playbook_text(category, analogs, bounce)
    recent = detect_dips(candles)

    signals = []
    mapping = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple"}
    for sym, cid in mapping.items():
        px = float(prices[cid]["usd"])
        chg = float(prices[cid].get("usd_24h_change") or 0)
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

    buys = [s for s in signals if s.primary_signal == SignalType.BUY]
    sells = [s for s in signals if s.primary_signal == SignalType.SELL]

    from app.models.schemas import NewsDipContext

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
        budget_eur=100,
        signals=signals,
        btc_candles=candles,
        verdict=verdict,
        category=category,
        playbook_text=playbook,
        eur_per_usd=fx,
    )
    last_eur = {
        "BTC": float(prices["bitcoin"]["eur"]),
        "ETH": float(prices["ethereum"]["eur"]),
        "SOL": float(prices["solana"]["eur"]),
        "XRP": float(prices["ripple"]["eur"]),
    }
    book = build_short_term_book(
        budget_eur=100,
        candles_by_symbol=candles_by_symbol,
        last_eur=last_eur,
        category=category,
        eur_per_usd=fx,
    )

    report = DailyReport(
        market_summary=(
            f"Live snapshot {datetime.now(timezone.utc).isoformat()}. "
            f"BTC €{btc_eur:,.0f} ({prices['bitcoin'].get('eur_24h_change'):+.1f}% 24h). "
            f"News bucket: {category}. 7d bounce off trough: {bounce*100:.1f}%. "
            f"Fear & Greed: {fg}."
        ),
        overall_sentiment=0.0 if fg is None else (fg - 50) / 50,
        fear_greed_index=fg,
        top_buys=buys,
        top_sells=sells,
        news_context=context,
        starter_plan=plan,
        short_term_book=book,
        risk_notes=[
            playbook,
            f"€100 starter: {plan.action} {plan.symbol} on {plan.pair}. Stop €{plan.stop_loss_eur}.",
            f"Short-term sleeve: " + ", ".join(f"{l.symbol} €{l.alloc_eur:.0f}" for l in book.legs),
        ],
    )

    data_dir = ROOT / "data"
    out = report.model_dump(mode="json")
    (data_dir / "reports" / "latest.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    (data_dir / "latest_report.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "verdict": verdict,
        "category": category,
        "bounce_pct": round(bounce * 100, 2),
        "fg": fg,
        "btc_eur": btc_eur,
        "btc_usd": btc_usd,
        "plan": plan.model_dump(),
        "book": book.model_dump(),
        "headlines": [h.title for h in headlines[:8]],
        "recent_dips": [d.model_dump() for d in recent[-5:]],
        "analogs": [a.model_dump() for a in analogs],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
