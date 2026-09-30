#!/usr/bin/env bash
# The Packet Pulse Proving Ground, one command: (pre-run assets) -> preflight -> run -> render.
#
#   ./run.sh [--config instance.yaml] [--out results/] [--assets assets/] [--tasks 0,1,2] [--force] [--dry-run]
#            [--pre-assets assets-pre/] [--round N] [--question "TEXT"] [--thumbnail-text "TEXT"]
#   ./run.sh pre [--config instance.yaml] [--pre-assets assets-pre/] [--round N] [--question "TEXT"] [--thumbnail-text "TEXT"]
#
# `pre` only renders the assets that need no results (opening, round, title, lower thirds, question, end
# and thumbnail cards) into assets-pre/. A normal run renders them too when assets-pre/ is missing.
# Works from a checkout of packetpulse-kits/llm-bench, and from a build directory that holds only
# instance.yaml and this script (it then installs the kit from the pinned git tag).
#   KIT_REF=v0.2.0    git tag to install when not run from a checkout
#   KIT_REPO=...      git URL override
#   PYTHON=python3    interpreter for the virtualenv (needs 3.11 or newer)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="instance.yaml"
OUT="results"
ASSETS="assets"
PRE_ASSETS="assets-pre"
TASKS=""
FORCE=()
DRY=()
MODE="full"
PRE_ARGS=()

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

if [ "${1:-}" = "pre" ]; then MODE="pre"; shift; fi

while [ $# -gt 0 ]; do
  case "$1" in
    --pre-assets) PRE_ASSETS="$2"; shift 2 ;;
    --round)   PRE_ARGS+=(--round "$2"); shift 2 ;;
    --question) PRE_ARGS+=(--question "$2"); shift 2 ;;
    --thumbnail-text) PRE_ARGS+=(--thumbnail-text "$2"); shift 2 ;;
    --config)  CONFIG="$2"; shift 2 ;;
    --out)     OUT="$2"; shift 2 ;;
    --assets)  ASSETS="$2"; shift 2 ;;
    --tasks)   TASKS="$2"; shift 2 ;;
    --force)   FORCE=(--force); shift ;;
    --dry-run) DRY=(--dry-run); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "run.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
done

cd "$HERE"
[ -f "$CONFIG" ] || { echo "run.sh: $CONFIG not found in $HERE" >&2; exit 2; }

PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "run.sh: $PYTHON is older than 3.11 (set PYTHON=python3.12 or newer)" >&2; exit 2; }

if [ ! -d .venv ]; then
  echo "run.sh: creating .venv" >&2
  "$PYTHON" -m venv .venv
fi
PY="$HERE/.venv/bin/python"

if ! "$PY" -c 'import llm_bench' 2>/dev/null; then
  echo "run.sh: installing llm-bench" >&2
  "$PY" -m pip install --quiet --upgrade pip
  if [ -f "$HERE/pyproject.toml" ] && [ -d "$HERE/llm_bench" ]; then
    "$PY" -m pip install --quiet -e "$HERE"
  else
    KIT_REF="${KIT_REF:-v0.2.0}"
    KIT_REPO="${KIT_REPO:-https://github.com/SouthPawCode/packetpulse-kits}"
    "$PY" -m pip install --quiet "llm-bench @ git+${KIT_REPO}@${KIT_REF}#subdirectory=llm-bench"
  fi
fi

render_pre() {
  echo "run.sh: pre-run assets -> $PRE_ASSETS/" >&2
  "$PY" -m llm_bench render --pre --config "$CONFIG" --out "$PRE_ASSETS" ${PRE_ARGS[@]+"${PRE_ARGS[@]}"}
}

if [ "$MODE" = "pre" ]; then
  render_pre
  echo "run.sh: done. $PRE_ASSETS/ (manifest.json lists every file)" >&2
  exit 0
fi

# The assets that need no results are ready before the run; a failure here never blocks the benchmark.
if [ ! -f "$PRE_ASSETS/manifest.json" ]; then
  render_pre || echo "run.sh: warning: pre-run assets failed (continuing)" >&2
fi

echo "run.sh: preflight" >&2
set +e
"$PY" -m llm_bench preflight --config "$CONFIG" ${FORCE[@]+"${FORCE[@]}"} ${DRY[@]+"${DRY[@]}"} > "$OUT.preflight.json"
rc=$?
set -e
if [ $rc -ne 0 ]; then
  echo "run.sh: preflight failed (exit $rc); report in $OUT.preflight.json" >&2
  [ $rc -eq 3 ] && echo "run.sh: a non-benchmark model is loaded on the shared GPU; rerun in the planned window or pass --force" >&2
  exit $rc
fi

echo "run.sh: run" >&2
RUN_ARGS=(--config "$CONFIG" --out "$OUT")
[ -n "$TASKS" ] && RUN_ARGS+=(--tasks "$TASKS")
"$PY" -m llm_bench run "${RUN_ARGS[@]}" ${FORCE[@]+"${FORCE[@]}"} ${DRY[@]+"${DRY[@]}"}

echo "run.sh: render" >&2
"$PY" -m llm_bench render --results "$OUT/results.json" --out "$ASSETS"

cat >&2 <<MSG

Done.
  results:  $HERE/$OUT/results.json
  outputs:  $HERE/$OUT/outputs/
  assets:   $HERE/$ASSETS/   (manifest.json lists every file)
  pre-run:  $HERE/$PRE_ASSETS/   (rendered before the run, from instance.yaml alone)
  rubric:   $HERE/$OUT/rubric.yaml   fill it on camera, then:
            $PY -m llm_bench score --results $OUT/results.json --rubric $OUT/rubric.yaml
            $PY -m llm_bench render --results $OUT/results.json --out $ASSETS
MSG
