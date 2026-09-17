#!/bin/bash
# 驗證使用者提示詞
# Hook: UserPromptSubmit
#
# 從 stdin 的 JSON 讀取使用者提示詞，並阻擋危險操作。
#
# 相容於：macOS、Linux、Windows（Git Bash）

# 從 stdin 讀取 JSON 輸入（Claude Code hook 協定）
INPUT=$(cat)

# 從 JSON 輸入擷取提示詞文字
# Claude Code 傳送 UserPromptSubmit 時使用欄位 "user_prompt"（沒有的話退回用 "prompt"）
PROMPT=$(echo "$INPUT" | sed -n 's/.*"user_prompt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
if [ -z "$PROMPT" ]; then
  PROMPT=$(echo "$INPUT" | sed -n 's/.*"prompt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

if [ -z "$PROMPT" ]; then
  exit 0
fi

# 檢查是否有危險操作
DANGEROUS_PATTERNS=(
  "rm -rf /"
  "delete database"
  "drop database"
  "format disk"
  "dd if="
)

for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if echo "$PROMPT" | grep -qi "$pattern"; then
    printf '{"decision": "block", "reason": "偵測到危險操作：%s"}' "$pattern"
    exit 0
  fi
done

# 檢查是否為正式環境部署
if echo "$PROMPT" | grep -qiE "(deploy|push).*production"; then
  if [ ! -f ".deployment-approved" ]; then
    echo '{"decision": "block", "reason": "正式環境部署需要核准。請建立 .deployment-approved 檔案以繼續。"}'
    exit 0
  fi
fi

# 檢查特定操作是否需要額外上下文
if echo "$PROMPT" | grep -qi "refactor"; then
  if [ ! -d "tests" ] && [ ! -d "test" ]; then
    printf '{"additionalContext": "警告：在沒有測試的情況下進行重構可能有風險，建議先撰寫測試。"}'
  fi
fi

exit 0
