"""Validators that run real tools: nginx -t and docker compose config in containers, code tests in a subprocess.

When docker is absent (or LLM_BENCH_NO_DOCKER=1) the task is not scored and the
row carries a "validator unavailable" note instead of a fake pass or fail.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ..textutil import code_blocks, pick_block, strip_think
from .auto import ScoreResult, keyword_groups

NGINX_IMAGE = "nginx:alpine"
_DOCKER_CACHE: dict[str, tuple[bool, str]] = {}


def docker_status(need_compose: bool = False) -> tuple[bool, str]:
    """(available, reason). Cached per process."""
    key = "compose" if need_compose else "docker"
    if key in _DOCKER_CACHE:
        return _DOCKER_CACHE[key]
    result: tuple[bool, str]
    if os.environ.get("LLM_BENCH_NO_DOCKER"):
        result = (False, "docker disabled by LLM_BENCH_NO_DOCKER")
    elif shutil.which("docker") is None:
        result = (False, "docker not found in PATH")
    else:
        try:
            cmd = ["docker", "compose", "version"] if need_compose else ["docker", "version", "--format", "{{.Server.Version}}"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                result = (True, "ok")
            else:
                why = (r.stderr or r.stdout).strip().splitlines()
                result = (False, ("docker compose plugin missing" if need_compose else "docker daemon not reachable") + (f": {why[-1][:120]}" if why else ""))
        except (OSError, subprocess.SubprocessError) as e:
            result = (False, f"docker check failed: {e}")
    _DOCKER_CACHE[key] = result
    return result


def reset_docker_cache() -> None:
    _DOCKER_CACHE.clear()


def unavailable(why: str, task: str) -> ScoreResult:
    return ScoreResult(None, {"validator": "unavailable", "reason": why}, note=f"validator unavailable: {why}; {task} not scored")


def _short(s: str, n: int = 400) -> str:
    return " ".join(s.split())[:n]


def _nginx_lines(out: str) -> str:
    """Only nginx's own messages, not the container entrypoint chatter."""
    keep = [ln for ln in out.splitlines() if ln.startswith("nginx:") or "[emerg]" in ln or "[warn]" in ln or "[alert]" in ln]
    return "\n".join(keep) if keep else out


def strip_nginx_comments(conf: str) -> str:
    return re.sub(r"(?m)^\s*#.*$|\s#[^\n]*$", "", conf)


# -- nginx ----------------------------------------------------------------------


def _make_test_cert(dirpath: Path) -> str | None:
    """Self-signed throwaway cert pair for nginx -t; returns an error string or None."""
    if shutil.which("openssl") is None:
        return "openssl not found (needed to make a throwaway test certificate)"
    r = subprocess.run(
        [
            "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
            "-keyout", str(dirpath / "privkey.pem"), "-out", str(dirpath / "fullchain.pem"),
            "-subj", "/CN=proving-ground.test",
        ],
        capture_output=True, text=True, timeout=60,
    )
    return None if r.returncode == 0 else f"openssl failed: {_short(r.stderr, 120)}"


def run_nginx_test(conf: str, timeout: int = 180) -> tuple[bool, str]:
    """nginx -t on conf in a container. Returns (valid, output)."""
    with tempfile.TemporaryDirectory(prefix="pg-nginx-") as td:
        tdp = Path(td)
        certs = tdp / "certs"
        certs.mkdir()
        err = _make_test_cert(certs)
        if err:
            raise RuntimeError(err)
        for f in certs.iterdir():
            f.chmod(0o644)
        (certs).chmod(0o755)
        confp = tdp / "app.conf"
        confp.write_text(conf, encoding="utf-8")
        confp.chmod(0o644)
        r = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{confp}:/etc/nginx/conf.d/default.conf:ro",
                "-v", f"{certs}:/etc/nginx/certs:ro",
                NGINX_IMAGE, "nginx", "-t",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, (r.stderr + r.stdout)


def score_nginx(text: str, required: list[dict[str, Any]]) -> ScoreResult:
    ok, why = docker_status()
    if not ok:
        return unavailable(why, "nginx config")
    conf = pick_block(strip_think(text), ("nginx", "conf", "config"))
    try:
        valid, out = run_nginx_test(conf)
    except RuntimeError as e:
        return unavailable(str(e), "nginx config")
    except subprocess.TimeoutExpired:
        return ScoreResult(0.0, {"validator": "nginx -t", "valid": False, "validator_output": "timeout"})
    feats = keyword_groups(strip_nginx_comments(conf), required) if required else ScoreResult(1.0, {"matched": [], "missed": []})
    details = {
        "validator": "nginx -t",
        "valid": valid,
        "validator_output": _short(_nginx_lines(out)),
        "features_matched": feats.details["matched"],
        "features_missed": feats.details["missed"],
    }
    if not valid:
        return ScoreResult(0.0, details)
    return ScoreResult(1.0 if feats.score >= 0.999 else 0.5, details)


# -- docker compose --------------------------------------------------------------


def compose_features(cfg: dict[str, Any]) -> dict[str, bool]:
    """Structural checks on a resolved compose model (output of `docker compose config --format json`)."""
    services = cfg.get("services") or {}
    db = next((s for s in services.values() if "postgres" in str(s.get("image", "")).lower()), None)
    feats = {"postgres_service": db is not None}
    feats["postgres_healthcheck"] = bool(db and db.get("healthcheck", {}).get("test"))
    feats["named_volume"] = bool(cfg.get("volumes")) and any(
        (v.get("type") == "volume" and v.get("source") in (cfg.get("volumes") or {})) for s in services.values() for v in (s.get("volumes") or []) if isinstance(v, dict)
    )
    healthy = False
    for s in services.values():
        dep = s.get("depends_on") or {}
        if isinstance(dep, dict) and any((d or {}).get("condition") == "service_healthy" for d in dep.values()):
            healthy = True
    feats["depends_on_service_healthy"] = healthy
    feats["restart_policy"] = all(s.get("restart") for s in services.values()) if services else False
    feats["published_port"] = any(s.get("ports") for s in services.values())
    feats["custom_network"] = "backend" in (cfg.get("networks") or {}) and all(
        "backend" in (s.get("networks") or {}) for s in services.values()
    )
    return feats


def run_compose_config(text: str, timeout: int = 60) -> tuple[bool, str, dict[str, Any] | None]:
    with tempfile.TemporaryDirectory(prefix="pg-compose-") as td:
        f = Path(td) / "docker-compose.yml"
        f.write_text(text, encoding="utf-8")
        r = subprocess.run(
            ["docker", "compose", "-f", str(f), "config", "--format", "json"],
            capture_output=True, text=True, timeout=timeout, cwd=td,
            env={**os.environ, "COMPOSE_PROJECT_NAME": "proving-ground"},
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout), None
        try:
            return True, r.stderr, json.loads(r.stdout)
        except ValueError:
            return True, r.stderr, yaml.safe_load(text)


def score_compose(text: str, required: list[str]) -> ScoreResult:
    ok, why = docker_status(need_compose=True)
    if not ok:
        return unavailable(why, "compose file")
    body = pick_block(strip_think(text), ("yaml", "yml", "compose", "docker-compose"))
    try:
        valid, out, cfg = run_compose_config(body)
    except subprocess.TimeoutExpired:
        return ScoreResult(0.0, {"validator": "docker compose config", "valid": False, "validator_output": "timeout"})
    details: dict[str, Any] = {"validator": "docker compose config", "valid": valid, "validator_output": _short(out)}
    if not valid or cfg is None:
        return ScoreResult(0.0, details)
    feats = compose_features(cfg)
    wanted = required or list(feats)
    missed = [k for k in wanted if not feats.get(k)]
    details["features_matched"] = [k for k in wanted if feats.get(k)]
    details["features_missed"] = missed
    return ScoreResult(1.0 if not missed else 0.5, details)


# -- code in a sandbox subprocess ---------------------------------------------------

_RUNNER = """\
import sys, traceback
import solution
ns = {k: getattr(solution, k) for k in dir(solution) if not k.startswith('__')}
try:
    exec(compile(open('tests.py', encoding='utf-8').read(), 'tests.py', 'exec'), ns)
except AssertionError as e:
    tb = traceback.extract_tb(sys.exc_info()[2])
    line = tb[-1].lineno if tb else '?'
    print('FAIL assertion at tests.py line %s: %s' % (line, e), file=sys.stderr)
    sys.exit(1)
except BaseException as e:
    print('FAIL %s: %s' % (type(e).__name__, e), file=sys.stderr)
    sys.exit(1)
print('PASS')
"""


def _limits(timeout: int):
    def apply() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
            resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 1))
            resource.setrlimit(resource.RLIMIT_FSIZE, (10 << 20, 10 << 20))
        except (ImportError, ValueError, OSError):
            pass

    return apply


def run_code_tests(code: str, tests: str, timeout: int = 10) -> tuple[bool, str]:
    """Run hidden tests against model code in a separate interpreter with a timeout.

    Not a security boundary: it stops runaway loops and memory, not malicious
    code. Run the kit in a container or VM if the model is untrusted.
    """
    with tempfile.TemporaryDirectory(prefix="pg-code-") as td:
        tdp = Path(td)
        (tdp / "solution.py").write_text(code, encoding="utf-8")
        (tdp / "tests.py").write_text(tests, encoding="utf-8")
        (tdp / "run_tests.py").write_text(_RUNNER, encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": td, "PYTHONDONTWRITEBYTECODE": "1"}
        kw: dict[str, Any] = {"start_new_session": True}
        if os.name == "posix":
            kw["preexec_fn"] = _limits(timeout)
        proc = subprocess.Popen(
            [sys.executable, "-E", "-s", "run_tests.py"], cwd=td, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, AttributeError):
                proc.kill()
            proc.communicate()
            return False, f"timeout after {timeout}s"
        if proc.returncode == 0:
            return True, "PASS"
        msg = (err or out).strip().splitlines()
        return False, (msg[-1] if msg else f"exit code {proc.returncode}")[:300]


def score_code(text: str, problem: dict[str, Any], timeout: int = 10) -> ScoreResult:
    body = strip_think(text)
    entry = problem["entry"]
    blocks = [c for _, c in code_blocks(body) if re.search(rf"def\s+{re.escape(entry)}\s*\(", c)]
    code = max(blocks, key=len) if blocks else pick_block(body, ("python", "py", "python3"))
    passed, msg = run_code_tests(code, problem["tests"], timeout)
    return ScoreResult(1.0 if passed else 0.0, {"entry": problem["entry"], "result": msg})
