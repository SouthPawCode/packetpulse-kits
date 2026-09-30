```python
import ipaddress


def next_free_ip(cidr: str, used: list[str]) -> str | None:
    taken = set(used)
    for host in ipaddress.IPv4Network(cidr, strict=False).hosts():
        if str(host) not in taken:
            return str(host)
    return None
```
