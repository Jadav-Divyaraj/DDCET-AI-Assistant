from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .db import Student


OFFICIAL_DDCET_URL = "https://gujacpc.admissions.nic.in/ddcet/"


@dataclass(frozen=True)
class MenuView:
    text: str
    reply_markup: InlineKeyboardMarkup


def main_menu_text(student: Student, *, welcome: bool = False) -> str:
    greeting = (
        f"Welcome, {student.display_name or 'DDCET student'}!\n\n"
        if welcome
        else ""
    )
    return (
        greeting
        + "DDCEThub DDCET Assistant\n\n"
        "Ask questions, open approved study material, find previous-paper topics, "
        "and manage your personal revision tools.\n\n"
        f"Language: {student.preferred_language}\n"
        f"Answer mode: {student.answer_mode.title()}\n\n"
        "Choose an option below."
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🤖 Ask DDCET", callback_data="menu:ask"),
                InlineKeyboardButton("📚 Study & Plan", callback_data="menu:study"),
            ],
            [
                InlineKeyboardButton("📝 Papers & PYQs", callback_data="menu:papers"),
                InlineKeyboardButton("🔍 DDCET Updates", callback_data="menu:updates"),
            ],
            [
                InlineKeyboardButton("📊 My Progress", callback_data="menu:progress"),
                InlineKeyboardButton("🧰 My Library", callback_data="menu:library"),
            ],
            [
                InlineKeyboardButton("👤 Profile", callback_data="menu:profile"),
                InlineKeyboardButton("❓ Help", callback_data="menu:help"),
            ],
        ]
    )


def study_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📚 Study Material",
                    callback_data="menu:study:materials",
                ),
                InlineKeyboardButton(
                    "🗓 Study Planner",
                    callback_data="menu:study:planner",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Revision Planner",
                    callback_data="menu:study:revision",
                ),
                InlineKeyboardButton(
                    "✅ Syllabus Tracker",
                    callback_data="menu:study:tracker",
                ),
            ],
            [back_button()],
        ]
    )


def papers_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📄 Previous Papers",
                    callback_data="menu:papers:previous",
                ),
                InlineKeyboardButton(
                    "🔎 PYQ Topic Finder",
                    callback_data="menu:papers:finder",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔥 Repeated Topics",
                    callback_data="menu:papers:repeated",
                )
            ],
            [back_button()],
        ]
    )


def library_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔖 Saved Answers",
                    callback_data="menu:library:answers",
                ),
                InlineKeyboardButton(
                    "📕 Mistake Notebook",
                    callback_data="menu:library:mistakes",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📝 Saved Questions",
                    callback_data="menu:library:questions",
                )
            ],
            [back_button()],
        ]
    )


def profile_text(student: Student) -> str:
    return (
        "👤 DDCET Student Profile\n\n"
        f"Name: {student.display_name or 'Not set'}\n"
        f"Language: {student.preferred_language}\n"
        f"Default answer mode: {student.answer_mode.title()}\n\n"
        "Choose your preferred language and answer length."
    )


def profile_keyboard(student: Student) -> InlineKeyboardMarkup:
    language = student.preferred_language
    mode = student.answer_mode

    def selected(label: str, active: bool) -> str:
        return f"✓ {label}" if active else label

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    selected("English", language == "English"),
                    callback_data="profile:language:English",
                ),
                InlineKeyboardButton(
                    selected("Gujarati", language == "Gujarati"),
                    callback_data="profile:language:Gujarati",
                ),
                InlineKeyboardButton(
                    selected("Hinglish", language == "Hinglish"),
                    callback_data="profile:language:Hinglish",
                ),
            ],
            [
                InlineKeyboardButton(
                    selected("Short", mode == "short"),
                    callback_data="profile:mode:short",
                ),
                InlineKeyboardButton(
                    selected("Detailed", mode == "detailed"),
                    callback_data="profile:mode:detailed",
                ),
            ],
            [back_button()],
        ]
    )


def help_text() -> str:
    return (
        "❓ DDCET Bot Help\n\n"
        "Ask a question:\n"
        "Send a normal message or use /ask followed by your question.\n\n"
        "Upload material:\n"
        "Send a DDCET image, PDF, PowerPoint, Word, or text file with an optional "
        "question in its caption.\n\n"
        "Useful commands:\n"
        "/menu - open the main menu\n"
        "/profile - language and answer preferences\n"
        "/history - recent questions\n"
        "/continue - continue your latest study chat\n"
        "/newchat - start a fresh study conversation\n"
        "/sources - approved knowledge-base status\n"
        "/forget confirm - delete stored conversation history\n\n"
        "Verified study answers show their source document and page whenever the "
        "approved material contains the answer."
    )


def build_menu_view(
    action: str,
    student: Student,
    *,
    website_url: str,
) -> MenuView:
    views = {
        "menu:main": MenuView(main_menu_text(student), main_menu_keyboard()),
        "menu:ask": MenuView(
            "🤖 Ask DDCET\n\n"
            "Send your DDCET question now. You can ask about a concept, syllabus "
            "topic, approved note, or previous paper.\n\n"
            "Examples:\n"
            "• Explain the 10% law of energy transfer.\n"
            "• What is the official DDCET subject weightage?\n"
            "• Explain this diagram in simple Hinglish.\n\n"
            "You can also upload an image, PDF, PowerPoint, Word, or text file.",
            one_back_keyboard(),
        ),
        "menu:study": MenuView(
            "📚 Study & Plan\n\n"
            "Browse approved DDCET material or prepare a personal study and revision "
            "plan.",
            study_menu_keyboard(),
        ),
        "menu:papers": MenuView(
            "📝 Papers & PYQs\n\n"
            "Open previous papers, find questions by topic, or review topics that "
            "occurred repeatedly in the available papers.",
            papers_menu_keyboard(),
        ),
        "menu:updates": MenuView(
            "🔍 DDCET Updates\n\n"
            "Official-source update search will be connected in its dedicated "
            "milestone. Until then, the bot will not guess current dates, rules, or "
            "announcements.\n\n"
            "Use the official DDCET portal below for verified current information.",
            link_and_back_keyboard("🌐 Official DDCET Portal", OFFICIAL_DDCET_URL),
        ),
        "menu:progress": MenuView(
            "📊 My Progress\n\n"
            "Progress tracking will use your questions, quizzes, saved answers, weak "
            "topics, and completed study-plan tasks. Collection will begin only when "
            "those activity features are enabled.",
            one_back_keyboard(),
        ),
        "menu:library": MenuView(
            "🧰 My Library\n\n"
            "Your personal revision area for saved explanations, difficult questions, "
            "and mistakes.",
            library_menu_keyboard(),
        ),
        "menu:profile": MenuView(profile_text(student), profile_keyboard(student)),
        "menu:help": MenuView(help_text(), one_back_keyboard()),
        "menu:study:materials": MenuView(
            "📚 Study Material\n\n"
            "The Telegram subject-and-unit browser will be connected to the approved "
            "PDF collection in the Study Material milestone. You can already upload a "
            "PDF here and ask questions from it.\n\n"
            "The complete DDCEThub library is available below.",
            link_and_section_keyboard("🌐 Open DDCEThub", website_url, "menu:study"),
        ),
        "menu:study:planner": MenuView(
            "🗓 Study Planner\n\n"
            "This tool will create daily tasks from your available time, exam date, "
            "pending topics, and weak areas. It will also support completion buttons "
            "and Telegram reminders.\n\n"
            "Implementation is scheduled after the material and topic catalogue.",
            section_back_keyboard("menu:study"),
        ),
        "menu:study:revision": MenuView(
            "🔄 Revision Planner\n\n"
            "This tool will divide the remaining days into syllabus completion, first "
            "revision, previous-paper practice, and final weak-topic revision.",
            section_back_keyboard("menu:study"),
        ),
        "menu:study:tracker": MenuView(
            "✅ Syllabus Tracker\n\n"
            "The complete tracker remains on DDCEThub. The bot can link to it now and "
            "later display synchronized progress when the website provides an API.",
            link_and_section_keyboard(
                "🌐 Open Syllabus Tracker",
                website_url,
                "menu:study",
            ),
        ),
        "menu:papers:previous": MenuView(
            "📄 Previous Papers\n\n"
            "The paper browser will let you select a year and receive the approved PDF "
            "directly in Telegram. The currently approved papers remain searchable by "
            "asking a normal question.",
            section_back_keyboard("menu:papers"),
        ),
        "menu:papers:finder": MenuView(
            "🔎 PYQ Topic Finder\n\n"
            "This tool will find questions by topic and show the paper year, question "
            "number, page, and original PDF. It requires structured question indexing, "
            "which is part of the Papers milestone.",
            section_back_keyboard("menu:papers"),
        ),
        "menu:papers:repeated": MenuView(
            "🔥 Repeated Topics\n\n"
            "This will report measurable past-paper frequency, such as ‘found in 3 of "
            "4 available papers.’ It will not claim to predict the next examination.",
            section_back_keyboard("menu:papers"),
        ),
        "menu:library:answers": MenuView(
            "🔖 Saved Answers\n\n"
            "You will be able to save an AI explanation, organize it by topic, reopen "
            "it, and start a revision quiz from it. The storage feature is planned "
            "after Papers and PYQ search.",
            section_back_keyboard("menu:library"),
        ),
        "menu:library:mistakes": MenuView(
            "📕 Mistake Notebook\n\n"
            "Incorrect quiz answers and questions marked difficult will appear here "
            "with the correct explanation and source.",
            section_back_keyboard("menu:library"),
        ),
        "menu:library:questions": MenuView(
            "📝 Saved Questions\n\n"
            "This section will hold previous-paper questions and student doubts that "
            "you want to practise again later.",
            section_back_keyboard("menu:library"),
        ),
    }
    return views.get(action, views["menu:main"])


def back_button(callback_data: str = "menu:main") -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Main Menu", callback_data=callback_data)


def one_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[back_button()]])


def section_back_keyboard(section: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back", callback_data=section), back_button()]]
    )


def link_and_back_keyboard(label: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, url=url)], [back_button()]]
    )


def link_and_section_keyboard(
    label: str,
    url: str,
    section: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, url=url)],
            [InlineKeyboardButton("🔙 Back", callback_data=section), back_button()],
        ]
    )
