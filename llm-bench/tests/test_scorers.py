from __future__ import annotations

import json

import pytest
import yaml

from llm_bench import syslog
from llm_bench.scoring import firewall, run_scorer, score_html, score_needle
from llm_bench.scoring import auto
from llm_bench.scoring.tools import FakeTools, score_tool_facts
from llm_bench.textutil import code_blocks, extract_json, pick_block, strip_think


def fx(battery, name):
    return (battery.tasks_dir / "dryrun" / "good" / name).read_text()


# ---------------------------------------------------------------- text helpers


def test_code_block_extraction():
    text = "intro\n```python\nx = 1\n```\nmore\n```\nlonger block\nline 2\n```"
    assert code_blocks(text) == [("python", "x = 1\n"), ("", "longer block\nline 2\n")]
    assert pick_block(text, ("python",)) == "x = 1\n"
    assert pick_block("no fences here") == "no fences here\n"
    assert pick_block("```yaml\na: 1\n") == "a: 1\n"  # unterminated fence, output cut at the token limit


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": [1, 2]}\n```\nDone') == {"a": [1, 2]}
    assert extract_json('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json at all")


def test_strip_think():
    assert strip_think("<think>hmm</think>answer") == "answer"


# ---------------------------------------------------------------- keyword / regex / numeric


def test_alert_triage_keyword_groups(battery):
    cfg = battery.task(2).scorer
    good = auto.keyword_groups(fx(battery, "2__default.md"), cfg["groups"])
    assert good.score == 1.0 and good.passed and good.details["missed"] == []
    partial = auto.keyword_groups("The root cause is the aggregation switch sw-agg-1, probably a bad cable.", cfg["groups"])
    assert partial.score == 0.5 and set(partial.details["missed"]) == {"port", "victim"}
    wrong = auto.keyword_groups("It is the WAN latency spike and the DFS radar event.", cfg["groups"])
    assert wrong.score == 0.0


def test_keyword_groups_ignore_hidden_reasoning(battery):
    cfg = battery.task(2).scorer
    text = "<think>maybe sw-agg-1 port 7 pve2 DAC</think>I do not know."
    assert auto.keyword_groups(text, cfg["groups"]).score == 0.0


def test_regex_and_numeric():
    assert auto.regex_score("port 443 is open", r"\b443\b").score == 1.0
    assert auto.regex_score("port 80", r"\b443\b").score == 0.0
    assert auto.regex_score("nothing", r"forbidden", must_match=False).score == 1.0
    assert auto.numbers_in("18,423 errors, 1.5k drops, 7.25 ms") == [18423.0, 1500.0, 7.25]
    assert auto.numeric_tolerance("CRC count is 18,423.", 18423).score == 1.0
    assert auto.numeric_tolerance("CRC count is 18,400.", 18423).score == 0.0
    assert auto.numeric_tolerance("CRC count is 18,400.", 18423, tolerance=100).score == 1.0
    assert auto.numeric_tolerance("about 105 ms", 100, tolerance=0.1, relative=True).score == 1.0
    assert auto.numeric_tolerance("about 120 ms", 100, tolerance=0.1, relative=True).score == 0.0


# ---------------------------------------------------------------- structured output


@pytest.mark.parametrize("variant, name", [("incident", "5__incident.md"), ("inventory", "5__inventory.md"), ("changelog", "5__changelog.md")])
def test_structured_output_pass(battery, variant, name):
    v = next(v for v in battery.task(5).variants if v["id"] == variant)
    res = run_scorer(v["scorer"], fx(battery, name), battery=battery)
    assert res.score == 1.0, res.details


def test_structured_output_partial_and_fail(battery):
    v = next(v for v in battery.task(5).variants if v["id"] == "incident")
    obj = json.loads(fx(battery, "5__incident.md"))
    wrong_value = dict(obj, severity="high")
    res = run_scorer(v["scorer"], json.dumps(wrong_value), battery=battery)
    assert res.score == 0.5 and "severity" in res.details["wrong"][0]
    extra_prop = dict(obj, surprise=1)
    res = run_scorer(v["scorer"], json.dumps(extra_prop), battery=battery)
    assert res.score == 0.0 and res.details["stage"] == "schema"
    bad_enum = dict(obj, severity="catastrophic")
    assert run_scorer(v["scorer"], json.dumps(bad_enum), battery=battery).score == 0.0
    assert run_scorer(v["scorer"], "I think the severity is critical.", battery=battery).details["stage"] == "parse"
    fenced = "Here you go:\n```json\n" + json.dumps(obj) + "\n```"
    assert run_scorer(v["scorer"], fenced, battery=battery).score == 1.0


def test_structured_output_null_handling(battery):
    v = next(v for v in battery.task(5).variants if v["id"] == "inventory")
    obj = json.loads(fx(battery, "5__inventory.md"))
    obj["devices"][0]["poe_watts"] = 0  # should be null
    assert run_scorer(v["scorer"], json.dumps(obj), battery=battery).score == 0.5


# ---------------------------------------------------------------- firewall


def test_firewall_auto_checks_pass_on_reference(battery):
    res = firewall.score_firewall(fx(battery, "3__default.md"))
    assert res.score is None, "the task score comes from Mike's rubric"
    assert res.details["auto_passed"] == res.details["auto_total"] == 10, res.details["auto_checks"]


def _rules(battery):
    return yaml.safe_load(fx(battery, "3__default.md").split("```yaml\n")[1].split("```")[0])


def _as_text(rules):
    return "```yaml\n" + yaml.safe_dump(rules, sort_keys=False) + "```"


def test_firewall_detects_missing_iot_to_ha_exception(battery):
    rules = [r for r in _rules(battery) if r["name"] != "IoT to Home Assistant"]
    d = firewall.score_firewall(_as_text(rules)).details
    assert d["auto_checks"]["iot_to_home_assistant_8123"] is False
    assert d["auto_checks"]["order_exceptions_before_blocks"] is False
    assert d["auto_passed"] == 8


def test_firewall_detects_misordered_exception(battery):
    rules = _rules(battery)
    ha = next(r for r in rules if r["name"] == "IoT to Home Assistant")
    rules.remove(ha)
    rules.append(ha)  # allow after the blocks: never reached
    d = firewall.score_firewall(_as_text(rules)).details
    assert d["auto_checks"]["iot_to_home_assistant_8123"] is True
    assert d["auto_checks"]["order_exceptions_before_blocks"] is False


def test_firewall_detects_open_guest_and_wrong_ports(battery):
    # with the explicit block gone, only the catch-all default covers Guest; removing that too opens it up
    rules = [r for r in _rules(battery) if r["name"] not in ("block Guest to internal", "default block between VLANs")]
    for r in rules:
        if r["name"].startswith("Trusted to Servers"):
            r["ports"] = [80]
    d = firewall.score_firewall(_as_text(rules)).details["auto_checks"]
    assert d["guest_blocked_from_internal"] is False
    assert d["trusted_to_servers_22_443"] is False


def test_firewall_accepts_rfc1918_shorthand_and_alias_keys():
    text = """```yaml
rules:
  - {rule: established related, verdict: accept, src: any, dst: any}
  - {rule: guest isolation, verdict: drop, src: VLAN 30, dst: RFC1918}
```"""
    d = firewall.score_firewall(text).details["auto_checks"]
    assert d["established_related_allowed"] is True
    assert d["guest_blocked_from_internal"] is True


def test_firewall_unparsable_output():
    res = firewall.score_firewall("Block everything from IoT. Allow the rest.")
    assert res.score is None and res.details["parsed_rules"] == 0 and "no parsable" in res.note


# ---------------------------------------------------------------- html, needle


def test_html_checks(battery):
    topo = json.loads(battery.read("topology.json"))
    res = score_html(fx(battery, "9__default.md"), topo)
    assert res.score is None and res.details["hosts_referenced"] == "12/12"
    assert res.details["external_script"] is False and res.details["has_interaction"] is True
    ext = score_html('```html\n<html><script src="https://cdn.example/x.js"></script></html>\n```', topo)
    assert ext.details["external_script"] is True and ext.details["hosts_referenced"] == "0/12"
    assert score_html("I cannot do that.", topo).details["html_found"] is False


def test_needle_scoring():
    needle = {"answer": "INC-AAAA-1111", "decoys": ["INC-BBBB-2222"], "depth_pct": 40.0}
    assert score_needle("INCIDENT: INC-AAAA-1111", needle).score == 1.0
    assert score_needle("incident: inc-aaaa-1111", needle).score == 1.0
    assert score_needle("INCIDENT: INC-BBBB-2222", needle).score == 0.0
    assert score_needle("It is INC-AAAA-1111 (or maybe INC-BBBB-2222)", needle).score == 0.0, "hedging with a decoy fails"
    assert score_needle("no idea", needle).score == 0.0


def test_syslog_is_deterministic_with_one_needle():
    a = syslog.generate(3000, 99)
    b = syslog.generate(3000, 99)
    assert a.text == b.text and a.answer == b.answer
    assert syslog.generate(3000, 100).answer != a.answer
    lines = a.text.splitlines()
    errs = [ln for ln in lines if "backup-agent" in ln and ": ERROR:" in ln]
    assert errs == [lines[a.line_no - 1]] and a.answer in errs[0]
    assert all(d not in errs[0] for d in a.decoys)
    assert any("WARN" in ln and any(d in ln for d in a.decoys) for ln in lines) or len(lines) < 300
    assert 0.2 < a.depth_pct / 100 < 0.8
    big = syslog.generate(58000, 1337, 2.2)
    assert 0.9 * 58000 * 2.2 < big.chars < 1.1 * 58000 * 2.2


# ---------------------------------------------------------------- tool calling


def test_fake_tools_are_deterministic_and_reject_guesses():
    t = FakeTools()
    host = t.call("lookup_host", {"hostname": "backup02"})
    assert host["mac"] == "f4:92:bf:2a:10:5c"
    port = t.call("get_switch_port", {"mac": host["mac"]})
    assert (port["switch"], port["port"], port["uplink"]["remote_switch"], port["uplink"]["remote_port"]) == ("sw-edge-3", 9, "sw-agg-1", 12)
    stats = t.call("get_port_stats", {"switch": "sw-agg-1", "port": 12})
    assert stats["rx_crc_errors"] == 18423 and stats["speed_mbps"] == 100
    assert "error" in t.call("lookup_host", {"hostname": "made-up"})
    assert "error" in t.call("get_port_stats", {"switch": "sw-edge-3", "port": 99})
    assert "error" in t.call("get_port_stats", {"switch": "sw-edge-3"})  # missing argument
    assert "error" in t.call("delete_everything", {})
    assert len(t.calls) == 7


def test_tool_facts_scoring(battery):
    scenario = json.loads(battery.read("tool_calling/scenario.json"))
    facts, ref = scenario["facts"], scenario["reference_calls"]
    good = fx(battery, "6__final.md")
    assert score_tool_facts(good, facts, [], ref).score == 1.0
    two = "The problem is on sw-agg-1, port 12."
    r = score_tool_facts(two, facts, [], ref)
    assert r.score == 0.5 and set(r.details["facts_missed"]) == {"crc", "speed"}
    one = "It is on sw-agg-1."
    assert score_tool_facts(one, facts, [], ref).score == 0.0
    assert score_tool_facts("CRC errors: 18423, 100 Mbps half duplex, sw-agg-1 port 12", facts, [], ref).score == 1.0
    assert score_tool_facts("18,423 CRC errors on sw-agg-1 port 12 at 100 Mbps", facts, [], ref).score == 1.0
