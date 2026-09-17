#!/bin/bash
# 提交前執行測試
# Hook: PreToolUse (matcher: Bash) - 檢查指令是否為 git commit
# 注意：沒有「PreCommit」這個 hook 事件。要用 PreToolUse 搭配 Bash matcher，
# 並檢查指令內容來偵測 git commit 操作。
#
# Exit code：2 會阻擋此次工具呼叫，並把 stderr 顯示為阻擋原因。
# 其他非零值都是不阻擋的錯誤 — commit 仍會繼續進行。
# 來源：https://code.claude.com/docs/en/hooks

echo "🧪 提交前，正在執行測試…"

# 檢查是否有 package.json（Node.js 專案）
if [ -f "package.json" ]; then
  if grep -q "\"test\":" package.json; then
    npm test
    if [ $? -ne 0 ]; then
      echo "❌ 測試失敗！已阻擋提交。" >&2
      exit 2
    fi
  fi
fi

# 檢查是否可用 pytest（Python 專案）
if [ -f "pytest.ini" ] || [ -f "setup.py" ]; then
  if command -v pytest &> /dev/null; then
    pytest
    if [ $? -ne 0 ]; then
      echo "❌ 測試失敗！已阻擋提交。" >&2
      exit 2
    fi
  fi
fi

# 檢查是否有 go.mod（Go 專案）
if [ -f "go.mod" ]; then
  go test ./...
  if [ $? -ne 0 ]; then
    echo "❌ 測試失敗！已阻擋提交。" >&2
    exit 2
  fi
fi

# 檢查是否有 Cargo.toml（Rust 專案）
if [ -f "Cargo.toml" ]; then
  cargo test
  if [ $? -ne 0 ]; then
    echo "❌ 測試失敗！已阻擋提交。" >&2
    exit 2
  fi
fi

echo "✅ 所有測試通過！正在繼續提交。"
exit 0
