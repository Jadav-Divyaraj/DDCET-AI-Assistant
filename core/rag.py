from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .db import Chunk, Database


KEYWORD_STOPWORDS = {
    "a",
    "about",
    "ai",
    "also",
    "am",
    "an",
    "and",
    "answer",
    "appeared",
    "are",
    "as",
    "at",
    "be",
    "bot",
    "can",
    "chat",
    "could",
    "details",
    "did",
    "do",
    "does",
    "explain",
    "for",
    "from",
    "give",
    "had",
    "has",
    "have",
    "he",
    "hello",
    "hey",
    "hi",
    "how",
    "i",
    "in",
    "is",
    "it",
    "know",
    "llm",
    "me",
    "mine",
    "my",
    "of",
    "on",
    "our",
    "ours",
    "paper",
    "please",
    "question",
    "questions",
    "she",
    "should",
    "show",
    "talk",
    "talking",
    "tell",
    "the",
    "their",
    "them",
    "they",
    "this",
    "to",
    "us",
    "was",
    "we",
    "were",
    "what",
    "when",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "yours",
}


@dataclass(frozen=True)
class RetrievalResult:
    text: str
    score: float
    document_id: int = 0
    filename: str = ""


@dataclass(frozen=True)
class ApprovedRetrievalResult:
    document_id: int
    title: str
    subject: str
    unit: str
    topic: str
    document_type: str
    page_number: int
    text: str
    score: float
    keyword_score: float


class RagIndex:
    def __init__(self, db: Database, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.db = db
        self.model_name = model_name
        self._model = None
        self._model_load_failed = False

    def add_document(self, user_id: int, filename: str, text: str) -> tuple[int, int]:
        chunks = split_text(text)
        document_id = self.db.create_document(user_id=user_id, filename=filename)
        embeddings = self.embed_many(chunks)
        for index, chunk in enumerate(chunks):
            embedding = embeddings[index] if index < len(embeddings) else []
            self.db.add_chunk(document_id, user_id, index, chunk, embedding)
        return document_id, len(chunks)

    def retrieve(self, user_id: int, query: str, limit: int = 4) -> list[RetrievalResult]:
        chunks = self.db.chunks_for_user(user_id)
        if not chunks:
            return []

        query_embedding = self.embed_one(query)
        if query_embedding:
            scored = [
                RetrievalResult(
                    chunk.text,
                    cosine_similarity(query_embedding, chunk.embedding),
                    document_id=chunk.document_id,
                )
                for chunk in chunks
                if chunk.embedding
            ]
        else:
            scored = [
                RetrievalResult(
                    chunk.text,
                    keyword_score(query, chunk),
                    document_id=chunk.document_id,
                )
                for chunk in chunks
            ]

        selected = sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
        filenames = self.db.document_filenames(
            {item.document_id for item in selected if item.document_id}
        )
        return [
            RetrievalResult(
                text=item.text,
                score=item.score,
                document_id=item.document_id,
                filename=filenames.get(item.document_id, ""),
            )
            for item in selected
        ]

    def retrieve_approved(self, query: str, limit: int = 5) -> list[ApprovedRetrievalResult]:
        chunks = self.db.approved_chunks()
        if not chunks:
            return []

        query_embedding = self.embed_one(query)
        query_years = set(re.findall(r"\b20\d{2}\b", query))
        query_lower = query.lower()
        results: list[ApprovedRetrievalResult] = []
        for chunk in chunks:
            text_lexical = keyword_score_text(query, chunk.text)
            metadata = (
                f"{chunk.title} {chunk.subject} {chunk.unit} "
                f"{chunk.topic} {chunk.document_type}"
            )
            metadata_lexical = keyword_score_text(query, metadata)
            lexical = max(text_lexical, metadata_lexical * 0.45)
            semantic = (
                cosine_similarity(query_embedding, chunk.embedding)
                if query_embedding and chunk.embedding
                else 0.0
            )
            if semantic:
                score = (semantic * 0.7) + (lexical * 0.3)
            else:
                score = lexical
            if query_years:
                title_years = set(re.findall(r"\b20\d{2}\b", chunk.title))
                if query_years & title_years:
                    score += 0.12
                elif chunk.document_type == "paper" and title_years:
                    score -= 0.08
            if (
                any(
                    term in query_lower
                    for term in (
                        "syllabus",
                        "weightage",
                        "topics covered",
                        "what is ddcet",
                        "about ddcet",
                        "define ddcet",
                        "full form of ddcet",
                        "ddcet full form",
                    )
                )
                and chunk.document_type == "syllabus"
            ):
                score += 0.18
            if (
                any(term in query_lower for term in ("previous paper", "appeared", "asked in"))
                and chunk.document_type == "paper"
            ):
                score += 0.08
            results.append(
                ApprovedRetrievalResult(
                    document_id=chunk.document_id,
                    title=chunk.title,
                    subject=chunk.subject,
                    unit=chunk.unit,
                    topic=chunk.topic,
                    document_type=chunk.document_type,
                    page_number=chunk.page_number,
                    text=chunk.text,
                    score=score,
                    keyword_score=lexical,
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:limit]

    def embed_one(self, text: str) -> list[float]:
        embedded = self.embed_many([text])
        return embedded[0] if embedded else []

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        if model is None:
            return [[] for _ in texts]
        vectors = model.encode(texts, normalize_embeddings=True)
        return [vector.astype(float).tolist() for vector in vectors]

    def _load_model(self):
        if self._model or self._model_load_failed:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        except Exception:
            self._model_load_failed = True
            self._model = None
        return self._model


def split_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        chunks.append(cleaned[start:end].strip())
        if end == len(cleaned):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def keyword_score(query: str, chunk: Chunk) -> float:
    return keyword_score_text(query, chunk.text)


def keyword_score_text(query: str, text: str) -> float:
    query_terms = set(meaningful_terms(query))
    chunk_terms = set(meaningful_terms(text))
    if not query_terms or not chunk_terms:
        return 0.0
    return len(query_terms & chunk_terms) / len(query_terms)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def meaningful_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in tokenize(text):
        if token in KEYWORD_STOPWORDS:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        terms.append(token)
    return terms
