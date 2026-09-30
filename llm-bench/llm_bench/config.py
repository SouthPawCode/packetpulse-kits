"""instance.yaml loading and validation (spec section 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

BACKENDS = ("ollama", "openai_compat", "dryrun")

DEFAULT_OPTIONS: dict[str, Any] = {
    "temperature": 0.2,
    "num_ctx_max": 65536,
    "num_ctx_default": 8192,
    "num_predict": 4096,
    "seed": 7,
    "think": None,
    "request_timeout_s": 900,
}

DEFAULT_CONTEXTS = [512, 4096, 16384, 32768, 65536]

INSTANCE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["kit", "title", "hardware", "models"],
    "properties": {
        "kit": {"const": "llm-bench"},
        "kit_version": {"type": "string"},
        "battery": {"type": "string"},
        "title": {"type": "string", "minLength": 1},
        "hardware": {"type": "string", "minLength": 1},
        "window": {"type": ["string", "null"]},
        "baseline": {"type": ["string", "null"]},
        "models": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "backend"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "label": {"type": "string"},
                    "backend": {"enum": list(BACKENDS)},
                    "endpoint": {"type": "string"},
                    "role": {"type": "string"},
                    "pull": {"type": "boolean"},
                    "api_key_env": {"type": "string"},
                    "pricing": {
                        "type": "object",
                        "required": ["input_per_m", "output_per_m"],
                        "properties": {
                            "input_per_m": {"type": "number", "minimum": 0},
                            "output_per_m": {"type": "number", "minimum": 0},
                        },
                    },
                    "dryrun": {"type": "object"},
                },
            },
        },
        "contexts": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "integer", "minimum": 256},
        },
        "options": {
            "type": "object",
            "properties": {
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                "num_ctx_max": {"type": "integer", "minimum": 512},
                "num_ctx_default": {"type": "integer", "minimum": 512},
                "num_predict": {"type": "integer", "minimum": 16},
                "seed": {"type": "integer"},
                "request_timeout_s": {"type": "number", "minimum": 1},
            },
        },
    },
}


class ConfigError(Exception):
    """Raised with a one-line reason when an instance or battery is unusable."""


@dataclass
class ModelCfg:
    name: str
    backend: str
    label: str = ""
    endpoint: str = ""
    role: str = ""
    pull: bool = False
    api_key_env: str = ""
    pricing: dict[str, float] | None = None
    dryrun: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.label or self.name


@dataclass
class Instance:
    kit: str
    title: str
    hardware: str
    models: list[ModelCfg]
    kit_version: str = ""
    battery: str = "battery.yaml"
    window: str | None = None
    baseline: str | None = None
    contexts: list[int] = field(default_factory=lambda: list(DEFAULT_CONTEXTS))
    options: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_OPTIONS))
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def model(self, name: str) -> ModelCfg:
        for m in self.models:
            if m.name == name:
                return m
        raise KeyError(name)


def _problems(raw: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    v = jsonschema.Draft202012Validator(INSTANCE_SCHEMA)
    for e in sorted(v.iter_errors(raw), key=lambda e: list(e.absolute_path)):
        where = ".".join(str(p) for p in e.absolute_path) or "(root)"
        errs.append(f"{where}: {e.message}")
    if errs:
        return errs
    names = [m["name"] for m in raw["models"]]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        errs.append(f"models: duplicate name(s): {', '.join(sorted(dupes))}")
    base = raw.get("baseline")
    if base and base not in names:
        errs.append(f"baseline: {base!r} is not one of the models")
    for i, m in enumerate(raw["models"]):
        b = m["backend"]
        if b in ("ollama", "openai_compat") and not m.get("endpoint"):
            errs.append(f"models[{i}] ({m['name']}): backend {b} needs an endpoint")
        if b == "openai_compat" and not m.get("api_key_env"):
            errs.append(f"models[{i}] ({m['name']}): openai_compat needs api_key_env")
    ctxs = raw.get("contexts") or []
    if ctxs != sorted(set(ctxs)):
        errs.append("contexts: must be ascending with no duplicates")
    return errs


def parse_instance(raw: Any, path: Path | None = None) -> Instance:
    if not isinstance(raw, dict):
        raise ConfigError("instance file is not a YAML mapping")
    errs = _problems(raw)
    if errs:
        raise ConfigError("; ".join(errs))
    options = dict(DEFAULT_OPTIONS)
    options.update(raw.get("options") or {})
    models = []
    for m in raw["models"]:
        models.append(
            ModelCfg(
                name=m["name"],
                backend=m["backend"],
                label=m.get("label", ""),
                endpoint=(m.get("endpoint") or "").rstrip("/"),
                role=m.get("role", ""),
                pull=bool(m.get("pull", False)),
                api_key_env=m.get("api_key_env", ""),
                pricing=m.get("pricing"),
                dryrun=m.get("dryrun") or {},
            )
        )
    baseline = raw.get("baseline")
    if not baseline:
        for m in models:
            if m.role == "baseline":
                baseline = m.name
                break
    for m in models:
        if baseline and m.name == baseline:
            m.role = "baseline"
    return Instance(
        kit=raw["kit"],
        title=raw["title"],
        hardware=raw["hardware"],
        models=models,
        kit_version=str(raw.get("kit_version", "")),
        battery=raw.get("battery", "battery.yaml"),
        window=raw.get("window"),
        baseline=baseline,
        contexts=list(raw.get("contexts") or DEFAULT_CONTEXTS),
        options=options,
        path=path,
        raw=raw,
    )


def load_instance(path: str | Path) -> Instance:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read {p}: {e.strerror or e}") from e
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{p}: invalid YAML: {e}") from e
    return parse_instance(raw, p)


def force_dry_run(inst: Instance) -> Instance:
    """Coerce every model to the dryrun backend (used by --dry-run)."""
    for m in inst.models:
        if m.backend != "dryrun":
            m.backend = "dryrun"
            m.endpoint = ""
    return inst
