#!/usr/bin/env python3
"""CLI entrypoint — extract all coins + run daily analysis, saved under data/.

Usage:
    python -m app.cli
    python -m app.cli --extract-only
    python -m app.cli --symbols BTC,ETH,SOL
    python -m app.cli --signal-limit 100
    python -m app.cli --notify
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.aggregator import aggregator
from app.core.cache import cache
from app.core.logging import get_logger, setup_logging
from app.core.storage import store
from app.services.alerts import format_report_text, notify_report
from app.signals.engine import signal_engine

logger = get_logger(__name__)


async def main(
    symbols: list[str] | None,
    notify: bool,
    extract_only: bool,
    signal_limit: int | None,
    budget_eur: float,
) -> int:
    setup_logging()
    store._ensure_dirs()
    try:
        await cache.connect()
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — continuing with local file storage", exc)

    if extract_only:
        tickers = await aggregator.fetch_and_store_all_coins()
        print(f"Extracted {len(tickers)} coins → {store.path('tickers/all_tickers.json')}")
        print(f"Symbols index → {store.path('universe/symbols.json')}")
        return 0

    report = await signal_engine.run_universe(symbols, signal_limit=signal_limit, budget_eur=budget_eur)
    text = format_report_text(report)
    print(text)
    print(f"\nSaved report → {store.path('reports/latest.json')}")
    print(f"Saved tickers → {store.path('tickers/all_tickers.json')} ({len(store.load_symbols())} coins)")
    print(f"Saved signals → {store.path('signals/all_signals.json')}")

    if notify:
        await notify_report(report)

    try:
        await cache.disconnect()
    except Exception:
        pass
    return 0


def cli() -> None:
    parser = argparse.ArgumentParser(description="CryptoSignal — local file analysis")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols (skip auto top-N)")
    parser.add_argument("--signal-limit", type=int, default=None, help="How many top coins to score (default from .env)")
    parser.add_argument("--budget-eur", type=float, default=100.0, help="Starter ticket size in euro")
    parser.add_argument("--extract-only", action="store_true", help="Only fetch & save all coin tickers")
    parser.add_argument("--notify", action="store_true", help="Send Telegram/email alerts")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    raise SystemExit(
        asyncio.run(main(symbols, args.notify, args.extract_only, args.signal_limit, args.budget_eur))
    )


if __name__ == "__main__":
    cli()
