"""GPU memory via nvidia-smi."""

from __future__ import annotations

import shutil
import subprocess

QUERY = ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]
QUERY_TOTAL = ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"]


def _sum_mb(cmd: list[str]) -> int | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    total = 0
    seen = False
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            total += int(line)
            seen = True
    return total if seen else None


def memory_used_mb() -> int | None:
    """Sum of memory.used over all GPUs in MiB, or None without nvidia-smi."""
    return _sum_mb(QUERY)


def memory_total_mb() -> int | None:
    return _sum_mb(QUERY_TOTAL)
