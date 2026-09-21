"""Map price dips to news type and turn that into a small-account action.

Historical analogs are well-documented BTC drawdowns. Live headlines are
classified into the same buckets so a current dip can be compared with
what usually happened next (buy, wait, or avoid).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.models.schemas import (
    DipAnalog,
    DipEvent,
    NewsHeadline,
    NewsItem,
    OHLCV,
    ShortTermBook,
    StarterPlan,
    TradeLeg,
    TradeSignal,
)

# Category → typical post-dip behaviour for majors (not the crashing token).
PLAYBOOK: Dict[str, Dict[str, str]] = {
    "war": {
        "action": "BUY",
        "rule": (
            "Geopolitical / war headlines usually produce a 1–3 day risk-off dip "
            "in Bitcoin that is bought back if no exchange or protocol is broken."
        ),
    },
    "macro": {
        "action": "BUY",
        "rule": (
            "Fed hikes, yields, and equity risk-off often overshoot. Buy majors "
            "once the first panic candle is in, unless credit is failing."
        ),
    },
    "regulation": {
        "action": "WAIT",
        "rule": (
            "A delayed bill or SEC headline is usually noise. A hard country ban "
            "or exchange shutdown can grind lower for weeks — wait for a higher low."
        ),
    },
    "hack": {
        "action": "AVOID",
        "rule": (
            "If the hacked protocol is the coin you would buy, stay out. Majors "
            "usually dip and recover; the exploited token often does not."
        ),
    },
    "protocol_failure": {
        "action": "AVOID",
        "rule": "Stablecoin or L1 failure is contagion. Do not catch the first knife.",
    },
    "exchange": {
        "action": "WAIT",
        "rule": (
            "Exchange insolvency: wait until withdrawals elsewhere look normal, "
            "then buy BTC/ETH — never the failed venue's token."
        ),
    },
    "banking": {
        "action": "BUY",
        "rule": "Banking / USDC-style scares that get backstopped have historically been fast buys.",
    },
    "tech": {
        "action": "WAIT",
        "rule": "Known upgrades and ETF launches are often sell-the-news. Do not chase a squeeze.",
    },
    "credit": {
        "action": "WAIT",
        "rule": "Lender / hedge-fund contagion keeps leaking. Wait for forced sellers to finish.",
    },
}

# Documented BTC-market dips used as analogs. Returns are approximate, in percent.
HISTORICAL_DIPS: List[Dict[str, Any]] = [
    {
        "date": "2020-03-12",
        "symbol": "BTC",
        "drop_pct": -39.0,
        "headline": "COVID-19 liquidity crash and lockdowns",
        "category": "macro",
        "bounce_7d_pct": 28.0,
        "bounce_30d_pct": 45.0,
        "lesson": "Systemic fear with nothing broken in crypto itself — historically a buy.",
        "typical_action": "BUY",
    },
    {
        "date": "2021-05-19",
        "symbol": "BTC",
        "drop_pct": -30.0,
        "headline": "China mining ban and Tesla/Musk FUD",
        "category": "regulation",
        "bounce_7d_pct": 8.0,
        "bounce_30d_pct": -15.0,
        "lesson": "A real mining/regulatory ban can grind lower for weeks. Do not buy the first red day.",
        "typical_action": "WAIT",
    },
    {
        "date": "2022-02-24",
        "symbol": "BTC",
        "drop_pct": -8.0,
        "headline": "Russia invades Ukraine",
        "category": "war",
        "bounce_7d_pct": 12.0,
        "bounce_30d_pct": 18.0,
        "lesson": "War shocks in Bitcoin have usually been bought within days.",
        "typical_action": "BUY",
    },
    {
        "date": "2022-05-09",
        "symbol": "BTC",
        "drop_pct": -15.0,
        "headline": "Terra/LUNA and UST collapse",
        "category": "protocol_failure",
        "bounce_7d_pct": -10.0,
        "bounce_30d_pct": -25.0,
        "lesson": "When a major protocol or stablecoin breaks, selling continues. Avoid related coins.",
        "typical_action": "AVOID",
    },
    {
        "date": "2022-06-13",
        "symbol": "BTC",
        "drop_pct": -18.0,
        "headline": "Celsius and Three Arrows Capital credit contagion",
        "category": "credit",
        "bounce_7d_pct": 5.0,
        "bounce_30d_pct": -8.0,
        "lesson": "Credit contagion needs time. Wait until failed firms stop bleeding.",
        "typical_action": "WAIT",
    },
    {
        "date": "2022-11-08",
        "symbol": "BTC",
        "drop_pct": -25.0,
        "headline": "FTX insolvency",
        "category": "exchange",
        "bounce_7d_pct": -6.0,
        "bounce_30d_pct": 8.0,
        "lesson": "Exchange failure: wait a week, then buy majors — not the failed venue's token.",
        "typical_action": "WAIT",
    },
    {
        "date": "2023-03-11",
        "symbol": "BTC",
        "drop_pct": -8.0,
        "headline": "SVB collapse and USDC depeg",
        "category": "banking",
        "bounce_7d_pct": 22.0,
        "bounce_30d_pct": 30.0,
        "lesson": "Banking/stablecoin scare that gets backstopped is usually a fast buy.",
        "typical_action": "BUY",
    },
    {
        "date": "2023-10-07",
        "symbol": "BTC",
        "drop_pct": -4.0,
        "headline": "Hamas attack on Israel — weekend risk-off",
        "category": "war",
        "bounce_7d_pct": 6.0,
        "bounce_30d_pct": 18.0,
        "lesson": "Weekend war headlines: shallow dip, typically bought quickly.",
        "typical_action": "BUY",
    },
    {
        "date": "2024-01-12",
        "symbol": "BTC",
        "drop_pct": -18.0,
        "headline": "Spot Bitcoin ETF launch sell-the-news and GBTC unlocks",
        "category": "tech",
        "bounce_7d_pct": 2.0,
        "bounce_30d_pct": 15.0,
        "lesson": "Known bullish events often dump first. Wait 1–3 days after the headline.",
        "typical_action": "WAIT",
    },
    {
        "date": "2024-04-13",
        "symbol": "BTC",
        "drop_pct": -8.0,
        "headline": "Iran–Israel missile exchange",
        "category": "war",
        "bounce_7d_pct": 7.0,
        "bounce_30d_pct": 5.0,
        "lesson": "Direct war headlines: buy the first red candle unless oil breaks risk markets for weeks.",
        "typical_action": "BUY",
    },
    {
        "date": "2024-08-05",
        "symbol": "BTC",
        "drop_pct": -16.0,
        "headline": "Japan yen carry-trade unwind and Nikkei crash",
        "category": "macro",
        "bounce_7d_pct": 12.0,
        "bounce_30d_pct": 18.0,
        "lesson": "Macro liquidation cascades overshoot. Bounce is fast if crypto itself is not broken.",
        "typical_action": "BUY",
    },
    {
        "date": "2026-09-16",
        "symbol": "BTC",
        "drop_pct": -8.0,
        "headline": "Senate blocked CLARITY Act + first Fed hike since 2023; Iran war overlay",
        "category": "regulation",
        "bounce_7d_pct": 12.0,
        "bounce_30d_pct": None,
        "lesson": (
            "Regulation delay plus a hawkish Fed, with no protocol break, was bought within a week. "
            "That is the dip that already happened — chasing the squeeze is a different trade."
        ),
        "typical_action": "BUY",
    },
]

_CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "war": (
        "war", "invasion", "missile", "airstrike", "israel", "iran", "ukraine",
        "russia", "hamas", "hezbollah", "geopolit", "conflict", "ceasefire",
        "troops", "nato", "bomb", "strike on",
    ),
    "regulation": (
        "sec", "cftc", "clarity act", "mica", "ban", "lawsuit", "senate",
        "congress", "regulator", "license", "compliance", "etf approval",
        "etf rejected", "white house",
    ),
    "macro": (
        "fed ", "federal reserve", "fomc", "rate hike", "interest rate", "inflation", "treasury",
        "recession", "jobs report", "cpi", "tariff", "oil price", "dxy",
        "hawkish", "yield",
    ),
    "hack": (
        "hack", "exploit", "breach", "drained", "stolen", "rug pull", "vulnerability",
    ),
    "protocol_failure": (
        "depeg", "insolvent protocol", "luna", "ust collapse", "terra",
        "chain halt",
    ),
    "exchange": (
        "ftx", "exchange insolvency", "withdrawal halt", "proof of reserves",
        "paused withdrawals",
    ),
    "banking": (
        "svb", "silicon valley bank", "usdc depeg", "bank failure", "fdic",
    ),
    "credit": (
        "celsius", "three arrows", "3ac", "margin call", "lender collapse",
    ),
    "tech": (
        "upgrade", "hard fork", "mainnet", "etf inflow", "partnership",
        "adoption", "halving", "tokenized stock",
    ),
}

STARTER_VENUES = {
    "BTC": "Kraken BTCEUR or Bitvavo BTC-EUR — SEPA deposit, spot only, no leverage.",
    "ETH": "Kraken ETHEUR or Bitvavo ETH-EUR — SEPA deposit, spot only, no leverage.",
    "SOL": "Kraken SOLEUR or Bitvavo SOL-EUR — slightly wider spread than BTC/ETH.",
    "XRP": "Kraken XRPEUR or Bitvavo XRP-EUR — liquid EUR pair, spot only.",
}

# Coin-specific: same news, different beta. Used to rank a short-term sleeve.
COIN_NEWS_ANALOGS: List[Dict[str, Any]] = [
    {
        "symbol": "XRP",
        "date": "2023-07-13",
        "headline": "SEC vs Ripple partial win — XRP not a security in secondary sales",
        "category": "regulation",
        "drop_pct": 0.0,
        "bounce_7d_pct": 70.0,
        "lesson": "XRP overshoots on US legal headlines in both directions.",
        "typical_action": "BUY",
    },
    {
        "symbol": "XRP",
        "date": "2026-09-16",
        "headline": "CLARITY Act cloture fails — XRP −8% to −10%, worst among majors",
        "category": "regulation",
        "drop_pct": -10.0,
        "bounce_7d_pct": 15.0,
        "lesson": "XRP is the regulation-beta trade. It dumped twice as hard as BTC on the vote, then bounced with the squeeze.",
        "typical_action": "BUY",
    },
    {
        "symbol": "SOL",
        "date": "2022-11-08",
        "headline": "FTX collapse — Solana uniquely linked to SBF/Alameda",
        "category": "exchange",
        "drop_pct": -40.0,
        "bounce_7d_pct": -15.0,
        "lesson": "When the news is FTX-like and SOL-specific, do not buy SOL. When the news is macro/regulation, SOL is high-beta BTC.",
        "typical_action": "AVOID",
    },
    {
        "symbol": "SOL",
        "date": "2026-09-16",
        "headline": "CLARITY fail −3.7%; later SOL ETF inflows while ETH saw outflows",
        "category": "regulation",
        "drop_pct": -5.0,
        "bounce_7d_pct": 22.0,
        "lesson": "SOL was not uniquely hurt by the bill and then attracted ETF money. Highest short-term beta of the liquid names.",
        "typical_action": "BUY",
    },
    {
        "symbol": "ETH",
        "date": "2023-03-11",
        "headline": "SVB / USDC depeg — ETH sold with DeFi, then snapped back",
        "category": "banking",
        "drop_pct": -12.0,
        "bounce_7d_pct": 20.0,
        "lesson": "ETH is the DeFi/stablecoin beta. Banking scares: buy. Plain BTC squeezes: ETH often lags.",
        "typical_action": "BUY",
    },
    {
        "symbol": "ETH",
        "date": "2026-09-16",
        "headline": "CLARITY −5%; this week ETH ETFs −$140m while SOL/XRP ETFs took inflows",
        "category": "regulation",
        "drop_pct": -5.0,
        "bounce_7d_pct": 14.0,
        "lesson": "Institutional money did not follow ETH on this bounce. Skip it on a €100 short-term ticket.",
        "typical_action": "WAIT",
    },
]

LIQUID_STARTER = ("BTC", "ETH", "SOL")
CHASE_BOUNCE_PCT = 0.06  # already rallied this much off the 7d low → do not chase


def classify_text(text: str) -> str:
    blob = (text or "").lower()
    scores: Dict[str, int] = {}
    for category, words in _CATEGORY_KEYWORDS.items():
        hit = sum(1 for w in words if w in blob)
        if hit:
            scores[category] = hit
    if not scores:
        return "other"
    return max(scores, key=scores.get)


def historical_analogs(category: str, limit: int = 4) -> List[DipAnalog]:
    rows = [e for e in HISTORICAL_DIPS if e["category"] == category]
    if not rows and category == "regulation":
        rows = [e for e in HISTORICAL_DIPS if e["category"] in ("regulation", "macro")]
    if not rows:
        rows = [e for e in HISTORICAL_DIPS if e["typical_action"] == "BUY"][:limit]
    out: List[DipAnalog] = []
    for e in rows[:limit]:
        out.append(
            DipAnalog(
                date=e["date"],
                symbol=e["symbol"],
                drop_pct=e["drop_pct"],
                headline=e["headline"],
                category=e["category"],
                bounce_7d_pct=e.get("bounce_7d_pct"),
                bounce_30d_pct=e.get("bounce_30d_pct"),
                lesson=e["lesson"],
                typical_action=e["typical_action"],
            )
        )
    return out


def detect_dips(candles: Sequence[OHLCV], *, min_drop: float = 0.05, lookback: int = 12) -> List[DipEvent]:
    """Find troughs where close fell at least ``min_drop`` from a recent peak."""
    if len(candles) < lookback + 2:
        return []
    events: List[DipEvent] = []
    closes = [float(c.close) for c in candles]
    for i in range(lookback, len(candles)):
        window = closes[i - lookback : i]
        peak = max(window)
        price = closes[i]
        if peak <= 0:
            continue
        drop = price / peak - 1.0
        if drop > -min_drop:
            continue
        # trough: next bar is higher (or last bar)
        is_last = i == len(candles) - 1
        next_up = is_last or closes[i + 1] >= price
        prev_down = closes[i - 1] >= price
        if not (next_up and prev_down):
            continue
        bounce_7d = None
        if i + 7 < len(closes) and price > 0:
            bounce_7d = (closes[i + 7] / price - 1.0) * 100.0
        ts = candles[i].open_time
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        events.append(
            DipEvent(
                date=ts.date().isoformat(),
                symbol=candles[i].symbol,
                drop_pct=round(drop * 100.0, 2),
                bounce_7d_pct=None if bounce_7d is None else round(bounce_7d, 2),
                matched_event=_match_catalog_headline(ts),
                news_type=_match_catalog_category(ts),
            )
        )
    # keep the most recent handful
    return events[-8:]


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _match_catalog(ts: datetime) -> Optional[Dict[str, Any]]:
    for e in HISTORICAL_DIPS:
        d = _parse_date(e["date"])
        if d is None:
            continue
        if abs((ts - d).days) <= 3:
            return e
    return None


def _match_catalog_headline(ts: datetime) -> Optional[str]:
    e = _match_catalog(ts)
    return None if e is None else str(e["headline"])


def _match_catalog_category(ts: datetime) -> Optional[str]:
    e = _match_catalog(ts)
    return None if e is None else str(e["category"])


def headlines_from_news(items: Iterable[NewsItem], limit: int = 12) -> List[NewsHeadline]:
    out: List[NewsHeadline] = []
    seen = set()
    for n in items:
        title = (n.title or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        out.append(
            NewsHeadline(
                title=title,
                url=n.url,
                category=classify_text(title),
                sentiment=n.sentiment,
                published_at=n.published_at,
                source=n.source,
            )
        )
        if len(out) >= limit:
            break
    return out


def dominant_category(headlines: Sequence[NewsHeadline]) -> str:
    counts: Dict[str, int] = {}
    for h in headlines:
        if h.category == "other":
            continue
        counts[h.category] = counts.get(h.category, 0) + 1
    if not counts:
        return "macro"
    return max(counts, key=counts.get)


def category_mix(headlines: Sequence[NewsHeadline]) -> Dict[str, int]:
    mix: Dict[str, int] = {}
    for h in headlines:
        mix[h.category] = mix.get(h.category, 0) + 1
    return mix


def seven_day_bounce(candles: Sequence[OHLCV]) -> float:
    if len(candles) < 8:
        return 0.0
    closes = [float(c.close) for c in candles[-8:]]
    trough = min(closes[:-1])
    last = closes[-1]
    if trough <= 0:
        return 0.0
    return last / trough - 1.0


def resolve_verdict(
    category: str,
    *,
    bounce_pct: float,
    fear_greed: Optional[int],
) -> str:
    base = PLAYBOOK.get(category, PLAYBOOK["macro"])["action"]
    if base == "AVOID":
        return "AVOID"
    if bounce_pct >= CHASE_BOUNCE_PCT:
        return "WAIT_PULLBACK"
    if fear_greed is not None and fear_greed >= 70 and bounce_pct > 0.03:
        return "WAIT_PULLBACK"
    if base == "WAIT":
        return "WAIT_PULLBACK" if bounce_pct >= 0.03 else "WAIT"
    if base == "BUY":
        return "BUY_NOW"
    return "WAIT_PULLBACK"


def _usd_to_eur(usd: float, eur_per_usd: float) -> float:
    return round(usd * eur_per_usd, 2)


def build_starter_plan(
    *,
    budget_eur: float,
    signals: Sequence[TradeSignal],
    btc_candles: Sequence[OHLCV],
    verdict: str,
    category: str,
    playbook_text: str,
    eur_per_usd: float,
) -> StarterPlan:
    budget = max(25.0, float(budget_eur))
    by_sym = {s.symbol.upper(): s for s in signals}
    symbol = "BTC"
    for cand in LIQUID_STARTER:
        sig = by_sym.get(cand)
        if sig and sig.primary_signal.value == "BUY" and cand == "BTC":
            symbol = cand
            break
    sig = by_sym.get(symbol)
    usd_price = float(sig.entry_price) if sig and sig.entry_price else (
        float(btc_candles[-1].close) if btc_candles else 0.0
    )
    price_eur = _usd_to_eur(usd_price, eur_per_usd) if usd_price else 0.0

    closes = [float(c.close) for c in btc_candles[-14:]] if btc_candles else []
    trough_usd = min(closes) if closes else usd_price * 0.92
    recent_high_usd = max(closes) if closes else usd_price
    trough_eur = _usd_to_eur(trough_usd, eur_per_usd)
    high_eur = _usd_to_eur(recent_high_usd, eur_per_usd)

    action = verdict
    if action == "WAIT":
        action = "WAIT_PULLBACK"

    # Limit zone: buy a retest of last week's dip, not the squeeze high.
    if action in ("WAIT_PULLBACK", "WAIT"):
        entry = round(trough_eur * 1.05, 2) if trough_eur else price_eur
        deploy_now = 0.0
        reserve = budget
        entry_note = (
            f"Do not market-buy the squeeze. Place a limit around €{entry:,.0f} "
            f"(retest of last week's dip). Keep the full €{budget:.0f} in cash until filled."
        )
    elif action == "AVOID":
        entry = price_eur
        deploy_now = 0.0
        reserve = budget
        entry_note = "News type historically keeps falling. Stay in euro cash."
    elif action == "SELL":
        entry = price_eur
        deploy_now = 0.0
        reserve = budget
        entry_note = "No new buy. If you already hold, trim into strength."
    else:
        # BUY_NOW — still cap first ticket so a 100 euro account is not all-in at the high.
        deploy_now = round(min(budget * 0.4, budget), 2)
        reserve = round(budget - deploy_now, 2)
        entry = price_eur
        entry_note = (
            f"Market is still near the dip. Deploy €{deploy_now:.0f} now in {symbol} spot; "
            f"keep €{reserve:.0f} for a deeper retest."
        )

    stop = round(trough_eur * 0.97, 2) if trough_eur else round(entry * 0.92, 2)
    if entry and stop >= entry:
        stop = round(entry * 0.92, 2)
    tp = round(max(high_eur * 1.02, entry * 1.12), 2) if entry else 0.0
    stop_pct = ((stop / entry) - 1.0) * 100 if entry else 0.0
    tp_pct = ((tp / entry) - 1.0) * 100 if entry else 0.0
    risk_eur = round(budget * abs(stop_pct) / 100.0, 2) if action == "BUY_NOW" else round(budget * abs(stop_pct) / 100.0, 2)
    coins = round((deploy_now or budget) / entry, 8) if entry else 0.0

    book = PLAYBOOK.get(category, PLAYBOOK["macro"])
    why = (
        f"{book['rule']} Current analog bucket: {category}. {playbook_text}"
    )
    invalidation = (
        f"Daily close below €{stop:,.0f} ({symbol} last-week low area). "
        "If a major exchange or stablecoin breaks, cancel the buy and sit in euro."
    )
    sell_rule = (
        f"Take profit near €{tp:,.0f} ({tp_pct:+.1f}%). "
        "If you already bought last week's dip, you may sell 30% into this squeeze and hold the rest. "
        "Do not short with a €100 account."
    )

    return StarterPlan(
        budget_eur=budget,
        action=action,
        symbol=symbol,
        venue=STARTER_VENUES.get(symbol, STARTER_VENUES["BTC"]),
        pair=f"{symbol}EUR",
        deploy_now_eur=deploy_now,
        reserve_eur=reserve,
        entry_price_eur=entry,
        entry_note=entry_note,
        stop_loss_eur=stop,
        stop_loss_pct=round(stop_pct, 2),
        risk_eur=round(abs(risk_eur), 2),
        take_profit_eur=tp,
        take_profit_pct=round(tp_pct, 2),
        coins_if_filled=coins,
        time_horizon="2–14 days",
        why=why,
        invalidation=invalidation,
        sell_rule=sell_rule,
    )


def coin_analogs(symbol: str, category: Optional[str] = None, limit: int = 2) -> List[DipAnalog]:
    rows = [e for e in COIN_NEWS_ANALOGS if e["symbol"] == symbol.upper()]
    if category:
        tagged = [e for e in rows if e["category"] == category]
        if tagged:
            rows = tagged
    out: List[DipAnalog] = []
    for e in rows[:limit]:
        out.append(
            DipAnalog(
                date=e["date"],
                symbol=e["symbol"],
                drop_pct=e["drop_pct"],
                headline=e["headline"],
                category=e["category"],
                bounce_7d_pct=e.get("bounce_7d_pct"),
                bounce_30d_pct=e.get("bounce_30d_pct"),
                lesson=e["lesson"],
                typical_action=e["typical_action"],
            )
        )
    return out


def _leg_from_candles(
    *,
    symbol: str,
    role: str,
    rank: int,
    alloc_eur: float,
    candles: Sequence[OHLCV],
    eur_per_usd: float,
    last_eur: Optional[float],
    category: str,
    extra_why: str,
) -> TradeLeg:
    bounce = seven_day_bounce(candles)
    verdict = resolve_verdict(category, bounce_pct=bounce, fear_greed=70)
    closes = [float(c.close) for c in candles[-14:]] if candles else []
    last_usd = float(closes[-1]) if closes else 0.0
    trough_usd = min(closes) if closes else last_usd * 0.92
    high_usd = max(closes) if closes else last_usd
    trough_eur = _usd_to_eur(trough_usd, eur_per_usd)
    last = last_eur if last_eur else _usd_to_eur(last_usd, eur_per_usd)
    tiny = last < 10
    entry = round(trough_eur * 1.05, 4 if tiny else 2)
    stop = round(trough_eur * 0.97, 4 if tiny else 2)
    if entry and stop >= entry:
        stop = round(entry * 0.92, 4 if tiny else 2)
    tp_mult = 1.12 if role == "core" else 1.18
    tp = round(max(_usd_to_eur(high_usd, eur_per_usd) * 1.02, entry * tp_mult), 4 if tiny else 2)
    stop_pct = ((stop / entry) - 1.0) * 100 if entry else 0.0
    tp_pct = ((tp / entry) - 1.0) * 100 if entry else 0.0
    analogs = coin_analogs(symbol, category)
    analog_txt = analogs[0].lesson if analogs else extra_why
    why = (
        f"{extra_why} 7d bounce {bounce*100:.1f}% — "
        + ("do not chase; limit on the retest." if bounce >= CHASE_BOUNCE_PCT else "still near the dip.")
    )
    return TradeLeg(
        symbol=symbol,
        role=role,
        rank=rank,
        action=verdict if verdict != "WAIT" else "WAIT_PULLBACK",
        alloc_eur=alloc_eur,
        pair=f"{symbol}EUR",
        venue=STARTER_VENUES.get(symbol, STARTER_VENUES["BTC"]),
        last_eur=last,
        bounce_7d_pct=round(bounce * 100.0, 1),
        entry_price_eur=entry,
        stop_loss_eur=stop,
        stop_loss_pct=round(stop_pct, 2),
        take_profit_eur=tp,
        take_profit_pct=round(tp_pct, 2),
        coins_if_filled=round(alloc_eur / entry, 6) if entry else 0.0,
        news_analog=analog_txt,
        why=why,
    )


def build_short_term_book(
    *,
    budget_eur: float,
    candles_by_symbol: Dict[str, Sequence[OHLCV]],
    last_eur: Dict[str, float],
    category: str,
    eur_per_usd: float,
) -> ShortTermBook:
    """Barbell: BTC core + one or two higher-beta alts. Never more than 3 names on €100."""
    budget = max(25.0, float(budget_eur))
    specs = [
        ("BTC", "core", 1, round(budget * 0.55, 2), "Anchor. Same news, lowest beta, tightest spread."),
        ("SOL", "short", 2, round(budget * 0.30, 2), "Short-term: ETF inflows this week; not uniquely hurt by CLARITY; historically 1.5–2× BTC on macro bounces."),
        ("XRP", "short", 3, round(budget * 0.15, 2), "Short-term: dumped 8–10% on the vote vs BTC ~3%. Regulation-beta leftover if we get a retest."),
    ]
    skipped = [
        "ETH — spot ETFs lost ~$140m this week while SOL/XRP funds took inflows; ETH is the laggard, not the short-term vehicle.",
        "DOGE — +12% today / +22% off the low is meme-beta. Fees and wicks eat a €100 ticket.",
        "BNB — followed BTC, no extra news edge.",
    ]
    legs: List[TradeLeg] = []
    for symbol, role, rank, alloc, why in specs:
        candles = candles_by_symbol.get(symbol) or []
        if not candles:
            skipped.append(f"{symbol} — no candles, skipped.")
            continue
        legs.append(
            _leg_from_candles(
                symbol=symbol,
                role=role,
                rank=rank,
                alloc_eur=alloc,
                candles=candles,
                eur_per_usd=eur_per_usd,
                last_eur=last_eur.get(symbol),
                category=category,
                extra_why=why,
            )
        )
    any_now = any(l.action == "BUY_NOW" for l in legs)
    deploy = round(sum(l.alloc_eur * 0.4 for l in legs if l.action == "BUY_NOW"), 2) if any_now else 0.0
    thesis = (
        "More coins do not maximize profit on €100 — fees and random wicks do. "
        "The edge is buying higher-beta names (SOL, XRP) on the same news dip that BTC already recovered, "
        "not chasing a 15–23% squeeze. All three legs are limit-on-retest until a fresh dip prints."
    )
    return ShortTermBook(
        thesis=thesis,
        budget_eur=budget,
        deploy_now_eur=deploy,
        reserve_eur=round(budget - deploy, 2),
        legs=legs,
        skipped=skipped,
    )


def build_playbook_text(category: str, analogs: Sequence[DipAnalog], bounce_pct: float) -> str:
    if not analogs:
        return PLAYBOOK.get(category, PLAYBOOK["macro"])["rule"]
    avg7 = [a.bounce_7d_pct for a in analogs if a.bounce_7d_pct is not None]
    avg_txt = f" Average 7-day bounce after similar news: {sum(avg7)/len(avg7):+.1f}%." if avg7 else ""
    bounce_txt = (
        f" Price has already bounced {bounce_pct*100:.1f}% off the 7-day low, so the dip itself is used."
        if bounce_pct >= CHASE_BOUNCE_PCT
        else f" Price is only {bounce_pct*100:.1f}% off the 7-day low — still close to the dip."
    )
    names = "; ".join(f"{a.date} {a.headline}" for a in analogs[:3])
    return f"Closest past episodes: {names}.{avg_txt}{bounce_txt}"
