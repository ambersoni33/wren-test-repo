"""Fieldnote API: store voice notes and their audio files."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fieldnote")

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .db import connect, init_db
from .mongo import (
    get_current_uri,
    get_database_name,
    get_session_transcript,
    list_sessions,
    init_mongo,
    is_mongo_connected,
    save_final_transcript,
    save_session_summary,
    save_transcript_chunk,
    set_mongo_uri,
)
from .summarizer import SUMMARY_MODEL, summarize_transcript
from .transcriber import transcribe_audio_file
from .vectors import answer_question, index_summary, indexed_session_ids, init_vectors

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
STATIC_DIR = BASE_DIR / "static"
MAX_BYTES = 40 * 1024 * 1024
MAX_DURATION_MS = 6 * 60 * 60 * 1000
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")

MIME_EXTENSIONS = {
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/ogg": ".ogg",
    "audio/3gpp": ".3gp",
    "audio/3gp": ".3gp",
}

EXTENSION_MIME = {ext: mime for mime, ext in MIME_EXTENSIONS.items() if ext not in {".m4a", ".mp3", ".3gp", ".wav"}}
EXTENSION_MIME.update(
    {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".aac": "audio/aac",
        ".mp3": "audio/mpeg",
        ".webm": "audio/webm",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".3gp": "audio/3gpp",
    }
)


class RecordingOut(BaseModel):
    id: str
    client_id: str
    title: str
    duration_ms: int
    created_at: str
    favorite: bool
    mime_type: str
    size_bytes: int
    waveform: list[float]
    audio_url: str


class RecordingPatch(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    favorite: bool | None = None


class MongoConfigIn(BaseModel):
    uri: str


class AIConfigIn(BaseModel):
    openai_api_key: str | None = None
    groq_api_key: str | None = None


class ChatIn(BaseModel):
    message: str


transcript_subscribers: dict[str, list[asyncio.Queue]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    init_mongo()
    try:
        init_vectors()
        _index_saved_summaries()
    except Exception as exc:
        logger.warning("Vector memory is not ready yet: %s", exc)
    yield


app = FastAPI(title="Fieldnote", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/test", response_class=FileResponse)
@app.get("/", response_class=FileResponse)
def serve_test_client():
    test_file = STATIC_DIR / "test_recorder.html"
    if test_file.is_file():
        return FileResponse(
            test_file,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
    raise HTTPException(status_code=404, detail="Test client page not found.")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    try:
        return answer_question(body.message)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/chat/voice")
async def chat_voice(file: UploadFile = File(...)) -> dict:
    """Turn a spoken question into text with Whisper, then answer from saved notes."""
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is larger than 40 MB.")

    _, extension = _resolve_type(file.content_type or "", file.filename or "question.webm")
    dest_dir = DATA_DIR / "chat_voice"
    dest_dir.mkdir(parents=True, exist_ok=True)
    audio_path = dest_dir / f"{uuid.uuid4().hex}{extension}"
    audio_path.write_bytes(payload)
    try:
        transcription = transcribe_audio_file(audio_path)
        spoken = (transcription.get("text") or "").strip()
        if not spoken:
            return {"answer": "I didn’t catch that. Try saying it once more."}
        return {"answer": answer_question(spoken)["answer"]}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        audio_path.unlink(missing_ok=True)


@app.get("/api/recordings", response_model=list[RecordingOut])
def list_recordings(q: str | None = None) -> list[RecordingOut]:
    query = "SELECT * FROM recordings"
    params: list[object] = []
    if q and q.strip():
        query += " WHERE title LIKE ?"
        params.append(f"%{q.strip()}%")
    query += " ORDER BY created_at DESC"
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [_to_out(row) for row in rows]


@app.get("/api/recordings/{recording_id}", response_model=RecordingOut)
def get_recording(recording_id: str) -> RecordingOut:
    row = _get_row(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    return _to_out(row)


@app.get("/api/recordings/{recording_id}/audio")
def get_audio(recording_id: str) -> FileResponse:
    row = _get_row(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = AUDIO_DIR / row["filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is missing.")
    return FileResponse(
        path,
        media_type=row["mime_type"],
        filename=f"{row['title']}{path.suffix}",
        content_disposition_type="inline",
    )


@app.post("/api/recordings", response_model=RecordingOut)
async def create_recording(
    file: UploadFile = File(...),
    client_id: str = Form(...),
    title: str = Form(...),
    duration_ms: int = Form(...),
    created_at: str = Form(...),
    favorite: bool = Form(False),
    waveform: str = Form("[]"),
) -> RecordingOut:
    client_id = client_id.strip()
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise HTTPException(status_code=422, detail="client_id must be 8–80 letters, numbers, _ or -.")

    clean_title = _clean_title(title)
    created = _parse_time(created_at)
    if duration_ms < 0 or duration_ms > MAX_DURATION_MS:
        raise HTTPException(status_code=422, detail="duration_ms is out of range.")
    levels = _parse_waveform(waveform)

    with connect() as connection:
        existing = connection.execute(
            "SELECT * FROM recordings WHERE client_id = ?",
            (client_id,),
        ).fetchone()
    if existing is not None:
        return _to_out(existing)

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is larger than 40 MB.")

    mime, extension = _resolve_type(file.content_type or "", file.filename or "")
    recording_id = str(uuid.uuid4())
    filename = f"{recording_id}{extension}"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    (AUDIO_DIR / filename).write_bytes(payload)

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO recordings (
                id, client_id, title, duration_ms, created_at, favorite,
                filename, mime_type, size_bytes, waveform
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recording_id,
                client_id,
                clean_title,
                duration_ms,
                created,
                1 if favorite else 0,
                filename,
                mime,
                len(payload),
                json.dumps(levels),
            ),
        )
        row = connection.execute(
            "SELECT * FROM recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()
    return _to_out(row)


@app.patch("/api/recordings/{recording_id}", response_model=RecordingOut)
def patch_recording(recording_id: str, body: RecordingPatch) -> RecordingOut:
    row = _get_row(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recording not found.")

    title = row["title"] if body.title is None else _clean_title(body.title)
    favorite = row["favorite"] if body.favorite is None else (1 if body.favorite else 0)
    with connect() as connection:
        connection.execute(
            "UPDATE recordings SET title = ?, favorite = ? WHERE id = ?",
            (title, favorite, recording_id),
        )
        updated = connection.execute(
            "SELECT * FROM recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()
    return _to_out(updated)


@app.delete("/api/recordings/{recording_id}", status_code=204, response_class=Response)
def delete_recording(recording_id: str) -> Response:
    row = _get_row(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = AUDIO_DIR / row["filename"]
    if path.is_file():
        path.unlink()
    with connect() as connection:
        connection.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
    return Response(status_code=204)


# --- MongoDB & Live Whisper Transcription Endpoints ---


@app.get("/api/mongo/status")
def get_mongo_status():
    return {
        "connected": is_mongo_connected(),
        "uri": get_current_uri(),
        "database": get_database_name(),
        "collection": "transcripts",
    }


def _update_env_file(key: str, value: str) -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")
        return
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@app.post("/api/mongo/config")
def update_mongo_config(body: MongoConfigIn):
    connected = set_mongo_uri(body.uri)
    if connected and body.uri:
        _update_env_file("MONGODB_URI", body.uri.strip())
    return {
        "connected": connected,
        "uri": get_current_uri(),
        "message": "Connected to MongoDB" if connected else "Could not connect to MongoDB (using local fallback)",
    }


@app.get("/api/config/status")
def get_config_status():
    from dotenv import load_dotenv
    load_dotenv()
    return {
        "mongo_connected": is_mongo_connected(),
        "mongo_uri": get_current_uri(),
        "has_openai_key": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "has_groq_key": bool(os.getenv("GROQ_API_KEY", "").strip()),
    }


@app.post("/api/config/ai")
def update_ai_config(body: AIConfigIn):
    if body.openai_api_key is not None:
        key_val = body.openai_api_key.strip()
        os.environ["OPENAI_API_KEY"] = key_val
        _update_env_file("OPENAI_API_KEY", key_val)
    if body.groq_api_key is not None:
        key_val = body.groq_api_key.strip()
        os.environ["GROQ_API_KEY"] = key_val
        _update_env_file("GROQ_API_KEY", key_val)
    return {
        "has_openai": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "has_groq": bool(os.getenv("GROQ_API_KEY", "").strip()),
        "message": "AI transcription keys updated and persisted successfully",
    }


@app.post("/api/transcribe")
async def transcribe_recording(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    title: str = Form(""),
):
    """Transcribe one finished recording with Whisper and store the text in MongoDB."""
    session_id = re.sub(r"[^A-Za-z0-9_-]", "", session_id.strip())
    if not session_id or len(session_id) > 80:
        raise HTTPException(status_code=422, detail="session_id is required.")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is larger than 40 MB.")

    mime, extension = _resolve_type(file.content_type or "", file.filename or "recording.m4a")
    dest_dir = DATA_DIR / "transcripts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    audio_path = dest_dir / f"{session_id}{extension}"
    audio_path.write_bytes(payload)

    try:
        transcription = transcribe_audio_file(audio_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = save_final_transcript(
        session_id=session_id,
        text=transcription.get("text", ""),
        engine=transcription.get("engine", "whisper"),
        title=title.strip(),
    )
    _attach_summary(saved, session_id)
    return {
        "session_id": session_id,
        "chunk_index": 0,
        "text": saved.get("text", ""),
        "is_final": True,
        "full_transcript": saved.get("full_transcript", ""),
        "engine": saved.get("engine") or transcription.get("engine"),
        "source": saved.get("source", "mongodb"),
        "mime_type": mime,
        "summary": saved.get("summary", ""),
        "summary_model": saved.get("summary_model", ""),
        "summary_error": saved.get("summary_error", ""),
    }


@app.post("/api/transcribe/chunk")
async def transcribe_chunk(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    chunk_index: int = Form(...),
    is_last: bool = Form(False),
):
    session_id = session_id.strip()
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required.")

    temp_chunk_dir = DATA_DIR / "chunks" / session_id
    temp_chunk_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "chunk.m4a").suffix or ".m4a"
    chunk_path = temp_chunk_dir / f"chunk_{chunk_index}{suffix}"
    payload = await file.read()
    chunk_path.write_bytes(payload)

    # Run Whisper transcription
    try:
        transcription = transcribe_audio_file(chunk_path)
        chunk_text = transcription.get("text", "")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Save to MongoDB
    saved = save_transcript_chunk(
        session_id=session_id,
        chunk_index=chunk_index,
        text=chunk_text,
        is_final=is_last,
    )
    saved["engine"] = transcription.get("engine", "whisper")
    if is_last:
        _attach_summary(saved, session_id)

    # Broadcast to SSE stream subscribers
    if session_id in transcript_subscribers:
        for q in list(transcript_subscribers[session_id]):
            await q.put(saved)

    return saved


@app.get("/api/sessions")
def sessions() -> list[dict]:
    return list_sessions()


@app.get("/api/transcribe/{session_id}")
def get_transcript(session_id: str):
    return get_session_transcript(session_id)


@app.get("/api/transcribe/{session_id}/stream")
async def stream_transcript(session_id: str):
    async def event_generator():
        q: asyncio.Queue = asyncio.Queue()
        if session_id not in transcript_subscribers:
            transcript_subscribers[session_id] = []
        transcript_subscribers[session_id].append(q)

        # First yield all chunks saved in MongoDB so far
        existing = get_session_transcript(session_id)
        for chunk in existing.get("chunks", []):
            yield f"data: {json.dumps(chunk)}\n\n"

        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(item)}\n\n"
                    if item.get("is_final"):
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            if session_id in transcript_subscribers:
                if q in transcript_subscribers[session_id]:
                    transcript_subscribers[session_id].remove(q)
                if not transcript_subscribers[session_id]:
                    del transcript_subscribers[session_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/transcribe/{session_id}")
async def websocket_transcribe(websocket: WebSocket, session_id: str):
    await websocket.accept()
    chunk_idx = 0
    try:
        while True:
            data = await websocket.receive()
            if "bytes" in data and data["bytes"]:
                payload = data["bytes"]
                temp_chunk_dir = DATA_DIR / "chunks" / session_id
                temp_chunk_dir.mkdir(parents=True, exist_ok=True)
                chunk_path = temp_chunk_dir / f"ws_chunk_{chunk_idx}.wav"
                chunk_path.write_bytes(payload)

                transcription = transcribe_audio_file(chunk_path)
                chunk_text = transcription.get("text", "")

                saved = save_transcript_chunk(
                    session_id=session_id,
                    chunk_index=chunk_idx,
                    text=chunk_text,
                    is_final=False,
                )
                chunk_idx += 1
                await websocket.send_json(
                    {
                        "type": "transcript",
                        "session_id": session_id,
                        "chunk_index": saved["chunk_index"],
                        "text": saved["text"],
                        "full_transcript": saved["full_transcript"],
                    }
                )
            elif "text" in data and data["text"]:
                msg = json.loads(data["text"])
                if msg.get("action") == "end":
                    saved = save_transcript_chunk(
                        session_id=session_id,
                        chunk_index=chunk_idx,
                        text="",
                        is_final=True,
                    )
                    await websocket.send_json(
                        {
                            "type": "transcript_final",
                            "session_id": session_id,
                            "full_transcript": saved["full_transcript"],
                        }
                    )
                    break
    except WebSocketDisconnect:
        pass


def _attach_summary(saved: dict, session_id: str) -> None:
    """After the last slice, ask GPT-5.6 Luna for summary points."""
    try:
        summary = summarize_transcript(saved.get("full_transcript") or "")
        save_session_summary(session_id, summary, SUMMARY_MODEL)
        saved["summary"] = summary
        saved["summary_model"] = SUMMARY_MODEL
        try:
            saved["points_indexed"] = index_summary(
                session_id,
                summary,
                recorded_at=datetime.now().astimezone().isoformat(),
                transcript=saved.get("full_transcript") or "",
            )
        except Exception as exc:
            logger.warning("Could not store summary points for search: %s", exc)
            saved["index_error"] = str(exc)
    except RuntimeError as exc:
        saved["summary_error"] = str(exc)


def _index_saved_summaries() -> None:
    """Embeds summary points that were saved before vector search existed."""
    already = indexed_session_ids()
    for session in list_sessions(limit=200):
        session_id = session.get("session_id") or ""
        summary = (session.get("summary") or "").strip()
        if not session_id or not summary or session_id in already:
            continue
        try:
            index_summary(
                session_id,
                summary,
                recorded_at=session.get("summary_updated_at") or session.get("updated_at"),
                transcript=session.get("full_transcript") or "",
            )
        except Exception as exc:
            logger.warning("Could not index session %s: %s", session_id, exc)


def _get_row(recording_id: str):
    with connect() as connection:
        return connection.execute(
            "SELECT * FROM recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()


def _to_out(row) -> RecordingOut:
    try:
        waveform = json.loads(row["waveform"] or "[]")
    except json.JSONDecodeError:
        waveform = []
    if not isinstance(waveform, list):
        waveform = []
    return RecordingOut(
        id=row["id"],
        client_id=row["client_id"],
        title=row["title"],
        duration_ms=row["duration_ms"],
        created_at=row["created_at"],
        favorite=bool(row["favorite"]),
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        waveform=[float(value) for value in waveform if isinstance(value, (int, float))],
        audio_url=f"/api/recordings/{row['id']}/audio",
    )


def _clean_title(title: str) -> str:
    clean = " ".join(title.split())
    if not clean:
        raise HTTPException(status_code=422, detail="Title cannot be empty.")
    if len(clean) > 80:
        raise HTTPException(status_code=422, detail="Title must be 80 characters or fewer.")
    return clean


def _parse_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="created_at must be an ISO-8601 timestamp.") from error
    return parsed.isoformat()


def _parse_waveform(raw: str) -> list[float]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail="waveform must be a JSON array.") from error
    if not isinstance(parsed, list):
        raise HTTPException(status_code=422, detail="waveform must be a JSON array.")
    levels: list[float] = []
    for value in parsed[:64]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            levels.append(max(0.0, min(1.0, float(value))))
    return levels


def _resolve_type(content_type: str, filename: str) -> tuple[str, str]:
    mime = content_type.split(";", 1)[0].strip().lower()
    suffix = Path(filename).suffix.lower()
    if mime in MIME_EXTENSIONS:
        extension = MIME_EXTENSIONS[mime]
        if suffix in EXTENSION_MIME:
            extension = suffix if suffix != ".mp4" else ".m4a"
        return (EXTENSION_MIME.get(extension, mime), extension)
    if suffix in EXTENSION_MIME:
        return (EXTENSION_MIME[suffix], suffix)
    raise HTTPException(
        status_code=415,
        detail="Upload AAC, MP3, WAV, WebM, OGG, or 3GP audio.",
    )
