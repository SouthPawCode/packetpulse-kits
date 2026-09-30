"""Ollama backend: /api/chat streaming, metrics from the final chunk, VRAM via nvidia-smi + /api/ps."""

from __future__ import annotations

import json
import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from . import gpu
from .base import Backend, ChatResult, Tag, normalize_args

KEEP_ALIVE = "30m"


def norm_name(name: str) -> str:
    """Ollama lists untagged models as name:latest; treat both spellings as the same."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def same_model(a: str, b: str) -> bool:
    return norm_name(a) == norm_name(b)


def is_local_endpoint(url: str) -> bool:
    """True when the endpoint is on this machine, so local nvidia-smi describes its GPU."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0", ""):
        return True
    try:
        mine = {socket.gethostname().lower(), socket.getfqdn().lower()}
        if host in mine:
            return True
        addrs = set(socket.gethostbyname_ex(socket.gethostname())[2])
        return host in addrs
    except OSError:
        return False


def _err_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])
    except ValueError:
        pass
    return (resp.text or "")[:200]


class OllamaBackend(Backend):
    kind = "ollama"

    def __init__(self, cfg: Any, options: dict[str, Any], client: httpx.Client | None = None):
        super().__init__(cfg, options)
        timeout = httpx.Timeout(connect=10.0, read=float(options.get("request_timeout_s", 900)), write=30.0, pool=10.0)
        self.client = client or httpx.Client(base_url=cfg.endpoint, timeout=timeout)
        self._loaded_ctx: int | None = None
        self.last_load: dict[str, Any] = {}

    # -- discovery -------------------------------------------------------------
    def tags(self) -> list[dict[str, Any]]:
        r = self.client.get("/api/tags", timeout=15.0)
        r.raise_for_status()
        return r.json().get("models", [])

    def find_tag(self) -> dict[str, Any] | None:
        for m in self.tags():
            if same_model(m.get("name", ""), self.cfg.name) or same_model(m.get("model", ""), self.cfg.name):
                return m
        return None

    def ps(self) -> list[dict[str, Any]]:
        r = self.client.get("/api/ps", timeout=15.0)
        r.raise_for_status()
        return r.json().get("models", [])

    def ps_entry(self) -> dict[str, Any] | None:
        for m in self.ps():
            if same_model(m.get("name", ""), self.cfg.name) or same_model(m.get("model", ""), self.cfg.name):
                return m
        return None

    def pull(self) -> None:
        """POST /api/pull, progress to stderr in coarse steps."""
        last_pct = -10
        with self.client.stream("POST", "/api/pull", json={"model": self.cfg.name, "stream": True}, timeout=None) as r:
            if r.status_code >= 400:
                r.read()
                raise RuntimeError(f"pull failed: HTTP {r.status_code}: {_err_text(r)}")
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(f"pull failed: {d['error']}")
                total, done = d.get("total"), d.get("completed")
                if total and done is not None:
                    pct = int(100 * done / total)
                    if pct >= last_pct + 10:
                        last_pct = pct
                        print(f"  pull {self.cfg.name}: {pct}%", file=sys.stderr, flush=True)

    def ensure_present(self) -> None:
        if self.find_tag() is not None:
            return
        if not self.cfg.pull:
            raise RuntimeError(f"model {self.cfg.name} is not present on {self.cfg.endpoint} and pull is not enabled")
        self.pull()
        if self.find_tag() is None:
            raise RuntimeError(f"model {self.cfg.name} still missing after pull")

    # -- lifecycle -------------------------------------------------------------
    def unload(self) -> None:
        try:
            self.client.post("/api/generate", json={"model": self.cfg.name, "keep_alive": 0, "stream": False}, timeout=60.0)
        except httpx.HTTPError:
            pass
        self._loaded_ctx = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if self.ps_entry() is None:
                    return
            except httpx.HTTPError:
                return
            time.sleep(0.5)

    def warm(self, num_ctx: int | None = None) -> None:
        """Load (or reload at a new context size) without counting it in a measured request."""
        if num_ctx is not None and num_ctx == self._loaded_ctx:
            return
        self._generate_load(num_ctx)

    def _generate_load(self, num_ctx: int | None) -> tuple[float, float | None]:
        opts: dict[str, Any] = {}
        if num_ctx:
            opts["num_ctx"] = num_ctx
        t0 = time.monotonic()
        r = self.client.post(
            "/api/generate",
            json={"model": self.cfg.name, "prompt": "", "stream": False, "keep_alive": KEEP_ALIVE, "options": opts},
            timeout=httpx.Timeout(connect=10.0, read=1800.0, write=30.0, pool=10.0),
        )
        wall = time.monotonic() - t0
        if r.status_code >= 400:
            raise RuntimeError(f"load failed: HTTP {r.status_code}: {_err_text(r)}")
        load_ns = r.json().get("load_duration")
        self._loaded_ctx = num_ctx
        return wall, (load_ns / 1e9 if load_ns else None)

    def load(self, num_ctx: int | None = None) -> dict[str, Any]:
        """Cold load: unload, record baseline VRAM, load, then read VRAM and the GPU/CPU split."""
        self.unload()
        baseline = gpu.memory_used_mb() if is_local_endpoint(self.cfg.endpoint) else None
        wall, load_s = self._generate_load(num_ctx)
        time.sleep(1.0)  # let the driver settle before reading memory
        info = self.model_info()
        info["load_seconds"] = round(load_s if load_s and load_s > 0.05 else wall, 2)
        info["vram_baseline_mb"] = baseline
        self.last_load = info
        return info

    def model_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "size_gb": None,
            "quant": None,
            "vram_after_load_mb": None,
            "gpu_share_pct": None,
            "load_seconds": None,
        }
        tag = self.find_tag()
        if tag:
            if tag.get("size"):
                info["size_gb"] = round(tag["size"] / 1e9, 2)
            info["quant"] = (tag.get("details") or {}).get("quantization_level")
        try:
            r = self.client.post("/api/show", json={"model": self.cfg.name}, timeout=30.0)
            if r.status_code < 400:
                q = (r.json().get("details") or {}).get("quantization_level")
                if q:
                    info["quant"] = q
        except httpx.HTTPError:
            pass
        entry = self.ps_entry()
        if entry:
            size, vram = entry.get("size") or 0, entry.get("size_vram") or 0
            if size:
                info["gpu_share_pct"] = int(round(100.0 * vram / size))
            info["ollama_size_mb"] = round(size / 1048576) if size else None
            info["ollama_size_vram_mb"] = round(vram / 1048576) if size else None
            if info["size_gb"] is None and size:
                info["size_gb"] = round(size / 1e9, 2)
        used = gpu.memory_used_mb() if is_local_endpoint(self.cfg.endpoint) else None
        if used is not None:
            info["vram_after_load_mb"] = used
        elif entry and entry.get("size_vram"):
            info["vram_after_load_mb"] = round(entry["size_vram"] / 1048576)
        return info

    # -- chat ----------------------------------------------------------------
    def _payload(self, messages, tools, num_ctx, num_predict) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "temperature": self.options.get("temperature", 0.2),
            "seed": self.options.get("seed", 7),
            "num_predict": num_predict or self.options.get("num_predict", 4096),
        }
        if num_ctx:
            opts["num_ctx"] = num_ctx
        payload: dict[str, Any] = {
            "model": self.cfg.name,
            "messages": messages,
            "stream": True,
            "keep_alive": KEEP_ALIVE,
            "options": opts,
        }
        if tools:
            payload["tools"] = tools
        if self.options.get("think") is not None:
            payload["think"] = bool(self.options["think"])
        return payload

    def chat(self, messages, *, tools=None, num_ctx=None, num_predict=None, tag: Tag | None = None) -> ChatResult:
        res = ChatResult()
        payload = self._payload(messages, tools, num_ctx, num_predict)
        text_parts: list[str] = []
        think_parts: list[str] = []
        final: dict[str, Any] | None = None
        t0 = time.monotonic()
        try:
            with self.client.stream("POST", "/api/chat", json=payload) as r:
                if r.status_code >= 400:
                    r.read()
                    res.error = f"HTTP {r.status_code}: {_err_text(r)}"
                    res.total_seconds = time.monotonic() - t0
                    return res
                for line in r.iter_lines():
                    if not line:
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        res.error = str(d["error"])
                        break
                    now_ms = (time.monotonic() - t0) * 1000.0
                    msg = d.get("message") or {}
                    think, content, calls = msg.get("thinking"), msg.get("content"), msg.get("tool_calls")
                    if (think or content or calls) and res.ttft_any_ms is None:
                        res.ttft_any_ms = now_ms
                    if think:
                        think_parts.append(think)
                    if content:
                        text_parts.append(content)
                        if res.ttft_ms is None and content.strip():
                            res.ttft_ms = now_ms
                    if calls:
                        for c in calls:
                            fn = c.get("function") or {}
                            res.tool_calls.append(
                                {
                                    "id": c.get("id") or f"call_{len(res.tool_calls)}",
                                    "name": fn.get("name", ""),
                                    "arguments": normalize_args(fn.get("arguments")),
                                }
                            )
                        if res.ttft_ms is None:
                            res.ttft_ms = now_ms
                    if d.get("done"):
                        final = d
                        break
        except httpx.TimeoutException:
            res.error = f"timeout after {self.options.get('request_timeout_s', 900)}s without data"
        except (httpx.HTTPError, ValueError) as e:
            res.error = f"{type(e).__name__}: {e}"
        res.total_seconds = time.monotonic() - t0
        res.text = "".join(text_parts)
        res.thinking = "".join(think_parts)
        if final is not None:
            res.prompt_tokens = final.get("prompt_eval_count")
            res.output_tokens = final.get("eval_count")
            ed = final.get("eval_duration") or 0
            if res.output_tokens and ed > 0:
                res.tokens_per_second = round(res.output_tokens / (ed / 1e9), 2)
            ld = final.get("load_duration")
            res.load_seconds = round(ld / 1e9, 3) if ld else None
            res.finish_reason = final.get("done_reason")
        elif res.error is None:
            res.error = "stream ended without a final message"
        if res.ttft_ms is not None:
            res.ttft_ms = round(res.ttft_ms, 1)
        return res

    def close(self) -> None:
        self.client.close()
