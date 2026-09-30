"""Backend factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Backend, ChatResult, Tag
from .dryrun import DryRunBackend
from .ollama import OllamaBackend
from .openai_compat import OpenAICompatBackend

__all__ = ["Backend", "ChatResult", "Tag", "make_backend", "OllamaBackend", "OpenAICompatBackend", "DryRunBackend"]


def make_backend(cfg: Any, options: dict[str, Any], *, fixtures_dir: Path | None = None, scenario: dict[str, Any] | None = None) -> Backend:
    if cfg.backend == "ollama":
        return OllamaBackend(cfg, options)
    if cfg.backend == "openai_compat":
        return OpenAICompatBackend(cfg, options)
    if cfg.backend == "dryrun":
        return DryRunBackend(cfg, options, fixtures_dir=fixtures_dir, scenario=scenario)
    raise ValueError(f"unknown backend {cfg.backend!r}")
