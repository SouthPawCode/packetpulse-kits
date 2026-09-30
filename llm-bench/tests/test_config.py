from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from llm_bench import battery as bat
from llm_bench.cli import main
from llm_bench.config import ConfigError, load_instance, parse_instance

KIT = Path(__file__).resolve().parent.parent
BASE = yaml.safe_load((KIT / "examples" / "instance.yaml").read_text())


def test_example_instances_load():
    inst = load_instance(KIT / "examples" / "instance.yaml")
    assert inst.baseline == "qwen3.8-27B-gsq-rco:64k"
    assert [m.backend for m in inst.models] == ["ollama", "ollama", "openai_compat"]
    assert inst.models[0].role == "baseline"
    assert inst.models[2].pricing == {"input_per_m": 0.28, "output_per_m": 0.42}
    assert inst.options["num_ctx_max"] == 65536 and inst.options["num_predict"] == 4096  # defaults filled
    assert inst.contexts == [512, 4096, 16384, 32768, 65536]
    load_instance(KIT / "examples" / "instance-dryrun.yaml")


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d["models"].append(copy.deepcopy(d["models"][0])), "duplicate"),
        (lambda d: d.update(baseline="nope"), "baseline"),
        (lambda d: d["models"][0].update(backend="lmstudio"), "backend"),
        (lambda d: d["models"][0].pop("endpoint"), "endpoint"),
        (lambda d: d["models"][2].pop("api_key_env"), "api_key_env"),
        (lambda d: d.update(contexts=[4096, 512]), "ascending"),
        (lambda d: d.pop("hardware"), "hardware"),
        (lambda d: d.update(kit="other-kit"), "kit"),
        (lambda d: d["models"][2]["pricing"].update(output_per_m=-1), "minimum"),
        (lambda d: d["options"].update(temperature=9), "temperature"),
    ],
)
def test_instance_rejects(mutate, needle):
    d = copy.deepcopy(BASE)
    mutate(d)
    with pytest.raises(ConfigError) as e:
        parse_instance(d)
    assert needle in str(e.value)


def test_battery_has_the_eleven_tasks(battery):
    assert [t.id for t in battery.tasks] == list(range(11))
    names = {t.id: t.name for t in battery.tasks}
    assert names[0] == "Specs & Load" and names[1] == "Speed Ladder" and names[10] == "Customer Verdict"
    assert all(t.pass_rule for t in battery.tasks), "every task states its pass rule"
    assert {t.id for t in battery.tasks if t.rubric == "required"} == {3, 9, 10}
    assert battery.task(10).weight == 0 and all(t.weight == 1 for t in battery.tasks if t.id != 10)


def test_battery_fixtures_all_exist(battery):
    assert bat.validate_battery(battery) == []
    for rel in ("topology.json", "alert_triage/bundle.txt", "firewall_policy/vlan_plan.md", "tool_calling/scenario.json", "code/problems.yaml", "customer_verdict/questions.yaml"):
        assert battery.path_of(rel).is_file(), rel


def test_prompts_render_with_fixtures(battery):
    p2 = battery.task_prompt(battery.task(2))
    assert "sw-agg-1" in p2 and "{{" not in p2
    p9 = battery.task_prompt(battery.task(9))
    assert "cam-garage" in p9 and '"hosts"' in p9
    v = battery.task(5).variants[0]
    p5 = battery.task_prompt({"prompt": None, "prompt_file": battery.task(5).prompt_file}, v["vars"])
    assert "INC-2041" in p5 and '"incident_id"' in p5


def test_battery_validation_catches_problems(battery, tmp_path):
    bad = yaml.safe_load(battery.path.read_text())
    bad["tasks"][2]["prompt_file"] = "alert_triage/missing.md"
    bad["tasks"][3]["scorer"]["type"] = "astrology"
    with pytest.raises(ConfigError):
        bat.parse_battery(bad, battery.path, battery.tasks_dir)
    bad["tasks"][3]["scorer"]["type"] = "firewall_rules"
    b = bat.parse_battery(bad, battery.path, battery.tasks_dir)
    errs = bat.validate_battery(b)
    assert any("missing.md" in e for e in errs)


def test_battery_subset_file_uses_kit_tasks(dryrun_instance, tmp_path):
    sub = tmp_path / "subset.yaml"
    sub.write_text(yaml.safe_dump({"battery_version": "0.1.0", "tasks": [{"id": 0, "name": "Specs & Load", "kind": "auto", "weight": 1.0, "runner": "specs", "scorer": {"type": "load_ok"}}]}))
    b = bat.load_battery(dryrun_instance, str(sub))
    assert [t.id for t in b.tasks] == [0] and b.tasks_dir.is_dir()


def test_select_unknown_task(battery):
    with pytest.raises(ConfigError):
        battery.select([99])


def test_validate_cli(capsys):
    assert main(["validate", "--config", str(KIT / "examples" / "instance.yaml")]) == 0
    assert "11 tasks" in capsys.readouterr().out


def test_validate_cli_rejects_bad_instance(tmp_path, capsys):
    f = tmp_path / "bad.yaml"
    f.write_text("kit: llm-bench\ntitle: x\n")
    assert main(["validate", "--config", str(f)]) == 2
    assert capsys.readouterr().err.startswith("llm-bench:")
    assert main(["validate", "--config", str(tmp_path / "missing.yaml")]) == 2


def test_missing_or_null_pricing_is_accepted_and_means_unknown_cost():
    for pricing in (None, {}, {"input_per_m": None, "output_per_m": None}, {"input_per_m": 0.3, "output_per_m": None}):
        d = copy.deepcopy(BASE)
        if pricing is None:
            d["models"][2].pop("pricing")
        else:
            d["models"][2]["pricing"] = pricing
        assert parse_instance(d).models[2].pricing is None
    d = copy.deepcopy(BASE)
    assert parse_instance(d).models[2].pricing == {"input_per_m": 0.28, "output_per_m": 0.42}
