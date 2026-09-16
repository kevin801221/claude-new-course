#!/bin/bash
# Bash 指令的執行前安全檢查
# Hook: PreToolUse (matcher: Bash)
#
# 此 hook 會在每次執行 Bash 工具之前執行，並針對可能具破壞性
# 或高風險的 shell 指令進行阻擋或警告。
#
# 設定方式：
#   cp 06-hooks/pre-tool-check.sh ~/.claude/hooks/
#   chmod +x ~/.claude/hooks/pre-tool-check.sh
#
# 在 ~/.claude/settings.json 中設定：
#   {
#     "hooks": {
#       "PreToolUse": [
#         {
#           "matcher": "Bash",
#           "hooks": [
#             {
#               "type": "command",
#               "command": "~/.claude/hooks/pre-tool-check.sh"
#             }
#           ]
#         }
#       ]
#     }
#   }
#
# 輸入：透過 stdin 傳入的 JSON，格式如下：
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# 輸出慣例（依 Claude Code hook 協定）：
#   - exit 0 → 允許執行。stdout 可包含 JSON（hookSpecificOutput）；stderr
#     會被靜默捨棄，所以寫到 stderr 的警告訊息不會顯示出來。
#     若要觀察被允許的指令，請寫入稽核記錄檔（audit log）。
#   - exit 2 → 阻擋執行。stderr 會回傳給 Claude 作為阻擋原因。
#     任何說明「為何阻擋此指令」的 echo，都必須導向
#     stderr（用 `>&2`），否則 Claude Code 會顯示「No stderr output」。
#
# 稽核記錄：每次呼叫都會記錄到
#   $CLAUDE_PROJECT_DIR/.claude/hooks/audit.log
# 內容包含判定結果（BLOCK/WARN/ALLOW），這樣即使 WARN 等級的
# stderr 輸出被 Claude Code 捨棄，你仍能觀察到這些比對結果。

# 從 stdin 讀取完整的 JSON 輸入
INPUT=$(cat)

# 用可攜的 sed 擷取指令（相容 macOS 與 Linux）
COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

# 若擷取失敗，就退回使用原始輸入
if [ -z "$COMMAND" ]; then
  COMMAND="$INPUT"
fi

# ── 稽核記錄 ─────────────────────────────────────────────────────────────────
# 記錄每次呼叫與最終判定結果。這是觀察 WARN 等級的唯一可靠
# 方式，因為 Claude Code 在 exit 0 時會靜默捨棄 stderr。當 hook 在
# Claude Code 之外被呼叫時（例如本機測試），會退回使用 $(pwd)。
LOG_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}/.claude/hooks"
LOG_FILE="$LOG_DIR/audit.log"
mkdir -p "$LOG_DIR" 2>/dev/null
log_decision() {
  echo "$(date -u +%FT%TZ) [$1] $COMMAND" >> "$LOG_FILE"
}

# ── 阻擋模式 ──────────────────────────────────────────────────────────────────
# 這些指令一律會被阻擋，因為它們幾乎都具有破壞性，
# 而且在自動化情境中很少是刻意執行的。

BLOCKED_PATTERNS=(
  # 把 `rm -rf /` 錨定，讓 `/` 後面必須接空白或行尾，
  # 否則子字串比對會誤判，例如把 `rm -rf /tmp/foo` 也算進去。
  "rm -rf /([[:space:]]|$)"
  "rm -rf \*"
  "dd if=/dev/zero"
  "dd if=/dev/random"
  ":\(\)\{:\|:&\};:"  # Fork bomb（已跳脫正規表達式的特殊字元）
  "mkfs\."           # 檔案系統格式化
  "format c:"        # Windows 磁碟格式化
)

for pattern in "${BLOCKED_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qE "$pattern"; then
    log_decision "BLOCK:$pattern"
    # 這些 echo 一定要導向 stderr — Claude Code 會在 exit 2 時
    # 把 stderr 顯示為阻擋原因。寫到 stdout 會顯示「No stderr output」。
    echo "❌ 已阻擋：偵測到可能具破壞性的指令：$pattern" >&2
    echo "   指令：$COMMAND" >&2
    exit 2
  fi
done

# ── 警告模式 ──────────────────────────────────────────────────────────────────
# 這些模式有風險，但可能是刻意執行的。記錄警告後仍允許執行。

WARNING_PATTERNS=(
  "rm -rf"
  "git push --force"
  "git reset --hard"
  "git clean -f"
  "chmod -R 777"
  "sudo rm"
  "DROP TABLE"
  "DROP DATABASE"
  "truncate"
)

MATCHED_WARNINGS=""
for pattern in "${WARNING_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qi "$pattern"; then
    MATCHED_WARNINGS="${MATCHED_WARNINGS:+$MATCHED_WARNINGS,}$pattern"
    # 把警告同時輸出到 stderr，方便手動執行此 hook 的人查看。
    # Claude Code 在 exit 0 時會捨棄這段輸出 — 稽核記錄才是可靠的
    # 記錄來源（參見 WARN 項目）。
    echo "⚠️  警告：偵測到高風險操作：$pattern" >&2
  fi
done

if [ -n "$MATCHED_WARNINGS" ]; then
  log_decision "WARN:$MATCHED_WARNINGS"
  echo "   指令：$COMMAND" >&2
  echo "   將繼續執行 — 請在繼續前檢查上述警告。" >&2
else
  log_decision "ALLOW"
fi

# ── 允許 ─────────────────────────────────────────────────────────────────────
exit 0
