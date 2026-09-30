"""results.json: building, incremental writes, summary, schema validation (spec section 3)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from . import SCHEMA_VERSION, __version__

SCHEMA_PATH = Path(__file__).with_name("results.schema.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_results(instance: Any, battery: Any, tasks: list[Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "title": instance.title,
        "hardware": instance.hardware,
        "started_at": now_iso(),
        "finished_at": None,
        "kit_version": __version__,
        "battery_version": battery.version,
        "baseline": instance.baseline,
        "models": [],
        "tasks": [
            {"id": t.id, "name": t.name, "kind": t.kind, "weight": t.weight, "aggregate": t.aggregate}
            for t in tasks
        ],
        "runs": [],
        "summary": {},
        "verdicts": {},
    }


def model_entry(cfg: Any, info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = info or {}
    return {
        "name": cfg.name,
        "label": cfg.display,
        "backend": cfg.backend,
        "role": cfg.role or None,
        "size_gb": info.get("size_gb"),
        "vram_after_load_mb": info.get("vram_after_load_mb"),
        "gpu_share_pct": info.get("gpu_share_pct"),
        "load_seconds": info.get("load_seconds"),
        "quant": info.get("quant"),
    }


def upsert_model(results: dict[str, Any], entry: dict[str, Any]) -> None:
    for i, m in enumerate(results["models"]):
        if m["name"] == entry["name"]:
            results["models"][i] = entry
            return
    results["models"].append(entry)


def upsert_run(results: dict[str, Any], run: dict[str, Any]) -> None:
    for i, r in enumerate(results["runs"]):
        if r["model"] == run["model"] and r["task_id"] == run["task_id"] and r["variant"] == run["variant"]:
            results["runs"][i] = run
            return
    results["runs"].append(run)


def task_score(rows: list[dict[str, Any]], aggregate: str) -> float | None:
    scored = [r["score"] for r in rows if r.get("score") is not None]
    if not scored:
        return None
    if aggregate == "all":
        # every row must exist and be scored 1; an unscored sibling row is not a pass
        return 1.0 if len(scored) == len(rows) and all(s >= 0.999 for s in scored) else 0.0
    return round(sum(scored) / len(scored), 4)


def recompute_summary(results: dict[str, Any]) -> None:
    summary: dict[str, Any] = {}
    tasks = results["tasks"]
    for m in results["models"]:
        name = m["name"]
        rows = [r for r in results["runs"] if r["model"] == name]
        total = 0.0
        passes = 0
        unscored: list[int] = []
        score_max = 0.0
        for t in tasks:
            if t["weight"] <= 0:
                continue
            score_max += t["weight"]
            s = task_score([r for r in rows if r["task_id"] == t["id"]], t.get("aggregate", "mean"))
            if s is None:
                unscored.append(t["id"])
                continue
            total += t["weight"] * s
            if s >= 0.999:
                passes += 1
        tps = [r["tokens_per_second"] for r in rows if r.get("tokens_per_second") is not None]
        ttft = [r["ttft_ms"] for r in rows if r.get("ttft_ms") is not None]
        cost = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
        summary[name] = {
            "score_total": round(total, 3),
            "score_max": round(score_max, 3),
            "passes": passes,
            "avg_tps": round(sum(tps) / len(tps), 2) if tps else None,
            "avg_ttft_ms": round(sum(ttft) / len(ttft), 1) if ttft else None,
            "cost_usd_total": round(sum(cost), 6) if cost else None,
            "unscored_task_ids": unscored,
        }
    results["summary"] = summary


def write_results(results: dict[str, Any], path: Path) -> None:
    """Atomic write so a crash mid-write never leaves a torn results.json."""
    recompute_summary(results)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".results-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_results(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_results(data: Any) -> list[str]:
    v = jsonschema.Draft202012Validator(schema())
    return [
        f"{'.'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"[:240]
        for e in sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
