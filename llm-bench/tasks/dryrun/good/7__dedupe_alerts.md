```python
def dedupe_alerts(alerts: list[tuple[int, str]], window_s: int) -> list[tuple[int, str]]:
    last_kept: dict[str, int] = {}
    kept = []
    for ts, key in alerts:
        if key in last_kept and ts - last_kept[key] < window_s:
            continue
        last_kept[key] = ts
        kept.append((ts, key))
    return kept
```
