from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User, Poll
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .config import Settings
from .db import Database
from .ddcet import (
    build_source_context,
    clean_model_text,
    contextualize_query,
    format_grounded_answer,
    format_source_footer,
    is_answerable,
    select_grounded_results,
    source_references,
    split_telegram_text,
)
from .llm import (
    INSUFFICIENT_SOURCE_MARKER,
    OUT_OF_SCOPE_MARKER,
    GroqClient,
    LlmResponse,
    LlmUnavailableError,
    ProviderAuthenticationError,
)
from .menu import (
    build_menu_view,
    help_text,
    main_menu_keyboard,
    main_menu_text,
    one_back_keyboard,
    profile_keyboard,
    profile_text,
)
from .media import (
    PRESENTATION_SUFFIXES,
    WORD_SUFFIXES,
    prepare_image_for_vision,
    render_office_document_pages,
    render_pdf_pages,
    safe_filename,
)
from .rag import ApprovedRetrievalResult, RagIndex, RetrievalResult
from .telegram_format import render_telegram_text, requested_answer_mode
from .text_extraction import extract_text
from .scraper import Scraper


LOGGER = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    student = database(context).ensure_student(user.id, user.full_name)
    database(context).get_or_create_active_conversation(user.id)
    await update.message.reply_text(
        main_menu_text(student, welcome=True),
        reply_markup=main_menu_keyboard(),
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    student = database(context).ensure_student(user.id, user.full_name)
    await update.message.reply_text(
        main_menu_text(student),
        reply_markup=main_menu_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        help_text(),
        reply_markup=one_back_keyboard(),
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    student = database(context).ensure_student(user.id, user.full_name)
    await update.message.reply_text(
        profile_text(student),
        reply_markup=profile_keyboard(student),
    )


async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, field, value = parts
    current = database(context).ensure_student(user.id, user.full_name)
    already_selected = (
        (field == "language" and value == current.preferred_language)
        or (field == "mode" and value == current.answer_mode)
    )
    if already_selected:
        await query.answer("Already selected")
        return
    await query.answer()
    if field == "language" and value in {"English", "Gujarati", "Hinglish"}:
        student = database(context).update_student_preferences(
            user.id,
            preferred_language=value,
        )
    elif field == "mode" and value in {"short", "detailed"}:
        student = database(context).update_student_preferences(
            user.id,
            answer_mode=value,
        )
    else:
        return
    await query.edit_message_text(
        profile_text(student),
        reply_markup=profile_keyboard(student),
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    await query.answer()
    student = database(context).ensure_student(user.id, user.full_name)
    view = build_menu_view(
        query.data or "menu:main",
        student,
        website_url=settings(context).ddcethub_website_url,
    )
    try:
        await query.edit_message_text(view.text, reply_markup=view.reply_markup)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(
            "Use /ask followed by a DDCET question.\n"
            "Example: /ask Explain the 10% law of energy transfer."
        )
        return
    await answer_question(update.message, context, question)


async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if update.message is None or user is None:
        return
    topic = " ".join(context.args).strip()
    if not topic:
        weak_topics = database(context).get_weak_topics(user.id, 1)
        if weak_topics:
            topic = str(weak_topics[0]["topic"])
            await update.message.reply_text(f"Generating a targeted quiz for your weak topic: {topic}")
        else:
            await update.message.reply_text(
                "Please specify a DDCET topic for the quiz.\n"
                "Example: /quiz matrix multiplication"
            )
            return
    
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        # Search the knowledge base for context
        retrieved = await asyncio.to_thread(rag(context).retrieve_approved, topic, 3)
        context_str = build_source_context(retrieved) if retrieved else None
        
        mistakes = database(context).get_recent_mistakes(user.id, topic, 3)
        quiz_data = await llm(context).generate_quiz(topic, context_str, mistakes=mistakes)
        
        question = str(quiz_data.get("question", "What is the answer?"))[:300]
        options = [str(opt)[:100] for opt in quiz_data.get("options", [])][:10]
        correct_index = int(str(quiz_data.get("correct_index", 0)))
        explanation = str(quiz_data.get("explanation", ""))[:200]
        
        if len(options) < 2:
            raise ValueError("Not enough options generated")
            
        message = await update.message.reply_poll(
            question=question,
            options=options,
            type=Poll.QUIZ,
            correct_option_id=correct_index,
            explanation=explanation,
            is_anonymous=False,
        )
        database(context).save_active_quiz(message.poll.id, topic, question, options, correct_index, explanation)
    except Exception as exc:
        LOGGER.warning("Quiz generation failed: %s", exc)
        await update.message.reply_text("Failed to generate a quiz for that topic. Please try again or use a different topic.")

async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    answer = update.poll_answer
    if not answer or not answer.user:
        return
    user_id = answer.user.id
    poll_id = answer.poll_id
    selected_option = answer.option_ids[0] if answer.option_ids else -1
    
    quiz_info = database(context).get_active_quiz(poll_id)
    if not quiz_info:
        return
        
    topic = str(quiz_info["topic"])
    question = str(quiz_info["question"])
    correct_option_id = int(str(quiz_info["correct_option_id"]))
    options = quiz_info.get("options", [])
    explanation = str(quiz_info["explanation"])
    
    is_correct = (selected_option == correct_option_id)
    
    wrong_answer = None
    if not is_correct and isinstance(options, list) and 0 <= selected_option < len(options):
        wrong_answer = options[selected_option]
        
    correct_answer = options[correct_option_id] if isinstance(options, list) and 0 <= correct_option_id < len(options) else ""
    
    database(context).log_quiz_attempt(
        user_id,
        topic,
        question,
        str(wrong_answer) if wrong_answer else None,
        str(correct_answer),
        explanation
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return
    await answer_question(update.message, context, update.message.text.strip())


async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    student = database(context).ensure_student(user.id, user.full_name)
    configured = settings(context)
    configured.upload_dir.mkdir(parents=True, exist_ok=True)

    telegram_file = None
    filename = "upload"
    mime_type = ""
    file_size = 0
    file_unique_id = "file"
    if message.document is not None:
        telegram_file = await context.bot.get_file(message.document.file_id)
        filename = message.document.file_name or "document"
        mime_type = message.document.mime_type or ""
        file_size = message.document.file_size or 0
        file_unique_id = message.document.file_unique_id
    elif message.photo:
        photo = message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)
        filename = f"photo_{photo.file_unique_id}.jpg"
        mime_type = "image/jpeg"
        file_size = photo.file_size or 0
        file_unique_id = photo.file_unique_id
    if telegram_file is None:
        return

    max_bytes = configured.max_upload_mb * 1024 * 1024
    if file_size and file_size > max_bytes:
        await message.reply_text(
            f"This file is too large. The current limit is {configured.max_upload_mb} MB."
        )
        return

    suffix = Path(filename).suffix.lower()
    is_pdf = suffix == ".pdf" or mime_type == "application/pdf"
    is_presentation = suffix in PRESENTATION_SUFFIXES or mime_type in {
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    is_word = suffix in WORD_SUFFIXES or mime_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    is_image = bool(message.photo) or suffix in {
        ".bmp",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    } or mime_type.startswith("image/")
    is_text = suffix in {".md", ".txt"} or mime_type.startswith("text/")
    if not (is_pdf or is_presentation or is_word or is_image or is_text):
        await message.reply_text(
            "Supported uploads are PDF, PPT, PPTX, PPS, PPSX, DOC, DOCX, TXT, "
            "Markdown, JPG, PNG, WEBP, BMP, and TIFF."
        )
        return

    # Download the file
    local_path = configured.upload_dir / str(user.id) / f"{file_unique_id}{suffix}"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    await telegram_file.download_to_drive(local_path)

    upload_data = {
        "local_path": str(local_path),
        "filename": filename,
        "mime_type": mime_type,
        "is_image": is_image,
        "is_pdf": is_pdf,
        "is_presentation": is_presentation,
        "is_word": is_word,
    }

    # If the user provided a caption with the photo/file, use it. Otherwise use default.
    caption = message.caption.strip() if message.caption else "Explain this uploaded document."

    # Call process directly without action menus
    await _process_upload(
        message, 
        user, 
        context, 
        upload_data, 
        action="explain", 
        prompt_override=caption
    )

async def animate_thinking(message: Message) -> None:
    frames = [
        "🤔 Analyzing document...",
        "🔍 Cross-referencing DDCET syllabus...",
        "🧠 Generating solution...",
        "✍️ Almost there..."
    ]
    try:
        while True:
            for frame in frames:
                try:
                    await message.edit_text(frame)
                except Exception:
                    pass
                await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        pass


async def _process_upload(
    message: Message,
    user,
    context: ContextTypes.DEFAULT_TYPE,
    upload_data: dict[str, object],
    action: str,
    prompt_override: str = ""
) -> None:
    student = database(context).ensure_student(user.id, user.full_name)
    configured = settings(context)
    user_dir = configured.upload_dir / str(user.id)
    
    local_path = Path(str(upload_data["local_path"]))
    filename = str(upload_data["filename"])
    mime_type = str(upload_data["mime_type"])
    is_image = bool(upload_data["is_image"])
    is_pdf = bool(upload_data["is_pdf"])
    is_presentation = bool(upload_data["is_presentation"])
    is_word = bool(upload_data["is_word"])
    
    processing_msg = await message.reply_text("Received. Processing the DDCET study file...")
    anim_task = asyncio.create_task(animate_thinking(processing_msg))
    
    try:
        recent = database(context).recent_conversation_messages(
            user.id,
            limit=configured.conversation_window,
        )
        
        if prompt_override:
            prompt = prompt_override
        else:
            if action == "solve":
                prompt = "Extract and solve all visible DDCET questions in this document step-by-step."
            elif action == "weakness":
                prompt = "Analyze this document and identify which DDCET topics are covered. List the concepts the student should review."
            else:
                prompt = "Explain this uploaded DDCET study material clearly and identify the important exam points."

        mode = requested_answer_mode(prompt, student.answer_mode)
        database(context).add_conversation_message(
            user.id,
            "user",
            f"[Uploaded {filename} - {action}] {prompt}",
        )

        if not configured.external_document_context_enabled:
            answer = (
                "The file was received, but cloud document analysis is disabled. Enable "
                "ALLOW_EXTERNAL_DOCUMENT_CONTEXT only after approving Groq processing for "
                "student uploads."
            )
            database(context).add_conversation_message(user.id, "assistant", answer)
            await processing_msg.edit_text(answer)
            return

        history_payload = history_for_model(recent)
        references: list[dict[str, object]] = []
        
        if is_image:
            prepared = await asyncio.to_thread(
                prepare_image_for_vision,
                local_path,
                user_dir / f"{local_path.stem}_vision.jpg",
            )
            response = await llm(context).analyze_images(
                prompt,
                [prepared],
                history=history_payload,
                answer_mode=mode,
                language=student.preferred_language,
            )
            answer = format_upload_answer(response, filename=filename, page_label="image")
            references = [{"title": filename, "page": "image", "kind": "upload"}]
        else:
            rendered: list[Path] = []
            extraction_path = local_path
            extraction_mime_type = mime_type
            page_kind = "pages"
            if is_presentation or is_word:
                office_render = await asyncio.to_thread(
                    render_office_document_pages,
                    local_path,
                    user_dir / f"{local_path.stem}_document",
                    limit=configured.max_pdf_vision_pages,
                )
                extraction_path = office_render.pdf_path
                extraction_mime_type = "application/pdf"
                rendered = office_render.page_images
                page_kind = office_render.page_kind

            extracted = await asyncio.to_thread(
                extract_text,
                extraction_path,
                extraction_mime_type,
                configured.tesseract_cmd,
            )
            if extracted.strip():
                await asyncio.to_thread(
                    rag(context).add_document,
                    user.id,
                    filename,
                    extracted,
                )

            if is_pdf:
                try:
                    rendered = await asyncio.to_thread(
                        render_pdf_pages,
                        local_path,
                        user_dir / f"{local_path.stem}_pages",
                        limit=configured.max_pdf_vision_pages,
                    )
                except Exception:
                    LOGGER.warning("PDF page rendering failed for %s", filename, exc_info=True)

            if rendered:
                visual_prompt = prompt
                if extracted.strip():
                    visual_prompt += (
                        "\n\nLocally extracted text from this document (layout may be "
                        f"imperfect):\n{extracted}"
                    )
                try:
                    response = await llm(context).analyze_images(
                        visual_prompt,
                        rendered,
                        history=history_payload,
                        answer_mode=mode,
                        language=student.preferred_language,
                    )
                    page_label = f"{page_kind} 1-{len(rendered)}"
                except LlmUnavailableError as exc:
                    LOGGER.warning("Vision model failed for %s, falling back to text: %s", filename, exc)
                    if extracted.strip():
                        response = await llm(context).answer(
                            prompt,
                            context=build_uploaded_context_from_text(filename, extracted),
                            history=history_payload,
                            answer_mode=mode,
                            language=student.preferred_language,
                        )
                        page_label = "text extraction (vision fallback)"
                    else:
                        raise exc
            elif extracted.strip():
                response = await llm(context).answer(
                    prompt,
                    context=build_uploaded_context_from_text(filename, extracted),
                    history=history_payload,
                    answer_mode=mode,
                    language=student.preferred_language,
                )
                page_label = "text extraction"
            else:
                answer = "I could not extract text or render readable pages from this file."
                database(context).add_conversation_message(user.id, "assistant", answer)
                await processing_msg.edit_text(answer)
                return
            answer = format_upload_answer(
                response,
                filename=filename,
                page_label=page_label,
            )
            references = [{"title": filename, "page": page_label, "kind": "upload"}]
            
        image_url = None
        image_match = re.search(r"\[IMAGE:\s*(.+?)\]", answer)
        if image_match:
            query = image_match.group(1).strip()
            answer = answer.replace(image_match.group(0), "").strip()
            scraper_inst = Scraper(settings(context).pdf_search_sources)
            image_url = await scraper_inst.fetch_image(query)
            if not image_url:
                answer += "\n\n*(I tried to fetch an image for this, but could not find a good match online.)*"

        database(context).add_conversation_message(
            user.id,
            "assistant",
            answer,
            source_refs=references,
        )

        if image_url:
            try:
                caption = answer[:1024]
                await processing_msg.delete()
                await message.reply_photo(photo=image_url, caption=caption, parse_mode="HTML")
                if len(answer) > 1024:
                    await send_long_message(message, answer[1024:], answer_keyboard())
                return
            except Exception as exc:
                LOGGER.warning("Failed to send image from Wikipedia: %s", exc)

        if len(answer) <= 4096:
            await processing_msg.edit_text(answer, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await processing_msg.delete()
            for i in range(0, len(answer), 4000):
                await message.reply_text(
                    answer[i : i + 4000],
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
    except ProviderAuthenticationError as exc:
        LOGGER.warning("Upload analysis failed: %s", exc)
        answer = "The file was saved, but the AI analysis service is temporarily unavailable."
        await processing_msg.edit_text(answer)
    except Exception as exc:
        LOGGER.error("Failed to process upload: %s", exc, exc_info=True)
        answer = "An error occurred while analyzing the file. Please try again."
        await processing_msg.edit_text(answer)
    finally:
        anim_task.cancel()


async def upload_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or query.message is None or user is None:
        return
    await query.answer()
    
    action = (query.data or "").removeprefix("upload:")
    if action not in {"solve", "explain", "weakness"}:
        return
        
    upload_data = context.user_data.get("pending_upload")
    if not upload_data:
        await query.message.edit_text("This upload session has expired. Please upload the file again.")
        return
        
    await query.message.delete()
    # Call process_upload with the original message that triggered the upload
    # However query.message is the bot's message. We need a way to reply to the user.
    # We can just send a new message.
    await _process_upload(query.message, user, context, upload_data, action)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is not None:
        LOGGER.error(
            "Unhandled Telegram update error",
            exc_info=(type(error), error, error.__traceback__),
        )
    else:
        LOGGER.error("Unhandled Telegram update error without exception details")
    if isinstance(update, Update) and update.callback_query is not None:
        try:
            await update.callback_query.answer(
                "That action could not be completed. Please try again.",
                show_alert=False,
            )
        except Exception:
            LOGGER.debug("Could not acknowledge failed callback", exc_info=True)
        return
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "Something went wrong while processing that request. Your saved "
                "profile and study history are safe. Please try again or use /menu."
            )
        except Exception:
            LOGGER.debug("Could not send the friendly error response", exc_info=True)


async def answer_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or query.message is None or user is None:
        return
    await query.answer()
    mode = (query.data or "").removeprefix("answer:")
    if mode not in {"short", "detailed"}:
        return
    questions = database(context).recent_user_questions(user.id, limit=1)
    if not questions:
        await query.message.reply_text("Ask a DDCET question first.")
        return
    await answer_question(
        query.message,
        context,
        questions[0].content,
        override_mode=mode,
        store_user_message=False,
        acting_user=user,
    )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    questions = database(context).recent_user_questions(user.id, limit=10)
    if not questions:
        await update.message.reply_text("Your DDCET question history is empty.")
        return
    lines = [
        f"{index}. {item.content[:180]}"
        for index, item in enumerate(questions, start=1)
    ]
    await update.message.reply_text("Recent DDCET questions:\n\n" + "\n\n".join(lines))


async def continue_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    messages = database(context).recent_conversation_messages(user.id, limit=6)
    if not messages:
        await update.message.reply_text("There is no previous conversation to continue.")
        return
    preview = "\n\n".join(
        f"{'You' if item.role == 'user' else 'Assistant'}: {item.content[:500]}"
        for item in messages
    )
    await send_long_message(
        update.message,
        "Latest study conversation:\n\n" + preview + "\n\nSend your next question to continue.",
    )


async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    database(context).start_new_conversation(user.id)
    await update.message.reply_text("Started a new DDCET study conversation.")


async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    if not context.args or context.args[0].lower() != "confirm":
        await update.message.reply_text(
            "This deletes your stored questions and answers.\n"
            "Use /forget confirm to continue."
        )
        return
    deleted = database(context).forget_student_history(user.id)
    database(context).get_or_create_active_conversation(user.id)
    await update.message.reply_text(f"Deleted {deleted} stored conversation messages.")


async def sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    count = database(context).approved_document_count()
    chunk_count = database(context).approved_chunk_count()
    await update.message.reply_text(
        "Approved DDCET knowledge base:\n"
        f"Documents: {count}\n"
        f"Searchable passages: {chunk_count}\n"
        f"Text routes: {', '.join(settings(context).groq_models)}\n"
        f"Vision route: {settings(context).groq_vision_model}\n"
        "Provider: Groq Cloud"
    )


async def answer_question(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
    *,
    override_mode: str | None = None,
    store_user_message: bool = True,
    acting_user: User | None = None,
) -> None:
    user = acting_user or message.from_user
    if user is None:
        return
    student = database(context).ensure_student(user.id, user.full_name)
    recent = database(context).recent_conversation_messages(
        user.id,
        limit=settings(context).conversation_window,
    )
    retrieval_query = contextualize_query(question, recent)
    if store_user_message:
        database(context).add_conversation_message(user.id, "user", question)

    await message.chat.send_action(ChatAction.TYPING)
    approved_results, uploaded_results = await asyncio.gather(
        asyncio.to_thread(rag(context).retrieve_approved, retrieval_query, 15),
        asyncio.to_thread(rag(context).retrieve, user.id, retrieval_query, 15),
    )
    grounded_results = (
        select_grounded_results(question, approved_results)
        if approved_results and is_answerable(question, approved_results)
        else []
    )
    relevant_uploads = [item for item in uploaded_results if item.score >= 0.30]

    context_blocks: list[str] = []
    if grounded_results:
        context_blocks.append(build_source_context(grounded_results))
    if relevant_uploads:
        context_blocks.append(build_uploaded_context(relevant_uploads))
    source_context = "\n\n".join(context_blocks) or None
    if source_context and not settings(context).external_document_context_enabled:
        answer = (
            "I found relevant approved or uploaded DDCET material, but cloud document "
            "context is disabled. The bot owner must explicitly approve sending "
            "retrieved excerpts to Groq Cloud before I can generate this answer."
        )
        references = source_references(grounded_results) + uploaded_source_references(
            relevant_uploads
        )
        database(context).add_conversation_message(
            user.id,
            "assistant",
            answer,
            source_refs=references,
        )
        await message.reply_text(answer)
        return
    context_for_model = (
        source_context
        if settings(context).external_document_context_enabled
        else None
    )

    mode = override_mode or requested_answer_mode(question, student.answer_mode)
    history_payload = history_for_model(recent)
    try:
        response = await llm(context).answer(
            question,
            context=context_for_model,
            history=history_payload,
            answer_mode=mode,
            language=student.preferred_language,
        )
        if INSUFFICIENT_SOURCE_MARKER in response.text:
            scraper = Scraper(settings(context).pdf_search_sources)
            web_results = await scraper.search_web(question)
            
            if web_results:
                web_context_blocks = ["LATEST WEB SEARCH RESULTS:"]
                for res in web_results:
                    web_context_blocks.append(f"- {res.title} (Source: {res.url})")
                
                web_context = "\n".join(web_context_blocks)
                if context_for_model:
                    context_for_model = f"{web_context}\n\n{context_for_model}"
                else:
                    context_for_model = web_context
            else:
                context_for_model = None

            response = await llm(context).answer(
                question,
                context=context_for_model,
                history=history_payload,
                answer_mode=mode,
                language=student.preferred_language,
            )
    except ProviderAuthenticationError as exc:
        LOGGER.error("Groq authentication failed: %s", exc)
        answer = (
            "The bot's Groq API key is invalid or missing. The bot administrator must "
            "replace GROQ_API_KEY before Ask DDCET can answer questions."
        )
        database(context).add_conversation_message(user.id, "assistant", answer)
        await message.reply_text(answer)
        return
    except LlmUnavailableError as exc:
        LOGGER.warning("All configured Groq models failed: %s", exc)
        answer = (
            "The DDCET explanation service is temporarily unavailable after trying "
            "the configured Groq models. Please try again shortly."
        )
        database(context).add_conversation_message(user.id, "assistant", answer)
        await message.reply_text(answer)
        return

    if OUT_OF_SCOPE_MARKER in response.text:
        answer = (
            "That's an interesting question, but as the DDCEThub DDCET Study Assistant, "
            "I specialize strictly in DDCET preparation and syllabus topics.\n\n"
        ) + main_menu_text(student)
        references: list[dict[str, object]] = []
        keyboard = main_menu_keyboard()
    elif context_for_model:
        answer, references = format_contextual_answer(
            response,
            grounded_results,
            relevant_uploads,
        )
        keyboard = answer_keyboard()
    else:
        answer = clean_model_text(response.text)
        references = []
        keyboard = answer_keyboard()

    image_url = None
    image_match = re.search(r"\[IMAGE:\s*(.+?)\]", answer)
    if image_match:
        query = image_match.group(1).strip()
        answer = answer.replace(image_match.group(0), "").strip()
        scraper_inst = Scraper(settings(context).pdf_search_sources)
        image_url = await scraper_inst.fetch_image(query)
        if not image_url:
            answer += "\n\n*(I tried to fetch an image for this, but could not find a good match online.)*"

    database(context).add_conversation_message(
        user.id,
        "assistant",
        answer,
        source_refs=references,
    )

    if image_url:
        try:
            # Telegram captions max out at 1024 characters
            caption = answer[:1024]
            await message.reply_photo(photo=image_url, caption=caption, parse_mode="HTML")
            if len(answer) > 1024:
                await send_long_message(message, answer[1024:], keyboard)
            return
        except Exception as exc:
            LOGGER.warning("Failed to send image from Wikipedia: %s", exc)
            
    await send_long_message(message, answer, keyboard)


def history_for_model(messages) -> list[dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in messages]


def build_uploaded_context(results: list[RetrievalResult]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(results, start=1):
        blocks.append(
            f"[STUDENT UPLOAD EXCERPT {index}]\n"
            f"Document: {item.filename or 'Student upload'}\n"
            f"Text:\n{render_telegram_text(item.text)}"
        )
    return "\n\n".join(blocks)


def build_uploaded_context_from_text(filename: str, text: str) -> str:
    return f"[STUDENT UPLOAD]\nDocument: {filename}\nText:\n{render_telegram_text(text)}"


def uploaded_source_references(
    results: list[RetrievalResult],
) -> list[dict[str, object]]:
    references: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for item in results:
        page_match = re.search(r"\[Page\s+(\d+)\]", item.text, flags=re.IGNORECASE)
        page: object = int(page_match.group(1)) if page_match else "extracted text"
        key = (item.document_id, str(page))
        if key in seen:
            continue
        seen.add(key)
        references.append(
            {
                "document_id": item.document_id,
                "title": item.filename or "Student upload",
                "page": page,
                "kind": "upload",
            }
        )
    return references


def format_contextual_answer(
    response: LlmResponse,
    approved_results: list[ApprovedRetrievalResult],
    uploaded_results: list[RetrievalResult],
) -> tuple[str, list[dict[str, object]]]:
    approved_refs = source_references(approved_results)
    upload_refs = uploaded_source_references(uploaded_results)
    if approved_results:
        answer = format_grounded_answer(
            response.text,
            approved_results,
            extra_references=upload_refs,
        )
    else:
        answer = clean_model_text(response.text)
        if upload_refs:
            answer += "\n\n" + format_source_footer(upload_refs)
    return answer, approved_refs + upload_refs


def format_upload_answer(
    response: LlmResponse,
    *,
    filename: str,
    page_label: str,
) -> str:
    text = clean_model_text(response.text)
    if OUT_OF_SCOPE_MARKER in text:
        text = (
            "This upload does not appear to be related to DDCET study material. "
            "Please upload a syllabus page, notes, diagram, or question paper."
        )
    return (
        f"{text}\n\n"
        + format_source_footer([{"title": filename, "page": page_label}])
    )


def answer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Short Answer", callback_data="answer:short"),
                InlineKeyboardButton(
                    "Detailed Explanation",
                    callback_data="answer:detailed",
                ),
            ]
        ]
    )


async def send_long_message(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    parts = split_telegram_text(render_telegram_text(text))
    for index, part in enumerate(parts):
        await message.reply_text(
            part,
            reply_markup=reply_markup if index == len(parts) - 1 else None,
        )


def settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def database(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def llm(context: ContextTypes.DEFAULT_TYPE) -> GroqClient:
    return context.application.bot_data["llm"]


def rag(context: ContextTypes.DEFAULT_TYPE) -> RagIndex:
    return context.application.bot_data["rag"]
