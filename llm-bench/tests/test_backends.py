from __future__ import annotations

import json

import httpx
import pytest

from llm_bench.backends import DryRunBackend, OllamaBackend, OpenAICompatBackend, Tag, make_backend, ollama as ollama_mod
from llm_bench.backends.openai_compat import cost_usd
from llm_bench.config import ModelCfg, load_instance, normalize_pricing, parse_instance
from llm_bench.preflight import EXIT_FAIL, EXIT_GPU_BUSY, EXIT_OK, loaded_foreign_models, run_preflight
from llm_bench.scoring import validators

OPTS = {"temperature": 0.2, "seed": 7, "num_predict": 512, "num_ctx_max": 65536, "num_ctx_default": 8192, "request_timeout_s": 30}


def ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs).encode() + b"\n"


def make_ollama(handler, name="m:1", endpoint="http://ollama.test", **kw):
    cfg = ModelCfg(name=name, backend="ollama", endpoint=endpoint, **kw)
    client = httpx.Client(base_url=endpoint, transport=httpx.MockTransport(handler))
    return OllamaBackend(cfg, OPTS, client=client)


# ---------------------------------------------------------------- ollama chat


def test_ollama_streaming_metrics():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        chunks = [
            {"message": {"role": "assistant", "content": "", "thinking": "let me think"}, "done": False},
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {"message": {"role": "assistant", "content": "lo"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "eval_count": 40, "eval_duration": 2_000_000_000,
             "prompt_eval_count": 123, "load_duration": 1_500_000_000},
        ]
        return httpx.Response(200, content=ndjson(*chunks))

    b = make_ollama(handler)
    r = b.chat([{"role": "user", "content": "hi"}], num_ctx=4096, num_predict=64)
    assert r.ok and r.text == "Hello" and r.thinking == "let me think"
    assert r.tokens_per_second == 20.0, "eval_count / eval_duration"
    assert r.prompt_tokens == 123 and r.output_tokens == 40
    assert r.ttft_ms is not None and r.ttft_any_ms is not None and r.ttft_any_ms <= r.ttft_ms
    assert r.load_seconds == 1.5 and r.finish_reason == "stop"
    body = seen["body"]
    assert body["stream"] is True and body["options"]["num_ctx"] == 4096 and body["options"]["num_predict"] == 64
    assert body["options"]["seed"] == 7 and body["options"]["temperature"] == 0.2


def test_ollama_ttft_is_first_content_token_not_first_chunk():
    import time

    def slow_stream():
        yield json.dumps({"message": {"content": "", "thinking": "t"}, "done": False}).encode() + b"\n"
        time.sleep(0.15)
        yield json.dumps({"message": {"content": "answer"}, "done": False}).encode() + b"\n"
        yield json.dumps({"message": {"content": ""}, "done": True, "eval_count": 1, "eval_duration": 1}).encode() + b"\n"

    b = make_ollama(lambda req: httpx.Response(200, content=slow_stream()))
    r = b.chat([{"role": "user", "content": "x"}])
    assert r.ttft_ms >= 120 and r.ttft_any_ms < 100


def test_ollama_tool_calls_and_message_shapes():
    def handler(req):
        call = {"function": {"name": "lookup_host", "arguments": {"hostname": "backup02"}}}
        return httpx.Response(200, content=ndjson(
            {"message": {"content": "", "tool_calls": [call]}, "done": False},
            {"message": {"content": ""}, "done": True, "eval_count": 9, "eval_duration": 1_000_000_000, "prompt_eval_count": 50},
        ))

    b = make_ollama(handler)
    r = b.chat([{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "lookup_host"}}])
    assert [c["name"] for c in r.tool_calls] == ["lookup_host"] and r.tool_calls[0]["arguments"] == {"hostname": "backup02"}
    assert r.ttft_ms is not None
    assert b.assistant_message(r)["tool_calls"][0]["function"]["name"] == "lookup_host"
    assert b.tool_message(r.tool_calls[0], "{}") == {"role": "tool", "tool_name": "lookup_host", "content": "{}"}


def test_ollama_errors_are_reported_not_raised():
    r = make_ollama(lambda req: httpx.Response(400, json={"error": "model does not support tools"})).chat([{"role": "user", "content": "x"}])
    assert r.error == "HTTP 400: model does not support tools"
    r = make_ollama(lambda req: httpx.Response(200, content=ndjson({"error": "out of memory"}))).chat([{"role": "user", "content": "x"}])
    assert r.error == "out of memory"
    r = make_ollama(lambda req: httpx.Response(200, content=ndjson({"message": {"content": "partial"}, "done": False}))).chat([{"role": "user", "content": "x"}])
    assert r.error and "without a final message" in r.error and r.text == "partial"

    def boom(req):
        raise httpx.ConnectError("refused")

    assert "ConnectError" in make_ollama(boom).chat([{"role": "user", "content": "x"}]).error


# ---------------------------------------------------------------- ollama model info, VRAM, pull


def test_ollama_model_info_vram_and_gpu_share(monkeypatch):
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m:1", "size": 12_100_000_000, "details": {"quantization_level": "Q4_0"}}]})
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"quantization_level": "IQ3_S"}})
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "m:1", "size": 20_000_000_000, "size_vram": 14_000_000_000}]})
        return httpx.Response(404)

    monkeypatch.setattr(ollama_mod.gpu, "memory_used_mb", lambda: 13200)
    remote = make_ollama(handler).model_info()
    assert remote["vram_after_load_mb"] == 13351, "a remote endpoint never uses this machine's nvidia-smi; /api/ps size_vram instead"
    b = make_ollama(handler, endpoint="http://127.0.0.1:11434")
    info = b.model_info()
    assert info["size_gb"] == 12.1 and info["quant"] == "IQ3_S"
    assert info["gpu_share_pct"] == 70, "size_vram / size from /api/ps"
    assert info["vram_after_load_mb"] == 13200, "nvidia-smi memory.used wins when the endpoint is local"


def test_ollama_vram_falls_back_to_ps_when_nvidia_smi_missing(monkeypatch):
    def handler(req):
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "m:1", "size": 2 * 1048576 * 1000, "size_vram": 1048576 * 1000}]})
        return httpx.Response(200, json={"models": []})

    monkeypatch.setattr(ollama_mod.gpu, "memory_used_mb", lambda: None)
    info = make_ollama(handler, endpoint="http://localhost:11434").model_info()
    assert info["vram_after_load_mb"] == 1000 and info["gpu_share_pct"] == 50


def test_nvidia_smi_query_command(monkeypatch):
    from llm_bench.backends import gpu

    calls = []

    class R:
        stdout = "4321\n1000\n"

    monkeypatch.setattr(gpu.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(gpu.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or R())
    assert gpu.memory_used_mb() == 5321
    assert calls[0] == ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]
    monkeypatch.setattr(gpu.shutil, "which", lambda n: None)
    assert gpu.memory_used_mb() is None


def test_ollama_pull_when_missing_and_enabled():
    state = {"pulled": False}

    def handler(req):
        if req.url.path == "/api/tags":
            models = [{"name": "hf.co/x/y:Q4", "size": 1}] if state["pulled"] else []
            return httpx.Response(200, json={"models": models})
        if req.url.path == "/api/pull":
            assert json.loads(req.content)["model"] == "hf.co/x/y:Q4"
            state["pulled"] = True
            return httpx.Response(200, content=ndjson({"status": "pulling", "total": 100, "completed": 50}, {"status": "success"}))
        return httpx.Response(404)

    b = make_ollama(handler, name="hf.co/x/y:Q4", pull=True)
    b.ensure_present()
    assert state["pulled"]
    b2 = make_ollama(lambda req: httpx.Response(200, json={"models": []}), name="nope:1", pull=False)
    with pytest.raises(RuntimeError, match="pull is not enabled"):
        b2.ensure_present()


def test_model_name_matching_treats_latest_as_equal():
    assert ollama_mod.same_model("qwen3:latest", "qwen3")
    assert ollama_mod.same_model("a/b:Q4", "a/b:Q4") and not ollama_mod.same_model("a:1", "a:2")
    assert ollama_mod.is_local_endpoint("http://127.0.0.1:11434") and ollama_mod.is_local_endpoint("http://localhost:11434")
    assert not ollama_mod.is_local_endpoint("http://203.0.113.9:11434")


# ---------------------------------------------------------------- openai compat


def sse(*objs, done=True):
    body = "".join(f"data: {json.dumps(o)}\n\n" for o in objs)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def make_openai(handler, pricing=None, monkeypatch=None):
    cfg = ModelCfg(name="flash", backend="openai_compat", endpoint="https://api.test/v1", api_key_env="PG_TEST_KEY", pricing=pricing)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatBackend(cfg, OPTS, client=client)


def test_openai_stream_usage_and_cost(monkeypatch):
    monkeypatch.setenv("PG_TEST_KEY", "sk-test")
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        seen["url"] = str(req.url)
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {"content": " there"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}},
        ))

    b = make_openai(handler, pricing={"input_per_m": 0.28, "output_per_m": 0.42})
    r = b.chat([{"role": "user", "content": "x"}], num_predict=99)
    assert r.ok and r.text == "Hi there" and r.prompt_tokens == 1_000_000 and r.output_tokens == 500_000
    assert r.cost_usd == round(0.28 + 0.21, 6)
    assert seen["auth"] == "Bearer sk-test" and seen["url"] == "https://api.test/v1/chat/completions"
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {"include_usage": True} and seen["body"]["max_tokens"] == 99
    assert r.ttft_ms is not None and r.finish_reason == "stop"


@pytest.mark.parametrize("pricing", [None, {}, {"input_per_m": None, "output_per_m": None}, {"input_per_m": 0.3, "output_per_m": None}])
def test_unknown_pricing_means_null_cost_never_zero(monkeypatch, pricing):
    monkeypatch.setenv("PG_TEST_KEY", "sk-test")
    cfg_pricing = normalize_pricing(pricing)
    assert cfg_pricing is None
    b = make_openai(lambda req: httpx.Response(200, content=sse(
        {"choices": [{"delta": {"content": "ok"}}]}, {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})), pricing=cfg_pricing)
    r = b.chat([{"role": "user", "content": "x"}])
    assert r.ok and r.cost_usd is None
    assert cost_usd(pricing, 10, 5) is None


def test_openai_tool_calls_accumulate_and_message_shapes(monkeypatch):
    monkeypatch.setenv("PG_TEST_KEY", "k")

    def handler(req):
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "get_port_stats", "arguments": '{"switch": "sw-'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'agg-1", "port": 12}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
        ))

    b = make_openai(handler)
    r = b.chat([{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "get_port_stats"}}])
    assert r.tool_calls == [{"id": "call_a", "name": "get_port_stats", "arguments": {"switch": "sw-agg-1", "port": 12}}]
    am = b.assistant_message(r)
    assert am["tool_calls"][0]["id"] == "call_a" and json.loads(am["tool_calls"][0]["function"]["arguments"])["port"] == 12
    assert b.tool_message(r.tool_calls[0], "{}") == {"role": "tool", "tool_call_id": "call_a", "content": "{}"}


def test_openai_missing_key_and_http_errors(monkeypatch):
    monkeypatch.delenv("PG_TEST_KEY", raising=False)
    assert "PG_TEST_KEY" in make_openai(lambda req: httpx.Response(200)).chat([{"role": "user", "content": "x"}]).error
    monkeypatch.setenv("PG_TEST_KEY", "k")
    r = make_openai(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}})).chat([{"role": "user", "content": "x"}])
    assert r.error == "HTTP 401: bad key"


def test_openai_retries_rate_limits(monkeypatch):
    monkeypatch.setenv("PG_TEST_KEY", "k")
    monkeypatch.setattr("llm_bench.backends.openai_compat.time.sleep", lambda s: None)
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        if n["c"] < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    r = make_openai(handler).chat([{"role": "user", "content": "x"}])
    assert r.ok and r.text == "ok" and n["c"] == 3


# ---------------------------------------------------------------- dryrun + factory


def test_dryrun_is_deterministic_and_needs_no_network(battery):
    cfg = ModelCfg(name="d", backend="dryrun", dryrun={"quality": 0.5, "tps": 40})
    fixtures = battery.tasks_dir / "dryrun" / "good"
    a, b = DryRunBackend(cfg, OPTS, fixtures), DryRunBackend(cfg, OPTS, fixtures)
    msgs = [{"role": "user", "content": "hello"}]
    ra = [a.chat(msgs, tag=Tag(7, p)).text for p in ("cidr_contains", "parse_size", "sliding_max", "normalize_mac")]
    rb = [b.chat(msgs, tag=Tag(7, p)).text for p in ("cidr_contains", "parse_size", "sliding_max", "normalize_mac")]
    assert ra == rb
    assert make_backend(cfg, OPTS, fixtures_dir=fixtures).kind == "dryrun"


# ---------------------------------------------------------------- preflight


INSTANCE = {
    "kit": "llm-bench", "title": "t", "hardware": "h", "baseline": "bench:1",
    "models": [
        {"name": "bench:1", "backend": "ollama", "endpoint": "http://ollama.test"},
        {"name": "hf.co/a/b:Q4", "backend": "ollama", "endpoint": "http://ollama.test", "pull": True},
    ],
}


def factory(tags, ps, calls=None):
    def make(base_url):
        def handler(req):
            if calls is not None:
                calls.append(req.url.path)
            if req.url.path == "/api/tags":
                return httpx.Response(200, json={"models": tags})
            if req.url.path == "/api/ps":
                return httpx.Response(200, json={"models": ps})
            return httpx.Response(404)

        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler))

    return make


def test_preflight_ok_and_pull_warning():
    inst = parse_instance(INSTANCE)
    code, rep = run_preflight(inst, client_factory=factory([{"name": "bench:1", "size": 1_000_000}], [{"name": "bench:1"}]))
    assert code == EXIT_OK and rep["ok"] and rep["loaded_models"] == []
    statuses = {c["name"]: c["status"] for c in rep["checks"]}
    assert statuses["model hf.co/a/b:Q4"] == "warn" and statuses["gpu guard"] == "ok"
    assert statuses["docker"] == "warn", "docker absent (disabled in tests) only warns"
    json.dumps(rep)


def test_preflight_refuses_when_production_model_is_loaded():
    inst = parse_instance(INSTANCE)
    make = factory([{"name": "bench:1", "size": 1}], [{"name": "c3-triage:latest"}, {"name": "bench:1"}])
    code, rep = run_preflight(inst, client_factory=make)
    assert code == EXIT_GPU_BUSY and not rep["ok"]
    assert rep["loaded_models"] == [{"endpoint": "http://ollama.test", "model": "c3-triage:latest"}]
    assert "c3-triage" in rep["reason"] and "--force" in rep["reason"]


def test_preflight_force_overrides_guard_but_says_so():
    inst = parse_instance(INSTANCE)
    make = factory([{"name": "bench:1", "size": 1}], [{"name": "c3-triage:latest"}])
    code, rep = run_preflight(inst, force=True, client_factory=make)
    assert code == EXIT_OK and rep["forced"] is True
    guard = [c for c in rep["checks"] if c["name"] == "gpu guard"][-1]
    assert guard["status"] == "warn" and "--force" in guard["detail"]


def test_preflight_benchmark_models_loaded_do_not_trip_guard():
    inst = parse_instance(INSTANCE)
    foreign, errs = loaded_foreign_models(inst, factory([], [{"name": "bench:1"}, {"name": "hf.co/a/b:Q4"}]))
    assert foreign == [] and errs == []


def test_preflight_missing_model_without_pull_fails():
    d = json.loads(json.dumps(INSTANCE))
    d["models"][1].pop("pull")
    code, rep = run_preflight(parse_instance(d), client_factory=factory([{"name": "bench:1", "size": 1}], []))
    assert code == EXIT_FAIL and "pull is not enabled" in rep["reason"]


def test_preflight_unreachable_endpoint_fails():
    def make(base_url):
        def boom(req):
            raise httpx.ConnectError("refused")

        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(boom))

    code, rep = run_preflight(parse_instance(INSTANCE), client_factory=make)
    assert code == EXIT_FAIL and "unreachable" in rep["reason"]


def test_preflight_api_key_missing(monkeypatch):
    monkeypatch.delenv("PG_TEST_KEY", raising=False)
    d = {"kit": "llm-bench", "title": "t", "hardware": "h", "models": [{"name": "flash", "backend": "openai_compat", "endpoint": "https://api.test/v1", "api_key_env": "PG_TEST_KEY"}]}
    code, rep = run_preflight(parse_instance(d))
    assert code == EXIT_FAIL and "PG_TEST_KEY" in rep["reason"]


def test_preflight_docker_present_is_reported(monkeypatch):
    monkeypatch.delenv("LLM_BENCH_NO_DOCKER", raising=False)
    validators.reset_docker_cache()
    monkeypatch.setattr(validators, "docker_status", lambda need_compose=False: (True, "ok"))
    monkeypatch.setattr("llm_bench.preflight.subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())
    code, rep = run_preflight(parse_instance(INSTANCE), client_factory=factory([{"name": "bench:1", "size": 1}], []))
    assert {c["name"]: c["status"] for c in rep["checks"]}["docker"] == "ok"
