```python
import ipaddress


def cidr_contains(cidr: str, ip: str) -> bool:
    net = ipaddress.IPv4Network(cidr, strict=False)
    addr = ipaddress.IPv4Address(ip)
    return addr in net
```
