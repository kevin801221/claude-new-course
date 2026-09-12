#!/usr/bin/env bash
# ============================================================
# Hook: inject-project-info
# 事件: UserPromptSubmit
# matcher: 無
#
# 這是做什麼用的
# ------------------------------------------------------------
# 每次你按 Enter 送 prompt，自動在前面塞一份「專案現況快照」
# 給 Claude — 比 inject-git-context.sh 更完整：
#
#   - git branch / 改動檔數 / 最近 3 個 commits
#   - 目前有改動的檔案清單（最多 5 個）
#   - Python / Node 版本（若有 pyproject.toml / package.json）
#   - TODO.md 開頭幾行（若存在）
#
# 為什麼有用：問 Claude「這個 PR 還缺什麼」時，它直接看到當前
# branch / 改動 / TODO，不用再叫它 git status。
#
# ⚠️ 注意：每次 prompt 都注入會吃 token。內容愈多愈貴。
#    教學版做了適度精簡；上線用建議再依需要裁減。
#
# 怎麼開啟（加進 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "UserPromptSubmit": [{
#     "hooks": [{
#       "type": "command",
#       "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/inject-project-info.sh"
#     }]
#   }]
# }
#
# 怎麼手動測
# ------------------------------------------------------------
#   .claude/hooks/inject-project-info.sh
#   # stdin 不重要，UserPromptSubmit 從 JSON 拿 prompt，但這個
#   # 腳本沒用到 prompt，直接執行就會印出當前專案資訊
# ============================================================

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0

echo "[project-info]"

# --- git 資訊 ---
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "(detached)")
  CHANGED=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  echo "branch       : $BRANCH"
  echo "modified     : $CHANGED file(s)"

  LAST_COMMITS=$(git log -3 --pretty=format:'  %h %s' 2>/dev/null)
  if [[ -n "$LAST_COMMITS" ]]; then
    echo ""
    echo "recent commits:"
    echo "$LAST_COMMITS"
  fi

  MODIFIED=$(git status --porcelain 2>/dev/null | head -5 | awk '{print "  " $0}')
  if [[ -n "$MODIFIED" ]]; then
    echo ""
    echo "currently modified:"
    echo "$MODIFIED"
  fi
else
  echo "branch       : (not a git repo)"
fi

# --- 環境版本 ---
if [[ -f pyproject.toml || -f requirements.txt ]]; then
  if command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "python       : $(python3 --version 2>&1 | head -1)"
  fi
fi

if [[ -f package.json ]] && command -v node >/dev/null 2>&1; then
  echo "node         : $(node --version 2>&1)"
fi

# --- TODO 預覽 ---
for todo in TODO.md TODO.txt todo.md; do
  if [[ -f "$todo" ]]; then
    echo ""
    echo "TODO preview ($todo):"
    head -5 "$todo" | awk '{print "  " $0}'
    break
  fi
done

exit 0
