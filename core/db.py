from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


UTC = timezone.utc


@dataclass(frozen=True)
class Reminder:
    id: int
    chat_id: int
    due_at: datetime
    text: str
    source: str


@dataclass(frozen=True)
class Chunk:
    id: int
    document_id: int
    user_id: int
    text: str
    embedding: list[float]


@dataclass(frozen=True)
class Student:
    user_id: int
    display_name: str
    preferred_language: str
    answer_mode: str


@dataclass(frozen=True)
class ConversationMessage:
    id: int
    conversation_id: int
    role: str
    content: str
    source_refs: list[dict[str, object]]
    created_at: datetime


@dataclass(frozen=True)
class ApprovedChunk:
    id: int
    document_id: int
    title: str
    subject: str
    unit: str
    topic: str
    document_type: str
    page_number: int
    text: str
    embedding: list[float]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER PRIMARY KEY,
                    assignment_reminders_enabled INTEGER NOT NULL DEFAULT 0,
                    gtu_news_enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    due_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_assignment_unique
                    ON reminders(chat_id, due_at, text, source);

                CREATE TABLE IF NOT EXISTS seen_news (
                    chat_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY(chat_id, url)
                );

                CREATE TABLE IF NOT EXISTS students (
                    user_id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    preferred_language TEXT NOT NULL DEFAULT 'English',
                    answer_mode TEXT NOT NULL DEFAULT 'short',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT 'DDCET study chat',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES students(user_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_conversation
                    ON conversations(user_id)
                    WHERE status = 'active';

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
                    FOREIGN KEY(user_id) REFERENCES students(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_conversation_messages_recent
                    ON conversation_messages(conversation_id, id DESC);

                CREATE TABLE IF NOT EXISTS approved_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    title TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    unit TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    document_type TEXT NOT NULL,
                    approval_status TEXT NOT NULL DEFAULT 'approved',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approved_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    page_number INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES approved_documents(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_approved_chunk_location
                    ON approved_chunks(document_id, page_number, chunk_index);

                CREATE INDEX IF NOT EXISTS idx_approved_document_metadata
                    ON approved_documents(subject, unit, topic, approval_status);

                CREATE TABLE IF NOT EXISTS user_performance (
                    user_id INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    total_attempts INTEGER NOT NULL DEFAULT 0,
                    correct_attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, topic),
                    FOREIGN KEY(user_id) REFERENCES students(user_id)
                );

                CREATE TABLE IF NOT EXISTS mistake_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    question TEXT NOT NULL,
                    wrong_answer TEXT NOT NULL,
                    correct_answer TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES students(user_id)
                );

                CREATE TABLE IF NOT EXISTS active_quizzes (
                    poll_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    correct_option_id INTEGER NOT NULL,
                    explanation TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "users", "gtu_news_enabled", "INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def ensure_user(self, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO users(chat_id, created_at)
                VALUES (?, ?)
                """,
                (chat_id, datetime.now(UTC).isoformat()),
            )

    def set_assignment_reminders(self, chat_id: int, enabled: bool) -> None:
        self.ensure_user(chat_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET assignment_reminders_enabled = ? WHERE chat_id = ?",
                (1 if enabled else 0, chat_id),
            )

    def assignment_reminders_enabled(self, chat_id: int) -> bool:
        self.ensure_user(chat_id)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT assignment_reminders_enabled FROM users WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return bool(row and row["assignment_reminders_enabled"])

    def set_gtu_news(self, chat_id: int, enabled: bool) -> None:
        self.ensure_user(chat_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET gtu_news_enabled = ? WHERE chat_id = ?",
                (1 if enabled else 0, chat_id),
            )

    def gtu_news_enabled(self, chat_id: int) -> bool:
        self.ensure_user(chat_id)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT gtu_news_enabled FROM users WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return bool(row and row["gtu_news_enabled"])

    def users_with_gtu_news_enabled(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM users WHERE gtu_news_enabled = 1"
            ).fetchall()
        return [int(row["chat_id"]) for row in rows]

    def users_with_assignment_reminders_enabled(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM users WHERE assignment_reminders_enabled = 1"
            ).fetchall()
        return [int(row["chat_id"]) for row in rows]

    def mark_news_seen(self, chat_id: int, url: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO seen_news(chat_id, url, seen_at)
                VALUES (?, ?, ?)
                """,
                (chat_id, url, datetime.now(UTC).isoformat()),
            )
        return cursor.rowcount > 0

    def add_reminder(self, chat_id: int, due_at: datetime, text: str, source: str = "user") -> int:
        self.ensure_user(chat_id)
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=UTC)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO reminders(chat_id, due_at, text, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    due_at.astimezone(UTC).isoformat(),
                    text,
                    source,
                    datetime.now(UTC).isoformat(),
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT id FROM reminders
                WHERE chat_id = ? AND due_at = ? AND text = ? AND source = ?
                """,
                (chat_id, due_at.astimezone(UTC).isoformat(), text, source),
            ).fetchone()
        return int(row["id"]) if row else 0

    def list_reminders(self, chat_id: int) -> list[Reminder]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, chat_id, due_at, text, source
                FROM reminders
                WHERE chat_id = ? AND status = 'pending'
                ORDER BY due_at ASC
                """,
                (chat_id,),
            ).fetchall()
        return [self._row_to_reminder(row) for row in rows]

    def delete_reminder(self, chat_id: int, reminder_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM reminders WHERE id = ? AND chat_id = ?",
                (reminder_id, chat_id),
            )
        return cursor.rowcount > 0

    def due_reminders(self, now: datetime) -> list[Reminder]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, chat_id, due_at, text, source
                FROM reminders
                WHERE status = 'pending' AND due_at <= ?
                ORDER BY due_at ASC
                LIMIT 100
                """,
                (now.astimezone(UTC).isoformat(),),
            ).fetchall()
        return [self._row_to_reminder(row) for row in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))

    def create_document(self, user_id: int, filename: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO documents(user_id, filename, created_at)
                VALUES (?, ?, ?)
                """,
                (user_id, filename, datetime.now(UTC).isoformat()),
            )
        return int(cursor.lastrowid)

    def add_chunk(
        self,
        document_id: int,
        user_id: int,
        chunk_index: int,
        text: str,
        embedding: list[float],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chunks(document_id, user_id, chunk_index, text, embedding_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    user_id,
                    chunk_index,
                    text,
                    json.dumps(embedding),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def chunks_for_user(self, user_id: int, limit: int = 500) -> list[Chunk]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, document_id, user_id, text, embedding_json
                FROM chunks
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [
            Chunk(
                id=int(row["id"]),
                document_id=int(row["document_id"]),
                user_id=int(row["user_id"]),
                text=str(row["text"]),
                embedding=json.loads(row["embedding_json"] or "[]"),
            )
            for row in rows
        ]

    def document_filenames(self, document_ids: set[int]) -> dict[int, str]:
        if not document_ids:
            return {}
        placeholders = ",".join("?" for _ in document_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT id, filename FROM documents WHERE id IN ({placeholders})",
                tuple(sorted(document_ids)),
            ).fetchall()
        return {int(row["id"]): str(row["filename"]) for row in rows}

    def ensure_student(self, user_id: int, display_name: str = "") -> Student:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO students(
                    user_id, display_name, preferred_language, answer_mode, created_at, updated_at
                )
                VALUES (?, ?, 'English', 'short', ?, ?)
                """,
                (user_id, display_name, now, now),
            )
            if display_name:
                conn.execute(
                    """
                    UPDATE students
                    SET display_name = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (display_name, now, user_id),
                )
            row = conn.execute(
                """
                SELECT user_id, display_name, preferred_language, answer_mode
                FROM students
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Could not create the student profile.")
        return self._row_to_student(row)

    def get_student(self, user_id: int) -> Student | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, display_name, preferred_language, answer_mode
                FROM students
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return self._row_to_student(row) if row else None

    def update_student_preferences(
        self,
        user_id: int,
        *,
        preferred_language: str | None = None,
        answer_mode: str | None = None,
    ) -> Student:
        self.ensure_student(user_id)
        updates: list[str] = []
        values: list[object] = []
        if preferred_language is not None:
            updates.append("preferred_language = ?")
            values.append(preferred_language)
        if answer_mode is not None:
            updates.append("answer_mode = ?")
            values.append(answer_mode)
        if updates:
            updates.append("updated_at = ?")
            values.append(datetime.now(UTC).isoformat())
            values.append(user_id)
            with self.connect() as conn:
                conn.execute(
                    f"UPDATE students SET {', '.join(updates)} WHERE user_id = ?",
                    values,
                )
        student = self.get_student(user_id)
        if student is None:
            raise RuntimeError("Student profile was not found.")
        return student

    def get_or_create_active_conversation(self, user_id: int) -> int:
        self.ensure_student(user_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM conversations
                WHERE user_id = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row:
                return int(row["id"])
            now = datetime.now(UTC).isoformat()
            cursor = conn.execute(
                """
                INSERT INTO conversations(user_id, title, status, created_at, updated_at)
                VALUES (?, 'DDCET study chat', 'active', ?, ?)
                """,
                (user_id, now, now),
            )
        return int(cursor.lastrowid)

    def start_new_conversation(self, user_id: int) -> int:
        self.ensure_student(user_id)
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET status = 'archived', updated_at = ?
                WHERE user_id = ? AND status = 'active'
                """,
                (now, user_id),
            )
            cursor = conn.execute(
                """
                INSERT INTO conversations(user_id, title, status, created_at, updated_at)
                VALUES (?, 'DDCET study chat', 'active', ?, ?)
                """,
                (user_id, now, now),
            )
        return int(cursor.lastrowid)

    def add_conversation_message(
        self,
        user_id: int,
        role: str,
        content: str,
        source_refs: list[dict[str, object]] | None = None,
    ) -> int:
        if role not in {"user", "assistant"}:
            raise ValueError("Conversation role must be user or assistant.")
        conversation_id = self.get_or_create_active_conversation(user_id)
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO conversation_messages(
                    conversation_id, user_id, role, content, source_refs_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    user_id,
                    role,
                    content,
                    json.dumps(source_refs or []),
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return int(cursor.lastrowid)

    def recent_conversation_messages(
        self,
        user_id: int,
        limit: int = 12,
    ) -> list[ConversationMessage]:
        conversation_id = self.get_or_create_active_conversation(user_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, role, content, source_refs_json, created_at
                FROM conversation_messages
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, user_id, max(1, limit)),
            ).fetchall()
        return [
            ConversationMessage(
                id=int(row["id"]),
                conversation_id=int(row["conversation_id"]),
                role=str(row["role"]),
                content=str(row["content"]),
                source_refs=json.loads(row["source_refs_json"] or "[]"),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in reversed(rows)
        ]

    def recent_user_questions(self, user_id: int, limit: int = 10) -> list[ConversationMessage]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, role, content, source_refs_json, created_at
                FROM conversation_messages
                WHERE user_id = ? AND role = 'user'
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, max(1, limit)),
            ).fetchall()
        return [
            ConversationMessage(
                id=int(row["id"]),
                conversation_id=int(row["conversation_id"]),
                role=str(row["role"]),
                content=str(row["content"]),
                source_refs=json.loads(row["source_refs_json"] or "[]"),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def forget_student_history(self, user_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM conversation_messages WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            count = int(row["count"]) if row else 0
            conn.execute("DELETE FROM conversation_messages WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        return count

    def upsert_approved_document(
        self,
        *,
        source_path: str,
        content_hash: str,
        filename: str,
        title: str,
        subject: str,
        unit: str,
        topic: str,
        document_type: str,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO approved_documents(
                    source_path, content_hash, filename, title, subject, unit, topic,
                    document_type, approval_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
                ON CONFLICT(source_path) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    filename = excluded.filename,
                    title = excluded.title,
                    subject = excluded.subject,
                    unit = excluded.unit,
                    topic = excluded.topic,
                    document_type = excluded.document_type,
                    approval_status = 'approved',
                    updated_at = excluded.updated_at
                """,
                (
                    source_path,
                    content_hash,
                    filename,
                    title,
                    subject,
                    unit,
                    topic,
                    document_type,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM approved_documents WHERE source_path = ?",
                (source_path,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Could not save the approved document.")
        return int(row["id"])

    def replace_approved_chunks(
        self,
        document_id: int,
        pages: list[tuple[int, list[tuple[str, list[float]]]]],
    ) -> int:
        now = datetime.now(UTC).isoformat()
        inserted = 0
        with self.connect() as conn:
            conn.execute("DELETE FROM approved_chunks WHERE document_id = ?", (document_id,))
            for page_number, chunks in pages:
                for chunk_index, (text, embedding) in enumerate(chunks):
                    conn.execute(
                        """
                        INSERT INTO approved_chunks(
                            document_id, page_number, chunk_index, text, embedding_json, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            document_id,
                            page_number,
                            chunk_index,
                            text,
                            json.dumps(embedding),
                            now,
                        ),
                    )
                    inserted += 1
        return inserted

    def approved_chunks(self, limit: int = 5000) -> list[ApprovedChunk]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.id,
                    c.document_id,
                    d.title,
                    d.subject,
                    d.unit,
                    d.topic,
                    d.document_type,
                    c.page_number,
                    c.text,
                    c.embedding_json
                FROM approved_chunks AS c
                JOIN approved_documents AS d ON d.id = c.document_id
                WHERE d.approval_status = 'approved'
                ORDER BY c.id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [
            ApprovedChunk(
                id=int(row["id"]),
                document_id=int(row["document_id"]),
                title=str(row["title"]),
                subject=str(row["subject"]),
                unit=str(row["unit"]),
                topic=str(row["topic"]),
                document_type=str(row["document_type"]),
                page_number=int(row["page_number"]),
                text=str(row["text"]),
                embedding=json.loads(row["embedding_json"] or "[]"),
            )
            for row in rows
        ]

    def approved_document_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM approved_documents
                WHERE approval_status = 'approved'
                """
            ).fetchone()
        return int(row["count"]) if row else 0

    def approved_chunk_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM approved_chunks AS c
                JOIN approved_documents AS d ON d.id = c.document_id
                WHERE d.approval_status = 'approved'
                """
            ).fetchone()
        return int(row["count"]) if row else 0

    def log_quiz_attempt(
        self,
        user_id: int,
        topic: str,
        question: str,
        wrong_answer: str | None,
        correct_answer: str,
        explanation: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_performance (user_id, topic, total_attempts, correct_attempts, last_attempt_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(user_id, topic) DO UPDATE SET
                    total_attempts = total_attempts + 1,
                    correct_attempts = correct_attempts + excluded.correct_attempts,
                    last_attempt_at = excluded.last_attempt_at
                """,
                (user_id, topic, 0 if wrong_answer else 1, now),
            )
            if wrong_answer:
                conn.execute(
                    """
                    INSERT INTO mistake_memory (user_id, topic, question, wrong_answer, correct_answer, explanation, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, topic, question, wrong_answer, correct_answer, explanation, now),
                )

    def get_weak_topics(self, user_id: int, limit: int = 3) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT topic, total_attempts, correct_attempts,
                       CAST(correct_attempts AS FLOAT) / total_attempts as accuracy
                FROM user_performance
                WHERE user_id = ? AND total_attempts >= 1
                ORDER BY accuracy ASC, last_attempt_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recent_mistakes(self, user_id: int, topic: str, limit: int = 3) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT question, wrong_answer, correct_answer, explanation
                FROM mistake_memory
                WHERE user_id = ? AND topic = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, topic, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_active_quiz(self, poll_id: str, topic: str, question: str, options: list[str], correct_option_id: int, explanation: str) -> None:
        import json
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO active_quizzes (poll_id, topic, question, options_json, correct_option_id, explanation, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (poll_id, topic, question, json.dumps(options), correct_option_id, explanation, now),
            )

    def get_active_quiz(self, poll_id: str) -> dict[str, object] | None:
        import json
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM active_quizzes WHERE poll_id = ?",
                (poll_id,)
            ).fetchone()
            if row:
                d = dict(row)
                d["options"] = json.loads(str(d["options_json"]))
                return d
            return None

    @staticmethod
    def _row_to_student(row: sqlite3.Row) -> Student:
        return Student(
            user_id=int(row["user_id"]),
            display_name=str(row["display_name"]),
            preferred_language=str(row["preferred_language"]),
            answer_mode=str(row["answer_mode"]),
        )

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            due_at=datetime.fromisoformat(row["due_at"]),
            text=str(row["text"]),
            source=str(row["source"]),
        )
