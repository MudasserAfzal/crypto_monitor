"""Celery worker for scheduled daily analysis."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "crypto_signals",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "daily-signal-run": {
            "task": "app.workers.tasks.run_daily_analysis",
            "schedule": crontab(hour=8, minute=0),
        },
    },
)
