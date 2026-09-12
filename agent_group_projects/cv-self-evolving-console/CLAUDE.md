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
- **M2–M5 ✅（2026-09-12）**：那顆按鈕 = `POST /runs` → `console.drive_run()` 一路跑
  `s01 → s03 → s02 → s04 → s05 → s06+s07 → s08`。實測（r67・teaching・120 張）
  **271.7 秒 / 548 筆事件 / verdict TRUSTED**，其中 s02 的一次 `claude -p` 命名就佔 197 秒（$0.31）。
  前端 `?selftest=1` **42 條**（40 條同步 + failSafe 409 + `GET /api/v1/stages`；條數以畫面
  印的為準，`index.html:1650` 的註解落後了）、`scripts/selfcheck.py` **3 條**、module 自檢
  （procs / bus / registry / runner / console / cluster / metrics）全綠。
  ⚠️ **server 一定要 `uv run --extra train uvicorn …` 起** —— extra 保證 torch 裝得到，
  少了它在還沒 sync 過 extra 的機器上 s05 會紅燈而錯誤訊息指不到真因。
  ❌ **更正（2026-09-12 在 uv 0.12.7 實測）**：舊版這裡寫「不帶 extra 的 `uv run` 會把 torch 反安裝掉」，
  **那是錯的**。裸 `uv run` 預設 inexact（只補缺的、不移除），實測連跑兩次 `torch 2.14.0` 都還在。
  真正會拆掉的是**不帶 extra 的 `uv sync`**（exact，`--dry-run` 印「Would uninstall 33 packages」）
  與 `uv run --exact`。連帶後果：不能再用「裸 `uv run` 跑不到 torch」當 M1 零 GPU 的閘門，
  要改驗 `pyproject.toml` 的 base 依賴 + grep M1/M2 路徑沒有 `import torch`。
  ⭐ 這一輪修掉的五個 blocker：Stop 殺不到 `to_thread` 裡的子行程（新增 `src/app/procs.py`
  統一登記 + `kill_all`）、kill -9 後孤兒子行程讓 run 永遠卡 running（`claim_or_crash` 改成
  殺掉並標 crashed）、長 stage 死畫面與假斷線（`stage.note` + 心跳改 data 框）、
  講稿缺 `--extra train`、WALKTHROUGH 與實際畫面對不上。
- **側欄說謊修正 + `source:"roboflow"` ✅（2026-09-12）**：
  - 右欄的 **split 統計 / 訓練設定** 改成吃後端快照（契約 §8.3 加了選填的 `split` 與 `recipe`），
    類別分佈改吃 `GET /datasets/{ds}/classes`。修掉實測抓到的謊：一條龍訓練完、verdict TRUSTED，
    右欄照樣寫「TRAIN 0 / VALID 0 / TEST 0・還有 120 張沒指派」＋「類別分佈（無資料）」＋
    一組沒人用過的 `yolov8n / 50 epoch / imgsz 640`。**前端一個數字都不准自己算**：
    事件裡的 `ds.image.split` 永遠是 null（s04 指派時不補發），配方在 `recipe.json`。
  - 過期標籤全部拿掉（`（M2）`、`（M3 才會動）`、`roboflow（M8 未實作）`）；
    `/healthz` 的 `milestone` 從 `"M1"` 改成 `"M5"`。前端「照類別分層抽樣」與兩顆匯出鈕**刪掉**
    （s04 真的在後端分層落檔了，前端再分一次只會產出伺服器沒有的 split）。
    `stratifiedSplit()` 本人留著 —— 契約 §11 assert 2 要拿它跟 Python 版逐筆比對。
  - **`source:"roboflow"` 那顆按鈕按得動了**：`POST /runs` 加四個選填欄位
    `labels`/`workspace`/`project`/`version`（**沒有 api_key**，key 只在 server 讀 `.env`），
    下載在開 run **之前**同步做完 —— 沒 key 當場 `400 ROBOFLOW_KEY_MISSING` 且不留下鬼 run。
    按鈕預設 `labels:"auto"`：`human` 會停在 s02 的 `409 CLASS_TABLE_SCHEMA_CONFLICT`
    （🔒 class 表 schema 的契約缺口，見 §10 那一列），一顆必定紅燈的按鈕是陷阱不是功能。
  - 錯誤橫幅從左欄 11px 小字搬到 rail 底下**整列寬 14px**（`#alert`）。
  - 前端 `?selftest=1` **32 → 36 條**（新增：側欄數字 == 快照、s04 之前是「—」不是 0、
    訓練設定 == 快照 recipe、過期里程碑標籤不存在）。
- **`source:"roboflow"` 的安全性與閘門修正 ✅（2026-09-12）**：
  - 🔒 **key 不准進 log**：`roboflow_src._get()` 的 except 漏了 `http.client.HTTPException`
    （`InvalidURL` / `IncompleteRead` 不是 `OSError`），例外逃出去變成 HTTP 500，而
    `InvalidURL` 的訊息帶著整串 `?api_key=<真 key>` 的 url → 落進 `runs/*-uvicorn.log`
    （而且失敗那句就是「看 runs/m2-uvicorn.log」）。修法三件：補 `http.client.HTTPException`、
    訊息一律過 `_scrub()`（`api_key=`/`key=` 的值抹掉）、workspace/project 先過
    `urllib.parse.quote`（控制字元根本走不到 http.client）。zip 下載斷線同一行一起修掉。
  - 🔒 **前端錯誤橫幅與 chips 一律 `esc()`**：`detail` 有兩個第三方來源（使用者填的 workspace
    被 404 原封回顯、第三方回應 body 前 400 bytes），原本直接 innerHTML → 同源 XSS
    → 注入的 JS 可以 `POST /api/v1/runs` 燒 GPU。
  - **半份 `index.json` 要自癒**：`load_index` / `_id_map` 壞檔回 None（= 當作沒下載過，
    `ensure()` 自然重抓），兩個寫入點改 tmp+replace 原子寫。
  - **缺檔不准靜默跳過**：s03 的 `path is None → continue` 改成 `404 IMAGE_NOT_FOUND`
    （和 s02 同一條規則）—— 原本 10 張檔案不存在照樣收一個全綠的 run，IoU 天花板用剩下那半算。
  - **`label_ceiling` 閘門真的接上 driver**：`drive_run()` 在 s03 之後問一次 `ceiling.gate()`，
    `abandon` 就寫 `stop {reason:"label_ceiling"}`（在翻狀態之前）、s04..s08 標 skipped、
    run 收成 `done(abandoned)`。實測把框縮成 0.45 倍：s03 量到 0.2096 < 0.4 → 停在 s03。
    合成那條路是退化值、`abandon` 永遠 False，M1 主線不受影響。
  - **天花板量的是這一輪自己那批**：`ceiling.measure()` 改吃 ds manifest 的 id 清單，
    快取鍵加 id 指紋（原本 limit=60 的 run 掛的是整份 index 289 張的數字，重疊 0 張）。
  - **卡片的 split 值域補齊**：前端白名單 → `train/valid/anchor/sealed`（+ 手動指派的 `test`），
    契約 §8.7 明列這組值。前端 `?selftest=1` **37 → 41 條**。
- **M6 以後一律不准提前做**。看到 DESIGN 裡 M6–M8 的細節，那是後續里程碑的規格，讀了不要現在動手。

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
