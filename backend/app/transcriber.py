"""Whisper speech-to-text.

Order:
1. OpenAI Whisper API (`whisper-1`) when OPENAI_API_KEY is set.
2. OpenAI's open-source Whisper package running on this machine.
3. faster-whisper, then Groq, when those are installed and configured.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fieldnote.transcriber")


_WEBM_HEADER = b"\x1a\x45\xdf\xa3"
_CLUSTER = bytes.fromhex("1f43b675")


def _prepare_audio(path: Path) -> Path:
    """Turn a browser timeslice into a file Whisper can open.

    The browser's first slice is a real WebM file. Later slices are raw
    clusters with no header, which Whisper rejects, so only the opening
    sentence was kept.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return path
    data = path.read_bytes()
    if data.startswith(_WEBM_HEADER) or not path.suffix.lower() == ".webm":
        return path

    cluster_at = data.find(_CLUSTER)
    header = _webm_init_header(path.parent)
    if cluster_at < 0 or not header:
        return path

    repaired = path.with_name(f"{path.stem}_repaired.webm")
    repaired.write_bytes(header + data[cluster_at:])
    wav = path.with_name(f"{path.stem}_repaired.wav")
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(repaired),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav),
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0 and wav.is_file() and wav.stat().st_size > 44:
        logger.info("Repaired WebM slice %s into %s", path.name, wav.name)
        return wav
    logger.warning("Could not repair WebM slice %s: %s", path.name, completed.stderr.decode("utf-8", "ignore")[:300])
    return path


def _webm_init_header(folder: Path) -> bytes | None:
    for candidate in sorted(folder.glob("chunk_0.*")):
        data = candidate.read_bytes()
        if not data.startswith(_WEBM_HEADER):
            continue
        cluster_at = data.find(_CLUSTER)
        if cluster_at > 0:
            return data[:cluster_at]
    return None


def _spoken_text(segments: Any, fallback: str) -> str:
    """Drop slices Whisper itself marks as silence, so a pause is not the end of the session."""
    if not segments:
        return (fallback or "").strip()
    parts: list[str] = []
    for segment in segments:
        if isinstance(segment, dict):
            no_speech = float(segment.get("no_speech_prob") or 0)
            text = segment.get("text") or ""
        else:
            no_speech = float(getattr(segment, "no_speech_prob", 0) or 0)
            text = getattr(segment, "text", "") or ""
        if no_speech >= 0.6:
            continue
        cleaned = str(text).strip()
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts).strip()

_local_whisper_model: Any = None
_model_name: str = os.getenv("WHISPER_MODEL", "base")


def transcribe_audio_file(file_path: str | Path, language: str | None = None) -> dict[str, Any]:
    """Transcribe an audio file. Language is detected when language is omitted."""
    path = _prepare_audio(Path(file_path))
    if not path.is_file() or path.stat().st_size == 0:
        return {"text": "", "engine": "empty", "duration": 0.0}

    load_dotenv()

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_api_key and not openai_api_key.startswith("sk-your-"):
        return _transcribe_openai_api(path, openai_api_key, language)

    try:
        return _transcribe_openai_whisper(path, language)
    except ImportError:
        logger.info("openai-whisper is not installed.")
    except Exception as exc:
        logger.error("Local OpenAI Whisper failed: %s", exc)
        raise RuntimeError(f"OpenAI Whisper transcription failed: {exc}") from exc

    try:
        return _transcribe_faster_whisper(path, language)
    except ImportError:
        pass
    except Exception as exc:
        logger.error("faster-whisper failed: %s", exc)
        raise RuntimeError(f"Whisper transcription failed: {exc}") from exc

    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_api_key and not groq_api_key.startswith("gsk_your_"):
        return _transcribe_groq(path, groq_api_key)

    raise RuntimeError(
        "Whisper is not available. Add OPENAI_API_KEY to backend/.env "
        "or install the openai-whisper package."
    )


def _transcribe_openai_api(path: Path, api_key: str, language: str | None) -> dict[str, Any]:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        kwargs: dict[str, Any] = {
            "model": "whisper-1",
            "file": open(path, "rb"),
            "response_format": "verbose_json",
        }
        if language:
            kwargs["language"] = language
        try:
            transcription = client.audio.transcriptions.create(**kwargs)
        finally:
            kwargs["file"].close()
        return {
            "text": _spoken_text(getattr(transcription, "segments", None), getattr(transcription, "text", "") or ""),
            "engine": "openai-whisper-1",
            "duration": getattr(transcription, "duration", None),
        }
    except Exception as exc:
        logger.error("OpenAI Whisper API error: %s", exc)
        raise RuntimeError(f"OpenAI Whisper transcription failed: {exc}") from exc


def _transcribe_openai_whisper(path: Path, language: str | None) -> dict[str, Any]:
    import whisper

    global _local_whisper_model
    if _local_whisper_model is None:
        logger.info("Loading OpenAI Whisper model %s", _model_name)
        _local_whisper_model = whisper.load_model(_model_name)

    kwargs: dict[str, Any] = {
        "fp16": False,
        # Each slice is its own utterance. Carrying the previous phrase
        # makes Whisper repeat it and then treat the rest as silence.
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.6,
    }
    if language:
        kwargs["language"] = language
    result = _local_whisper_model.transcribe(str(path), **kwargs)
    return {
        "text": _spoken_text(result.get("segments"), result.get("text") or ""),
        "engine": f"openai-whisper-{_model_name}",
        "duration": None,
    }


def _transcribe_faster_whisper(path: Path, language: str | None) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    global _local_whisper_model
    if _local_whisper_model is None or not isinstance(_local_whisper_model, WhisperModel):
        _local_whisper_model = WhisperModel(_model_name, device="cpu", compute_type="int8")

    segments, info = _local_whisper_model.transcribe(
        str(path),
        language=language,
        beam_size=1,
    )
    text = " ".join(segment.text for segment in segments).strip()
    return {
        "text": text,
        "engine": f"faster-whisper-{_model_name}",
        "duration": getattr(info, "duration", None),
    }


def _transcribe_groq(path: Path, api_key: str) -> dict[str, Any]:
    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        with open(path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(path.name, audio_file.read()),
                model="whisper-large-v3",
                response_format="verbose_json",
            )
        return {
            "text": (getattr(transcription, "text", "") or "").strip(),
            "engine": "groq-whisper-large-v3",
            "duration": getattr(transcription, "duration", None),
        }
    except Exception as exc:
        logger.error("Groq Whisper API error: %s", exc)
        raise RuntimeError(f"Groq Whisper transcription failed: {exc}") from exc
