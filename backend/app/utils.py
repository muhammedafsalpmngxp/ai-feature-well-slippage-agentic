"""Tolerant extraction from LLM output."""
from __future__ import annotations

import json
import re
from typing import Any

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def extract_sql(text: str) -> str:
    """Pull the statement out of a reply, preferring a fenced block."""
    text = strip_think(text)
    match = _SQL_FENCE_RE.search(text)
    candidate = match.group(1) if match else text
    candidate = candidate.replace("```", "").strip()
    # Cut any prose the model put before the statement.
    keyword = re.search(r"\b(WITH|SELECT)\b", candidate, re.IGNORECASE)
    if keyword:
        candidate = candidate[keyword.start():]
    return candidate.strip().rstrip(";").strip()


def extract_json(text: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pull a JSON object out of a reply, tolerating fences and surrounding prose."""
    text = strip_think(text)
    match = _JSON_FENCE_RE.search(text)
    blob = match.group(1) if match else None
    if blob is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            blob = text[start : end + 1]
    if blob:
        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return default if default is not None else {}
