from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO

import dateparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .config import Settings
from .db import Database
from .llm import GroqClient
from .rag import RagIndex
from .rcti import RctiScraper
from .scraper import Scraper
from .text_extraction import extract_text


WELCOME = (
    "Hi, I am GTU Bot.\n\n"
    "Ask me a normal question, upload a PDF/image for document Q&A, or use:\n"
    "/remind tomorrow 9am submit assignment\n"
    "/pdf_search Computer Engineering Winter 2023\n"
    "/gtu_news\n"
    "/gtu_notify on\n"
    "/rcti\n"
    "/assignments on"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        db(context).ensure_user(update.effective_chat.id)
    await update.message.reply_text(WELCOME)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/remind <date/time> <text> - create reminder\n"
        "/myreminders - show pending reminders\n"
        "/delreminder <id> - delete a reminder\n"
        "/pdf_search <query> - search configured PDF sites\n"
        "/gtu_news - show important GTU links\n"
        "/gtu_notify on|off - push important GTU links to this chat\n"
        "/rcti - show RCTI Computer Department links\n"
        "/assignments on|off - enable or disable RCTI assignment reminders\n\n"
        "You can also upload a PDF/image, then ask questions about it."
    )


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    raw = " ".join(context.args)
    if not raw:
        await update.message.reply_text("Use: /remind tomorrow 9am submit assignment")
        return
    parsed = parse_reminder(raw, config(context).timezone.key)
    if not parsed:
        await update.message.reply_text("I could not understand the date. Try: /remind 2026-08-01 09:00 exam form")
        return
    due_at, text = parsed
    if not text:
        await update.message.reply_text("Please include reminder text after the date/time.")
        return
    reminder_id = db(context).add_reminder(chat_id, due_at, text)
    await update.message.reply_text(f"Reminder #{reminder_id} saved for {due_at:%d %b %Y, %I:%M %p}.")


async def my_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reminders = db(context).list_reminders(update.effective_chat.id)
    if not reminders:
        await update.message.reply_text("No pending reminders.")
        return
    lines = [
        f"#{item.id} - {item.due_at:%d %b %Y, %I:%M %p}: {item.text}"
        for item in reminders[:20]
    ]
    await update.message.reply_text("\n".join(lines))


async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Use: /delreminder <id>")
        return
    deleted = db(context).delete_reminder(update.effective_chat.id, int(context.args[0]))
    await update.message.reply_text("Deleted." if deleted else "Reminder not found.")


async def pdf_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Use: /pdf_search Computer Engineering Winter 2023")
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    results = await scraper(context).search_pdfs(query)
    if not results:
        await update.message.reply_text("No matching PDF links found from configured sources.")
        return
    keyboard = [
        [InlineKeyboardButton(f"Download #{index + 1}", callback_data=f"pdf:{index}")]
        for index, item in enumerate(results[:6])
    ]
    context.user_data["pdf_results"] = [item.url for item in results[:6]]
    lines = [f"{index + 1}. {item.title}\n{item.url}" for index, item in enumerate(results[:6])]
    await update.message.reply_text("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def download_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    index_text = query.data.removeprefix("pdf:")
    urls = context.user_data.get("pdf_results", [])
    if not index_text.isdigit() or int(index_text) >= len(urls):
        await query.message.reply_text("This download button expired. Run /pdf_search again.")
        return
    url = urls[int(index_text)]
    await query.message.reply_text("Downloading PDF...")
    content, filename = await scraper(context).download(url)
    bio = BytesIO(content)
    bio.name = filename
    await query.message.reply_document(document=InputFile(bio, filename=filename))


async def gtu_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    results = await scraper(context).fetch_gtu_news()
    if not results:
        await update.message.reply_text("Could not fetch GTU news right now.")
        return
    await update.message.reply_text("\n\n".join(f"{i + 1}. {item.title}\n{item.url}" for i, item in enumerate(results)))


async def gtu_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or context.args[0].lower() not in {"on", "off", "status"}:
        enabled = db(context).gtu_news_enabled(update.effective_chat.id)
        await update.message.reply_text(f"Use /gtu_notify on or /gtu_notify off. Current status: {'on' if enabled else 'off'}.")
        return

    action = context.args[0].lower()
    chat_id = update.effective_chat.id
    if action == "off":
        db(context).set_gtu_news(chat_id, False)
        await update.message.reply_text("GTU news notifications disabled.")
        return
    if action == "status":
        enabled = db(context).gtu_news_enabled(chat_id)
        await update.message.reply_text(f"GTU news notifications are {'on' if enabled else 'off'}.")
        return

    db(context).set_gtu_news(chat_id, True)
    for item in await scraper(context).fetch_gtu_news():
        db(context).mark_news_seen(chat_id, item.url)
    await update.message.reply_text("GTU news notifications enabled. I will notify this chat when new important GTU links appear.")


async def rcti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action(ChatAction.TYPING)
    links = await rcti_scraper(context).fetch_links()
    if not links:
        await update.message.reply_text("Could not fetch RCTI updates right now.")
        return
    await update.message.reply_text("\n\n".join(f"{i + 1}. {item.title}\n{item.url}" for i, item in enumerate(links[:12])))


async def assignments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or context.args[0].lower() not in {"on", "off", "status"}:
        enabled = db(context).assignment_reminders_enabled(update.effective_chat.id)
        await update.message.reply_text(f"Use /assignments on or /assignments off. Current status: {'on' if enabled else 'off'}.")
        return

    action = context.args[0].lower()
    chat_id = update.effective_chat.id
    if action == "off":
        db(context).set_assignment_reminders(chat_id, False)
        await update.message.reply_text("RCTI assignment reminders disabled.")
        return
    if action == "status":
        enabled = db(context).assignment_reminders_enabled(chat_id)
        await update.message.reply_text(f"RCTI assignment reminders are {'on' if enabled else 'off'}.")
        return

    db(context).set_assignment_reminders(chat_id, True)
    items = await rcti_scraper(context).fetch_assignment_items()
    saved = 0
    for item in items:
        if item.due_at:
            text = f"RCTI assignment/material: {item.title} - {item.url}"
            db(context).add_reminder(chat_id, item.due_at, text, source="rcti")
            saved += 1
    if saved:
        await update.message.reply_text(f"RCTI assignment reminders enabled. Added {saved} dated reminders.")
    else:
        await update.message.reply_text("RCTI assignment reminders enabled. I did not find dated assignment links yet.")


async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user_id = update.effective_user.id
    settings = config(context)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    telegram_file = None
    filename = "upload"
    mime_type = None

    if message.document:
        telegram_file = await context.bot.get_file(message.document.file_id)
        filename = message.document.file_name or "document"
        mime_type = message.document.mime_type
    elif message.photo:
        telegram_file = await context.bot.get_file(message.photo[-1].file_id)
        filename = f"photo_{message.photo[-1].file_unique_id}.jpg"
        mime_type = "image/jpeg"

    if telegram_file is None:
        return

    await message.reply_text("Received. Extracting text and building your document index...")
    local_path = settings.upload_dir / safe_filename(filename)
    await telegram_file.download_to_drive(local_path)

    settings = config(context)
    try:
        text = await asyncio.to_thread(extract_text, local_path, mime_type, settings.tesseract_cmd)
    except Exception as exc:
        await message.reply_text(f"Text extraction failed: {exc}")
        return

    if not text.strip():
        await message.reply_text("I could not extract readable text from this file.")
        return

    document_id, chunk_count = await asyncio.to_thread(rag(context).add_document, user_id, filename, text)
    preview = text[:700].strip()
    await message.reply_text(
        f"Indexed document #{document_id} with {chunk_count} chunks.\n\n"
        f"Preview:\n{preview}\n\n"
        "Now ask me a question about this file."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = update.message.text.strip()
    await update.message.chat.send_action(ChatAction.TYPING)

    retrieved = await asyncio.to_thread(rag(context).retrieve, update.effective_user.id, question)
    context_text = "\n\n".join(item.text for item in retrieved if item.score > 0.05)
    try:
        answer = await llm(context).answer(question, context=context_text or None)
    except Exception as exc:
        await update.message.reply_text(f"LLM request failed: {exc}")
        return
    await update.message.reply_text(answer[:3900])


def parse_reminder(raw: str, timezone_name: str = "Asia/Kolkata") -> tuple[datetime, str] | None:
    tokens = raw.split()
    for count in range(min(6, len(tokens)), 0, -1):
        candidate = " ".join(tokens[:count])
        parsed = dateparser.parse(
            candidate,
            settings={
                "PREFER_DATES_FROM": "future",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "TIMEZONE": timezone_name,
            },
        )
        if parsed:
            return parsed, " ".join(tokens[count:]).strip()
    return None


def safe_filename(name: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in name)[:120]


def config(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def llm(context: ContextTypes.DEFAULT_TYPE) -> GroqClient:
    return context.application.bot_data["llm"]


def rag(context: ContextTypes.DEFAULT_TYPE) -> RagIndex:
    return context.application.bot_data["rag"]


def scraper(context: ContextTypes.DEFAULT_TYPE) -> Scraper:
    return context.application.bot_data["scraper"]


def rcti_scraper(context: ContextTypes.DEFAULT_TYPE) -> RctiScraper:
    return context.application.bot_data["rcti"]
