# 五位專家怎麼派工 — 多 agent 蓋一台「會自己學」的 CV 主控台 Walkthrough

> **對象**：會用 Claude Code、想學「怎麼同時指揮好幾個 agent 而不互相覆蓋」的人
> **形式**：講師現場帶，學員邊聽邊在自己機器上打
> **時長**：120 分鐘（含真實踩坑與現場越權示範）
> **產出**：一套可以直接用的 `.claude/`（4 位 subagent + 5 個 slash command + 擁有權 hook），和一套會用一輩子的派工節奏
> **核心方法**：**契約先凍結 → 目錄不重疊 → 平行開工 → 閘門驗收**。前端不派 agent，走 Claude Design 迴圈。

---

## 開場（5 分鐘）：「叫 Claude 蓋一個大東西」為什麼會爛尾

你一定試過這個：開一個 session，跟 Claude 說「幫我蓋一個 CV 訓練平台，要能自動標註、自己訓練、自己評估」。

前 20 分鐘很爽。第 40 分鐘開始，它忘記自己 30 分鐘前定的欄位名。第 60 分鐘，前端顯示的數字跟後端算的對不上。第 90 分鐘，你已經在讀它寫的 3000 行然後決定重寫。

| | 一個 session 蓋 | 五位專家平行蓋 |
|---|---|---|
| context | 什麼都塞，到後面互相擠掉 | 每位只看自己那塊 |
| 欄位名 | 每次想起來都不一樣 | 契約凍結，誰都不准自己改 |
| 誰負責 | 都是它，所以都不是 | 一個目錄一個擁有者 |
| 出錯怎麼查 | 全部重讀 | 看是誰發的事件 |
| 判分 | 自己出考卷自己改 | 球員不能兼裁判 |

這份教案教的不是「怎麼寫 prompt」，是**怎麼當一個不會讓四個人互相覆蓋的 team lead**。

> **教學金句**：「目錄不重疊不是行政規定，是唯一能讓四個人同時打字而不互相覆蓋的機制 —— 而前端只給一個人，是因為它是唯一沒辦法用目錄切開的東西。」

---

## 📁 你要先看懂的東西

```
agent_group_projects/cv-self-evolving-console/
├── _Context/                          ← 唯一真相，動手前先讀
│   ├── DESIGN.md                       (773 行定案：專家編制、11 個 stage、開工順序 M0–M8)
│   ├── api-contract.md  🔒             (事件信封逐欄、REST 形狀、錯誤碼。要改走 §12)
│   ├── team-roles.md                   (⭐ 角色 × 可寫目錄 + 三條紅線 + 階段閘門)
│   └── class_table.schema.json         (class 真相表欄位，值由 dataset-truth 填)
│
├── .claude/                           ← 這份教案要教你用的東西
│   ├── settings.json                   (掛 guard-ownership hook)
│   ├── agents/
│   │   ├── dataset-truth.md            (資料長什麼樣、有幾類、每張框在哪)
│   │   ├── training-engineer.md        (選型 + 配方 + 不阻塞的訓練)
│   │   ├── metric-auditor.md           (唯一有權說「這算不算真進步」)
│   │   └── experiment-arbiter.md       (不擁有任何 ML 變因，只裁決這輪改哪一個)
│   ├── commands/
│   │   ├── dispatch.md                 ⭐ /dispatch M6  → 印派工單
│   │   ├── gate.md                       /gate M1      → 實跑驗收 + 越權稽核
│   │   ├── contract-change.md            /contract-change → 契約變更四段式
│   │   ├── design-brief.md               /design-brief  → 產給內建 /design 的 brief
│   │   └── design-pull.md                /design-pull   → canvas 決策搬回生產前端
│   └── hooks/
│       └── guard-ownership.sh          (PreToolUse 擋手滑寫入)
│
├── src/app/bus.py                     ← 真正的鎖在這（TYPE_OWNERS 擋越權發言）
├── prototype/index.html               ← 前端，只有一個擁有者
└── runs/                              ← 執行期產物，手改會被 hook 擋
```

**注意編制是 5 位，subagent 只有 4 個。** 第五位是 `console-owner`，也就是**你自己**（主 session）。team lead 不能是 subagent —— 它要 spawn 別人、要批契約、要收驗收，這三件事 subagent 做不了。

---

## Phase 0：環境準備（10 分鐘）🛠

### 0.1 進對資料夾

```bash
cd ~/claude-new-course/agent_group_projects/cv-self-evolving-console
claude
```

⚠️ 一定要從**專案根**進，不要從上層 repo 進。`.claude/` 是照 cwd 讀的，從上層進會讀到上層那套（guard-secrets + notify-done），這個專案的擁有權 hook 不會生效。

### 0.2 確認 agent 讀得到

```
/agents
```

看到 `dataset-truth` / `training-engineer` / `metric-auditor` / `experiment-arbiter` 四個就對了。

看不到的話 99% 是 frontmatter 壞掉 —— 打開 `.claude/agents/dataset-truth.md` 看第一行是不是 `---`，`name:` 是不是等於檔名。

### 0.3 宣告你是 team lead

```bash
export CV_ROLE=console-owner
```

這一行決定 hook 讓不讓你改共同地基。**沒設的話你自己也會被擋**，這是故意的 —— 逼你意識到「我現在是以誰的身分在打字」。

### 0.4 現場實測 hook（不要跳過，這是最有感的 30 秒）

```bash
# 不是 console-owner 的身分改契約 → 應該被擋
CV_ROLE= .claude/hooks/guard-ownership.sh <<< '{"tool_name":"Write","tool_input":{"file_path":"'"$PWD"'/_Context/api-contract.md"}}'; echo "exit=$?"

# 改自己的地盤 → 應該放行
.claude/hooks/guard-ownership.sh <<< '{"tool_name":"Write","tool_input":{"file_path":"'"$PWD"'/src/autolabel/geometry.py"}}'; echo "exit=$?"
```

`exit=2` 是擋下，`exit=0` 是放行。

> **教學金句**：「hook 不是問 Claude 同不同意才跑的 —— 它是管 Claude 的 harness，exit 2 那一刻 Claude 連工具都沒碰到。」

---

## Phase 1：M0 契約閘門 ⭐ 最詳細（20 分鐘）📋

### 1.1 為什麼「還沒凍契約就不准 spawn 任何人」

這是整份教案最重要的一句。

假設你現在就派四個人出去。dataset-truth 寫事件的時候想了一個欄位叫 `image_id`，training-engineer 同一時間想的是 `img_id`，metric-auditor 寫成 `id`。三小時後你把三份合起來 —— **三份都要重寫**，因為每一份都在對方的假設上蓋了三小時的東西。

所以規矩是：**M0 沒過，不准平行開工。**

### 1.2 三份契約各管什麼

| 檔案 | 凍什麼 | 誰能改 |
|---|---|---|
| `api-contract.md` 🔒 | 事件信封逐欄、stage 代號、REST 形狀、錯誤碼 | 只有 console-owner，且要走 §12 |
| `team-roles.md` | 角色 × 可寫目錄、三條紅線、階段閘門 | 只有 console-owner |
| `class_table.schema.json` | class 真相表有哪些**欄位**（值等 M2 填） | 只有 console-owner |

加上一個空的 FastAPI 骨架：五個 router 掛進同一個 app，佔位端點回 **501**。

### 1.3 為什麼佔位要回 501 不是 404、更不是假資料

```
501 → 前端知道「這個還沒做」，會把按鈕 disable
404 → 前端以為「路徑打錯了」，開始亂改自己
假資料 → 💀 前端以為自己接好了，兩週後你才發現整條線沒接上
```

> **教學金句**：「回假資料是所有整合地獄的起點 —— 它讓你以為你已經接好了。」

### 1.4 現場 demo：閘門會真的擋

M0 還沒過的時候打 `/dispatch M1`，它會先跑前置檢查然後拒絕派工，告訴你缺哪一份契約。這不是裝飾，這是整套流程的第一道閘門。

---

## Phase 2：第一次派工，只派兩個人（20 分鐘）🧠

### 2.1 為什麼 M1 只派兩個人

M1 的驗收條件是：**零 GPU、零 Roboflow、零 LLM、零網路，全程 90 秒內跑完。**

120 張真點陣 wafer PNG + 免費 GT → 按那顆按鈕 → 縮圖牆逐卡長出來 → 每張即時疊連通分量 bbox → 三條 assert 全綠。

這只需要 console-owner（前端 + 事件總線）和 dataset-truth（產圖 + 自動畫框）。**另外三位這時候派出去只會搶著寫還不存在的東西。**

> **教學金句**：「第一個里程碑要選那個『不用等任何人、不用網路、不用 GPU、90 秒能驗完』的 —— 因為你要的是一條會亮的燈，不是一張漂亮的架構圖。」

### 2.2 打這一句

```
/dispatch M1
```

它會做四件事：

1. 先跑現況檢查（關鍵路徑在不在、`git status`、帳本狀態統計）
2. 讀 `DESIGN.md` 的開工順序 + `team-roles.md` §3 的階段閘門
3. 印一張**派工單**：派誰、任務是什麼、誰跟誰可以平行
4. 對每一位產一段**可以直接丟給 Task 的任務書**

### 2.3 派出去

把 `/dispatch` 印出來的任務書整段丟給 Task 工具，`subagent_type` 填 `dataset-truth`。

或者直接說「照 dispatch 的第 2 條派 dataset-truth 出去」。

### 2.4 觀察它只動自己的目錄

它回來以後，**先不要看它寫了什麼，先看它動了哪些檔**：

```bash
git status --short
```

應該只有 `src/autolabel/`、`scripts/gen_demo_wafers.py`、`01-raw-data/`、`_Context/dataset-notes.md` 這幾塊。

有 `src/app/bus.py` 就是越權了 —— 那是共同地基。

### 2.5 驗收

```
/gate M1
```

它會**實際跑**這幾條，不是讀文件說有沒有做：

```bash
uv run python scripts/selfcheck.py      # 三條 assert
git status --short                       # 越權稽核
```

三條 assert 分別守什麼：

| assert | 守什麼 | 壞掉會怎樣 |
|---|---|---|
| A 事件重播一致 | 完整讀 events.jsonl == 斷線後 `since=` 續讀 | 學生電腦睡著醒來，畫面少幾張圖 |
| B 抽樣一致 | Python 分層抽樣 == 前端 `stratifiedSplit` | 前端說 84 張 train，後端只餵 81 張 |
| C auto-bbox IoU | 自動畫的框 vs 免費 GT，中位數 ≥ 0.6 | 框全歪了但一路綠燈跑到訓練 |

⚠️ assert C **同時判 IoU 和框數**。只看 IoU 的話，把 `min_area_px` 從 64 調成 16（框數 155 → 942）它照樣 1.0000 PASS —— 那就不是閘門，是裝飾品。

> **教學金句**：「一個永遠會過的檢查，比沒有檢查更危險 —— 它會給你一種已經驗過的錯覺。」

---

## Phase 3：四個人同時打字（25 分鐘）⚙️

### 3.1 平行還是序列？就看這張表

| 情況 | 怎麼派 | 為什麼 |
|---|---|---|
| 兩人可寫目錄**無交集** | 平行 | 這就是整套設計的目的 |
| 要動共同地基（契約 / `main.py` / `bus.py` / `registry.py` / `pyproject.toml`） | 序列，console-owner 先落，其他人才開工 | 五個人搶改同一個 include = 整合當天爆炸 |
| B 要吃 A 的產出檔案 | 序列 | 資料走檔案系統（`01-raw-data/` → `02-dataset/` → `04-experiments/`），不走 in-memory |
| 兩人都要發同一個前綴的事件 | 不可能發生 | 前綴就是擁有權，`bus.py` 會丟 `ValueError` |
| 「順便把 X 也改一下」 | ⛔ 不准 | 跨目錄的順便就是越權 |

### 3.2 事件前綴 = 發言權

| 前綴 | 誰 |
|---|---|
| `ds.` `label.` `class.` | dataset-truth |
| `model.` `probe.` `train.` `sweep.` | training-engineer |
| `eval.` `test.` `charts.` | metric-auditor |
| `chat.` `expert.` `arbiter.` `round.` `autonomy.` `stop` | experiment-arbiter |
| `run.` `stage.` `provenance.` | console-owner |

這條**不是寫在文件裡靠自律**，是 `src/app/bus.py` 的 `TYPE_OWNERS` 在擋。dataset-truth 想發 `train.epoch` 會直接吃到 `ValueError`。

> **教學金句**：「發別人的 type，等同越權改別人的目錄 —— 只是它比較難被你肉眼看見，所以要用程式擋。」

### 3.3 跨角色依賴怎麼辦：一個真實案例

這是這個專案**真的發生過**的事：

> metric-auditor 把 verdict 算出來了（事件層 `eval.verdict` 寫著 `TRUSTED`），
> 但 `runs/runs.jsonl` 551 列裡 `verdict` / `val_map5095` / `ci` / `anchor_ok` 非 null 計數**全部是 0**。
> 事件說 trusted，帳本說 null。

為什麼會這樣？因為**判分的手不准寫紀錄**（球員不能兼裁判）。metric-auditor 只讀 `runs.jsonl`，寫入一律經 `registry.py`，而 `registry.py` 是 console-owner 的地盤。

所以正確流程是：

```
metric-auditor  → 提變更請求（我要這五個欄位進帳本，值長這樣）
                  ↓  /contract-change
console-owner   → 落契約 + 改 registry.set_status + 補一條「帶 verdict 之後讀得回來」的 assert
                  ↓
metric-auditor  → 驗收：跑一個 run，讀帳本確認欄位有值
```

**錯誤流程**（學生九成會犯）：metric-auditor 自己跑去改 `registry.py`。改完當下會動，整合那天你會發現有兩隻手在寫帳本，而 seq 單調性已經死了。

### 3.4 契約變更請求要四段講完

```
/contract-change 帳本要記 verdict / val_map5095 / ci / anchor_ok / cost_min
```

四段是：**改哪一條 → 現有形狀為什麼做不到 → 改完的完整 JSON → 誰要跟著改**。

⛔ 有一個方向是禁止的：「程式已經這樣寫了，所以契約改一下」。**契約與實作不一致時，契約是對的，改實作。** 反過來那一天，就是整合當天爆掉的起點。

### 3.5 課堂練習：擁有權表上真的有三個洞 ⭐

這不是我編的題目，是複核這包教材時**實際被抓出來的**。`_Context/team-roles.md` §1 那張表漏了三個東西：

| 漏的 | 現況 | 為什麼是個洞 |
|---|---|---|
| `scripts/fetch_weights.py` | 檔頭第 14 行自稱「擁有者：training-engineer」，但 §1 沒這一列 | **自己發權限**。檔頭不是契約，表才是 |
| `src/app/procs.py` | 沒有任何人擁有 | M6 的 arbiter 要用它包 `claude -p`，到時候沒人有權改 |
| `.claude/` 本身 | 沒有任何人擁有 | 下一輪兩個人同時改 `dispatch.md` 就會互相覆蓋 —— 正是這包東西自己在教的失敗模式 |

讓學生自己跑一次：

```
/contract-change 把 scripts/fetch_weights.py、src/app/procs.py、.claude/ 補進 team-roles §1
```

看它有沒有四段講完（改哪一條 / 現有形狀為什麼做不到 / 改完的完整內容 / 誰要跟著改）。

> **教學金句**：「檔頭寫『擁有者：我』不算擁有 —— 擁有權表才算。自己在自己檔案裡宣告權限，跟沒有宣告是一樣的。」

---

## Phase 4：前端不派 agent — Claude Design 迴圈（20 分鐘）🎨

### 4.1 為什麼前端只給一個人

後端可以用目錄切：`src/autolabel/` 給 A、`src/train/` 給 B、`src/eval/` 給 C，四個人同時打字互不干擾。

前端只有**一個檔**：`prototype/index.html`。單檔、零依賴、無 build step。

沒辦法切。所以只給一個擁有者，其他四位**一行都不准改，只准交 JSON 契約**。

### 4.2 那要改版面怎麼辦？走這四步

```
/design-brief 右欄要顯示 noise floor 灰帶與 per-class CI
        ↓            產一份 brief，落檔到 _Context/design-briefs/
/design-sync         （Claude Code 內建）把 design-system/ 推上 claude.ai/design
        ↓
/design              （Claude Code 內建）貼上 brief，產出 .dc.html artboard canvas
        ↓            在 canvas 上點選、改字、拖拉，直到視覺定案
/design-pull <canvas>
                     把**設計決策**搬回 prototype/index.html + 三個稽核
```

⚠️ `/design` 和 `/design-sync` 是 **Claude Code 內建的**，不要自己再造一個同名的。我們自己寫的是前後兩支包裝：`/design-brief`（進去之前）和 `/design-pull`（出來之後）。

### 4.3 ⭐ 最重要的一句：搬設計決策，不搬程式碼

Claude Design 產的 `.dc.html` 是**設計稿**。`prototype/index.html` 是**會跑的東西** —— 它有 SSE、有 Stop 鈕、有 42 條 `?selftest=1`。

把 canvas 匯出的 HTML 整段蓋上去 = selftest 全爆、SSE 斷掉、Stop 鈕消失。

所以 `/design-pull` 做的是：抽出**差異清單**（色 / 間距 / 字級 / 圓角 / 區塊順序 / 新元件），一條一條套。

### 4.4 三個稽核，全綠才算完成

| 稽核 | 怎麼跑 | 在守什麼 |
|---|---|---|
| token 稽核 | grep 硬編 hex，對照 `tokens.css` 的 `--ds-*` | 每次改版顏色都不一樣 |
| 零依賴稽核 | grep `<script src` / `cdn` / `unpkg` | 教室沒網路就整頁掛掉 |
| selftest | `?selftest=1` 要全綠 | 前端說謊（側欄數字自己算的） |

⚠️ **selftest 一定要從 uvicorn 開**：

```bash
uv run --extra train uvicorn src.app.main:app --reload --reload-dir src
# 然後開 http://127.0.0.1:8000/prototype/index.html?selftest=1
```

有一條斷言（前後端 stage 表逐欄對帳）**只有從 uvicorn 開才跑得到**。用 `python -m http.server` 或 `file://` 開會看到 1 條 FAIL，然後你會以為那是環境雜訊 —— 不是，那條是真的在守東西。

### 4.5 一條紅線

⛔ `claude-design_claude-code/prototype/index.html`、`design-canvas/`、`design-system/tokens.css` **唯讀**。本專案的前端是複製過來再改的，任何人不准回頭動原檔。

---

## Phase 5：閘門與越權稽核（15 分鐘）🔍

### 5.1 三層鎖，各擋各的

| 層 | 在哪 | 擋什麼 | 擋不住什麼 |
|---|---|---|---|
| hook | `.claude/hooks/guard-ownership.sh` | 路徑：唯讀原檔、`runs/` 手改、`.env`、共同地基 | **身分** |
| 發言權 | `src/app/bus.py` 的 `TYPE_OWNERS` | 發別人前綴的事件 → `ValueError` | 非事件的越權 |
| 閘門 | `/gate` 的 `git status` × 擁有權表 | 身分：誰動了不是自己的目錄 | 事後才抓得到 |

### 5.2 ⚠️ 誠實揭露：hook 擋不住身分

`guard-ownership.sh` 的規則 4、5 靠環境變數 `CV_ROLE`。而 **subagent 會繼承主 session 的環境變數**。

所以你設了 `CV_ROLE=console-owner` 之後，派出去的 dataset-truth **也是 console-owner 的身分**，照樣改得動契約。

這兩條實際上是「**防手滑**」，不是「防越權」。

真正擋越權的是 `/gate` 那段 `git status` × 擁有權表的比對（**他律**），和 `bus.py` 的 `TYPE_OWNERS`（那個是真的鎖）。

> **教學金句**：「別把防手滑吹成權限系統。你要很清楚每一層擋得住什麼、擋不住什麼 —— 不然你會信任一個根本沒鎖的門。」

### 5.3 現場示範：故意越權一次

叫 `training-engineer` 「順便把 `_Context/api-contract.md` 的錯誤碼表補一列」。

它會照做（因為環境變數是 console-owner）。然後跑 `/gate`，看它被 `git status` 抓出來標紅。

**這個示範比口頭講有效十倍。** 學生會記住：自律不算鎖，要有人在出口查。

---

## Phase 6：接 M6 之前先把債還掉（15 分鐘）⚠️

這一段用這個專案**真實的對抗審查結果**（11 個 agent、實跑驗證）當教材。

### 6.1 現在的迴圈缺哪一段

```
按按鈕 → 自己標 → 自己分群命名 → 自己選型 → 自己訓練 → 自己量 noise floor → 給 verdict → 停
                                                                                      ↑
                                                                    缺這裡：看完數字自己決定下一輪做什麼
```

M6（討論迴圈）+ M7（自主模式）就是那一段。`src/evolve/`、`_Context/prompts/`、`scripts/evolve.py` 三個路徑目前**都不存在**，`routers/round.py` 六支端點全部 501。

### 6.2 但 M6 現在不准開工

因為 arbiter 要靠 `runs/runs.jsonl` 挑 best run、比 Δ mAP、排除 `final:true`。而現在：

| 症狀 | 實測 | arbiter 會看到什麼 |
|---|---|---|
| 判分沒進帳本 | 551 列，`verdict` 非 null 計數 = 0 | 139 個一模一樣的 null |
| 鬼 run | r85 / r88 / r93 / r96 `status=done` 但零事件 | 把它們當成功案例 |
| done 卻有 pending | 17 個，其中 r86 是 `s02=failed` 而 `status=done` | 拿失敗的 run 當證據 |

> **教學金句**：「在一張全是 null 的表上做決策，AI 不會告訴你它在亂猜 —— 它會給你一個很有自信的答案。」

### 6.3 所以派工順序是

```
1. metric-auditor  → 提變更請求：判分五欄進帳本
2. console-owner   → 落契約 + 改 registry.set_status + 加不變式（done 不准有 pending 格）
3. console-owner   → 清掉 4 個鬼 run
4. /gate           → 驗收：跑一個 run，帳本讀得回 verdict
5. ✅ 這時候才 /dispatch M6
```

**這就是 team lead 的工作**：不是把任務發出去，是知道**哪一件還不能發**。

---

## 整合 demo：一條龍 15 分鐘跑完 ✅

```bash
# 1. 進對資料夾、宣告身分
cd ~/claude-new-course/agent_group_projects/cv-self-evolving-console
export CV_ROLE=console-owner
claude

# 2. 看四位專家在不在
/agents

# 3. 看現在做到哪、下一步該派誰
/dispatch M1

# 4. 派出去（把派工單那段丟給 Task，subagent_type: dataset-truth）

# 5. 驗收 + 越權稽核
/gate M1

# 6. 改前端（不派 agent）
/design-brief 右欄加 noise floor 灰帶
/design-sync          # 內建
/design               # 內建，貼上 brief
/design-pull design-canvas/Main.dc.html

# 7. 要動契約的時候
/contract-change 帳本要記 verdict / val_map5095 / ci / anchor_ok
```

---

## 常見問題 FAQ

**Q1. 四個 subagent 可以真的同時跑嗎？**
可以。在同一則訊息裡開多個 Task 就會並行。但**只有可寫目錄無交集時才准這樣做** —— 有交集就改序列，不要僥倖。

**Q2. subagent 看得到 CLAUDE.md 嗎？**
看得到，會自動注入。所以 agent 檔裡**不要再複製一份 CLAUDE.md 的內容**，只寫這個角色專屬的東西（擁有權邊界、事件前綴、交付格式、工單）。

**Q3. 為什麼 console-owner 不做成 subagent？**
因為它要 spawn 別人、批契約、收驗收。subagent 沒辦法再 spawn subagent，而且「批准自己的變更請求」本身就不合理。team lead 永遠是主 session。

**Q4. `/dispatch` 印出來的任務書，我一定要照抄嗎？**
不用。它是給你當底稿的。但**三件事不要動**：可寫目錄清單、驗收指令、什麼時候要停下來喊人。學生最常刪掉最後一條，然後 agent 就自己猜著往前衝。

**Q5. 我的專案沒這麼複雜，五個人是不是太多？**
是。切線的判準不是「功能有幾塊」，是**「證據來源不同」和「球員不能兼裁判」**。兩個人（做的人 + 判分的人）就足以擋掉最致命的那個錯：自己出考卷自己改。

---

## 卡點對照表 ⭐（都是真的踩過的）

| 卡點 | 真實原因 | 處理 |
|---|---|---|
| `/agents` 看不到四位專家 | 從上層 repo 進 `claude`，讀到別套 `.claude/` | `cd` 到專案根再開 |
| agent 存在但沒被自動選中 | `description` 寫得太抽象 | description 要寫「什麼時候該叫我」，具體到調度者能自動選 |
| 改自己的檔也被 hook 擋 | 忘了 `export CV_ROLE=console-owner` | 設環境變數。這是故意的 |
| 派兩個人出去結果互相覆蓋 | 兩人可寫目錄有交集 | 開工前先對 `team-roles.md` §1 那張表 |
| agent 說「我順便把契約也補了」 | 環境變數繼承，hook 擋不住身分 | `/gate` 的 `git status` 稽核會抓到。這是設計，不是 bug |
| `?selftest=1` 有 1 條 FAIL | 用 `python -m http.server` 或 `file://` 開 | 一定要從 uvicorn 開，那條在對帳前後端 stage 表 |
| `/design-pull` 第一個 Edit 就被擋 | 沒 `export CV_ROLE=console-owner` | 設環境變數。hook 在做它該做的事，不是指令壞了 |
| `/gate M3` 第一次跑一定紅 | `GET /models/candidates` 已實作，但契約 §9 還把它列在「一律回 501」表 | 這是 gate 在抓真問題，FAIL 記在 console-owner 身上，不是 agent 寫壞 |
| `s05` 訓練紅燈但錯誤訊息指不到真因 | server 沒帶 `--extra train` 起，torch 不在 | `uv run --extra train uvicorn ...` |
| 一條龍跑完但右欄數字全是 0 | 前端自己算了不該自己算的東西 | 前端一個數字都不准自己算，全部吃後端快照 |
| `uv run` 之後 torch 不見了 | 舊版 uv 會剪掉多餘套件 | 訓練相關一律 `uv run --extra train` |
| arbiter 給的建議看起來很有自信但是空的 | 帳本判分欄全 null | 先還 Phase 6 的債再開 M6 |

---

## 講師私房筆記 💡

**節奏建議（120 分鐘）**
- 開場 5 分 + Phase 0 用 10 分（hook 實測那 30 秒一定要現場打，最有感）
- Phase 1 給 20 分 —— 「501 vs 404 vs 假資料」那張表是全場最值錢的 3 分鐘
- Phase 2 給 20 分，Phase 3 給 25 分（3.3 那個真實案例慢慢講）
- Phase 4 給 20 分（前端這段學生最容易想歪，要把「搬決策不搬程式碼」講到他們會複述）
- Phase 5 的越權示範**一定要現場做**，15 分
- Phase 6 給 15 分收尾

**故意踩三個坑**（比口頭講有效十倍）
1. 不設 `CV_ROLE` 就去改契約 → 被 hook 擋 → 學生第一次體會 exit 2
2. 叫 training-engineer「順便補一下契約」→ 它真的會做 → `/gate` 抓出來 → 學生學會「自律不算鎖」
3. 用 `python -m http.server` 開 selftest → 看到 1 條 FAIL → 學生學會「不要把紅燈當雜訊」

**給不同角色的推薦**
| 你是 | 重點看 |
|---|---|
| 想學多 agent 協作 | Phase 1（契約閘門）+ Phase 3（平行判準表） |
| 前端/設計 | Phase 4 整段，尤其 4.3 |
| ML / 資料 | Phase 6（為什麼判分要跟訓練分手） |
| team lead / PM | Phase 5 + Phase 6.3（知道哪一件還不能發，才是這個角色的工作） |

**Kevin 自己驗過的真實狀況**
- 這台東西真的會訓練。r137 用 Roboflow 真圖 409 張跑完整條龍：1929 筆事件、8 分 8 秒、verdict TRUSTED。
- auto-label 對人工 anchor 的 IoU 中位數 **0.826**（40 張，門檻 0.4）。合成資料那條是 1.0000 —— 那條只能當抗噪 smoke test，不能當能力證明，因為兩邊共用「連通分量外接框」這個定義。
- LLM 命名實際成功率 **58%**（45 份裡 llm 26 / fallback 19），其餘是 240 秒 timeout 退機械命名。課堂上有機會現場看到降級，不要慌，那正好是教「AI 步驟一定要有 fallback」的時機。
- 我讓 11 個 agent 對抗審查這個專案自己，抓到 13 個 critical/high，包括 anchor 標籤整批 `cls` 錯位、判分從沒進帳本、前端一個 XSS。**寫完就以為對了，是這堂課最想打破的習慣。**

---

## 一句話總結

> **契約先凍結才有平行，目錄不重疊才不會互相覆蓋，前端只給一個人因為它切不開 —— 而自律從來不算鎖，出口一定要有人查。**

---

## 進階閱讀

- 🔗 `../../agent_group_projects/cv-self-evolving-console/WALKTHROUGH.md` —— 學生視角：怎麼把這台東西跑起來
- 🔗 `../../agent_group_projects/cv-self-evolving-console/_Context/team-roles.md` —— 擁有權的唯一真相
- 🔗 `../../agent_group_projects/cv-self-evolving-console/_Context/DESIGN.md` —— 專家編制與 M0–M8 開工順序
- 🔗 `../../claude-design_claude-code/WALKTHROUGH.md` —— Claude Design 迴圈的完整教案（Phase 0–7）
- 🔗 `agent_team_walkthrough.md` —— 另一種平行法：多 Claude 實例 × git worktree
- 🔗 `hook_walkthrough.md` —— hook 心智模型與 8 支教學腳本

---

_Last updated: 2026-09-12_
_Maintainer: Kevin (kevin@legalsign.ai)_
_配套教材（同目錄）：`agent_team_walkthrough.md`（worktree 平行）+ `hook_walkthrough.md`（hooks）+ `four_skills_walkthrough.md`_
_專案實戰案例：`../../agent_group_projects/cv-self-evolving-console/WALKTHROUGH.md`_
