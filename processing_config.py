"""Plain-text AI instructions. No remotely supplied executable code."""
import hashlib
from pathlib import Path

from fastapi import HTTPException

PROMPT_PATH = Path(__file__).resolve().parent / "transformation_prompt.txt"


def load_processing_config():
    try:
        with PROMPT_PATH.open("rb") as source:
            data = source.read(16001)
        if len(data) > 16000:
            raise ValueError("Prompt too large")
        prompt = data.decode("utf-8").strip()
        if not prompt or len(prompt.encode("utf-16-le")) // 2 > 4000:
            raise ValueError("Invalid prompt length")
        return {"schemaVersion": 1, "prompt": prompt,
                "revision": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}
    except (OSError, UnicodeError, ValueError):
        raise HTTPException(503, "Processing prompt is unavailable. Ask Bluqq to check the server configuration.") from None
