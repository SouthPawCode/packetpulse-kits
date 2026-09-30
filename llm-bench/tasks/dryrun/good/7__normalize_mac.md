```python
import re


def normalize_mac(text: str) -> str:
    digits = re.sub(r"[:\-.]", "", text.strip())
    if not re.fullmatch(r"[0-9a-fA-F]{12}", digits):
        raise ValueError("not a MAC address")
    digits = digits.lower()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))
```
