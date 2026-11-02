from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .rag import RagIndex, split_text
from .text_extraction import extract_pages


ALLOWED_DOCUMENT_TYPES = {"syllabus", "notes", "paper", "solution", "question_bank"}
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}


@dataclass(frozen=True)
class SourceSpec:
    path: Path
    relative_path: str
    title: str
    subject: str
    unit: str
    topic: str
    document_type: str
    include_pages: tuple[int, ...] | None


@dataclass(frozen=True)
class IngestionResult:
    documents: int
    pages: int
    chunks: int
    empty_pages: int
    ocr_pages: int
    errors: tuple[str, ...]


def load_manifest(knowledge_dir: Path) -> tuple[list[SourceSpec], list[str]]:
    manifest_path = knowledge_dir / "manifest.json"
    if not manifest_path.exists():
        return [], [f"Manifest not found: {manifest_path}"]
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [], [f"Could not read manifest: {error}"]

    documents = payload.get("documents")
    if not isinstance(documents, list):
        return [], ["manifest.json must contain a documents list."]

    specs: list[SourceSpec] = []
    errors: list[str] = []
    knowledge_root = knowledge_dir.resolve()
    for index, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            errors.append(f"Document #{index} must be an object.")
            continue
        if item.get("approved") is not True:
            continue
        relative_path = str(item.get("path", "")).strip().replace("\\", "/")
        title = str(item.get("title", "")).strip()
        subject = str(item.get("subject", "")).strip()
        unit = str(item.get("unit", "")).strip()
        topic = str(item.get("topic", "")).strip()
        document_type = str(item.get("document_type", "")).strip().lower()
        include_pages_raw = item.get("include_pages")
        include_pages: tuple[int, ...] | None = None
        if include_pages_raw is not None:
            if not isinstance(include_pages_raw, list) or not all(
                isinstance(page_number, int) and page_number > 0
                for page_number in include_pages_raw
            ):
                errors.append(
                    f"Document #{index} include_pages must be a list of positive page numbers."
                )
                continue
            include_pages = tuple(sorted(set(include_pages_raw)))
        if not relative_path or not title or not subject:
            errors.append(f"Document #{index} requires path, title, and subject.")
            continue
        if document_type not in ALLOWED_DOCUMENT_TYPES:
            errors.append(
                f"Document #{index} has unsupported document_type: {document_type or '<empty>'}."
            )
            continue
        source_path = (knowledge_dir / relative_path).resolve()
        try:
            source_path.relative_to(knowledge_root)
        except ValueError:
            errors.append(f"Document #{index} points outside the approved knowledge directory.")
            continue
        if source_path.suffix.lower() not in ALLOWED_SUFFIXES:
            errors.append(f"Document #{index} has unsupported file type: {source_path.suffix}.")
            continue
        if not source_path.is_file():
            errors.append(f"Document #{index} file was not found: {relative_path}")
            continue
        specs.append(
            SourceSpec(
                path=source_path,
                relative_path=relative_path,
                title=title,
                subject=subject,
                unit=unit,
                topic=topic,
                document_type=document_type,
                include_pages=include_pages,
            )
        )
    return specs, errors


def ingest_approved_corpus(
    database: Database,
    rag: RagIndex,
    knowledge_dir: Path,
    tesseract_cmd: str | None = None,
) -> IngestionResult:
    specs, manifest_errors = load_manifest(knowledge_dir)
    errors = list(manifest_errors)
    document_count = 0
    page_count = 0
    chunk_count = 0
    empty_pages = 0
    ocr_pages = 0

    for spec in specs:
        try:
            raw = spec.path.read_bytes()
            content_hash = hashlib.sha256(raw).hexdigest()
            pages = extract_pages(spec.path, tesseract_cmd)
            stored_pages: list[tuple[int, list[tuple[str, list[float]]]]] = []
            for page in pages:
                if spec.include_pages is not None and page.page_number not in spec.include_pages:
                    continue
                page_count += 1
                if page.used_ocr:
                    ocr_pages += 1
                chunks = split_text(page.text, chunk_size=1000, overlap=140)
                if not chunks:
                    empty_pages += 1
                    continue
                embeddings = rag.embed_many(chunks)
                stored_pages.append(
                    (
                        page.page_number,
                        [
                            (chunk, embeddings[index] if index < len(embeddings) else [])
                            for index, chunk in enumerate(chunks)
                        ],
                    )
                )

            document_id = database.upsert_approved_document(
                source_path=spec.relative_path,
                content_hash=content_hash,
                filename=spec.path.name,
                title=spec.title,
                subject=spec.subject,
                unit=spec.unit,
                topic=spec.topic,
                document_type=spec.document_type,
            )
            inserted = database.replace_approved_chunks(document_id, stored_pages)
            document_count += 1
            chunk_count += inserted
        except Exception as error:
            errors.append(f"{spec.relative_path}: {type(error).__name__}: {error}")

    return IngestionResult(
        documents=document_count,
        pages=page_count,
        chunks=chunk_count,
        empty_pages=empty_pages,
        ocr_pages=ocr_pages,
        errors=tuple(errors),
    )
