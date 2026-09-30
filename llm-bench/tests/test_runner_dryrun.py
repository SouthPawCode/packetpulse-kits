from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from llm_bench import battery as bat
from llm_bench.backends import DryRunBackend, Tag
from llm_bench.cli import main
from llm_bench.config import ModelCfg, load_instance
from llm_bench.results import load_results, recompute_summary, task_score, validate_results
from llm_bench.runner import Runner, build_ladder_prompt
from llm_bench.scoring import rubric
from llm_bench.scoring.tools import run_tool_loop
from llm_bench.textutil import estimate_tokens, slug

KIT = Path(__file__).resolve().parent.parent
TOP_KEYS = {"schema_version", "title", "hardware", "started_at", "finished_at", "kit_version", "battery_version", "models", "tasks", "runs", "summary", "verdicts"}
RUN_KEYS = {"model", "task_id", "variant", "ttft_ms", "tokens_per_second", "prompt_tokens", "output_tokens", "total_seconds", "cost_usd", "score", "score_kind", "pass", "details", "output_file"}
MODEL_KEYS = {"name", "label", "backend", "size_gb", "vram_after_load_mb", "gpu_share_pct", "load_seconds", "quant"}
ROWS_PER_MODEL = {0: 1, 1: 5, 2: 1, 3: 1, 4: 2, 5: 3, 6: 1, 7: 10, 8: 1, 9: 1, 10: 1}


@pytest.fixture
def results(dryrun_out):
    return load_results(dryrun_out / "results.json")


# ---------------------------------------------------------------- results.json shape


def test_results_json_matches_spec_schema(results):
    assert TOP_KEYS <= set(results)
    assert results["schema_version"] == 1 and results["kit_version"] == "0.2.0" and results["battery_version"] == "0.1.0"
    assert results["finished_at"] and results["started_at"] <= results["finished_at"]
    assert validate_results(results) == []
    for m in results["models"]:
        assert MODEL_KEYS <= set(m)
    assert [t["id"] for t in results["tasks"]] == list(range(11))
    for r in results["runs"]:
        assert RUN_KEYS <= set(r)
        assert r["score_kind"] in ("auto", "rubric", "mixed")


def test_row_counts_and_speed_ladder_rows(results):
    for m in results["models"]:
        rows = [r for r in results["runs"] if r["model"] == m["name"]]
        counts = {}
        for r in rows:
            counts[r["task_id"]] = counts.get(r["task_id"], 0) + 1
        assert counts == ROWS_PER_MODEL
        ladder = [r for r in rows if r["task_id"] == 1]
        assert [r["variant"] for r in ladder] == ["512", "4096", "16384", "32768", "65536"]
        assert all(r["score"] == 1.0 and r["ttft_ms"] and r["tokens_per_second"] for r in ladder)
        assert [r["prompt_tokens"] for r in ladder] == sorted(r["prompt_tokens"] for r in ladder)
        assert ladder[-1]["prompt_tokens"] > 50000, "64k rung really is a big prompt"


def test_model_entries_carry_load_specs(results):
    base = next(m for m in results["models"] if m["name"] == "dry-baseline-27b")
    assert (base["size_gb"], base["quant"], base["vram_after_load_mb"], base["gpu_share_pct"], base["load_seconds"]) == (12.1, "IQ3_S", 13200, 100, 6.3)
    assert base["role"] == "baseline" and results["baseline"] == "dry-baseline-27b"
    api = next(m for m in results["models"] if m["name"] == "dry-cloud-flash")
    assert api["vram_after_load_mb"] is None and api["size_gb"] is None


def test_outputs_saved_per_model_and_task(dryrun_out, results):
    for r in results["runs"]:
        if r["task_id"] == 10:
            assert r["output_file"] is None
            continue
        f = dryrun_out / r["output_file"]
        assert f.is_file() and f.read_text().strip(), r["output_file"]
        assert r["output_file"] == f"outputs/{slug(r['model'])}/{r['task_id']}.md"
    assert (dryrun_out / "outputs" / "dry-baseline-27b" / "9.html").read_text().lstrip().lower().startswith("<!doctype html")
    code_md = (dryrun_out / "outputs" / "dry-baseline-27b" / "7.md").read_text()
    assert "## cidr_contains" in code_md and "## merge_intervals" in code_md


def test_scores_reflect_fixture_quality(results):
    by = lambda m, t, v=None: next(r for r in results["runs"] if r["model"] == m and r["task_id"] == t and (v is None or r["variant"] == v))
    good = "dry-baseline-27b"
    for t in (0, 2, 5, 6, 8):
        assert all(r["score"] == 1.0 and r["pass"] is True for r in results["runs"] if r["model"] == good and r["task_id"] == t)
    assert by(good, 2)["details"]["missed"] == []
    assert by(good, 6)["details"]["tool_calls_made"] == 5 and by(good, 6)["details"]["followed_reference_order"] is True
    assert by(good, 8)["details"]["found"] is True
    assert sum(r["score"] for r in results["runs"] if r["model"] == good and r["task_id"] == 7) == 10
    worse = "dry-moe-35b"
    assert any(r["score"] == 0.0 for r in results["runs"] if r["model"] == worse and r["task_id"] in (2, 6, 7))


def test_rubric_tasks_are_pending_after_run(results):
    for r in results["runs"]:
        if r["task_id"] in (3, 9, 10):
            assert r["score"] is None and r["pass"] is None
    fw = next(r for r in results["runs"] if r["model"] == "dry-baseline-27b" and r["task_id"] == 3)
    assert fw["details"]["auto_passed"] == 10
    html = next(r for r in results["runs"] if r["model"] == "dry-baseline-27b" and r["task_id"] == 9)
    assert html["details"]["artifact_file"] == "outputs/dry-baseline-27b/9.html"


def test_validator_unavailable_is_a_note_not_a_score(results):
    rows = [r for r in results["runs"] if r["task_id"] == 4]
    assert len(rows) == 6
    for r in rows:
        assert r["score"] is None and r["pass"] is None and r["error"] is None
        assert "validator unavailable" in r["details"]["score_note"]
    for name, s in results["summary"].items():
        assert 4 in s["unscored_task_ids"]


def test_summary_is_the_weighted_sum(results):
    weights = {t["id"]: t["weight"] for t in results["tasks"]}
    agg = {t["id"]: t["aggregate"] for t in results["tasks"]}
    for m in results["models"]:
        name = m["name"]
        s = results["summary"][name]
        total, passes = 0.0, 0
        for tid, w in weights.items():
            if w == 0:
                continue
            sc = task_score([r for r in results["runs"] if r["model"] == name and r["task_id"] == tid], agg[tid])
            if sc is not None:
                total += w * sc
                passes += sc >= 0.999
        assert s["score_total"] == pytest.approx(total, abs=1e-3) and s["passes"] == passes
        assert s["score_max"] == 10.0, "task 10 has weight 0"
        tps = [r["tokens_per_second"] for r in results["runs"] if r["model"] == name and r["tokens_per_second"] is not None]
        assert s["avg_tps"] == pytest.approx(sum(tps) / len(tps), abs=0.01)
    # tasks 0,1,2,5,6,7,8 pass in full; 3 and 9 await the rubric and 4 has no validator
    assert results["summary"]["dry-baseline-27b"]["score_total"] == 7.0 and results["summary"]["dry-baseline-27b"]["unscored_task_ids"] == [3, 4, 9]


def test_speed_ladder_aggregate_is_all_or_nothing():
    rows = [{"score": 1.0}] * 4 + [{"score": 0.0}]
    assert task_score(rows, "all") == 0.0 and task_score(rows, "mean") == 0.8
    assert task_score([{"score": None}], "mean") is None
    assert task_score([{"score": 1.0}, {"score": None}], "all") == 0.0


def test_cost_only_for_priced_models(results):
    assert results["summary"]["dry-cloud-flash"]["cost_usd_total"] > 0
    assert results["summary"]["dry-baseline-27b"]["cost_usd_total"] is None
    assert all(r["cost_usd"] is None for r in results["runs"] if r["model"] == "dry-baseline-27b")


# ---------------------------------------------------------------- pricing absent or null: cost is null, never 0


@pytest.mark.parametrize("pricing_yaml", ["", "pricing: null", "pricing: {input_per_m: null, output_per_m: null}"])
def test_unpriced_api_model_has_null_cost_everywhere(tmp_path, pricing_yaml):
    inst = f"""
kit: llm-bench
title: "Unpriced"
hardware: "box"
models:
  - name: cloud-x
    backend: dryrun
    {pricing_yaml}
    dryrun: {{api: true, tps: 80, ttft_ms: 300, quality: 1.0}}
contexts: [512, 4096]
"""
    cfg = tmp_path / "i.yaml"
    cfg.write_text(inst)
    out = tmp_path / "out"
    assert main(["run", "--config", str(cfg), "--out", str(out), "--tasks", "0,1,2"]) == 0
    res = load_results(out / "results.json")
    assert all(r["cost_usd"] is None for r in res["runs"])
    assert res["summary"]["cloud-x"]["cost_usd_total"] is None
    assert "$0" not in json.dumps(res) and '"cost_usd": 0' not in json.dumps(res)


# ---------------------------------------------------------------- incremental writes, crashes, subsets


class Spy:
    """Reads results.json every time the runner logs a progress line."""

    def __init__(self, path: Path):
        self.path, self.counts = path, []

    def __call__(self, msg: str):
        if msg.startswith("  [") or not self.path.exists():
            return
        data = json.loads(self.path.read_text())  # must always be a complete JSON document
        self.counts.append(len(data["runs"]))


def test_results_written_after_every_row(tmp_path, dryrun_instance):
    b = bat.load_battery(dryrun_instance)
    out = tmp_path / "r"
    spy = Spy(out / "results.json")
    Runner(dryrun_instance, b, out, task_ids=[0, 1, 2], log=spy).run()
    assert spy.counts == sorted(spy.counts) and spy.counts[-1] == 3 * 7
    assert len(set(spy.counts)) == len(spy.counts), "each progress line had a fresh row already on disk"


def test_crash_keeps_partial_data(tmp_path, dryrun_instance):
    b = bat.load_battery(dryrun_instance)
    out = tmp_path / "r"
    calls = {"n": 0}

    def factory(cfg, opts, **kw):
        be = DryRunBackend(cfg, opts, fixtures_dir=kw.get("fixtures_dir"), scenario=kw.get("scenario"))
        orig = be.chat

        def chat(*a, **k):
            calls["n"] += 1
            if calls["n"] == 9:
                raise KeyboardInterrupt
            return orig(*a, **k)

        be.chat = chat
        return be

    with pytest.raises(KeyboardInterrupt):
        Runner(dryrun_instance, b, out, backend_factory=factory, log=lambda m: None).run()
    res = load_results(out / "results.json")
    assert len(res["runs"]) >= 7 and res["finished_at"], "partial data kept, run marked finished"
    assert validate_results(res) == []


def test_one_broken_task_does_not_lose_the_rest(tmp_path, dryrun_instance):
    b = bat.load_battery(dryrun_instance)
    out = tmp_path / "r"

    def factory(cfg, opts, **kw):
        be = DryRunBackend(cfg, opts, fixtures_dir=kw.get("fixtures_dir"), scenario=kw.get("scenario"))
        orig = be.chat

        def chat(messages, **k):
            if k.get("tag") and k["tag"].task_id == 2:
                raise RuntimeError("backend exploded")
            return orig(messages, **k)

        be.chat = chat
        return be

    res = Runner(dryrun_instance, b, out, task_ids=[0, 2, 5], backend_factory=factory, log=lambda m: None).run()
    t2 = [r for r in res["runs"] if r["task_id"] == 2]
    assert len(t2) == 3 and all(r["score"] == 0.0 and "backend exploded" in r["error"] for r in t2)
    assert all(r["score"] is not None for r in res["runs"] if r["task_id"] == 5)


def test_backend_error_rows_score_zero_with_reason(tmp_path, dryrun_instance):
    b = bat.load_battery(dryrun_instance)

    def factory(cfg, opts, **kw):
        be = DryRunBackend(cfg, opts, fixtures_dir=kw.get("fixtures_dir"), scenario=kw.get("scenario"))
        from llm_bench.backends import ChatResult

        be.chat = lambda *a, **k: ChatResult(error="HTTP 500: upstream down")
        return be

    res = Runner(dryrun_instance, b, tmp_path / "r", task_ids=[2, 3], backend_factory=factory, log=lambda m: None).run()
    for r in res["runs"]:
        assert r["score"] == 0.0 and r["pass"] is False and r["error"] == "HTTP 500: upstream down"
        assert "backend error" in r["details"]["score_note"]


def test_tasks_subset_and_merge(tmp_path):
    cfg = str(KIT / "examples" / "instance-dryrun.yaml")
    out = tmp_path / "r"
    assert main(["run", "--config", cfg, "--out", str(out), "--tasks", "0,2"]) == 0
    res = load_results(out / "results.json")
    assert {r["task_id"] for r in res["runs"]} == {0, 2} and [t["id"] for t in res["tasks"]] == [0, 2]
    assert res["summary"]["dry-baseline-27b"]["score_max"] == 2.0
    assert main(["run", "--config", cfg, "--out", str(out), "--tasks", "5"]) == 0
    res = load_results(out / "results.json")
    assert {r["task_id"] for r in res["runs"]} == {0, 2, 5} and [t["id"] for t in res["tasks"]] == [0, 2, 5]
    assert len([r for r in res["runs"] if r["task_id"] == 2]) == 3, "earlier rows kept, not duplicated"
    assert validate_results(res) == []


def test_unknown_task_id_is_an_error(tmp_path, capsys):
    cfg = str(KIT / "examples" / "instance-dryrun.yaml")
    assert main(["run", "--config", cfg, "--out", str(tmp_path / "r"), "--tasks", "0,42"]) == 2
    assert "unknown task id" in capsys.readouterr().err
    assert main(["run", "--config", cfg, "--out", str(tmp_path / "r"), "--tasks", "a,b"]) == 2


def test_dry_run_flag_works_on_the_real_example_without_network(tmp_path, monkeypatch):
    import httpx

    def no_network(*a, **k):
        raise AssertionError("network used in --dry-run")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    out = tmp_path / "r"
    assert main(["run", "--config", str(KIT / "examples" / "instance.yaml"), "--out", str(out), "--dry-run", "--tasks", "0,1,8"]) == 0
    res = load_results(out / "results.json")
    assert {m["backend"] for m in res["models"]} == {"dryrun"} and len(res["models"]) == 3
    assert main(["preflight", "--config", str(KIT / "examples" / "instance.yaml"), "--dry-run"]) == 0


# ---------------------------------------------------------------- GPU guard through the CLI


def test_run_refuses_when_gpu_is_busy_unless_forced(tmp_path, monkeypatch, capsys):
    cfg = str(KIT / "examples" / "instance-dryrun.yaml")
    monkeypatch.setattr("llm_bench.cli.loaded_foreign_models", lambda inst: ([{"endpoint": "http://x", "model": "c3-triage:latest"}], []))
    out = tmp_path / "r"
    assert main(["run", "--config", cfg, "--out", str(out), "--tasks", "0"]) == 3
    assert "c3-triage" in capsys.readouterr().err and not (out / "results.json").exists()
    assert main(["run", "--config", cfg, "--out", str(out), "--tasks", "0", "--force"]) == 0
    assert (out / "results.json").exists()


def test_preflight_cli_exit_code_3_and_json(monkeypatch, capsys):
    from llm_bench import preflight as pf

    monkeypatch.setattr(pf, "loaded_foreign_models", lambda inst, cf=None: ([{"endpoint": "http://ollama.test", "model": "hermes-27b"}], []))
    monkeypatch.setattr(pf, "default_client", lambda base_url: __import__("httpx").Client(base_url=base_url, transport=__import__("httpx").MockTransport(
        lambda req: __import__("httpx").Response(200, json={"models": [{"name": "m:1", "size": 1}]}))))
    cfg = KIT / "examples" / "instance.yaml"
    rc = main(["preflight", "--config", str(cfg)])
    out = capsys.readouterr()
    report = json.loads(out.out)
    assert rc in (2, 3) and report["exit_code"] == rc and report["checks"]
    assert out.err.strip(), "one-line reason on stderr"


# ---------------------------------------------------------------- tool loop


def scenario(battery):
    return json.loads(battery.read("tool_calling/scenario.json"))


def test_tool_loop_good_model_follows_the_chain(battery):
    sc = scenario(battery)
    be = DryRunBackend(ModelCfg(name="d", backend="dryrun"), {"seed": 7}, battery.tasks_dir / "dryrun" / "good", sc)
    lr = run_tool_loop(be, sc, num_ctx=8192, num_predict=500)
    assert lr.error is None and [t["name"] for t in lr.trace] == ["lookup_host", "get_switch_port", "get_port_stats", "get_port_stats", "get_port_stats"]
    assert all(t["valid"] and not t["result_error"] for t in lr.trace)
    assert "18,423" in lr.final_text and lr.turns == 6 and lr.ttft_ms and lr.output_tokens


def test_tool_loop_bad_model_never_calls_tools(battery):
    sc = scenario(battery)
    be = DryRunBackend(ModelCfg(name="d", backend="dryrun", dryrun={"quality": 0.0}), {"seed": 7}, battery.tasks_dir / "dryrun" / "good", sc)
    lr = run_tool_loop(be, sc, num_ctx=8192, num_predict=500)
    assert lr.trace == [] and "18,423" not in lr.final_text


def test_tool_loop_gives_up_after_max_turns(battery):
    sc = dict(scenario(battery), max_turns=3)

    class Looper(DryRunBackend):
        def chat(self, messages, **k):
            r = super().chat(messages, **k)
            r.text, r.tool_calls = "", [{"id": "c", "name": "lookup_host", "arguments": {"hostname": "nas01"}}]
            return r

    be = Looper(ModelCfg(name="d", backend="dryrun"), {"seed": 7}, None, sc)
    lr = run_tool_loop(be, sc, num_ctx=8192, num_predict=100)
    assert lr.used_all_turns and "no final answer" in lr.error and len(lr.trace) == 3


def test_tool_loop_flags_invalid_calls(battery):
    sc = scenario(battery)

    class Sloppy(DryRunBackend):
        n = 0

        def chat(self, messages, **k):
            r = super().chat(messages, **k)
            Sloppy.n += 1
            if Sloppy.n == 1:
                r.text, r.tool_calls = "", [{"id": "c", "name": "get_port_stats", "arguments": {"switch": "sw-edge-3"}}]
            elif Sloppy.n == 2:
                r.text, r.tool_calls = "", [{"id": "d", "name": "reboot_switch", "arguments": {}}]
            else:
                r.text, r.tool_calls = "I could not find it.", []
            return r

    lr = run_tool_loop(Sloppy(ModelCfg(name="d", backend="dryrun"), {"seed": 7}, None, sc), sc, num_ctx=8192, num_predict=100)
    assert [t["valid"] for t in lr.trace] == [False, False] and lr.trace[0]["result_error"] and lr.final_text.startswith("I could not")


# ---------------------------------------------------------------- speed ladder prompts


def test_ladder_prompt_size_tracks_the_estimate(battery):
    corpus = battery.read("speed_ladder/corpus.txt").strip().splitlines()
    for target in (512, 4096, 65536):
        p = build_ladder_prompt(corpus, target, 4.0, f"[Document {target}]", "Summarize it.")
        assert abs(estimate_tokens(p) - target) / target < 0.03, (target, estimate_tokens(p))
        assert p.endswith("Summarize it.") and p.startswith(f"[Document {target}]")
    a = build_ladder_prompt(corpus, 4096, 4.0, "[Document 4096]", "x")
    b = build_ladder_prompt(corpus, 4096, 4.0, "[Document 16384]", "x")
    assert a[:15] != b[:15], "prompts differ from the first token so the server cannot reuse a cached prefix"
    cal = build_ladder_prompt(corpus, 4096, 3.0, "[D]", "x")
    assert len(cal) < len(a), "a lower chars-per-token calibration gives a shorter prompt"


def test_num_ctx_clipping_for_small_limits(tmp_path):
    inst = load_instance(KIT / "examples" / "instance-dryrun.yaml")
    inst.options["num_ctx_max"] = 16384
    b = bat.load_battery(inst)
    res = Runner(inst, b, tmp_path / "r", task_ids=[1, 8], log=lambda m: None).run()
    ladder = [r for r in res["runs"] if r["model"] == "dry-baseline-27b" and r["task_id"] == 1]
    clipped = [r for r in ladder if r["details"].get("clipped_to_fit_num_ctx_max")]
    assert {r["variant"] for r in clipped} == {"16384", "32768", "65536"}
    assert all(r["details"]["num_ctx"] <= 16384 for r in ladder)
    needle = next(r for r in res["runs"] if r["model"] == "dry-baseline-27b" and r["task_id"] == 8)
    assert needle["details"]["clipped_to_fit_num_ctx_max"] and needle["details"]["estimated_log_tokens"] < 16384


# ---------------------------------------------------------------- rubric and score


def test_rubric_is_emitted_for_tasks_3_9_10(dryrun_out):
    entries = yaml.safe_load((dryrun_out / "rubric.yaml").read_text())
    assert sorted({(e["task_id"]) for e in entries}) == [3, 9, 10]
    assert len(entries) == 9
    for e in entries:
        assert {"model", "task_id"} <= set(e)
        if e["task_id"] == 10:
            assert set(e["verdict"]) >= {"latency_sensitive", "quality_sensitive", "vram_constrained", "would_i_run_it"}
        else:
            assert e["score"] is None and e["note"] == ""
    text = (dryrun_out / "rubric.yaml").read_text()
    assert "Score 1:" in text, "the rubric card criteria are written into the file as comments"


def fill(rubric_path: Path, out: Path):
    entries = yaml.safe_load(rubric_path.read_text())
    for e in entries:
        good = "baseline" in e["model"]
        if e["task_id"] == 3:
            e["score"], e["note"] = (1 if good else 0.5), "order fine"
        elif e["task_id"] == 9:
            e["score"], e["note"] = (1 if good else 0), "drew it"
        else:
            e["verdict"] = {"latency_sensitive": "yes", "quality_sensitive": "maybe", "vram_constrained": "no" if not good else "yes", "would_i_run_it": "yes" if good else "maybe", "note": "ok for triage"}
    out.write_text(yaml.safe_dump(entries, sort_keys=False))


def test_score_merges_rubric_into_results(dryrun_out, tmp_path):
    res_copy = tmp_path / "results.json"
    shutil.copy(dryrun_out / "results.json", res_copy)
    filled = tmp_path / "rubric.yaml"
    fill(dryrun_out / "rubric.yaml", filled)
    assert main(["score", "--results", str(res_copy), "--rubric", str(filled)]) == 0
    res = load_results(res_copy)
    fw = next(r for r in res["runs"] if r["model"] == "dry-baseline-27b" and r["task_id"] == 3)
    assert fw["score"] == 1.0 and fw["pass"] is True and fw["score_kind"] == "mixed" and fw["details"]["rubric_note"] == "order fine"
    assert fw["details"]["auto_passed"] == 10, "auto check results survive the merge"
    build = next(r for r in res["runs"] if r["model"] == "dry-moe-35b" and r["task_id"] == 9)
    assert build["score"] == 0.0 and build["pass"] is False and build["score_kind"] == "rubric"
    half = next(r for r in res["runs"] if r["model"] == "dry-moe-35b" and r["task_id"] == 3)
    assert half["score"] == 0.5 and half["pass"] is False
    v = res["verdicts"]["dry-baseline-27b"]
    assert v == {"latency_sensitive": "yes", "quality_sensitive": "maybe", "vram_constrained": "yes", "would_i_run_it": "yes", "note": "ok for triage"}
    assert 3 not in res["summary"]["dry-baseline-27b"]["unscored_task_ids"] and 9 not in res["summary"]["dry-baseline-27b"]["unscored_task_ids"]
    before = load_results(dryrun_out / "results.json")["summary"]["dry-baseline-27b"]["score_total"]
    assert res["summary"]["dry-baseline-27b"]["score_total"] == pytest.approx(before + 2.0)
    assert validate_results(res) == []


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda e: e[0].update(score=0.75), "score must be"),
        (lambda e: e[0].update(model="ghost"), "unknown model"),
        (lambda e: e[0].update(task_id=77), "not in the results"),
        (lambda e: [x for x in e if x["task_id"] == 10][0]["verdict"].update(would_i_run_it="perhaps"), "yes, no or maybe"),
    ],
)
def test_score_rejects_bad_rubric_and_leaves_results_untouched(dryrun_out, tmp_path, mutate, needle, capsys):
    res_copy = tmp_path / "results.json"
    shutil.copy(dryrun_out / "results.json", res_copy)
    entries = yaml.safe_load((dryrun_out / "rubric.yaml").read_text())
    mutate(entries)
    bad = tmp_path / "rubric.yaml"
    bad.write_text(yaml.safe_dump(entries, sort_keys=False))
    assert main(["score", "--results", str(res_copy), "--rubric", str(bad)]) == 2
    assert needle in capsys.readouterr().err
    assert load_results(res_copy) == load_results(dryrun_out / "results.json")


def test_score_with_nothing_filled_in_changes_nothing(dryrun_out, tmp_path):
    res_copy = tmp_path / "results.json"
    shutil.copy(dryrun_out / "results.json", res_copy)
    assert main(["score", "--results", str(res_copy), "--rubric", str(dryrun_out / "rubric.yaml")]) == 0
    assert load_results(res_copy)["summary"] == load_results(dryrun_out / "results.json")["summary"]


def test_rubric_reemit_keeps_filled_scores(tmp_path, dryrun_instance):
    b = bat.load_battery(dryrun_instance)
    out = tmp_path / "r"
    res = Runner(dryrun_instance, b, out, log=lambda m: None).run()
    path = out / "rubric.yaml"
    fill(path, path)
    rubric.emit(res, b, path)
    entries = yaml.safe_load(path.read_text())
    assert next(e for e in entries if e["task_id"] == 3 and "baseline" in e["model"])["score"] == 1
    assert next(e for e in entries if e["task_id"] == 10 and "baseline" in e["model"])["verdict"]["would_i_run_it"] == "yes"
