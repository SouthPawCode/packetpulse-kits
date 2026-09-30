from __future__ import annotations

import os
from pathlib import Path

import pytest

from llm_bench import battery as battery_mod
from llm_bench.config import load_instance
from llm_bench.scoring import validators

KIT = Path(__file__).resolve().parent.parent
EXAMPLES = KIT / "examples"


@pytest.fixture(autouse=True)
def no_docker(monkeypatch):
    """Tests never call docker or the network; validators report 'unavailable' unless a test mocks them."""
    monkeypatch.setenv("LLM_BENCH_NO_DOCKER", "1")
    validators.reset_docker_cache()
    yield
    validators.reset_docker_cache()


@pytest.fixture
def dryrun_instance():
    return load_instance(EXAMPLES / "instance-dryrun.yaml")


@pytest.fixture
def battery(dryrun_instance):
    return battery_mod.load_battery(dryrun_instance)


@pytest.fixture(scope="session")
def dryrun_out(tmp_path_factory):
    """One full dry run shared by the end-to-end tests (read-only use)."""
    from llm_bench.cli import main

    os.environ["LLM_BENCH_NO_DOCKER"] = "1"
    out = tmp_path_factory.mktemp("dryrun")
    rc = main(["run", "--config", str(EXAMPLES / "instance-dryrun.yaml"), "--out", str(out)])
    assert rc == 0
    return out
