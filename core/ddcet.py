from __future__ import annotations

import re
from collections import Counter

from .db import ConversationMessage
from .rag import ApprovedRetrievalResult, tokenize
from .telegram_format import render_telegram_text


FOLLOW_UP_TERMS = {
    "again",
    "detail",
    "detailed",
    "explain",
    "hinglish",
    "it",
    "more",
    "short",
    "simple",
    "simply",
    "that",
    "this",
    "ગુજરાતી",
    "gujarati",
}

DDCET_OVERVIEW_PHRASES = (
    "what is ddcet",
    "about ddcet",
    "define ddcet",
    "ddcet full form",
    "full form of ddcet",
    "ddcet exam",
)
def contextualize_query(
    question: str,
    history: list[ConversationMessage],
) -> str:
    terms = tokenize(question)
    looks_like_follow_up = len(terms) <= 6 and (
        not terms or bool(set(terms) & FOLLOW_UP_TERMS) or question.strip().isdigit()
    )
    if not looks_like_follow_up:
        return question
    previous_user = next(
        (message.content for message in reversed(history) if message.role == "user"),
        "",
    )
    previous_assistant = next(
        (message.content for message in reversed(history) if message.role == "assistant"),
        "",
    )
    if not previous_user and not previous_assistant:
        return question
    
    context_str = ""
    if previous_user:
        context_str += f"User previously asked: {previous_user}\n"
    if previous_assistant:
        context_str += f"Assistant previously replied: {previous_assistant}\n"
        
    return f"{context_str.strip()}\nFollow-up request: {question}"


def select_grounded_results(
    question: str,
    results: list[ApprovedRetrievalResult],
) -> list[ApprovedRetrievalResult]:
    lower = question.lower()
    asks_for_solution = any(
        phrase in lower
        for phrase in (
            "answer key",
            "correct answer",
            "correct option",
            "solve this",
            "solution for",
            "which option",
        )
    )
    asks_about_syllabus = any(
        phrase in lower
        for phrase in (
            "syllabus",
            "weightage",
            "topics covered",
            "topics are covered",
            "included in ddcet",
            "in ddcet",
            "official topic",
            "official ddcet",
            "part of ddcet",
            "part of the syllabus",
            "in the syllabus",
            "asked in ddcet",
            *DDCET_OVERVIEW_PHRASES,
        )
    )
    asks_what_appeared = any(
        phrase in lower
        for phrase in (
            "appeared",
            "asked in",
            "previous paper",
            "which questions",
            "what questions",
            "show the paper",
        )
    ) or (
        "paper" in lower
        and (
            bool(re.search(r"\b20\d{2}\b", lower))
            or any(term in lower for term in ("show", "give", "find", "download"))
        )
    )

    if asks_for_solution:
        allowed_types = {"notes", "solution"}
        type_priority = {"solution": 0, "notes": 1}
    elif asks_about_syllabus:
        allowed_types = {"syllabus"}
        type_priority = {"syllabus": 0}
    elif asks_what_appeared:
        allowed_types = {"paper"}
        type_priority = {"paper": 0}
    else:
        allowed_types = {"notes", "solution"}
        type_priority = {"notes": 0, "solution": 1}
    selected = [
        result for result in results 
        if result.document_type in allowed_types 
        and (result.score >= 0.60 or result.keyword_score >= 0.20)
    ]
    return sorted(
        selected,
        key=lambda item: (type_priority.get(item.document_type, 99), -item.score),
    )


def is_answerable(
    question: str,
    results: list[ApprovedRetrievalResult],
) -> bool:
    if len(question.strip()) < 8:
        return False
        
    grounded = select_grounded_results(question, results)
    if not grounded:
        return False
    top = grounded[0]
    return top.keyword_score >= 0.20 or top.score >= 0.65


def build_source_context(results: list[ApprovedRetrievalResult]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(results, start=1):
        blocks.append(
            f"[EXCERPT {index}]\n"
            f"Document: {item.title}\n"
            f"Subject: {item.subject}\n"
            f"Unit: {item.unit or 'Not specified'}\n"
            f"Topic: {item.topic or 'Not specified'}\n"
            f"Page: {item.page_number}\n"
            f"Text:\n{render_telegram_text(item.text)}"
        )
    return "\n\n".join(blocks)


def source_references(results: list[ApprovedRetrievalResult]) -> list[dict[str, object]]:
    seen: set[tuple[int, int]] = set()
    references: list[dict[str, object]] = []
    for item in results:
        key = (item.document_id, item.page_number)
        if key in seen:
            continue
        seen.add(key)
        references.append(
            {
                "document_id": item.document_id,
                "title": item.title,
                "page": item.page_number,
                "subject": item.subject,
                "unit": item.unit,
                "topic": item.topic,
            }
        )
    return references


def dominant_metadata(results: list[ApprovedRetrievalResult]) -> tuple[str, str, str]:
    if not results:
        return "", "", ""

    def most_common(field: str) -> str:
        values = [
            str(getattr(item, field)).strip()
            for item in results
            if str(getattr(item, field)).strip()
        ]
        return Counter(values).most_common(1)[0][0] if values else ""

    return most_common("subject"), most_common("unit"), most_common("topic")


def format_grounded_answer(
    answer: str,
    results: list[ApprovedRetrievalResult],
    *,
    extra_references: list[dict[str, object]] | None = None,
) -> str:
    subject, unit, topic = dominant_metadata(results)
    headings = [f"Subject: {subject or 'DDCET'}"]
    if unit:
        headings.append(f"Unit: {unit}")
    if topic:
        headings.append(f"Topic: {topic}")

    references = source_references(results) + (extra_references or [])
    cleaned_answer = clean_model_text(answer)
    return (
        "\n".join(headings)
        + "\n\n"
        + cleaned_answer
        + "\n\n"
        + format_source_footer(references)
    )


def clean_model_text(text: str) -> str:
    return render_telegram_text(text)


def format_source_footer(references: list[dict[str, object]]) -> str:
    grouped: dict[str, list[object]] = {}
    for reference in references:
        title = str(reference.get("title", "Source")).strip() or "Source"
        page = reference.get("page", "")
        if page not in grouped.setdefault(title, []):
            grouped[title].append(page)
    lines = [
        f"• {title} — {_format_pages(pages)}"
        for title, pages in grouped.items()
    ]
    heading = "📌 Source:" if len(lines) == 1 else "📌 Sources:"
    return heading + "\n" + "\n".join(lines)


def _format_pages(pages: list[object]) -> str:
    numeric_pages = sorted({page for page in pages if isinstance(page, int)})
    other_pages = [str(page) for page in pages if not isinstance(page, int) and str(page)]
    parts: list[str] = []
    if numeric_pages:
        ranges: list[str] = []
        start = end = numeric_pages[0]
        for page in numeric_pages[1:]:
            if page == end + 1:
                end = page
                continue
            ranges.append(str(start) if start == end else f"{start}–{end}")
            start = end = page
        ranges.append(str(start) if start == end else f"{start}–{end}")
        parts.append("page " + ", ".join(ranges) if len(ranges) == 1 and start == end else "pages " + ", ".join(ranges))
    parts.extend(other_pages)
    return ", ".join(parts) or "page not specified"


def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
    remaining = text.strip()
    if not remaining:
        return []
    parts: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts
