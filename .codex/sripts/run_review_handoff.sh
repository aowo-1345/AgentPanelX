#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HANDOFF_ARG="${1:-}"

if [ -z "$HANDOFF_ARG" ]; then
  echo "Usage: .codex/sripts/run_review_handoff.sh <review-handoff-prompt.md>" >&2
  exit 1
fi

resolve_repo_path() {
  python3 - "$REPO_ROOT" "$1" <<'PY'
import os
import sys

repo_root = os.path.realpath(sys.argv[1])
path_arg = sys.argv[2]

candidate = path_arg
if not os.path.isabs(candidate):
    candidate = os.path.join(repo_root, candidate)
candidate = os.path.realpath(candidate)

try:
    common = os.path.commonpath([repo_root, candidate])
except ValueError:
    print("")
    raise SystemExit(0)

if common == repo_root:
    print(candidate)
else:
    print("")
PY
}

HANDOFF_PATH="$(resolve_repo_path "$HANDOFF_ARG")"
if [ -z "$HANDOFF_PATH" ] || [ ! -f "$HANDOFF_PATH" ]; then
  echo "[Review Handoff] handoff file does not exist or is outside the repo: $HANDOFF_ARG" >&2
  exit 1
fi

HANDOFF_REL="${HANDOFF_PATH#$REPO_ROOT/}"
HANDOFF_DIR_REL="$(dirname "$HANDOFF_REL")"
BASENAME="$(basename "$HANDOFF_PATH")"

RESULT_ROOT="$REPO_ROOT/review-results"
RESULT_SUBDIR="$RESULT_ROOT"
RESULT_REL_DIR="review-results"
LOG_SUBDIR="$RESULT_ROOT/logs"

if [[ "$HANDOFF_DIR_REL" == review-handoff-prompts/* ]]; then
  REL_SUFFIX="${HANDOFF_DIR_REL#review-handoff-prompts/}"
  if [ -n "$REL_SUFFIX" ] && [ "$REL_SUFFIX" != "." ]; then
    RESULT_SUBDIR="$RESULT_ROOT/$REL_SUFFIX"
    RESULT_REL_DIR="review-results/$REL_SUFFIX"
    LOG_SUBDIR="$LOG_SUBDIR/$REL_SUFFIX"
  fi
fi

RESULT_PATH="$RESULT_SUBDIR/$BASENAME"
RESULT_REL="$RESULT_REL_DIR/$BASENAME"
LOG_PATH="$LOG_SUBDIR/${BASENAME%.md}.jsonl"
TMP_RESULT_PATH="$RESULT_SUBDIR/.${BASENAME}.tmp.$$"
TMP_RESULT_REL="$RESULT_REL_DIR/.${BASENAME}.tmp.$$"

mkdir -p "$RESULT_SUBDIR" "$LOG_SUBDIR"
rm -f "$TMP_RESULT_PATH"
cleanup() {
  rm -f "$TMP_RESULT_PATH"
}
trap cleanup EXIT

CODEX_BIN="${CODEX_REVIEW_CODEX_BIN:-codex}"
CODEX_APPROVAL="${CODEX_REVIEW_APPROVAL:-never}"
CODEX_SANDBOX="${CODEX_REVIEW_SANDBOX:-workspace-write}"

if [ -z "${CODEX_HOME:-}" ]; then
  if [ -d "$HOME/.codex-krill" ]; then
    export CODEX_HOME="$HOME/.codex-krill"
  else
    export CODEX_HOME="$HOME/.codex"
  fi
fi

PROMPT=$(cat <<EOF
请读取仓库中的 handoff 文件并执行其中定义的 code review：

\`$HANDOFF_REL\`

执行要求：
- 按照 handoff 文件中的 review 要求执行。
- 不要修改任何代码或文档，唯一允许写入的文件是本 adapter 指定的临时 review 结果文件。
- 需要时可以阅读相关文件、查看 diff、运行只读检查命令。
- handoff 中约定的最终结果路径是：\`$RESULT_REL\`；本 adapter 会在成功后将临时文件移动到该路径。
- 本轮必须先把完整 review 结果写入临时文件：\`$TMP_RESULT_REL\`
- 写完结果文件后，再在终端里给出一句简短确认。

开始前先阅读全文，再开展 review。
EOF
)

echo "[Review Handoff] running Codex review for $HANDOFF_REL" >&2
echo "[Review Handoff] result: $RESULT_REL" >&2
echo "[Review Handoff] log: ${LOG_PATH#$REPO_ROOT/}" >&2

if ! (
  cd "$REPO_ROOT"
  "$CODEX_BIN" \
    -a "$CODEX_APPROVAL" \
    --sandbox "$CODEX_SANDBOX" \
    -C "$REPO_ROOT" \
    exec \
    --json \
    "$PROMPT"
) >"$LOG_PATH" 2>&1; then
  echo "[Review Handoff] Codex CLI failed." >&2
  tail -n 40 "$LOG_PATH" >&2 || true
  exit 1
fi

if [ ! -f "$TMP_RESULT_PATH" ]; then
  echo "[Review Handoff] Codex finished but did not write temp result file: $TMP_RESULT_REL" >&2
  tail -n 40 "$LOG_PATH" >&2 || true
  exit 1
fi

mv "$TMP_RESULT_PATH" "$RESULT_PATH"
trap - EXIT
echo "[Review Handoff] done. Result written to $RESULT_REL" >&2
