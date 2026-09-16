#!/bin/bash
# 在事件發生時傳送通知
# Hook: PostToolUse (matcher: Bash) — 在 bash 指令執行後執行；於腳本邏輯中篩選 git push
# 注意：Claude Code 沒有原生的 PostPush 事件。若要在 git push 時觸發，需在此腳本中
# 用 matcher 或條件邏輯檢查 bash 指令字串是否包含「git push」。

REPO_NAME=$(basename $(git rev-parse --show-toplevel 2>/dev/null) 2>/dev/null)
COMMIT_MSG=$(git log -1 --pretty=%B 2>/dev/null)
AUTHOR=$(git log -1 --pretty=%an 2>/dev/null)
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

echo "📢 正在傳送通知給團隊…"

# Slack webhook 範例（請換成你自己的 webhook URL）
SLACK_WEBHOOK="${SLACK_WEBHOOK_URL:-}"

if [ -n "$SLACK_WEBHOOK" ]; then
  curl -X POST "$SLACK_WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d "{
      \"text\": \"New push to *$REPO_NAME*\",
      \"attachments\": [{
        \"color\": \"good\",
        \"fields\": [
          {\"title\": \"Branch\", \"value\": \"$BRANCH\", \"short\": true},
          {\"title\": \"Author\", \"value\": \"$AUTHOR\", \"short\": true},
          {\"title\": \"Commit\", \"value\": \"$COMMIT_MSG\"}
        ]
      }]
    }" \
    --silent --output /dev/null

  echo "✅ Slack 通知已傳送"
fi

# Discord webhook 範例（請換成你自己的 webhook URL）
DISCORD_WEBHOOK="${DISCORD_WEBHOOK_URL:-}"

if [ -n "$DISCORD_WEBHOOK" ]; then
  curl -X POST "$DISCORD_WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d "{
      \"content\": \"**New push to $REPO_NAME**\",
      \"embeds\": [{
        \"title\": \"$COMMIT_MSG\",
        \"color\": 3066993,
        \"fields\": [
          {\"name\": \"Branch\", \"value\": \"$BRANCH\", \"inline\": true},
          {\"name\": \"Author\", \"value\": \"$AUTHOR\", \"inline\": true}
        ]
      }]
    }" \
    --silent --output /dev/null

  echo "✅ Discord 通知已傳送"
fi

# 電子郵件通知範例
EMAIL_TO="${TEAM_EMAIL:-}"

if [ -n "$EMAIL_TO" ]; then
  echo "$REPO_NAME 有新推送，作者：$AUTHOR" | \
    mail -s "Git Push: $BRANCH" "$EMAIL_TO"

  echo "✅ 電子郵件通知已傳送"
fi

exit 0
