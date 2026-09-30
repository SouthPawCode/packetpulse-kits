# network-bench (planned)

Status: **planned, not built yet.** This kit is reserved for the GL.iNet travel-router build.

What it will measure, on your own gear:

- Throughput with iperf3 (TCP and UDP, both directions, parallel streams).
- Latency and jitter under load (bufferbloat), idle vs saturated.
- WireGuard tunnel throughput and CPU cost compared with the same path unencrypted.

It will follow the same shape as [`llm-bench`](../llm-bench/README.md): an `instance.yaml`, a fixed battery
with written-out pass rules, a versioned `results.json`, and a `render` step that produces the same dark
scorecards and charts with the hardware line on every card. The spec lands with the build that needs it.
