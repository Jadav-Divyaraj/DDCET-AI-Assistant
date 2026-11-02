from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from core.config import get_settings
from core.llm import GroqClient


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

    overview = await client.answer(
        "What is DDCET?",
        answer_mode="short",
        language="English",
    )
    print(f"TEXT_1_MODEL={overview.model}")
    print(f"TEXT_1={overview.text[:500]}")

    law = await client.answer(
        "Explain the 10 percent law of energy transfer",
        answer_mode="short",
        language="English",
    )
    print(f"TEXT_2_MODEL={law.model}")
    print(f"TEXT_2={law.text[:500]}")

    follow_up = await client.answer(
        "Explain that again in one very simple sentence.",
        history=[
            {"role": "user", "content": "Explain the 10 percent law of energy transfer"},
            {"role": "assistant", "content": law.text},
        ],
        answer_mode="short",
        language="English",
    )
    print(f"FOLLOW_UP_MODEL={follow_up.model}")
    print(f"FOLLOW_UP={follow_up.text[:500]}")

    with tempfile.TemporaryDirectory() as directory:
        image_path = Path(directory) / "energy-flow.png"
        image = Image.new("RGB", (1000, 240), "white")
        draw = ImageDraw.Draw(image)
        labels = ["Sun", "Plants", "Herbivores", "Carnivores"]
        x_positions = [70, 300, 560, 800]
        for index, (label, x) in enumerate(zip(labels, x_positions)):
            draw.rounded_rectangle((x, 80, x + 140, 160), radius=12, outline="black", width=3)
            draw.text((x + 25, 108), label, fill="black")
            if index < len(labels) - 1:
                draw.line((x + 140, 120, x_positions[index + 1], 120), fill="black", width=3)
        image.save(image_path)
        vision = await client.analyze_images(
            "Explain the visible energy-flow diagram for a DDCET Environmental Science student.",
            [image_path],
            answer_mode="short",
            language="English",
        )
    print(f"VISION_MODEL={vision.model}")
    print(f"VISION={vision.text[:500]}")


if __name__ == "__main__":
    asyncio.run(main())
