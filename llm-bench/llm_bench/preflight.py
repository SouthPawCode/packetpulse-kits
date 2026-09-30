"""preflight: endpoints, models, memory fit, the GPU-sharing guard, docker for validators."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Callable

import httpx

from .backends import gpu
from .backends.ollama import is_local_endpoint, same_model
from .config import Instance
from .scoring import validators

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_GPU_BUSY = 3

ClientFactory = Callable[[str], httpx.Client]


def default_client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=httpx.Timeout(10.0))


def ram_total_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1048576.0
    except (OSError, ValueError):
        pass
    return None


def loaded_foreign_models(inst: Instance, client_factory: ClientFactory = default_client) -> tuple[list[dict[str, str]], list[str]]:
    """Models resident in Ollama that are not in the instance. Returns (foreign, errors)."""
    foreign: list[dict[str, str]] = []
    errors: list[str] = []
    ours = [m.name for m in inst.models if m.backend == "ollama"]
    for ep in sorted({m.endpoint for m in inst.models if m.backend == "ollama"}):
        try:
            with client_factory(ep) as c:
                r = c.get("/api/ps")
                r.raise_for_status()
                for m in r.json().get("models", []):
                    name = m.get("name") or m.get("model") or "?"
                    if not any(same_model(name, o) for o in ours):
                        foreign.append({"endpoint": ep, "model": name})
        except (httpx.HTTPError, ValueError) as e:
            errors.append(f"{ep}: cannot read /api/ps ({type(e).__name__})")
    return foreign, errors


def run_preflight(inst: Instance, *, force: bool = False, client_factory: ClientFactory = default_client) -> tuple[int, dict[str, Any]]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    # ---- endpoints and models -------------------------------------------------
    tags_by_ep: dict[str, list[dict[str, Any]] | None] = {}
    for m in inst.models:
        if m.backend == "dryrun":
            add(f"model {m.name}", "ok", "dryrun backend, no network")
            continue
        if m.backend == "openai_compat":
            key = os.environ.get(m.api_key_env or "", "")
            if not key:
                add(f"model {m.name}", "fail", f"environment variable {m.api_key_env} is not set")
                continue
            try:
                with client_factory(m.endpoint) as c:
                    r = c.get("/models", headers={"Authorization": f"Bearer {key}"})
                if r.status_code in (401, 403):
                    add(f"model {m.name}", "fail", f"{m.endpoint} rejected the API key (HTTP {r.status_code})")
                else:
                    add(f"model {m.name}", "ok", f"endpoint reachable (HTTP {r.status_code})")
            except httpx.HTTPError as e:
                add(f"model {m.name}", "fail", f"{m.endpoint} unreachable: {type(e).__name__}")
            continue
        # ollama
        if m.endpoint not in tags_by_ep:
            try:
                with client_factory(m.endpoint) as c:
                    r = c.get("/api/tags")
                    r.raise_for_status()
                    tags_by_ep[m.endpoint] = r.json().get("models", [])
                add(f"endpoint {m.endpoint}", "ok", "ollama reachable")
            except (httpx.HTTPError, ValueError) as e:
                tags_by_ep[m.endpoint] = None
                add(f"endpoint {m.endpoint}", "fail", f"ollama unreachable: {type(e).__name__}")
        tags = tags_by_ep[m.endpoint]
        if tags is None:
            continue
        entry = next((t for t in tags if same_model(t.get("name", ""), m.name) or same_model(t.get("model", ""), m.name)), None)
        if entry is None:
            if m.pull:
                add(f"model {m.name}", "warn", "not present; will be pulled before the run")
            else:
                add(f"model {m.name}", "fail", "not present on the endpoint and pull is not enabled")
            continue
        size_gb = (entry.get("size") or 0) / 1e9
        add(f"model {m.name}", "ok", f"present, {size_gb:.1f} GB on disk")
        if is_local_endpoint(m.endpoint) and size_gb:
            vram_mb, ram = gpu.memory_total_mb(), ram_total_gb()
            if vram_mb:
                vram_gb = vram_mb / 1024.0
                need = size_gb * 1e9 / 2**30
                if need <= vram_gb * 0.94:
                    add(f"fit {m.name}", "ok", f"{need:.1f} GiB fits in {vram_gb:.0f} GiB VRAM")
                elif ram and need <= vram_gb + ram * 0.6:
                    add(f"fit {m.name}", "warn", f"{need:.1f} GiB exceeds {vram_gb:.0f} GiB VRAM; will spill to CPU and run slowly")
                else:
                    add(f"fit {m.name}", "fail", f"{need:.1f} GiB does not fit in {vram_gb:.0f} GiB VRAM + RAM")

    # ---- GPU sharing guard ---------------------------------------------------------
    foreign, errs = loaded_foreign_models(inst, client_factory)
    for e in errs:
        add("gpu guard", "warn", e)
    blocked = bool(foreign) and not force
    if foreign:
        names = ", ".join(f"{f['model']} @ {f['endpoint']}" for f in foreign)
        if force:
            add("gpu guard", "warn", f"non-benchmark model loaded ({names}); continuing because --force was given")
        else:
            add("gpu guard", "fail", f"non-benchmark model loaded ({names}); the GPU is shared with production, rerun in the planned window or pass --force")
    else:
        add("gpu guard", "ok", "no non-benchmark model loaded")

    # ---- validators ------------------------------------------------------------------
    ok, why = validators.docker_status()
    if ok:
        c_ok, c_why = validators.docker_status(need_compose=True)
        add("docker", "ok", "docker reachable" + ("" if c_ok else f"; compose plugin missing ({c_why})"))
        if not c_ok:
            checks[-1]["status"] = "warn"
        if shutil.which("openssl") is None:
            add("openssl", "warn", "openssl not found; nginx validation will be reported as unavailable")
        try:
            r = subprocess.run(["docker", "image", "inspect", validators.NGINX_IMAGE], capture_output=True, timeout=20)
            if r.returncode != 0:
                add("nginx image", "warn", f"{validators.NGINX_IMAGE} is not local; it will be pulled on first validation")
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        add("docker", "warn", f"{why}; task 4 (Config That Validates) will be skipped and reported as unavailable")

    failures = [c for c in checks if c["status"] == "fail" and c["name"] != "gpu guard"]
    if failures:
        code = EXIT_FAIL
        reason = failures[0]["detail"]
    elif blocked:
        code = EXIT_GPU_BUSY
        reason = next(c["detail"] for c in checks if c["name"] == "gpu guard" and c["status"] == "fail")
    else:
        code, reason = EXIT_OK, "ok"
    report = {
        "ok": code == EXIT_OK,
        "exit_code": code,
        "reason": reason,
        "forced": bool(force and foreign),
        "loaded_models": foreign,
        "checks": checks,
    }
    return code, report
