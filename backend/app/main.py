"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.signals import router as signals_router
from app.core.cache import cache
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.storage import store

logger = get_logger(__name__)


async def _scheduled_signal_run() -> None:
    """Periodic task: refresh full coin universe + regenerate signals."""
    from app.signals.engine import signal_engine

    logger.info("Scheduled signal run starting")
    try:
        report = await signal_engine.run_universe()
        buys = len(report.top_buys)
        sells = len(report.top_sells)
        logger.info("Scheduled run complete — %d buys, %d sells", buys, sells)
    except Exception:
        logger.exception("Scheduled signal run failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    store._ensure_dirs()
    try:
        await cache.connect()
    except Exception:
        # Redis optional — local JSON store is the persistence layer
        pass

    settings = get_settings()
    scheduler = AsyncIOScheduler()
    try:
        trigger = CronTrigger.from_crontab(settings.signal_schedule_cron)
    except Exception:
        # Fallback: daily at 08:00 if cron string is invalid
        trigger = CronTrigger(hour=8, minute=0)
    scheduler.add_job(_scheduled_signal_run, trigger, id="daily_signals", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started — cron: %s", settings.signal_schedule_cron)

    yield

    scheduler.shutdown(wait=False)
    try:
        await cache.disconnect()
    except Exception:
        pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description=(
            "Cryptocurrency buy/sell signal platform. "
            "Data persists to local JSON under data/. "
            "Educational/informational only — NOT financial advice."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(signals_router, prefix="/api/v1", tags=["signals"])
    return app


app = create_app()
