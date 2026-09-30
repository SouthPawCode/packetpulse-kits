"""Automatic scorers: keyword groups, regex, JSON schema, numeric tolerance.

Every scorer returns a ScoreResult; score is 0..1 and None means "not scored"
(for example when a validator is unavailable). Pass means score >= 1.0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from ..textutil import extract_json, strip_think


@dataclass
class ScoreResult:
    score: float | None
    details: dict[str, Any] = field(default_factory=dict)
    note: str | None = None  # one-line human reason, printed in progress and kept in details

    @property
    def passed(self) -> bool | None:
        return None if self.score is None else self.score >= 0.999

    def to_details(self) -> dict[str, Any]:
        d = dict(self.details)
        if self.note:
            d["score_note"] = self.note
        return d


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE | re.MULTILINE) is not None


def keyword_groups(text: str, groups: list[dict[str, Any]]) -> ScoreResult:
    """Each group passes when ANY of its regexes matches (case-insensitive).

    score = groups matched / groups. Pass = every group matched.
    """
    text = strip_think(text)
    matched: list[str] = []
    missed: list[str] = []
    for g in groups:
        gid = g.get("id") or g["any"][0]
        if any(_search(p, text) for p in g["any"]):
            matched.append(gid)
        else:
            missed.append(gid)
    total = len(groups)
    score = len(matched) / total if total else 0.0
    return ScoreResult(round(score, 4), {"matched": matched, "missed": missed, "expected": [g.get("id") for g in groups]})


def regex_score(text: str, pattern: str, must_match: bool = True) -> ScoreResult:
    hit = _search(pattern, strip_think(text))
    ok = hit if must_match else not hit
    return ScoreResult(1.0 if ok else 0.0, {"pattern": pattern, "hit": hit, "must_match": must_match})


def numbers_in(text: str) -> list[float]:
    """Every number in a text: 18,423 and 18423 and 1.5k all parse."""
    out: list[float] = []
    for m in re.finditer(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*([kK])?(?![\w])", text or ""):
        whole = m.group(1).replace(",", "")
        val = float(whole + ("." + m.group(2) if m.group(2) else ""))
        if m.group(3):
            val *= 1000
        out.append(val)
    return out


def numeric_tolerance(text: str, expected: float, tolerance: float = 0.0, relative: bool = False) -> ScoreResult:
    """Pass when any number in the text is within tolerance (absolute, or fraction when relative)."""
    nums = numbers_in(strip_think(text))
    limit = abs(expected) * tolerance if relative else tolerance
    close = [n for n in nums if abs(n - expected) <= limit]
    return ScoreResult(1.0 if close else 0.0, {"expected": expected, "tolerance": limit, "found": close[:3]})


def json_schema_score(text: str, schema: dict[str, Any], expect: dict[str, Any] | None = None) -> ScoreResult:
    """1.0 schema-valid and every expected value right; 0.5 schema-valid but a value is wrong; 0 otherwise.

    `expect` maps a dotted path to the required value, for example
    {"severity": "high", "affected_hosts.0": "pve2"}.
    """
    try:
        obj = extract_json(strip_think(text))
    except ValueError as e:
        return ScoreResult(0.0, {"stage": "parse", "error": str(e)})
    v = jsonschema.Draft202012Validator(schema)
    errs = sorted(v.iter_errors(obj), key=lambda e: list(e.absolute_path))
    if errs:
        e = errs[0]
        where = ".".join(str(p) for p in e.absolute_path) or "(root)"
        return ScoreResult(0.0, {"stage": "schema", "error": f"{where}: {e.message}"[:300], "error_count": len(errs)})
    wrong: list[str] = []
    for path, want in (expect or {}).items():
        got = _dig(obj, path)
        if not _equal(got, want):
            wrong.append(f"{path}: expected {want!r}, got {got!r}")
    if wrong:
        return ScoreResult(0.5, {"stage": "values", "wrong": wrong, "valid_schema": True})
    return ScoreResult(1.0, {"stage": "ok", "valid_schema": True, "checked_values": len(expect or {})})


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _equal(got: Any, want: Any) -> bool:
    if isinstance(want, str) and isinstance(got, str):
        return got.strip().lower() == want.strip().lower()
    if isinstance(want, (int, float)) and isinstance(got, (int, float)) and not isinstance(got, bool):
        return abs(got - want) < 1e-9
    return got == want
