from __future__ import annotations

from .config import get_settings
from .db import Database
from .knowledge import ingest_approved_corpus
from .rag import RagIndex


def main() -> None:
    settings = get_settings()
    database = Database(settings.database_path)
    result = ingest_approved_corpus(
        database,
        RagIndex(database),
        settings.knowledge_dir,
        settings.tesseract_cmd,
    )
    print(f"Documents: {result.documents}")
    print(f"Pages: {result.pages}")
    print(f"Chunks: {result.chunks}")
    print(f"OCR pages: {result.ocr_pages}")
    print(f"Empty pages: {result.empty_pages}")
    if result.errors:
        print("Warnings/errors:")
        for error in result.errors:
            print(f"- {error}")
    if result.documents == 0:
        print(
            "No approved sources were ingested. Add files and manifest entries under "
            f"{settings.knowledge_dir}."
        )


if __name__ == "__main__":
    main()
