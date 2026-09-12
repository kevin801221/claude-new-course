# `.claude/hooks/` 對照表

> 這個資料夾放的是「shell 腳本本身」。
> **要被執行，必須在 `.claude/settings.json` 點名才行**（看完不懂這句話的話，先讀 [hook_walkthrough.md 的「30 秒釘穩心智模型」](../../docs/walkthroughs/hook_walkthrough.md#30-秒釘穩心智模型讀不下去全文至少看這節)）。

## 目前的腳本

| 腳本 | 事件 | matcher | 用途 | 預設 |
|---|---|---|---|---|
| `notify-done.sh` | `Stop` | — | Claude 一輪結束時叮咚 + 桌面通知 | ✅ 已啟用 |
| `guard-secrets.sh` | `PreToolUse` | `Edit\|Write\|MultiEdit` | 擋住寫 `.env` / `*.pem` / `credentials.json` | ⬜ 未啟用 |
| `auto-format-py.sh` | `PostToolUse` | `Edit\|Write\|MultiEdit` | 改完 `.py` 自動跑 `ruff format` | ⬜ 未啟用 |
| `inject-git-context.sh` | `UserPromptSubmit` | — | **極簡版**：注入 git branch + 改動數 | ⬜ 未啟用 |
| `inject-project-info.sh` | `UserPromptSubmit` | — | **進階版**：branch + 最近 3 commits + 改動清單 + 環境版本 + TODO 預覽 | ⬜ 未啟用 |
| `enforce-prompt-format.sh` | `UserPromptSubmit` | — | 太短 prompt 擋下 + 模糊指令提示 + **關鍵字條件式注入團隊規則**（commit/test/deploy） | ⬜ 未啟用 |
| `audit-bash.sh` | `PostToolUse` | `Bash` | 所有 Bash 指令寫到 `.claude/logs/bash-audit.log` | ⬜ 未啟用 |
| `session-start-status.sh` | `SessionStart` | — | 開 session 自動印 git branch / 最近 commit | ⬜ 未啟用 |

### `UserPromptSubmit` 三個範例的差異 ⭐

三個都掛在同一個事件，但目的不同 — 可以單獨用、也可以**全部一起掛**：

| 腳本 | 走「補資訊」路線 | 走「擋/提示」路線 | 大概貢獻多少 token |
|---|---|---|---|
| `inject-git-context.sh` | ✅ 極簡 | — | ~30 |
| `inject-project-info.sh` | ✅ 豐富 | — | ~200 |
| `enforce-prompt-format.sh` | 條件式注入規則 | ✅ 太短擋下、模糊提示 | 通常 0，觸發時 ~80 |

> 💡 **典型搭配**：選一個 inject-* + 加 enforce-prompt-format。
> 不要兩個 inject 同時開（資訊重複塞）。

---

## 怎麼啟用一個 hook？

**Step 1：手動測過 OK**（每個腳本檔頭都有「怎麼手動測」範例）

```bash
# 例：測 guard-secrets
echo '{"tool_name":"Write","tool_input":{"file_path":".env"}}' \
  | .claude/hooks/guard-secrets.sh
echo "exit=$?"
```

**Step 2：把對應的 JSON 加進 `.claude/settings.json`**

每個腳本檔頭都貼好 JSON 範例，**複製過去合併**到 `hooks` 區塊。例：

```json
{
  "hooks": {
    "Stop": [ ... 原本的 ... ],
    "PreToolUse": [{
      "matcher": "Edit|Write|MultiEdit",
      "hooks": [{
        "type": "command",
        "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/guard-secrets.sh"
      }]
    }]
  }
}
```

**Step 3：重啟 Claude Code**

第一次會問你「信任這個 repo 的 hooks 嗎？」選同意。

---

## 怎麼關掉一個 hook？

三種方法擇一：

1. **永久關**：從 `settings.json` 把那筆刪掉
2. **只關自己這台機器**：寫進 `.claude/settings.local.json`（gitignore 不分享）覆蓋掉
3. **暫時關**：把腳本 `chmod -x` 拿掉執行權限（hook 跑失敗但不會擋你做事）

---

## 共通慣例

- **路徑**：腳本內一律用 `$CLAUDE_PROJECT_DIR` 起頭，不要寫絕對路徑（換電腦會壞）
- **依賴**：用到 `jq` 的腳本，若機器沒裝會安靜跳過、不擋 Claude
- **安全網**：所有腳本都 `set -uo pipefail`，遇到未定義變數會 fail 出來
- **不要 set -e**：因為 hook 任何錯誤都不該擋 Claude 做事，個別失敗用 `|| true` 處理
- **exit code**：`0` = 放行 / `2` = PreToolUse 阻擋（只對 Pre 事件有效）/ 其他 = 警告但不擋

---

## 想加新 hook？

照這個範本：

```bash
#!/usr/bin/env bash
# ============================================================
# Hook: <name>
# 事件: <PreToolUse|PostToolUse|Stop|...>
# matcher: <pattern or 無>
#
# 這是做什麼用的
# ------------------------------------------------------------
# <一段白話說明>
#
# 怎麼開啟（加進 .claude/settings.json）
# ------------------------------------------------------------
# <JSON snippet>
#
# 怎麼手動測
# ------------------------------------------------------------
# <能在 terminal 直接跑的測試指令>
# ============================================================

set -uo pipefail
# ... 你的邏輯
exit 0
```

寫完別忘了 `chmod +x` 跟更新本表格。
