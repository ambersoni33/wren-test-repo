"""Store each summary point in Postgres with pgvector and answer questions from those points."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fieldnote.vectors")

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
ANSWER_MODEL = "gpt-5.6-luna"
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "rag_answer.txt"
DEFAULT_URL = "postgresql://wren:wren@127.0.0.1:5433/wren"
TAG_RE = re.compile(r"\[([^\]]+)\]\s*$")


def database_url() -> str:
    load_dotenv()
    return os.getenv("PGVECTOR_URL", DEFAULT_URL).strip() or DEFAULT_URL


def init_vectors() -> None:
    """Creates the pgvector extension and the summary-point table."""
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS summary_points (
                id BIGSERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                point_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                category TEXT NOT NULL,
                recorded_at TIMESTAMPTZ,
                transcript TEXT NOT NULL DEFAULT '',
                embedding vector(1536) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (session_id, point_index)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS summary_points_embedding_idx
            ON summary_points USING hnsw (embedding vector_cosine_ops)
            """
        )


def index_summary(
    session_id: str,
    summary: str,
    recorded_at: str | None = None,
    transcript: str = "",
) -> int:
    """Embeds each summary point and replaces that recording's rows."""
    points = parse_points(summary)
    when = _parse_time(recorded_at)
    context = (transcript or "").strip()[:4000]
    with _connect() as connection:
        if not points:
            connection.execute("DELETE FROM summary_points WHERE session_id = %s", (session_id,))
            return 0
        vectors = _embed([point[0] for point in points])
        connection.execute("DELETE FROM summary_points WHERE session_id = %s", (session_id,))
        for index, ((text, category), vector) in enumerate(zip(points, vectors)):
            connection.execute(
                """
                INSERT INTO summary_points
                    (session_id, point_index, text, category, recorded_at, transcript, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (session_id, index, text, category, when, context, _as_vector(vector)),
            )
    return len(points)


def indexed_session_ids() -> set[str]:
    with _connect() as connection:
        rows = connection.execute("SELECT DISTINCT session_id FROM summary_points").fetchall()
    return {row[0] for row in rows}


def answer_question(question: str) -> dict:
    """Finds the nearest saved points, then asks the model to answer from them."""
    asked = question.strip()
    if not asked:
        raise RuntimeError("Ask a question first.")

    query_vector = _embed([asked])[0]
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT text, category, recorded_at, session_id,
                   1 - (embedding <=> %s) AS score
            FROM summary_points
            ORDER BY embedding <=> %s
            LIMIT 8
            """,
            (_as_vector(query_vector), _as_vector(query_vector)),
        ).fetchall()

    notes = [
        {
            "text": row[0],
            "category": row[1],
            "recorded_at": row[2].isoformat() if row[2] else "",
            "session_id": row[3],
            "score": round(float(row[4]), 3),
        }
        for row in rows
        if float(row[4]) >= 0.25
    ]
    answer = _write_answer(asked, notes)
    return {"answer": answer, "sources": notes}


def parse_points(summary: str) -> list[tuple[str, str]]:
    points: list[tuple[str, str]] = []
    for line in (summary or "").splitlines():
        cleaned = line.strip()
        if not cleaned.startswith("-") and not cleaned.startswith("•"):
            continue
        text = re.sub(r"^[-•]\s*", "", cleaned)
        tag = TAG_RE.search(text)
        category = "talked about"
        if tag:
            category = tag.group(1).strip().lower()
            text = text[: tag.start()].strip().rstrip(".;")
        if text:
            points.append((text, category))
    return points


def _write_answer(question: str, notes: list[dict]) -> str:
    if not PROMPT_PATH.is_file():
        raise RuntimeError(f"Answer prompt file is missing: {PROMPT_PATH}")
    template = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if notes:
        rendered = "\n".join(
            f"- {note['recorded_at'][:10] or 'undated'} [{note['category']}] {note['text']}"
            for note in notes
        )
    else:
        rendered = "(no matching notes)"
    prompt = template.replace("{{notes}}", rendered).replace("{{question}}", question)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing, so Wren could not answer.")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    try:
        response = client.responses.create(
            model=ANSWER_MODEL,
            input=prompt,
            reasoning={"effort": "low"},
        )
    except Exception as exc:
        logger.error("RAG answer failed: %s", exc)
        raise RuntimeError(f"Answer failed: {exc}") from exc

    answer = (getattr(response, "output_text", None) or "").strip()
    if not answer:
        raise RuntimeError("The answer came back empty.")
    if _too_close(answer, notes):
        try:
            retry = client.responses.create(
                model=ANSWER_MODEL,
                input=(
                    "Rewrite this reply in your own spoken words. Keep every fact. "
                    "Do not reuse a six-word stretch from the notes.\n\n"
                    f"Reply:\n{answer}\n\nNotes:\n{rendered}\n\nQuestion:\n{question}"
                ),
                reasoning={"effort": "low"},
            )
        except Exception as exc:
            logger.error("RAG rewrite failed: %s", exc)
            return answer
        rewritten = (getattr(retry, "output_text", None) or "").strip()
        if rewritten:
            return rewritten
    return answer


def _too_close(answer: str, notes: list[dict]) -> bool:
    spoken = " ".join(answer.lower().split())
    for note in notes:
        words = note.get("text", "").lower().split()
        for start in range(0, max(0, len(words) - 5)):
            phrase = " ".join(words[start : start + 6])
            if phrase and phrase in spoken:
                return True
    return False


def _embed(texts: list[str]) -> list[list[float]]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing, so the notes could not be embedded.")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    try:
        response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    except Exception as exc:
        logger.error("Embedding failed: %s", exc)
        raise RuntimeError(f"Embedding failed: {exc}") from exc
    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [item.embedding for item in ordered]
    if len(vectors) != len(texts) or any(len(vector) != EMBED_DIM for vector in vectors):
        raise RuntimeError("The embedding model returned an unexpected vector.")
    return vectors


def _as_vector(values: list[float]):
    from pgvector import Vector

    return Vector(values)


def _connect():
    import psycopg
    from pgvector.psycopg import register_vector

    connection = psycopg.connect(database_url(), autocommit=True)
    connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(connection)
    return connection


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
