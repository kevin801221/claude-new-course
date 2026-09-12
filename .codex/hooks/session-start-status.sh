#!/usr/bin/env bash
# ============================================================
# Hook: session-start-status
# 事件: SessionStart
# matcher: 無
#
# 這是做什麼用的
# ------------------------------------------------------------
# 每次你開一個新 Claude Code session，自動印出當前 git
# branch、改動檔數、最近一個 commit。讓 Claude 一開場
# 就知道你目前的工作脈絡。
#
# 怎麼觸發
# ------------------------------------------------------------
# 開 `claude` 進入 session、或在現有 session 跑 /clear、
# /compact 時。stdout 會被當成 context 塞給 Claude。
#
# 怎麼開啟（加進 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "SessionStart": [{
#     "hooks": [{
#       "type": "command",
#       "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start-status.sh"
#     }]
#   }]
# }
#
# 怎麼手動測
# ------------------------------------------------------------
#   .claude/hooks/session-start-status.sh
# ============================================================

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "(detached)")
CHANGED=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
LAST_COMMIT=$(git log -1 --pretty=format:'%h %s (%ar)' 2>/dev/null || echo "(no commits)")

cat <<EOF
[session-start-status]
branch       : $BRANCH
modified     : $CHANGED file(s)
last commit  : $LAST_COMMIT
EOF

exit 0
