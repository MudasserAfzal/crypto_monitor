"""Celery tasks."""

from __future__ import annotations

import asyncio

from app.core.cache import cache
from app.core.logging import get_logger, setup_logging
from app.core.storage import store
from app.services.alerts import notify_report
from app.signals.engine import signal_engine
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.run_daily_analysis")
def run_daily_analysis():
    setup_logging()

    async def _job():
        store._ensure_dirs()
        try:
            await cache.connect()
        except Exception:
            pass
        try:
            report = await signal_engine.run_universe()
            await notify_report(report)
            logger.info(
                "Daily analysis complete — universe=%d buys=%d sells=%d shorts=%d",
                len(store.load_symbols()),
                len(report.top_buys),
                len(report.top_sells),
                len(report.top_shorts),
            )
            return {"buys": len(report.top_buys), "sells": len(report.top_sells)}
        finally:
            try:
                await cache.disconnect()
            except Exception:
                pass

    return asyncio.run(_job())
