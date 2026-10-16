#!/usr/bin/env bash
# FrontierAgent -> DeepResearch Bench II reports -> external judge -> aggregate scores.

set -Eeuo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTIER_DIR="${WORKSPACE_DIR}/FrontierAgent"
EVALUATOR_DIR="${WORKSPACE_DIR}/drb2-evaluator"
GEN_ENV="${WORKSPACE_DIR}/nvidia-aiq/deploy/.env"
FRONTIER_ENV="${FRONTIER_ENV:-${FRONTIER_DIR}/.env}"
JUDGE_ENV="${EVALUATOR_DIR}/.env"
SOURCE_DATASET="${EVALUATOR_DIR}/tasks_and_rubrics.jsonl"
SANITIZED_DATASET="${FRONTIER_DIR}/benchmarks/public/datasets/deepresearch_bench_ii/standardized_data.jsonl"

# Preserve explicit caller values; sourcing dotenv files below must not erase
# command-line environment overrides with blank example fields.
CALLER_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
CALLER_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
CALLER_OPENAI_MODEL="${OPENAI_MODEL:-}"
CALLER_SERPER_API_KEY="${SERPER_API_KEY:-}"

RUN_NAME="${RUN_NAME:-frontier_drb2}"
PIPELINE="${PIPELINE:-agent_team_report}"
PROFILE="${PROFILE:-drb2_deepseek_v2}"
MODEL_NAME="${MODEL_NAME:-frontier-agent}"
GENERATION_CONCURRENCY="${GENERATION_CONCURRENCY:-1}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
JUDGE_CHUNK_SIZE="${JUDGE_CHUNK_SIZE:-50}"
LIMIT="${LIMIT:-}"
OFFSET="${OFFSET:-0}"
WEB="${WEB:-1}"

RUN_DIR="${WORKSPACE_DIR}/runs/${RUN_NAME}"
GENERATION_DIR="${RUN_DIR}/generation"
REPORTS_DIR="${RUN_DIR}/reports"
PENDING_REPORTS_DIR="${RUN_DIR}/judge_pending"
JUDGE_JSONL="${RUN_DIR}/judge_results.jsonl"
JUDGE_LOG="${RUN_DIR}/judge.log"
SCORE_PREFIX="${RUN_DIR}/scores"
SUMMARY_FILE="${RUN_DIR}/summary.txt"

ACTION="full"
ALLOW_PARTIAL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) ACTION="check" ;;
        --prepare-only) ACTION="prepare" ;;
        --generate-only) ACTION="generate" ;;
        --score-only) ACTION="score" ;;
        --allow-partial) ALLOW_PARTIAL=1 ;;
        -h|--help)
            echo "Usage: ./run_frontier_benchmark.sh [--check|--prepare-only|--generate-only|--score-only] [--allow-partial]"
            echo "See FRONTIER_AGENT_DRB2.md for environment overrides and examples."
            exit 0
            ;;
        *) echo "[error] Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

usage_notes() {
    : <<'EOF'
Usage: ./run_frontier_benchmark.sh [MODE] [--allow-partial]

Modes (default: full):
  --check          Validate files, credentials, dataset and runtime.
  --prepare-only   Generate the prompt-only agent dataset, then stop.
  --generate-only  Generate/resume reports and export Markdown, then stop.
  --score-only     Export existing reports, resume judging, aggregate scores.

Useful environment overrides:
  RUN_NAME=frontier_drb2       Stable name makes repeated commands resume.
  LIMIT=1 OFFSET=0             Smoke-test a slice (use --allow-partial).
  PIPELINE=agent_team_report     Default quality path: plan, research, report.
  PROFILE=drb2_deepseek_v2       Default profile for the quality path.
  PIPELINE=stateful-react-agent  Faster single-agent fallback.
  GENERATION_CONCURRENCY=1     Start at 1 to control Serper/model usage.
  JUDGE_WORKERS=2              Parallel DeepSeek judge workers.
EOF
}

if [[ ! "$RUN_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ || "$RUN_NAME" == "." || "$RUN_NAME" == ".." ]]; then
    echo "[error] RUN_NAME contains unsafe characters: ${RUN_NAME}" >&2
    exit 2
fi
if [[ ! "$MODEL_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "[error] MODEL_NAME contains unsafe characters: ${MODEL_NAME}" >&2
    exit 2
fi
if [[ "$PIPELINE" != "stateful-react-agent" && "$PIPELINE" != "agent_team_report" && "$PIPELINE" != "agent_team" ]]; then
    echo "[error] PIPELINE must be stateful-react-agent, agent_team_report, or agent_team" >&2
    exit 2
fi
for integer_pair in \
    "GENERATION_CONCURRENCY:${GENERATION_CONCURRENCY}" \
    "JUDGE_WORKERS:${JUDGE_WORKERS}" \
    "JUDGE_CHUNK_SIZE:${JUDGE_CHUNK_SIZE}"; do
    name="${integer_pair%%:*}"
    value="${integer_pair#*:}"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "[error] ${name} must be a positive integer; got ${value}" >&2
        exit 2
    fi
done
if [[ ! "$OFFSET" =~ ^[0-9]+$ ]]; then
    echo "[error] OFFSET must be a non-negative integer" >&2
    exit 2
fi
if [[ -n "$LIMIT" && ! "$LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    echo "[error] LIMIT must be a positive integer when set" >&2
    exit 2
fi
if [[ "$WEB" != "0" && "$WEB" != "1" ]]; then
    echo "[error] WEB must be 0 or 1" >&2
    exit 2
fi

require_file() {
    [[ -f "$1" ]] || { echo "[error] Missing required file: $1" >&2; exit 1; }
}
require_env() {
    [[ -n "${!1:-}" ]] || { echo "[error] Required setting $1 is missing" >&2; exit 1; }
}

for required in "$GEN_ENV" "$JUDGE_ENV" "$SOURCE_DATASET" \
    "${FRONTIER_DIR}/pyproject.toml" "${EVALUATOR_DIR}/pyproject.toml"; do
    require_file "$required"
done

UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
    USER_HOME_DIR="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6 || true)"
    if [[ -n "$USER_HOME_DIR" && -x "${USER_HOME_DIR}/.local/bin/uv" ]]; then
        UV_BIN="${USER_HOME_DIR}/.local/bin/uv"
    fi
fi
if [[ -z "$UV_BIN" ]]; then
    echo "[error] uv is required. Install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "[setup] Synchronizing Python environments..."
(cd "$FRONTIER_DIR" && "$UV_BIN" sync --python 3.12 --extra eval --extra document-readers --quiet)
(cd "$EVALUATOR_DIR" && "$UV_BIN" sync --python 3.12 --quiet)
FRONTIER_PY="${FRONTIER_DIR}/.venv/bin/python"
EVALUATOR_PY="${EVALUATOR_DIR}/.venv/bin/python"

echo "[setup] Building prompt-only dataset (rubrics and blocked URLs excluded)..."
"$FRONTIER_PY" "${FRONTIER_DIR}/benchmarks/public/scripts/prepare_deepresearch_bench_ii.py" \
    --source "$SOURCE_DATASET" \
    --out "$SANITIZED_DATASET" \
    --expected-count 132

set -a
# shellcheck disable=SC1090
source "$GEN_ENV"
# An optional FrontierAgent-local file is the preferred home for SERPER_API_KEY
# and can also override the mapped OpenAI-compatible generation settings.
if [[ -f "$FRONTIER_ENV" ]]; then
    # shellcheck disable=SC1090
    source "$FRONTIER_ENV"
fi
set +a

[[ -z "$CALLER_OPENAI_API_KEY" ]] || OPENAI_API_KEY="$CALLER_OPENAI_API_KEY"
[[ -z "$CALLER_OPENAI_BASE_URL" ]] || OPENAI_BASE_URL="$CALLER_OPENAI_BASE_URL"
[[ -z "$CALLER_OPENAI_MODEL" ]] || OPENAI_MODEL="$CALLER_OPENAI_MODEL"
[[ -z "$CALLER_SERPER_API_KEY" ]] || SERPER_API_KEY="$CALLER_SERPER_API_KEY"

export OPENAI_API_KEY="${OPENAI_API_KEY:-${DEEPSEEK_API_KEY:-}}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-${DEEPSEEK_BASE_URL:-}}"
export OPENAI_MODEL="${OPENAI_MODEL:-${DEEPSEEK_MODEL:-}}"
require_env OPENAI_API_KEY
require_env OPENAI_BASE_URL
require_env OPENAI_MODEL
if [[ "$WEB" == "1" && ( "$ACTION" == "full" || "$ACTION" == "generate" || "$ACTION" == "check" ) ]]; then
    require_env SERPER_API_KEY
    require_env JINA_API_KEY
    export HTTP_PROXY="${HTTP_PROXY:-${http_proxy:-http://127.0.0.1:7893}}"
    export HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy:-http://127.0.0.1:7893}}"
    export http_proxy="${http_proxy:-$HTTP_PROXY}"
    export https_proxy="${https_proxy:-$HTTPS_PROXY}"
    export NO_PROXY="${NO_PROXY:-${no_proxy:-modelfactory.lenovo.com,127.0.0.1,localhost}}"
    export no_proxy="${no_proxy:-$NO_PROXY}"
fi
export OPENAI_CONTEXT_WINDOW="${OPENAI_CONTEXT_WINDOW:-131072}"
export OPENAI_MAX_INPUT_TOKENS="${OPENAI_MAX_INPUT_TOKENS:-98304}"
export OPENAI_MAX_TOKENS="${OPENAI_MAX_TOKENS:-32768}"
export SANDBOX_BACKEND="${SANDBOX_BACKEND:-auto}"

if [[ "$ACTION" == "prepare" ]]; then
    echo "[done] Sanitized dataset ready: ${SANITIZED_DATASET}"
    exit 0
fi

if [[ "$ACTION" == "check" ]]; then
    (
        cd "$FRONTIER_DIR"
        "$FRONTIER_PY" -c 'from benchmarks.public.core.registry import load_questions; q=load_questions("deepresearch_bench_ii"); assert len(q)==132 and all(not x.ground_truth for x in q); print(f"[check] dataset registry: {len(q)} prompt-only tasks")'
        "$FRONTIER_PY" "benchmarks/public/runner/run_subprocess.py" --help >/dev/null
    )
    if [[ "$(uname -s)" == "Linux" && "$SANDBOX_BACKEND" == "auto" ]] && ! command -v bwrap >/dev/null; then
        echo "[warning] bwrap is not installed; generation will fail closed. Install bubblewrap or use an isolated container backend."
    fi
    echo "[check] OK: code, environments, credentials and 132-task dataset are ready."
    exit 0
fi

mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"

if [[ "$ACTION" == "full" || "$ACTION" == "generate" ]]; then
    echo "[1/4] Generating/resuming FrontierAgent reports..."
    generator_args=(
        --benchmark deepresearch_bench_ii
        --pipeline "$PIPELINE"
        --profile "$PROFILE"
        --concurrency "$GENERATION_CONCURRENCY"
        --offset "$OFFSET"
        --no-shuffle
        --out "$GENERATION_DIR"
    )
    if [[ "$WEB" == "1" ]]; then
        generator_args+=(--web)
    else
        generator_args+=(--no-web)
    fi
    if [[ -n "$LIMIT" ]]; then
        generator_args+=(--limit "$LIMIT")
    fi
    (
        cd "$FRONTIER_DIR"
        "$FRONTIER_PY" benchmarks/public/runner/run_subprocess.py "${generator_args[@]}"
    )
fi

if [[ "$ACTION" != "score" || ! -d "${REPORTS_DIR}/${MODEL_NAME}" ]]; then
    echo "[2/4] Exporting reports as idx-N.md..."
    export_args=(
        "$GENERATION_DIR"
        --out "$REPORTS_DIR"
        --model-name "$MODEL_NAME"
        --canonical "$SANITIZED_DATASET"
    )
    if [[ "$ALLOW_PARTIAL" -eq 1 || -n "$LIMIT" ]]; then
        export_args+=(--allow-partial)
    fi
    (
        cd "$FRONTIER_DIR"
        "$FRONTIER_PY" benchmarks/public/runner/export_deepresearch_bench_ii.py "${export_args[@]}"
    )
else
    echo "[2/4] Reusing existing reports under ${REPORTS_DIR}/${MODEL_NAME}"
fi

REPORT_COUNT="$(find "${REPORTS_DIR}/${MODEL_NAME}" -maxdepth 1 -type f -name 'idx-*.md' | wc -l | tr -d '[:space:]')"
if [[ "$ACTION" == "generate" ]]; then
    echo "[done] Exported ${REPORT_COUNT} report(s): ${REPORTS_DIR}/${MODEL_NAME}"
    exit 0
fi

echo "[3/4] Staging only reports without an existing judge verdict..."
(
    cd "$FRONTIER_DIR"
    "$FRONTIER_PY" benchmarks/public/runner/prepare_deepresearch_bench_ii_scoring.py \
        --reports "$REPORTS_DIR" \
        --judged "$JUDGE_JSONL" \
        --out "$PENDING_REPORTS_DIR"
)
PENDING_COUNT="$(find "$PENDING_REPORTS_DIR" -type f -name 'idx-*.md' | wc -l | tr -d '[:space:]')"

set -a
# shellcheck disable=SC1090
source "$JUDGE_ENV"
set +a
require_env OPENAI_API_KEY
require_env OPENAI_API_URL
require_env OPENAI_MODEL

if [[ "$PENDING_COUNT" -gt 0 ]]; then
    echo "[judge] Scoring ${PENDING_COUNT} pending report(s) with ${OPENAI_MODEL}..."
    (
        cd "$EVALUATOR_DIR"
        "$EVALUATOR_PY" run_evaluation.py \
            --pdf_dir "$PENDING_REPORTS_DIR" \
            --out_jsonl "$JUDGE_JSONL" \
            --tasks_jsonl "$SOURCE_DATASET" \
            --chunk_size "$JUDGE_CHUNK_SIZE" \
            --max_workers "$JUDGE_WORKERS" \
            --log_file "$JUDGE_LOG"
    )
else
    echo "[judge] No pending reports; reusing existing verdicts."
fi

# A second staging pass proves every exported report now has a valid verdict.
"$FRONTIER_PY" "${FRONTIER_DIR}/benchmarks/public/runner/prepare_deepresearch_bench_ii_scoring.py" \
    --reports "$REPORTS_DIR" \
    --judged "$JUDGE_JSONL" \
    --out "$PENDING_REPORTS_DIR" >/dev/null
REMAINING_COUNT="$(find "$PENDING_REPORTS_DIR" -type f -name 'idx-*.md' | wc -l | tr -d '[:space:]')"
if [[ "$REMAINING_COUNT" -ne 0 ]]; then
    echo "[error] ${REMAINING_COUNT} report(s) still lack judge verdicts" >&2
    exit 1
fi

echo "[4/4] Aggregating scores..."
(
    cd "$EVALUATOR_DIR"
    "$EVALUATOR_PY" aggregate_scores.py \
        --input "$JUDGE_JSONL" \
        --tasks-file "$SOURCE_DATASET" \
        --output-prefix "$SCORE_PREFIX"
)

score_value() {
    awk -F',' 'END { print $NF }' "$1" | tr -d '\r'
}

INFO_SCORE="$(score_value "${SCORE_PREFIX}_inforecall.csv")"
ANALYSIS_SCORE="$(score_value "${SCORE_PREFIX}_analysis.csv")"
PRESENTATION_SCORE="$(score_value "${SCORE_PREFIX}_presentation.csv")"
TOTAL_SCORE="$(score_value "${SCORE_PREFIX}_total.csv")"
BLOCKED_SCORE="$(score_value "${SCORE_PREFIX}_blocked.csv")"

{
    echo "DeepResearch Bench II FrontierAgent result"
    echo "run=${RUN_NAME}"
    echo "pipeline=${PIPELINE}"
    echo "profile=${PROFILE}"
    echo "reports=${REPORT_COUNT}"
    echo "information_recall=${INFO_SCORE}"
    echo "analysis=${ANALYSIS_SCORE}"
    echo "presentation=${PRESENTATION_SCORE}"
    echo "total=${TOTAL_SCORE}"
    echo "blocked_rate=${BLOCKED_SCORE}"
    echo "judge_model=${OPENAI_MODEL}"
    echo "note=Local DeepSeek-judge score; the official leaderboard uses its specified judge."
} | tee "$SUMMARY_FILE"

chmod -R go-rwx "$RUN_DIR"
echo "[done] Score ready: ${SUMMARY_FILE}"

# Keep the detailed help text discoverable without maintaining a duplicate file.
usage_notes >/dev/null
