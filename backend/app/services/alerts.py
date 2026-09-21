"""Alert notifications — Telegram + email."""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import DailyReport, TradeSignal

logger = get_logger(__name__)


def format_report_text(report: DailyReport) -> str:
    lines = [
        f"📊 {get_settings().app_name} Daily Report",
        f"Generated: {report.generated_at.isoformat()}",
        "",
        report.market_summary,
        f"Overall sentiment: {report.overall_sentiment:+.2f}",
        f"Fear & Greed: {report.fear_greed_index}",
        "",
        "🟢 TOP BUYS:",
    ]
    for s in report.top_buys[:10]:
        lines.append(
            f"  {s.symbol} conf={s.confidence:.0f}% entry={s.entry_price} "
            f"TP={s.take_profit} SL={s.stop_loss} alloc={s.portfolio_allocation_pct}%"
        )
    lines.append("")
    lines.append("🔴 TOP SELLS:")
    for s in report.top_sells[:10]:
        lines.append(f"  {s.symbol} conf={s.confidence:.0f}% composite={s.composite_score:+.2f}")
    lines.append("")
    lines.append("📉 TOP SHORTS:")
    for s in report.top_shorts[:5]:
        lines.append(f"  {s.symbol} conf={s.confidence:.0f}% lev={s.leverage_suggestion}x")
    if report.starter_plan:
        p = report.starter_plan
        lines.append("")
        lines.append(f"💶 €{p.budget_eur:.0f} STARTER: {p.action} {p.symbol} on {p.pair}")
        lines.append(f"  venue={p.venue}")
        lines.append(f"  deploy_now=€{p.deploy_now_eur:.0f} reserve=€{p.reserve_eur:.0f}")
        lines.append(f"  entry=€{p.entry_price_eur} SL=€{p.stop_loss_eur} ({p.stop_loss_pct}%) TP=€{p.take_profit_eur}")
        lines.append(f"  {p.entry_note}")
    if report.news_context:
        lines.append("")
        lines.append(f"🗞️ NEWS ANALOG: {report.news_context.dominant_category} → {report.news_context.verdict}")
        lines.append(f"  {report.news_context.playbook}")
    if report.risk_notes:
        lines.append("")
        lines.append("⚠️ RISK:")
        lines.extend(f"  - {n}" for n in report.risk_notes)
    lines.append("")
    lines.append(f"⚖️ {report.disclaimer}")
    return "\n".join(lines)


async def send_telegram(message: str) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("Telegram not configured — skip alert")
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Telegram message limit ~4096
            for i in range(0, len(message), 4000):
                chunk = message[i : i + 4000]
                resp = await client.post(
                    url,
                    json={"chat_id": settings.telegram_chat_id, "text": chunk},
                )
                resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Telegram alert failed: %s", exc)
        return False


def send_email(subject: str, body: str) -> bool:
    settings = get_settings()
    if not settings.smtp_host or not settings.alert_email_to:
        logger.info("Email not configured — skip alert")
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.smtp_user or "crypto-signals@localhost"
        msg["To"] = settings.alert_email_to
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception as exc:
        logger.error("Email alert failed: %s", exc)
        return False


async def notify_report(report: DailyReport) -> None:
    text = format_report_text(report)
    await send_telegram(text)
    send_email(f"{get_settings().app_name} Daily Signals", text)


async def notify_signal(signal: TradeSignal) -> None:
    if signal.confidence < 70:
        return
    text = (
        f"🚨 High-confidence {signal.primary_signal.value} {signal.symbol}\n"
        f"Confidence: {signal.confidence:.0f}%\n"
        f"Entry: {signal.entry_price} TP: {signal.take_profit} SL: {signal.stop_loss}\n"
        f"{signal.rationale}"
    )
    await send_telegram(text)
