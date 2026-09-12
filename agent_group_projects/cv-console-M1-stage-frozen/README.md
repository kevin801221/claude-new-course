# CV 自我進化主控台 — 五位專家 × 一條事件流

> ⚠️ **不可部署到公開網址。** 只 bind `127.0.0.1`，非 localhost 的 `Host` header 一律回 403。
> 自主模式無人看管 + 公開網址 = 任何人都能替你燒 GPU 與 token。

## 🚦 第一次來？先看這個

**新手請直接讀 [`WALKTHROUGH.md`](WALKTHROUGH.md)**（一步一步、有名詞對照表、不用會寫程式）。

### 現在按前端那顆按鈕，會訓練嗎？**不會。**

目前只完成到 **M1**。按下去**實際發生的只有兩件事**：

1. 120 張合成晶圓圖**一張一張**送到畫面上
2. 用**純幾何**（連通分量，不是 AI）自動畫框

| | 現在（M1） | 什麼時候才有 |
|---|---|---|
| 訓練模型 | ❌ 沒有，`uv sync` 預設不裝 PyTorch（要 `--extra train` 才裝） | M3 |
| GPU | ❌ 完全不用 | M3 |
| LLM 呼叫 / 花錢 | ❌ 零 | M2 |
| Roboflow 帳號 / API key | ❌ 不用 | M8 |
| 網路 | ❌ 不用，斷網也能跑 | M8 |
| mAP 圖表 | ❌ 沒有 | M4 |
| AI 跟你討論怎麼改 | ❌ 沒有 | M6 |

畫面右邊 11 格流程，現在只有 **3 格是活的**，其餘 8 格顯示灰色的「未實作（M2+）」——
刻意不放假進度條。三顆按鈕（分層抽樣 / 下載 splits.json / data.yaml）也刻意鎖住。

---

> **學什麼**：用 5 個職責不重疊的 agent 蓋一套「AI 自己標資料 → 自己選模型 → 自己看指標 → 自己討論下一輪」的主控台，並且**全程在瀏覽器上看得見**
> **時長**：M1 段 90 分鐘（零 GPU、零帳號、零網路）；全案 M0–M8 約 10 天
> **產出**：一個單檔零依賴前端 + 一條 append-only 事件流（`events.jsonl`）+ 三條會綠的 assert

## 為什麼這個範例存在？

`computer-vision-wafer-detection` 教的是「ML pipeline 有天然階段，agent 照階段分工」。
這個範例補上更難的一課：**當 agent 會自己改參數、自己宣告進步時，分工要怎麼切才不會自欺**。

切線不畫在 pipeline 步驟上，而是畫在兩件事上：**證據來源不同**、**球員不得兼裁判**。
所以訓練的人不准判分、判分的人不准寫紀錄、量尺的製造者不准判分、做 provenance 比對的人刻意不懂 CV。

## 你會用到的 Claude Code 功能

- [x] 多 agent 平行協作（目錄不重疊 + 契約先凍結）
- [x] 契約驅動開發（`_Context/api-contract.md` 🔒，改要走批准流程）
- [x] SSE 事件流與斷線重播（`?since=` / `Last-Event-ID`）
- [x] subprocess 邊界（訓練不阻塞 API，M3）
- [x] `claude -p` 當執行期的專家發言者（M6）

## 現在做到哪

| 里程碑 | 內容 | 狀態 |
|---|---|---|
| **M0** | 契約凍結（`api-contract.md` / `team-roles.md` / `class_table.schema.json`）+ 空 FastAPI 骨架（五個 router 掛進同一個 app、佔位回 501） | ✅ 完成 |
| **M1** | 120 張真點陣 wafer PNG + 免費 GT → 一顆按鈕 → 縮圖牆逐卡 append → 即時疊連通分量 bbox；`selfcheck.py` 三條 assert 全綠、全程 < 90 秒 | ✅ 完成（三條 assert 全綠、前端 `?selftest=1` 18 條全綠；`preset=teaching` 逐張 0.12 秒約 30 秒跑完、`real` 約 2 秒） |
| M2 | class 真相凍結（KMeans + 一次 LLM 命名）、split 落檔與硬閘門 | ⬜ |
| M3 | 訓練不阻塞 + 真 per-epoch 串流 + Stop 鈕 | ⬜（依賴已備妥：`uv sync --extra train`） |
| M4 | noise floor + bootstrap CI + verdict + 四張 inline SVG 圖 | ⬜ |
| M5–M8 | 一鍵端到端 / 討論迴圈 / 自主模式 / Roboflow 真資料 | ⬜ |

## 起手式

```bash
cd agent_group_projects/cv-self-evolving-console
uv sync                                                    # 1 秒上下（不含 torch / ultralytics）
uv run uvicorn src.app.main:app --reload --reload-dir src   # http://127.0.0.1:8000
```

**`--reload-dir src` 不是可選的。** 事件總線 `runs/<run_id>/events.jsonl` 就寫在專案樹裡，
預設的 `--reload` 會監看整棵樹 —— 逐張串流時等於每寫一筆事件就重啟一次 server 並殺掉 driver，
畫面會停在半路而且看不出原因。只監看 `src/`，`runs/` 怎麼寫都不會觸發 reload。

確認起得來：

```bash
curl -s localhost:8000/healthz                       # {"ok":true,"contract_v":1,"milestone":"M1"}
curl -s -X POST localhost:8000/api/v1/runs -H 'content-type: application/json' -d '{}'
curl -sN 'localhost:8000/api/v1/runs/r1/events?since=0'   # 全段回放，Ctrl-C 離開
curl -s localhost:8000/api/v1/models/candidates      # 501：M2+ 的端點只有簽章
```

## 訓練依賴：什麼時候才要 `--extra train`

`torch` 與 `ultralytics` **不在基礎依賴裡**，放在 optional extra：

```bash
uv sync                  # 預設。27 個套件、.venv 159 MB —— M1 / M2 全部的事都做得完
uv sync --extra train    # 只有要真的訓練（M3 起）才加，+33 個套件、.venv 1.1 GB
```

| | `uv sync` | `uv sync --extra train` |
|---|---|---|
| 套件數 | 27 | 60（+33） |
| `.venv` 大小 | 159 MB | 1.1 GB |
| 刪光 `.venv` 重建（uv cache 熱） | **0.10 秒** | 0.19 秒 |
| 重跑（什麼都沒變） | 0.02 秒 | 0.25 秒 |
| `import torch` | `ModuleNotFoundError` | torch 2.14.0，`mps_available True` |

（本機實測，Apple M5 / 24 GB。`--extra train` **第一次**要下載 torch + ultralytics 的 wheel，
實測 20.8 秒；之後 uv cache 熱了才是表裡那 0.19 秒。）

**為什麼不預設裝**：M1 的驗收條件是「零 GPU、零網路、全程 < 90 秒」，而 M1 段最常做的動作
是刪掉 `.venv` 重來一次示範給學生看。torch + ultralytics 第一次要多花 20 秒下載幾百 MB、
把 `.venv` 撐大 7 倍（159 MB → 1.1 GB），而 M1 / M2 **一行都不會 import 到它們**（auto-label 是 `scipy.ndimage` 純幾何、
分群是 `sklearn`）。付這個代價要等到 s05 真的要抓模型的那一刻。

反過來也成立：extra 沒裝時 `import torch` 會直接 `ModuleNotFoundError` —— 這是**故意**的閘門，
表示「M3 的程式偷跑進 M1 路徑」會當場爆掉，而不是靜靜跑一條沒人發現的分支。

### 權重與 MPS 實測（裝了 extra 之後跑一次）

```bash
uv run python scripts/fetch_weights.py
# {"name": "yolov8n.pt", "bytes": 6549796, "sha256": "f59b3d83…", "mps_ok": true}
# dummy forward 0.625s on mps
# PASS 3/3
```

它把 `yolov8n.pt` 抓到 `00-weights/`（已存在就沿用，離線也還跑得動）、記 sha256、
然後**真的 forward 一張 640×640** 並 assert 權重確實落在 `mps` 上。
`torch.backends.mps.is_available()` 只是宣稱支援，不是證據 —— 把 device 改成 `cpu` 這支會紅。
本機實測：process 內第一次 forward 4.5–6 秒（含 Metal shader 首次編譯），第二次起 0.63 秒。
`00-weights/` 在 `.gitignore` 裡，權重不進 repo。

## 地基自檢（不必起 server）

```bash
uv run python -m src.app.bus           # seq 發號 / 越權守門 / since 續讀 / 200 筆上限 / 跨 process / SSE 框
uv run python -m src.app.registry      # id 發號 / 快照逐欄 / 帳本摺疊 / 重啟認領
uv run python -m src.autolabel.split   # 分層抽樣：與前端 mulberry32 逐位相同
uv run python -m src.autolabel.geometry  # auto-bbox：IoU 中位數 + 框數上限（逐型態拆開看）
uv run python scripts/selfcheck.py     # M1 三條驗收 assert（會自己起一個 uvicorn，跑完自己收）
```

## 目錄

```
cv-self-evolving-console/
├── _Context/
│   ├── DESIGN.md                 ← 唯一設計真相（773 行，已定案）
│   ├── api-contract.md 🔒        ← 事件信封 / REST 形狀 / 錯誤碼（改要走 §12）
│   ├── team-roles.md             ← 角色 × 可寫目錄（刻意不重疊）
│   └── class_table.schema.json   ← class 真相表欄位（值待 M2 填）
├── src/app/
│   ├── main.py                   ← 單一 FastAPI app（五個 router + Host 護欄 + 錯誤形狀）
│   ├── bus.py                    ← events.jsonl 唯一 writer + SSE tail + 心跳
│   ├── registry.py               ← runs.jsonl + state.json + 重啟認領
│   └── routers/                  ← console / dataset / train / eval / round（一檔一個擁有者）
├── prototype/index.html          ← 唯一前端（單檔零依賴、無 build step）
├── scripts/                      ← gen_demo_wafers / fetch_weights / prepare_dataset / run_all / selfcheck / …
└── runs/                         ← 執行期產物（gitignore；刻意不在 --reload-dir 監看範圍）
```

## 十一個 stage（前端的 11 格骨架，M0 就定死）

| | stage | 擁有者 | M1 |
|---|---|---|---|
| s01 | 資料一張一張進來 | dataset-truth | ✅ |
| s02 | AI 自己做 labeling（分群 + 命名） | dataset-truth | M2 |
| s03 | 生成 bbox（含天花板證據） | dataset-truth | ✅ |
| s04 | 落檔成 dataset（含硬閘門） | dataset-truth | M2 |
| s05 | 抓模型並自己決定最好的 | training-engineer | M3 |
| s06 | 配方凍結與單一變因宣告 | training-engineer | M3 |
| s07 | 訓練 | training-engineer | M3 |
| s08 | Evaluation + 圖表 | metric-auditor | M4 |
| s09 | 自然語言討論 | experiment-arbiter | M6 |
| s10 | 再討論一輪 → 重 train | experiment-arbiter | M6 |
| s11 | 自我進化模式 | experiment-arbiter | M7 |

骨架 11 格在 M0 就定死，是為了讓前端只寫一次版面 —— M1 沒跑到的格子顯示灰色 `skipped`，
M2 加回來時版面不會跳。

## 進階閱讀

- 設計為什麼長這樣（五位專家為什麼不能兩兩合併）：`_Context/DESIGN.md`
- 想加一個欄位 / 改一個型別：`_Context/api-contract.md` §12 變更流程（console-owner 批准才動）
- 同型態的多 agent 案例：`../computer-vision-wafer-detection/`（4-agent ML pipeline）、`../stock-groups-skills/`（平行鏡頭 + 彙整）
