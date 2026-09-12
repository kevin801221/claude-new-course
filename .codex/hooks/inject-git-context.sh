#!/usr/bin/env bash
# ============================================================
# Hook: inject-git-context
# 事件: UserPromptSubmit
# matcher: 無（每次送 prompt 都跑）
#
# 這是做什麼用的
# ------------------------------------------------------------
# 你每次按 Enter 送 prompt 時，自動在訊息前面塞一段
# 「目前 git branch + 改動檔數」的 context 給 Claude。
# 好處：問 Claude「這個檔現在怎樣」時它知道 branch 名稱、
# 不會誤以為在 main。
#
# 怎麼觸發
# ------------------------------------------------------------
# 你按 Enter 送 prompt → Claude Code 跑這個腳本 →
# stdout 內容會被當成「額外 context」塞到 prompt 前。
#
# 怎麼開啟（加進 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "UserPromptSubmit": [{
#     "hooks": [{
#       "type": "command",
#       "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/inject-git-context.sh"
#     }]
#   }]
# }
#
# 怎麼手動測
# ------------------------------------------------------------
#   .claude/hooks/inject-git-context.sh
#   # 預期看到：[git-context] branch: main / modified files: 3
# ============================================================

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "(detached)")
CHANGED=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')

cat <<EOF
[git-context]
branch: $BRANCH
modified files: $CHANGED
EOF

exit 0
