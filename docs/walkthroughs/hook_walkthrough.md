# Claude Code Hooks — 從 0 到 7 個實戰場景的 Walkthrough

> **對象**：用過 Claude Code、但沒設定過 hook 的工程師
> **形式**：講師現場帶、學生跟著做
> **時長**：90 分鐘
> **產出**：7 個可進 git 的 hook 設定 + 一份「以後想加 hook 馬上能查」的對照手冊
> **核心方法**：每個 hook 都從「為什麼要這條規則」出發，再翻譯成 JSON

---

## 為什麼要寫這份 walkthrough？

`/agents` 是「教 Claude 幫你做事」，**hook 是「教 Claude Code 這個 harness（外殼）幫你做事」**。

兩者很容易混：

| 機制 | 由誰執行 | 何時跑 | 用來做什麼 |
|---|---|---|---|
| **Sub-agent** | Claude（AI） | 對話中被路由觸發 | 把任務交給專家 AI |
| **Skill** | Claude（AI） | 對話中按需引用 | 給 Claude 工作流程或知識 |
| **Slash command** | Claude（AI） | 使用者打 `/xxx` | 把長 prompt 模板化 |
| **Hook** ⭐ | **你的 shell**（不是 AI） | **特定事件**自動觸發 | 加規則、加自動化、加安全邊界 |

> **教學金句**：「Hook 不會問 Claude 同意才跑——它是『管 Claude 的 harness』。你寫的是 shell 指令，不是 prompt。」

---

## 🧠 30 秒釘穩心智模型（讀不下去全文，至少看這節）

### Hook 一句話

> **「當 X 發生時，幫我跑 Y 這條指令」**

就這樣。其他細節都是這句話的衍生。

### 用門鈴想

| 真實世界 | Claude Code |
|---|---|
| 有人按門鈴（**事件**） | 對話結束、Claude 用工具…（**事件**） |
| 鈴聲響起（**動作**） | 跑你寫的 shell 腳本（**動作**） |
| 門鈴接的那條線（**接線**） | `settings.json` 裡的 hook 設定（**接線**） |

**沒接線 = 不會響。** 預設狀態 Claude Code 一個 hook 都沒有，安安靜靜。

### 三個檔案各自做什麼

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│   .claude/settings.json                                 │
│   ─────────────────────────                             │
│   「規則本」：事件 X → 跑 Y 腳本                          │
│                                                         │
│              ↓ 指向                                      │
│                                                         │
│   .claude/hooks/*.sh                                    │
│   ─────────────────────────                             │
│   「腳本本身」：實際被執行的 shell                          │
│                                                         │
│              ↓ 可能會用到                                 │
│                                                         │
│   .claude/sounds/*.wav  或  .claude/data/...            │
│   ─────────────────────────                             │
│   「素材」：腳本會讀的檔案（音效、模板、設定…）              │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**重點**：`.claude/hooks/` 放 100 個 `.sh` 也沒用，只有被 `settings.json` 點名的才會跑。

### 觸發流程（背一次就懂）

```
   你在用 Claude Code
         │
         │  發生某件事（例如：對話結束）
         ▼
   ┌────────────────────────┐
   │  Claude Code 查        │
   │  settings.json         │
   │  有沒有對應的 hook？     │
   └─────────┬──────────────┘
             │
       ┌─────┴─────┐
       │           │
      沒有          有
       │           │
       ▼           ▼
    什麼都    跑指定的 .sh 腳本
    不發生    （叮咚、發通知、存 log…）
```

### 這份 walkthrough 是「食譜書」，不是「套餐」⭐⭐⭐

下面 Phase 1–7 是**七道菜的食譜**，不是「全部都要裝」的 checklist：

| Phase | 場景 | 適合誰 |
|---|---|---|
| 1 | 自動 ruff format | 寫 Python 的人 |
| 2 | 擋 .env / *.pem | 想多一層安全 |
| 3 | 完成叮咚 ✅ | **本 repo 預設裝這個** |
| 4 | 注入 git branch context | 多人協作 |
| 5 | Bash 指令存 audit.log | 想稽核 |
| 6 | SessionStart 載 TODO | 個人偏好 |
| 7 | sub-agent 完成發 Telegram | 跑長任務 |

**只裝你會用到的。不裝 = 完全沒影響。**

### Hook ≠ Permission（最容易混的點）

|  | Hook | Permission |
|---|---|---|
| 在管什麼 | **事件 → 跑什麼** | **Claude 能不能用某工具/指令** |
| 誰執行 | 你的 shell | Claude Code harness（在 AI 動手前攔截） |
| 寫在哪 | `settings.json` 的 `hooks` 區塊 | `settings.json` 的 `permissions` 區塊 |
| 例子 | 對話結束 → 叮咚 | `Bash(rm -rf:*)` → 直接擋掉 |

兩者**住同一個檔案、互不干涉**。設 hook 不會被 permission 擋（因為 hook 是 shell 在跑，不是 Claude 在跑）。

### 一次性的「信任閘」

第一次開 Claude Code 進這個 repo 時，會跳一次：

> 「這個專案的 `settings.json` 裡有 hook，要信任嗎？」

選同意之後 hook 才會啟用。**這是 hook 專屬的安全機制，跟 permission 是兩回事。** 改了 `settings.json` 之後也會再問一次。

---

> 💡 **這節看完後，你應該能回答**：
> 1. 我的 `.claude/hooks/` 放了 5 個 `.sh`，會自動全部跑嗎？（不會，要 `settings.json` 點名）
> 2. Hook 是 AI 在跑嗎？（不是，是你電腦上的 shell）
> 3. Permission 能擋掉一個會 `rm -rf` 的 hook 嗎？（不行，permission 管的是 Claude，不管 hook）

答得出來，下面 Phase 0 細節就會輕鬆很多。

---

## 📁 你會用到的檔案

```
~/.claude/settings.json              # 全域 hook（所有專案共用）
<repo>/.claude/settings.json         # 專案 hook（進 git，團隊共用）⭐ 推薦
<repo>/.claude/settings.local.json   # 個人覆蓋（gitignore，不分享）
<repo>/.claude/hooks/                # 慣例：把 shell script 放這裡
```

> 三個檔案合併規則：local > project > user（local 蓋掉 project，project 蓋掉 user）。

---

## Phase 0：你必須先懂的 8 件事（15 分鐘，必聽）

### 1. Hook 是什麼？

**一句話**：在 `settings.json` 裡寫一條「當 X 事件發生時，跑 Y 這條 shell 指令」。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          { "type": "command", "command": "echo 'something changed'" }
        ]
      }
    ]
  }
}
```

這條翻成中文是：「每次 Claude 用 `Write` 或 `Edit` 之後，跑一次 `echo 'something changed'`」。

---

### 2. 8 個事件（背下這張表）

| 事件 | 何時觸發 | 你能拿到什麼 | 典型用途 |
|---|---|---|---|
| `SessionStart` | Claude Code 啟動 | session_id, cwd | 注入專案狀態、載入 TODO |
| `UserPromptSubmit` | 你按 Enter 送 prompt | prompt 內容 | 自動加 context（git branch 等） |
| `PreToolUse` | Claude 正要呼叫工具 | tool_name, tool_input | 阻止危險動作 ⚠️ |
| `PostToolUse` | Claude 工具呼叫完成 | tool_name, tool_input, tool_response | 格式化、lint、記 log |
| `Notification` | Claude 觸發系統通知 | message | 自訂叮咚聲 |
| `Stop` | Claude 整輪回應結束 | session_id | 任務完成通知 |
| `SubagentStop` | sub-agent 結束 | subagent name | sub-agent 跑完發 Slack |
| `PreCompact` | 上下文壓縮前 | transcript_path | 備份對話、寫日誌 |
| `SessionEnd` | session 關閉 | session_id | 清理、寫總結 |

> ⚠️ **記憶法**：事件名都用 PascalCase，跟 hook event 名一定要一字不差。

---

### 3. 三層設定檔

| 檔案 | 在哪 | 進 git？ | 適合放 |
|---|---|---|---|
| `~/.claude/settings.json` | 你的 home | ❌ | 個人偏好（音效、桌面通知） |
| `<repo>/.claude/settings.json` | 專案根 | ✅ | 團隊規則（不准寫 .env、格式化） |
| `<repo>/.claude/settings.local.json` | 專案根 | ❌（gitignore） | 個人臨時覆蓋 |

> 💡 **教學金句**：「**規則進 git，偏好留 home**。團隊要 enforce 的 hook 放 project；只有你愛的叮咚音放 user。」

---

### 4. hook JSON 結構（最容易卡的一段）

```json
{
  "hooks": {
    "<事件名>": [
      {
        "matcher": "<正則或工具名>",
        "hooks": [
          {
            "type": "command",
            "command": "<你的 shell 指令>",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

注意 **3 層 hooks**：
- 外層 `hooks` 是 settings.json 的頂層 key
- 中層 `"PostToolUse": [...]` 是事件名 → 陣列
- 內層 `"hooks": [...]` 是真正要跑的指令陣列

> ⚠️ **常見錯誤**：把內層 `hooks:` 寫成 `commands:` 或漏掉一層，hook 就完全不會跑也不會報錯。

---

### 5. matcher 怎麼寫

| 事件 | matcher 比的是 | 範例 |
|---|---|---|
| `PreToolUse` / `PostToolUse` | 工具名（regex） | `"Write\|Edit"`、`"Bash"`、`".*"` |
| `UserPromptSubmit` / `Stop` / `SessionStart` | 通常省略或 `""` | 全部都觸發 |
| `Notification` | 通知類型 | `"permission"` |

工具名清單（最常用）：
```
Read, Write, Edit, NotebookEdit, Bash, Glob, Grep,
WebFetch, WebSearch, Task, TodoWrite, ...
```

---

### 6. 從 stdin 拿到什麼

每個 hook 被呼叫時，**Claude Code 會把一坨 JSON 從 stdin 餵給你的指令**。

範例（`PostToolUse` for `Write`）：

```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../conversation.jsonl",
  "cwd": "/Users/.../my-project",
  "hook_event_name": "PostToolUse",
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/abs/path/foo.py",
    "content": "..."
  },
  "tool_response": { "success": true }
}
```

讀法：

```bash
# 用 jq
cat | jq -r '.tool_input.file_path'

# 用 python
python3 -c "import json,sys; d=json.load(sys.stdin); print(d['tool_input']['file_path'])"
```

---

### 7. exit code 的三種意義 ⭐⭐⭐

這是 hook **最重要的一頁**。

| exit code | 行為 | Claude 看得到什麼 |
|---|---|---|
| **0** | 通過 | stdout（選擇性顯示） |
| **2** | **阻止**（blocking） | **stderr 內容被當作錯誤訊息餵回 Claude** ⭐ |
| 其他非 0 | 非阻止錯誤 | 在使用者介面顯示警告，但 Claude 不會中斷 |

> 💡 **教學金句**：「`exit 2` 是你跟 Claude 對話的**唯一直接管道**——你用 stderr 寫話，Claude 真的會讀到並改行為。其他 exit code 都是『悄悄話』。」

**簡單示意**：

```bash
#!/usr/bin/env bash
if [[ "$file" == *.env ]]; then
  echo "⛔ 不准動 .env 檔案，請改 .env.example" >&2
  exit 2
fi
exit 0
```

Claude 收到後會看到那行中文，自動換做法。

---

### 8. 安全注意（5 條，必講）

1. **Hook 用你的 user 權限跑**：能 `rm -rf`、能 `curl | bash`、能寫你的 home。**比 sudo 弱、但比 sandbox 強很多**。
2. **永遠不要 source 別人的 settings.json**：clone 別人專案，先看 `.claude/settings.json` 有沒有可疑指令。
3. **別把機密 echo 出來**：hook 的 stdout 可能被 transcript 記錄。
4. **timeout 一定要設**：跑卡住的指令會擋住整個 Claude Code。預設 60 秒，建議 5–30 秒。
5. **Hook 是 shell，PATH 很重要**：Claude Code 從 GUI 啟動時可能少 PATH。指令請用絕對路徑或在 hook 開頭 `export PATH="$HOME/.local/bin:$PATH"`。

---

### Phase 0 收尾驗證

打開終端：

```bash
# 看看你目前有沒有 hook
cat ~/.claude/settings.json 2>/dev/null | jq '.hooks // {}'
cat .claude/settings.json 2>/dev/null | jq '.hooks // {}'
```

如果都是空的 `{}` 就對了，我們等下從零開始。

---

## Phase 1：你的第一個 hook ⭐ 全課最重要 25 分鐘

### 目標：「Write/Edit 完 .py 後自動印一行提示」

最簡單、最不會壞、最有感的 hook。

---

### Step 1：建設定檔

```bash
cd /Users/kevinluo/claude-code-complete-tutorial/agent_group_projects/computer-vision-wafer-agents-detection-demo
mkdir -p .claude
```

> 💡 已有 `.claude/agents/`？沒關係，`.claude/` 底下可以同時放 agents/、settings.json、hooks/。

---

### Step 2：寫第一個 settings.json

用編輯器打開 `.claude/settings.json`，貼：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "echo '✅ hook fired'",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

存檔。

---

### Step 3：重啟 Claude Code

> ⚠️ **必踩坑警告**：改 `settings.json` 後**一定要重啟 Claude Code**（`/exit` 然後重開），現有 session 不會自動 reload。

---

### Step 4：觸發看看

在 Claude Code 對話框：

```
幫我建一個 hello.txt 寫 "hi"
```

Claude 用 `Write` 工具寫完之後，你會在 Claude Code UI 看到 hook 的 stdout：

```
✅ hook fired
```

> 💡 **教學金句**：「這就是 hook 的最小可運作版（MVP）。**6 行 JSON、1 條 echo、Claude Code 重啟一次**——你已經會用 hook 了。」

---

### Step 5：升級——只在 .py 檔觸發

當前 hook 對所有 Write/Edit 都跑。我們改成「只在 Python 檔」：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 -c \"import json,sys; d=json.load(sys.stdin); p=d.get('tool_input',{}).get('file_path',''); print(f'🐍 改了 {p}') if p.endswith('.py') else None\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

**這條做了什麼？**
- matcher 還是 `Write|Edit`（hook 還是會跑）
- 但**指令內部**讀 stdin JSON，只有 `.py` 才印
- 不是 `.py` 時，指令安靜結束（exit 0，無 stdout）

> 💡 **教學金句**：「matcher 是粗篩，指令內部邏輯是細篩。**hook 是程式**，不是宣告。」

---

### Step 6：升級到「自動 ruff format」

如果你有裝 `ruff`（`uv tool install ruff`），把指令改成：

```bash
.claude/hooks/format-python.sh
```

```bash
#!/usr/bin/env bash
set -e

# 從 stdin 拿 JSON
payload=$(cat)
file_path=$(echo "$payload" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))")

# 只處理 .py
[[ "$file_path" == *.py ]] || exit 0

# 跑 ruff format
ruff format "$file_path" 2>&1
echo "🎨 ruff formatted: $file_path"
```

記得 `chmod +x .claude/hooks/format-python.sh`。

`settings.json` 改成：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/format-python.sh",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

> ⚠️ `$CLAUDE_PROJECT_DIR` 是 Claude Code 注入的環境變數，指向專案根。避免寫相對路徑（`cwd` 不一定對）。

重啟 Claude Code，叫它改個 `.py` 檔，會看到 ruff 自動格式化。

---

### 🎉 你做了什麼

- 從 6 行 JSON 開始
- 升級到能讀 stdin、條件分流、呼叫外部工具
- **這就是 hook 的所有套路**——剩下都是「換事件、換指令、換 exit code」

---

## Phase 2：阻止性 hook（PreToolUse + exit 2）⭐ 安全邊界

### 目標：「禁止 Claude 寫 .env / credentials / *.pem」

這條規則不是給 AI 看（AI 可能忘），是給 harness 看（永遠不會忘）。

---

### Step 1：寫 guard 腳本

`.claude/hooks/guard-secrets.sh`：

```bash
#!/usr/bin/env bash
set -e

payload=$(cat)
file_path=$(echo "$payload" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))")

# 黑名單
patterns=(
  ".env"
  ".env.local"
  ".env.production"
  "credentials.json"
  "service-account.json"
  "*.pem"
  "*.key"
  "id_rsa"
  "id_ed25519"
)

basename_file=$(basename "$file_path")
for pat in "${patterns[@]}"; do
  if [[ "$basename_file" == $pat ]]; then
    cat >&2 <<EOF
⛔ 拒絕寫入敏感檔案：$file_path

理由：這個路徑在專案的 secrets 黑名單中。
建議：
  - 改寫 .env.example（不含真值）
  - 或叫使用者手動編輯
  - 若真的需要寫，請使用者把這條 hook 暫時關掉
EOF
    exit 2
  fi
done

exit 0
```

`chmod +x`。

---

### Step 2：掛到 settings.json

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/guard-secrets.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

---

### Step 3：測試

重啟 Claude Code，打：

```
寫一個 .env 檔，內容 API_KEY=abc
```

預期看到：
- Claude 嘗試 `Write`
- hook 攔下、回傳 `exit 2`
- Claude 收到 stderr 的中文訊息
- Claude **自動改寫策略**，例如改去寫 `.env.example`

> 💡 **教學金句**：「Hook + exit 2 = 你寫給 Claude 的『鐵則』。比 prompt engineering 強很多，因為 prompt 會被忘、規則不會。」

---

### 教學重點

- `PreToolUse` 才能阻止；`PostToolUse` 拿到的已經是寫完的事實
- exit 2 的 stderr **必須是給 AI 看的人話**，不是給人類的 log
- 阻止性 hook 一定要短、快、不依賴網路

---

## Phase 3：完成通知（Stop hook）

### 目標：「Claude 一輪做完叮咚一聲 + 桌面通知」

長任務跑完你早跑去喝咖啡，叮咚一下叫你回來。

---

### macOS 版本

`.claude/hooks/notify-done.sh`：

```bash
#!/usr/bin/env bash

# 系統聲音
afplay /System/Library/Sounds/Glass.aiff &

# 桌面通知（macOS）
osascript -e 'display notification "任務完成 ✅" with title "Claude Code" sound name "Glass"'

exit 0
```

`chmod +x`。

`settings.json`（**這條建議放 `~/.claude/settings.json`，因為這是個人偏好，不該強塞給團隊**）：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/.claude/hooks/notify-done.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

> 💡 注意：`Stop` 不需要 matcher，每次 Claude 結束都觸發。

---

### macOS 進階版：用 repo 內建的 `.wav`（推薦給教學 / 團隊）

> **教學金句**：「個人版用系統音檔最快，但要讓**整個團隊或一份教學 repo**都聽到同一聲叮咚，就把 `.wav` 也 commit 進去——hook 不該依賴別人電腦上剛好存在的檔案。」

上面那版的 `afplay /System/Library/Sounds/Glass.aiff` 有兩個小缺點：

1. **依賴系統路徑**——換到別的 OS、別台機器，那個檔案不一定在
2. **每個人聽到的音色可能被改過**——macOS 系統音可以被使用者替換掉

解法：把音檔做成 `.wav`，放在 repo 的 `.claude/sounds/` 裡，hook 用 `$CLAUDE_PROJECT_DIR` 指過去。

#### Step 1：把系統音轉成 `.wav`

`macOS` 內建 `afconvert`，挑你喜歡的系統音（`Glass` / `Tink` / `Pop` / `Hero`）轉檔：

```bash
mkdir -p .claude/sounds
afconvert -f WAVE -d LEI16@44100 \
  /System/Library/Sounds/Glass.aiff \
  .claude/sounds/notify.wav
```

驗證：

```bash
file .claude/sounds/notify.wav
# → RIFF (little-endian) data, WAVE audio, Microsoft PCM, 16 bit, stereo 44100 Hz

afplay .claude/sounds/notify.wav   # 聽聽看
```

> 想自製音效？也可以從任何 `.aiff` / `.mp3` / `.m4a` 轉，`afconvert -f WAVE -d LEI16@44100 <input> <output>.wav` 通殺。

#### Step 2：改寫 `notify-done.sh`，指向 repo 內的檔

`.claude/hooks/notify-done.sh`：

```bash
#!/usr/bin/env bash

# 用 repo 內建的 wav，路徑由 Claude Code 注入
afplay "$CLAUDE_PROJECT_DIR/.claude/sounds/notify.wav" &

# 桌面通知（這條跟系統還是有點依賴，但 osascript 在 macOS 一定有）
osascript -e 'display notification "任務完成 ✅" with title "Claude Code"'

exit 0
```

`chmod +x .claude/hooks/notify-done.sh`。

#### Step 3：掛到**專案** `settings.json`

注意這次不放 `~/.claude/`，放 **`<repo>/.claude/settings.json`**（會進 git，團隊共用）：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/notify-done.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

#### Step 4：重啟 Claude Code，叫它跑任何一個任務，結束時應該叮咚

#### 教學重點

- `$CLAUDE_PROJECT_DIR` 是 Claude Code 在執行 hook 時注入的環境變數，指到專案根目錄；用它就能寫出**「在誰電腦上都對的路徑」**
- `.wav` 進 git 後，整個團隊 / 整份教學 repo 拿到的都是同一聲音
- 不想讓某些隊友收到通知？讓他們用 `.claude/settings.local.json`（gitignore）把這個 hook 關掉就好——這是 local override 的標準用法

---

### Linux 版本（用 notify-send）

```bash
#!/usr/bin/env bash
notify-send "Claude Code" "任務完成 ✅"
paplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null
```

---

### Windows / WSL 版本

```bash
#!/usr/bin/env bash
powershell.exe -c 'New-BurntToastNotification -Text "Claude Code", "任務完成 ✅"' 2>/dev/null
```

---

### 教學重點

- `Stop` 跟 `SubagentStop` 是不同事件——一個是主對話結束，一個是 sub-agent 結束
- 想兩個都通知，兩個事件都掛
- 聲音放背景（`&`）才不會 block timeout

---

## Phase 4：注入 context（UserPromptSubmit）

### 目標：「每次送 prompt 自動加當前 git branch + 改動檔數」

不用每次跟 Claude 解釋「現在在哪個 branch」。

---

### Step 1：寫 context 注入腳本

`.claude/hooks/inject-git-context.sh`：

```bash
#!/usr/bin/env bash

# 切到專案根
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

# 不是 git repo 就跳過
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

branch=$(git branch --show-current 2>/dev/null)
changed=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
ahead_behind=$(git status -sb 2>/dev/null | head -1)

# stdout 內容會被當作額外 context 注入到這次 prompt
cat <<EOF
<git-context>
branch: $branch
changed_files: $changed
status: $ahead_behind
</git-context>
EOF

exit 0
```

`chmod +x`。

---

### Step 2：掛上去

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/inject-git-context.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

---

### Step 3：測試

重啟 Claude Code，隨便打個 prompt，Claude 就會自動收到 `<git-context>` 區塊。

> 💡 **教學金句**：「`UserPromptSubmit` 的 stdout = 額外 context。你寫多少 Claude 就收多少。**這是給 Claude『環境感知』的最便宜方法**。」

---

### 注意

- 不要 stdout 太多（會吃 token）
- 別把機密 echo 進去（會進 transcript）
- 用 XML-ish tag 包起來方便 Claude 識別

---

## Phase 5：審計記錄（PostToolUse Bash）

### 目標：「所有 Bash 指令都記到 audit.log」

合規場景、或單純想知道 Claude 都跑了什麼。

---

`.claude/hooks/audit-bash.sh`：

```bash
#!/usr/bin/env bash

payload=$(cat)
cmd=$(echo "$payload" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# 寫到專案根的 .audit.log（記得 gitignore）
echo "[$ts] [$CLAUDE_PROJECT_DIR] $cmd" >> "$CLAUDE_PROJECT_DIR/.audit.log"
exit 0
```

`settings.json`：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/audit-bash.sh",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

別忘了：

```bash
echo ".audit.log" >> .gitignore
```

---

## Phase 6：SessionStart 載入專案狀態

### 目標：「開 Claude Code 自動帶入今日 TODO」

每次進 session 不用再貼一遍「我們之前做到哪」。

---

`.claude/hooks/session-context.sh`：

```bash
#!/usr/bin/env bash

cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

cat <<EOF
<session-bootstrap>
專案：$(basename "$CLAUDE_PROJECT_DIR")
時間：$(date "+%Y-%m-%d %H:%M")
最近 3 個 commit：
$(git log --oneline -3 2>/dev/null || echo "  (not a git repo)")

當前 TODO（從 _Context/TODO.md）：
$(cat _Context/TODO.md 2>/dev/null | head -10 || echo "  (no TODO file)")
</session-bootstrap>
EOF

exit 0
```

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/session-context.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

---

### 教學重點

- `SessionStart` 的 stdout 是「Claude 一進來就讀到的東西」
- 內容越精煉越好——這是**每次開 session 都會付的 token 成本**

---

## Phase 7：SubagentStop 通知 ⭐ 結合本專案

### 目標：「training-runner 跑完發 Telegram / Slack / 桌面通知」

訓練要 10 分鐘，你拿來等不如自動通知。

---

### macOS 桌面通知版

`.claude/hooks/notify-subagent.sh`：

```bash
#!/usr/bin/env bash

payload=$(cat)
agent=$(echo "$payload" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('subagent_name', d.get('tool_input',{}).get('subagent_type','unknown')))" 2>/dev/null)

# 只在訓練/推論這類耗時 agent 發通知（自行調整）
case "$agent" in
  training-runner|inference-runner)
    osascript -e "display notification \"$agent 完成 ✅\" with title \"Claude Code\" sound name \"Glass\""
    ;;
esac

exit 0
```

```json
{
  "hooks": {
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/notify-subagent.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

---

### 升級：發 Telegram

如果你有 Telegram bot，把通知改成：

```bash
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d "chat_id=${TELEGRAM_CHAT_ID}" \
  -d "text=🤖 ${agent} 完成"
```

記得 `TELEGRAM_BOT_TOKEN` 從 `.env` 讀進來，**不要 hardcode**。

---

## 整合 demo：跑一次完整流程

把 Phase 1–7 全部開啟，叫 Claude：

```
幫我重訓 wafer 模型
```

預期看到（依序）：
1. **SessionStart**（如果是新 session）→ 注入 TODO
2. **UserPromptSubmit** → 注入 git branch
3. Claude 呼叫 `training-runner` sub-agent
4. **PreToolUse(Bash)** → guard 檢查（沒問題就過）
5. sub-agent 跑訓練
6. **PostToolUse(Bash)** → audit log 記指令
7. **SubagentStop** → 桌面通知「training-runner 完成」
8. Claude 整輪結束
9. **Stop** → 叮咚一聲

**7 個 hook 同時 work，0 行 prompt 改動。**

---

## 卡點對照表 ⭐ 學生最常踩的坑

| 卡點 | 真實原因 | 處理 |
|---|---|---|
| Hook 完全不跑也不報錯 | 多半是路徑或執行權限，**不是沒重啟** | `settings.json` 的改動有 file watcher 在看，不用重開。先手動 `echo '{}' \| .claude/hooks/x.sh` 試一次，再 `chmod +x` |
| 改了 **plugin / skill** 的 hook 沒生效 | 那類的生命週期跟 settings 不同 | plugin 的要重開；skill 的要等它下次被叫起來 |
| `unbound variable`，而且變數後面接著中文 | macOS 的 bash 會把全形字的位元組當成變數名的一部分 | 一律寫 `${var}` 不要寫 `$var` |
| Hook 找不到指令（`command not found`） | GUI 啟動的 Claude Code 缺 PATH | hook 開頭加 `export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"` |
| `exit 2` 沒讓 Claude 改行為 | 你 echo 到 stdout 而不是 stderr | 改成 `echo "..." >&2` |
| matcher 寫成 `"Write, Edit"` 不 work | matcher 是 regex 不是逗號清單 | 改成 `"Write\|Edit"`（pipe = OR） |
| `$CLAUDE_PROJECT_DIR` 是空的 | 你在 user 級 settings.json 用了它 | user 級 hook 用 `$HOME` 或絕對路徑 |
| hook 卡住 60 秒才繼續 | timeout 沒設、指令 hang | 一律設 `"timeout": 5`～`30` |
| `python3` 在 macOS 找不到 | 系統 Python 路徑問題 | 用絕對路徑 `/usr/bin/python3` 或 `which python3` 確認 |
| Hook 跑了但 Claude 沒看到 stdout | `UserPromptSubmit` 之外的事件 stdout 不會注入 prompt | 只有 `UserPromptSubmit` / `SessionStart` 的 stdout 會變 context |
| 改了 hook 學生跑得到、你跑不到 | 一個用 `~/.claude/settings.json`、一個用 project | 用 `cat .claude/settings.json` 確認載入位置 |
| Hook 在 Windows / WSL 跑出怪錯 | 換行符 CRLF | `dos2unix .claude/hooks/*.sh` |

---

## Debug 技巧（4 招）

### 1. 加 log file

```bash
echo "$(date) hook fired with payload: $(cat)" >> /tmp/hook-debug.log
```

開另一個 terminal `tail -f /tmp/hook-debug.log` 即時看。

### 2. 跑 Claude Code 加 `--debug`

```bash
claude --debug
```

會印出 hook 啟動、stdout、stderr、exit code。

### 3. 直接手動測 hook 腳本

```bash
echo '{"tool_input":{"file_path":"test.py"}}' | bash .claude/hooks/format-python.sh
echo "exit: $?"
```

### 4. 最小化還原法

hook 卡住 debug 不出來時，把 settings.json hook 全部刪掉，用最簡單的 `echo 'fired'` 重來，逐條加回去。

---

## 講師私房筆記 ⭐ 帶課時這樣講會更有效

### 1. 「為什麼是 shell 不是 Python？」

學生會問。答：**hook 要快、要簡單、要不依賴 Python 環境**。你可以在 hook 裡呼叫 Python，但 hook 本身用 bash 最穩。

### 2. 「Hook 跟 settings.json 其他設定有什麼關係？」

`settings.json` 不只放 hooks，還有 `permissions`、`env`、`model` 等。但本課只教 hooks，其他設定請看官方文件。

### 3. 「為什麼 `exit 2` 是 2 不是 1？」

Claude Code 故意挑 2 區分：
- 0 = 通過
- 1 = 一般錯誤（不阻止）
- **2 = 阻止（特殊語意）**
- 其他 = 非阻止錯誤

這跟一般 UNIX exit code 沒對應關係，是 Claude Code 自己定的協定。

### 4. 「Hook 可以呼叫 Claude API 嗎？」

可以，但**強烈不建議**：
- 每次 Write/Edit 都呼叫 API 會爆 token
- 比較好的做法是讓 Claude 自己當 evaluator agent，hook 只做機械性檢查

### 5. 「Hook 跟 plugin 是什麼關係？」

plugin 可以**內建 hooks**——你裝一個 plugin，plugin 的 settings.json 會把 hook 自動加到你的環境。要小心信任來源。

### 6. 你示範時會故意踩的坑（這比講更有效）

| 故意踩 | 教什麼 |
|---|---|
| 改完 settings.json 沒重啟，跑 hook，無反應 | 「永遠重啟」 |
| `exit 2` 但 echo 到 stdout，Claude 沒改行為 | stderr vs stdout |
| matcher 寫 `"Write, Edit"`，hook 不跑 | regex 不是清單 |
| 不設 timeout，跑一個 `sleep 30` 卡 30 秒 | 一律設 timeout |

---

## 一句話總結

> **Hook = 用 shell 在 Claude Code 的事件流上加規則。
> 規則進 git，偏好留 home，rule 用 exit 2，notification 用 stdout。**

---

## 進階閱讀

- [Claude Code Hooks 官方文件](https://docs.claude.com/en/docs/claude-code/hooks)（以官方為準，本份偶有過時）
- [Claude Code Settings 官方文件](https://docs.claude.com/en/docs/claude-code/settings)
- 進階場景：用 `superpowers:update-config` skill 讓 Claude 幫你改 settings.json

---

_Last updated: 2026-05-12_
_Maintainer: Kevin (kevin@legalsign.ai)_
_配套教材（同目錄）：`anthropics_marketplace_skills_walkthrough.md` + `four_skills_walkthrough.md`_
_專案實戰案例：`../../agent_group_projects/computer-vision-wafer-agents-detection-demo/WALKTHROUGH.md`（sub-agents）_
