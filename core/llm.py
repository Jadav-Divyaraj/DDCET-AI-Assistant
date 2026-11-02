from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable

import httpx

from .telegram_format import telegram_style_instruction


LOGGER = logging.getLogger(__name__)
GROQ_API_ROOT = "https://api.groq.com/openai/v1"
OUT_OF_SCOPE_MARKER = "DDCET_OUT_OF_SCOPE"
INSUFFICIENT_SOURCE_MARKER = "DDCET_SOURCE_INSUFFICIENT"


class LlmUnavailableError(RuntimeError):
    """Raised when no configured model can complete the request."""


class ProviderAuthenticationError(LlmUnavailableError):
    """Raised when a provider rejects its configured credential."""


@dataclass(frozen=True)
class LlmResponse:
    text: str
    model: str


def unique_models(models: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(model.strip() for model in models if model.strip()))


class GroqClient:
    def __init__(
        self,
        api_key: str,
        models: str | Iterable[str],
        *,
        vision_model: str = "qwen/qwen3.6-27b",
        attempts_per_model: int = 2,
        max_retry_seconds: float = 4.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.api_key = api_key
        if isinstance(models, str):
            models = (models,)
        self.models = unique_models(models)
        self.vision_model = vision_model.strip()
        self.attempts_per_model = max(1, attempts_per_model)
        self.max_retry_seconds = max(0.0, max_retry_seconds)
        self.transport = transport
        self.sleep = sleep

    async def answer(
        self,
        question: str,
        context: str | None = None,
        *,
        history: list[dict[str, str]] | None = None,
        answer_mode: str = "short",
        language: str = "English",
    ) -> LlmResponse:
        self._require_configuration()
        messages = build_text_messages(
            question,
            context=context,
            history=history,
            answer_mode=answer_mode,
            language=language,
        )
        max_tokens = 950 if answer_mode == "detailed" else 600
        payload = {
            "messages": messages,
            "temperature": 0.25,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": "medium" if answer_mode == "detailed" else "low",
        }
        return await self._route_groq_chat(payload)

    async def analyze_images(
        self,
        prompt: str,
        image_paths: Iterable[Path],
        *,
        history: list[dict[str, str]] | None = None,
        answer_mode: str = "detailed",
        language: str = "English",
    ) -> LlmResponse:
        self._require_configuration()
        if not self.api_key:
            raise ProviderAuthenticationError(
                "GROQ_API_KEY is missing. Create a Groq key and add it to .env."
            )
        if not self.vision_model:
            raise LlmUnavailableError("No Groq vision model is configured.")

        images = list(image_paths)
        if not images:
            raise ValueError("At least one image is required.")
        if len(images) > 3:
            raise ValueError("Groq Qwen vision accepts at most three images per request.")

        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": build_vision_prompt(
                    prompt,
                    answer_mode=answer_mode,
                    language=language,
                ),
            }
        ]
        for path in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url(path)},
                }
            )

        messages: list[dict[str, object]] = []
        for item in (history or [])[-8:]:
            role = item.get("role", "")
            text = item.get("content", "")
            if role in {"user", "assistant"} and text:
                messages.append({"role": role, "content": text[:1800]})
        messages.append({"role": "user", "content": content})

        payload = {
            "model": self.vision_model,
            "messages": messages,
            "max_completion_tokens": 950 if answer_mode == "detailed" else 600,
            "reasoning_effort": "none",
            "temperature": 0.7,
            "top_p": 0.8,
        }
        data = await self._request_with_retries(
            f"{GROQ_API_ROOT}/chat/completions",
            payload,
            model=self.vision_model,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        text = extract_chat_completion_text(data)
        if text:
            return LlmResponse(text=text, model=self.vision_model)
        raise LlmUnavailableError(
            f"Groq vision model {self.vision_model} returned no text."
        )

    async def generate_quiz(self, topic: str, context: str | None = None, mistakes: list[dict[str, object]] | None = None) -> dict[str, object]:
        self._require_configuration()
        system = (
            "You are the DDCEThub DDCET Study Assistant. Generate exactly ONE Multiple Choice Question (MCQ) "
            "for the GTU Diploma to Degree Common Entrance Test (DDCET) preparation. "
            "The question must be relevant to the requested topic. If a context is provided, use it to form the question. "
            "If past mistakes are provided, specifically target those weak areas with a NEW question that tests the same concept. "
            "Output strictly a JSON object with no markdown fences, code blocks, or extra text. "
            'The JSON must have this exact structure: {"question": "...", "options": ["...", "...", "...", "..."], "correct_index": 0, "explanation": "..."}. '
            "There must be exactly 4 options. 'correct_index' must be 0, 1, 2, or 3. 'explanation' should be under 150 characters. "
            "Never use LaTeX math. Format math using plain unicode."
        )
        prompt = f"Topic: {topic}"
        if context:
            prompt += f"\nContext: {context}"
        if mistakes:
            prompt += "\nStudent's Past Mistakes in this topic (Do NOT repeat the exact same question, but test the concept):"
            for m in mistakes:
                prompt += f"\n- Question: {m.get('question')}\n  Wrong Answer Given: {m.get('wrong_answer')}\n  Correct Answer Was: {m.get('correct_answer')}"
        
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
        
        payload = {
            "messages": messages,
            "temperature": 0.5,
            "max_completion_tokens": 800,
            "response_format": {"type": "json_object"},
        }
        
        response = await self._route_groq_chat(payload)
        import json
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            raise LlmUnavailableError("The model failed to generate valid JSON.")

    async def _route_groq_chat(self, payload: dict[str, object]) -> LlmResponse:
        failures: list[str] = []
        for model in self.models:
            model_payload = dict(payload)
            model_payload["model"] = model
            try:
                data = await self._request_with_retries(
                    f"{GROQ_API_ROOT}/chat/completions",
                    model_payload,
                    model=model,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                text = extract_chat_completion_text(data)
                if isinstance(text, str) and text.strip():
                    return LlmResponse(text=text.strip(), model=model)
                failures.append(f"{model}: empty response")
            except ProviderAuthenticationError as exc:
                raise exc
            except LlmUnavailableError as exc:
                LOGGER.warning("Groq model route failed: %s", exc)
                failures.append(str(exc))
        raise LlmUnavailableError(
            "All configured Groq text models failed. " + " | ".join(failures)
        )

    async def _request_with_retries(
        self,
        url: str,
        payload: dict[str, object],
        *,
        model: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        last_error = "unknown error"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            transport=self.transport,
        ) as client:
            for attempt in range(1, self.attempts_per_model + 1):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    if attempt < self.attempts_per_model:
                        await self.sleep(self._retry_delay(None, attempt))
                        continue
                    break

                if response.status_code < 400:
                    data = response.json()
                    if not isinstance(data, dict):
                        raise LlmUnavailableError(
                            f"{model}: provider returned an invalid JSON object."
                        )
                    return data

                safe_detail = provider_error_detail(response)
                last_error = f"HTTP {response.status_code}: {safe_detail}"
                if response.status_code in {401, 403}:
                    raise ProviderAuthenticationError(f"{model}: {last_error}")
                retryable = response.status_code == 429 or response.status_code >= 500
                if retryable and attempt < self.attempts_per_model:
                    retry_after = response.headers.get("retry-after")
                    await self.sleep(self._retry_delay(retry_after, attempt))
                    continue
                break
        raise LlmUnavailableError(f"{model}: {last_error}")

    def _retry_delay(self, retry_after: str | None, attempt: int) -> float:
        try:
            requested = float(retry_after or "")
        except ValueError:
            requested = 0.5 * (2 ** (attempt - 1))
        return min(max(requested, 0.0), self.max_retry_seconds)

    def _require_configuration(self) -> None:
        if not self.api_key:
            raise ProviderAuthenticationError(
                "GROQ_API_KEY is missing. Create a Groq key and add it to .env."
            )
        if not self.models:
            raise LlmUnavailableError("No Groq text model is configured.")


def build_text_messages(
    question: str,
    *,
    context: str | None,
    history: list[dict[str, str]] | None,
    answer_mode: str,
    language: str,
) -> list[dict[str, str]]:
    if context:
        evidence_policy = (
            "Use the supplied APPROVED OR STUDENT-UPLOADED SOURCE EXCERPTS as the "
            "factual basis. If they do not support the requested fact, output exactly "
            f"{INSUFFICIENT_SOURCE_MARKER}. Do not invent citations, page numbers, "
            "document names, formulas, or facts."
        )
    else:
        evidence_policy = (
            "No verified source excerpt matched this question. You may provide a useful "
            "general DDCET-focused educational explanation from your knowledge, but do "
            "not invent an official rule, date, eligibility condition, notification, "
            "source, page number, exam pattern, cut-offs, or total marks. State uncertainty when appropriate."
        )
    system = (
        "You are the DDCEThub DDCET Study Assistant. Behave like a capable conversational "
        "tutor: understand informal wording, remember follow-ups from the supplied chat "
        "history, and give clear exam-focused answers. In this application DDCET means "
        "only Gujarat Technological University's Diploma to Degree Common Entrance Test "
        "for Gujarat (Exam Pattern: 100 MCQs, 2 marks each, total 200 marks, 150 mins, with negative marking); "
        "never describe similarly named examinations from other states. "
        "Official GTU DDCET Syllabus Structure: "
        "1. BE-01 Basics of Science & Engineering (50 Qs, 100 Marks): Units & Measurement (12%), Classical Mechanics (12%), Electric Current (12%), Heat & Thermometry (12%), Wave motion/Optics/Acoustics (12%), Chemical Reactions (6%), Acids Bases & Salts (8%), Metals & Non-metals (6%), Computer Practice (10%), Environmental Sciences (10%). "
        "2. BE-02 Aptitude Test (50 Qs, 100 Marks): Mathematics covers ONLY: Determinants & Matrices (8%), Trigonometry (6%), Vectors (4%), Coordinate Geometry (4%), Function & Limit (6%), Differentiation & Applications (8%), Integration (8%), Logarithm (4%), Statistics (2% - Mean/Median/Mode for ungrouped data only). Note: Probability, Permutations, Combinations, and Quadratic Equations are NOT in DDCET. "
        "Soft Skills & English covers: Comprehension (10%), Theory of Communication (10%), Techniques of Writing (10%), Grammar (10%), Correction of Words & Sentences (10%). "
        f"{evidence_policy} "
        "Treat academic concept questions from engineering, mathematics, aptitude, "
        "Environmental Science, and other plausible DDCET preparation subjects as "
        "in-scope even when the student does not write the word DDCET. "
        "If a user asks about your identity, capabilities, or what model you are, "
        "always reply that you are the DDCEThub DDCET Study Assistant, here to help them prepare for their exams, "
        "and NEVER mention Groq, OpenAI, Llama, Qwen, Mixtral, or any underlying AI models. "
        "If a user asks about your creator, developer, engineer, author, founder, owner, "
        "who built/coded/made/trained you, or whose project this is (in English, Hinglish, or Gujarati, "
        "e.g., 'who made you', 'developer kon che', 'kisne banaya', 'are you made by OpenAI'): "
        "always clearly state that you are the DDCEThub DDCET Study Assistant, developed and engineered "
        "by Divyaraj Jadav to help students prepare for the Gujarat Technological University's DDCET exam. "
        "Never attribute your creation to OpenAI, Groq, Meta, or other third-party AI companies. "
        "Only when a request is clearly completely unrelated to academics or student "
        "preparation, do not refuse outright. Instead, politely ask the student to elaborate "
        "or clarify how it relates to their studies. "
        f"Write in {language}. Use a {answer_mode} answer style. Do not expose hidden "
        "reasoning. Return only the student-facing final answer. "
        f"{telegram_style_instruction(answer_mode)}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for item in (history or [])[-12:]:
        role = item.get("role", "")
        content = item.get("content", "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content[:2500]})

    user_content = question
    if context:
        user_content = (
            "SOURCE EXCERPTS:\n\n"
            f"{context}\n\nSTUDENT QUESTION:\n{question}\n\n"
            "Answer from the source excerpts. If the question is a general greeting or asks about your capabilities, "
            "answer it directly and ignore the excerpts. Do not add a separate source list because "
            "the application appends verified references."
        )
    messages.append({"role": "user", "content": user_content})
    return messages


def build_vision_prompt(prompt: str, *, answer_mode: str, language: str) -> str:
    return (
        "You are the DDCEThub DDCET Study Assistant. Analyze only the attached image or "
        "rendered PDF page(s). Explain the visible content as a DDCET tutor, including "
        "labels, relationships, formulas, questions, or diagrams that are actually "
        "visible. \n\n"
        "CRITICAL MATH INSTRUCTION: If the image contains a dense list of mathematical formulas (like limits, "
        "derivatives, or trigonometry), you MUST transcribe the exact formulas shown in the image verbatim. "
        "Do NOT hallucinate general algebraic identities (like (a+b)^2) unless they are literally printed on the page. "
        "Read visible labels and characteristic parts before naming an instrument, "
        "diagram, or formula. Never substitute a different instrument name; if the image is "
        "unclear, say what is visible and state uncertainty. Never invent text hidden by blur "
        "or cropping. If this image is completely unrelated to academics or study material, "
        "do not refuse outright. Instead, politely ask the student to elaborate. Write in {language} using a "
        f"{answer_mode} exam-focused style. Return only the student-facing explanation: "
        "do not restate the request, describe your analysis process, or expose hidden "
        f"reasoning. {telegram_style_instruction(answer_mode)}\n\n"
        f"Student request: {prompt}"
    )


def image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def extract_chat_completion_text(data: dict[str, object]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def provider_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or response.reason_phrase)[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message[:300]
        if isinstance(error, str):
            return error[:300]
    return response.reason_phrase
