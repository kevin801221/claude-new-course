# CV 自我進化主控台 — Claude 規則

多 agent 教學專案。五位專家平行開工，**目錄不重疊**，契約先凍結再寫程式。

## 唯一真相（動手前先讀，不要重新設計）

| 檔案 | 是什麼 |
|---|---|
| `_Context/DESIGN.md` | 唯一設計真相（773 行，已定案）。專家編制、11 個 stage、護欄、開工順序。 |
| `_Context/api-contract.md` 🔒 | 事件信封逐欄、stage 代號、REST 形狀、錯誤碼。**不准自己改**，要改走 §12。 |
| `_Context/team-roles.md` | 角色 × 可寫目錄 + 三條紅線 + 階段閘門。 |
| `_Context/class_table.schema.json` | class 真相表欄位（值待 M2 由 dataset-truth 填，欄位不准動）。 |

契約與實作不一致時 **契約是對的**，改實作。反過來那一天（「程式已經這樣寫了所以契約改一下」）
就是整合當天爆掉的起點。

## 現在做到哪

- **M0 ✅**：三份契約 + 空 FastAPI 骨架（五個 router 掛進同一個 app，M2+ 佔位回 501）。
- **M1 ✅**：四件全數落地並實跑驗過 —— `scripts/gen_demo_wafers.py`、`routers/dataset.py` 四個 seam、
  `prototype/index.html`（逐卡 append、`?selftest=1` 18 條）、`scripts/selfcheck.py`（三條 assert）。
  實測：冷啟動（刪光 `01-raw-data/demo`、`01-raw-data/datasets` 與 `runs/`）產圖 + 三條 assert 全綠
  **2.5 秒**，預算是 90 秒。`tests/test_split_parity.py` 另有 5 條（pytest 或直接 python 都能跑）。
  課堂節奏由 `preset` 決定：`teaching` 逐張 0.12 秒（120 張 × 2 個 stage ≈ 31 秒，縮圖牆看得見
  一張一張長出來、Stop 鈕按得到），`real` 不停頓（selfcheck 走這條，1.3 秒）。
  assert C 同時判 IoU 中位數與 auto 框數（`<= GT × 1.2`）—— 只看 IoU 的話，把 `min_area_px`
  從 64 改成 16（框數 155 → 942）它照樣 1.0000 PASS，那就不是閘門是裝飾。
- **M1 複驗 ✅（2026-09-11，三份對抗審查修正後的獨立重跑）**：冷啟動 **3.0 秒**／三條 assert 3/3；
  四支 module 自檢（geometry / split / bus / registry）全過；pytest 5 passed；前端 `?selftest=1` **18/18**。
  Host 護欄實測 `0.0.0.0`・`testserver`・`evil.com` → 403，`localhost`・`127.0.0.1` → 放行。
  `teaching` 節奏實測事件時間戳相隔 120ms；24 張的 run 在 **15.2 秒**內逐卡展開（不是 0.12 秒噴完），
  Stop 鈕在第 25 張按下去 → `cancelled`、SSE 乾淨關閉、卡片停止增加。
  uvicorn 全程 `--reload --reload-dir src`，期間寫入 6 個 run 的事件，**Reload 觸發 0 次**。
  ⭐ 審查抓到的唯一 blocker（bbox 疊圖座標基準）已修並複驗：1:1 影像塞進 4:3 卡槽被 `object-fit:cover`
  裁掉上下 12.5%、框只有正確高度的 0.75 倍。改成 `.thumb{aspect-ratio:1}` 後實測 **33 個框
  `worstDeltaPx = 0`**（逐像素對齊）。`?selftest=1` 第 17 條就是守這件事的，不要拿掉。
- **M2 以後一律不准提前做**。看到 DESIGN 裡 M3–M7 的細節，那是後續里程碑的規格，讀了不要現在動手。

M1 驗收（唯一標準，零 GPU / 零 Roboflow / 零 LLM / 零網路、全程 < 90 秒）：
120 張真點陣 wafer PNG + 免費 GT → 按那顆按鈕 → 縮圖牆**逐卡 append** → 每張即時疊連通分量 bbox；
`uv run python scripts/selfcheck.py` 三條 assert 全綠（事件重播一致 / 抽樣一致 / auto-bbox IoU 中位數 ≥ 0.6）。

## 目錄擁有權（誰能改哪些檔案）

| 角色 | 可寫 |
|---|---|
| **console-owner**（team lead） | `prototype/index.html`、`src/app/{main,bus,registry}.py`、`src/app/routers/console.py`、`scripts/{run_all,selfcheck}.py`、`_Context/` 三份契約、`README.md`・`CLAUDE.md`・`pyproject.toml`・`.env.example`・`.gitignore` |
| **dataset-truth** | `src/autolabel/`、`src/app/routers/dataset.py`、`scripts/{gen_demo_wafers,prepare_dataset}.py`、`_Context/dataset-notes.md`、`01-raw-data/`・`02-dataset/`・`03-sealed-test/` |
| **training-engineer** | `src/train/`、`src/app/routers/train.py`、`scripts/train_yolo.py`、`00-weights/`・`04-experiments/` |
| **metric-auditor** | `src/eval/`、`src/app/routers/eval.py`、`tests/test_ci.py`（`runs/runs.jsonl` **只讀**） |
| **experiment-arbiter** | `src/evolve/`、`src/app/routers/round.py`、`_Context/prompts/*.md`、`scripts/evolve.py` |

三條紅線：
1. 🚫 **前端只有一個擁有者**（console-owner）。其他四位一行前端都不准改，只准交 JSON 契約。
2. ⛔ `claude-design_claude-code/prototype/index.html`、`design-canvas/`、`design-system/tokens.css`
   **唯讀**。本專案前端是複製過來再改，不准回頭動原檔。
3. 🔒 共同地基（三份契約 + `main.py` + `bus.py` + `registry.py` + `pyproject.toml`）只有 console-owner 能改。

## 不靠自律的權限鎖（改程式時會直接踩到）

- **事件只有兩處能寫**：`src/app/bus.py` 與訓練子行程的 `src/train/callbacks.py`，
  後者必須 `import bus.append_event()`。不准自己拼 JSON、不准自己算 seq。
- **type 前綴 = 發言權**：`ds./label./class.` → dataset-truth、`model./probe./train./sweep.` →
  training-engineer、`eval./test./charts.` → metric-auditor、`chat./expert./arbiter./round./autonomy./stop`
  → experiment-arbiter、`run./stage./provenance.` → console-owner。
  `bus.append_event()` 會擋下越權的 type（丟 `ValueError`），不是靠自律。
- **判分的手不寫紀錄**：metric-auditor 只讀 `runs.jsonl`，寫入一律經 `registry.py`。
- **封印 test 四道鎖**：`03-sealed-test/` 不進 `data.yaml`、`/eval` 的 `split` 傳 `test` 回 400、
  只有 `/final-test` 讀得到它且要求 `stop_event_seq`、arbiter 排除 `final:true`。

## 規則

- **套件一律 `uv`，禁止 `pip` / `requirements.txt`**。加依賴前先問 console-owner
  （`pyproject.toml` 是共同地基）。**torch / ultralytics 只准待在 optional extra `train` 裡**，
  base 依賴永遠是那六個 —— 不帶 extra 的 `uv sync` 實測 0.10 秒、`import torch` 必須是
  `ModuleNotFoundError`（M1/M2 跑到 torch 就是有人偷跑）。要訓練才 `uv sync --extra train`（.venv 159 MB → 1.1 GB）。
- 啟動一律 `uv run uvicorn src.app.main:app --reload --reload-dir src`。
  少了 `--reload-dir src`，`runs/` 每寫一筆事件就重啟並殺掉 driver。
- 路徑一律 `pathlib.Path` 從 `PROJECT_ROOT` 推導（`bus.PROJECT_ROOT`），不硬編絕對路徑。
  `scripts/*.py` 直接執行時 cwd 不會進 `sys.path`，自己 `sys.path.insert(0, str(PROJECT_ROOT))`。
- PyTorch 預設 `device="mps"` + `PYTORCH_ENABLE_MPS_FALLBACK=1`，不是 cuda（M3 起）。
- 前端**單檔零依賴、無 build step**：不准引 CDN、不准 npm，圖表一律 inline SVG 自畫。
- 顏色 / 間距 / 字級**只能取** `claude-design_claude-code/design-system/tokens.css` 的 `--ds-*` 變數，
  不准自己發明 hex。code 與數字一律 Menlo。
- 文件與 UI 文字**繁體中文**，技術名詞保留英文；`lang="zh-Hant"`。
- 非 trivial 邏輯留一個能跑的檢查：Python 走 `python -m <module>` 的 `_selfcheck()` 或
  `scripts/selfcheck.py`，前端沿用 `?selftest=1`；pytest 以外不引框架。
- `.env` 進 `.gitignore`，repo 只留 `.env.example`；`ROBOFLOW_API_KEY` 只在 server 讀、**沿用既有變數名**。
- commit：繁中、Kevin 風格、**不署名 Claude Code**、無 emoji。

## 常見任務

**加一個事件 type** → 先在 `api-contract.md` §4 補一列（console-owner 落筆），
再 `bus.append_event(run_id, stage="sNN", type="<你的前綴>.xxx", actor="<你自己>", data={...}, text="...")`。
`data` 一律是 object；`text` 是前端 `#log` 直接印的一行，不要在前端拼字。

**加一個端點** → 只動自己那個 router 檔。錯誤一律
`raise HTTPException(status_code=<碼>, detail={"code": "<MACHINE_CODE>", "detail": "<繁中一句話>"})`，
`main.py` 會轉成契約 §1 的形狀。新的 `code` 要在 §10 錯誤碼表補一列。

**會產生事件的 POST** → 一律回 `202` + JSON，**不要回串流**。
全系統只有一條 SSE 出口 `GET /api/v1/runs/{run_id}/events`（`EventSource` 只能 GET）。

**改契約** → 不要自己改。提變更請求四段講完（改哪一條 / 現有形狀為什麼做不到 / 改完的完整 JSON /
誰要跟著改），console-owner 批准才動。

## 不要做的事

- 不要編輯 `_Context/` 的三份契約（除非你是 console-owner 且走了 §12）。
- 不要在 M1 提前實作 M2+ 的端點，也不要把 501 改成 404 或回假資料 —— 回假資料會讓前端以為自己接好了。
- 不要用 `BackgroundTasks` 跑訓練（阻塞 event loop，心跳會停、SSE 會卡死、cancel 會失效）。
- 不要 parse 訓練 stdout 取指標（實測 log 帶 ANSI 與 `\r`），用 `on_fit_epoch_end` callback。
- 不要開第二條 SSE、不要開 per-expert 微服務、不要引圖表庫、不要引 WebSocket。
- 不要 hardcode API key，不要把服務 bind 到 `0.0.0.0`。
