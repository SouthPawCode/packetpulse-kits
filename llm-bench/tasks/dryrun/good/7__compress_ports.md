```python
def compress_ports(ports: list[int]) -> str:
    nums = sorted(set(ports))
    parts = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{nums[i]}-{nums[j]}")
        else:
            parts.extend(str(n) for n in nums[i : j + 1])
        i = j + 1
    return ",".join(parts)
```
