"""Auto checks for task 3 (Firewall Policy): are the required rules present and in a workable order?

The model returns a YAML rule list. We normalize the fields and check ten
requirements derived from tasks/firewall_policy/vlan_plan.md. These checks feed
Mike's rubric card; they do not set the task score on their own.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from ..textutil import code_blocks, strip_think
from .auto import ScoreResult

INTERNAL = ("trusted", "iot", "guest", "servers", "cameras", "mgmt")

NET_RE = {
    "trusted": r"trusted|vlan[\s_-]*10\b|192\.168\.10\.|^10$",
    "iot": r"\biot\b|vlan[\s_-]*20\b|192\.168\.20\.|^20$",
    "guest": r"guest|vlan[\s_-]*30\b|192\.168\.30\.|^30$",
    "servers": r"server|\blab\b|vlan[\s_-]*40\b|192\.168\.40\.0|^40$",
    "cameras": r"camera|cctv|vlan[\s_-]*50\b|192\.168\.50\.|^50$",
    "mgmt": r"mgmt|manage|vlan[\s_-]*99\b|192\.168\.99\.|^99$",
    "internet": r"internet|\bwan\b|external|!\s*rfc1918|non[\s_-]*rfc1918|public",
    "ha": r"192\.168\.40\.30|home[\s_-]*assistant|\bha\b",
    "nvr": r"192\.168\.40\.20|\bnvr\b",
}
ALL_INTERNAL_RE = r"rfc1918|private|internal|all[\s_-]*vlans|192\.168\.0\.0/16|10\.0\.0\.0/8|all[\s_-]*local"
ANY_RE = r"^(any|\*|all|0\.0\.0\.0/0)$"
ALLOW = {"allow", "accept", "permit", "pass"}
BLOCK = {"block", "drop", "deny", "reject"}

KEYS = {
    "name": ("name", "rule", "description", "id", "comment"),
    "action": ("action", "verdict", "policy", "target"),
    "source": ("source", "src", "from", "source_network", "source_zone"),
    "destination": ("destination", "dst", "dest", "to", "destination_network", "destination_zone"),
    "ports": ("ports", "port", "dport", "destination_port", "destination_ports", "dst_port"),
    "protocol": ("protocol", "proto"),
}


def _flat(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        return " ".join(_flat(x) for x in v)
    if isinstance(v, dict):
        return " ".join(_flat(x) for x in v.values())
    return str(v).strip().lower()


def _ports(v: Any) -> set[int] | None:
    s = _flat(v)
    if not s or s in ("any", "all", "*"):
        return None
    out: set[int] = set()
    for a, b in re.findall(r"(\d+)\s*-\s*(\d+)", s):
        lo, hi = int(a), int(b)
        if hi - lo <= 2000:
            out.update(range(lo, hi + 1))
    out.update(int(x) for x in re.findall(r"\d+", re.sub(r"\d+\s*-\s*\d+", " ", s)))
    return out


def normalize_rule(raw: dict[str, Any]) -> dict[str, Any]:
    low = {str(k).lower(): v for k, v in raw.items()}

    def pick(field: str) -> Any:
        for k in KEYS[field]:
            if k in low:
                return low[k]
        return None

    action = _flat(pick("action"))
    if action in ALLOW:
        action = "allow"
    elif action in BLOCK:
        action = "block"
    return {
        "name": _flat(pick("name")),
        "action": action,
        "source": _flat(pick("source")),
        "destination": _flat(pick("destination")),
        "ports": _ports(pick("ports")),
        "all_text": _flat(list(raw.values())),
    }


def explicit_nets(field: str) -> set[str]:
    return {n for n, rx in NET_RE.items() if re.search(rx, field)}


def covered_nets(field: str) -> set[str]:
    nets = explicit_nets(field)
    if re.search(ALL_INTERNAL_RE, field):
        nets |= set(INTERNAL)
    if re.fullmatch(ANY_RE, field.strip()):
        nets |= set(INTERNAL) | {"internet"}
    return nets


def parse_rules(text: str) -> list[dict[str, Any]]:
    text = strip_think(text)
    cands = [c for lang, c in code_blocks(text) if lang in ("yaml", "yml", "")] + [text]
    for c in cands:
        try:
            obj = yaml.safe_load(c)
        except yaml.YAMLError:
            continue
        if isinstance(obj, dict):
            for key in ("rules", "firewall_rules", "policies", "firewall"):
                if isinstance(obj.get(key), list):
                    obj = obj[key]
                    break
        if isinstance(obj, list) and obj and all(isinstance(r, dict) for r in obj):
            return [normalize_rule(r) for r in obj]
    return []


def _first_index(rules: list[dict[str, Any]], pred) -> int | None:
    for i, r in enumerate(rules):
        if pred(r):
            return i
    return None


def run_checks(rules: list[dict[str, Any]]) -> dict[str, bool]:
    def allow_from_to(src: str, dst: str, ports: tuple[int, ...] = ()) -> bool:
        for r in rules:
            if r["action"] != "allow" or src not in explicit_nets(r["source"]) or dst not in explicit_nets(r["destination"]):
                continue
            if ports and (r["ports"] is not None) and not set(ports) <= r["ports"]:
                continue
            return True
        return False

    def block_union(src: str, need: set[str]) -> bool:
        got: set[str] = set()
        for r in rules:
            if r["action"] == "block" and src in covered_nets(r["source"]):
                got |= covered_nets(r["destination"])
        return need <= got

    checks: dict[str, bool] = {}
    est = _first_index(rules, lambda r: r["action"] == "allow" and re.search(r"established|related", r["all_text"]) is not None)
    first_block = _first_index(rules, lambda r: r["action"] == "block")
    checks["established_related_allowed"] = est is not None
    checks["guest_blocked_from_internal"] = block_union("guest", {"trusted", "iot", "servers", "cameras", "mgmt"})
    checks["guest_internet_allowed"] = any(
        r["action"] == "allow" and "guest" in explicit_nets(r["source"]) and ("internet" in explicit_nets(r["destination"]) or re.fullmatch(ANY_RE, r["destination"].strip()))
        for r in rules
    )
    checks["iot_blocked_from_trusted"] = block_union("iot", {"trusted"})
    checks["iot_to_home_assistant_8123"] = any(
        r["action"] == "allow" and "iot" in explicit_nets(r["source"]) and "ha" in explicit_nets(r["destination"]) and (r["ports"] is None or 8123 in r["ports"])
        for r in rules
    )
    checks["cameras_blocked_from_internet"] = block_union("cameras", {"internet"})
    checks["trusted_to_servers_22_443"] = allow_from_to("trusted", "servers", (22, 443)) or (
        allow_from_to("trusted", "servers", (22,)) and allow_from_to("trusted", "servers", (443,))
    )
    checks["servers_blocked_from_trusted"] = block_union("servers", {"trusted"})
    checks["trusted_to_mgmt_443"] = allow_from_to("trusted", "mgmt", (443,))
    ha_allow = _first_index(
        rules,
        lambda r: r["action"] == "allow" and "iot" in explicit_nets(r["source"]) and "ha" in explicit_nets(r["destination"]),
    )
    iot_block_servers = _first_index(
        rules, lambda r: r["action"] == "block" and "iot" in covered_nets(r["source"]) and "servers" in covered_nets(r["destination"])
    )
    order_ok = ha_allow is not None and (iot_block_servers is None or ha_allow < iot_block_servers)
    if est is not None and first_block is not None and est > first_block:
        order_ok = False
    checks["order_exceptions_before_blocks"] = bool(order_ok) and est is not None
    return checks


def score_firewall(text: str) -> ScoreResult:
    """Auto checks only; the task score comes from Mike's rubric (see README)."""
    rules = parse_rules(text)
    if not rules:
        return ScoreResult(None, {"auto_checks": {}, "auto_passed": 0, "auto_total": 10, "parsed_rules": 0}, note="no parsable YAML rule list in the output")
    checks = run_checks(rules)
    passed = sum(1 for v in checks.values() if v)
    return ScoreResult(
        None,
        {
            "auto_checks": checks,
            "auto_passed": passed,
            "auto_total": len(checks),
            "auto_score": round(passed / len(checks), 3),
            "parsed_rules": len(rules),
        },
        note=f"auto checks {passed}/{len(checks)}; waiting for rubric score",
    )
