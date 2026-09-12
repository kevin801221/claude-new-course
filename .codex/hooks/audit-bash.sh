#!/usr/bin/env bash
# ============================================================
# Hook: audit-bash
# 事件: PostToolUse
# matcher: "Bash"
#
# 這是做什麼用的
# ------------------------------------------------------------
# Claude 每次跑完一條 Bash 指令，就把指令本身寫到
# .claude/logs/bash-audit.log。用於：稽核、回顧、debug
# 「剛剛它到底做了什麼」。
#
# ⚠️ 不會記敏感輸出（只記指令本身）。但指令中若含密碼、
#    token，這條 log 也會帶到，請斟酌是否進 git。
#    建議：把 .claude/logs/ 加進 .gitignore。
#
# 怎麼觸發
# ------------------------------------------------------------
# Claude 跑完任何 Bash 工具呼叫之後跑。
#
# 怎麼開啟（加進 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "PostToolUse": [{
#     "matcher": "Bash",
#     "hooks": [{
#       "type": "command",
#       "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/audit-bash.sh"
#     }]
#   }]
# }
#
# 怎麼手動測
# ------------------------------------------------------------
#   echo '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}' \
#     | .claude/hooks/audit-bash.sh
#   cat .claude/logs/bash-audit.log
#   # 預期：[2026-05-13 22:00:00] ls -la
# ============================================================

set -uo pipefail

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
[[ "$TOOL" == "Bash" ]] || exit 0

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
[[ -z "$CMD" ]] && exit 0

LOG_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}/.claude/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/bash-audit.log"

TS=$(date '+%Y-%m-%d %H:%M:%S')
printf '[%s] %s\n' "$TS" "$CMD" >> "$LOG_FILE"

exit 0
