"""Rubric cards for the tasks Mike scores on camera, and the `score` merge (spec sections 2 and 3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..battery import Battery
from ..results import recompute_summary

VALID_SCORES = (0, 0.5, 1)
VERDICT_KEYS = ("latency_sensitive", "quality_sensitive", "vram_constrained", "would_i_run_it")
VERDICT_ANSWERS = ("yes", "no", "maybe")

HEADER = """\
# Proving Ground rubric. Fill `score` (0, 0.5 or 1) and a one-line `note` for each entry,
# then run:  python -m llm_bench score --results results/results.json --rubric results/rubric.yaml
# Task 10 has no score: answer yes / no / maybe in the `verdict` block instead.
"""


def _hint(run: dict[str, Any] | None) -> str | None:
    if not run:
        return None
    d = run.get("details") or {}
    if "auto_passed" in d:
        if not d.get("auto_checks"):
            return "no parsable YAML rule list in the output"
        return f"auto checks {d['auto_passed']}/{d['auto_total']} passed; missing: " + (
            ", ".join(k for k, v in d.get("auto_checks", {}).items() if not v) or "none"
        )
    if "html_found" in d:
        if not d["html_found"]:
            return "no HTML document found in the output"
        return (
            f"hosts referenced {d['hosts_referenced']}; external script: {d['external_script']}; "
            f"svg/canvas: {d['uses_svg_or_canvas']}; interaction: {d['has_interaction']}; file: {d.get('artifact_file', '?')}"
        )
    return None


def build_entries(results: dict[str, Any], battery: Battery) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    by_id = {t["id"]: t for t in results["tasks"]}
    for bt in battery.tasks:
        if bt.rubric != "required" or bt.id not in by_id:
            continue
        for m in results["models"]:
            runs = [r for r in results["runs"] if r["model"] == m["name"] and r["task_id"] == bt.id]
            if bt.id != 10 and runs and all(r.get("error") for r in runs):
                continue  # nothing to score: the run itself failed
            entry: dict[str, Any] = {"model": m["name"], "task_id": bt.id, "task": bt.name, "score": None, "note": ""}
            hint = _hint(runs[0] if runs else None)
            if hint:
                entry["auto_hint"] = hint
            if bt.id == 10:
                entry.pop("score")
                entry["verdict"] = {k: None for k in VERDICT_KEYS} | {"note": ""}
            entries.append(entry)
    return entries


def _card_comment(battery: Battery, task_id: int) -> str:
    card = battery.task(task_id).params.get("rubric_card") or []
    return "".join(f"# {line}\n" for line in card)


def emit(results: dict[str, Any], battery: Battery, path: Path) -> int:
    """Write rubric.yaml, keeping any scores Mike already filled in. Returns the entry count."""
    fresh = build_entries(results, battery)
    existing: dict[tuple[str, int], dict[str, Any]] = {}
    if path.exists():
        try:
            for e in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
                existing[(e["model"], e["task_id"])] = e
        except (yaml.YAMLError, KeyError, TypeError):
            pass
    out: list[str] = [HEADER]
    last_task = None
    for e in fresh:
        old = existing.get((e["model"], e["task_id"]))
        if old:
            for k in ("score", "note", "verdict"):
                if k in old and old[k] not in (None, ""):
                    if k == "verdict" and isinstance(old[k], dict):
                        e[k] = {**e[k], **{kk: vv for kk, vv in old[k].items() if vv not in (None, "")}}
                    else:
                        e[k] = old[k]
        if e["task_id"] != last_task:
            out.append(f"\n# ---- Task {e['task_id']}: {e['task']} ----\n")
            out.append(_card_comment(battery, e["task_id"]))
            last_task = e["task_id"]
        out.append(yaml.safe_dump([e], sort_keys=False, allow_unicode=True, width=100))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(out), encoding="utf-8")
    return len(fresh)


class RubricError(Exception):
    pass


def load_rubric(path: Path) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except OSError as e:
        raise RubricError(f"cannot read {path}: {e.strerror or e}") from e
    except yaml.YAMLError as e:
        raise RubricError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, list):
        raise RubricError(f"{path}: expected a list of entries")
    return data


def apply_rubric(results: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge rubric entries into results in place. Returns {applied, skipped, warnings}."""
    task_ids = {t["id"] for t in results["tasks"]}
    models = {m["name"] for m in results["models"]}
    applied = 0
    skipped = 0
    warnings: list[str] = []
    for i, e in enumerate(entries):
        where = f"entry {i + 1}"
        if not isinstance(e, dict) or "model" not in e or "task_id" not in e:
            raise RubricError(f"{where}: needs model and task_id")
        model, tid = e["model"], e["task_id"]
        if model not in models:
            raise RubricError(f"{where}: unknown model {model!r}")
        if tid not in task_ids:
            raise RubricError(f"{where}: task {tid} is not in the results")
        if tid == 10 or "verdict" in e:
            v = e.get("verdict") or {}
            clean: dict[str, Any] = {}
            for k in VERDICT_KEYS:
                val = v.get(k)
                if val is None or val == "":
                    continue
                if str(val).lower() not in VERDICT_ANSWERS:
                    raise RubricError(f"{where}: verdict.{k} must be yes, no or maybe (got {val!r})")
                clean[k] = str(val).lower()
            if v.get("note"):
                clean["note"] = str(v["note"])
            if clean:
                cur = results["verdicts"].setdefault(model, {k: None for k in VERDICT_KEYS} | {"note": None})
                cur.update(clean)
                applied += 1
            else:
                skipped += 1
        score = e.get("score")
        if score is None:
            if tid != 10:
                skipped += 1
            continue
        if score not in VALID_SCORES:
            raise RubricError(f"{where}: score must be 0, 0.5 or 1 (got {score!r})")
        rows = [r for r in results["runs"] if r["model"] == model and r["task_id"] == tid]
        if not rows:
            warnings.append(f"{where}: no run for {model} task {tid}; score not applied")
            continue
        kind = next(t["kind"] for t in results["tasks"] if t["id"] == tid)
        for r in rows:
            d = r.setdefault("details", {})
            if r.get("score") is not None and "auto_score" not in d and kind != "rubric":
                d["auto_score"] = r["score"]
            r["score"] = float(score)
            r["pass"] = float(score) >= 1.0
            r["score_kind"] = "rubric" if kind == "rubric" else "mixed"
            d["rubric_score"] = float(score)
            if e.get("note"):
                d["rubric_note"] = str(e["note"])
            d.pop("score_note", None)
        applied += 1
    recompute_summary(results)
    return {"applied": applied, "skipped": skipped, "warnings": warnings}
