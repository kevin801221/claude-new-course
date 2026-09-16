#!/bin/bash
# 寫入檔案後自動格式化程式碼
# Hook: PostToolUse (matcher: Write)
#
# 從 stdin 的 JSON 讀取目標檔案路徑，並執行對應的格式化工具
# 在 Claude 寫入檔案後，直接原地格式化該檔案。
#
# 相容於：macOS、Linux、Windows（Git Bash）

# 從 stdin 讀取 JSON 輸入（Claude Code hook 協定）
INPUT=$(cat)

# 用 sed 擷取 file_path（相容所有平台）
FILE_PATH=$(echo "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

# 偵測檔案類型，並執行對應的格式化
case "$FILE_PATH" in
  *.js|*.jsx|*.ts|*.tsx)
    if command -v prettier &> /dev/null; then
      prettier --write "$FILE_PATH" 2>/dev/null
    fi
    ;;
  *.py)
    if command -v black &> /dev/null; then
      black "$FILE_PATH" 2>/dev/null
    fi
    ;;
  *.go)
    if command -v gofmt &> /dev/null; then
      gofmt -w "$FILE_PATH" 2>/dev/null
    fi
    ;;
  *.rs)
    if command -v rustfmt &> /dev/null; then
      rustfmt "$FILE_PATH" 2>/dev/null
    fi
    ;;
  *.java)
    if command -v google-java-format &> /dev/null; then
      google-java-format -i "$FILE_PATH" 2>/dev/null
    fi
    ;;
esac

exit 0
