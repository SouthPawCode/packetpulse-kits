#!/usr/bin/env bash
# The Packet Pulse Proving Ground, one command: preflight -> run -> render.
#
#   ./run.sh [--config instance.yaml] [--out results/] [--assets assets/] [--tasks 0,1,2] [--force] [--dry-run]
#
# Works from a checkout of packetpulse-kits/llm-bench, and from a build directory that holds only
# instance.yaml and this script (it then installs the kit from the pinned git tag).
#   KIT_REF=v0.1.0    git tag to install when not run from a checkout
#   KIT_REPO=...      git URL override
#   PYTHON=python3    interpreter for the virtualenv (needs 3.11 or newer)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="instance.yaml"
OUT="results"
ASSETS="assets"
TASKS=""
FORCE=()
DRY=()

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
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
    KIT_REF="${KIT_REF:-v0.1.0}"
    KIT_REPO="${KIT_REPO:-https://github.com/SouthPawCode/packetpulse-kits}"
    "$PY" -m pip install --quiet "llm-bench @ git+${KIT_REPO}@${KIT_REF}#subdirectory=llm-bench"
  fi
fi

echo "run.sh: preflight" >&2
set +e
"$PY" -m llm_bench preflight --config "$CONFIG" "${FORCE[@]}" "${DRY[@]}" > "$OUT.preflight.json"
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
"$PY" -m llm_bench run "${RUN_ARGS[@]}" "${FORCE[@]}" "${DRY[@]}"

echo "run.sh: render" >&2
"$PY" -m llm_bench render --results "$OUT/results.json" --out "$ASSETS"

cat >&2 <<MSG

Done.
  results:  $HERE/$OUT/results.json
  outputs:  $HERE/$OUT/outputs/
  assets:   $HERE/$ASSETS/   (manifest.json lists every file)
  rubric:   $HERE/$OUT/rubric.yaml   fill it on camera, then:
            $PY -m llm_bench score --results $OUT/results.json --rubric $OUT/rubric.yaml
            $PY -m llm_bench render --results $OUT/results.json --out $ASSETS
MSG
