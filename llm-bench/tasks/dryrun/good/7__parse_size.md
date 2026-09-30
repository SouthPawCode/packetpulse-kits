```python
import re

UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*", text, re.IGNORECASE)
    if not m:
        raise ValueError("bad size")
    return int(float(m.group(1)) * UNITS[m.group(2).lower()])
```
