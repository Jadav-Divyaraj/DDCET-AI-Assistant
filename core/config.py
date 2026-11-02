from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    project_root: Path
    telegram_bot_token: str
    groq_api_key: str
    groq_models: tuple[str, ...]
    groq_vision_model: str
    groq_attempts_per_model: int
    groq_max_retry_seconds: float
    telegram_connect_timeout: float
    telegram_read_timeout: float
    telegram_write_timeout: float
    telegram_pool_timeout: float
    telegram_media_write_timeout: float
    telegram_poll_timeout: int
    telegram_bootstrap_retries: int
    timezone: ZoneInfo
    database_path: Path
    upload_dir: Path
    knowledge_dir: Path
    pdf_search_sources: tuple[str, ...]
    tesseract_cmd: str | None
    conversation_window: int
    external_document_context_enabled: bool
    max_upload_mb: int
    max_pdf_vision_pages: int
    ddcethub_website_url: str


def _split_sources(raw: str) -> tuple[str, ...]:
    return tuple(source.strip().rstrip("/") for source in raw.split(",") if source.strip())


def _project_path(raw: str, default: str) -> Path:
    path = Path(raw or default)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _positive_int(raw: str, default: int) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def _nonnegative_int(raw: str, default: int) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


def _positive_float(raw: str, default: float) -> float:
    try:
        parsed = float(raw)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _nonnegative_float(raw: str, default: float) -> float:
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return default


def _boolean(raw: str, default: bool = False) -> bool:
    normalized = raw.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _tesseract_command() -> str | None:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        return configured
    discovered = shutil.which("tesseract")
    if discovered:
        return discovered
    windows_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    return str(windows_default) if windows_default.is_file() else None


def get_settings() -> Settings:
    database_path = _project_path(os.getenv("DATABASE_PATH", ""), "data/gtu_bot.sqlite3")
    upload_dir = _project_path(os.getenv("UPLOAD_DIR", ""), "data/uploads")
    knowledge_dir = _project_path(
        os.getenv("KNOWLEDGE_DIR", ""),
        "knowledge/approved",
    )
    sources = os.getenv(
        "PDF_SEARCH_SOURCES",
        "https://gtu-paper-solution.com,https://gtupapers.in,https://www.gtupaper.in",
    )
    configured_models = _split_sources(os.getenv("GROQ_TEXT_MODELS", ""))
    legacy_model = os.getenv("GROQ_MODEL", "").strip()
    groq_models = configured_models or (
        legacy_model or "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    )
    return Settings(
        project_root=PROJECT_ROOT,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_models=groq_models,
        groq_vision_model=os.getenv(
            "GROQ_VISION_MODEL",
            "qwen/qwen3.6-27b",
        ).strip(),
        groq_attempts_per_model=_positive_int(
            os.getenv("GROQ_ATTEMPTS_PER_MODEL", "2"),
            2,
        ),
        groq_max_retry_seconds=_nonnegative_float(
            os.getenv("GROQ_MAX_RETRY_SECONDS", "4"),
            4.0,
        ),
        telegram_connect_timeout=_positive_float(
            os.getenv("TELEGRAM_CONNECT_TIMEOUT", "20"),
            20.0,
        ),
        telegram_read_timeout=_positive_float(
            os.getenv("TELEGRAM_READ_TIMEOUT", "30"),
            30.0,
        ),
        telegram_write_timeout=_positive_float(
            os.getenv("TELEGRAM_WRITE_TIMEOUT", "30"),
            30.0,
        ),
        telegram_pool_timeout=_positive_float(
            os.getenv("TELEGRAM_POOL_TIMEOUT", "10"),
            10.0,
        ),
        telegram_media_write_timeout=_positive_float(
            os.getenv("TELEGRAM_MEDIA_WRITE_TIMEOUT", "60"),
            60.0,
        ),
        telegram_poll_timeout=_positive_int(
            os.getenv("TELEGRAM_POLL_TIMEOUT", "20"),
            20,
        ),
        telegram_bootstrap_retries=_nonnegative_int(
            os.getenv("TELEGRAM_BOOTSTRAP_RETRIES", "5"),
            5,
        ),
        timezone=ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Kolkata")),
        database_path=database_path,
        upload_dir=upload_dir,
        knowledge_dir=knowledge_dir,
        pdf_search_sources=_split_sources(sources),
        tesseract_cmd=_tesseract_command(),
        conversation_window=max(
            2,
            _positive_int(os.getenv("CONVERSATION_WINDOW", "12"), 12),
        ),
        external_document_context_enabled=_boolean(
            os.getenv("ALLOW_EXTERNAL_DOCUMENT_CONTEXT", ""),
            False,
        ),
        max_upload_mb=_positive_int(os.getenv("MAX_UPLOAD_MB", "18"), 18),
        max_pdf_vision_pages=min(
            3,
            _positive_int(os.getenv("MAX_PDF_VISION_PAGES", "3"), 3),
        ),
        ddcethub_website_url=(
            os.getenv("DDCETHUB_WEBSITE_URL", "").strip().rstrip("/")
            or "https://ddcethub.vercel.app"
        ),
    )
