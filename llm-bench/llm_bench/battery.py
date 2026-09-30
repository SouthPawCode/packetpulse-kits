"""battery.yaml loading: tasks, fixtures, prompt templating, validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .config import ConfigError, Instance

RUNNERS = ("specs", "ladder", "prompt", "variants", "tool_loop", "code", "needle", "verdict")
KINDS = ("auto", "rubric", "mixed")
KIT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(__file__).resolve().parent / "data"

SCORER_TYPES = (
    "load_ok",
    "completed",
    "keyword_groups",
    "regex",
    "json_schema",
    "numeric_tolerance",
    "firewall_rules",
    "nginx_validate",
    "compose_validate",
    "tool_facts",
    "code_tests",
    "needle",
    "html_checks",
    "none",
)

BATTERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["battery_version", "tasks"],
    "properties": {
        "battery_version": {"type": "string"},
        "name": {"type": "string"},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "name", "kind", "weight", "runner"],
                "properties": {
                    "id": {"type": "integer", "minimum": 0},
                    "name": {"type": "string", "minLength": 1},
                    "kind": {"enum": list(KINDS)},
                    "weight": {"type": "number", "minimum": 0},
                    "runner": {"enum": list(RUNNERS)},
                    "aggregate": {"enum": ["mean", "all"]},
                    "rubric": {"enum": ["required", "optional", "none"]},
                    "ctx": {"anyOf": [{"enum": ["default", "max"]}, {"type": "integer"}]},
                    "num_predict": {"type": "integer", "minimum": 1},
                    "prompt": {"type": "string"},
                    "prompt_file": {"type": "string"},
                    "scorer": {
                        "type": "object",
                        "required": ["type"],
                        "properties": {"type": {"enum": list(SCORER_TYPES)}},
                    },
                    "variants": {"type": "array", "items": {"type": "object", "required": ["id"]}},
                    "params": {"type": "object"},
                    "description": {"type": "string"},
                    "pass_rule": {"type": "string"},
                },
            },
        },
    },
}


@dataclass
class Task:
    id: int
    name: str
    kind: str
    weight: float
    runner: str
    aggregate: str = "mean"
    rubric: str = "none"
    ctx: Any = "default"
    num_predict: int | None = None
    prompt: str | None = None
    prompt_file: str | None = None
    scorer: dict[str, Any] = field(default_factory=lambda: {"type": "none"})
    variants: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    pass_rule: str = ""


@dataclass
class Battery:
    version: str
    name: str
    tasks: list[Task]
    path: Path
    tasks_dir: Path

    def task(self, task_id: int) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)

    def select(self, ids: list[int] | None) -> list[Task]:
        if not ids:
            return list(self.tasks)
        known = {t.id for t in self.tasks}
        missing = [i for i in ids if i not in known]
        if missing:
            raise ConfigError(f"unknown task id(s): {', '.join(map(str, missing))}")
        return [t for t in self.tasks if t.id in set(ids)]

    def read(self, rel: str) -> str:
        p = self.tasks_dir / rel
        try:
            return p.read_text(encoding="utf-8")
        except OSError as e:
            raise ConfigError(f"fixture missing: {rel} ({e.strerror or e})") from e

    def path_of(self, rel: str) -> Path:
        return self.tasks_dir / rel

    def render(self, template: str, variables: dict[str, str] | None = None) -> str:
        """Fill {{ key }} from variables and {{ file:relative/path }} from tasks/."""
        variables = variables or {}

        def sub(m: re.Match[str]) -> str:
            key = m.group(1).strip()
            if key.startswith("file:"):
                return self.read(key[5:].strip()).rstrip("\n")
            if key in variables:
                val = str(variables[key])
                if val.startswith("file:"):
                    return self.read(val[5:].strip()).rstrip("\n")
                return val
            raise ConfigError(f"template key {{{{ {key} }}}} has no value")

        return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", sub, template)

    def task_prompt(self, task: Task | dict[str, Any], variables: dict[str, str] | None = None) -> str:
        """Prompt text for a task or one of its variants (inline prompt or prompt_file)."""
        get = task.get if isinstance(task, dict) else lambda k, d=None: getattr(task, k, d)
        text = get("prompt", None)
        if text is None and get("prompt_file", None):
            text = self.read(get("prompt_file"))
        if text is None:
            raise ConfigError("task has neither prompt nor prompt_file")
        return self.render(text, variables)


def find_battery_file(instance: Instance | None, battery: str | None = None) -> Path:
    name = battery or (instance.battery if instance else "battery.yaml")
    cands: list[Path] = []
    p = Path(name)
    if p.is_absolute():
        cands.append(p)
    else:
        if instance is not None and instance.path is not None:
            cands.append(instance.path.parent / name)
        cands.append(Path.cwd() / name)
        cands.append(KIT_ROOT / name)
        cands.append(DATA_ROOT / name)
    for c in cands:
        if c.is_file():
            return c
    raise ConfigError(f"battery file not found: {name}")


def _tasks_dir_for(battery_file: Path) -> Path:
    for c in (battery_file.parent / "tasks", KIT_ROOT / "tasks", DATA_ROOT / "tasks"):
        if c.is_dir():
            return c
    raise ConfigError("tasks/ directory not found next to the battery or in the kit")


def load_battery(instance: Instance | None = None, battery: str | None = None) -> Battery:
    f = find_battery_file(instance, battery)
    try:
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{f}: invalid YAML: {e}") from e
    return parse_battery(raw, f, _tasks_dir_for(f))


def parse_battery(raw: Any, path: Path, tasks_dir: Path) -> Battery:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: battery is not a YAML mapping")
    errs = [
        f"{'.'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in jsonschema.Draft202012Validator(BATTERY_SCHEMA).iter_errors(raw)
    ]
    if errs:
        raise ConfigError(f"{path}: " + "; ".join(errs[:5]))
    tasks: list[Task] = []
    for t in raw["tasks"]:
        tasks.append(
            Task(
                id=t["id"],
                name=t["name"],
                kind=t["kind"],
                weight=float(t["weight"]),
                runner=t["runner"],
                aggregate=t.get("aggregate", "mean"),
                rubric=t.get("rubric", "none"),
                ctx=t.get("ctx", "default"),
                num_predict=t.get("num_predict"),
                prompt=t.get("prompt"),
                prompt_file=t.get("prompt_file"),
                scorer=t.get("scorer") or {"type": "none"},
                variants=t.get("variants") or [],
                params=t.get("params") or {},
                description=t.get("description", ""),
                pass_rule=t.get("pass_rule", ""),
            )
        )
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{path}: duplicate task ids")
    return Battery(version=str(raw["battery_version"]), name=raw.get("name", "battery"), tasks=tasks, path=path, tasks_dir=tasks_dir)


def validate_battery(b: Battery) -> list[str]:
    """Deeper checks than the schema: fixtures exist, templates resolve, scorers are known."""
    errs: list[str] = []

    def need(rel: str | None, who: str) -> None:
        if rel and not b.path_of(rel).is_file():
            errs.append(f"task {who}: fixture {rel} not found under tasks/")

    for t in b.tasks:
        who = f"{t.id} ({t.name})"
        need(t.prompt_file, who)
        if t.runner in ("prompt", "needle") and not (t.prompt or t.prompt_file):
            errs.append(f"task {who}: runner {t.runner} needs prompt or prompt_file")
        if t.kind in ("rubric", "mixed") and t.rubric == "none":
            errs.append(f"task {who}: kind {t.kind} needs rubric: required|optional")
        if t.kind == "auto" and t.rubric == "required":
            errs.append(f"task {who}: kind auto cannot require a rubric")
        if t.runner == "variants":
            if not t.variants:
                errs.append(f"task {who}: runner variants needs variants")
            ids = [v["id"] for v in t.variants]
            if len(ids) != len(set(ids)):
                errs.append(f"task {who}: duplicate variant ids")
            for v in t.variants:
                need(v.get("prompt_file"), f"{who}/{v['id']}")
                for val in (v.get("vars") or {}).values():
                    if isinstance(val, str) and val.startswith("file:"):
                        need(val[5:].strip(), f"{who}/{v['id']}")
                st = (v.get("scorer") or {}).get("type")
                if st not in SCORER_TYPES:
                    errs.append(f"task {who}/{v['id']}: unknown scorer {st!r}")
        if t.runner in ("prompt", "needle", "ladder"):
            st = t.scorer.get("type")
            if st not in SCORER_TYPES:
                errs.append(f"task {who}: unknown scorer {st!r}")
        for key in ("scenario", "problems", "questions", "corpus_file"):
            if key in t.params:
                need(t.params[key], who)
        if t.runner == "code":
            try:
                probs = yaml.safe_load(b.read(t.params.get("problems", "code/problems.yaml")))
                for p in probs:
                    for k in ("id", "entry", "prompt", "tests"):
                        if k not in p:
                            errs.append(f"task {who}: problem missing {k!r}")
            except Exception as e:  # noqa: BLE001 - report, do not crash validate
                errs.append(f"task {who}: problems file unreadable: {e}")
    # prompt templates must render
    for t in b.tasks:
        try:
            if t.runner in ("prompt",):
                b.task_prompt(t)
            for v in t.variants:
                b.task_prompt({"prompt": v.get("prompt"), "prompt_file": v.get("prompt_file") or t.prompt_file}, v.get("vars"))
        except ConfigError as e:
            errs.append(f"task {t.id} ({t.name}): {e}")
    return errs
