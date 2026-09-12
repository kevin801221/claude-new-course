#!/usr/bin/env bash
# Stop hook：Claude Code 一輪結束時叮咚一聲 + 桌面通知
# 教學詳見 docs/walkthroughs/hook_walkthrough.md Phase 3

set -uo pipefail

SOUND="${CLAUDE_PROJECT_DIR:-$(pwd)}/.claude/sounds/notify.wav"

if [[ -f "$SOUND" ]]; then
  afplay "$SOUND" &
fi

osascript -e 'display notification "任務完成 ✅" with title "Claude Code"' 2>/dev/null || true

exit 0
