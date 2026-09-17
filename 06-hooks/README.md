<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../resources/logos/claude-code-tutorial-logo-dark.svg">
  <img alt="Claude Code 完整教學" src="../resources/logos/claude-code-tutorial-logo.svg">
</picture>

# Hooks

Hooks 是在 Claude Code 工作階段（session）中，於特定事件發生時自動執行的腳本。它們能實現自動化、驗證、權限管理與自訂工作流程。

## 總覽

Hooks 是在 Claude Code 發生特定事件時自動執行的動作（shell 指令、HTTP webhook、LLM 提示詞、MCP 工具呼叫，或子代理（Subagents）評估）。它們接收 JSON 輸入，並透過 exit code 與 JSON 輸出來傳達結果。

**主要功能：**
- 事件驅動的自動化
- 以 JSON 為基礎的輸入／輸出
- 支援 `command`、`http`、`mcp_tool`、`prompt`、`agent` 等 hook 類型
- 針對特定工具的模式比對

## 設定

Hooks 是在設定檔中以特定結構來設定：

- `~/.claude/settings.json` - 使用者設定（適用所有專案）
- `.claude/settings.json` - 專案設定（可分享、會提交）
- `.claude/settings.local.json` - 本機專案設定（不提交）
- 受管政策 - 組織層級的設定
- 外掛（Plugins）的 `hooks/hooks.json` - 外掛範圍的 Hooks
- 技能（Skills）／代理的 frontmatter - 元件生命週期的 Hooks

### 基本設定結構

```json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "ToolPattern",
        "hooks": [
          {
            "type": "command",
            "command": "your-command-here",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

**主要欄位：**

| 欄位 | 說明 | 範例 |
|-------|-------------|---------|
| `matcher` | 用來比對工具名稱的模式（區分大小寫） | `"Write"`、`"Edit\|Write"`、`"*"` |
| `hooks` | Hook 定義的陣列 | `[{ "type": "command", ... }]` |
| `type` | Hook 類型：`"command"`（bash）、`"prompt"`（LLM）、`"http"`（webhook）、`"mcp_tool"`（MCP 工具呼叫，v2.1.118+），或 `"agent"`（子代理） | `"command"` |
| `command` | 要執行的 shell 指令 | `"$CLAUDE_PROJECT_DIR/.claude/hooks/format.sh"` |
| `timeout` | 選用的逾時秒數。預設值：command/http/mcp_tool 為 600、prompt 為 30、agent 為 60。 | `30` |
| `once` | 若為 `true`，此 hook 每個工作階段只會執行一次 | `true` |
| `async` | 若為 `true`，會在背景執行且不阻擋 | `true` |
| `asyncRewake` | 若為 `true`，會在背景執行，並在 exit code 為 2 時喚醒 Claude。隱含 `async`。 | `true` |
| `shell` | 接受 `"bash"` 或 `"powershell"`。預設為 `"bash"`；在 Windows 上若未安裝 Git Bash，則預設為 `"powershell"`。 | `"bash"` |
| `statusMessage` | hook 執行期間顯示的自訂轉圈訊息 | `"Formatting…"` |

> **備註**：某些事件會降低預設逾時時間。`UserPromptSubmit` 會把 `command`、`http`、`mcp_tool` 的預設值降到 30 秒，`MessageDisplay` 則降到 10 秒。`SessionEnd` 的 Hooks 共用 1.5 秒的預算；若你的設定為個別 hook 設了更長的 `timeout`，Claude Code 會把預算提高以配合，最多到 60 秒。

### Matcher 模式

| 模式 | 說明 | 範例 |
|---------|-------------|---------|
| 精確字串 | 比對特定工具 | `"Write"` |
| 正規表達式模式 | 比對多個工具 | `"Edit\|Write"` |
| 逗號分隔 | 比對清單中任一工具（v2.1.191+） | `"Write,Edit"` |
| 萬用字元 | 比對所有工具 | `"*"` 或 `""` |
| MCP 工具 | 伺服器與工具的模式 | `"mcp__memory__.*"` |

> **Matcher 現在會做精確比對（v2.1.195+）。** 含連字號的識別字（例如含連字號的 MCP 工具名稱）不再會意外用子字串比對到別的工具。像 `"Write,Edit"` 這種逗號分隔的 matcher，清單中任一工具都會觸發它 — 舊版本會靜默地完全不觸發。

**InstructionsLoaded 的 matcher 值：**

| Matcher 值 | 說明 |
|---------------|-------------|
| `session_start` | 在工作階段啟動時載入指示 |
| `nested_traversal` | 在巢狀目錄遍歷期間載入指示 |
| `path_glob_match` | 透過路徑 glob 模式比對載入指示 |

### 用 `if` 條件縮小範圍（工具參數路徑）

`matcher` 欄位是依**工具名稱**（`"Write"`、`"Edit|Write"`、`"*"`）來挑選要觸發的 hook。若要依工具的**引數**做更細的篩選 — 例如只在編輯動作觸及 `src/` 時才執行 hook，或是要防護對機密檔案的讀取 — 就在個別的 hook handler 上加上 `if` 條件。這和以工具名稱比對的 matcher 不同：`matcher` 決定*哪個工具*，`if` 決定*哪一次呼叫*。

`if` 使用[權限規則語法](https://code.claude.com/docs/en/permissions)（`ToolName(pattern)`），會同時比對工具名稱**與**其引數。對 `Read`／`Edit`／`Write` 而言，路徑模式遵循 gitignore 語意，錨點規則與權限規則相同：像 `.env` 這種單純的名稱在任何深度都會比對到，`src/**` 相對於目前目錄，`/src/**` 相對於專案根目錄，`~/...` 相對於你的家目錄，而 `//...` 則是檔案系統的絕對路徑。

`if` 欄位位於 **hook handler 層級** — 是 `hooks` 陣列內 `type` 與 `command` 的同層欄位 — 而不是放在 `matcher` 上：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "if": "Edit(src/**)",
            "command": "./hooks/lint-src.sh"
          }
        ]
      },
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "if": "Read(.env)",
            "command": "./hooks/block-secret-read.sh"
          }
        ]
      }
    ]
  }
}
```

有效的 `if` 模式範例：`Edit(src/**)`（`src/` 底下的編輯）、`Read(~/.ssh/**)`（讀取任何 SSH 金鑰）、`Read(.env)`（目前目錄或其下的任何 `.env`）、`Bash(git push *)`（僅限 `git push` 子指令）。

> **v2.1.214 更新**：hook 的 `if` 條件中，單一區段的 `dir/**` 模式（例如 `Edit(src/**)`）現在只會比對 `<cwd>/dir` — 不會比對樹狀結構中任何深度的該目錄。以前 `src/**` 也會比對到 `foo/src/**`。若需要任意深度的比對，請使用 `**/dir/**`。**重要**：此限縮只套用在 hook 的 `if:` 條件與允許規則的自動核准 — 拒絕／詢問的權限規則仍會在任何深度比對 `dir/**`。

## Hook 類型

Claude Code 支援五種 hook 類型：

### 指令型 Hooks

預設的 hook 類型。執行 shell 指令，並透過 JSON stdin/stdout 與 exit code 來溝通。

```json
{
  "type": "command",
  "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/validate.py\"",
  "timeout": 60
}
```

#### Exec 形式（`args`）

> v2.1.139 新增。

指令型 hook 除了 shell 形式的 `"command": "..."` 之外，也能用 `args` 陣列透過 `execve()` 直接產生一個執行檔行程。因為沒有經過 shell 解析，路徑佔位字串永遠不需要加引號，設定也不會受 shell injection 漏洞影響。

```json
{
  "type": "command",
  "args": ["python3", "$CLAUDE_PROJECT_DIR/.claude/hooks/validate.py", "--strict"],
  "timeout": 60
}
```

這兩種形式**互斥** — 若一個 hook 同時設了 `command` 與 `args`，會在載入設定時被拒絕。需要管道、重導向、`&&` 串接或 shell 展開時用 `command`；只是呼叫單一執行檔並帶引數時用 `args`。

### HTTP 型 Hooks

> v2.1.63 新增。

遠端的 webhook 端點，接收和指令型 hook 相同的 JSON 輸入。HTTP 型 hook 會把 JSON POST 到該 URL，並收到 JSON 回應。啟用沙箱時，HTTP 型 hook 會經由沙箱路由。基於安全考量，URL 中的環境變數插值需要明確設定 `allowedEnvVars` 清單。

```json
{
  "hooks": {
    "PostToolUse": [{
      "type": "http",
      "url": "https://my-webhook.example.com/hook",
      "matcher": "Write"
    }]
  }
}
```

**主要屬性：**
- `"type": "http"` -- 標示這是 HTTP 型 hook
- `"url"` -- webhook 的端點 URL
- 啟用沙箱時會經由沙箱路由
- URL 中若要做任何環境變數插值，都需要明確的 `allowedEnvVars` 清單

### 提示詞型 Hooks

由 LLM 評估的提示詞，hook 的內容就是一段讓 Claude 評估的提示詞。主要搭配 `Stop` 與 `SubagentStop` 事件，用來做智慧型的任務完成檢查。

```json
{
  "type": "prompt",
  "prompt": "檢查 Claude 是否完成了所有要求的任務。",
  "timeout": 30
}
```

LLM 會評估此提示詞，並回傳結構化的判定結果（詳見[基於提示詞的 Hooks](#基於提示詞的-hooks)）。

### MCP 工具型 Hooks

> v2.1.118 新增。

`mcp_tool` 類型會直接呼叫一個已設定的 MCP 工具；其設定參照的是 MCP 伺服器與工具名稱，而不是 shell 指令或 URL。當驗證或反應邏輯已經存在於你設定好的 MCP 伺服器中時，這個類型就很好用。

```json
{
  "matcher": "Edit",
  "hooks": [{
    "type": "mcp_tool",
    "server": "my-mcp-server",
    "tool": "validate_edit"
  }]
}
```

**主要屬性：**
- `"type": "mcp_tool"` -- 標示這是 MCP 工具型 hook
- `"server"` -- 已設定的 MCP 伺服器名稱
- `"tool"` -- 該伺服器上要呼叫的工具名稱

Hook 的輸入（工具名稱、工具輸入、工作階段上下文）會當作該 MCP 工具的引數傳入。設定 MCP 伺服器請參見 [MCP 伺服器設定](../05-mcp/README.md)。

### 代理型 Hooks

以子代理為基礎的驗證 hook，會產生一個專屬代理來評估條件或執行複雜的檢查。和提示詞型 hook（單輪 LLM 評估）不同，代理型 hook 可以使用工具並進行多步驟推理。

> **備註**：代理型 hooks 為實驗性功能，未來可能變動。

```json
{
  "type": "agent",
  "prompt": "確認程式碼變更是否遵循我們的架構準則，並查閱相關設計文件進行比對。",
  "timeout": 120
}
```

**主要屬性：**
- `"type": "agent"` -- 標示這是代理型 hook
- `"prompt"` -- 給子代理的任務說明
- 該代理可以使用工具（Read、Grep、Bash 等）來進行評估
- 回傳的結構化判定結果與提示詞型 hook 類似

## Hook 事件

Claude Code 支援 **33 種 hook 事件**：

| 事件 | 觸發時機 | Matcher 輸入 | 可阻擋 | 常見用途 |
|-------|---------------|---------------|-----------|------------|
| **SessionStart** | 工作階段開始／恢復／清除／壓縮（compact）時 | startup/resume/clear/compact/fork | 否 | 環境設定 |
| **Setup** | 初始環境設定（每個工作階段一次性） | （無） | 否 | 佈建工具、安裝相依套件 |
| **InstructionsLoaded** | CLAUDE.md 或規則檔載入後 | （無） | 否 | 修改／篩選指示 |
| **UserPromptSubmit** | 使用者送出提示詞時 | （無） | 是 | 驗證提示詞 |
| **UserPromptExpansion** | 使用者提示詞被展開時（例如解析 `@` 提及、斜線指令（Slash Commands）） | （無） | 是 | 轉換或檢查展開後的提示詞 |
| **PreToolUse** | 工具執行前 | 工具名稱 | 是（allow/deny/ask/defer） | 驗證、修改輸入 |
| **PermissionRequest** | 顯示權限對話框時 | 工具名稱 | 是 | 自動核准／拒絕 |
| **PermissionDenied** | 使用者拒絕權限提示時 | 工具名稱 | 否 | 記錄、分析、政策執行 |
| **PostToolUse** | 工具執行成功後 | 工具名稱 | 否 | 新增上下文、回饋 |
| **PostToolUseFailure** | 工具執行失敗時 | 工具名稱 | 否 | 錯誤處理、記錄 |
| **PostToolBatch** | 一批工具呼叫完成後 | （無） | 否 | 彙總報告、批次驗證 |
| **Notification** | 傳送通知時 | 通知類型 | 否 | 自訂通知 |
| **MessageDisplay** | 顯示助理訊息文字期間 | （無） | 否 | 轉換或隱藏顯示的訊息文字（v2.1.152） |
| **SubagentStart** | 子代理產生時 | 代理類型名稱 | 否 | 子代理設定 |
| **SubagentStop** | 子代理完成時 | 代理類型名稱 | 是 | 子代理驗證 |
| **Stop** | Claude 完成回應時 | （無） | 是 | 任務完成檢查 |
| **StopFailure** | API 錯誤導致該輪結束時 | （無） | 否 | 錯誤復原、記錄 |
| **TeammateIdle** | 代理團隊（Agent Teams）隊友閒置時 | （無） | 是 | 隊友協調 |
| **TaskCompleted** | 任務標記為完成時 | （無） | 是 | 任務後續動作 |
| **TaskCreated** | 透過 TaskCreate 建立任務時 | （無） | 否 | 任務追蹤、記錄 |
| **ConfigChange** | 設定檔變更時 | （無） | 是（政策設定除外） | 因應設定更新 |
| **CwdChanged** | 工作目錄變更時 | （無） | 否 | 針對目錄的專屬設定 |
| **DirectoryAdded** | 工作階段中透過 `/add-dir` 或 SDK 的 `register_repo_root` 控制請求新增工作目錄時（v2.1.219） | （無） | 否 | 為新增的目錄設定工具 |
| **FileChanged** | 被監看的檔案變更時 | （無） | 否 | 檔案監控、重新建置 |
| **PreCompact** | 上下文壓縮前 | manual/auto | 否 | 壓縮前的動作 |
| **PostCompact** | 壓縮完成後 | （無） | 否 | 壓縮後的動作 |
| **PreModelSwitch** | Claude Code 套用請求的模型切換前 | 要切換到的模型的標準名稱（來自 `to_model`） | 是 | 攔截或否決模型變更 |
| **PostModelSwitch** | 工作階段模型變更後（包含 Claude Code 自己觸發的變更，例如在 resume 時還原模型） | 切換到的模型的標準名稱（來自 `to_model`） | 否 | 記錄或回應模型變更 |
| **WorktreeCreate** | worktree 建立時 | （無） | 是（可回傳路徑） | worktree 初始化 |
| **WorktreeRemove** | worktree 移除時 | （無） | 否 | worktree 清理 |
| **Elicitation** | MCP 伺服器要求使用者輸入時 | （無） | 是 | 輸入驗證 |
| **ElicitationResult** | 使用者回應 elicitation 時 | （無） | 是 | 回應處理 |
| **SessionEnd** | 工作階段結束時 | （無） | 否 | 清理、最終記錄 |

`PreModelSwitch` 與 `PostModelSwitch` 需要 v2.1.251 以上版本。兩者都會收到 `from_model` 與 `to_model`；matcher 是依從 `to_model` 推算出的標準名稱來評估的（例如 `claude-opus-5`、`.*opus.*`）。它們的 `command`、`http`、`mcp_tool` 預設逾時會降到 30 秒。

> **`TaskCreated` 與 `TaskCompleted` 需要啟用待辦事項工具（v2.1.233）。** 這兩個
> 事件是由待辦事項／任務追蹤工具（`TaskCreate`/`Get`/`Update`/`List`、
> `TodoWrite`）觸發的，而這些工具在 **Opus 4.8、Sonnet 5、Fable 5、Mythos 5
> 及更新的模型上已不再提供**。在這些模型上，這些 hook 仍是合法設定，只是
> 完全不會觸發 — 不會有輸出也不會有錯誤。設定 `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`
> 即可讓這些工具（連帶這些事件）重新回來。

> **PostToolUse 的執行時間（v2.1.119）：** `PostToolUse` 與 `PostToolUseFailure` 的 hook 輸入現在包含 `duration_ms` — 詳見 [PostToolUse](#posttooluse) 一節。

### PreToolUse

在 Claude 建立工具參數之後、處理之前執行。可用來驗證或修改工具輸入。

**設定：**
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/validate-bash.py"
          }
        ]
      }
    ]
  }
}
```

**常見的 matcher：** `Task`、`Bash`、`Glob`、`Grep`、`Read`、`Edit`、`Write`、`WebFetch`、`WebSearch`

**輸出控制：**
- `permissionDecision`：`"allow"`、`"deny"`、`"ask"` 或 `"defer"`
  - `"allow"` 會跳過權限提示（需要使用者互動的工具除外，以及你組織設為 `ask` 的連接器工具）
  - `"deny"` 會阻止該次工具呼叫
  - `"ask"` 會提示使用者確認
  - `"defer"` 會優雅地結束，讓工具之後可以繼續執行；此值下 `permissionDecisionReason`、`updatedInput` 與 `additionalContext` 都會被忽略
  - 不論 hook 回傳什麼，deny 與 ask 規則仍會被評估。當多個 `PreToolUse` hook 意見不一致時，優先順序是 `deny` > `defer` > `ask` > `allow`
- `permissionDecisionReason`：判定結果的說明。`"allow"` 與 `"ask"` 時會顯示給使用者（不是 Claude）看；`"deny"` 時會顯示給 Claude 看；`"defer"` 時會被忽略
- `updatedInput`：修改後的工具輸入參數

### PostToolUse

在工具完成後立即執行。可用於驗證、記錄，或把上下文回傳給 Claude。

**設定：**
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/security-scan.py"
          }
        ]
      }
    ]
  }
}
```

**輸出控制：**
- `"block"` 判定結果會把回饋提示給 Claude
- `additionalContext`：新增給 Claude 的上下文

**額外的輸入欄位（v2.1.119）：**

| 欄位 | 型別 | 說明 |
|-------|------|-------------|
| `duration_ms` | number | 工具執行時間（毫秒）。不含花在權限提示與 PreToolUse hook 執行上的時間。`PostToolUse` 與 `PostToolUseFailure` 這兩種 hook 都提供此欄位。 |

#### 可恢復的阻擋（`continueOnBlock`，v2.1.139）

預設情況下，`PostToolUse` hook 若回傳 `"decision": "block"`，會中止目前這一輪。在 hook 上設定 `"continueOnBlock": true`，改為把拒絕的結果以 `tool_result` 的形式回傳給 Claude，讓模型可以讀取回饋並重試或調整。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/policy-check.py",
            "continueOnBlock": true
          }
        ]
      }
    ]
  }
}
```

當 hook 的 `reason` 是 Claude 可以據以行動的內容時（例如「這個檔案是唯讀的，請寫到別處」）就使用這個選項；若阻擋必須完全中止該輪，就不要設定它。

### UserPromptSubmit

在使用者送出提示詞、Claude 處理之前執行。

**設定：**
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/validate-prompt.py"
          }
        ]
      }
    ]
  }
}
```

**輸出控制：**
- `decision`：`"block"` 可阻止處理
- `reason`：被阻擋時的說明
- `additionalContext`：新增到提示詞的上下文

### Stop 與 SubagentStop

在 Claude 完成回應時（Stop）或子代理完成時（SubagentStop）執行。支援以提示詞為基礎的評估，做智慧型的任務完成檢查。

**額外的輸入欄位：** `Stop` 與 `SubagentStop` 這兩種 hook 的 JSON 輸入都會收到 `last_assistant_message` 欄位，內容是 Claude 或子代理停止前的最後一則訊息。這在評估任務是否完成時很有用。

**設定：**
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "檢查 Claude 是否完成了所有要求的任務。",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

> **連續阻擋的安全上限（v2.1.143）**：若同一輪中 `Stop` hook 連續 **8 次**回傳 `"decision": "block"`（或設定 `continue: false`），Claude Code 會中斷這個迴圈，並以警告結束該工作階段。可用環境變數 `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP=<integer>` 覆寫此門檻（設為 `0` 可完全停用此上限）。這能避免有問題的 Stop hook 讓工作階段無限迴圈下去。

**回傳欄位（v2.1.163）：** `Stop` 或 `SubagentStop` hook 可以回傳 `hookSpecificOutput.additionalContext`，把回饋給 Claude，並**在不顯示錯誤標籤的情況下繼續這一輪**。以前要從 Stop hook 影響模型不太方便；現在 hook 可以乾淨地注入上下文，不會出現舊回饋路徑（例如 `"decision": "block"`）那種錯誤標籤的行為。

```json
{
  "hookSpecificOutput": {
    "hookEventName": "Stop",
    "additionalContext": "提醒：宣告完成前請先執行測試套件。"
  }
}
```

### SubagentStart

在子代理開始執行時執行。matcher 輸入是代理類型名稱，讓 hook 可以鎖定特定的子代理類型。

**設定：**
```json
{
  "hooks": {
    "SubagentStart": [
      {
        "matcher": "code-review",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/subagent-init.sh"
          }
        ]
      }
    ]
  }
}
```

### SessionStart

在工作階段開始或恢復時執行。可以持久化環境變數。

**Matcher：** `startup`、`resume`、`clear`、`compact`、`fork`

> **v2.1.214 更新**：分岔（fork）出來的工作階段現在會回報來源為 `"fork"` — 以前回報的是 `"resume"`。

**特殊功能：** 用 `CLAUDE_ENV_FILE` 來持久化環境變數（`CwdChanged` 與 `FileChanged` hook 也提供此變數）：

```bash
#!/bin/bash
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo 'export NODE_ENV=development' >> "$CLAUDE_ENV_FILE"
fi
exit 0
```

**工作階段範圍的輸出（v2.1.152）：** `SessionStart` hook 可以回傳 JSON，重新掃描技能並設定工作階段標題：

```json
{
  "reloadSkills": true,
  "hookSpecificOutput": {
    "sessionTitle": "Payments migration"
  }
}
```

頂層的 `reloadSkills: true` 會在同一個工作階段中觸發技能重新掃描（和 `/reload-skills` 指令的動作相同），讓 hook 剛安裝的技能立即可用。`hookSpecificOutput.sessionTitle` 會在啟動與恢復時設定工作階段的顯示標題。

### SessionEnd

在工作階段結束時執行，用來做清理或最終記錄。無法阻擋工作階段結束。

**`reason` 欄位的值：**
- `clear` - 使用者清除了工作階段
- `logout` - 使用者登出
- `prompt_input_exit` - 使用者透過提示詞輸入離開
- `other` - 其他原因

**設定：**
```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/session-cleanup.sh\""
          }
        ]
      }
    ]
  }
}
```

### Notification 事件

通知事件的 matcher 更新：
- `permission_prompt` - 權限請求通知
- `idle_prompt` - 閒置狀態通知
- `auth_success` - 驗證成功
- `elicitation_dialog` - 顯示給使用者的對話框
- `agent_needs_input` - 背景代理需要輸入（v2.1.198）
- `agent_completed` - 背景代理已完成（v2.1.198）

### PreModelSwitch

在 Claude Code 套用所請求的模型切換**之前**執行 — 例如你執行 `/model` 時，或某個元件要求換一個模型時。需要 v2.1.251 以上版本。

**Matcher：** 要切換到的模型的標準名稱，從 `to_model` 推算而來。可以比對特定模型（`claude-opus-5`），或用正規表達式比對整個系列（`.*opus.*`）。

**輸入欄位：** 除了共同欄位外，此 hook 還會收到 `from_model`（切換前使用的模型）與 `to_model`（要求切換到的模型）。

**可阻擋：** 可以。Exit code `2` 會阻擋此次切換，並把 stderr 顯示為錯誤，讓工作階段維持目前的模型。可用這個機制來攔截或否決模型變更 — 例如讓成本敏感的專案不要用到最貴的模型。

**逾時：** 此事件會把 `command`、`http`、`mcp_tool` 的預設逾時降到 30 秒。

**設定：**
```json
{
  "hooks": {
    "PreModelSwitch": [
      {
        "matcher": ".*opus.*",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/gate-model-switch.sh"
          }
        ]
      }
    ]
  }
}
```

```bash
#!/bin/bash
# gate-model-switch.sh - 拒絕在此專案切換到 Opus
input=$(cat)
to_model=$(echo "$input" | jq -r '.to_model')

if [[ "$to_model" == *opus* ]]; then
  echo "此專案的預算只編列給 Sonnet；將維持使用目前的模型。" >&2
  exit 2
fi

exit 0
```

### PostModelSwitch

在工作階段的模型變更**之後**執行。它也會在 Claude Code 自己觸發變更時觸發 — 例如在恢復工作階段時還原先前選定的模型 — 不只限於你自己要求的切換。需要 v2.1.251 以上版本。

**Matcher：** 與 `PreModelSwitch` 相同 — 從 `to_model` 推算出的標準名稱。

**輸入欄位：** `from_model` 與 `to_model`，以及共同欄位。

**可阻擋：** 否。切換已經發生；此 hook 只能觀察並回應。

**逾時：** 此事件會把 `command`、`http`、`mcp_tool` 的預設逾時降到 30 秒。

**設定：**
```json
{
  "hooks": {
    "PostModelSwitch": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/log-model-switch.sh"
          }
        ]
      }
    ]
  }
}
```

```bash
#!/bin/bash
# log-model-switch.sh - 把每次模型變更都附加到工作階段記錄檔
input=$(cat)
from=$(echo "$input" | jq -r '.from_model')
to=$(echo "$input" | jq -r '.to_model')

echo "$(date -Iseconds) $from -> $to" >> ~/.claude/model-switches.log
exit 0
```

## 元件範圍的 Hooks

Hooks 可以附加在特定元件（技能、代理、指令）的 frontmatter 中：

**在 SKILL.md、agent.md 或 command.md 中：**

```yaml
---
name: secure-operations
description: 執行具安全檢查的操作
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/check.sh"
          once: true  # 每個工作階段只執行一次
---
```

**元件 Hooks 支援的事件：** `PreToolUse`、`PostToolUse`、`Stop`

這樣就能直接在使用它們的元件中定義 Hooks，讓相關程式碼放在一起。

### 子代理 Frontmatter 中的 Hooks

當子代理的 frontmatter 中定義了 `Stop` hook 時，它會自動轉換成限定於該子代理的 `SubagentStop` hook。這樣可確保 stop hook 只在該特定子代理完成時觸發，而不是在主工作階段結束時觸發。

```yaml
---
name: code-review-agent
description: 自動化程式碼審查子代理
hooks:
  Stop:
    - hooks:
        - type: prompt
          prompt: "確認程式碼審查是否完整且徹底。"
  # 上面的 Stop hook 對這個子代理會自動轉換成 SubagentStop
---
```

**需要工作區信任（v2.1.218）：** **專案**子代理中的 frontmatter hooks，現在執行前需要先對代理檔案所在的資料夾接受工作區信任。在 v2.1.218 之前，這些 hooks 可以在你尚未信任的資料夾中執行。哪些範圍可豁免，請參見[子代理文件](https://code.claude.com/docs/en/sub-agents#hooks-in-subagent-frontmatter)。

## PermissionRequest 事件

用自訂的輸出格式處理權限請求：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow|deny",
      "updatedInput": {},
      "message": "自訂訊息",
      "interrupt": false
    }
  }
}
```

## Hook 輸入與輸出

### JSON 輸入（透過 stdin）

所有 hooks 都會透過 stdin 接收 JSON 輸入：

```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/path/to/file.js",
    "content": "..."
  },
  "tool_use_id": "toolu_01ABC123...",
  "agent_id": "agent-abc123",
  "agent_type": "main",
  "worktree": "/path/to/worktree",
  "effort": { "level": "medium" }
}
```

**常見欄位：**

| 欄位 | 說明 |
|-------|-------------|
| `session_id` | 唯一的工作階段識別碼 |
| `transcript_path` | 對話紀錄檔的路徑 |
| `cwd` | 目前的工作目錄 |
| `prompt_id` | 目前處理中提示詞的 UUID；對應 OpenTelemetry 的 `prompt.id` 屬性（v2.1.196） |
| `hook_event_name` | 觸發此 hook 的事件名稱 |
| `agent_id` | 執行此 hook 的代理識別碼 |
| `agent_type` | 代理類型（`"main"`、子代理類型名稱等） |
| `worktree` | 若代理在 git worktree 中執行，此為該 worktree 的路徑 |
| `effort.level` | （v2.1.133+）目前生效的 effort 等級：`low`、`medium`、`high`、`xhigh` 或 `max` |

### Exit Codes

| Exit Code | 意義 | 行為 |
|-----------|---------|----------|
| **0** | 成功 | 繼續執行，解析 JSON stdout |
| **2** | 阻擋型錯誤 | 阻擋操作，stderr 顯示為錯誤 |
| **其他** | 非阻擋型錯誤 | 繼續執行，stderr 在詳細模式下顯示 |

> ⚠️ **`exit 1` 不會擋。** Unix 慣例上非零就是失敗，但這裡只有 `2` 是「阻止」——
> 其他非零只代表「這支 hook 自己出錯了」，動作照樣執行完。
> 現場想驗證：把擋 `.env` 的 hook 從 `exit 2` 改成 `exit 1` 再試一次，檔案會真的被寫出去。

### 哪些事件擋得住（`exit 2` 的效果因事件而異）

前面每個事件章節都各自寫了「可阻擋」，這裡併成一張表方便查：

| 事件 | 擋得住嗎 | `exit 2` 的效果 |
|---|---|---|
| `PreToolUse` | ✅ | 這次工具呼叫不執行 |
| `UserPromptSubmit` | ✅ | 阻擋處理，**而且會把你的 prompt 清掉** |
| `UserPromptExpansion` | ✅ | 阻擋這次展開 |
| `Stop` / `SubagentStop` | ✅ | **不准它停** —— 對話繼續跑下去 |
| `PreModelSwitch` | ✅ | 維持目前的模型，不切換 |
| `PostToolUse` | ❌ | 工具已經執行完了，來不及 |
| `PostModelSwitch` | ❌ | 切換已經發生，只能觀察 |
| `SessionStart` | ❌ | `exit 2` 被當成沒擋 |
| `PermissionRequest` | ❌ | 不吃 `exit 2`，要用 `decision` 物件拒絕 |

**`Stop` 那一列是最有想像空間的**：exit 2 的意思是「不准它停下來」。
所以「檢查這一輪的產出，不合格就叫它重做」只要三行 —— 檢查、不合格就
`echo 理由 >&2; exit 2`。自動迴圈型的 plugin（例如 `ralph-wiggum`）就是這樣做的。

### JSON 輸出（stdout，exit code 0）

```json
{
  "continue": true,
  "stopReason": "停止時的選用訊息",
  "suppressOutput": false,
  "systemMessage": "選用的警告訊息",
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "檔案位於允許的目錄中",
    "updatedInput": {
      "file_path": "/modified/path.js"
    }
  }
}
```

> **適用範圍（v2.1.121+）：** `hookSpecificOutput.updatedToolOutput` 現在對**所有**工具都有效，不只是 MCP 工具。針對 `Bash`、`Edit`、`Read` 等工具的 `PostToolUse` hook，可以在 Claude 看到工具輸出之前先改寫它 — 適合用來遮蔽機密資訊、正規化 diff，或過濾雜訊過多的指令輸出。範例（從 `Bash` 輸出中移除 ANSI 顏色碼）：
>
> ```json
> {
>   "hookSpecificOutput": {
>     "hookEventName": "PostToolUse",
>     "updatedToolOutput": "<移除 ANSI 逸出序列後的純文字輸出>"
>   }
> }
> ```

> **`retry`（PermissionDenied）：** 用 JSON 的 `hookSpecificOutput.retry: true` 來告訴模型它可以重試被拒絕的工具呼叫。

> **已棄用的 `PreToolUse` 決策形式：** 對於 `PreToolUse`，最上層的 `decision` 與 `reason` 欄位已**棄用** — 請改用 `hookSpecificOutput.permissionDecision`（`allow` / `deny` / `ask` / `defer`）與 `permissionDecisionReason`。各決策的優先順序為 `deny` > `defer` > `ask` > `allow`。另外要注意，`suppressOutput` 雖然仍可傳入，但**不會有任何效果**。

#### `terminalSequence`（v2.1.141）

Hooks 可以透過在 JSON 輸出中設定 `terminalSequence`，來送出原始的 OSC（作業系統指令）逸出序列。當 hook 回傳時，host 會把該序列寫入其控制終端機 — 適合用來做桌面通知、更新視窗標題、終端機提示音，而不需要自己擁有 TTY。

| 欄位 | 型別 | 說明 |
|-------|------|-------------|
| `terminalSequence` | string | 原始逸出序列（通常是 OSC 9 / OSC 0 / OSC 777）。會原樣寫入 host 終端機。 |

範例 — 在長時間任務完成時觸發一則 OSC 9 桌面通知：

```json
{
  "terminalSequence": "]9;任務完成"
}
```

把它設定在 `Stop` hook 上，這樣 Claude 完成一輪回應時就會觸發通知。序列的支援程度依終端機而異；Kitty／iTerm2／Windows Terminal 都支援 OSC 9。

## 環境變數

| 變數 | 適用範圍 | 說明 |
|----------|-------------|-------------|
| `CLAUDE_PROJECT_DIR` | 所有 hooks | 專案根目錄的絕對路徑 |
| `CLAUDE_ENV_FILE` | SessionStart、CwdChanged、FileChanged | 用來持久化環境變數的檔案路徑 |
| `CLAUDE_CODE_REMOTE` | 所有 hooks | 在遠端環境中執行時為 `"true"` |
| `${CLAUDE_PLUGIN_ROOT}` | 外掛 hooks | 外掛目錄的路徑 |
| `${CLAUDE_PLUGIN_DATA}` | 外掛 hooks | 外掛資料目錄的路徑 |
| `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` | SessionEnd hooks | SessionEnd hooks 可設定的逾時時間（毫秒），會覆寫預設值 |
| `CLAUDE_CODE_SESSION_ID` | Bash 工具子行程（v2.1.132+） | 工作階段的 UUID；對應 hook 輸入 JSON 中的 `session_id` 欄位。可用來把 bash 記錄和 hook 遙測資料互相對應。 |
| `CLAUDE_EFFORT` | Bash 工具子行程（v2.1.133+） | 目前生效的 effort 等級（`low`/`medium`/`high`/`xhigh`/`max`）；對應 hook 輸入 JSON 中的 `effort.level`。 |
| `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` | 全行程範圍（v2.1.143+） | 在工作階段以警告結束前，Stop hook 連續阻擋的最大次數（預設 `8`）。設為 `0` 可停用此上限。 |

## 基於提示詞的 Hooks

對於 `Stop` 與 `SubagentStop` 事件，你可以使用以 LLM 為基礎的評估：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "檢查所有任務是否都已完成，並回傳你的決策。",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

**LLM 回應格式：**
```json
{
  "decision": "approve",
  "reason": "所有任務都已成功完成",
  "continue": false,
  "stopReason": "任務已完成"
}
```

## 範例

### 範例 1：Bash 指令驗證器（PreToolUse）

**檔案：** `.claude/hooks/validate-bash.py`

```python
#!/usr/bin/env python3
import json
import sys
import re

BLOCKED_PATTERNS = [
    (r"\brm\s+-rf\s+/", "阻擋危險的 rm -rf / 指令"),
    (r"\bsudo\s+rm", "阻擋 sudo rm 指令"),
]

def main():
    input_data = json.load(sys.stdin)

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    command = input_data.get("tool_input", {}).get("command", "")

    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, command):
            print(message, file=sys.stderr)
            sys.exit(2)  # Exit 2 = 阻擋型錯誤

    sys.exit(0)

if __name__ == "__main__":
    main()
```

**設定：**
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/validate-bash.py\""
          }
        ]
      }
    ]
  }
}
```

### 範例 2：安全掃描器（PostToolUse）

**檔案：** `.claude/hooks/security-scan.py`

```python
#!/usr/bin/env python3
import json
import sys
import re

SECRET_PATTERNS = [
    (r"password\s*=\s*['\"][^'\"]+['\"]", "疑似寫死的密碼"),
    (r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]", "疑似寫死的 API 金鑰"),
]

def main():
    input_data = json.load(sys.stdin)

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ["Write", "Edit"]:
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    content = tool_input.get("content", "") or tool_input.get("new_string", "")
    file_path = tool_input.get("file_path", "")

    warnings = []
    for pattern, message in SECRET_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            warnings.append(message)

    if warnings:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": f"{file_path} 的安全性警告：" + "；".join(warnings)
            }
        }
        print(json.dumps(output))

    sys.exit(0)

if __name__ == "__main__":
    main()
```

### 範例 3：自動格式化程式碼（PostToolUse）

**檔案：** `.claude/hooks/format-code.sh`

```bash
#!/bin/bash

# 從 stdin 讀取 JSON
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('tool_name', ''))")
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys, json; print(json.load(sys.stdin).get('tool_input', {}).get('file_path', ''))")

if [ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ]; then
    exit 0
fi

# 依檔案副檔名決定要用的格式化工具
case "$FILE_PATH" in
    *.js|*.jsx|*.ts|*.tsx|*.json)
        command -v prettier &>/dev/null && prettier --write "$FILE_PATH" 2>/dev/null
        ;;
    *.py)
        command -v black &>/dev/null && black "$FILE_PATH" 2>/dev/null
        ;;
    *.go)
        command -v gofmt &>/dev/null && gofmt -w "$FILE_PATH" 2>/dev/null
        ;;
esac

exit 0
```

### 範例 4：提示詞驗證器（UserPromptSubmit）

**檔案：** `.claude/hooks/validate-prompt.py`

```python
#!/usr/bin/env python3
import json
import sys
import re

BLOCKED_PATTERNS = [
    (r"delete\s+(all\s+)?database", "危險：刪除資料庫"),
    (r"rm\s+-rf\s+/", "危險：刪除根目錄"),
]

def main():
    input_data = json.load(sys.stdin)
    prompt = input_data.get("user_prompt", "") or input_data.get("prompt", "")

    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            output = {
                "decision": "block",
                "reason": f"已阻擋：{message}"
            }
            print(json.dumps(output))
            sys.exit(0)

    sys.exit(0)

if __name__ == "__main__":
    main()
```

### 範例 5：智慧型 Stop Hook（提示詞型）

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "檢查 Claude 是否完成了所有要求的任務。請確認：1）所有檔案是否都已建立／修改？2）是否還有未解決的錯誤？若未完成，請說明缺少什麼。",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

### 範例 6：上下文用量追蹤器（成對 Hook）

搭配使用 `UserPromptSubmit`（訊息前）與 `Stop`（回應後）這兩個 hook，追蹤每個請求消耗的 token 數量。

**檔案：** `.claude/hooks/context-tracker.py`

```python
#!/usr/bin/env python3
"""
上下文用量追蹤器 — 追蹤每個請求消耗的 token 數量。

用 UserPromptSubmit 當作「訊息前」hook、Stop 當作「回應後」hook，
計算每個請求的 token 用量差值。

Token 計算方式：
1. 字元估算（預設）：每個 token 約 4 個字元，不需要相依套件
2. tiktoken（選用）：較準確（約 90-95%），需要安裝：pip install tiktoken
"""
import json
import os
import sys
import tempfile

# 設定
CONTEXT_LIMIT = 128000  # Claude 的上下文視窗（請依你使用的模型調整）
USE_TIKTOKEN = False    # 若已安裝 tiktoken 以取得較準確的結果，設為 True


def get_state_file(session_id: str) -> str:
    """取得用來儲存訊息前 token 數量的暫存檔路徑，依工作階段區隔。"""
    return os.path.join(tempfile.gettempdir(), f"claude-context-{session_id}.json")


def count_tokens(text: str) -> int:
    """
    計算文字中的 token 數量。

    若有安裝 tiktoken，會用 p50k_base 編碼計算（準確度約 90-95%），
    否則退回使用字元估算法（準確度約 80-90%）。
    """
    if USE_TIKTOKEN:
        try:
            import tiktoken
            enc = tiktoken.get_encoding("p50k_base")
            return len(enc.encode(text))
        except ImportError:
            pass  # 退回使用估算法

    # 以字元估算：英文每個 token 約 4 個字元
    return len(text) // 4


def read_transcript(transcript_path: str) -> str:
    """讀取對話紀錄檔，並串接其中所有內容。"""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""

    content = []
    with open(transcript_path, "r") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                # 從各種訊息格式中擷取文字內容
                if "message" in entry:
                    msg = entry["message"]
                    if isinstance(msg.get("content"), str):
                        content.append(msg["content"])
                    elif isinstance(msg.get("content"), list):
                        for block in msg["content"]:
                            if isinstance(block, dict) and block.get("type") == "text":
                                content.append(block.get("text", ""))
            except json.JSONDecodeError:
                continue

    return "\n".join(content)


def handle_user_prompt_submit(data: dict) -> None:
    """訊息前 hook：在請求前儲存目前的 token 數量。"""
    session_id = data.get("session_id", "unknown")
    transcript_path = data.get("transcript_path", "")

    transcript_content = read_transcript(transcript_path)
    current_tokens = count_tokens(transcript_content)

    # 存到暫存檔，供之後比對
    state_file = get_state_file(session_id)
    with open(state_file, "w") as f:
        json.dump({"pre_tokens": current_tokens}, f)


def handle_stop(data: dict) -> None:
    """回應後 hook：計算並回報 token 用量差值。"""
    session_id = data.get("session_id", "unknown")
    transcript_path = data.get("transcript_path", "")

    transcript_content = read_transcript(transcript_path)
    current_tokens = count_tokens(transcript_content)

    # 讀取訊息前的數量
    state_file = get_state_file(session_id)
    pre_tokens = 0
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
                pre_tokens = state.get("pre_tokens", 0)
        except (json.JSONDecodeError, IOError):
            pass

    # 計算差值
    delta_tokens = current_tokens - pre_tokens
    remaining = CONTEXT_LIMIT - current_tokens
    percentage = (current_tokens / CONTEXT_LIMIT) * 100

    # 回報用量
    method = "tiktoken" if USE_TIKTOKEN else "estimated"
    print(f"上下文（{method}）：約 {current_tokens:,} tokens（已使用 {percentage:.1f}%，剩餘約 {remaining:,}）", file=sys.stderr)
    if delta_tokens > 0:
        print(f"這次請求：約 {delta_tokens:,} tokens", file=sys.stderr)


def main():
    data = json.load(sys.stdin)
    event = data.get("hook_event_name", "")

    if event == "UserPromptSubmit":
        handle_user_prompt_submit(data)
    elif event == "Stop":
        handle_stop(data)

    sys.exit(0)


if __name__ == "__main__":
    main()
```

**設定：**
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/context-tracker.py\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/context-tracker.py\""
          }
        ]
      }
    ]
  }
}
```

**運作原理：**
1. `UserPromptSubmit` 會在你的提示詞被處理前觸發 — 儲存目前的 token 數量
2. `Stop` 會在 Claude 回應後觸發 — 計算差值並回報用量
3. 每個工作階段都透過暫存檔名中的 `session_id` 互相隔離

**Token 計算方式：**

| 方法 | 準確度 | 相依套件 | 速度 |
|--------|----------|--------------|-------|
| 字元估算 | 約 80-90% | 無 | <1ms |
| tiktoken（p50k_base） | 約 90-95% | `pip install tiktoken` | <10ms |

> **備註：** Anthropic 尚未釋出官方的離線 tokenizer。這兩種方法都只是近似值。對話紀錄檔包含使用者的提示詞、Claude 的回應與工具輸出，但**不**包含系統提示詞或內部上下文。

### 範例 7：種子化自動模式權限（一次性設定腳本）

這是一支一次性的設定腳本，會把約 67 條安全的權限規則寫入 `~/.claude/settings.json`，等同於 Claude Code 自動模式的基準設定 — 不需要任何 hook，也不需要記住未來的選擇。只要執行一次；重複執行也是安全的（已存在的規則會被略過）。

**檔案：** `09-advanced-features/setup-auto-mode-permissions.py`

```bash
# 預覽將會新增的內容
python3 09-advanced-features/setup-auto-mode-permissions.py --dry-run

# 套用
python3 09-advanced-features/setup-auto-mode-permissions.py
```

**會新增的內容：**

| 類別 | 範例 |
|----------|---------|
| 內建工具 | `Read(*)`, `Edit(*)`, `Write(*)`, `Glob(*)`, `Grep(*)`, `Agent(*)`, `WebSearch(*)` |
| Git 讀取 | `Bash(git status:*)`, `Bash(git log:*)`, `Bash(git diff:*)` |
| Git 寫入（本機） | `Bash(git add:*)`, `Bash(git commit:*)`, `Bash(git checkout:*)` |
| 套件管理工具 | `Bash(npm install:*)`, `Bash(pip install:*)`, `Bash(cargo build:*)` |
| 建置與測試 | `Bash(make:*)`, `Bash(pytest:*)`, `Bash(go test:*)` |
| 常用 shell 指令 | `Bash(ls:*)`, `Bash(cat:*)`, `Bash(find:*)`, `Bash(cp:*)`, `Bash(mv:*)` |
| GitHub CLI | `Bash(gh pr view:*)`, `Bash(gh pr create:*)`, `Bash(gh issue list:*)` |

**刻意排除的項目**（此腳本絕不會新增）：
- `rm -rf`、`sudo`、強制推送（force push）、`git reset --hard`
- `DROP TABLE`、`kubectl delete`、`terraform destroy`
- `npm publish`、`curl | bash`、正式環境部署

### 範例 8：學習進度記錄器（SessionEnd）

在每次 Claude Code 工作階段結束時，記錄你這次學習了哪些模組。進度會儲存
在 `~/.claude-code-tutorial-progress.json` — 位於 repo 之外，所以能在
`git pull` 之後留存，不會被覆寫。

**為什麼用 `SessionEnd` 而不是 `Stop`？**
`Stop` 會在 Claude *每一次*回應後觸發。`SessionEnd` 只會在工作階段
結束時觸發一次 — 這正好適合用來寫「工作階段結束日誌」。

**為什麼輸入要用 `/dev/tty`？**
Hook 腳本是透過 `stdin` 接收 hook 的 JSON payload，所以互動式的 `read`
必須直接使用 `/dev/tty` 才能連到終端機。

**檔案：** `06-hooks/session-end.sh`

```bash
#!/usr/bin/env bash
# SessionEnd hook：詢問這次處理了哪些模組，然後把工作階段記錄
# 附加到 ~/.claude-code-tutorial-progress.json，用來持續追蹤學習進度。

PROGRESS_FILE="$HOME/.claude-code-tutorial-progress.json"

# 防護：只在此 repo 內執行
if [[ "$CLAUDE_PROJECT_DIR" != *"claude-code-tutorial"* ]] && [[ "$PWD" != *"claude-code-tutorial"* ]]; then
  exit 0
fi

if [ ! -f "$PROGRESS_FILE" ]; then
  echo '{"sessions":[]}' > "$PROGRESS_FILE"
fi

DATE=$(date +"%Y-%m-%d")
TIME=$(date +"%H:%M")

echo ""
echo " 你這次處理了哪些模組？（例如 06,07，直接按 Enter 可跳過）"
echo " 01=斜線指令  02=記憶  03=技能  04=子代理  05=MCP"
echo " 06=Hooks  07=外掛 08=檢查點 09=進階功能 10=CLI"
printf " > "
read -r INPUT </dev/tty

if [ -z "$INPUT" ] || [ "$INPUT" = "skip" ]; then
  exit 0
fi

MODULES_JSON=$(echo "$INPUT" | tr ',' '\n' | tr -d ' ' | while read -r m; do
  case "$m" in
    01) echo '"01-slash-commands"' ;;
    02) echo '"02-memory"' ;;
    03) echo '"03-skills"' ;;
    04) echo '"04-subagents"' ;;
    05) echo '"05-mcp"' ;;
    06) echo '"06-hooks"' ;;
    07) echo '"07-plugins"' ;;
    08) echo '"08-checkpoints"' ;;
    09) echo '"09-advanced-features"' ;;
    10) echo '"10-cli"' ;;
    *)  echo "\"$m\"" ;;
  esac
done | paste -sd ',' -)

printf " 備註？（選填，按 Enter 可跳過）："
read -r NOTES </dev/tty

# 把 NOTES 當成獨立參數傳入，讓 Python 處理 JSON 跳脫 —
# 避免 notes 內含引號或反斜線時破壞 JSON 格式。
python3 - "$PROGRESS_FILE" "$DATE" "$TIME" "$MODULES_JSON" "$NOTES" <<'PYEOF'
import sys, json

path, date, time_str, modules_raw, notes = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]

new_session = {
    "date": date,
    "time": time_str,
    "modules": json.loads(f"[{modules_raw}]") if modules_raw else [],
    "notes": notes,
}

with open(path, 'r') as f:
    data = json.load(f)

data.setdefault('sessions', []).append(new_session)

with open(path, 'w') as f:
    json.dump(data, f, indent=2)
PYEOF

echo " 已儲存到 $PROGRESS_FILE"
```

**安裝** — 把腳本複製到專案的 hook 目錄，這樣 `settings.json` 裡的路徑才能正確解析：

```bash
mkdir -p .claude/hooks
cp 06-hooks/session-end.sh .claude/hooks/
chmod +x .claude/hooks/session-end.sh
```

**設定**（於 `.claude/settings.json` 中）：

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/session-end.sh\""
          }
        ]
      }
    ]
  }
}
```

**輸出 — `~/.claude-code-tutorial-progress.json`：**

```json
{
  "sessions": [
    {
      "date": "2026-04-18",
      "time": "14:32",
      "modules": ["06-hooks", "07-plugins"],
      "notes": "安裝了第一個 hook，試用了 pre-commit 範例"
    }
  ]
}
```

**這裡示範的關鍵模式：**

| 模式 | 為什麼重要 |
|---------|----------------|
| `SessionEnd` 事件 | 只在結束時觸發一次 — 不像 `Stop` 那樣每次回應都觸發 |
| `read -r INPUT </dev/tty` | Hook 本身佔用了 `stdin`（JSON payload）；使用者輸入要靠 `/dev/tty` |
| `$CLAUDE_PROJECT_DIR` | 可攜的路徑 — 絕不要寫死 `/Users/yourname/...` |
| 開頭的防護判斷 | 避免此 hook 若被全域安裝時，在不相關的專案中執行 |
| 儲存在 repo 之外 | `~/` 路徑能在 `git pull` 後留存，不會覆寫你的資料 |

**搭配工具：視覺化進度追蹤器**

若要有涵蓋全部 10 個模組、以核取方塊呈現的完整介面，可在瀏覽器中開啟內建的追蹤工具：

```bash
open local-progress/index.html
```

進度會儲存在瀏覽器的 `localStorage` 中（絕不會寫入 repo 內的磁碟檔案）。
用 **Export** 按鈕把快照存成 JSON，用 **Import** 還原它。

## 外掛 Hooks

外掛可以在自己的 `hooks/hooks.json` 檔案中放入 Hooks：

**檔案：** `plugins/hooks/hooks.json`

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/scripts/validate.sh"
          }
        ]
      }
    ]
  }
}
```

**外掛 Hooks 中的環境變數：**
- `${CLAUDE_PLUGIN_ROOT}` - 外掛目錄的路徑
- `${CLAUDE_PLUGIN_DATA}` - 外掛資料目錄的路徑

這讓外掛可以放入自訂的驗證與自動化 Hooks。

## MCP 工具 Hooks

MCP 工具遵循 `mcp__<server>__<tool>` 的命名模式：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__memory__.*",
        "hooks": [
          {
            "type": "command",
            "command": "echo '{\"systemMessage\": \"已記錄記憶操作\"}'"
          }
        ]
      }
    ]
  }
}
```

## 安全性考量

### 免責聲明

**風險自負**：Hooks 會執行任意的 shell 指令。你必須自行負責：
- 你設定的指令內容
- 檔案存取／修改權限
- 潛在的資料遺失或系統損害
- 在正式環境使用前，先在安全的環境中測試 hooks

### 安全性備註

- **需要工作區信任：** `statusLine` 與 `fileSuggestion` 這兩種 hook 輸出指令，現在必須先接受工作區信任才會生效。
- **狀態列的終端機尺寸（v2.1.153）：** 狀態列指令腳本現在會收到 `COLUMNS` 與 `LINES` 環境變數，讓腳本可以依終端機的寬高調整輸出（例如 `[ "$COLUMNS" -lt 80 ] && short_output`）。
- **HTTP hooks 與環境變數：** HTTP hooks 若要在 URL 中做環境變數插值，必須明確指定 `allowedEnvVars` 清單。這能避免機密環境變數不小心外洩到遠端端點。
- **受管設定的階層：** `disableAllHooks` 設定現在會遵循受管設定的階層，也就是說組織層級的設定可以強制停用 hooks，個別使用者無法覆寫。
- **PowerShell 自動核准（v2.1.119）：** PowerShell 工具指令現在也能在權限模式中自動核准，與 Bash 一致。這讓在 Windows 上以 PowerShell 為底層 shell 工具執行 Claude Code 的使用者，享有和其他平台相同的待遇。
- **關閉 Bash 純環境變數自動核准的漏洞（v2.1.145）：** 在 v2.1.145 之前，形如 `FOO=bar somecommand` 的 Bash 指令（一個未在允許清單中的指令，前面接了單純的變數指派）只要 `FOO=bar` 本身在允許清單中，就可能被自動核准。v2.1.145 修補了這個漏洞 — 這類指令現在會觸發權限提示。原本依賴這個隱含允許的腳本會開始跳出提示；請改用涵蓋完整指令（而不只是變數指派）的 `Bash(...)` 權限規則，明確重新允許它們。

### 最佳實踐

| 建議做法 | 避免做法 |
|-----|-------|
| 驗證並清理所有輸入 | 盲目信任輸入資料 |
| 為 shell 變數加上引號：`"$VAR"` | 不加引號使用：`$VAR` |
| 阻擋路徑穿越（`..`） | 允許任意路徑 |
| 搭配 `$CLAUDE_PROJECT_DIR` 使用絕對路徑 | 寫死路徑 |
| 略過機密檔案（`.env`、`.git/`、金鑰） | 處理所有檔案 |
| 先在隔離環境測試 hooks | 部署未經測試的 hooks |
| 對 HTTP hooks 明確使用 `allowedEnvVars` | 把所有環境變數都暴露給 webhook |

## 除錯

### 啟用除錯模式

用除錯旗標執行 Claude，取得詳細的 hook 記錄：

```bash
claude --debug
```

### 詳細模式

在 Claude Code 中按 `Ctrl+O` 可啟用詳細模式，觀察 hook 的執行進度。

### 獨立測試 Hooks

```bash
# 用範例 JSON 輸入測試
echo '{"tool_name": "Bash", "tool_input": {"command": "ls -la"}}' | python3 .claude/hooks/validate-bash.py

# 檢查 exit code
echo $?
```

## 完整設定範例

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/validate-bash.py\"",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/format-code.sh\"",
            "timeout": 30
          },
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/security-scan.py\"",
            "timeout": 10
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/validate-prompt.py\""
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/session-init.sh\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "在停止之前，先確認所有任務都已完成。",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

## Hook 執行細節

| 項目 | 行為 |
|--------|----------|
| **逾時** | command/http/mcp_tool 預設 600 秒（prompt 為 30 秒，agent 為 60 秒）；可依 hook 個別設定 |
| **平行化** | 所有符合條件的 hooks 會平行執行 |
| **去重複** | 相同的 hook 指令會被去除重複 |
| **執行環境** | 在目前目錄中執行，並沿用 Claude Code 的環境 |

## 疑難排解

### Hook 沒有執行
- 確認 JSON 設定語法正確
- 檢查 matcher 模式是否符合工具名稱
- 確認腳本存在且可執行：`chmod +x script.sh`
- 執行 `claude --debug` 查看 hook 的執行記錄
- 確認 hook 是從 stdin 讀取 JSON（而不是指令引數）

### Hook 意外阻擋
- 用範例 JSON 測試 hook：`echo '{"tool_name": "Write", ...}' | ./hook.py`
- 檢查 exit code：允許應為 0，阻擋應為 2
- 檢查 stderr 輸出（exit code 2 時會顯示）

### JSON 解析錯誤
- 一律從 stdin 讀取，而不是指令引數
- 使用正確的 JSON 解析方式（而非字串操作）
- 妥善處理缺漏的欄位

## 安裝

### 步驟 1：建立 Hooks 目錄
```bash
mkdir -p ~/.claude/hooks
```

### 步驟 2：複製範例 Hooks
```bash
cp 06-hooks/*.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/*.sh
```

### 步驟 3：在設定中設定
依照上面的 hook 設定，編輯 `~/.claude/settings.json` 或 `.claude/settings.json`。

## 相關概念

- **[檢查點（Checkpoints）與回溯](../08-checkpoints/)** - 儲存並還原對話狀態
- **[斜線指令](../01-slash-commands/)** - 建立自訂的斜線指令
- **[技能](../03-skills/)** - 可重複使用的自主能力
- **[子代理](../04-subagents/)** - 委派任務執行
- **[外掛](../07-plugins/)** - 打包好的擴充套件
- **[進階功能](../09-advanced-features/)** - 探索 Claude Code 的進階能力

## 想直接演一次？有一個設好的專案

講 hook 最有效的方式是**當場擋一次給人看**，但那需要一個 `.claude/` 已經設好、
而且擋下來的時候「看得出它在擋什麼」的專案。

[`agenticRAG-ClaudeAgentSDK`](https://github.com/kevin801221/agenticRAG-ClaudeAgentSDK)
的 `.claude/` 掛了五個示範用的 hook，clone 下來 `claude` 一開就會動：

| 腳本 | 事件 | 一行觸發 | 學生會看到 |
|---|---|---|---|
| `session-start.sh` | SessionStart | 打 `claude` | 開場就知道分支、索引有幾個片段 |
| `prompt-context.sh` | UserPromptSubmit | 問「現在幾點？」 | 它不用跑 `date` 就答得出來 |
| `guard-secrets.sh` | PreToolUse | 「把 key 寫進 `.env`」 | **當場被擋**，而且 Claude 自己說改寫去 `.env.example` |
| `audit-bash.sh` | PostToolUse | 叫它 `ls` | `.claude/logs/bash-audit.log` 多一筆 |
| `done.sh` | Stop | 任何一輪結束 | 叮一聲 + 改了幾個檔 |

那個專案本身是 Agentic RAG 的教材，所以還能接著講**同一個概念的另一層**：
它的 `modules.py` 用 Agent SDK 的 `HookMatcher` 做 `PreToolUse`/`PostToolUse`，
網頁上那條一格一格亮起來的流程圖就是這樣畫出來的。

| | Claude Code CLI | Claude Agent SDK |
|---|---|---|
| 設定在哪 | `.claude/settings.json` | `ClaudeAgentOptions(hooks=...)` |
| hook 是什麼 | 一支外部腳本 | 一個 async 函式 |
| 怎麼收輸入 | stdin 拿 JSON | 參數 `(data, tool_use_id, context)` |
| 怎麼擋 | `exit 2` | 回傳阻止用的 dict |

> **教學金句**：「你在 CLI 學的 hook，換個寫法就是 SDK 的 hook。」

帶課譜看 [`docs/walkthroughs/hook_walkthrough.md`](../docs/walkthroughs/hook_walkthrough.md)（90 分鐘完整版）。

## 延伸資源

- **[官方 Hooks 文件](https://code.claude.com/docs/en/hooks)** - 完整的 hooks 參考
- **[CLI 參考](https://code.claude.com/docs/en/cli-reference)** - 命令列介面文件
- **[記憶（Memory）指南](../02-memory/)** - 持久化上下文設定

---

**最後更新**：2026 年 9 月 2 日
**Claude Code 版本**：2.1.257
**資料來源**：
- https://code.claude.com/docs/en/hooks
- https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md
- https://code.claude.com/docs/en/sub-agents
**相容模型**：Claude Fable 5、Claude Opus 5、Claude Sonnet 5、Claude Sonnet 4.6、Claude Opus 4.8、Claude Haiku 4.5
