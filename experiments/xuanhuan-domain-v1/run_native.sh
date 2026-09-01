#!/usr/bin/env bash
# Native-only executor for xuanhuan-domain-v1.
# Does not construct prompts, split windows, call the model API, validate
# citations, merge candidates, or rewrite SceneCandidates.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"

MODEL="${SCOUT_MODEL:-gpt-4.1}"
SPEC_DIR="${ROOT}/experiments/xuanhuan-domain-v1"
RUNTIME="${ROOT}/.runtime/xuanhuan-domain-v1"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "FAIL: OPENAI_API_KEY is not set; native OpenAIResponsesClient cannot run." >&2
  exit 2
fi

run_one() {
  local name="$1"
  local spec="${SPEC_DIR}/experiment-${name}.json"
  local work="${RUNTIME}/${name}"
  echo "=== research-novel ${name} model=${MODEL} ==="
  xhnovel-pipeline research-novel "${spec}" --scout-model "${MODEL}" --work-dir "${work}"
  local catalog
  catalog="$(find "${work}/research" -name catalog.json -print | sort | tail -n 1)"
  if [[ -z "${catalog}" ]]; then
    echo "FAIL: no catalog.json under ${work}/research" >&2
    exit 1
  fi
  local store="${work}/ingestion/objects"
  echo "=== validate all ${name} ==="
  xhnovel-pipeline validate all "${catalog}" --store "${store}"
  xhnovel-pipeline validate scene "${catalog}" --store "${store}"
  xhnovel-pipeline validate evidence "${catalog}" --store "${store}"
  xhnovel-pipeline validate export "${catalog}" --store "${store}"
  echo "PASS ${name} catalog=${catalog}"
}

run_one A1
run_one B
run_one A2

python3 "${SPEC_DIR}/evaluate_xuanhuan_domain.py" --root "${ROOT}" --runtime "${RUNTIME}"
