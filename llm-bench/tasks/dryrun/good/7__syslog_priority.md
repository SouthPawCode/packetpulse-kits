```python
import re

FACILITIES = {0: "kern", 1: "user", 2: "mail", 3: "daemon", 4: "auth", 5: "syslog"}
FACILITIES.update({16 + i: f"local{i}" for i in range(8)})
SEVERITIES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]


def syslog_priority(line: str) -> tuple[str, str]:
    m = re.match(r"<(\d+)>", line)
    if not m:
        raise ValueError("no <PRI> prefix")
    pri = int(m.group(1))
    facility, severity = divmod(pri, 8)
    if facility not in FACILITIES:
        raise ValueError("unknown facility")
    return FACILITIES[facility], SEVERITIES[severity]
```
