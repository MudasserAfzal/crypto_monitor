"""News-vs-dip classification and €100 starter-plan sizing."""

from datetime import datetime, timezone

from app.models.schemas import OHLCV, SignalType, Direction, TimeHorizon, TradeSignal
from app.signals.news_dips import (
    build_starter_plan,
    classify_text,
    detect_dips,
    resolve_verdict,
    seven_day_bounce,
)


def test_classify_war_regulation_hack():
    assert classify_text("Iran-Israel missile exchange rattles markets") == "war"
    assert classify_text("Senate blocks the CLARITY Act in a 49-50 vote") == "regulation"
    assert classify_text("Protocol exploit drains $40m from the bridge") == "hack"
    assert classify_text("Fed rate hike and hawkish Treasury yields") == "macro"


def test_resolve_verdict_does_not_chase_a_bounce():
    assert resolve_verdict("war", bounce_pct=0.12, fear_greed=40) == "WAIT_PULLBACK"
    assert resolve_verdict("war", bounce_pct=0.01, fear_greed=30) == "BUY_NOW"
    assert resolve_verdict("hack", bounce_pct=0.01, fear_greed=20) == "AVOID"


def _candle(i: int, close: float, symbol: str = "BTC") -> OHLCV:
    return OHLCV(
        symbol=symbol,
        timeframe="1d",
        open_time=datetime(2026, 9, 1, tzinfo=timezone.utc).replace(day=min(i + 1, 28)),
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=1000,
        source="test",
    )


def test_detect_dip_and_bounce():
    prices = [100] * 12 + [88, 87, 92, 95]
    candles = [_candle(i, p) for i, p in enumerate(prices, start=1)]
    events = detect_dips(candles, min_drop=0.05, lookback=8)
    assert events
    assert events[-1].drop_pct <= -5
    assert seven_day_bounce(candles) > 0


def test_starter_plan_waits_and_sizes_stop():
    sig = TradeSignal(
        symbol="BTC",
        primary_signal=SignalType.BUY,
        secondary_signal=Direction.LONG,
        confidence=40,
        entry_price=85000,
        take_profit=95000,
        stop_loss=74000,
        time_horizon=TimeHorizon.MEDIUM,
    )
    prices = [82000] * 6 + [75000, 76000, 80000, 85000]
    candles = [_candle(i, p) for i, p in enumerate(prices, start=1)]
    plan = build_starter_plan(
        budget_eur=100,
        signals=[sig],
        btc_candles=candles,
        verdict="WAIT_PULLBACK",
        category="regulation",
        playbook_text="Regulation plus Fed hike analog.",
        eur_per_usd=0.87,
    )
    assert plan.symbol == "BTC"
    assert plan.deploy_now_eur == 0
    assert plan.reserve_eur == 100
    assert plan.stop_loss_eur < plan.entry_price_eur
    assert plan.pair == "BTCEUR"
    assert "Kraken" in plan.venue or "Bitvavo" in plan.venue


def test_starter_plan_buy_now_is_partial():
    sig = TradeSignal(
        symbol="BTC",
        primary_signal=SignalType.BUY,
        secondary_signal=Direction.LONG,
        confidence=50,
        entry_price=76000,
        time_horizon=TimeHorizon.SHORT,
    )
    candles = [_candle(i, 76000) for i in range(1, 12)]
    plan = build_starter_plan(
        budget_eur=100,
        signals=[sig],
        btc_candles=candles,
        verdict="BUY_NOW",
        category="war",
        playbook_text="War dip analog.",
        eur_per_usd=0.87,
    )
    assert plan.deploy_now_eur == 40
    assert plan.reserve_eur == 60
    assert plan.coins_if_filled and plan.coins_if_filled > 0


def test_short_term_book_is_three_legs_not_six():
    from app.signals.news_dips import build_short_term_book

    def series(symbol: str, last: float, trough: float):
        vals = [last] * 10 + [trough, trough * 1.02, last]
        return [_candle(i, p, symbol) for i, p in enumerate(vals, start=1)]

    book = build_short_term_book(
        budget_eur=100,
        candles_by_symbol={
            "BTC": series("BTC", 85000, 75000),
            "SOL": series("SOL", 117, 96),
            "XRP": series("XRP", 1.49, 1.25),
            "ETH": series("ETH", 2700, 2360),
        },
        last_eur={"BTC": 75000, "SOL": 103, "XRP": 1.3, "ETH": 2380},
        category="regulation",
        eur_per_usd=0.87,
    )
    assert [l.symbol for l in book.legs] == ["BTC", "SOL", "XRP"]
    assert book.legs[0].alloc_eur == 55
    assert book.legs[1].alloc_eur == 30
    assert book.legs[2].alloc_eur == 15
    assert book.deploy_now_eur == 0
    assert any("ETH" in s for s in book.skipped)
    assert any("DOGE" in s for s in book.skipped)
