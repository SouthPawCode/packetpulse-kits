"""Runs the battery for every model in the instance and writes results incrementally."""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import yaml

from . import syslog
from .backends import Backend, ChatResult, Tag, make_backend
from .battery import Battery, Task
from .config import Instance, ModelCfg
from .results import (
    load_results,
    model_entry,
    new_results,
    now_iso,
    upsert_model,
    upsert_run,
    write_results,
)
from .scoring import rubric, run_scorer
from .scoring.auto import ScoreResult
from .scoring.tools import run_tool_loop, score_tool_facts
from .scoring import extract_html
from .textutil import CHARS_PER_TOKEN, estimate_tokens, slug

CODE_WRAPPER = (
    "Write a Python 3 function that satisfies the specification below. Standard library only. "
    "Return only the function (plus any imports or helpers it needs) in a single ```python code block: "
    "no example usage, no prints, no explanation.\n\n{spec}"
)


def _round(v: float | None, n: int = 3) -> float | None:
    return None if v is None else round(v, n)


class Runner:
    def __init__(
        self,
        inst: Instance,
        battery: Battery,
        out_dir: Path,
        *,
        task_ids: list[int] | None = None,
        log: Callable[[str], None] | None = None,
        backend_factory: Callable[..., Backend] = make_backend,
    ) -> None:
        self.inst = inst
        self.battery = battery
        self.out = Path(out_dir)
        self.tasks = battery.select(task_ids)
        self.subset = bool(task_ids)
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.backend_factory = backend_factory
        self.results_path = self.out / "results.json"
        self.results: dict[str, Any] = {}
        self._cpt: dict[str, float] = {}  # measured chars per token, per model
        self._outputs: dict[tuple[str, int], list[tuple[str, str]]] = {}

    # ------------------------------------------------------------------ public
    def run(self) -> dict[str, Any]:
        self.out.mkdir(parents=True, exist_ok=True)
        self.results = self._init_results()
        write_results(self.results, self.results_path)
        try:
            for cfg in self.inst.models:
                self._run_model(cfg)
        finally:
            self.results["finished_at"] = now_iso()
            write_results(self.results, self.results_path)
            rubric.emit(self.results, self.battery, self.out / "rubric.yaml")
        return self.results

    # ------------------------------------------------------------------ setup
    def _init_results(self) -> dict[str, Any]:
        fresh = new_results(self.inst, self.battery, self.tasks)
        if self.subset and self.results_path.exists():
            try:
                old = load_results(self.results_path)
                if old.get("schema_version") == fresh["schema_version"]:
                    have = {t["id"] for t in old["tasks"]}
                    for t in fresh["tasks"]:
                        if t["id"] not in have:
                            old["tasks"].append(t)
                    order = {t.id: i for i, t in enumerate(self.battery.tasks)}
                    old["tasks"].sort(key=lambda t: order.get(t["id"], 999))
                    old["finished_at"] = None
                    old["title"], old["hardware"], old["baseline"] = fresh["title"], fresh["hardware"], fresh["baseline"]
                    old["kit_version"], old["battery_version"] = fresh["kit_version"], fresh["battery_version"]
                    return old
            except (OSError, ValueError, KeyError):
                pass
        return fresh

    def _scenario(self) -> dict[str, Any] | None:
        for t in self.battery.tasks:
            if t.runner == "tool_loop":
                try:
                    return json.loads(self.battery.read(t.params["scenario"]))
                except Exception:  # noqa: BLE001
                    return None
        return None

    # ------------------------------------------------------------------ per model
    def _run_model(self, cfg: ModelCfg) -> None:
        opts = self.inst.options
        backend = self.backend_factory(
            cfg, opts, fixtures_dir=self.battery.tasks_dir / "dryrun" / "good", scenario=self._scenario()
        )
        self._current_info: dict[str, Any] = {}
        upsert_model(self.results, model_entry(cfg, {}))
        self.results["verdicts"].setdefault(cfg.name, {"latency_sensitive": None, "quality_sensitive": None, "vram_constrained": None, "would_i_run_it": None, "note": None})
        try:
            try:
                if cfg.backend == "ollama":
                    backend.ensure_present()  # type: ignore[attr-defined]
                info = backend.model_info()
                upsert_model(self.results, model_entry(cfg, info))
            except Exception as e:  # noqa: BLE001
                self.log(f"[{cfg.display}] cannot use model: {e}")
                self._fail_all(cfg, str(e))
                return
            write_results(self.results, self.results_path)
            for task in self.tasks:
                self._run_task(cfg, backend, task)
        finally:
            try:
                backend.unload()
            finally:
                backend.close()

    def _fail_all(self, cfg: ModelCfg, reason: str) -> None:
        for t in self.tasks:
            self._record(cfg, t, "default", ChatResult(error=reason), ScoreResult(0.0, {}, note="model unavailable"), "")
        write_results(self.results, self.results_path)

    # ------------------------------------------------------------------ per task
    def _ctx_for(self, task: Task) -> int:
        o = self.inst.options
        if task.ctx == "max":
            return int(o["num_ctx_max"])
        if isinstance(task.ctx, int):
            return min(int(task.ctx), int(o["num_ctx_max"]))
        return min(int(o["num_ctx_default"]), int(o["num_ctx_max"]))

    def _predict_for(self, task: Task) -> int:
        return int(task.num_predict or self.inst.options["num_predict"])

    def _run_task(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        self._outputs.pop((cfg.name, task.id), None)
        handler = getattr(self, f"_task_{task.runner}")
        try:
            handler(cfg, backend, task)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 - one broken task must not lose the others
            self.log(f"[{cfg.display}] task {task.id} crashed: {type(e).__name__}: {e}")
            if not any(r["model"] == cfg.name and r["task_id"] == task.id for r in self.results["runs"]):
                self._record(cfg, task, "default", ChatResult(error=f"{type(e).__name__}: {e}"), ScoreResult(0.0, {"traceback": traceback.format_exc(limit=3)}), "")
        write_results(self.results, self.results_path)

    # ------------------------------------------------------------------ recording
    def _record(
        self,
        cfg: ModelCfg,
        task: Task,
        variant: str,
        res: ChatResult | Any,
        score: ScoreResult,
        output: str,
        extra: dict[str, Any] | None = None,
        artifact: str | None = None,
    ) -> dict[str, Any]:
        error = getattr(res, "error", None)
        details = score.to_details()
        if extra:
            details.update(extra)
        thinking = getattr(res, "thinking", "")
        if thinking:
            details["thinking_chars"] = len(thinking)
        if getattr(res, "ttft_any_ms", None) is not None and getattr(res, "ttft_ms", None) is not None:
            details.setdefault("ttft_first_token_any_ms", _round(res.ttft_any_ms, 1))
        if artifact:
            details["artifact_file"] = artifact
        s = score.score
        if error:
            s = 0.0
            details["score_note"] = f"backend error: {error[:160]}"
        out_rel = f"outputs/{slug(cfg.name)}/{task.id}.md"
        if output or error:
            sections = self._outputs.setdefault((cfg.name, task.id), [])
            sections.append((variant, output if output else f"(no output) {error}"))
            self._write_output(cfg, task, out_rel)
        run = {
            "model": cfg.name,
            "task_id": task.id,
            "variant": variant,
            "ttft_ms": _round(getattr(res, "ttft_ms", None), 1),
            "tokens_per_second": _round(getattr(res, "tokens_per_second", None), 2),
            "prompt_tokens": getattr(res, "prompt_tokens", None),
            "output_tokens": getattr(res, "output_tokens", None),
            "total_seconds": _round(getattr(res, "total_seconds", None), 3),
            "cost_usd": getattr(res, "cost_usd", None),
            "score": s,
            "score_kind": task.kind,
            "pass": None if s is None else s >= 0.999,
            "error": error,
            "details": details,
            "output_file": out_rel if (output or error) else None,
        }
        upsert_run(self.results, run)
        write_results(self.results, self.results_path)
        self._progress(cfg, task, variant, run, details.get("score_note"))
        return run

    def _write_output(self, cfg: ModelCfg, task: Task, rel: str) -> None:
        sections = self._outputs[(cfg.name, task.id)]
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if len(sections) == 1 and sections[0][0] in ("default",):
            body = sections[0][1]
        else:
            body = "\n\n".join(f"## {v}\n\n{t.rstrip()}\n" for v, t in sections)
        p.write_text(f"<!-- {cfg.name} | task {task.id}: {task.name} -->\n\n{body}\n", encoding="utf-8")

    def _progress(self, cfg: ModelCfg, task: Task, variant: str, run: dict[str, Any], note: str | None) -> None:
        s = run["score"]
        verdict = "pending" if s is None else f"score={s:.2f}" + (" PASS" if run["pass"] else "")
        bits = [f"[{cfg.display}] task {task.id} {task.name}"]
        if variant != "default":
            bits.append(f"[{variant}]")
        bits.append(verdict)
        if run["ttft_ms"] is not None:
            bits.append(f"ttft={run['ttft_ms']:.0f}ms")
        if run["tokens_per_second"] is not None:
            bits.append(f"{run['tokens_per_second']:.1f} tok/s")
        if run["total_seconds"] is not None:
            bits.append(f"{run['total_seconds']:.1f}s")
        if run["cost_usd"] is not None:
            bits.append(f"${run['cost_usd']:.4f}")
        if note and s is None or run["error"]:
            bits.append(f"({(run['error'] or note)[:100]})")
        self.log("  ".join(bits))

    def _save_html(self, cfg: ModelCfg, task: Task, text: str) -> str | None:
        html = extract_html(text)
        if not html:
            return None
        rel = f"outputs/{slug(cfg.name)}/{task.id}.html"
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        return rel

    # ------------------------------------------------------------------ handlers
    def _task_specs(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        ctx = self._ctx_for(task)
        err = None
        info: dict[str, Any] = {}
        try:
            info = backend.load(ctx)
        except Exception as e:  # noqa: BLE001
            err = f"load failed: {e}"
        if info:
            upsert_model(self.results, model_entry(cfg, info))
        ping = ChatResult(error=err) if err else backend.chat(
            [{"role": "user", "content": "Reply with the single word: OK"}], num_ctx=ctx, num_predict=8, tag=Tag(task.id, "default")
        )
        ok = ping.error is None
        extra = {k: v for k, v in info.items() if k not in ("size_gb", "quant", "vram_after_load_mb", "gpu_share_pct", "load_seconds") and v is not None}
        extra.update({k: info.get(k) for k in ("size_gb", "quant", "vram_after_load_mb", "gpu_share_pct", "load_seconds")})
        extra["num_ctx"] = ctx
        self._record(cfg, task, "default", ping, ScoreResult(1.0 if ok else 0.0, {}), ping.text, extra)

    def _task_ladder(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        o = self.inst.options
        corpus = self.battery.read(task.params["corpus_file"]).strip().splitlines()
        instruction = task.params.get("instruction", "Summarize the document above.")
        predict = int(task.params.get("num_predict", 256))
        ctx_max = int(o["num_ctx_max"])
        cpt = self._cpt.get(cfg.name, CHARS_PER_TOKEN)
        for rung in self.inst.contexts:
            target = rung
            clipped = False
            if rung + predict + 64 > ctx_max:
                target = max(256, ctx_max - predict - 64)
                clipped = True
            num_ctx = min(ctx_max, -(-(target + predict + 256) // 1024) * 1024)
            prompt = build_ladder_prompt(corpus, target, cpt, f"[Document {rung}]", instruction)
            try:
                backend.warm(num_ctx)
            except Exception as e:  # noqa: BLE001
                self._record(cfg, task, str(rung), ChatResult(error=f"load failed: {e}"), ScoreResult(0.0, {}), "", {"target_tokens": target})
                continue
            res = backend.chat([{"role": "user", "content": prompt}], num_ctx=num_ctx, num_predict=predict, tag=Tag(task.id, str(rung)))
            extra: dict[str, Any] = {
                "rung": rung,
                "target_tokens": target,
                "estimated_prompt_tokens": estimate_tokens(prompt, cpt),
                "chars_per_token_used": round(cpt, 3),
                "num_ctx": num_ctx,
            }
            if clipped:
                extra["clipped_to_fit_num_ctx_max"] = True
            if res.prompt_tokens and res.prompt_tokens > 200 and not res.error:
                measured = len(prompt) / res.prompt_tokens
                # Ollama reports only evaluated tokens; trust it only when it is close to the target.
                if 0.6 * target <= res.prompt_tokens <= 1.6 * target:
                    cpt = measured
                    self._cpt[cfg.name] = cpt
            self._record(cfg, task, str(rung), res, ScoreResult(1.0, {}), res.text, extra)

    def _task_prompt(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        prompt = self.battery.task_prompt(task)
        self._one_shot(cfg, backend, task, "default", prompt, task.scorer, {})

    def _task_variants(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        for v in task.variants:
            prompt = self.battery.task_prompt(
                {"prompt": v.get("prompt"), "prompt_file": v.get("prompt_file") or task.prompt_file}, v.get("vars")
            )
            self._one_shot(cfg, backend, task, v["id"], prompt, v["scorer"], {})

    def _task_code(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        problems = yaml.safe_load(self.battery.read(task.params.get("problems", "code/problems.yaml")))
        scorer = dict(task.scorer)
        scorer.setdefault("timeout_s", task.params.get("timeout_s", 10))
        for p in problems:
            prompt = CODE_WRAPPER.format(spec=p["prompt"].strip())
            self._one_shot(cfg, backend, task, p["id"], prompt, scorer, {"problem": p})

    def _one_shot(self, cfg: ModelCfg, backend: Backend, task: Task, variant: str, prompt: str, scorer: dict[str, Any], ctx: dict[str, Any]) -> None:
        num_ctx = self._ctx_for(task)
        try:
            backend.warm(num_ctx)
        except Exception as e:  # noqa: BLE001
            self._record(cfg, task, variant, ChatResult(error=f"load failed: {e}"), ScoreResult(0.0, {}), "")
            return
        res = backend.chat([{"role": "user", "content": prompt}], num_ctx=num_ctx, num_predict=self._predict_for(task), tag=Tag(task.id, variant))
        if res.error:
            self._record(cfg, task, variant, res, ScoreResult(0.0, {}), res.text)
            return
        full_ctx = dict(ctx)
        if scorer["type"] == "html_checks":
            full_ctx["topology"] = json.loads(self.battery.read("topology.json"))
        score = run_scorer(scorer, res.text, battery=self.battery, ctx=full_ctx)
        artifact = self._save_html(cfg, task, res.text) if scorer["type"] == "html_checks" else None
        self._record(cfg, task, variant, res, score, res.text, artifact=artifact)

    def _task_tool_loop(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        scenario = json.loads(self.battery.read(task.params["scenario"]))
        num_ctx = self._ctx_for(task)
        backend.warm(num_ctx)
        lr = run_tool_loop(backend, scenario, num_ctx=num_ctx, num_predict=self._predict_for(task), task_id=task.id)
        if lr.error and not lr.final_text:
            score = ScoreResult(0.0, {"tool_calls_made": len(lr.trace), "trace": [{"name": t["name"], "arguments": t["arguments"]} for t in lr.trace]})
        else:
            score = score_tool_facts(lr.final_text, scenario["facts"], lr.trace, scenario["reference_calls"])
        score.details["turns"] = lr.turns
        body = lr.final_text or "(no final answer)"
        body += "\n\n### tool trace\n\n```json\n" + json.dumps(score.details.get("trace", []), indent=2) + "\n```"
        self._record(cfg, task, "default", lr, score, body)

    def _task_needle(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        p = task.params
        ctx_max = int(self.inst.options["num_ctx_max"])
        predict = self._predict_for(task)
        target = int(p.get("target_tokens", 58000))
        cpt = float(p.get("chars_per_token", 2.2))
        room = ctx_max - predict - 1500
        clipped = target > room
        target = max(1000, min(target, room))
        attempts: list[dict[str, Any]] = []
        res = ChatResult(error="not run")
        hay = None
        for attempt in range(3):
            hay = syslog.generate(target, int(p.get("seed", 1337)), cpt)
            prompt = self.battery.task_prompt(task, {"log": hay.text, "lines": str(hay.lines)})
            try:
                backend.warm(ctx_max)
            except Exception as e:  # noqa: BLE001
                res = ChatResult(error=f"load failed: {e}")
                break
            res = backend.chat([{"role": "user", "content": prompt}], num_ctx=ctx_max, num_predict=predict, tag=Tag(task.id, "default"))
            attempts.append({"target_tokens": target, "prompt_tokens": res.prompt_tokens, "error": res.error})
            too_big = res.error and "context" in res.error.lower()
            truncated = res.prompt_tokens is not None and res.prompt_tokens >= ctx_max - predict - 64
            if not (too_big or truncated):
                break
            basis = res.prompt_tokens or int(target * 1.3)
            target = int(target * (ctx_max - predict - 1500) / max(basis, 1) * 0.92)
            self.log(f"  [{cfg.display}] long context prompt did not fit; retrying with a shorter log (~{target} est. tokens)")
        needle = {"answer": hay.answer, "decoys": hay.decoys, "depth_pct": hay.depth_pct} if hay else {}
        extra = {
            "log_lines": hay.lines if hay else None,
            "log_chars": hay.chars if hay else None,
            "needle_depth_pct": hay.depth_pct if hay else None,
            "estimated_log_tokens": hay.est_tokens if hay else None,
            "num_ctx": ctx_max,
            "attempts": attempts,
        }
        if clipped:
            extra["clipped_to_fit_num_ctx_max"] = True
        score = ScoreResult(0.0, {}) if res.error else run_scorer(task.scorer, res.text, battery=self.battery, ctx={"needle": needle})
        self._record(cfg, task, "default", res, score, res.text, extra)

    def _task_verdict(self, cfg: ModelCfg, backend: Backend, task: Task) -> None:
        questions = yaml.safe_load(self.battery.read(task.params["questions"]))
        score = ScoreResult(None, {"questions": [{"id": q["id"], "question": q["question"]} for q in questions]}, note="Mike answers the four verdict questions on camera")
        self._record(cfg, task, "default", ChatResult(), score, "")


def build_ladder_prompt(paragraphs: list[str], target_tokens: int, chars_per_token: float, header: str, instruction: str) -> str:
    """A prompt of about target_tokens (at chars_per_token) made of numbered, repeated paragraphs."""
    tail = "\n\n" + instruction
    budget = int(target_tokens * chars_per_token) - len(header) - len(tail) - 2
    parts: list[str] = []
    size = 0
    i = 0
    while size < budget:
        para = f"Section {i + 1}. {paragraphs[i % len(paragraphs)]}"
        parts.append(para)
        size += len(para) + 2
        i += 1
    body = "\n\n".join(parts)
    if len(body) > budget > 0:
        cut = body.rfind(" ", 0, budget)
        body = body[: cut if cut > 0 else budget]
    return f"{header}\n{body}{tail}"
