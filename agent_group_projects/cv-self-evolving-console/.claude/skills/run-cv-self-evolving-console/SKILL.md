---
name: run-cv-self-evolving-console
description: 起這個 CV 自我進化主控台、跑整條 s01→s08、跑驗收 assert 與測試、截 UI 的圖。Use when asked to start / run / build / test / screenshot this app, drive its UI, or check that the pipeline still works.
---

兩個入口：**無頭一條龍** `uv run --extra train python scripts/run_all.py`（不用瀏覽器、不用 uvicorn，
59 秒跑完 s01→s08），**UI** 則是起 uvicorn 後開 `http://127.0.0.1:8000/` 按那顆按鈕。
要驗「還活著」不用開瀏覽器 —— `scripts/selfcheck.py` 三條 assert 2.4 秒給答案。

以下路徑都相對於 `agent_group_projects/cv-self-evolving-console/`。
所有數字都是 2026-09-12 在 **macOS 26.5.1 · arm64（Apple Silicon）** 實跑出來的，不是抄 README。

## Prerequisites

這台機器不是 Ubuntu，沒有 apt。實際用到的只有三樣：

```bash
uv --version    # 0.12.7
node -v         # v26.7.0 —— selfcheck 的 assert B 會真的用 node 跑前端抽出來的 stratifiedSplit
curl --version  # 健康檢查與 API 都走 curl
```

沒有 node 也跑得完，assert B 會退回比對凍結的黃金值並在輸出標明。
訓練走 **MPS**（`PYTORCH_ENABLE_MPS_FALLBACK=1` 已寫在 `src/train/runner.py`），不需要 CUDA。

## Setup

```bash
uv sync --extra train    # 60 個套件（.venv 1.1 GB）；已同步時實測 0.04 秒
```

`--extra train` 不是可選的：`s05`（探針）/`s07`（訓練）/`s08`（推論）會起需要 torch 的子行程。

```bash
uv run --extra train python -c "import torch;print(torch.__version__, torch.backends.mps.is_available())"
# -> 2.14.0 True
```

`.env` 只有 `source:"roboflow"` 那條路要（`ROBOFLOW_API_KEY`）；`source:"demo"`（預設）零帳號零網路。

## Build

沒有 build step。前端是單檔零依賴的 `prototype/index.html`，由同一個 FastAPI process 從 `/` 送出。

## Run（agent path — 無頭，不開瀏覽器）

```bash
CV_LLM_CMD=nope uv run --extra train python scripts/run_all.py --preset real
```

`CV_LLM_CMD=nope` 讓 s02 的命名走**降級路徑**（機械命名）：完全離線、不碰帳號、畫面上會標黃。
拿掉它就會真的呼叫 `claude -p`（要網路，並算進登入帳號的用量；README 記 150–200 秒 / 折算
$0.25–0.41 —— **那條我沒跑**）。本機是 `billingType: stripe_subscription`，那個 $ 是依 API
定價折算的用量計，不是帳單。

實測輸出（120 張・preset real・**59.13 秒**・$0）：

```
run r145 · ds ds146 · preset real · limit 120
[drive_run] r145 s01 done 0.01s — 120 張進場
[drive_run] r145 s03 done 0.39s — 120 張有框
[drive_run] r145 s02 done 0.45s — k=6 · class 表 v1 · 命名 fallback
[drive_run] r145 s04 done 0.04s — train 48 / valid 12 · selfcheck 全過
[drive_run] r145 s05 done 16.78s — 探針 yolov8n, yolov8s → …（見 Gotchas，這行印的不是選中的）
[drive_run] r145 s06+s07 done 24.44s — yolov8s · 10/10 epoch · 24.0 秒
[drive_run] r145 s08 done 17.02s — mAP50-95 0.3218（CI 0.2026–0.4299）· verdict suspect
run r145 → done · GPU 0.68 分 · LLM 1 次 · $0
```

只跑前兩段（教學用、10 秒內）：`--stages s01,s03`。

落點：事件 `runs/<run_id>/events.jsonl`、帳本 `runs/runs.jsonl`、資料集 `02-dataset/`、
權重與訓練結果 `00-weights/` 與 `04-experiments/`。

## Run（server + UI）

```bash
CV_LLM_CMD=nope nohup uv run --extra train uvicorn src.app.main:app --reload --reload-dir src \
  > /tmp/cv-console.log 2>&1 &
for i in $(seq 1 40); do curl -sf localhost:8000/healthz >/dev/null && break; sleep 0.5; done
curl -s localhost:8000/healthz
# -> {"ok":true,"contract_v":1,"milestone":"M5","pipeline":"s01-s08","sources":["demo","roboflow"]}
```

`--reload-dir src` 不能省（見 Gotchas）。log 在 `/tmp/cv-console.log`。收工：

```bash
lsof -ti:8000 -sTCP:LISTEN | xargs -r kill
```

純 curl 驅動（和按鈕走同一個函式）：

| 指令 | 做什麼 |
|---|---|
| `curl -s -X POST localhost:8000/api/v1/runs -H 'content-type: application/json' -d '{"preset":"real"}'` | 開一條 run，回 202 + `run_id`（一次只准一個，第二次回 409） |
| `curl -s localhost:8000/api/v1/runs/<run_id>` | 快照：`status` / `stages`（dict 不是 list）/ `split` / `recipe` / `budget` |
| `curl -sN 'localhost:8000/api/v1/runs/<run_id>/events?since=0'` | SSE 全段回放，Ctrl-C 離開 |
| `curl -s -X POST localhost:8000/api/v1/runs/<run_id>/cancel` | 中止，實測回 `{"status":"cancelled","stop_seq":58,…}` 且子行程確認死掉才回 |

UI 與截圖（用 playwright MCP，實測可行）：

```
browser_navigate http://127.0.0.1:8000/
browser_evaluate () => { const p=[...document.querySelectorAll('select')]
      .find(s=>[...s.options].some(o=>o.value==='real'));
    p.value='real'; p.dispatchEvent(new Event('change',{bubbles:true}));
    [...document.querySelectorAll('button')].find(b=>b.textContent.includes('開始 run')).click(); }
browser_take_screenshot   # 檔案落在專案根目錄，例如 run-r144-done.png
```

實測從按下去到 `verdict` 事件 **61.4 秒 / 548 筆事件**，跑完會自己切到「評分圖表」分頁，
右欄顯示 split 48/12/40 與 `yolov8s / 10 epoch / imgsz 320 / mps`。

第四個分頁是**標註器**（不必先開 run）：左上下拉挑 dataset（`GET /api/v1/datasets` 列最近 50 個）
→ 圖上拖曳畫框、
1–9 選類別、Backspace 刪框，或按「匯入現成答案 / 一鍵幾何 / 一鍵 AI 看圖」。
實測拖 0.25,0.25 → 0.55,0.60 得到 `{cx:0.4, cy:0.425, w:0.3, h:0.35}`（逐位相符），
存檔後 `GET .../human/boxes/{id}` 讀得回同一組數字。「一鍵 AI 看圖」會起一次 `claude -p`
（實測單張 29 秒、折算 $0.196）—— 本機是 `billingType: stripe_subscription`，**折算值不是帳單**，
消耗的是訂閱用量額度；幾何與匯入完全離線、不碰帳號。

## Test

四項，全部實跑過：

```bash
uv run python scripts/selfcheck.py
# -> 3/3 PASS · 2.4 秒（事件重播一致 / 抽樣與前端一致 / auto-bbox IoU 中位數 1.0000 >= 0.6）
#    它自己起一個隨機 port 的 uvicorn，不會撞到你手邊那個 8000

uv run --with pytest --extra train pytest -q
# -> 26 passed, 2 warnings in 1.40s        （pytest 不是專案依賴，--with 不能省）

for m in src.app.bus src.app.registry src.app.procs src.autolabel.split src.autolabel.geometry \
         src.autolabel.cluster src.autolabel.human src.train.runner src.eval.metrics \
         src.app.routers.console; do
  printf "%-28s " "$m"; uv run --extra train python -m $m >/tmp/m.txt 2>&1; printf "rc=%s " $?; tail -1 /tmp/m.txt
done
# -> 十支全 rc=0 · 全 PASS
```

前端自檢（要瀏覽器）：開 `http://127.0.0.1:8000/?selftest=1`，讀 `#selftest` 的最後一行 ——
實測 **「全部通過（51 條）」**（條數以畫面最後一行印的為準，別信註解）。

## Gotchas

- **server 少了 `--extra train`** — s01–s04 正常、**s05 紅燈**，錯誤只寫「結束時狀態是 crashed」，
  真因是 `ModuleNotFoundError: ultralytics`。起 server 那行就帶上。
- **裸 `uv sync` 會把 train extra 拆掉** — 實測 `uv sync --dry-run` 印「Would uninstall 33 packages」。
  但**裸 `uv run` 不會**（inexact，只補不移除）：跑完 `uv run python scripts/selfcheck.py`，
  `torch 2.14.0` 還在。README 第 76 行說「`uv run`（不帶 extra）會把環境同步回去」，**那行是舊的**。
- **`--reload-dir src` 不能省** — 不限定目錄的話，`runs/` 每寫一筆事件就觸發 reload，driver 當場被殺。
- **一次只准一個 run** — 第二次 `POST /runs` 實測回 `409`。先 cancel 或等它跑完。
- **Host header 守門** — 實測 `evil.com`、`0.0.0.0` 都回 `403`，只有 `localhost` / `127.0.0.1` 放行。
  不要 bind `0.0.0.0`，也不要用別的 Host header 打 API。
- **`run_all` 的 s05 摘要那行印的不是選中的模型** — 實測印「→ yolov8n」，但同一輪的
  `model.selected` 事件與 `recipe.model` 都是 **yolov8s**（`routers/console.py:188` 讀的是探針紀錄的
  `model` 欄，不是勝出者）。要看真的選了誰：`curl …/runs/<id> | jq .recipe.model`。
- **demo 是合成資料** — IoU 天花板 1.0000 是**退化值**（GT 與 auto-bbox 同一套連通分量定義），
  `verdict` 實測是 `suspect`（`overfit_gap` 沒過），**不是壞掉**。真天花板要 `source:"roboflow"`。

## Troubleshooting

- **`error: Failed to spawn: pytest / No such file or directory (os error 2)`**：pytest 不在 `.venv`
  裡（`pyproject.toml` 故意不裝）。用 `uv run --with pytest --extra train pytest -q`，
  或直接 `uv run python tests/test_ci.py`（檔案自己有 `__main__`，實測 8 條 PASS）。
- **port 8000 起不來**：`lsof -ti:8000 -sTCP:LISTEN | xargs -r kill`，再起。
- **`POST /runs` 回 409**：上一條還在跑。`curl -s -X POST localhost:8000/api/v1/runs/<run_id>/cancel`。
- **瀏覽器 console 有一個 error**：`favicon.ico 404`。只有這一個，不用查。
