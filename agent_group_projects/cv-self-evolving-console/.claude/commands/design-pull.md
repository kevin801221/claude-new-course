---
description: 把 Claude Design canvas 的視覺決策搬進 prototype/index.html —— 搬決策不搬程式碼，三道稽核全綠才算完成
argument-hint: [canvas 檔路徑或 artifact URL]（省略＝design-canvas/Main.dc.html）
allowed-tools: Read, Edit, Grep, Bash(grep:*), Bash(shasum:*), Bash(curl:*), Bash(uv run:*), Artifact
---

# /design-pull — canvas 的視覺決策 → 生產前端

## ⚠️ 前置條件：先設 `CV_ROLE=console-owner`，不然第一個 Edit 就被擋

這支指令從頭到尾只改一個檔：`prototype/index.html`。
而 `.claude/hooks/guard-ownership.sh` 規則 5（第 162–170 行）對**沒有 `CV_ROLE=console-owner`
的 session 一律 exit 2**。`settings.json` 只把這支 hook 掛上去、**沒有設任何 env**，
`CV_ROLE` 這個字在整個專案裡也只出現在那支 hook 檔裡 —— 沒有人會替你設好，你不設就是沒設。

| 怎麼開 session | 第一個 `Edit prototype/index.html` 會怎樣 |
|---|---|
| `claude`（沒設 `CV_ROLE`） | 🔒 被擋，exit 2，一個位元組都沒寫進去 |
| `CV_ROLE=console-owner claude`，或先 `export CV_ROLE=console-owner` 再 `claude` | 放行，exit 0 |

被擋的時候**不要去改 hook、也不要改用 Bash 的 `sed -i` / `cat >` 繞過去**：那是 hook 在做它該做的事
（紅線 1：前端只有一個擁有者），不是指令壞了。繞過去的代價是這條線對其他四位專家同時失效，
而你不會在當下發現 —— 會在整合當天發現 index.html 被四個人各改了一半。

動手前先自測。**在你自己的 terminal 跑**（唯讀，不寫任何檔，跟 session 無關）：

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"prototype/index.html"}}' \
  | .claude/hooks/guard-ownership.sh ; echo "exit=$? ／ CV_ROLE=${CV_ROLE:-（沒設）}"
```

實測兩種結果：沒設 → 印出以 `🔒 guard-ownership.sh 擋下：prototype/index.html` 開頭的 5 行說明，
結尾 `exit=2 ／ CV_ROLE=（沒設）`；設好 → 安靜無輸出，只有 `exit=0 ／ CV_ROLE=console-owner`。
看到 `exit=2` 就先別往下做，去把 session 開對。

## 先把這句話刻進去：搬設計決策，不搬程式碼

canvas 的 `.dc.html` 是**設計稿**，`prototype/index.html` 是**會跑的東西**。
整段覆蓋 = 42 條 selftest 全爆、SSE 斷掉、Stop 鈕消失。

三個從檔案裡查證過的證據，不是嚇你：

| 證據 | canvas 稿長這樣 | 生產前端的規矩 |
|---|---|---|
| `<script src="./support.js">` | `Main.dc.html` 第 5 行、`Components.dc.html` 第 5 行都有 | 單檔零依賴，一個外部 script 都不准 |
| `{{accent}}` 這類樣板變數 | 兩份 `.dc.html` 加起來 20 個變數名、27 處未展開的 `{{...}}` | 搬進去畫面上就直接印出 `{{accent}}` 這串字 |
| 顏色寫死字面 hex（`#141B3D`…） | 整份都是 hex，一個 `var()` 都沒有 | 只准 `var(--ds-*)`，`tokens.css` 是唯一真相 |

而且 `design-canvas/support.js` **這個檔在 repo 裡根本不存在**（`ls` 找不到，它是 canvas 執行環境注入的）。
你就算整份複製過來，它也不會動 —— 只會安靜地壞掉。

## 你這一輪只准動一個檔

`prototype/index.html`。這是 console-owner 的地盤（`_Context/team-roles.md` §0 紅線 1：前端只有一個擁有者）。

⛔ 全程唯讀，一個位元組都不准寫回去：

- `../../claude-design_claude-code/design-canvas/`（`Main.dc.html`、`Components.dc.html`、`canvas.json`、`cv-self-training-console.html`）
- `../../claude-design_claude-code/design-system/`（含 `tokens.css`）
- `../../claude-design_claude-code/prototype/index.html`

紅線 2 明文寫了：本專案前端是**複製過來再改**的，不准回頭動原檔。
回頭改原檔的代價是 —— 下一個做 design-pull 的人拿到一份已經被你污染的「設計真相」，而他不會知道。

## 動手前先照一張基準

以下四條都是唯讀指令，指令展開的當下就跑完了。
相對路徑以 PROJECT_ROOT 為 cwd（`claude` 要從 `agent_group_projects/cv-self-evolving-console/` 開）。

唯讀原檔的指紋（收工前要逐字相同）：
!`shasum ../../claude-design_claude-code/design-canvas/* ../../claude-design_claude-code/design-system/* ../../claude-design_claude-code/prototype/* 2>/dev/null`

tokens.css 的 hex → token 對照（你只准用左邊那個名字）：
!`grep -oE '\-\-ds-[a-z0-9-]+: *#[0-9A-Fa-f]{6}' ../../claude-design_claude-code/design-system/tokens.css`

index.html 目前非 token 顏色的基準（**現在是 4 行**，多出來的就是你新埋的）：
!`grep -nEi '#[0-9a-fA-F]{3,8}\b|rgba?\(' prototype/index.html | grep -v -- '--ds-'`

server 起了沒（`200` = 起了，`000` = 沒起）：
!`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/healthz || true`

---

## 步驟 1 — 讀 canvas，抽出差異清單（不要整份 diff）

讀 `$1`。沒給參數就讀 `../../claude-design_claude-code/design-canvas/Main.dc.html`。
給的是 artifact URL → 用 `Artifact` 的 `action: "read"` 抓內容，**只准 read，不准 publish / delete**。

抽完列一張表，一條一個決策，不要貼程式碼：

| # | 類別 | canvas 是什麼 | index.html 現在是什麼 | 動作 |
|---|---|---|---|---|
| 1 | 色 | 側欄小標 `#6B7399` | `var(--ds-ink-faint)` | 一樣，不動 |
| 2 | 間距 | 卡片 gap 16px | `var(--ds-4)` | 一樣，不動 |
| 3 | 新元件 | split 比例條 | 沒有 | 新增，色走 `--ds-train/valid/test` |

類別只有六種：**色 / 間距 / 字級 / 圓角 / 區塊順序 / 新元件**。
落在這六種以外的（互動行為、資料流、打哪個端點）**不是設計決策，不搬**。

「一樣，不動」的條目也要列。你列了才證明你比對過 —— 不然沒人分得出你是看過覺得一樣，還是根本沒看到。

## 步驟 2 — 逐條套進 prototype/index.html

- 顏色一律 `var(--ds-*)`。canvas 給的是字面 hex，**你要自己對回上面那張對照表**。
  對不到的（canvas 發明了新色）→ 停下來。那要先改 `tokens.css`，而 `tokens.css` 是唯讀原檔 ——
  提變更請求給 console-owner，不要在 index.html 埋一個新 hex 把問題藏起來。
- 間距 / 字級 / 圓角同理：`--ds-1..6`、`--ds-t-xs..2xl`、`--ds-r-sm` / `--ds-r` / `--ds-r-lg`。
  canvas 的 16px 就是 `--ds-4`，不要寫 `16px`。
- `<script>` 區（第 428 行往後）是**行為**不是視覺。差異清單裡沒有「新元件」要渲染，就一行都不要動。
- 不要改既有的 `id` 與 class 名。`#app` `#selftest` `#alert` `#log` `.thumb` `.ov` `.chart`
  被 42 條斷言用選擇器抓著。

⚠️ 三顆已知的地雷 —— 踩到不會當場爆，會在課堂上安靜地說謊：

| 地雷 | 為什麼不能照 canvas 改 |
|---|---|
| `.thumb{aspect-ratio:1}`（第 76 行） | 卡槽比例必須等於影像比例（契約 §11：256×256 = 1:1）。canvas 的卡片是 4:3 —— 照搬的話 `object-fit:cover` 把影像上下各裁 12.5%，bbox 框照畫在卡槽上，變成正確高度的 0.75 倍，**而 IoU 數字還是漂亮的 1.000**。斷言「`.thumb` 長寬比 == 影像 1:1」就是守這件事，不要拿掉。 |
| 小字別跟著用 `--ds-ink-faint` | canvas 的 `.h3` 與 meta 小字是 `#6B7399`（= `--ds-ink-faint`）。`#log` / `.ticker` **不准**跟進：10px + ink-faint 畫在 navy-900 上對比只有 3.62:1，低於 WCAG AA 的 4.5，投影機上等於沒有。那兩處固定 `--ds-ink-dim`（7.4:1）。 |
| 圖表線的顏色 | 有一條斷言把 stroke 寫死成 `rgb(167, 174, 200)`（`--ds-ink-dim`）與 `rgb(227, 179, 65)`（`--ds-valid`）。你改這兩個 token 的**值**，那條就 FAIL —— 那是斷言在做它的工作，不是誤報。要改就連斷言一起改，並在回報裡講清楚你改了什麼。 |

## 步驟 3 — 三道稽核，全綠才算完成

### A. token 稽核

```bash
grep -nEi '#[0-9a-fA-F]{3,8}\b|rgba?\(' prototype/index.html | grep -v -- '--ds-'
```

基準是 **4 行**：89 / 147 是陰影底色的 `rgba(...)`，1845 / 1846 是斷言拿來逐字比對的 `rgb(...)` 字串。
多一行就是你新埋的硬編色，回去換成 `var(--ds-*)`。
（`:root` 第 15–19 行那組 hex 是 `tokens.css` 的內嵌副本，被 `grep -v -- '--ds-'` 濾掉，不算違規。）

### B. 零依賴稽核

```bash
grep -nEi '<script[^>]+src=|<link[^>]+href=|cdn\.|unpkg|jsdelivr|fonts\.googleapis|import\(' prototype/index.html
```

**必須零輸出。** 有一行就是你把 canvas 的 `support.js`、某個 CDN 或某套字型一起搬進來了。
教室沒網路，CDN 掛掉那天就是你上台那天。

### C. selftest

server 沒起就先起。**另開一個 terminal**，這行會一直佔著不還你：

```bash
uv run --extra train uvicorn src.app.main:app --reload --reload-dir src
```

`--extra train` 不能省 —— 它保證 torch 裝得到。（裸 `uv run` 不會把已裝好的 torch 拆掉，
那是舊文件的誤傳；會拆掉的是不帶 extra 的 `uv sync`。）

然後開這個網址：

```
http://127.0.0.1:8000/index.html?selftest=1
```

頁面底下要印 `全部通過（42 條）`。條數以畫面印的為準，不要信原始碼註解裡那個數字（它落後了）。

⚠️ 兩件會讓你白忙半小時的事：

1. **網址沒有 `/prototype/`。** `main.py` 把 `prototype/` 目錄掛在 `/`，所以
   `http://127.0.0.1:8000/prototype/index.html` 實測是 **404**。是 `/index.html?selftest=1`，
   或 `/?selftest=1`。
2. **一定要從 uvicorn 開。** 最後一條斷言是「前端 stage 表 == 後端 `STAGE_TABLE` / `drive_run` 的 8 段」，
   它要打真的 `GET /api/v1/stages`。用 `python -m http.server` 或 `file://` 開，實測會拿到：

   ```
   FAIL  GET /api/v1/stages 抓得到（SyntaxError: Unexpected token '<', "<!DOCTYPE "... is not valid JSON）—— 這條要從 uvicorn 開，不是 file://
   1 項失敗
   ```

   那不是環境雜訊，是這條斷言**根本沒跑到**。前端 rail 在後端回應之前是拿本地表畫的，兩邊文案一漂開，
   畫面就會在最要緊的三十秒裡說謊（後端叫「抓模型並自己決定最好的」、前端寫「抓模型並自己決定」，
   學生對不上 log）。別把它當背景噪音放過去。

### D. 唯讀原檔沒被動到

把開頭那行 `shasum` 再跑一次，九個指紋要與上面那份逐字相同。
有一個變了 → 你剛剛寫到唯讀原檔了，還原它再回報。

---

## 回報怎麼寫

1. 差異清單那張表（含「一樣，不動」的條目）
2. 實際改了 `prototype/index.html` 哪幾段：行號 + 一句話
3. 三道稽核的**真實輸出**（貼出來，不要只寫「已通過」）
4. 沒搬的東西與理由：canvas 想要但 token 沒有的色、屬於行為不屬於視覺的、需要走契約 §12 的

## 不要做的事

| 不要 | 會炸在哪 |
|---|---|
| 整份覆蓋 `prototype/index.html` | 42 條斷言、SSE reducer、Stop 鈕、事件重播一起沒了。而 canvas 稿裡沒有任何一行是可以跑的，你連換回來的東西都沒有 |
| 在 index.html 埋新 hex | 下一個人改 token 時 index.html 跟不動，畫面一半新色一半舊色 —— 而唯一會抓到的是 token 稽核多出來的那一行，那行你會習慣性忽略 |
| 引 CDN 把 canvas 的字型或 icon 帶進來 | 教室沒網路；而且「單檔零依賴」是這個專案能被學生 `cp` 一份就跑起來的唯一理由 |
| 把 `id` / class 改成更好聽的名字 | 斷言用選擇器抓。改名之後的 FAIL 訊息長得像「量到 沒有線」，你會花 20 分鐘找一個自己 5 秒前造的 bug |
| 回頭改 `claude-design_claude-code/` 的原檔讓它「跟生產一致」 | 那份是 Claude Design 教案的已發佈成品，也是下一輪 design-pull 的輸入。改了它，下一輪就分不出設計決策到底是誰定的 |
| 把互動行為（哪顆鈕做什麼、抓哪個端點）當設計決策一起搬 | canvas 的按鈕是**畫**出來的。它那顆「載入 Roboflow 影像」背後沒有任何端點，搬進來就是一顆死鈕 |
| 順手把 M6 / M7 的畫面先做起來 | M6 以後一律不准提前做。前端先長出一個沒有後端的區塊，學生會以為它壞了，而它只是還沒被實作 |
