"""Task 6: three fake network tools, a deterministic five-step scenario, and the loop driver."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..backends.base import Backend, ChatResult, Tag
from ..textutil import strip_think
from .auto import ScoreResult, numbers_in
import re

HOSTS = {
    "nas01": {"hostname": "nas01", "mac": "f4:92:bf:2a:10:21", "ip": "192.168.40.21", "vlan": 40},
    "backup02": {"hostname": "backup02", "mac": "f4:92:bf:2a:10:5c", "ip": "192.168.40.22", "vlan": 40},
    "cam03": {"hostname": "cam03", "mac": "74:ac:b9:11:30:03", "ip": "192.168.50.13", "vlan": 50},
    "pve2": {"hostname": "pve2", "mac": "f4:92:bf:2a:10:b2", "ip": "192.168.40.12", "vlan": 40},
}

SWITCH_PORTS = {
    "f4:92:bf:2a:10:5c": {
        "mac": "f4:92:bf:2a:10:5c",
        "switch": "sw-edge-3",
        "port": 9,
        "uplink": {"local_port": 25, "remote_switch": "sw-agg-1", "remote_port": 12},
    },
    "f4:92:bf:2a:10:21": {
        "mac": "f4:92:bf:2a:10:21",
        "switch": "sw-edge-1",
        "port": 4,
        "uplink": {"local_port": 25, "remote_switch": "sw-agg-1", "remote_port": 10},
    },
    "74:ac:b9:11:30:03": {
        "mac": "74:ac:b9:11:30:03",
        "switch": "sw-edge-2",
        "port": 17,
        "uplink": {"local_port": 25, "remote_switch": "sw-agg-1", "remote_port": 11},
    },
    "f4:92:bf:2a:10:b2": {
        "mac": "f4:92:bf:2a:10:b2",
        "switch": "sw-agg-1",
        "port": 7,
        "uplink": None,
    },
}

PORT_STATS = {
    ("sw-edge-3", 9): {"link": "up", "speed_mbps": 1000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 14},
    ("sw-edge-3", 25): {"link": "up", "speed_mbps": 10000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 31},
    ("sw-agg-1", 12): {"link": "up", "speed_mbps": 100, "duplex": "half", "rx_crc_errors": 18423, "tx_drops": 5210, "util_pct": 93},
    ("sw-agg-1", 10): {"link": "up", "speed_mbps": 10000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 22},
    ("sw-agg-1", 11): {"link": "up", "speed_mbps": 10000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 9},
    ("sw-agg-1", 7): {"link": "up", "speed_mbps": 10000, "duplex": "full", "rx_crc_errors": 2, "tx_drops": 0, "util_pct": 40},
    ("sw-edge-1", 4): {"link": "up", "speed_mbps": 1000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 6},
    ("sw-edge-1", 25): {"link": "up", "speed_mbps": 10000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 12},
    ("sw-edge-2", 17): {"link": "up", "speed_mbps": 1000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 3},
    ("sw-edge-2", 25): {"link": "up", "speed_mbps": 10000, "duplex": "full", "rx_crc_errors": 0, "tx_drops": 0, "util_pct": 5},
}


class FakeTools:
    """Deterministic tool implementations. Unknown inputs return an error object, never a guess."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"name": name, "arguments": arguments})
        try:
            if name == "lookup_host":
                host = str(arguments["hostname"]).strip().lower()
                return HOSTS.get(host) or {"error": f"unknown host {host!r}"}
            if name == "get_switch_port":
                mac = str(arguments["mac"]).strip().lower()
                return SWITCH_PORTS.get(mac) or {"error": f"MAC {mac} not found in any forwarding table"}
            if name == "get_port_stats":
                key = (str(arguments["switch"]).strip().lower(), int(arguments["port"]))
                return PORT_STATS.get(key) or {"error": f"no such port {key[0]}/{key[1]}"}
        except (KeyError, ValueError, TypeError) as e:
            return {"error": f"bad arguments for {name}: {e}"}
        return {"error": f"unknown tool {name!r}"}


@dataclass
class LoopResult:
    final_text: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    error: str | None = None
    ttft_ms: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_second: float | None = None
    total_seconds: float = 0.0
    cost_usd: float | None = None
    used_all_turns: bool = False


def _required(tool_defs: list[dict[str, Any]], name: str) -> list[str]:
    for t in tool_defs:
        if t["function"]["name"] == name:
            return t["function"]["parameters"].get("required", [])
    return []


def run_tool_loop(backend: Backend, scenario: dict[str, Any], *, num_ctx: int | None, num_predict: int | None, task_id: int = 6) -> LoopResult:
    tools = scenario["tools"]
    names = {t["function"]["name"] for t in tools}
    fake = FakeTools()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": scenario["system"]},
        {"role": "user", "content": scenario["question"]},
    ]
    lr = LoopResult()
    gen_seconds = 0.0
    out_tokens = 0
    prompt_tokens = 0
    have_prompt = have_out = False
    cost = 0.0
    have_cost = False
    max_turns = int(scenario.get("max_turns", 8))
    for turn in range(max_turns):
        res: ChatResult = backend.chat(messages, tools=tools, num_ctx=num_ctx, num_predict=num_predict, tag=Tag(task_id, "default", turn))
        lr.turns += 1
        lr.total_seconds += res.total_seconds
        if turn == 0:
            lr.ttft_ms = res.ttft_ms
        if res.prompt_tokens is not None:
            prompt_tokens += res.prompt_tokens
            have_prompt = True
        if res.output_tokens is not None:
            out_tokens += res.output_tokens
            have_out = True
            if res.tokens_per_second:
                gen_seconds += res.output_tokens / res.tokens_per_second
        if res.cost_usd is not None:
            cost += res.cost_usd
            have_cost = True
        if res.error:
            lr.error = res.error
            break
        if not res.tool_calls:
            lr.final_text = strip_think(res.text)
            break
        messages.append(backend.assistant_message(res))
        for call in res.tool_calls:
            valid = call["name"] in names and all(k in call["arguments"] for k in _required(tools, call["name"]))
            result = fake.call(call["name"], call["arguments"]) if call["name"] in names else {"error": f"unknown tool {call['name']!r}"}
            lr.trace.append({"turn": turn, "name": call["name"], "arguments": call["arguments"], "valid": valid, "result_error": "error" in result})
            messages.append(backend.tool_message(call, json.dumps(result)))
    else:
        lr.used_all_turns = True
        lr.error = f"no final answer after {max_turns} turns"
    lr.prompt_tokens = prompt_tokens if have_prompt else None
    lr.output_tokens = out_tokens if have_out else None
    lr.tokens_per_second = round(out_tokens / gen_seconds, 2) if gen_seconds > 0 else None
    lr.cost_usd = round(cost, 6) if have_cost else None
    return lr


def score_tool_facts(final_text: str, facts: list[dict[str, Any]], trace: list[dict[str, Any]], reference: list[dict[str, Any]]) -> ScoreResult:
    """Score the final answer against the known facts.

    1.0 every fact present; 0.5 at least half; 0 fewer. Facts can only be known by
    making the tool calls, so a correct answer proves the chain was followed.
    """
    text = strip_think(final_text)
    got: list[str] = []
    missed: list[str] = []
    for f in facts:
        if "numeric" in f:
            nums = numbers_in(text)
            hit = any(abs(n - f["numeric"]) <= f.get("tolerance", 0) for n in nums)
        else:
            hit = any(re.search(p, text, re.IGNORECASE) for p in f["any"])
        (got if hit else missed).append(f["id"])
    frac = len(got) / len(facts) if facts else 0.0
    score = 1.0 if not missed else (0.5 if frac >= 0.5 else 0.0)
    ref_names = [c["name"] for c in reference]
    made = [t["name"] for t in trace]
    return ScoreResult(
        score,
        {
            "facts_matched": got,
            "facts_missed": missed,
            "tool_calls_made": len(trace),
            "tool_calls_expected": len(reference),
            "invalid_calls": sum(1 for t in trace if not t["valid"]),
            "followed_reference_order": made[: len(ref_names)] == ref_names,
            "trace": [{"name": t["name"], "arguments": t["arguments"]} for t in trace],
        },
    )
