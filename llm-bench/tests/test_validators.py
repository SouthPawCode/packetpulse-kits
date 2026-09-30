from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from llm_bench.scoring import run_scorer, validators
from llm_bench.scoring.validators import compose_features, run_code_tests, score_code, strip_nginx_comments


def fx(battery, name):
    return (battery.tasks_dir / "dryrun" / "good" / name).read_text()


def problems(battery):
    return yaml.safe_load(battery.read("code/problems.yaml"))


# ---------------------------------------------------------------- code in a subprocess


def test_ten_problems_each_with_hidden_tests(battery):
    probs = problems(battery)
    assert len(probs) == 10 and len({p["id"] for p in probs}) == 10
    assert all(p["tests"].strip() and p["prompt"].strip() for p in probs)


def test_reference_solutions_pass_hidden_tests(battery):
    for p in problems(battery):
        res = score_code(fx(battery, f"7__{p['id']}.md"), p, timeout=20)
        assert res.score == 1.0, (p["id"], res.details)


def test_wrong_solution_fails(battery):
    p = next(p for p in problems(battery) if p["id"] == "compress_ports")
    wrong = "```python\ndef compress_ports(ports):\n    return ','.join(map(str, sorted(ports)))\n```"
    res = score_code(wrong, p)
    assert res.score == 0.0 and "assertion" in res.details["result"].lower()


def test_missing_function_and_syntax_error_fail(battery):
    p = problems(battery)[0]
    assert score_code("```python\ndef other():\n    pass\n```", p).score == 0.0
    assert score_code("```python\ndef cidr_contains(:\n```", p).score == 0.0
    assert score_code("I cannot write code.", p).score == 0.0


def test_infinite_loop_times_out_quickly():
    ok, msg = run_code_tests("def f():\n    while True:\n        pass\n", "f()\n", timeout=2)
    assert ok is False and "timeout" in msg or "CPU" in msg or "exit" in msg


def test_hidden_tests_are_not_visible_to_the_model(battery):
    for p in problems(battery):
        assert "assert " not in p["prompt"]


def test_sliding_max_performance_test_rejects_quadratic(battery):
    p = next(p for p in problems(battery) if p["id"] == "sliding_max")
    slow = "```python\ndef sliding_max(values, k):\n    if k < 1:\n        raise ValueError\n    return [max(values[i:i+k]) for i in range(len(values) - k + 1)]\n```"
    res = score_code(slow, p, timeout=30)
    assert res.score == 0.0


def test_code_answer_with_example_block_still_scores(battery):
    p = next(p for p in problems(battery) if p["id"] == "merge_intervals")
    good = fx(battery, "7__merge_intervals.md")
    with_example = good + "\nExample:\n```python\nprint(merge_intervals([(1, 2)]))\n```\n"
    assert score_code(with_example, p).score == 1.0


# ---------------------------------------------------------------- docker validators, unavailable


def test_nginx_and_compose_report_validator_unavailable(battery):
    nginx = next(v for v in battery.task(4).variants if v["id"] == "nginx")
    res = run_scorer(nginx["scorer"], fx(battery, "4__nginx.md"), battery=battery)
    assert res.score is None and res.passed is None
    assert "validator unavailable" in res.note and "not scored" in res.note
    compose = next(v for v in battery.task(4).variants if v["id"] == "compose")
    res = run_scorer(compose["scorer"], fx(battery, "4__compose.md"), battery=battery)
    assert res.score is None and "validator unavailable" in res.note


# ---------------------------------------------------------------- docker validators, mocked


@pytest.fixture
def docker_ok(monkeypatch):
    monkeypatch.delenv("LLM_BENCH_NO_DOCKER", raising=False)
    monkeypatch.setattr(validators, "docker_status", lambda need_compose=False: (True, "ok"))


def test_nginx_valid_and_complete_scores_one(battery, docker_ok, monkeypatch):
    seen = {}

    def fake(conf, timeout=180):
        seen["conf"] = conf
        return True, "nginx: configuration file /etc/nginx/nginx.conf test is successful"

    monkeypatch.setattr(validators, "run_nginx_test", fake)
    nginx = next(v for v in battery.task(4).variants if v["id"] == "nginx")
    res = run_scorer(nginx["scorer"], fx(battery, "4__nginx.md"), battery=battery)
    assert res.score == 1.0, res.details
    assert seen["conf"].lstrip().startswith("map ") and "```" not in seen["conf"]


def test_nginx_valid_but_incomplete_scores_half(battery, docker_ok, monkeypatch):
    monkeypatch.setattr(validators, "run_nginx_test", lambda conf, timeout=180: (True, "ok"))
    nginx = next(v for v in battery.task(4).variants if v["id"] == "nginx")
    minimal = "```nginx\nserver {\n    listen 80;\n    location / { proxy_pass http://192.168.40.15:8080; }\n}\n```"
    res = run_scorer(nginx["scorer"], minimal, battery=battery)
    assert res.score == 0.5 and "hsts" in res.details["features_missed"] and "redirect_301" in res.details["features_missed"]


def test_nginx_invalid_scores_zero(battery, docker_ok, monkeypatch):
    monkeypatch.setattr(validators, "run_nginx_test", lambda conf, timeout=180: (False, '[emerg] unknown directive "proxy_passs"'))
    nginx = next(v for v in battery.task(4).variants if v["id"] == "nginx")
    res = run_scorer(nginx["scorer"], fx(battery, "4__nginx.md"), battery=battery)
    assert res.score == 0.0 and res.details["valid"] is False and "emerg" in res.details["validator_output"]


def test_nginx_directives_in_comments_do_not_count(battery, docker_ok, monkeypatch):
    monkeypatch.setattr(validators, "run_nginx_test", lambda conf, timeout=180: (True, "ok"))
    nginx = next(v for v in battery.task(4).variants if v["id"] == "nginx")
    cheat = "```nginx\nserver { listen 80; }\n# return 301 https://$host$request_uri;\n# add_header Strict-Transport-Security max-age=1;\n```"
    res = run_scorer(nginx["scorer"], cheat, battery=battery)
    assert "redirect_301" in res.details["features_missed"] and "hsts" in res.details["features_missed"]
    assert "return 301" not in strip_nginx_comments("# return 301 x;\nserver {}")


def test_nginx_missing_openssl_is_unavailable(battery, docker_ok, monkeypatch):
    def boom(conf, timeout=180):
        raise RuntimeError("openssl not found (needed to make a throwaway test certificate)")

    monkeypatch.setattr(validators, "run_nginx_test", boom)
    nginx = next(v for v in battery.task(4).variants if v["id"] == "nginx")
    res = run_scorer(nginx["scorer"], fx(battery, "4__nginx.md"), battery=battery)
    assert res.score is None and "openssl" in res.note


def test_nginx_container_command_shape(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "nginx: configuration file test is successful")

    monkeypatch.setattr(validators, "_make_test_cert", lambda d: None)
    monkeypatch.setattr(validators.subprocess, "run", fake_run)
    ok, _ = validators.run_nginx_test("server {}")
    cmd = captured["cmd"]
    assert ok and cmd[:3] == ["docker", "run", "--rm"] and cmd[-3:] == ["nginx:alpine", "nginx", "-t"]
    assert any(":/etc/nginx/conf.d/default.conf:ro" in a for a in cmd)


COMPOSE_OK = {
    "services": {
        "db": {"image": "postgres:16", "restart": "unless-stopped", "healthcheck": {"test": ["CMD-SHELL", "pg_isready"]}, "networks": {"backend": None},
               "volumes": [{"type": "volume", "source": "dbdata", "target": "/var/lib/postgresql/data"}]},
        "web": {"image": "x", "restart": "unless-stopped", "ports": [{"published": "8080", "target": 8000}], "networks": {"backend": None},
                "depends_on": {"db": {"condition": "service_healthy", "required": True}}},
    },
    "volumes": {"dbdata": {}},
    "networks": {"backend": {}},
}


def test_compose_features_full_and_missing():
    assert all(compose_features(COMPOSE_OK).values())
    import copy

    bad = copy.deepcopy(COMPOSE_OK)
    del bad["services"]["db"]["healthcheck"]
    bad["services"]["web"]["depends_on"]["db"]["condition"] = "service_started"
    bad["services"]["web"].pop("restart")
    f = compose_features(bad)
    assert not f["postgres_healthcheck"] and not f["depends_on_service_healthy"] and not f["restart_policy"] and f["named_volume"]
    bad["networks"] = {}
    assert not compose_features(bad)["custom_network"]


def test_compose_scoring_with_mocked_config(battery, docker_ok, monkeypatch):
    monkeypatch.setattr(validators, "run_compose_config", lambda text, timeout=60: (True, "", COMPOSE_OK))
    compose = next(v for v in battery.task(4).variants if v["id"] == "compose")
    assert run_scorer(compose["scorer"], fx(battery, "4__compose.md"), battery=battery).score == 1.0
    monkeypatch.setattr(validators, "run_compose_config", lambda text, timeout=60: (True, "", {"services": {"web": {"image": "x"}}}))
    res = run_scorer(compose["scorer"], "```yaml\nservices: {web: {image: x}}\n```", battery=battery)
    assert res.score == 0.5 and "postgres_service" in res.details["features_missed"]
    monkeypatch.setattr(validators, "run_compose_config", lambda text, timeout=60: (False, "services.web must be a mapping", None))
    res = run_scorer(compose["scorer"], "```yaml\nservices: nope\n```", battery=battery)
    assert res.score == 0.0 and res.details["valid"] is False


def test_docker_status_honours_disable_switch(monkeypatch):
    validators.reset_docker_cache()
    monkeypatch.setenv("LLM_BENCH_NO_DOCKER", "1")
    ok, why = validators.docker_status()
    assert ok is False and "LLM_BENCH_NO_DOCKER" in why
