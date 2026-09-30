"""Deterministic synthetic syslog with one planted needle, for task 8 (Long Context).

The log is generated from a seed at run time, so the repo carries a generator
instead of a 300 KB fixture. The same seed and size always give byte-identical
output, so every model sees the same haystack.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

HOSTS = ["pve1", "pve2", "pve3", "ai470", "nas01", "udm-pro"]
ALNUM = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass
class Haystack:
    text: str
    answer: str  # the planted incident ID
    decoys: list[str] = field(default_factory=list)
    depth_pct: float = 0.0
    line_no: int = 0
    lines: int = 0
    chars: int = 0
    est_tokens: int = 0
    disk: str = "vm-104-disk-0"


def _incident(rng: random.Random) -> str:
    return "INC-" + "".join(rng.choice(ALNUM) for _ in range(4)) + "-" + "".join(rng.choice("0123456789") for _ in range(4))


def _line(rng: random.Random, ts: datetime, pid_pool: dict[str, int], decoy_ids: list[str]) -> str:
    host = rng.choice(HOSTS)
    stamp = ts.strftime("%b %d %H:%M:%S")
    r = rng.random()
    ip = f"192.168.{rng.choice([10, 20, 40, 50])}.{rng.randint(2, 250)}"
    if r < 0.10:
        return f"{stamp} {host} sshd[{rng.randint(1000, 60000)}]: Accepted publickey for ops from {ip} port {rng.randint(20000, 65000)} ssh2: ED25519 SHA256:{''.join(rng.choice(ALNUM) for _ in range(12))}"
    if r < 0.18:
        return f"{stamp} {host} CRON[{rng.randint(1000, 60000)}]: (root) CMD (/usr/local/bin/{rng.choice(['rotate-logs', 'zfs-snap', 'prune-tmp', 'ceph-health'])}.sh >/dev/null 2>&1)"
    if r < 0.26:
        return f"{stamp} {host} systemd[1]: {rng.choice(['Started', 'Finished', 'Starting'])} {rng.choice(['Daily apt download activities', 'Rotate log files', 'Proxmox VE replication runner', 'Ceph object storage daemon osd.' + str(rng.randint(0, 11))])}."
    if r < 0.36:
        return f"{stamp} {host} kernel: [{rng.randint(10000, 9999999)}.{rng.randint(100000, 999999)}] vmbr0: port {rng.randint(1, 9)}(tap{rng.randint(100, 120)}i0) entered {rng.choice(['forwarding', 'disabled', 'blocking'])} state"
    if r < 0.44:
        return f"{stamp} {host} pvedaemon[{rng.randint(1000, 9999)}]: <root@pam> starting task UPID:{host}:{rng.randint(0, 0xFFFFFF):06X}:{rng.randint(0, 0xFFFFFFF):08X}:{rng.randint(0, 0xFFFFFFF):08X}:{rng.choice(['qmstart', 'vzdump', 'qmstop'])}:{rng.randint(100, 120)}:root@pam:"
    if r < 0.52:
        return f"{stamp} {host} ceph-osd[{rng.randint(1000, 9999)}]: osd.{rng.randint(0, 11)} pg {rng.randint(1, 9)}.{rng.randint(0, 255):x} deep-scrub ok, {rng.randint(100, 9000)} objects, {rng.randint(1, 900)} MiB"
    if r < 0.60:
        return f"{stamp} {host} nginx[{rng.randint(1000, 9999)}]: {ip} - - \"GET /{rng.choice(['api/health', 'dashboard', 'static/app.js', 'api/v1/devices'])} HTTP/1.1\" {rng.choice([200, 200, 200, 304, 404])} {rng.randint(120, 50000)}"
    if r < 0.66:
        return f"{stamp} {host} dhcpd[{rng.randint(1000, 9999)}]: DHCPACK on {ip} to {':'.join(f'{rng.randint(0, 255):02x}' for _ in range(6))} via vlan{rng.choice([10, 20, 40])}"
    if r < 0.72:
        return f"{stamp} {host} backup-agent[2210]: INFO: snapshot complete id=SNAP-{rng.randint(10000, 99999)} vm-{rng.randint(100, 120)}-disk-0 in {rng.randint(2, 90)}s"
    if r < 0.74:
        return f"{stamp} {host} backup-agent[2210]: WARN: retrying upload (attempt {rng.randint(2, 4)}/5) ref=R-{rng.randint(10000, 99999)} incident={decoy_ids[rng.randrange(len(decoy_ids))]}"
    if r < 0.80:
        return f"{stamp} {host} sshd[{rng.randint(1000, 60000)}]: error: maximum authentication attempts exceeded for invalid user {rng.choice(['admin', 'test', 'oracle', 'pi'])} from {ip} port {rng.randint(20000, 65000)} ssh2 [preauth]"
    if r < 0.85:
        return f"{stamp} {host} kernel: [{rng.randint(10000, 9999999)}.{rng.randint(100000, 999999)}] EXT4-fs error (device sdc1): ext4_find_entry:1455: inode #{rng.randint(1000, 999999)}: comm {rng.choice(['updatedb', 'find', 'rsync'])}: reading directory lblock 0"
    if r < 0.89:
        return f"{stamp} {host} nginx[{rng.randint(1000, 9999)}]: [error] {rng.randint(1000, 9999)}#0: *{rng.randint(1, 99999)} connect() failed (111: Connection refused) while connecting to upstream, client: {ip}, upstream: \"http://192.168.40.{rng.randint(10, 60)}:8080/\""
    if r < 0.93:
        return f"{stamp} {host} corosync[{rng.randint(1000, 9999)}]: [KNET  ] link: host: {rng.randint(1, 3)} link: 0 is up, latency {rng.randint(150, 900)}us"
    if r < 0.96:
        return f"{stamp} {host} smartd[{rng.randint(500, 900)}]: Device: /dev/sd{rng.choice('abc')}, SMART Prefailure Attribute: 1 Raw_Read_Error_Rate changed from {rng.randint(90, 120)} to {rng.randint(90, 120)}"
    return f"{stamp} {host} pve-ha-lrm[{rng.randint(500, 900)}]: status change {rng.choice(['wait_for_agent_lock', 'active'])} => {rng.choice(['active', 'wait_for_agent_lock'])}"


def generate(target_tokens: int, seed: int, chars_per_token: float = 3.0) -> Haystack:
    """Build a log of about target_tokens (estimated at chars_per_token) with one planted ERROR line."""
    rng = random.Random(seed)
    decoy_ids = [_incident(rng) for _ in range(6)]
    answer = _incident(rng)
    while answer in decoy_ids:
        answer = _incident(rng)
    depth = round(0.25 + rng.random() * 0.5, 3)  # somewhere in the middle half
    budget = int(target_tokens * chars_per_token)
    ts = datetime(2026, 9, 12, 0, 0, 0)
    pid_pool: dict[str, int] = {}
    lines: list[str] = []
    stamps: list[datetime] = []
    size = 0
    while size < budget:
        ts += timedelta(seconds=rng.randint(0, 3))
        ln = _line(rng, ts, pid_pool, decoy_ids)
        lines.append(ln)
        stamps.append(ts)
        size += len(ln) + 1
    pos = int(len(lines) * depth)
    ts_needle = stamps[max(pos - 1, 0)]
    needle = (
        f"{ts_needle.strftime('%b %d %H:%M:%S')} pve2 backup-agent[2210]: ERROR: verify failed for vm-104-disk-0 "
        f"(checksum mismatch at chunk {rng.randint(10000, 99999)}), incident={answer}"
    )
    lines.insert(pos, needle)
    text = "\n".join(lines)
    return Haystack(
        text=text,
        answer=answer,
        decoys=decoy_ids,
        depth_pct=round(depth * 100, 1),
        line_no=pos + 1,
        lines=len(lines),
        chars=len(text),
        est_tokens=int(len(text) / chars_per_token),
    )
