from __future__ import annotations

import logging

from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from .config import get_settings
from .db import Database
from .ddcet_handlers import (
    answer_mode_callback,
    ask_command,
    continue_chat,
    error_handler,
    forget,
    handle_upload,
    handle_text,
    help_command,
    history,
    menu_callback,
    menu_command,
    new_chat,
    profile,
    profile_callback,
    quiz_command,
    poll_answer_handler,
    upload_action_callback,
    sources,
    start,
)
from .llm import GroqClient
from .rag import RagIndex


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_application() -> Application:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Add it to the project .env.")
    if not settings.groq_api_key:
        logging.getLogger(__name__).warning(
            "GROQ_API_KEY is missing. The menu will work, but Ask DDCET will "
            "remain unavailable until GROQ_API_KEY is added."
        )

    database = Database(settings.database_path)
    api_request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=settings.telegram_connect_timeout,
        read_timeout=settings.telegram_read_timeout,
        write_timeout=settings.telegram_write_timeout,
        pool_timeout=settings.telegram_pool_timeout,
        media_write_timeout=settings.telegram_media_write_timeout,
    )
    updates_request = HTTPXRequest(
        connection_pool_size=1,
        connect_timeout=settings.telegram_connect_timeout,
        read_timeout=settings.telegram_read_timeout,
        write_timeout=settings.telegram_write_timeout,
        pool_timeout=settings.telegram_pool_timeout,
    )
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(api_request)
        .get_updates_request(updates_request)
        .build()
    )
    application.bot_data.update(
        {
            "settings": settings,
            "db": database,
            "llm": GroqClient(
                settings.groq_api_key,
                settings.groq_models,
                vision_model=settings.groq_vision_model,
                attempts_per_model=settings.groq_attempts_per_model,
                max_retry_seconds=settings.groq_max_retry_seconds,
            ),
            "rag": RagIndex(database),
        }
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ask", ask_command))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("continue", continue_chat))
    application.add_handler(CommandHandler("newchat", new_chat))
    application.add_handler(CommandHandler("forget", forget))
    application.add_handler(CommandHandler("sources", sources))
    application.add_handler(CallbackQueryHandler(profile_callback, pattern=r"^profile:"))
    application.add_handler(CallbackQueryHandler(answer_mode_callback, pattern=r"^answer:"))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(upload_action_callback, pattern=r"^upload:"))
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, handle_upload)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(PollAnswerHandler(poll_answer_handler))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    application = build_application()
    settings = application.bot_data["settings"]
    application.run_polling(
        allowed_updates=["message", "callback_query", "poll_answer"],
        bootstrap_retries=settings.telegram_bootstrap_retries,
        timeout=settings.telegram_poll_timeout,
    )


if __name__ == "__main__":
    main()
