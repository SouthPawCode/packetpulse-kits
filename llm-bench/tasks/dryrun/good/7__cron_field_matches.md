```python
def cron_field_matches(field: str, value: int) -> bool:
    if not field:
        raise ValueError("empty field")
    matched = False
    for part in field.split(","):
        if not part:
            raise ValueError("empty list item")
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise ValueError("bad step")
            step = int(s)
        if part == "*":
            lo, hi = 0, None
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ValueError("bad range")
            lo, hi = int(a), int(b)
        elif part.isdigit():
            lo = hi = int(part)
        else:
            raise ValueError("bad field")
        if value >= lo and (hi is None or value <= hi) and (value - lo) % step == 0:
            matched = True
    return matched
```
