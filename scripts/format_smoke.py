from __future__ import annotations

import asyncio
import sys

from core.config import get_settings
from core.llm import GroqClient
from core.telegram_format import render_telegram_text, requested_answer_mode


CASES = (
    "Give a short eg of vernier caliper with solution and explanation.",
    "Solve this matrix multiplication in short: A = [[9,5,2],[1,8,5],[3,1,6]], "
    "B = [[3,2],[1,4],[5,3]].",
    "Briefly explain Ohm's law with one numerical example.",
    "Give a concise formula for kinetic energy and one use.",
    "Explain the 10% law in 4 lines.",
    "Give a detailed, step-by-step explanation of matrix multiplication.",
)
FORBIDDEN_MARKUP = ("$$", "\\begin", "<br", "|---", "###")


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = get_settings()
    client = GroqClient(
        settings.groq_api_key,
        settings.groq_models,
        vision_model=settings.groq_vision_model,
        attempts_per_model=settings.groq_attempts_per_model,
        max_retry_seconds=settings.groq_max_retry_seconds,
    )
    for number, question in enumerate(CASES, start=1):
        mode = requested_answer_mode(question, "detailed")
        response = await client.answer(
            question,
            answer_mode=mode,
            language="English",
        )
        final_text = render_telegram_text(response.text)
        found_markup = [token for token in FORBIDDEN_MARKUP if token in final_text]
        if found_markup:
            raise RuntimeError(
                f"Case {number} still contains unsupported Telegram markup: {found_markup}"
            )
        print(f"CASE_{number}_MODEL={response.model}")
        print(f"CASE_{number}_MODE={mode}")
        print(f"CASE_{number}_FINAL={final_text[:1800]}")


if __name__ == "__main__":
    asyncio.run(main())
