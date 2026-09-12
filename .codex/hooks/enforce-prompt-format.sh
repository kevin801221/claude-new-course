#!/usr/bin/env bash
# ============================================================
# Hook: enforce-prompt-format
# 事件: UserPromptSubmit
# matcher: 無
#
# 這是做什麼用的
# ------------------------------------------------------------
# 三件事一次做：
#
#   1. 太短的 prompt 擋下 (< 3 個非空白字元)
#         → exit 2，prompt 整個不會送出，stderr 顯示原因
#
#   2. 偵測「模糊指令」（fix it / 修一下 / 弄一下…）
#         → 不擋，但塞一段 hint 提醒使用者該補哪些資訊
#
#   3. 偵測 prompt 的關鍵字，**條件式**注入團隊規則：
#         - 含 "commit"      → 注入 commit 規則
#         - 含 "test/測試"   → 注入測試規則
#         - 含 "deploy/部署" → 注入部署規則
#
#   ⭐ 條件式注入的好處：避免每個 prompt 都塞一大坨規則。
#      只在相關 prompt 才提醒，省 token 也減少干擾。
#
# 為什麼有用：團隊裡每個人習慣不同，把規範交給 hook 守，
# 比放在 CLAUDE.md 期待大家都記得更可靠。
#
# 怎麼開啟（加進 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "UserPromptSubmit": [{
#     "hooks": [{
#       "type": "command",
#       "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/enforce-prompt-format.sh"
#     }]
#   }]
# }
#
# 怎麼手動測
# ------------------------------------------------------------
#   # 太短：
#   echo '{"prompt":"嗯"}' | .claude/hooks/enforce-prompt-format.sh
#     ; echo "exit=$?"     # → exit=2，會看到擋下訊息
#
#   # 模糊：
#   echo '{"prompt":"fix it"}' | .claude/hooks/enforce-prompt-format.sh
#     # → 注入 hint 段，exit=0
#
#   # 關鍵字觸發團隊規則：
#   echo '{"prompt":"幫我 commit"}' | .claude/hooks/enforce-prompt-format.sh
#     # → 注入 commit 規則
# ============================================================

set -uo pipefail

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

# === 1. 太短直接擋 ===
TRIMMED=$(echo -n "$PROMPT" | tr -d '[:space:]')
LEN=${#TRIMMED}
if (( LEN > 0 && LEN < 3 )); then
  echo "🚫 enforce-prompt-format：prompt 太短（${LEN} 字）" >&2
  echo "" >&2
  echo "請說明：" >&2
  echo "  1) 想做什麼" >&2
  echo "  2) 期望結果" >&2
  echo "  3) （可選）試過什麼" >&2
  exit 2
fi

# === 2. 模糊指令偵測 ===
HINT=""
PROMPT_LC=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]')

VAGUE_PATTERNS=(
  "fix it" "fix it." "fix this" "make it work"
  "修一下" "弄一下" "幫我修" "幫我弄" "修好它" "它壞了" "不能用"
  "help me" "幫我看看"
)
for pattern in "${VAGUE_PATTERNS[@]}"; do
  if [[ "$PROMPT_LC" == *"$pattern"* ]]; then
    HINT="這個 prompt 偏模糊，建議補：(a) 哪個檔/功能 (b) 錯誤訊息 (c) 期望變成怎樣"
    break
  fi
done

# === 3. 條件式注入團隊規則 ===
RULES=""

if echo "$PROMPT_LC" | grep -qE 'commit|git commit|提交'; then
  RULES+="[team-rule:commit]
  - commit message 用繁體中文
  - 不要在 message 提到 Claude / Claude Code
  - 作者保持 kevin801221
  - 用 HEREDOC 確保格式正確

"
fi

if echo "$PROMPT_LC" | grep -qE 'test|pytest|測試|單元測試|integration'; then
  RULES+="[team-rule:test]
  - Python 一律用 uv: \`uv run pytest\`，不要用 pip
  - 整合測試打真實 DB，不要 mock（過去有事故）

"
fi

if echo "$PROMPT_LC" | grep -qE 'deploy|部署|上線|production|prod'; then
  RULES+="[team-rule:deploy]
  - 部署前確認 main 的 CI 全綠
  - production 改動先在 PR 描述列出風險點
  - 避免週五下午部署

"
fi

# === 輸出 ===
if [[ -n "$HINT" || -n "$RULES" ]]; then
  echo "[enforce-prompt-format]"
  if [[ -n "$HINT" ]]; then
    echo "hint: $HINT"
    echo ""
  fi
  if [[ -n "$RULES" ]]; then
    printf '%s' "$RULES"
  fi
fi

exit 0
