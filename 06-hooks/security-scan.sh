#!/bin/bash
# 寫入檔案時進行安全性掃描
# Hook: PostToolUse (matcher: Write)
#
# 掃描檔案中是否有寫死的機密資訊、API 金鑰與憑證。
# 發現問題時，透過 additionalContext 輸出不會阻擋執行的警告。
#
# 相容於：macOS、Linux、Windows（Git Bash）

# 從 stdin 讀取 JSON 輸入（Claude Code hook 協定）
INPUT=$(cat)

# 用 sed 擷取 file_path（相容所有平台，包含 Windows Git Bash）
# 避免使用 grep -P（Windows Git Bash 不支援）與 python3 相依
FILE_PATH=$(echo "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

# 略過二進位檔案、第三方套件目錄與建置產物
case "$FILE_PATH" in
  *.png|*.jpg|*.jpeg|*.gif|*.svg|*.ico|*.woff|*.woff2|*.ttf|*.eot) exit 0 ;;
  */node_modules/*|*/.git/*|*/dist/*|*/build/*) exit 0 ;;
esac

ISSUES=""

# 檢查是否有寫死的密碼
# 同時處理 JSON 格式（"password": "value"）與程式碼格式（password = 'value'）
# 用 \\n 當分隔符 — 這是合法的 JSON 換行跳脫字元，能安全通過 printf
if grep -qiE '"password"[[:space:]]*:[[:space:]]*"[^"]+"' "$FILE_PATH" 2>/dev/null; then
  ISSUES="${ISSUES}- 警告：偵測到疑似寫死的密碼\\n"
elif grep -qiE '(password|passwd|pwd)[[:space:]]*=[[:space:]]*'"'"'[^'"'"']+'"'"'' "$FILE_PATH" 2>/dev/null; then
  ISSUES="${ISSUES}- 警告：偵測到疑似寫死的密碼\\n"
fi

# 檢查是否有寫死的 API 金鑰
if grep -qiE '"(api[_-]?key|apikey|access[_-]?token)"[[:space:]]*:[[:space:]]*"[^"]+"' "$FILE_PATH" 2>/dev/null; then
  ISSUES="${ISSUES}- 警告：偵測到疑似寫死的 API 金鑰\\n"
fi

# 檢查是否有寫死的機密資訊與 token
if grep -qiE '(secret|token)[[:space:]]*=[[:space:]]*['"'"'"][^'"'"'"]+['"'"'"]' "$FILE_PATH" 2>/dev/null; then
  ISSUES="${ISSUES}- 警告：偵測到疑似寫死的機密資訊或 token\\n"
fi

# 檢查是否有私密金鑰
if grep -q "BEGIN.*PRIVATE KEY" "$FILE_PATH" 2>/dev/null; then
  ISSUES="${ISSUES}- 警告：偵測到私密金鑰\\n"
fi

# 檢查是否有 AWS 金鑰
if grep -qE "AKIA[0-9A-Z]{16}" "$FILE_PATH" 2>/dev/null; then
  ISSUES="${ISSUES}- 警告：偵測到 AWS 存取金鑰\\n"
fi

# 若有 semgrep 可用就用它掃描（隱藏 stdout，避免和 JSON 輸出混在一起）
if command -v semgrep &> /dev/null; then
  semgrep --config=auto "$FILE_PATH" --quiet >/dev/null 2>/dev/null
fi

# 若有 trufflehog 可用就用它掃描（隱藏 stdout，避免和 JSON 輸出混在一起）
if command -v trufflehog &> /dev/null; then
  trufflehog filesystem "$FILE_PATH" --only-verified --quiet >/dev/null 2>/dev/null
fi

# 若發現問題，就以 additionalContext 輸出（不會阻擋執行的警告）
# 使用 Claude Code PostToolUse 協定所要求的 hookSpecificOutput 格式
if [ -n "$ISSUES" ]; then
  # 為 JSON 轉義檔案路徑（反斜線與雙引號）
  # ISSUES 已經用 \\n 當分隔符（合法的 JSON 跳脫）— 只需轉義雙引號
  SAFE_PATH=$(printf '%s' "$FILE_PATH" | sed 's/\\/\\\\/g; s/"/\\"/g')
  SAFE_ISSUES=$(printf '%s' "$ISSUES" | sed 's/"/\\"/g')
  printf '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "Security scan found issues in %s:\\n%sPlease review and use environment variables instead."}}' "$SAFE_PATH" "$SAFE_ISSUES"
fi

exit 0
