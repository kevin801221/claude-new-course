#!/usr/bin/env bash
# ============================================================
# Hook: auto-format-py
# 事件: PostToolUse
# matcher: "Edit|Write|MultiEdit"
#
# 這是做什麼用的
# ------------------------------------------------------------
# Claude 改完 .py 檔之後，自動跑 `ruff format` 把格式弄整齊。
# 不存在 ruff 或不是 .py 都會安靜跳過 — 不會壞事。
#
# 怎麼觸發
# ------------------------------------------------------------
# Claude 用 Edit / Write / MultiEdit 改完檔之後跑。
# 從 stdin 拿到 JSON，內含 .tool_input.file_path。
#
# 怎麼開啟（加進 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "PostToolUse": [{
#     "matcher": "Edit|Write|MultiEdit",
#     "hooks": [{
#       "type": "command",
#       "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/auto-format-py.sh"
#     }]
#   }]
# }
#
# 怎麼手動測
# ------------------------------------------------------------
#   echo '{"tool_name":"Edit","tool_input":{"file_path":"main.py"}}' \
#     | .claude/hooks/auto-format-py.sh
#   # 若有 ruff + main.py 存在 → 印 "✨ ruff format: main.py"
# ============================================================

set -uo pipefail

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

case "$TOOL" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

[[ "$FILE_PATH" == *.py ]] || exit 0
[[ -f "$FILE_PATH" ]] || exit 0

if command -v ruff >/dev/null 2>&1; then
  ruff format "$FILE_PATH" >/dev/null 2>&1 || true
  echo "✨ ruff format: $FILE_PATH"
fi

exit 0
