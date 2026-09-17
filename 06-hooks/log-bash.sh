#!/bin/bash
# 記錄所有 bash 指令
# Hook: PostToolUse (matcher: Bash)
#
# 從 stdin 的 JSON 讀取已執行的指令，並記錄到檔案中。
#
# 相容於：macOS、Linux、Windows（Git Bash）

# 從 stdin 讀取 JSON 輸入（Claude Code hook 協定）
INPUT=$(cat)

# 從 tool_input 擷取 bash 指令
# 注意：sed 的 [^"]* 在 JSON 中遇到跳脫的引號就會停止；若指令中含有雙引號
# 字串，只會擷取到第一個 \" 之前的部分 — 這是用 sed 解析 JSON 的已知
# 限制，但對記錄用途來說可以接受。
COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

if [ -z "$COMMAND" ]; then
  exit 0
fi

TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
LOGFILE="$HOME/.claude/bash-commands.log"

# 若記錄目錄不存在就建立它
mkdir -p "$(dirname "$LOGFILE")"

# 記錄該指令
echo "[$TIMESTAMP] $COMMAND" >> "$LOGFILE"

exit 0
