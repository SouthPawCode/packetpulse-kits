# llm-bench: the Packet Pulse Proving Ground

A fixed battery of eleven tasks for language models, run the same way for every model so the numbers can be
compared across videos and across hardware. It measures speed and memory on your box first, then the work an
operator actually hands a model: triage an alert bundle, write a firewall policy, emit configs that must
validate, return structured data, call tools, write code, and find a needle in a 58,000-token log. Scored
automatically wherever a machine can judge it; the rest is a rubric card scored on camera.

Two lanes share one battery and one scorecard:

- **16 GB** (local): models served by [Ollama](https://ollama.com) on your own GPU.
- **Cloud Edition**: flagship models through any OpenAI-compatible API, with cost per run on the card.

Reference hardware for the channel numbers: RTX 4070 Ti SUPER 16 GB, 64 GB RAM, Ollama. Yours will differ.

## Run it

```bash
./run.sh            # preflight, then run, then render; reads ./instance.yaml
./run.sh pre        # only the assets that need no results, into assets-pre/ (see "Before the run" below)
```

That is the whole loop. `run.sh` creates `.venv` if missing, installs the kit (`pip install -e .` from a
checkout, or from the pinned git tag when run from a build directory that holds only `instance.yaml` and
`run.sh`), renders the pre-run assets if `assets-pre/` is missing, checks the environment, runs every task for
every model, and renders the assets. Options:

```
./run.sh [--config instance.yaml] [--out results] [--assets assets] [--tasks 0,1,2] [--force] [--dry-run]
         [--pre-assets assets-pre] [--round N] [--question "TEXT"] [--thumbnail-text "TEXT"]
./run.sh pre [--config instance.yaml] [--pre-assets assets-pre] [--round N] [--question "TEXT"] [--thumbnail-text "TEXT"]
```

Needs Python 3.11 or newer. Docker is optional but task 4 is only scored with it (see below).

### The GPU is shared: read this before running on a shared box

If another model is resident in Ollama (a production triage model, an agent system), benchmark load adds
latency to it. `preflight` and `run` therefore **refuse to start while a model that is not in your instance is
loaded, exit code 3**, unless you pass `--force`. Schedule the run for a window (`window:` in the instance
file is where the plan is written down) and use `--force` only when you mean it. Each model is unloaded when
its turn is over, and the kit sends no alerts of its own.

### Try it with no GPU and no network

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m llm_bench run --config examples/instance-dryrun.yaml --out /tmp/pg-out
.venv/bin/python -m llm_bench render --results /tmp/pg-out/results.json --out /tmp/pg-assets
```

The `dryrun` backend replays fixture answers with fake timings. Results made this way carry
`backend: "dryrun"` and are for testing the pipeline, not for publishing. `--dry-run` on `run` and `preflight`
does the same for any instance file.

## Commands

```
python -m llm_bench preflight --config instance.yaml [--force] [--dry-run]
python -m llm_bench run       --config instance.yaml --out results/ [--tasks 0,1,2] [--force] [--dry-run]
python -m llm_bench score     --results results/results.json --rubric results/rubric.yaml
python -m llm_bench render    --results results/results.json --out assets/ [--thumbnail-text "TEXT"]
python -m llm_bench render --pre --config instance.yaml --out assets-pre/ [--question "TEXT"] [--round N] [--thumbnail-text "TEXT"]
                                                          (alias: python -m llm_bench pre-render ...)
python -m llm_bench validate  --config instance.yaml [--results results.json]
```

All commands exit 0 on success and non-zero with a one-line reason on stderr. Exit 3 means the GPU guard
refused. `--tasks` with an existing `results.json` merges the new rows into it.

| Command | What it does |
|---|---|
| `preflight` | Endpoints reachable; models present (Ollama `/api/tags`) or pullable when `pull: true`; VRAM/RAM against model size; no non-benchmark model loaded; docker available for validators. Prints a JSON report. |
| `run` | Runs every model in the instance over the selected tasks. Writes `results.json` after every row, so a crash keeps partial data; raw model output goes to `outputs/<model>/<task>.md`; progress is one line per task on stderr. Also writes `rubric.yaml` for the tasks you score. |
| `score` | Merges your rubric scores (tasks 3 and 9) and verdict answers (task 10) into `results.json` and recomputes the summary. |
| `render` | Writes the assets and `manifest.json` (below). With `--pre`, writes only the assets that need no results, from `--config` (see "Before the run"). |
| `validate` | Schema-checks the instance and the battery, and that every fixture exists. No network. |

## The battery

Every task records time to first token (TTFT, measured at the first visible content token), tokens per second
(from Ollama's `eval_count / eval_duration`, or from the stream for API models), prompt and output tokens,
total time, cost for priced API models, the raw output, and a score from 0 to 1. A task's score is the mean of
its variants, except the Speed Ladder, which is all-or-nothing. A task counts as a **pass** only at a score of
exactly 1. The scorecard total is the sum of task scores; tasks 0 to 9 weigh 1 each, so the maximum is 10.
Task 10 is not a test and weighs 0.

| # | Name | Kind | What it measures and how it is scored |
|---|---|---|---|
| 0 | **Specs & Load** | auto | Cold-loads the model, then records size on disk, VRAM after load (`nvidia-smi --query-gpu=memory.used`), the GPU/CPU split from Ollama (`size_vram` / `size` in `/api/ps`), and load time. **Pass:** 1 if the model loaded and answered a one-token request. The numbers are reported, not judged. |
| 1 | **Speed Ladder** | auto | Tokens per second and TTFT with prompts of 512, 4k, 16k, 32k and 64k tokens (the `contexts` list). Prompts are built from a fixed corpus, sized at about 4 characters per token and re-calibrated from the real `prompt_eval_count` when the server reports it; one result row per rung, `variant` is the rung. **Pass:** 1 only if every rung completed without an error. A rung that does not fit `num_ctx_max` is cut to fit and marked `clipped_to_fit_num_ctx_max`. |
| 2 | **Alert Triage** | auto (+ optional rubric) | A synthetic bundle of 39 UniFi and Proxmox/Ceph alerts with one planted root cause (a failing SFP+ DAC on `sw-agg-1` port 7) among its symptoms and unrelated noise. **Pass:** four case-insensitive keyword groups must all match in the answer: the device (`sw-agg-1`), the port (7), a physical-layer fault word (DAC, SFP, cable, transceiver, CRC, flapping and so on) and the affected host (`pve2`). Score = groups matched / 4. Text inside `<think>` tags is ignored. |
| 3 | **Firewall Policy** | rubric (+ auto checks) | Write the ordered inter-VLAN rules for a six-VLAN plan, as a YAML list. Ten automatic checks find required rules: return traffic allowed, Guest blocked from every internal network, Guest internet allowed, IoT blocked from Trusted, IoT to Home Assistant on TCP 8123 allowed, Cameras blocked from the internet, Trusted to Servers on 22 and 443, Servers blocked from Trusted, Trusted to Management on 443, and the exception-before-block ordering. **Pass:** your rubric score of 0, 0.5 or 1; the ten checks are printed on the rubric card as a hint and cannot award the score. |
| 4 | **Config That Validates** | auto | An nginx reverse-proxy config and a docker-compose file. nginx is checked with `docker run nginx:alpine nginx -t` (with a throwaway self-signed certificate mounted at the paths the prompt names), compose with `docker compose config`. **Pass, per file:** the tool must accept it and every required feature must be present (nginx: the 301 redirect, TLS 1.2/1.3 on 443, both certificate paths, the proxy target, the WebSocket location with Upgrade/Connection headers, Host and X-Forwarded headers, `client_max_body_size 50m`, gzip, HSTS; compose: a postgres service with a `pg_isready` healthcheck, a named volume, `depends_on` with `service_healthy`, restart policies, a published port, a user-defined network). Valid but incomplete scores 0.5, rejected by the tool scores 0. Task score = mean of the two. Comments do not count as directives. **Without docker the task is not scored**: the rows carry `score: null` and a "validator unavailable" note, and the matrix shows UNAVAILABLE. |
| 5 | **Structured Output** | auto | Three extractions into JSON Schemas (a nested incident report, an inventory with nulls, a strict changelog), validated with `jsonschema` (no extra properties allowed) and then spot-checked against known values. **Pass, per variant:** 1 if the output parses, validates and every checked value is right; 0.5 if it validates but a value is wrong; 0 otherwise. Fenced JSON and surrounding chatter are tolerated. |
| 6 | **Tool Calling** | auto | A tool loop over three fake tools (`lookup_host`, `get_switch_port`, `get_port_stats`) answering "backup02 is slow: which link is at fault?". Five dependent calls are needed and the answer hinges on data only the last call returns. The loop ends at the first reply with no tool call, or after 9 turns. **Pass:** the final answer must contain four facts: `sw-agg-1`, port 12, the CRC count 18,423 and the speed 100 Mbps. 1 = all four, 0.5 = at least two, 0 = fewer. Invented tool arguments come back as errors, never as data. |
| 7 | **Code** | auto | Ten small network-operator functions with hidden tests: CIDR membership, syslog priority, port ranges, MAC normalisation, next free IP, size parsing, sliding maximum (with a speed test), alert dedupe, cron fields, interval merge. Each test suite runs in a separate Python process with a 10 second timeout and memory limits. **Pass, per function:** all hidden assertions hold. Task score = functions passed / 10, so the matrix shows PASS only at 10 of 10. The subprocess is not a security boundary: run the kit in a container or VM with models you do not trust. |
| 8 | **Long Context** | auto | Find the one `backup-agent` ERROR line, and the incident ID in it, in a generated syslog of about 58,000 tokens. The log is produced from a fixed seed at run time (identical for every model, nothing large is committed) and contains lookalike decoy IDs at lower severity. **Pass:** 1 if the answer contains the exact planted ID and none of the decoys, else 0. If the server reports that the prompt did not fit, the log is shortened and retried (up to twice) and the attempts are recorded. |
| 9 | **Build Something** | rubric | One visible artifact for the video: a single-file web app that draws `tasks/topology.json` (a gateway, two switches, three VLANs, twelve hosts) as an interactive map. The HTML is saved as `outputs/<model>/9.html` to open in a browser. **Pass:** your rubric score of 0, 0.5 or 1 against the card printed in `rubric.yaml`; automatic hints (hosts referenced, external scripts, interaction) appear with it. |
| 10 | **Customer Verdict** | verdict | Not a test. Four answers, yes / no / maybe, filled on camera: latency-sensitive use, quality-sensitive use, a 16 GB shared box, and would-I-run-it, plus one sentence. They become the verdict card. Weight 0. |

The pass rule for each task also lives, next to its prompt, in [`battery.yaml`](battery.yaml), and the fixtures
(prompts, alert bundle, VLAN plan, schemas, tool scenario, code problems and their hidden tests, topology) are
under [`tasks/`](tasks/). If you think a score is wrong, the rule and the raw output
(`results/outputs/<model>/<task>.md`) are all you need to check it.

## instance.yaml

```yaml
kit: llm-bench
kit_version: "0.2.0"
battery: battery.yaml            # default: the battery shipped with the kit
title: "Ornith 1.5 at the Proving Ground"
hardware: "RTX 4070 Ti SUPER 16 GB · 64 GB RAM · Ollama"   # printed on every card
window: "2026-10-04T14:00:00-04:00"                          # when the run is planned
baseline: qwen3.8-27B-gsq-rco:64k
models:
  - {name: qwen3.8-27B-gsq-rco:64k, backend: ollama, endpoint: "http://127.0.0.1:11434", role: baseline}
  - {name: "hf.co/bartowski/Ornith-1.5-35B-A3B-GGUF:Q4_K_M", label: "Ornith 1.5 35B-A3B", backend: ollama, endpoint: "http://127.0.0.1:11434", pull: true}
  - name: deepseek-v4.1-flash
    label: "DeepSeek V4.1 Flash (API)"
    backend: openai_compat
    endpoint: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY                  # the key is read from this environment variable
    pricing: {input_per_m: 0.28, output_per_m: 0.42}   # USD per million tokens
contexts: [512, 4096, 16384, 32768, 65536]          # task 1 ladder
options: {temperature: 0.2, num_ctx_max: 65536, seed: 7}
```

See [`examples/instance.yaml`](examples/instance.yaml). Optional `options`: `num_ctx_default` (8192, the context
for ordinary tasks), `num_predict` (4096, the output cap), `think` (true/false for models that take it),
`request_timeout_s` (900). `pricing` may be absent or null: the cost is then unknown, `cost_usd` is `null` on
every row and the cards print "n/a", never "$0".

### How it talks to a model

- **`ollama`**: `POST /api/chat` with `stream: true`. TTFT is the time to the first non-empty content token
  (thinking tokens are counted separately in `details.ttft_first_token_any_ms`); tokens per second is
  `eval_count / eval_duration` from the final chunk; prompt tokens come from `prompt_eval_count`. The model is
  cold-loaded for task 0, re-loaded with a matching `num_ctx` when a task needs a different context (outside the
  timed request), and unloaded at the end. VRAM is `nvidia-smi` `memory.used` after load when the endpoint is
  this machine, else Ollama's `size_vram`. `pull: true` pulls through `/api/pull` when the model is missing.
- **`openai_compat`**: `POST {endpoint}/chat/completions` with streaming and usage; tokens per second is
  measured over the stream; cost comes from `pricing` and the reported usage.
- **`dryrun`**: fixtures and fake timings, offline.

## results.json

Written incrementally to `results/results.json` (schema in
[`llm_bench/results.schema.json`](llm_bench/results.schema.json), `schema_version: 1`).

```json
{
  "schema_version": 1, "title": "...", "hardware": "...", "started_at": "...", "finished_at": "...",
  "kit_version": "0.2.0", "battery_version": "0.1.0", "baseline": "<model name>",
  "models": [{"name": "...", "label": "...", "backend": "ollama|openai_compat", "role": "baseline|null",
              "size_gb": 12.1, "vram_after_load_mb": 12800, "gpu_share_pct": 100, "load_seconds": 4.2, "quant": "IQ3_S"}],
  "tasks":  [{"id": 1, "name": "Speed Ladder", "kind": "auto|rubric|mixed", "weight": 1.0, "aggregate": "all"}],
  "runs":   [{"model": "...", "task_id": 2, "variant": "default",
              "ttft_ms": 412, "tokens_per_second": 31.8, "prompt_tokens": 1830, "output_tokens": 412,
              "total_seconds": 13.4, "cost_usd": null,
              "score": 1.0, "score_kind": "auto", "pass": true, "error": null,
              "details": {"matched": ["..."]}, "output_file": "outputs/<model>/2.md"}],
  "summary": {"<model>": {"score_total": 8.5, "score_max": 10, "passes": 7, "avg_tps": 29.1, "avg_ttft_ms": 500,
                          "cost_usd_total": null, "unscored_task_ids": [3, 9]}},
  "verdicts": {"<model>": {"latency_sensitive": "yes|no|maybe", "quality_sensitive": "...",
                           "vram_constrained": "...", "would_i_run_it": "...", "note": "..."}}
}
```

Notes on reading it:

- `score` is `null` while a rubric task waits for you, or when a validator was unavailable; the reason is in
  `details.score_note`. Null scores are left out of `score_total` and listed in `summary.*.unscored_task_ids`.
  A backend error is a score of 0 with the reason in `error`.
- The Speed Ladder has one row per context size, with `variant` set to the rung in tokens.
- Multi-variant tasks (4, 5, 7) have one row per variant and share one `outputs/<model>/<task>.md`.
- `avg_tps` and `avg_ttft_ms` are plain means over every row that measured them, ladder rungs included.
- `baseline` and `models[].role` mark the comparison model, which the scorecard highlights.
- `backend` is `ollama` or `openai_compat` for real runs; `dryrun` marks test data.

### Scoring on camera

`run` writes `results/rubric.yaml`, with the criteria for tasks 3, 9 and 10 as comments:

```yaml
- model: "..."
  task_id: 3
  score: null          # 0 | 0.5 | 1
  note: ""
```

Task 10 has a `verdict:` block (`latency_sensitive`, `quality_sensitive`, `vram_constrained`,
`would_i_run_it`, each yes/no/maybe, and a `note`) instead of a score. Fill it in, then
`python -m llm_bench score --results results/results.json --rubric results/rubric.yaml`, then render again.
Re-running `run` keeps the scores you already entered.

## Before the run: `render --pre`

Assets that do not depend on benchmark results are rendered from `instance.yaml` alone, before the run, so they
exist when you sit down to record:

```bash
python -m llm_bench render --pre --config instance.yaml --out assets-pre/ \
    --round 1 --question "can a thirty-five-billion mixture-of-experts beat the twenty-seven-billion model I already run" \
    --thumbnail-text "Will it fit in 16 GB?"
# or:  ./run.sh pre --round 1 --question "..."        (alias for the command: python -m llm_bench pre-render ...)
```

It needs no `results.json`. Every file is 1920x1080 PNG and is listed, with its `role`, in
`assets-pre/manifest.json` (`"phase": "pre"`; the manifest also records the round and the question).

| File | Role | Content |
|---|---|---|
| `opening_vitals.png` | `opening_vitals` | The wordmark PACKET PULSE, a patient-monitor "vitals strip" under it (the hardware line split into segments: `RTX 4070 Ti SUPER · 16 GB VRAM · 64 GB RAM · Ollama`), and a flat line running into one ECG-style spike across the lower third. A static frame. |
| `round_card.png` | `round_card` | With `--round N`: `Proving Ground · Round N` over the instance title. Without: the instance title over `Packet Pulse`. |
| `title_card.png` | `title_card` | Episode title and hardware line (the same card as after the run). |
| `lower_third_mike.png` | `lower_third_mike` | Transparent overlay: `Mike` large, `Packet Pulse` small. `lower_third_mike_card.png` (`lower_third_mike_preview`) is the same plate on the dark card. |
| `lower_third_<model>.png` | `lower_third` | One per model in `models`: its `label` (else `name`), the parameter count and active parameters when the name carries them (`35B (3B active)`), the quantization when the name carries it (`Q4_K_M`), and local or API. Transparent. `lower_third_<model>_card.png` (`lower_third_preview`) is the preview. |
| `question_card.png` | `question_card` | `--question` set large and centred with the wordmark small (capitalised, closed with `?`); without `--question`, the placeholder "This week's question". |
| `end_card.png` | `end_card` | Wordmark, vitals strip, then `github.com/SouthPawCode/packetpulse-kits`, `packetpulse.dev` and `@packetpulsedev`. |
| `thumbnail_text.png` | `thumbnail_text` | `--thumbnail-text`, else the instance title, on the style card. |

The scorecard, charts, matrix and verdict cards need results and still come from `render --results` after the
run. Use separate output directories (`assets-pre/` and `assets/`). If both land in one directory, the post-run
render leaves the files `render --pre` wrote alone (the shared names `title_card.png`, `lower_third_<model>.png`
and `thumbnail_text.png` keep their pre-run versions) and the manifest lists both sets; pre-run entries carry
`"phase": "pre"`.

## Assets

`render --results` writes everything to `assets/` and lists every file, with its role, size and dimensions, in
`assets/manifest.json`. PNGs are 1920x1080; charts and cards that go into blog posts also come as SVG.

| File | Content |
|---|---|
| `scorecard.png/.svg` | One row per model: score total, passes, average tok/s, average TTFT, VRAM, cost. Baseline row highlighted. Pending tasks are starred. |
| `tps_chart.png/.svg` | Tokens per second per model per context rung (task 1), with axis scales. |
| `ttft_chart.png/.svg` | TTFT per model per rung, log scale when the range is wide. |
| `vram_chart.png/.svg` | VRAM after load per model with the 16 GB card line drawn, plus the GPU share. |
| `matrix.png/.svg` | Tasks by models: PASS, PARTIAL (with score), FAIL, PENDING, UNAVAILABLE. |
| `verdict_<model>.png` | The four-line customer verdict card per model. |
| `title_card.png` | Title and hardware line. |
| `lower_third_<model>.png` | Transparent 1920x1080 overlay with the model name, size and quant, for OBS. `lower_third_<model>_card.png` is the same plate on the dark card, for previewing. |
| `thumbnail_text.png` | `--thumbnail-text`, else `thumbnail_text` from `results.json`, else the title, on the style card. |

`<model>` is the model name reduced to lowercase letters, digits and hyphens. Every card carries the hardware
line from `results.json` bottom-left and the handle `@packetpulsedev` small in a corner. The look (dark card
`#0F1519`, teal `#0E6B67` and `#4FC2BA`, off-white text `#E3E9ED`, IBM Plex Sans when installed, else DejaVu
Sans) lives in one file, [`llm_bench/render/style.py`](llm_bench/render/style.py).

## Add a model

1. Add it to `models:` in `instance.yaml`. For Ollama, `name` is the tag Ollama knows (for a Hugging Face GGUF,
   `hf.co/<user>/<repo>:<quant>`), and `pull: true` fetches it if it is missing. For an API, use
   `backend: openai_compat` with `endpoint`, `api_key_env` and, for the cost column, `pricing`.
2. Keep the same `baseline` so every table has the same reference row. A `label` is what cards print.
3. `python -m llm_bench validate --config instance.yaml`, then `./run.sh`. Two local models on the full battery
   take roughly 40 minutes on the reference box.

To change the battery rather than the models, copy `battery.yaml`, edit it (task weights, `contexts`, prompt
files under `tasks/`), and point `battery:` at the copy; `validate` checks the fixtures. Bump `battery_version`
so results from different batteries are not compared by mistake.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest                      # offline: no network, no docker, no GPU
LLM_BENCH_TEST_DOCKER=1 .venv/bin/python -m pytest tests/test_docker_live.py   # real nginx -t and compose config
```

`LLM_BENCH_NO_DOCKER=1` makes the validators report "unavailable", which is how CI runs them.

## Layout

```
llm_bench/cli.py config.py battery.py runner.py results.py preflight.py syslog.py textutil.py
llm_bench/backends/   ollama.py openai_compat.py dryrun.py gpu.py base.py
llm_bench/scoring/    auto.py firewall.py validators.py tools.py rubric.py
llm_bench/render/     style.py assets.py pre.py
battery.yaml   tasks/   examples/   run.sh   tests/
```

MIT licensed, like the rest of [packetpulse-kits](../README.md).
