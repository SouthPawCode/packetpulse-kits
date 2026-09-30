"""Scorer dispatch: a battery scorer config + model output -> ScoreResult."""

from __future__ import annotations

import json
import re
from typing import Any

from ..battery import Battery
from ..textutil import code_blocks, pick_block, strip_think
from . import auto, firewall, validators
from .auto import ScoreResult


def score_needle(text: str, needle: dict[str, Any]) -> ScoreResult:
    """1.0 when the planted incident ID is in the answer and no decoy ID is; else 0."""
    body = strip_think(text)
    want = needle["answer"]
    found = want.lower() in body.lower()
    decoys = [d for d in needle.get("decoys", []) if d.lower() in body.lower()]
    ok = found and not decoys
    return ScoreResult(1.0 if ok else 0.0, {"expected": want, "found": found, "decoys_quoted": decoys, "depth_pct": needle.get("depth_pct")})


def extract_html(text: str) -> str | None:
    body = strip_think(text)
    blocks = [c for lang, c in code_blocks(body) if lang in ("html", "htm") or re.search(r"<html|<!doctype", c, re.I)]
    if blocks:
        return max(blocks, key=len)
    m = re.search(r"<!doctype html.*", body, re.I | re.S) or re.search(r"<html.*", body, re.I | re.S)
    return m.group(0) if m else None


def score_html(text: str, topology: dict[str, Any]) -> ScoreResult:
    """Auto hints for the build task. The score itself is Mike's rubric."""
    html = extract_html(text)
    if not html:
        return ScoreResult(None, {"html_found": False}, note="no HTML document found in the output; waiting for rubric score")
    names = [h["name"] for h in topology.get("hosts", [])]
    hosts_seen = sum(1 for n in names if n.lower() in html.lower())
    details = {
        "html_found": True,
        "html_chars": len(html),
        "hosts_referenced": f"{hosts_seen}/{len(names)}",
        "external_script": bool(re.search(r"<script[^>]+src\s*=\s*[\"']https?://", html, re.I)),
        "uses_svg_or_canvas": bool(re.search(r"<svg|<canvas|createElementNS", html, re.I)),
        "has_interaction": bool(re.search(r"addEventListener|onclick|onmouse|:hover", html, re.I)),
        "single_file": not re.search(r"<link[^>]+href\s*=\s*[\"'](?!data:)[^\"']+\.css", html, re.I),
    }
    return ScoreResult(None, details, note="open the saved .html and score it on camera")


def run_scorer(cfg: dict[str, Any], text: str, *, battery: Battery | None = None, ctx: dict[str, Any] | None = None) -> ScoreResult:
    ctx = ctx or {}
    t = cfg["type"]
    if t == "none":
        return ScoreResult(None, {}, note="not auto-scored")
    if t == "keyword_groups":
        return auto.keyword_groups(text, cfg["groups"])
    if t == "regex":
        return auto.regex_score(text, cfg["pattern"], cfg.get("must_match", True))
    if t == "numeric_tolerance":
        return auto.numeric_tolerance(text, cfg["expected"], cfg.get("tolerance", 0.0), cfg.get("relative", False))
    if t == "json_schema":
        assert battery is not None
        schema = json.loads(battery.read(cfg["schema_file"]))
        return auto.json_schema_score(text, schema, cfg.get("expect"))
    if t == "firewall_rules":
        return firewall.score_firewall(text)
    if t == "nginx_validate":
        return validators.score_nginx(text, cfg.get("required", []))
    if t == "compose_validate":
        return validators.score_compose(text, cfg.get("required", []))
    if t == "code_tests":
        return validators.score_code(text, ctx["problem"], cfg.get("timeout_s", 10))
    if t == "needle":
        return score_needle(text, ctx["needle"])
    if t == "html_checks":
        return score_html(text, ctx["topology"])
    raise ValueError(f"scorer {t!r} is not dispatchable here")
