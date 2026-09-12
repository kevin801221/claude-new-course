---
description: 產一份可以整段貼進內建 /design 的 canvas brief（鎖死 --ds-* token、現有版面、事件資料形狀）
argument-hint: <要設計什麼，例如「s09/s10 討論區」或「重畫 s08 評分圖表分頁」>
allowed-tools: Read, Grep, Write, Bash(grep:*), Bash(sed:*), Bash(ls:*), Bash(mkdir:*), Bash(sort:*), Bash(tr:*), Bash(echo:*)
---

你要產一份 **brief**，不是產畫面。產完人類會把它整段貼進 Claude Code 內建的 `/design`。

## 先搞清楚誰是誰（寫錯整件事就歪了）

| 東西 | 誰的 | 產出什麼 | 這個指令跟它的關係 |
|---|---|---|---|
| `/design` | Claude Code **內建** | `.dc.html` artboard（多畫板 canvas，可拖拉、可匯出 PNG/PDF） | 你產的 brief 是餵它的輸入 |
| `/design-sync` | Claude Code **內建** | 把本機 `design-system/` 推上 claude.ai/design | 不歸你管，別呼叫 |
| `/design-brief` | 本專案（你現在在跑的這支） | `_Context/design-briefs/<slug>.md` 一份文字 brief | 就是你 |
| `/design-pull` | 本專案的姊妹指令 | 把 canvas 的視覺決策**手工移植**回 `prototype/index.html` | brief 的最後一段要指向它 |

🚫 **不准自己實作一個叫 `/design` 的東西**，內建的已經在了。你只產文字。

⚠️ **canvas 匯出不能蓋掉 `prototype/index.html`**。那支是生產前端：單檔、零依賴、無 build step、
一條 SSE、`?selftest=1` 42 條 assert。用 canvas 匯出的 HTML 覆蓋它，SSE 沒了、42 條 assert 當場全爆，
而且 canvas 不知道 `ds.image` 事件長什麼樣、不知道 `#alert` 要 `esc()`。
所以 canvas 的角色只有一個：**先把視覺決策定下來**。落地是 `/design-pull` 的事。

---

## 手上的真值（下面是實跑出來的，不要再憑印象改寫）

要設計的東西：**$ARGUMENTS**

### 可用的 design token（唯一來源 `claude-design_claude-code/design-system/tokens.css`，唯讀）
!`grep -oE -- '--ds-[a-z0-9-]+: *[^;]+' ../../claude-design_claude-code/design-system/tokens.css`

### 11 格 stage 骨架（後端 `src/app/registry.py` 的 STAGE_TABLE，`GET /api/v1/stages` 就是回這張）
!`sed -n '28,43p' src/app/registry.py`

### 現有前端的所有 id（`prototype/index.html`，2109 行）
!`grep -oE 'id="[a-zA-Z0-9_-]+"' prototype/index.html | sort -u | tr '\n' ' '`

### 已經有哪些 brief / 姊妹指令
!`ls _Context/design-briefs 2>/dev/null || echo '(目錄還不存在，你第一步就是 mkdir -p 它)'`
!`ls .claude/commands 2>/dev/null || echo '(沒有其他指令)'`

> 上面四段是這支指令展開時**真的跑出來**的。token 只有列出來的那些，
> 沒列到的顏色一律不准用 —— 自己發明 hex 的那一版，`/design-pull` 移植回去就對不上，
> 整個畫面會變成「兩套配色混在一起」。

---

## 你要做的四件事

### 1. 先讀清楚才動筆（只讀跟 $ARGUMENTS 有關的那幾段，不要整份 cat）

| 讀什麼 | 為什麼 |
|---|---|
| `_Context/api-contract.md` §8.3 | 快照形狀 —— 畫面上每個數字要指得出來自哪一欄 |
| `_Context/api-contract.md` §4 | 事件 type 表 —— 哪個 stage 發哪種事件、`data` 有什麼欄位 |
| `prototype/index.html` 對應區塊 | 現況長什麼樣。`grep -n 'aside class="left"' prototype/index.html` 找位置 |

如果 $ARGUMENTS 講的是 s09/s10/s11，那些事件在契約 §4 標的是 **M6/M7**（欄位名已定、型別未凍）。
brief 裡要註明「這一格的 `data` 型別還沒凍結，畫面先照欄位名排版，型別由 experiment-arbiter 走 §12 補」。

### 2. 版面現況要寫進 brief（不是重新發明一個版面）

`.app` 是 `grid-template-columns: 270px 1fr 330px`，三欄 + 上面兩條橫列：

| 區塊 | 真實 id / class | 裡面有什麼 |
|---|---|---|
| header | `#vbadge` `#src` `#conn` `#dev` | 標題、裁決徽章、SSE 連線狀態、device |
| rail（整列寬） | `#rail` `#budget` `#ticker` `#alert` | 11 格 stage pill、三軸預算條、最新一行旁白、整列寬錯誤橫幅 |
| 左欄 270px | `#source` `#rf` `#preset` `#limit` `#run` `#cancel` `#q` `#fcls` `#fsplit` `#dist` | 資料來源、那顆按鈕、篩選、類別分佈 |
| 中欄 1fr | `#wall`（縮圖牆）`#view-eval`（四張 inline SVG 圖表）`#view-pred`（推論圖牆） | 三個分頁，`.tab` 切換 |
| 右欄 330px | `#iou-hist` `#n-train` `#n-valid` `#n-test` `#bar` `#health` `#recipe` `#pbar` `#log` `#artifacts` | 標註天花板、split 統計、配方、進度、log |

⚠️ `#log` 在右欄底部，1440×900 時整塊在畫面外 —— 所以最新一行才另外印在 rail 下面的 `#ticker`。
brief 裡要重要的字**不要**只出現在 `#log`，台上看不到。

### 3. 寫出 brief 檔案

落到 `_Context/design-briefs/<slug>.md`（目錄不存在就先 `mkdir -p _Context/design-briefs`）。
slug 從 $ARGUMENTS 取，短橫線小寫英數，例如 `s09-discussion-panel`、`s08-charts-redo`。

檔案照這個骨架寫，**全部繁體中文，技術名詞保留英文**：

```markdown
# design brief：<標題>

> 貼進 Claude Code 內建 `/design` 用。產出的是 canvas 設計稿，**不是**能跑的前端。
> 落地走 `/design-pull` 手工移植回 prototype/index.html。

## 目標 / 受眾
（一句話講清楚給誰用、解決什麼。例：「給站在台前的講師看的 s09 討論區，
30 秒內要能指出哪個專家提了什麼、arbiter 准了哪一個變因」）

## 版面
（在現有三欄版面的哪裡。是新分頁？塞進右欄？整列？寫死 px 或 fr）

## 每個區塊要顯示什麼、資料從哪來
| 區塊 | 顯示什麼 | 資料來源 |
|---|---|---|
| … | … | 事件 `expert.msg` 的 `data.factor` / 快照 §8.3 的 `budget.cost_usd` |
（每一列的「資料來源」都要指得到契約裡真的存在的欄位。指不到的欄位就是還沒有的東西，
別畫上去 —— 畫上去的第一件事就是有人跑去後端硬加一個欄位，那是繞過 §12。）

## 配色與間距（只准用這些）
| 用途 | token | 值 |
|---|---|---|
（把上面注入的 --ds-* 表，挑這個畫面真的會用到的搬進來，附值）

## 硬規矩（canvas 也要照做）
- code 與所有數字一律 `--ds-mono`（Menlo）
- `lang="zh-Hant"`
- 四個 split 語意色只認 `--ds-train` / `--ds-valid` / `--ds-test` / `--ds-unassigned`
- 不准引 CDN、不准 npm、不准圖表庫；圖表一律 inline SVG 自畫
- 不准自己發明 hex

## 不要畫的東西
（照下面「禁止清單」那節逐條搬，每條附為什麼）

## 落地路徑
這份 canvas 只定視覺。移植回 `prototype/index.html` 走 `/design-pull`，
移植完 `?selftest=1` 42 條要全綠才算完成。
```

### 4. 最後一行印出來

> 下一步：跑內建 `/design` 並貼上這份 brief（檔案：`_Context/design-briefs/<slug>.md`）

---

## 禁止清單（brief 裡一定要有，每條都要附為什麼）

| 不要做 | 為什麼（整合當天會炸在哪） |
|---|---|
| 不要引 CDN / npm / 圖表庫 | `prototype/index.html` 是單檔零依賴無 build step。引了之後 `/design-pull` 移植不進去，而且台上斷網就白畫面 |
| 不要開第二條 SSE | 全系統只有一條出口 `GET /api/v1/runs/{run_id}/events`。第二條會讓 seq 去重失效，同一筆事件畫兩張卡 |
| 不要自己發明 hex | token 是唯一真相。自創色移植回去會變成兩套配色並存，而且 `--ds-navy-800` 上的低對比色實測 1.23:1 等於沒畫 |
| 不要用 sans 顯示數字或 code | 數字對不齊，`?selftest=1` 第 23 條那類對比/字型 assert 會 FAIL |
| 不要漏 `lang="zh-Hant"` | 中文會被當簡體斷行，標點位置跑掉 |
| 不要在畫面上自己算 split / 類別分佈 | 事件裡的 `ds.image.split` **永遠是 null**（s04 指派時不補發）。前端自算過一次，結果是一條龍訓練完、verdict TRUSTED，右欄照樣寫「train 0 / valid 0 / test 0」。數字一律吃 §8.3 快照的 `split` 與 `GET /api/v1/datasets/{ds}/classes` |
| 不要把第三方字串直接當 HTML | `#alert` 與 chips 的 `detail` 有兩個第三方來源（使用者填的 workspace 被 404 原封回顯、第三方回應 body 前 400 bytes）。漏 `esc()` 就是同源 XSS，注入的 JS 可以 `POST /api/v1/runs` 燒 GPU |
| 不要畫 M6 以後才有的東西當「已完成」 | s09–s11 契約上是 `skipped`。畫成綠色完成態，學生會以為按得動，按下去回 501 |
| 不要動 `claude-design_claude-code/` 底下任何檔案 | 那是 Claude Design 教案的已發佈成品，⛔ 唯讀。本專案前端是複製過來再改的 |

## 寫完自己檢一遍

- [ ] brief 裡每個數字都指得到契約裡真的存在的欄位（指不到 = 那個欄位不存在，刪掉）
- [ ] 每個顏色都是 `--ds-*`，沒有裸 hex
- [ ] 有寫「這是 canvas 不是生產前端」「落地走 `/design-pull`」
- [ ] 沒有寫互動行為（第一版只要版面對、元件對；行為是第二輪的事）
- [ ] 檔案真的落在 `_Context/design-briefs/`，最後一行印了下一步
