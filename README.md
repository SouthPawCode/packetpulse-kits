# Packet Pulse kits

The test harnesses behind [Packet Pulse](https://packetpulse.dev), the weekly
hands-on lab channel ([@packetpulsedev](https://www.youtube.com/@packetpulsedev)).
Every build on the channel runs one of these kits, and every kit is here so you
can run the same tests on your own hardware and compare.

## Kits

| Kit | What it measures | Status |
|---|---|---|
| `llm-bench/` | **The Packet Pulse Proving Ground**: a fixed battery for local and cloud language models. Speed and memory on your hardware, then operator tasks: alert triage, firewall policy, configs that must validate, structured output, tool calling, code, long context, and one visible build. Scored automatically where possible. | v0.2.0, awaiting its first live run |
| `network-bench/` | Throughput, latency under load, and VPN performance for routers and gateways. | planned |

Each kit has its own README with a one-line run command, the results schema,
and how the scorecards are rendered.

## Reference hardware

The channel's numbers come from one box: an RTX 4070 Ti SUPER (16 GB) over
OCuLink on a Minisforum AI X1 Pro-470 with 64 GB RAM, running Ollama. Your
numbers will differ; that is the point.

## License

MIT. Use them, fork them, tell us what you get.
