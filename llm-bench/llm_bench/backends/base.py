"""Backend interface shared by ollama, openai_compat and dryrun."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Tag:
    """Tells the dryrun backend which fixture to return; real backends ignore it."""

    task_id: int = -1
    variant: str = "default"
    step: int = 0


@dataclass
class ChatResult:
    text: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # [{id, name, arguments(dict)}]
    ttft_ms: float | None = None  # first visible content (or tool call) token
    ttft_any_ms: float | None = None  # first token of any kind, including thinking
    tokens_per_second: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    total_seconds: float = 0.0
    load_seconds: float | None = None
    cost_usd: float | None = None
    finish_reason: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Backend:
    """One model on one endpoint."""

    kind = "base"

    def __init__(self, cfg: Any, options: dict[str, Any]):
        self.cfg = cfg
        self.options = options

    # -- chat --------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        tag: Tag | None = None,
    ) -> ChatResult:
        raise NotImplementedError

    # -- message shapes for the tool loop ------------------------------------
    def assistant_message(self, result: ChatResult) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": result.text}
        if result.tool_calls:
            msg["tool_calls"] = [
                {"function": {"name": c["name"], "arguments": c["arguments"]}} for c in result.tool_calls
            ]
        return msg

    def tool_message(self, call: dict[str, Any], content: str) -> dict[str, Any]:
        return {"role": "tool", "tool_name": call["name"], "content": content}

    # -- lifecycle -----------------------------------------------------------
    def model_info(self) -> dict[str, Any]:
        """size_gb, quant, vram_after_load_mb, gpu_share_pct, load_seconds (None when unknown)."""
        return {}

    def load(self, num_ctx: int | None = None) -> dict[str, Any]:
        """Cold-load the model and return model_info() after load."""
        return self.model_info()

    def warm(self, num_ctx: int | None = None) -> None:
        """Make sure the model is resident with this context size (no-op for APIs)."""

    def unload(self) -> None:
        """Free GPU memory (no-op for APIs)."""

    def close(self) -> None:
        pass


def normalize_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}
