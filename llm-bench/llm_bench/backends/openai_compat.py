"""OpenAI-compatible chat completions backend (DeepSeek, OpenRouter, Anthropic behind a proxy, vLLM...)."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from .base import Backend, ChatResult, Tag, normalize_args


def cost_usd(pricing: dict[str, float] | None, prompt_tokens: int | None, output_tokens: int | None) -> float | None:
    """USD cost from instance pricing (per million tokens); None when pricing or usage is missing."""
    if not pricing or prompt_tokens is None or output_tokens is None:
        return None
    return round(
        prompt_tokens * pricing.get("input_per_m", 0.0) / 1e6 + output_tokens * pricing.get("output_per_m", 0.0) / 1e6,
        6,
    )


class OpenAICompatBackend(Backend):
    kind = "openai_compat"
    RETRY_STATUS = (429, 500, 502, 503, 504)

    def __init__(self, cfg: Any, options: dict[str, Any], client: httpx.Client | None = None):
        super().__init__(cfg, options)
        timeout = httpx.Timeout(connect=10.0, read=float(options.get("request_timeout_s", 900)), write=30.0, pool=10.0)
        self.client = client or httpx.Client(timeout=timeout)
        self.url = cfg.endpoint.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict[str, str] | None:
        key = os.environ.get(self.cfg.api_key_env or "", "")
        if not key:
            return None
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def assistant_message(self, result: ChatResult) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": result.text or None}
        if result.tool_calls:
            msg["tool_calls"] = [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                for c in result.tool_calls
            ]
        return msg

    def tool_message(self, call: dict[str, Any], content: str) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": call["id"], "content": content}

    def chat(self, messages, *, tools=None, num_ctx=None, num_predict=None, tag: Tag | None = None) -> ChatResult:
        res = ChatResult()
        headers = self._headers()
        if headers is None:
            res.error = f"environment variable {self.cfg.api_key_env} is not set"
            return res
        payload: dict[str, Any] = {
            "model": self.cfg.name,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self.options.get("temperature", 0.2),
            "max_tokens": num_predict or self.options.get("num_predict", 4096),
        }
        if self.options.get("seed") is not None:
            payload["seed"] = self.options["seed"]
        if tools:
            payload["tools"] = tools
        t0 = time.monotonic()
        for attempt in range(3):
            res = self._once(payload, headers, t0)
            if res.error and res.error.startswith("HTTP ") and int(res.error.split()[1].rstrip(":")) in self.RETRY_STATUS and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            break
        return res

    def _once(self, payload: dict[str, Any], headers: dict[str, str], t0: float) -> ChatResult:
        res = ChatResult()
        text_parts: list[str] = []
        think_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] | None = None
        t_first: float | None = None
        t_last = t0
        try:
            with self.client.stream("POST", self.url, json=payload, headers=headers) as r:
                if r.status_code >= 400:
                    r.read()
                    detail = ""
                    try:
                        body = r.json()
                        err = body.get("error", body) if isinstance(body, dict) else body
                        detail = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    except ValueError:
                        detail = r.text[:200]
                    res.error = f"HTTP {r.status_code}: {detail}"
                    res.total_seconds = time.monotonic() - t0
                    return res
                for line in r.iter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    d = json.loads(data)
                    if d.get("usage"):
                        usage = d["usage"]
                    for ch in d.get("choices") or []:
                        delta = ch.get("delta") or {}
                        now = time.monotonic()
                        think = delta.get("reasoning_content") or delta.get("reasoning")
                        content = delta.get("content")
                        if (think or content or delta.get("tool_calls")) and res.ttft_any_ms is None:
                            res.ttft_any_ms = (now - t0) * 1000.0
                        if think:
                            think_parts.append(think)
                        if content:
                            text_parts.append(content)
                            if res.ttft_ms is None and content.strip():
                                res.ttft_ms = (now - t0) * 1000.0
                                t_first = now
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] = fn.get("name") or slot["name"]
                            slot["args"] += fn.get("arguments") or ""
                            if res.ttft_ms is None:
                                res.ttft_ms = (now - t0) * 1000.0
                                t_first = now
                        if ch.get("finish_reason"):
                            res.finish_reason = ch["finish_reason"]
                        t_last = now
        except httpx.TimeoutException:
            res.error = f"timeout after {self.options.get('request_timeout_s', 900)}s without data"
        except (httpx.HTTPError, ValueError) as e:
            res.error = f"{type(e).__name__}: {e}"
        res.total_seconds = time.monotonic() - t0
        res.text = "".join(text_parts)
        res.thinking = "".join(think_parts)
        for i in sorted(calls):
            c = calls[i]
            res.tool_calls.append({"id": c["id"] or f"call_{i}", "name": c["name"], "arguments": normalize_args(c["args"])})
        if usage:
            res.prompt_tokens = usage.get("prompt_tokens")
            res.output_tokens = usage.get("completion_tokens")
        if res.output_tokens and t_first is not None and t_last > t_first:
            res.tokens_per_second = round(res.output_tokens / (t_last - t_first), 2)
        res.cost_usd = cost_usd(self.cfg.pricing, res.prompt_tokens, res.output_tokens)
        if res.ttft_ms is not None:
            res.ttft_ms = round(res.ttft_ms, 1)
        return res

    def model_info(self) -> dict[str, Any]:
        return {"size_gb": None, "quant": None, "vram_after_load_mb": None, "gpu_share_pct": None, "load_seconds": None}

    def close(self) -> None:
        self.client.close()
