from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from telegram.ext import Application

from .db import Database
from .rcti import RctiScraper
from .scraper import Scraper


async def reminder_loop(application: Application, interval_seconds: int = 60) -> None:
    db: Database = application.bot_data["db"]
    next_sync_at = datetime.now(timezone.utc)
    while True:
        now = datetime.now(timezone.utc)
        for reminder in db.due_reminders(now):
            try:
                await application.bot.send_message(
                    chat_id=reminder.chat_id,
                    text=f"Reminder: {reminder.text}",
                )
                db.mark_reminder_sent(reminder.id)
            except Exception:
                continue
        if now >= next_sync_at:
            try:
                await sync_external_notifications(application)
            except Exception:
                pass  # Log error but don't crash the reminder loop
            next_sync_at = now + timedelta(hours=6)
        await asyncio.sleep(interval_seconds)


async def sync_external_notifications(application: Application) -> None:
    db: Database = application.bot_data["db"]
    await sync_gtu_news(application, db)
    await sync_rcti_assignments(application, db)


async def sync_gtu_news(application: Application, db: Database) -> None:
    chat_ids = db.users_with_gtu_news_enabled()
    if not chat_ids:
        return
    scraper: Scraper = application.bot_data["scraper"]
    news = await scraper.fetch_gtu_news(limit=8)
    if not news:
        return
    for chat_id in chat_ids:
        new_items = [item for item in news if db.mark_news_seen(chat_id, item.url)]
        if not new_items:
            continue
        text = "New important GTU links:\n\n" + "\n\n".join(
            f"{index + 1}. {item.title}\n{item.url}" for index, item in enumerate(new_items[:4])
        )
        try:
            await application.bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            continue


async def sync_rcti_assignments(application: Application, db: Database) -> None:
    chat_ids = db.users_with_assignment_reminders_enabled()
    if not chat_ids:
        return
    rcti: RctiScraper = application.bot_data["rcti"]
    settings = application.bot_data["settings"]
    items = await rcti.fetch_assignment_items()
    for chat_id in chat_ids:
        for item in items:
            if not item.due_at:
                continue
            due_at = item.due_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=settings.timezone)
            text = f"RCTI assignment/material: {item.title} - {item.url}"
            db.add_reminder(chat_id, due_at, text, source="rcti")
