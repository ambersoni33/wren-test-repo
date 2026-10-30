"""Turn a finished transcript into summary points with GPT-5.6 Luna."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("fieldnote.summarizer")

SUMMARY_MODEL = "gpt-5.6-luna"
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "transcript_summary.txt"


def summarize_transcript(transcript: str) -> str:
    """Read the prompt file and ask GPT-5.6 Luna for summary points."""
    text = (transcript or "").strip()
    if not text:
        return "Nothing new this time."

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-your-"):
        raise RuntimeError("OPENAI_API_KEY is missing, so the summary could not be created.")

    template = _load_prompt()
    prompt = _fill_prompt(template, text)

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    try:
        response = client.responses.create(
            model=SUMMARY_MODEL,
            input=prompt,
            reasoning={"effort": "low"},
        )
    except Exception as exc:
        logger.error("GPT-5.6 Luna summary failed: %s", exc)
        raise RuntimeError(f"Summary failed: {exc}") from exc

    summary = (getattr(response, "output_text", None) or "").strip()
    if not summary:
        raise RuntimeError("GPT-5.6 Luna returned an empty summary.")
    return summary


def _fill_prompt(template: str, transcript: str) -> str:
    now = datetime.now().astimezone()
    now_text = now.strftime("%A %Y-%m-%d %H:%M %Z")
    values = {
        "{{now}}": now_text,
        "{{recording}}": "Start and end times were not stored. Use <now> as the reference time.",
        "{{user_profile}}": (
            "name: unknown; speaker label: User; language: English; quiet hours: none; "
            "morning 09:00, afternoon 14:00, evening 19:00, tonight 20:00, end of day 18:00"
        ),
        "{{consent_mode}}": "everyone",
        "{{connected_apps}}": "none",
        "{{existing_items_json}}": "[]",
        "{{transcript}}": transcript,
    }
    prompt = template
    for key, value in values.items():
        prompt = prompt.replace(key, value)
    if transcript not in prompt:
        prompt = f"{prompt.rstrip()}\n\n<transcript>\n{transcript}\n</transcript>"
    return prompt


def _load_prompt() -> str:
    if not PROMPT_PATH.is_file():
        raise RuntimeError(f"Summary prompt file is missing: {PROMPT_PATH}")
    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"Summary prompt file is empty: {PROMPT_PATH}")
    return prompt
