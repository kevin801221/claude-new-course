#!/usr/bin/env bash
# ============================================================
# Hook: git-commit-secret-guard
# 事件: PreToolUse
# matcher: "Bash"
#
# 這是做什麼用的（影片案例一：Gary Chen 示範）
# ------------------------------------------------------------
# 屬於「確定性安全防呆」。在 Claude 準備執行終端機指令之前攔截。
# 先判斷是否為 git commit 指令：
#   - 如果不是 commit，立即安靜放行（exit 0），完全不拖慢開發速度。
#   - 如果是 commit，深入掃描暫存區（staged changes）：
#     1. 是否誤加了 .env 相關敏感設定檔
#     2. 是否包含 RSA / OpenSSH 等私密金鑰 (Private Key)
#     3. 是否包含常見 API 金鑰（OpenAI, Anthropic, AWS, Google, GitHub 等）
#   - 一旦發現敏感資料，立即以 exit 2 強制阻擋，並列出具體有問題的檔案與行數！
#
# 怎麼開啟（加進專案層 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "hooks": {
#     "PreToolUse": [
#       {
#         "matcher": "Bash",
#         "hooks": [
#           {
#             "type": "command",
#             "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/git-commit-secret-guard.sh",
#             "statusMessage": "🔍 Git Commit Secret Guard 正在掃描機密資訊..."
#           }
#         ]
#       }
#     ]
#   }
# }
#
# 怎麼手動測試教學示範
# ------------------------------------------------------------
# 1. 測試一般指令（放行 exit 0）：
#    echo '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}' \
#      | .claude/hooks/git-commit-secret-guard.sh ; echo "exit=$?"
#
# 2. 測試提交敏感檔案（攔截 exit 2）：
#    git add .env.test 2>/dev/null
#    echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat: add keys\""}}' \
#      | .claude/hooks/git-commit-secret-guard.sh ; echo "exit=$?"
# ============================================================

set -uo pipefail

INPUT=$(cat)

# 支援 jq 解析，若無 jq 則優雅退回 sed
COMMAND=""
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
else
  COMMAND=$(echo "$INPUT" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
fi

# 1. 範圍精確化：判斷是否為 git commit 指令，若無關則安靜放行
if ! echo "$COMMAND" | grep -qE "git[[:space:]]+commit"; then
  exit 0
fi

# 2. 取得 git 暫存區（staged）檔案清單
STAGED_FILES=$(git diff --cached --name-only 2>/dev/null || true)
if [ -z "$STAGED_FILES" ]; then
  exit 0
fi

LEAK_REPORT=""

# 檢查 (A)：是否誤加了 .env 相關檔案（排除 example / template）
for FILE in $STAGED_FILES; do
  BASENAME=$(basename "$FILE")
  case "$BASENAME" in
    .env.example|.env.sample|.env.template|.env.*.example|.env.*.sample)
      continue
      ;;
    .env|.env.*|*.pem|*.key|id_rsa|id_ed25519)
      LEAK_REPORT="${LEAK_REPORT}\n❌ [敏感檔案] 偵測到環境變數或密鑰檔即將被提交：$FILE"
      ;;
  esac
done

# 檢查 (B)：掃描暂存內容中的敏感金鑰與私鑰 (git diff --cached)
DIFF_CONTENT=$(git diff --cached 2>/dev/null || true)

# 私鑰檢測
if echo "$DIFF_CONTENT" | grep -qE "^\+[[:space:]]*.*BEGIN.*PRIVATE KEY"; then
  LEAK_REPORT="${LEAK_REPORT}\n❌ [私鑰外洩] 程式碼變更中包含私密金鑰 (BEGIN PRIVATE KEY)"
fi

# 常見 API Key 特徵檢測
# 1. AWS Access Key (AKIA...)
if echo "$DIFF_CONTENT" | grep -qE "^\+[[:space:]]*.*AKIA[0-9A-Z]{16}"; then
  LEAK_REPORT="${LEAK_REPORT}\n❌ [API 金鑰] 偵測到 AWS Access Key ID"
fi

# 2. Anthropic API Key (sk-ant-...)
if echo "$DIFF_CONTENT" | grep -qE "^\+[[:space:]]*.*sk-ant-[a-zA-Z0-9_\-]{40,}"; then
  LEAK_REPORT="${LEAK_REPORT}\n❌ [API 金鑰] 偵測到 Anthropic Claude API Key"
fi

# 3. OpenAI API Key (sk-...)
if echo "$DIFF_CONTENT" | grep -qE "^\+[[:space:]]*.*sk-[a-zA-Z0-9]{20,T3BlbkFJ[a-zA-Z0-9]{20,}"; then
  LEAK_REPORT="${LEAK_REPORT}\n❌ [API 金鑰] 偵測到 OpenAI API Key"
fi

# 4. Google API Key (AIza...)
if echo "$DIFF_CONTENT" | grep -qE "^\+[[:space:]]*.*AIza[0-9A-Za-z\-_]{35}"; then
  LEAK_REPORT="${LEAK_REPORT}\n❌ [API 金鑰] 偵測到 Google API Key"
fi

# 5. GitHub Token (ghp_...)
if echo "$DIFF_CONTENT" | grep -qE "^\+[[:space:]]*.*ghp_[a-zA-Z0-9]{36}"; then
  LEAK_REPORT="${LEAK_REPORT}\n❌ [API 金鑰] 偵測到 GitHub Personal Access Token"
fi

# 3. 阻擋處理（Exit Code 2 屬於硬性阻擋）
if [ -n "$LEAK_REPORT" ]; then
  echo "==================================================" >&2
  echo "🛡️ [Git Commit Secret Guard] 提交被安全 Hook 強制攔截！" >&2
  echo "==================================================" >&2
  echo -e "$LEAK_REPORT" >&2
  echo -e "\n【處理建議】請將金鑰移至 .env 或系統環境變數中，並將敏感檔案加入 .gitignore！" >&2
  exit 2
fi

exit 0
