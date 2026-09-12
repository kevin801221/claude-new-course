#!/usr/bin/env bash
# ============================================================
# Hook: guard-secrets
# 事件: PreToolUse
# matcher: "Edit|Write|MultiEdit"
#
# 這是做什麼用的
# ------------------------------------------------------------
# 在 Claude **要寫檔之前**攔截。如果它打算寫 .env / *.pem /
# credentials.json 之類的敏感檔案，直接擋下（exit 2）並把
# 原因印到 stderr — Claude 看得到原因，會自己改路徑或放棄。
#
# 怎麼觸發
# ------------------------------------------------------------
# Claude 用 Edit / Write / MultiEdit 工具時，先跑這個腳本。
# 從 stdin 拿到 JSON，內含 .tool_input.file_path。
#
# 怎麼開啟（加進 .claude/settings.json 的 hooks）
# ------------------------------------------------------------
# {
#   "PreToolUse": [{
#     "matcher": "Edit|Write|MultiEdit",
#     "hooks": [{
#       "type": "command",
#       "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/guard-secrets.sh"
#     }]
#   }]
# }
#
# 怎麼手動測（不靠 Claude）
# ------------------------------------------------------------
#   echo '{"tool_name":"Write","tool_input":{"file_path":".env"}}' \
#     | .claude/hooks/guard-secrets.sh ; echo "exit=$?"
#   # 預期看到：🚫 guard-secrets.sh 擋下：.env  ／  exit=2
#
#   echo '{"tool_name":"Write","tool_input":{"file_path":"main.py"}}' \
#     | .claude/hooks/guard-secrets.sh ; echo "exit=$?"
#   # 預期：（沒輸出）／ exit=0   ← 放行
# ============================================================

set -uo pipefail

if ! command -v jq >/dev/null 2>&1; then
  echo "guard-secrets.sh: 需要 jq（brew install jq）" >&2
  exit 0   # 沒 jq 不擋，避免誤殺
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

case "$TOOL" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

[[ -z "$FILE_PATH" ]] && exit 0

BASENAME=$(basename "$FILE_PATH")
case "$BASENAME" in
  .env.example|.env.sample|.env.template|.env.*.example|.env.*.sample)
    exit 0
    ;;
  .env|.env.*|*.pem|*.p12|*.pfx|credentials.json|*.key|id_rsa|id_ed25519)
    echo "🚫 guard-secrets.sh 擋下：$FILE_PATH" >&2
    echo "原因：符合敏感檔案模式（.env / *.pem / credentials...）" >&2
    echo "如需範本請改用 .env.example / .env.sample / .env.template。" >&2
    exit 2
    ;;
esac

exit 0
