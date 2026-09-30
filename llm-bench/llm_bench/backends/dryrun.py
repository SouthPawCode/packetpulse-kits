"""Dry-run backend: returns fixture outputs and fake timings so run and render work offline.

Which answers a fake model gets right is decided by `quality` (0..1, from the
instance's per-model `dryrun:` block) and a stable hash of (model, task, variant),
so the same instance always gives the same results.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..textutil import estimate_tokens
from .base import Backend, ChatResult, Tag

GENERIC_BAD = "I could not complete this task. Sorry."


def stable_unit(*parts: Any) -> float:
    """Deterministic float in [0, 1) from the parts."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


class DryRunBackend(Backend):
    kind = "dryrun"

    def __init__(self, cfg: Any, options: dict[str, Any], fixtures_dir: Path | None = None, scenario: dict[str, Any] | None = None):
        super().__init__(cfg, options)
        self.fx = cfg.dryrun or {}
        self.fixtures_dir = fixtures_dir
        self.scenario = scenario
        self._ctx: int | None = None

    # -- knobs -----------------------------------------------------------------
    @property
    def quality(self) -> float:
        return float(self.fx.get("quality", 1.0))

    def _good(self, task_id: int, variant: str) -> bool:
        if self.quality >= 1.0:
            return True
        return stable_unit(self.cfg.name, task_id, variant, self.options.get("seed", 7)) < self.quality

    def _fixture(self, task_id: int, variant: str) -> str | None:
        if self.fixtures_dir is None:
            return None
        p = self.fixtures_dir / f"{task_id}__{variant}.md"
        return p.read_text(encoding="utf-8") if p.is_file() else None

    # -- lifecycle ---------------------------------------------------------------
    def model_info(self) -> dict[str, Any]:
        if (self.cfg.pricing or self.fx.get("api")) and "vram_mb" not in self.fx:  # an API-like fake model has no local footprint
            return {"size_gb": None, "quant": None, "vram_after_load_mb": None, "gpu_share_pct": None, "load_seconds": None}
        return {
            "size_gb": self.fx.get("size_gb", 12.0),
            "quant": self.fx.get("quant", "Q4_K_M"),
            "vram_after_load_mb": self.fx.get("vram_mb", 12000),
            "gpu_share_pct": self.fx.get("gpu_share_pct", 100),
            "load_seconds": self.fx.get("load_seconds", 4.0),
        }

    # -- chat ------------------------------------------------------------------
    def _timings(self, res: ChatResult, prompt_tokens: int, tag: Tag) -> None:
        base_tps = float(self.fx.get("tps", 30.0))
        ttft_base = float(self.fx.get("ttft_ms", 300.0))
        jitter = 0.9 + 0.2 * stable_unit(self.cfg.name, tag.task_id, tag.variant, tag.step, "j")
        scale = 1.0 - min(prompt_tokens / 65536.0, 1.0) * 0.45  # slower as the context grows
        res.tokens_per_second = round(base_tps * scale * jitter, 2)
        res.ttft_ms = round(ttft_base + prompt_tokens * float(self.fx.get("ttft_ms_per_token", 0.12)) * jitter, 1)
        res.ttft_any_ms = res.ttft_ms
        res.prompt_tokens = prompt_tokens
        res.output_tokens = max(1, estimate_tokens(res.text)) if res.text else max(1, 20 * len(res.tool_calls))
        res.total_seconds = round(res.ttft_ms / 1000.0 + res.output_tokens / res.tokens_per_second, 3)
        from .openai_compat import cost_usd

        res.cost_usd = cost_usd(self.cfg.pricing, prompt_tokens, res.output_tokens)

    def _tool_turn(self, messages: list[dict[str, Any]], tag: Tag, res: ChatResult) -> None:
        scenario = self.scenario or {}
        ref = scenario.get("reference_calls", [])
        done = sum(1 for m in messages if m.get("role") == "tool")
        if not self._good(tag.task_id, "default"):
            res.text = "backup02 looks fine to me; I did not need any tools. The switch is probably sw-edge-1 and there are no errors."
            return
        if done < len(ref):
            call = ref[done]
            res.tool_calls = [{"id": f"call_{done}", "name": call["name"], "arguments": dict(call["arguments"])}]
            return
        res.text = self._fixture(tag.task_id, "final") or "Done."

    def chat(self, messages, *, tools=None, num_ctx=None, num_predict=None, tag: Tag | None = None) -> ChatResult:
        tag = tag or Tag()
        res = ChatResult()
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        prompt_tokens = estimate_tokens(prompt)
        tid, variant = tag.task_id, tag.variant
        if tools:
            self._tool_turn(messages, tag, res)
        elif tid == 8:
            m = re.search(r"backup-agent\[\d+\]: ERROR:.*?incident=(INC-[A-Z0-9-]+)", prompt)
            res.text = f"INCIDENT: {m.group(1)}" if (m and self._good(tid, variant)) else "INCIDENT: INC-ZZZZ-0000"
        elif tid == 1:
            res.text = "The document explains switching, spanning tree, reverse proxies, Ceph, inference speed and firewall ordering. " * 4
        elif tid == 0:
            res.text = "OK"
        else:
            fx = self._fixture(tid, variant)
            if fx is not None and self._good(tid, variant):
                res.text = fx
            else:
                res.text = GENERIC_BAD
        res.finish_reason = "stop"
        self._timings(res, prompt_tokens, tag)
        return res
