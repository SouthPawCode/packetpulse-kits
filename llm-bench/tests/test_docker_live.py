"""Real docker validators. Skipped unless LLM_BENCH_TEST_DOCKER=1 (needs docker and the nginx:alpine image, so it may touch the network)."""

from __future__ import annotations

import os

import pytest

from llm_bench.scoring import run_scorer, validators

pytestmark = pytest.mark.skipif(os.environ.get("LLM_BENCH_TEST_DOCKER") != "1", reason="set LLM_BENCH_TEST_DOCKER=1 to run against real docker")


@pytest.fixture(autouse=True)
def real_docker(monkeypatch):
    monkeypatch.delenv("LLM_BENCH_NO_DOCKER", raising=False)
    validators.reset_docker_cache()


def fx(battery, name):
    return (battery.tasks_dir / "dryrun" / "good" / name).read_text()


def variant(battery, vid):
    return next(v for v in battery.task(4).variants if v["id"] == vid)


def test_reference_nginx_passes_real_nginx_t(battery):
    res = run_scorer(variant(battery, "nginx")["scorer"], fx(battery, "4__nginx.md"), battery=battery)
    assert res.score == 1.0, res.details


def test_broken_nginx_fails_real_nginx_t(battery):
    bad = fx(battery, "4__nginx.md").replace("proxy_pass http://192.168.40.15:8080;", "proxy_passs http://192.168.40.15:8080;", 1)
    res = run_scorer(variant(battery, "nginx")["scorer"], bad, battery=battery)
    assert res.score == 0.0 and "unknown directive" in res.details["validator_output"]


def test_missing_cert_path_fails_real_nginx_t(battery):
    bad = fx(battery, "4__nginx.md").replace("/etc/nginx/certs/fullchain.pem", "/etc/ssl/nope.pem")
    assert run_scorer(variant(battery, "nginx")["scorer"], bad, battery=battery).score == 0.0


def test_reference_compose_passes_real_compose_config(battery):
    res = run_scorer(variant(battery, "compose")["scorer"], fx(battery, "4__compose.md"), battery=battery)
    assert res.score == 1.0, res.details


def test_broken_compose_fails_real_compose_config(battery):
    bad = "```yaml\nservices:\n  web:\n    image: x\n    depends_on: [ghost]\n```"
    res = run_scorer(variant(battery, "compose")["scorer"], bad, battery=battery)
    assert res.score == 0.0 and res.details["valid"] is False
