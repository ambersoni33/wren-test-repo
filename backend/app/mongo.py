"""MongoDB storage for live voice recording transcripts."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fieldnote.mongo")

DEFAULT_MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/voiceRecorder")
COLLECTION_NAME = "transcripts"

FALLBACK_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "transcripts_fallback.db"

_mongo_client: Any = None
_current_uri: str = DEFAULT_MONGO_URI
_db_name: str = "voiceRecorder"
_is_connected: bool = False


def get_database_name() -> str:
    return _db_name


def _database_name(uri: str) -> str:
    try:
        from pymongo.uri_parser import parse_uri

        parsed = parse_uri(uri)
        if parsed.get("database"):
            return str(parsed["database"])
    except Exception as exc:
        logger.warning("Could not read a database name from the MongoDB URI: %s", exc)
    return "voiceRecorder"


def get_current_uri() -> str:
    return _current_uri


def is_mongo_connected() -> bool:
    return _is_connected


def init_mongo(uri: str | None = None) -> bool:
    global _mongo_client, _current_uri, _db_name, _is_connected
    load_dotenv()
    if uri:
        _current_uri = uri.strip()
    else:
        _current_uri = os.getenv("MONGODB_URI", _current_uri).strip()
    _db_name = _database_name(_current_uri)

    try:
        import pymongo  # type: ignore

        if _mongo_client:
            try:
                _mongo_client.close()
            except Exception:
                pass

        _mongo_client = pymongo.MongoClient(
            _current_uri,
            serverSelectionTimeoutMS=2500,
            connectTimeoutMS=2500,
        )
        # Test connection
        _mongo_client.admin.command("ping")
        _is_connected = True
        logger.info("Successfully connected to MongoDB at %s", _current_uri)
        return True
    except Exception as exc:
        _is_connected = False
        logger.warning(
            "Could not connect to MongoDB (%s): %s. Falling back to local storage.",
            _current_uri,
            exc,
        )
        _init_fallback_db()
        return False


def set_mongo_uri(new_uri: str) -> bool:
    """Updates MongoDB connection string and attempts reconnection."""
    return init_mongo(new_uri)


def save_transcript_chunk(
    session_id: str,
    chunk_index: int,
    text: str,
    is_final: bool = False,
) -> dict[str, Any]:
    """Saves an audio chunk transcription into MongoDB (or fallback)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "session_id": session_id,
        "chunk_index": chunk_index,
        "text": text.strip(),
        "is_final": is_final,
        "created_at": now_iso,
    }

    if _is_connected and _mongo_client:
        try:
            db = _mongo_client[_db_name]
            collection = db[COLLECTION_NAME]

            # Upsert the chunk
            collection.update_one(
                {"session_id": session_id, "chunk_index": chunk_index},
                {"$set": record},
                upsert=True,
            )

            # Also maintain session summary document
            full_chunks = list(
                collection.find({"session_id": session_id}).sort("chunk_index", 1)
            )
            full_text = " ".join(c["text"] for c in full_chunks if c.get("text")).strip()

            db["sessions"].update_one(
                {"session_id": session_id},
                {
                    "$set": {
                        "session_id": session_id,
                        "full_transcript": full_text,
                        "updated_at": now_iso,
                        "chunk_count": len(full_chunks),
                        "is_complete": is_final,
                    }
                },
                upsert=True,
            )
            record["full_transcript"] = full_text
            record["source"] = "mongodb"
            record["summary"] = _read_summary(session_id)
            return record
        except Exception as exc:
            logger.error("MongoDB write error: %s. Writing to fallback.", exc)

    # Fallback to SQLite
    return _save_fallback(session_id, chunk_index, text, is_final, now_iso)


def list_sessions(limit: int = 40) -> list[dict[str, Any]]:
    """Returns finished recordings so the list can show real summary points."""
    if _is_connected and _mongo_client:
        try:
            return list(
                _mongo_client[_db_name]["sessions"]
                .find(
                    {},
                    {
                        "_id": 0,
                        "session_id": 1,
                        "summary": 1,
                        "updated_at": 1,
                        "summary_updated_at": 1,
                        "full_transcript": 1,
                    },
                )
                .sort("updated_at", -1)
                .limit(limit)
            )
        except Exception as exc:
            logger.error("MongoDB session list error: %s", exc)

    _init_fallback_db()
    with sqlite3.connect(FALLBACK_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT session_id, summary, updated_at
            FROM session_summaries
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {"session_id": row[0], "summary": row[1], "updated_at": row[2]}
        for row in rows
    ]


def get_session_transcript(session_id: str) -> dict[str, Any]:
    """Retrieves full transcript and all chunks for a session from MongoDB."""
    if _is_connected and _mongo_client:
        try:
            db = _mongo_client[_db_name]
            collection = db[COLLECTION_NAME]
            chunks = list(
                collection.find({"session_id": session_id}, {"_id": 0}).sort("chunk_index", 1)
            )
            full_text = " ".join(c["text"] for c in chunks if c.get("text")).strip()
            return {
                "session_id": session_id,
                "full_transcript": full_text,
                "summary": _read_summary(session_id),
                "chunk_count": len(chunks),
                "chunks": chunks,
                "source": "mongodb",
            }
        except Exception as exc:
            logger.error("MongoDB read error: %s", exc)

    # Fallback read
    return _get_fallback(session_id)


def save_final_transcript(
    session_id: str,
    text: str,
    engine: str,
    title: str = "",
) -> dict[str, Any]:
    """Stores one finished transcript for a recording."""
    now_iso = datetime.now(timezone.utc).isoformat()
    clean = text.strip()
    record = {
        "session_id": session_id,
        "chunk_index": 0,
        "text": clean,
        "is_final": True,
        "engine": engine,
        "title": title,
        "created_at": now_iso,
        "full_transcript": clean,
        "source": "mongodb",
    }

    if _is_connected and _mongo_client:
        try:
            db = _mongo_client[_db_name]
            collection = db[COLLECTION_NAME]
            collection.delete_many({"session_id": session_id})
            collection.insert_one(
                {
                    "session_id": session_id,
                    "chunk_index": 0,
                    "text": clean,
                    "is_final": True,
                    "engine": engine,
                    "title": title,
                    "created_at": now_iso,
                }
            )
            db["sessions"].update_one(
                {"session_id": session_id},
                {
                    "$set": {
                        "session_id": session_id,
                        "title": title,
                        "full_transcript": clean,
                        "engine": engine,
                        "updated_at": now_iso,
                        "chunk_count": 1,
                        "is_complete": True,
                    }
                },
                upsert=True,
            )
            return record
        except Exception as exc:
            logger.error("MongoDB write error: %s. Writing to fallback.", exc)

    _init_fallback_db()
    with sqlite3.connect(FALLBACK_DB_PATH) as connection:
        connection.execute("DELETE FROM transcript_chunks WHERE session_id = ?", (session_id,))
    saved = _save_fallback(session_id, 0, clean, True, now_iso)
    saved["engine"] = engine
    saved["title"] = title
    return saved


def save_session_summary(session_id: str, summary: str, model: str) -> None:
    """Stores the GPT summary on the finished recording."""
    now_iso = datetime.now(timezone.utc).isoformat()
    clean = summary.strip()
    if _is_connected and _mongo_client:
        try:
            db = _mongo_client[_db_name]
            db["sessions"].update_one(
                {"session_id": session_id},
                {
                    "$set": {
                        "session_id": session_id,
                        "summary": clean,
                        "summary_model": model,
                        "summary_updated_at": now_iso,
                    }
                },
                upsert=True,
            )
            return
        except Exception as exc:
            logger.error("MongoDB summary write error: %s. Writing to fallback.", exc)

    _init_fallback_db()
    with sqlite3.connect(FALLBACK_DB_PATH) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO session_summaries (session_id, summary, model, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, clean, model, now_iso),
        )


def _read_summary(session_id: str) -> str:
    if _is_connected and _mongo_client:
        try:
            document = _mongo_client[_db_name]["sessions"].find_one(
                {"session_id": session_id},
                {"_id": 0, "summary": 1},
            )
            if document and document.get("summary"):
                return str(document["summary"])
        except Exception as exc:
            logger.error("MongoDB summary read error: %s", exc)

    _init_fallback_db()
    with sqlite3.connect(FALLBACK_DB_PATH) as connection:
        row = connection.execute(
            "SELECT summary FROM session_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else ""


def _init_fallback_db():
    FALLBACK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(FALLBACK_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_summaries (
                session_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                model TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transcript_chunks (
                session_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                is_final INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, chunk_index)
            )
            """
        )


def _save_fallback(
    session_id: str, chunk_index: int, text: str, is_final: bool, now_iso: str
) -> dict[str, Any]:
    _init_fallback_db()
    with sqlite3.connect(FALLBACK_DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO transcript_chunks (session_id, chunk_index, text, is_final, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, chunk_index, text.strip(), 1 if is_final else 0, now_iso),
        )
        rows = conn.execute(
            "SELECT text FROM transcript_chunks WHERE session_id = ? ORDER BY chunk_index ASC",
            (session_id,),
        ).fetchall()
        full_text = " ".join(r[0] for r in rows if r[0]).strip()

    return {
        "session_id": session_id,
        "chunk_index": chunk_index,
        "text": text.strip(),
        "is_final": is_final,
        "created_at": now_iso,
        "full_transcript": full_text,
        "summary": _read_summary(session_id),
        "source": "local_fallback",
    }


def _get_fallback(session_id: str) -> dict[str, Any]:
    _init_fallback_db()
    with sqlite3.connect(FALLBACK_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM transcript_chunks WHERE session_id = ? ORDER BY chunk_index ASC",
            (session_id,),
        ).fetchall()

    chunks = [
        {
            "session_id": r["session_id"],
            "chunk_index": r["chunk_index"],
            "text": r["text"],
            "is_final": bool(r["is_final"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    full_text = " ".join(c["text"] for c in chunks if c["text"]).strip()
    return {
        "session_id": session_id,
        "full_transcript": full_text,
        "summary": _read_summary(session_id),
        "chunk_count": len(chunks),
        "chunks": chunks,
        "source": "local_fallback",
    }
