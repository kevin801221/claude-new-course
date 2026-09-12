# 角色 × 可寫目錄（console-owner 凍結・M0 閘門）

> 五位專家平行開工的前提是**目錄不重疊**。這份表就是那條線。
> 切線畫在「證據來源不同」與「球員不得兼裁判」上（見 `_Context/DESIGN.md`），不是畫在 pipeline 步驟上。
> 專案根 `PROJECT_ROOT` = `agent_group_projects/cv-self-evolving-console/`，以下路徑皆相對於它。

## 0. 三條紅線（先看這個）

1. 🚫 **前端只有一個擁有者。其他四位一行前端都不准改。**
   唯一前端檔 = `prototype/index.html`（本專案內）。dataset-truth / training-engineer / metric-auditor / experiment-arbiter **只准交 JSON 與 chart series 契約**，要前端顯示什麼就提變更請求給 console-owner，不要自己開編輯器。
   五個人同時改同一個 `index.html` = 整合當天互相覆蓋，這是 DESIGN 點名的致命缺陷。
2. ⛔ **`/Users/awesomeartengineer01/claude-new-course/claude-design_claude-code/prototype/index.html` 是唯讀的**（Claude Design 教案的已發佈成品）。本專案的前端是**複製**過來再改，任何人不准回頭動原檔。同理 `claude-design_claude-code/design-canvas/` 與 `design-system/tokens.css` 一律唯讀，只准取用不准編輯。
3. 🔒 **共同地基只有 console-owner 能改**：`_Context/api-contract.md`、`_Context/team-roles.md`、`_Context/class_table.schema.json`、`src/app/main.py`、`src/app/bus.py`、`src/app/registry.py`、`pyproject.toml`。要改走 `api-contract.md` §12 的變更流程。

---

## 1. 角色 × 可寫目錄

| 角色 | 一句話 | 可寫（擁有） | 唯讀（可看不可改） |
|---|---|---|---|
| **console-owner**（team lead） | 前端 + 唯一事件總線 + run registry + 一鍵腳本 + 契約 + provenance | `prototype/index.html`<br>`src/app/main.py`<br>`src/app/bus.py`<br>`src/app/registry.py`<br>`src/app/routers/console.py`<br>`scripts/run_all.py`<br>`scripts/selfcheck.py`<br>`_Context/api-contract.md`<br>`_Context/team-roles.md`<br>`_Context/class_table.schema.json`<br>`README.md`・`CLAUDE.md`・`pyproject.toml`・`.env.example`・`.gitignore`<br>`runs/`（執行期產物） | 全部其他人的檔案 |
| **dataset-truth** | 資料長什麼樣、有幾類、每張框在哪 | `src/autolabel/`（`geometry.py`・`cluster.py`）<br>`src/app/routers/dataset.py`<br>`scripts/gen_demo_wafers.py`<br>`scripts/prepare_dataset.py`<br>`_Context/dataset-notes.md`<br>`_Context/class_table.json`（**值**，欄位照 schema）<br>`tests/test_split_parity.py`<br>`01-raw-data/`・`02-dataset/`・`03-sealed-test/` | `_Context/api-contract.md`・`_Context/class_table.schema.json`・`src/app/bus.py`（呼叫它的 `append_event()`） |
| **training-engineer** | 選型 + 配方 + 不阻塞的訓練 | `src/train/`（`runner.py`・`callbacks.py`・`select.py`）<br>`src/app/routers/train.py`<br>`scripts/train_yolo.py`<br>`_Context/train-notes.md`<br>`00-weights/`・`04-experiments/` | 同上 + `02-dataset/`（吃 dataset，不改它） |
| **metric-auditor** | 唯一有權說「這算不算真進步」，持封印 test 的鑰匙 | `src/eval/`（`metrics.py`・`checks.py`・`series.py`）<br>`src/app/routers/eval.py`<br>`_Context/eval-notes.md`<br>`tests/test_ci.py` | 同上 + `04-experiments/`、**`runs/runs.jsonl` 只准讀不准寫**（見 §2.3） |
| **experiment-arbiter** | 不擁有任何 ML 變因，只裁決這一輪准改哪一個 | `src/evolve/`（`driver.py`・`llm.py`・`ofat.py`）<br>`src/app/routers/round.py`<br>`_Context/prompts/*.md`（五份 system prompt）<br>`scripts/evolve.py`<br>`tests/test_ofat.py` | 同上 + `runs/runs.jsonl`（讀記憶；寫紀錄一律經 registry） |

> `src/app/routers/` 是唯一被四個人共用的目錄，所以**擁有權切到檔案層**：一個 router 一個檔一個擁有者（`console.py` / `dataset.py` / `train.py` / `eval.py` / `round.py`）。`main.py` 只有 console-owner 能改（掛 router 的那一行也是），避免五個人搶著改同一個 include。

---

## 2. 幾條不靠自律的權限鎖

### 2.1 事件只有兩處能寫
`runs/<run_id>/events.jsonl` 的 writer 只有 `src/app/bus.py`；例外是訓練子行程裡的 `src/train/callbacks.py`，它**必須 import bus 的 `append_event()`**（不准自己拼 JSON、不准自己算 seq）。seq 單調與重播語意只要有第二隻手寫就死。

### 2.2 type 前綴 = 發言權
任何人只准發自己前綴的事件：`ds.* label.* class.*` → dataset-truth；`model.* probe.* train.* sweep.*` → training-engineer；`eval.* test.* charts.*` → metric-auditor；`chat.* expert.* arbiter.* round.* autonomy.* stop` → experiment-arbiter；`run.* stage.* provenance.*` → console-owner。發別人的 type 等同越權改別人的目錄。

### 2.3 判分的手不寫紀錄
metric-auditor **只讀** `runs/runs.jsonl`，寫入一律經 `registry.py`（console-owner）。寫紀錄的手與判分的手分開，否則「讓結論通過」的最短路徑就是改 baseline。

### 2.4 沒有 ML 立場的人做 provenance
`plan vs actual` 逐欄比對（run args / `ds_version` / `label_version` / `class_table_version`）由 console-owner 做。它刻意不懂 CV（不算 metric、不碰 `.pt`、不提訓練參數），所以它的否決才可信。

### 2.5 封印 test 的四道鎖各有主
`03-sealed-test/` 由 dataset-truth 切、由 metric-auditor 持鑰匙、由 console-owner 的契約擋（`/eval` 的 `split` 傳 `test` 回 400）、由 arbiter 的純函式排除（`final:true` 不進證據池）。四把鎖分在四個人手上，不是同一個人自律。

---

## 3. 階段閘門（不可跳）

| 閘門 | 誰 | 過關標準 |
|---|---|---|
| **M0 契約閘門** | console-owner 一人（**此時不准 spawn 任何人**） | `_Context/api-contract.md` 🔒 + 本檔 + `_Context/class_table.schema.json` + 空 FastAPI 骨架（五個 router 掛進同一個 app，`uv run uvicorn src.app.main:app --reload --reload-dir src` 起得來，佔位端點回 501） |
| **M1 可驗收閘門** | console-owner + dataset-truth（零 GPU / 零 Roboflow / 零 LLM / 零網路） | 120 張真點陣 wafer PNG + 免費 GT → 按那顆按鈕 → 縮圖牆逐卡 append → 每張即時疊出連通分量 bbox；`uv run python scripts/selfcheck.py` 三條 assert 全綠；全程 < 90 秒 |
| **M2+** | 照 DESIGN 開工順序，一個里程碑一次驗收 | 見 `_Context/DESIGN.md`「開工順序」 |

M0 不過不准平行開工 —— 四個人照著一份還會變的契約寫，等於四份都要重寫。

---

## 4. 硬規矩（每位都適用，違反就是 bug）

- **套件一律 `uv`，禁止 `pip` / `requirements.txt`**。加依賴前先問 console-owner（`pyproject.toml` 是共同地基）。M1 的依賴只有 `fastapi` / `uvicorn` / `numpy` / `pillow` / `scipy` / `scikit-learn` —— **不要在 M1 裝 torch / ultralytics**，`uv sync` 才快。
- **PyTorch 預設 `device="mps"`** 並設 `PYTORCH_ENABLE_MPS_FALLBACK=1`，不是 cuda。
- 所有路徑用 `pathlib.Path` 從 `PROJECT_ROOT` 推導，**不硬編絕對路徑**。
- 前端**單檔零依賴、無 build step**：不准引 CDN、不准 npm、圖表一律 inline SVG 自畫。
- 顏色 / 間距 / 字級**只能取** `claude-design_claude-code/design-system/tokens.css` 的 `--ds-*` 變數，不准自己發明 hex。code 與數字一律 Menlo（`--ds-mono`）。
- 文件與 UI 文字**繁體中文**，技術名詞保留英文；`lang="zh-Hant"`。
- 非 trivial 邏輯留一個能跑的檢查：前端沿用 `?selftest=1` 的 assert 慣例，Python 用 `scripts/selfcheck.py` 或 `tests/test_*.py`（pytest 以外不引框架）。
- 秘密：`.env` 進 `.gitignore`，repo 只留 `.env.example`；`ROBOFLOW_API_KEY` 只在 server 讀，**沿用既有變數名不改名**；前端不持 key。repo root 的 `guard-secrets.sh` hook 會擋。
- commit：繁中、Kevin 風格、**不署名 Claude Code**、無 emoji。
- 資料交換走**檔案系統**不走 in-memory（`01-raw-data/` → `02-dataset/` → `04-experiments/` → `runs/`），這樣才能跨 session 接續。

---

## 5. 要喊話的四種時機

1. 要動共同地基（契約 / `main.py` / `bus.py` / `registry.py` / `pyproject.toml` / 前端）→ **不准自己改**，提變更請求等 console-owner 批准。
2. 需要契約細節（欄位名、型別、錯誤碼、事件 `data` 形狀）→ 直接問 console-owner，**不要自己猜一個先寫**。
3. 跨角色依賴（例：metric-auditor 想要某個 chart series 欄位、training-engineer 想要 dataset 多一個統計）→ 經 console-owner 走契約，不要跑去改對方目錄。
4. 自己的里程碑驗收過了 → 說清楚做了什麼、哪條 assert 綠了、留下哪個可跑的檢查。

> **教學金句**：「目錄不重疊不是行政規定，是唯一能讓四個人同時打字而不互相覆蓋的機制 —— 而前端只給一個人，是因為它是唯一沒辦法用目錄切開的東西。」
