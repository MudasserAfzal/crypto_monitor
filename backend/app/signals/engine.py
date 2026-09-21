"""Signal generation — weighted multi-factor model, confidence, risk."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.collectors.aggregator import aggregator
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.storage import store
from app.indicators.engine import engine as ta_engine
from app.indicators.volatility import true_range
from app.indicators.base import candles_to_df
from app.ml.models import MLSignalModel, LSTMLiteModel
from app.models.schemas import (
    DailyReport,
    Direction,
    IndicatorSignal,
    NewsDipContext,
    OHLCV,
    OnChainMetric,
    SentimentData,
    SignalType,
    TimeHorizon,
    TradeSignal,
    Ticker,
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


class RiskAssessor:
    """Volatility-adjusted sizing, stops, correlation-aware allocation."""

    def suggest_levels(
        self,
        candles: List[OHLCV],
        direction: Direction,
        confidence: float,
    ) -> Dict[str, Optional[float]]:
        if not candles:
            return {"entry": None, "take_profit": None, "stop_loss": None, "risk_reward": None, "atr": None}

        df = candles_to_df(candles)
        price = float(df["close"].iloc[-1])
        atr_series = true_range(df).ewm(span=14, adjust=False).mean()
        atr = float(atr_series.iloc[-1]) if len(atr_series) else price * 0.02

        # Wider stops for lower confidence
        sl_mult = 1.5 + (1 - confidence / 100) * 1.0
        tp_mult = 2.5 + (confidence / 100) * 1.5

        if direction == Direction.LONG:
            stop = price - atr * sl_mult
            tp = price + atr * tp_mult
        elif direction == Direction.SHORT:
            stop = price + atr * sl_mult
            tp = price - atr * tp_mult
        else:
            stop = price - atr * sl_mult
            tp = price + atr * tp_mult

        risk = abs(price - stop)
        reward = abs(tp - price)
        rr = reward / risk if risk > 0 else None

        # Position sizing: Kelly-ish fraction capped
        win_prob = confidence / 100
        kelly = max(0, win_prob - (1 - win_prob) / (rr or 1.5))
        allocation = min(8.0, max(0.5, kelly * 20))  # % of portfolio

        leverage = 1.0
        if confidence >= 75 and (rr or 0) >= 2:
            leverage = 2.0
        elif confidence >= 85 and (rr or 0) >= 2.5:
            leverage = 3.0

        return {
            "entry": price,
            "take_profit": tp,
            "stop_loss": stop,
            "risk_reward": rr,
            "atr": atr,
            "allocation_pct": allocation,
            "leverage": leverage,
        }

    def correlation_penalty(self, symbol: str, selected: List[str], returns_map: Dict[str, List[float]]) -> float:
        """Reduce allocation if highly correlated with already-selected buys."""
        if symbol not in returns_map or not selected:
            return 1.0
        a = np.array(returns_map[symbol])
        penalties = []
        for other in selected:
            if other not in returns_map:
                continue
            b = np.array(returns_map[other])
            n = min(len(a), len(b))
            if n < 10:
                continue
            corr = np.corrcoef(a[-n:], b[-n:])[0, 1]
            if np.isnan(corr):
                continue
            if corr > 0.7:
                penalties.append(corr)
        if not penalties:
            return 1.0
        return max(0.3, 1.0 - 0.4 * max(penalties))


class SignalEngine:
    """Combines technical, sentiment, on-chain, volume, and macro into TradeSignals."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.risk = RiskAssessor()

    def _sentiment_score(self, sentiments: List[SentimentData], symbol: str) -> float:
        if not sentiments:
            return 0.0
        relevant = [s for s in sentiments if s.symbol in (None, symbol)]
        if not relevant:
            relevant = sentiments
        return float(np.mean([s.score for s in relevant]))

    def _onchain_score(self, metrics: List[OnChainMetric], symbol: str) -> float:
        if not metrics:
            return 0.0
        scores = []
        for m in metrics:
            if m.symbol not in (symbol, "MARKET", "ETH") and m.symbol != symbol:
                continue
            if m.metric == "gas_gwei":
                # High gas = network congestion / activity — mild bullish for ETH ecosystem
                scores.append(0.2 if m.value > 30 else -0.1)
            elif m.metric == "defi_tvl":
                scores.append(0.1)  # presence of data; trend would need history
            elif m.metric == "github_activity":
                scores.append(0.3 if m.symbol == symbol else 0.0)
        return float(np.clip(np.mean(scores) if scores else 0.0, -1, 1))

    def _volume_score(self, indicators: List[IndicatorSignal]) -> float:
        vol_names = {"VolumeMA", "CMF", "ADLine", "VPT", "VWAP", "OBV", "MFI"}
        vol_sigs = [s for s in indicators if s.name in vol_names]
        if not vol_sigs:
            return 0.0
        buy = sum(s.strength for s in vol_sigs if s.signal == SignalType.BUY)
        sell = sum(s.strength for s in vol_sigs if s.signal == SignalType.SELL)
        total = buy + sell + 1e-9
        return (buy - sell) / total

    def _macro_score(self, macro: Dict[str, Any]) -> float:
        return float(macro.get("risk_score") or 0.0)

    def _ml_score(self, candles: List[OHLCV]) -> float:
        """Train a per-symbol RF/XGB model on candles and return a [-1, 1] score."""
        if len(candles) < 80:
            return 0.0
        try:
            model = MLSignalModel()
            metrics = model.fit(candles)
            if metrics.get("accuracy", 0) < 0.45:
                # Model accuracy too low to trust — return neutral
                return 0.0
            sig, conf = model.predict(candles)
            if sig == SignalType.BUY:
                return conf
            if sig == SignalType.SELL:
                return -conf
        except Exception as exc:
            logger.debug("ML model failed: %s", exc)
        return 0.0

    def _composite(
        self,
        technical: float,
        sentiment: float,
        onchain: float,
        volume: float,
        macro: float,
        ml: float = 0.0,
    ) -> float:
        s = self.settings
        return (
            s.weight_technical * technical
            + s.weight_sentiment * sentiment
            + s.weight_onchain * onchain
            + s.weight_volume * volume
            + s.weight_macro * macro
            + s.weight_ml * ml
        )

    def _confidence(self, indicators: List[IndicatorSignal], composite: float) -> float:
        strong = [s for s in indicators if s.strength >= 0.65 and s.signal != SignalType.HOLD]
        weak = [s for s in indicators if s.strength < 0.65 or s.signal == SignalType.HOLD]
        agreement = abs(composite)
        if not strong and not weak:
            base = agreement * 50
        else:
            ratio = len(strong) / max(1, len(strong) + len(weak) * 0.5)
            base = (0.4 * ratio + 0.6 * agreement) * 100
        # Align confidence with direction strength
        return float(np.clip(base, 5, 98))

    def _direction(self, composite: float, confidence: float) -> Tuple[SignalType, Direction]:
        if composite >= 0.08 and confidence >= 35:
            return SignalType.BUY, Direction.LONG
        if composite <= -0.08 and confidence >= 35:
            # Strong bearish → SELL for spots, SHORT for derivatives
            return SignalType.SELL, Direction.SHORT if confidence >= 50 else Direction.NONE
        return SignalType.HOLD, Direction.NONE

    def _horizon(self, tf_scores: Dict[str, float]) -> TimeHorizon:
        short = abs(tf_scores.get("1h", 0)) + abs(tf_scores.get("4h", 0))
        long = abs(tf_scores.get("1d", 0)) + abs(tf_scores.get("1w", 0))
        if short > long * 1.3:
            return TimeHorizon.SHORT
        if long > short * 1.3:
            return TimeHorizon.LONG
        return TimeHorizon.MEDIUM

    async def analyze_symbol(
        self,
        symbol: str,
        *,
        sentiments: Optional[List[SentimentData]] = None,
        onchain: Optional[List[OnChainMetric]] = None,
        macro: Optional[Dict[str, Any]] = None,
        timeframes: Optional[List[str]] = None,
    ) -> TradeSignal:
        timeframes = timeframes or self.settings.signal_timeframe_list
        candles_by_tf: Dict[str, List[OHLCV]] = {}
        for tf in timeframes:
            candles_by_tf[tf] = await aggregator.get_ohlcv(symbol, timeframe=tf, limit=300)

        primary_tf = "1d" if "1d" in candles_by_tf else (timeframes[-1] if timeframes else "1d")
        primary_candles = candles_by_tf.get(primary_tf) or next(iter(candles_by_tf.values()), [])

        by_tf = ta_engine.analyze_multi_timeframe(candles_by_tf)
        mt = ta_engine.multi_tf_score(by_tf)
        technical = float(mt["score"])
        all_indicators = by_tf.get(primary_tf) or []
        # Flatten for volume scoring
        flat = [s for sigs in by_tf.values() for s in sigs]

        sentiments = sentiments if sentiments is not None else await aggregator.get_sentiment([symbol])
        onchain = onchain if onchain is not None else await aggregator.get_onchain([symbol])
        macro = macro if macro is not None else await aggregator.get_macro()

        sent = self._sentiment_score(sentiments, symbol)
        oc = self._onchain_score(onchain, symbol)
        vol = self._volume_score(flat)
        mac = self._macro_score(macro)
        ml = self._ml_score(primary_candles)
        composite = self._composite(technical, sent, oc, vol, mac, ml)
        confidence = self._confidence(all_indicators, composite)
        primary, secondary = self._direction(composite, confidence)

        levels = self.risk.suggest_levels(primary_candles, secondary if secondary != Direction.NONE else (
            Direction.LONG if primary == SignalType.BUY else Direction.SHORT if primary == SignalType.SELL else Direction.NONE
        ), confidence)

        tf_map = mt.get("by_timeframe") or {}
        if isinstance(tf_map, dict):
            horizon = self._horizon({k: float(v) for k, v in tf_map.items()})
        else:
            horizon = TimeHorizon.MEDIUM

        rationale_parts = [
            f"tech={technical:.2f}",
            f"sent={sent:.2f}",
            f"onchain={oc:.2f}",
            f"vol={vol:.2f}",
            f"macro={mac:.2f}",
            f"ml={ml:.2f}",
        ]
        strong = [s for s in all_indicators if s.strength >= 0.65 and s.signal != SignalType.HOLD]
        if strong:
            rationale_parts.append(
                "drivers: " + ", ".join(f"{s.name}({s.signal.value})" for s in sorted(strong, key=lambda x: -x.strength)[:5])
            )

        return TradeSignal(
            symbol=symbol,
            primary_signal=primary,
            secondary_signal=secondary,
            confidence=confidence,
            technical_score=technical,
            sentiment_score=sent,
            onchain_score=oc,
            volume_score=vol,
            macro_score=mac,
            ml_score=ml,
            composite_score=composite,
            entry_price=levels.get("entry"),
            take_profit=levels.get("take_profit"),
            stop_loss=levels.get("stop_loss"),
            risk_reward=levels.get("risk_reward"),
            leverage_suggestion=levels.get("leverage"),
            portfolio_allocation_pct=levels.get("allocation_pct"),
            time_horizon=horizon,
            timeframe=primary_tf,
            rationale="; ".join(rationale_parts),
            indicator_breakdown=all_indicators,
        )

    async def _news_and_starter(
        self,
        signals: List[TradeSignal],
        *,
        fear_greed: Optional[int],
        budget_eur: float,
        symbols: List[str],
    ) -> tuple:
        news_items = await aggregator.get_news(symbols)
        btc_candles = await aggregator.get_ohlcv("BTC", timeframe="1d", limit=400)
        candles_by_symbol = {"BTC": btc_candles}
        for extra in ("ETH", "SOL", "XRP"):
            try:
                candles_by_symbol[extra] = await aggregator.get_ohlcv(extra, timeframe="1d", limit=120)
            except Exception as exc:
                logger.warning("OHLCV for %s failed: %s", extra, exc)
        headlines = headlines_from_news(news_items)
        category = dominant_category(headlines)
        analogs = historical_analogs(category)
        bounce = seven_day_bounce(btc_candles)
        verdict = resolve_verdict(category, bounce_pct=bounce, fear_greed=fear_greed)
        playbook = build_playbook_text(category, analogs, bounce)
        recent = detect_dips(btc_candles)
        context = NewsDipContext(
            dominant_category=category,
            headline_mix=category_mix(headlines),
            current_headlines=headlines,
            recent_dips=recent,
            historical_analogs=analogs,
            playbook=playbook,
            verdict=verdict,
        )
        try:
            fx = await aggregator.get_eur_per_usd()
        except Exception:
            fx = 0.87
        plan = build_starter_plan(
            budget_eur=budget_eur,
            signals=signals,
            btc_candles=btc_candles,
            verdict=verdict,
            category=category,
            playbook_text=playbook,
            eur_per_usd=fx,
        )
        last_eur = {}
        for sig in signals:
            if sig.entry_price:
                last_eur[sig.symbol.upper()] = round(float(sig.entry_price) * fx, 4)
        book = build_short_term_book(
            budget_eur=budget_eur,
            candles_by_symbol=candles_by_symbol,
            last_eur=last_eur,
            category=category,
            eur_per_usd=fx,
        )
        return context, plan, book

    async def run_universe(
        self,
        symbols: Optional[List[str]] = None,
        *,
        refresh_all_coins: bool = True,
        signal_limit: Optional[int] = None,
        budget_eur: float = 100.0,
    ) -> DailyReport:
        # Always extract & persist full coin universe unless symbols explicitly passed
        if symbols is None and refresh_all_coins:
            try:
                all_tickers = await aggregator.fetch_and_store_all_coins()
                logger.info("Stored universe: %d coins", len(all_tickers))
            except Exception as exc:
                logger.warning("Full coin extract failed (%s) — using local/fallback list", exc)

        if symbols is None:
            symbols = aggregator.top_symbols_for_signals(signal_limit)
        logger.info("Running signal analysis for %d symbols", len(symbols))

        sentiments = await aggregator.get_sentiment(symbols)
        onchain = await aggregator.get_onchain(symbols)
        macro = await aggregator.get_macro()
        overview = await aggregator.get_market_overview()
        fg = next((s for s in sentiments if s.source == "fear_greed"), None)

        signals: List[TradeSignal] = []
        returns_map: Dict[str, List[float]] = {}

        for i, sym in enumerate(symbols, 1):
            try:
                logger.info("[%d/%d] Analyzing %s", i, len(symbols), sym)
                sig = await self.analyze_symbol(
                    sym, sentiments=sentiments, onchain=onchain, macro=macro
                )
                signals.append(sig)
                candles = await aggregator.get_ohlcv(sym, timeframe="1d", limit=60)
                if candles:
                    closes = [c.close for c in candles]
                    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
                    returns_map[sym] = rets
            except Exception as exc:
                logger.error("Analysis failed for %s: %s", sym, exc)

        # Correlation-adjusted allocations for buys
        buys = sorted(
            [s for s in signals if s.primary_signal == SignalType.BUY],
            key=lambda s: (-s.confidence, -s.composite_score),
        )
        sells = sorted(
            [s for s in signals if s.primary_signal == SignalType.SELL],
            key=lambda s: (-s.confidence, s.composite_score),
        )
        shorts = sorted(
            [s for s in signals if s.secondary_signal == Direction.SHORT],
            key=lambda s: -s.confidence,
        )
        selected: List[str] = []
        for s in buys:
            penalty = self.risk.correlation_penalty(s.symbol, selected, returns_map)
            if s.portfolio_allocation_pct:
                s.portfolio_allocation_pct = round(s.portfolio_allocation_pct * penalty, 2)
            selected.append(s.symbol)

        # Surface soft leans only when there is a meaningful directional tilt
        holds = sorted(
            [s for s in signals if s.primary_signal == SignalType.HOLD],
            key=lambda s: -abs(s.composite_score),
        )
        if len(buys) < 3:
            lean_buy = [s for s in holds if s.composite_score > 0.05 and s.confidence >= 40]
            for s in lean_buy:
                s.primary_signal = SignalType.BUY
                s.secondary_signal = Direction.LONG
                s.rationale = (s.rationale or "") + "; lean-buy (soft threshold)"
            buys = sorted(buys + lean_buy, key=lambda s: (-s.confidence, -s.composite_score))
        if len(sells) < 3:
            lean_sell = [s for s in holds if s.composite_score < -0.05 and s.confidence >= 40]
            for s in lean_sell:
                s.primary_signal = SignalType.SELL
                s.secondary_signal = Direction.SHORT if s.confidence >= 45 else Direction.NONE
                s.rationale = (s.rationale or "") + "; lean-sell (soft threshold)"
            sells = sorted(sells + lean_sell, key=lambda s: (-s.confidence, s.composite_score))
        if len(shorts) < 3:
            shorts = sorted(
                [s for s in sells if s.secondary_signal == Direction.SHORT],
                key=lambda s: -s.confidence,
            )

        overall_sent = float(np.mean([s.score for s in sentiments])) if sentiments else 0.0
        global_data = overview.get("global") or {}
        mcap_change = (global_data.get("market_cap_change_percentage_24h_usd") if isinstance(global_data, dict) else None)

        risk_notes = []
        if fg and fg.score < -0.4:
            risk_notes.append("Extreme fear — elevated downside / mean-reversion opportunity")
        if fg and fg.score > 0.4:
            risk_notes.append("Extreme greed — elevated correction risk")
        if macro.get("sp500_change_pct") is not None and macro["sp500_change_pct"] < -1.5:
            risk_notes.append("Risk-off in equities — crypto correlation risk elevated")

        fg_idx = int((fg.score + 1) * 50) if fg else None
        news_context = None
        starter_plan = None
        short_term_book = None
        try:
            news_context, starter_plan, short_term_book = await self._news_and_starter(
                signals,
                fear_greed=fg_idx,
                budget_eur=budget_eur,
                symbols=symbols,
            )
            if news_context.playbook:
                risk_notes.append(news_context.playbook)
            if starter_plan:
                risk_notes.append(
                    f"€{starter_plan.budget_eur:.0f} starter: {starter_plan.action} {starter_plan.symbol} "
                    f"via {starter_plan.pair}. Stop €{starter_plan.stop_loss_eur}."
                )
            if short_term_book and short_term_book.legs:
                mix = ", ".join(f"{l.symbol} €{l.alloc_eur:.0f}" for l in short_term_book.legs)
                risk_notes.append(f"Short-term sleeve: {mix}. {short_term_book.thesis}")
        except Exception as exc:
            logger.warning("News-dip / starter plan failed: %s", exc)

        stored_count = len(store.load_symbols())
        summary = (
            f"Universe stored locally: {stored_count} coins. "
            f"Analyzed {len(signals)} assets. "
            f"Overall sentiment {overall_sent:+.2f}. "
            f"Market 24h change: {mcap_change if mcap_change is not None else 'n/a'}%. "
            f"Fear & Greed: {fg.label if fg else 'n/a'}."
        )
        portfolio = {s.symbol: s.portfolio_allocation_pct or 0 for s in buys[:10]}

        report = DailyReport(
            market_summary=summary,
            overall_sentiment=overall_sent,
            fear_greed_index=fg_idx,
            top_buys=buys[:10],
            top_sells=sells[:10],
            top_shorts=shorts[:5],
            risk_notes=risk_notes,
            portfolio_suggestions=portfolio,
            news_context=news_context,
            starter_plan=starter_plan,
            short_term_book=short_term_book,
        )
        # Persist locally (no database)
        store.save_report(report.model_dump(mode="json"))
        store.save_signals([s.model_dump(mode="json") for s in signals])
        return report


signal_engine = SignalEngine()
