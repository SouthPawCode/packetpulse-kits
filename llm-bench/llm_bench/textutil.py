"""Small text helpers shared by runner, scorers and render."""

from __future__ import annotations

import json
import math
import re
from typing import Any

CHARS_PER_TOKEN = 4.0  # the simple tokenizer estimate used to size prompts


def estimate_tokens(text: str, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Rough token count: characters / chars_per_token (default 4)."""
    if not text:
        return 0
    return int(math.ceil(len(text) / chars_per_token))


def slug(name: str) -> str:
    """File-system and URL safe slug used in asset and output file names."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s or "model"


_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+.\-]*)[^\n]*\n(.*?)```", re.DOTALL)


def code_blocks(text: str) -> list[tuple[str, str]]:
    """Return [(lang, code)] for every fenced block. An unterminated final fence counts."""
    blocks = [(m.group(1).lower(), m.group(2)) for m in _FENCE.finditer(text or "")]
    if not blocks and text and "```" in text:
        # unterminated fence (output cut off at the token limit)
        m = re.search(r"```[ \t]*([A-Za-z0-9_+.\-]*)[^\n]*\n(.*)$", text, re.DOTALL)
        if m:
            blocks.append((m.group(1).lower(), m.group(2)))
    return blocks


def pick_block(text: str, langs: tuple[str, ...] = ()) -> str:
    """Best code block for a language list; falls back to the largest block, then the raw text."""
    blocks = code_blocks(text)
    if not blocks:
        return (text or "").strip("\n") + "\n"
    if langs:
        wanted = [c for lang, c in blocks if lang in langs]
        if wanted:
            return max(wanted, key=len)
    return max((c for _, c in blocks), key=len)


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model answer (fenced or bare). Raises ValueError."""
    text = text or ""
    candidates: list[str] = [c for lang, c in code_blocks(text) if lang in ("json", "")]
    candidates.append(text)
    decoder = json.JSONDecoder()
    for cand in candidates:
        cand = cand.strip()
        try:
            return json.loads(cand)
        except ValueError:
            pass
        for start in (i for i, ch in enumerate(cand) if ch in "{["):
            try:
                obj, _ = decoder.raw_decode(cand[start:])
            except ValueError:
                continue
            if isinstance(obj, (dict, list)):
                return obj
    raise ValueError("no JSON value found in output")


def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks some models leave inline."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def fmt_num(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.{digits}f}"
