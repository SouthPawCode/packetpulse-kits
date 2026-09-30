```python
from collections import deque


def sliding_max(values: list[int], k: int) -> list[int]:
    if k < 1:
        raise ValueError("k must be >= 1")
    if k > len(values):
        return []
    dq: deque[int] = deque()
    out = []
    for i, v in enumerate(values):
        while dq and values[dq[-1]] <= v:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - k:
            dq.popleft()
        if i >= k - 1:
            out.append(values[dq[0]])
    return out
```
