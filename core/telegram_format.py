from __future__ import annotations

import html
import re


_MATRIX_PATTERN = re.compile(
    r"\\begin\{(?P<kind>[bBpPvV]?matrix)\}(?P<body>.*?)\\end\{(?P=kind)\}",
    flags=re.DOTALL,
)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_SHORT_REQUEST = re.compile(
    r"\b(?:short\s+(?:answer|example|eg|explanation|solution)|brief(?:ly)?|"
    r"quick(?:ly)?|in\s+short|concise|one[-\s]line|in\s+\d+\s+lines?)\b",
    flags=re.IGNORECASE,
)
_DETAILED_REQUEST = re.compile(
    r"\b(?:detailed|in\s+detail|full\s+explanation|step[-\s]by[-\s]step|"
    r"complete\s+solution)\b",
    flags=re.IGNORECASE,
)


def requested_answer_mode(question: str, default: str) -> str:
    """Let a student's wording override the saved default answer preference."""
    if _DETAILED_REQUEST.search(question):
        return "detailed"
    if _SHORT_REQUEST.search(question):
        return "short"
    return default if default in {"short", "detailed"} else "short"


def telegram_style_instruction(answer_mode: str) -> str:
    length_rule = (
        "For a short answer, use at most six compact sections or lines. For a numerical "
        "answer, use only Given, Calculation, and Answer."
        if answer_mode == "short"
        else "For a detailed answer, keep the explanation structured and under 1,800 characters "
        "unless the student explicitly asks for more."
    )
    return (
        "Telegram formatting rules: return plain, phone-readable text only. CRITICALLY IMPORTANT: NEVER use LaTeX math environments ($$, \\[, \\begin{bmatrix}, \\times, etc.). Telegram does not support MathJax/LaTeX. You MUST format all mathematics, matrices, and equations using plain text and standard Unicode characters (like × for multiplication, ÷, √). Write formulas inline with standard brackets. For a matrix, use plain text brackets with one row per line, e.g., '[9  5  2]'. Do not use Markdown tables (|), headings (#), or code blocks (```). "
        + length_rule
        + " YOU HAVE A BUILT-IN IMAGE TOOL. If the user asks for an image, photo, or picture, you MUST STILL provide a helpful textual explanation. However, you MUST ALSO include EXACTLY this tag on its own line: [IMAGE: <search query>]. The system will intercept this and display the image. Do NOT apologize or say you cannot fetch images."
    )


def render_telegram_text(text: str) -> str:
    """Convert common model/source markup into Telegram-readable plain text."""
    if not text:
        return ""
    cleaned = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"<br\s*/?>", " · ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</(?:p|div|li|tr|h[1-6])\s*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = _render_latex_matrices(cleaned)
    cleaned = _render_latex_inline(cleaned)
    cleaned = _render_markdown_tables(cleaned)
    cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s*(.*?)\s*#*\s*$", _heading, cleaned)
    cleaned = re.sub(r"(?m)^\s*(?:---+|___+|\*\*\*+)\s*$", "", cleaned)
    cleaned = cleaned.replace("```math", "").replace("```", "")
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = re.sub(r"(?m)^(\s*)[*+-]\s+", r"\1• ", cleaned)
    cleaned = re.sub(r"(?m)^\s*>\s?", "", cleaned)
    cleaned = cleaned.replace("|", " — ")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _heading(match: re.Match[str]) -> str:
    title = match.group(1).strip()
    return f"📘 {title}" if title else ""


def _render_latex_matrices(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        rows = [
            [render_telegram_text(cell).strip() for cell in row.split("&")]
            for row in re.split(r"\\\\", match.group("body"))
            if row.strip()
        ]
        if not rows:
            return ""
        width_count = max(len(row) for row in rows)
        widths = [
            max(len(row[index]) if index < len(row) else 0 for row in rows)
            for index in range(width_count)
        ]
        lines = []
        for row in rows:
            cells = [
                (row[index] if index < len(row) else "").ljust(widths[index])
                for index in range(width_count)
            ]
            lines.append("[" + "  ".join(cells).rstrip() + "]")
        return "\n".join(lines)

    return _MATRIX_PATTERN.sub(replace, text)


def _render_latex_inline(text: str) -> str:
    cleaned = text
    for _ in range(3):
        updated = re.sub(
            r"\\(?:d?frac)\{([^{}]+)\}\{([^{}]+)\}",
            lambda match: f"({match.group(1)})/({match.group(2)})",
            cleaned,
        )
        updated = re.sub(
            r"\\(?:boxed|text|mathrm|operatorname)\{([^{}]+)\}",
            lambda match: match.group(1),
            updated,
        )
        if updated == cleaned:
            break
        cleaned = updated
    replacements = {
        r"\times": "×",
        r"\cdot": "×",
        r"\div": "÷",
        r"\pm": "±",
        r"\leq": "≤",
        r"\geq": "≥",
        r"\neq": "≠",
        r"\rightarrow": "→",
        r"\to": "→",
        r"\degree": "°",
        r"\%": "%",
        r"\left": "",
        r"\right": "",
        r"\displaystyle": "",
        r"\,": " ",
        r"\!": "",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", cleaned)
    cleaned = cleaned.replace("\\[", "").replace("\\]", "")
    cleaned = cleaned.replace("\\(", "").replace("\\)", "")
    cleaned = cleaned.replace("$$", "").replace("$", "")
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = cleaned.replace("\\", "")
    return cleaned


def _render_markdown_tables(text: str) -> str:
    lines = text.split("\n")
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        if not _is_table_line(lines[index]):
            rendered.append(lines[index])
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and _is_table_line(lines[index]):
            block.append(lines[index])
            index += 1
        converted = _convert_table_block(block)
        rendered.extend(converted if converted else block)
    return "\n".join(rendered)


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _convert_table_block(lines: list[str]) -> list[str]:
    rows = [_split_table_row(line) for line in lines]
    if len(rows) < 3 or not all(_TABLE_SEPARATOR.match(cell) for cell in rows[1]):
        return []
    headers = rows[0]
    converted: list[str] = []
    for row in rows[2:]:
        pieces = [
            f"{headers[index]}: {value}"
            for index, value in enumerate(row)
            if index < len(headers) and value
        ]
        if pieces:
            converted.append("• " + "\n  ".join(pieces))
    return converted


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]
