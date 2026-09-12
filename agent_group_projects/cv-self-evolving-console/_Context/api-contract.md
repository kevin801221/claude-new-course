# API 契約 🔒（console-owner 凍結・M0 閘門）

> 這份是五位專家 + 前端的**共同地基**。唯一設計真相是 `_Context/DESIGN.md`；本檔把 DESIGN 的形狀寫死到欄位名與型別。
> 🔒 = 已凍結，**不准自己改**。要改走 §12 變更流程（console-owner 批准，其他四位不得自行改本檔）。
> 凍結日：2026-09-11・凍結人：console-owner（team lead）・契約版本 `v` = 1

---

## 0. 里程碑對照（這一輪實作到哪）

| 狀態 | 端點 | 里程碑 |
|---|---|---|
| ✅ 實作 | `POST /api/v1/runs`、`GET /api/v1/runs/{run_id}/events`、`GET /api/v1/runs/{run_id}`、`POST /api/v1/runs/{run_id}/cancel`、`POST /api/v1/datasets/ingest`、`POST /api/v1/label/auto`（僅 `mode:"refine_only"`）、`GET /api/v1/datasets/{ds}/images`、`GET /api/v1/images/{image_id}` | M1 |
| ⛔ 佔位回 `501` | 其餘全部（見 §9） | M2–M7 |

> 2026-09-12 更新：M2–M4 落地後，那顆按鈕（`POST /runs` → `drive_run()`）一路跑 `s01`→`s08`；
> `s09`..`s11` 仍是 `skipped`（M6/M7）。上表「⛔ 佔位回 501」只剩 §9 表裡標 M6/M7 的那幾支。
>
> 2026-09-12 再更新：`source:"roboflow"` 落地（WM-811K 409 張＋人工 bbox），`POST /runs` 與
> `POST /datasets/ingest` 兩條入口都接受它，**只剩 `local` 回 501**。`/healthz` 的 `milestone`
> 同步改成 `"M5"`（回 `"M1"` 的健康檢查等於叫接手的人相信 s04–s08 還沒做）。

M1 只跑 `s01`→`s03`。`s04`..`s11` 在 stage 骨架裡存在、狀態一律 `skipped`，前端要能畫出灰色的它們（否則 M2 加回來時版面會跳）。
**理由**：stage 骨架在 M0 就定死 11 格，是為了讓前端只寫一次版面；佔位端點回 501 而不是 404，是為了讓呼叫方分得出「還沒做」與「打錯路徑」。

---

## 1. 通則 🔒

| 項目 | 凍結值 | 理由 |
|---|---|---|
| base path | `/api/v1` | 版本在路徑上，契約破壞式改動走 `/api/v2`，不靠 header 協商 |
| 綁定位址 | `127.0.0.1`，`Host` header 非 `localhost`/`127.0.0.1`/`::1`/`[::1]` → `403` | 自主模式無人看管 + 公開網址 = 任何人都能燒你的 GPU 與 token。白名單只放真的是本機的四個字串：`0.0.0.0` 是威脅模型本身、`testserver`（TestClient 預設 host）是為測試在生產邊界開洞 |
| 回應型別 | `application/json; charset=utf-8`（`/images/{id}` 例外，回 `image/png`） | — |
| SSE 型別 | `text/event-stream; charset=utf-8`，加 `X-Accel-Buffering: no`、`Cache-Control: no-cache` | 不加這兩個 header，反向代理與瀏覽器會把串流緩衝成一坨 |
| **會產生事件的 `POST` 一律回 `202 Accepted` + JSON，不回串流** | 見 §2 裁決 A | 全系統只有一條 SSE 出口；`EventSource` 只能 GET，POST 物理上訂閱不了 |
| 錯誤形狀 | `{"code":"<MACHINE_CODE>","detail":"<繁中一句話>"}` | `detail` 沿用 FastAPI 既有鍵名不另發明；`code` 給前端做分支（前端只有一套錯誤渲染器） |
| body 驗證失敗 | 一律轉成 `400`（掛 `RequestValidationError` handler），**不准漏出 FastAPI 預設的 `422`** | 前端只認一種錯誤形狀 |
| 時間 | ISO8601、UTC、毫秒、結尾 `Z`（`2026-09-11T04:12:33.412Z`） | 事件要能按字串排序 |
| 座標 | bbox 一律 `cx,cy,w,h` 正規化到 `[0,1]` 的 float（YOLO 慣例） | 與既有 `prepare_dataset.py` 的 label 驗證同一套，前端疊框直接乘寬高 |
| 尺寸單位 | 時間 `_s`/`_min`、記憶體 `_mb`、金額 `_usd` 一律寫在欄名尾 | 免得 ETA 到底是秒還是分這種整合當天才炸的爭議 |
| 啟動指令 | `uv run uvicorn src.app.main:app --reload --reload-dir src` | 預設 `--reload` 會因為 `runs/` 被寫入而每秒重啟數次並殺掉 driver |

---

## 2. 事件信封 🔒（唯一真相）

落檔位置：`runs/<run_id>/events.jsonl`，**append-only、單 writer、一行一包**。

| 欄位 | 型別 | 必填 | 凍結規則 |
|---|---|---|---|
| `v` | int | ✅ | 契約版本，目前一律 `1`。不認識的 `v` 前端直接忽略該包並在 log 印一行 |
| `seq` | int | ✅ | 全 run 唯一、單調遞增、**從 1 開始**（所以 `since=0` 自然等於全段回放）。由 bus 單 writer 發號，不准由任何專家自己算 |
| `ts` | string | ✅ | ISO8601 UTC 毫秒 |
| `run_id` | string | ✅ | `r<int>`，例 `r7` |
| `stage` | string | ✅ | `s01`..`s11`，見 §3 |
| `type` | string | ✅ | `<域>.<動作>`，見 §4。前綴即擁有者 |
| `actor` | string | ✅ | 五個 slug 之一：`dataset-truth` / `training-engineer` / `metric-auditor` / `experiment-arbiter` / `console-owner` |
| `data` | object | ✅ | 型別隨 `type`（見 §4 表）。**一律是 object，不准是陣列或純量** |
| `text` | string | ⬜ | 已格式化好的一行字串，前端 `#log` 現有渲染器直接印，不再拼字 |

完整落檔範例（一行，此處為閱讀換行）：

```json
{"v":1,"seq":1042,"ts":"2026-09-11T04:12:33.412Z","run_id":"r7","stage":"s07",
 "type":"train.epoch","actor":"training-engineer",
 "data":{"epoch":12,"total":50,"box_loss":1.234,"cls_loss":0.876,"dfl_loss":1.011,
         "map50":0.567,"map5095":0.312,"mem_mb":2048,"eta_s":410},
 "text":"epoch  12/50  box_loss 1.234  mAP50 0.567"}
```

SSE 線上框（`GET /api/v1/runs/{run_id}/events`）：

```
id: 1042
data: {"v":1,"seq":1042,"ts":"2026-09-11T04:12:33.412Z","run_id":"r7","stage":"s07","type":"train.epoch","actor":"training-engineer","data":{"epoch":12,"total":50,"box_loss":1.234,"cls_loss":0.876,"dfl_loss":1.011,"map50":0.567,"map5095":0.312,"mem_mb":2048,"eta_s":410},"text":"epoch  12/50  box_loss 1.234  mAP50 0.567"}

: ping

```
（每框以空行結束；`: ping` 是註解行，前端 `EventSource` 不會觸發任何 handler。）

### 裁決 A：線上框**不送 `event:` 欄位** 🔒
DESIGN 同時要求「線上框帶 `event: train.epoch`」與「前端統一 `es.onmessage` → `switch(ev.type)`，不要 `addEventListener(type)` 綁 20 種」——這兩條在瀏覽器裡互斥：帶了 `event:` 名稱的框**不會**觸發 `onmessage`，只會觸發 `addEventListener("train.epoch")`。
**凍結**：只送 `id:` + `data:`，`type` 在 JSON body 裡。保留 DESIGN 的前端規則（新增 type 不會漏接），捨棄 `event:` 那一行。
要按 type debug 就 `jq 'select(.type=="train.epoch")' runs/r7/events.jsonl`。

### 裁決 B：誰能寫 events.jsonl 🔒
只有兩處可以 append：`src/app/bus.py`（唯一 writer，API process 內）與 `src/train/callbacks.py`（訓練子行程內，**必須 import bus 的 `append_event()` 做序列化與發號**，不准自己拼 JSON）。其他人一律呼叫 bus。
**理由**：seq 單調與重播語意只要有第二個手寫 writer 就死。

---

## 3. stage 代號表 🔒

| 代號 | 名稱 | 擁有者 | M1 |
|---|---|---|---|
| `s01` | 資料一張一張進來 | `dataset-truth` | ✅ |
| `s02` | AI 自己做 labeling（分群 + 命名） | `dataset-truth` | ⛔ skipped（M2） |
| `s03` | 生成 bbox（含天花板證據） | `dataset-truth` | ✅（只跑第一階段，無類別） |
| `s04` | 落檔成 dataset（含硬閘門） | `dataset-truth` | ⛔ skipped（M2） |
| `s05` | 抓模型並自己決定最好的 | `training-engineer` | ⛔ skipped（M3） |
| `s06` | 配方凍結與單一變因宣告 | `training-engineer` | ⛔ skipped（M3） |
| `s07` | 訓練 | `training-engineer` | ⛔ skipped（M3） |
| `s08` | Evaluation + 圖表 | `metric-auditor` | ⛔ skipped（M4） |
| `s09` | 自然語言討論 | `experiment-arbiter` | ⛔ skipped（M6） |
| `s10` | 再討論一輪 → 重 train | `experiment-arbiter` | ⛔ skipped（M6） |
| `s11` | 自我進化模式 | `experiment-arbiter` | ⛔ skipped（M7） |

stage 狀態 enum 🔒：`pending` / `running` / `done` / `failed` / `skipped` / `reused` / `awaiting_go`
（`reused` = 最小重跑時「沿用上輪」；`awaiting_go` = 手動單次訓練的人工 GO 閘門。）

run 狀態 enum 🔒：`queued` / `running` / `done` / `failed` / `cancelled` / `crashed`
（`converged` / `abandoned` 不是 run 狀態，是 `stop` 事件的 `reason`。）

---

## 4. `type` 命名空間與擁有者 🔒

**前綴即擁有者。任何人只准發自己前綴的 type，違反視同越權改別人的目錄。**

| 前綴 | 擁有者 |
|---|---|
| `ds.*` `label.*` `class.*` | `dataset-truth` |
| `model.*` `probe.*` `train.*` `sweep.*` `recipe.*` `augment.*` | `training-engineer` |
| `eval.*` `test.*` `charts.*` | `metric-auditor` |
| `chat.*` `expert.*` `arbiter.*` `round.*` `autonomy.*` `stop` | `experiment-arbiter` |
| `run.*` `stage.*` `provenance.*` | `console-owner` |

全 type 清單（`data` 欄位取自 DESIGN；標 ✅ 的在 M1 型別已凍結，其餘欄位名已定、型別由擁有者在對應里程碑走 §12 補細）：

| type | stage | `data` 欄位 | M1 |
|---|---|---|---|
| `run.created` | s01 | `{run_id, mode, source, preset, stages[], ds_id}` | ✅ |
| `ds.total` | s01 | `{total:int, source:string}` | ✅ |
| `ds.image` | s01 | `{id, name, url, cls:string[], counts:object, split:null}` | ✅ |
| `ds.progress` | s01 | `{loaded:int, total:int}` | ✅ |
| `label.mask` | s03 | `{image_id, defect_ratio:float, n_components:int}` | ✅ |
| `label.bbox` | s03 | `{image_id, boxes:[{cls:int, cx,cy,w,h:float, conf:float, rule:string}]}` | ✅ |
| `label.lowconf` | s03 | `{image_id, conf:float}` | ✅ |
| `label.anchor_iou` | s03 | `{source:"synthetic"\|"roboflow_anchor", iou_median:float, iou_hist:int[], n:int, threshold:float, verdict:"pass"\|"fail"}` | ✅ |
| `stage.failed` | any | `{stage, detail}` | ✅ |
| `stage.note` | any | `{stage, kind, …}`（`text` 才是重點） | M5 |
| `label.descriptor` | s02 | `{image_id, ring_ness, center_ness, elongation, edge_frac}` | M2 |
| `class.cluster` | s02 | `{k, silhouette, cluster_sizes, montage_urls}` | M2 |
| `class.table.frozen` | s02 | `{version, nc, names, naming_rationale, naming?, naming_error?}` | M2 |
| `ds.split` | s04 | `{train, valid, unassigned}` | M2 |
| `ds.gt_partition` | s04 | `{anchor:40, sealed_test:80, unused}` | M2 |
| `ds.written` | s04 | `{path, data_yaml}` | M2 |
| `ds.selfcheck` | s04 | `{checks:[{name, pass, detail}]}` | M2 |
| `model.candidates` | s05 | `{list}` | M3 |
| `model.weights.fetched` | s05 | `{name, bytes, sha256, mps_ok}` | M3 |
| `probe.epoch` | s05 | `{name, epoch, map5095}` | M3 |
| `model.selected` | s05 | `{name, score, why}` | M3 |
| `recipe.proposed` | s06 | `{params, augment, rationale, eta_min}` | M3 |
| `augment.decision` | s06 | `{name, on, why}` | M3 |
| `train.calibrated` | s06 | `{scale_factor, probe_2ep_sec}` | M3 |
| `recipe.frozen` | s06 | `{recipe_id, diff_vs_last}` | M3 |
| `train.start` | s07 | `{run_id, pid, eta_min}` | M3 |
| `train.epoch` | s07 | `{epoch,total,box_loss,cls_loss,dfl_loss,map50,map5095,mem_mb,eta_s}` | M3 |
| `train.warn` | s07 | `{msg}` | M3 |
| `train.done` | s07 | `{best_pt, elapsed_s}` | M3 |
| `train.cancelled` | s07 | `{}` | M3 |
| `sweep.trial` | s07 | `{trial, factors, map5095}` | M6 |
| `eval.noise_floor` | s08 | `{sigma, noise_floor, seeds}` | M4 |
| `eval.leakage` | s08 | `{overlap_ids, near_dup}` | M4 |
| `eval.perclass` | s08 | `{cls, ap50, recall, support, ci_lo, ci_hi}` | M4 |
| `eval.verdict` | s08 | `{verdict, checks, delta, significant, anchor_ok}` | M4 |
| `charts.series` | s08 | `{curves\|pr\|confusion\|per_class}`＋選填 `ceiling{median, ci, degenerate, credible, source, anchor, sampling_note}` | M4 |
| `test.image` | s08 | `{image_id, pred_url, boxes, iou_vs_label}` | M4 |
| `chat.user` | s09 | `{text}` | M6 |
| `arbiter.ask` | s09 | `{metric, class, direction, forbid}` | M6 |
| `expert.msg` | s09 | `{expert, factor, direction, expected_delta, evidence_path, cost_min, status}` | M6 |
| `round.opened` | s10 | `{round_id, ask}` | M6 |
| `expert.challenge` | s10 | `{from, target, reason, alternative}` | M6 |
| `arbiter.decision` | s10 | `{one_factor, patch, reason, budget_left}` | M6 |
| `provenance.check` | s10 | `{plan_vs_actual_diff, provenance_ok}` | M6 |
| `round.closed` | s10 | `{next_run_id, rerun_from, reused_stages}` | M6 |
| `autonomy.on` | s11 | `{preset, max_rounds, gpu_min_cap, llm_calls_cap, usd_cap}` | M7 |
| `autonomy.tick` | s11 | `{round, gpu_min_used, llm_calls, cost_usd, best_map5095}` | M7 |
| `stop` | s11 | `{reason, best_run, best_pt, detail?}` | M7（`reason:"user_cancel"` M5、`reason:"label_ceiling"` M5） |

> **命名空間補一條**：DESIGN 的 s09/s10 用了 `arbiter.ask` / `arbiter.decision`，不在它列的那組前綴裡。**凍結為 `experiment-arbiter` 的第六個前綴 `arbiter.*`**（已併入上表），不改 DESIGN 寫出來的 type 名 —— 改名等於改前端 switch。
> `provenance.check` 的 `actor` 是 `console-owner`（比對的手不能是有 ML 立場的人），但 `stage` 記在 s10。

---

## 5. SSE 重連語意 🔒

`GET /api/v1/runs/{run_id}/events?since=<seq>`

1. `id:` = `seq`。斷線後 `EventSource` 自動帶 `Last-Event-ID` 回來，server 從**該 seq 之後**（嚴格大於）續發。
2. `?since=<seq>` 與 `Last-Event-ID` 語意完全相同：回 `seq > since` 的事件。`since=0`（或不給）= **全段回放**（seq 從 1 起算）。
3. **兩者同時出現時 `Last-Event-ID` 勝** 🔒。瀏覽器重連會沿用原始 URL（可能還掛著 `since=0`），若讓 URL 勝，每次斷線都會從第一筆重播到現在，越跑越慢。
4. 每 15 秒發一框心跳 `data: {"v":1,"type":"heartbeat"}` 撐反向代理與背景分頁節流。
   **不是 `: ping` 註解行** —— 註解行瀏覽器不會交給 JS，所以第 5 條那個 45 秒看門狗看不到它，
   每個超過 45 秒的 stage（LLM 命名約 150 秒、noise floor 約 70 秒）都會固定觸發一次假斷線重連。
   心跳沒有 `seq`、不落檔，前端 reducer 第一行就把它擋掉（不進 `#log`、不動 `seenSeq`）。
5. 前端 **45 秒**沒收到任何東西（含 ping）就主動 `es.close()` 再開一條，帶上已收到的最大 seq。半開連線 `EventSource` 不會自己重連。
6. run 結束（`done`/`failed`/`cancelled`/`crashed`）且沒有新事件時，server 發最後一框後**保持連線並繼續 ping**，不主動關（關了瀏覽器會 3 秒重連一次無限輪）。前端收到 `stop` 或快照 `status != running` 才自己 close。
7. 一個 run 只開一條 SSE。五位專家不各開端點（HTTP/1.1 同源 6 連線上限，開兩個 tab 就撞頂；而且訓練是子行程，跨 process 只有檔案這條路）。
8. 不依賴 SSE 的退路：`GET /api/v1/runs/{run_id}` 是純快照，SSE 全掛也能重新整理看到現況。
9. `run_id` 不存在 → `404`（**不是** 200 空串流，否則前端會一直等）。
10. **run 收工後 `bus.append_event()` 一律拒收**（回 `None`）。被 cancel 掉的 run 底下還有
    `asyncio.to_thread` 裡的 thread 在跑（`to_thread` 不可取消），它們會在 run 標成 cancelled
    之後繼續追加事件 —— 實測 cancel 後 24 秒還長出一筆 `eval.noise_floor`（seq 比 `stop` 大）。
    所以**終局的墓碑事件**（`stop` / `stage.failed`）要寫在翻狀態**之前**。

---

## 6. 冪等契約 🔒

**任何事件重放兩次，畫面不得壞。** 重播邊界（`Last-Event-ID` 的那一筆、伺服器重啟、前端 45 秒自救重連）**會重疊一筆**，這是協議允許的，所以 reducer 必須冪等。

| type | 主鍵 | 動作 |
|---|---|---|
| `ds.image` | `data.id` | **upsert**，不是 push（push 會長出兩張一樣的縮圖） |
| `label.bbox` | `data.image_id` | **整包 `boxes` 取代**，不是 append（append 會在同一張圖上疊兩層框，IoU 看起來還會變好，是最惡毒的一種） |
| `label.mask` / `label.lowconf` | `data.image_id` | upsert |
| `ds.progress` | `run_id` | 取 `max(loaded)`，允許亂序，不准倒退 |
| `train.epoch` | `(run_id, data.epoch)` | upsert（曲線上同一個 x 只能有一點） |
| `probe.epoch` | `(run_id, data.name, data.epoch)` | upsert |
| `eval.perclass` | `(run_id, data.cls)` | upsert |
| 其餘一次性事件（`run.created`/`class.table.frozen`/`train.done`/`stop`…） | `seq` | 同 seq 只處理一次；前端保留 `seenSeq` 上界，`seq <= seenSeq` 直接丟掉 |

後端側的對應義務：同一個邏輯事實**不准發兩個不同 seq**（例如重試 ingest 不可重發 120 筆 `ds.image` 到同一個 run）；要重做就開新 run。

---

## 7. 大量事件的游標規則 🔒

1. `ds.image` 與 `test.image` **每個 run 只在 SSE 送前 200 筆**，第 201 筆起改送 `ds.progress {loaded,total}` 游標；落檔（events.jsonl）同樣只寫前 200 筆影像事件，**不是寫了不送**（否則 `since=0` 回放還是會吐幾千筆）。
2. SSE ring buffer 只保最後 **2000** 筆；`since` 落在 buffer 之外時 server 從 `events.jsonl` seek 補（檔案是真相，buffer 只是加速）。
3. **重連順序寫死**：先打 REST 快照 `GET /api/v1/runs/{run_id}` → 再打影像快照 `GET /api/v1/datasets/{ds}/images?offset=0&limit=200`（分頁到 total）→ 最後才帶 `since=<last_seq>` 接串流。**早期縮圖絕不依賴 replay。**
4. M1 是 120 張（< 200），走不到游標分支 —— 但**前端 M1 就要把這個分支寫掉並用 `?selftest=1` 蓋住**，因為 M8 接 Roboflow 是 409 張，當天才寫就是當天才爆。

---

## 8. REST — M1 實作（形狀已凍結）

> 前端那一顆按鈕**只打 `POST /api/v1/runs` 一支**。`/datasets/ingest` 與 `/label/auto` 是同一組內部函式的 HTTP 入口（給 curl 單步教學與 M2 之後單獨重跑用），**不是第二套邏輯**；`scripts/run_all.py` 也呼叫同一個函式。

### 8.1 `POST /api/v1/runs` — 開一個 run（那顆按鈕）

Request：
```json
{"mode":"oneshot","source":"demo","preset":"teaching","limit":120,"seed":42}
```
- `mode` ∈ `oneshot` | `manual` | `autonomous`（M1 只接受 `oneshot`，其餘回 `501`）
- `source` ∈ `demo` | `roboflow` | `local`（M1 只接受 `demo`，其餘回 `501`）
- `preset` ∈ `teaching` | `real`（預設 `teaching`）。M1 的行為差別只有逐張節奏：`teaching` 每張停 0.12 秒（120 張 × 2 個 stage ≈ 30 秒，縮圖牆看得見一張一張長出來、Stop 鈕按得到），`real` 不停頓（≈ 2 秒，`selfcheck.py` 與 M5 的一鍵腳本走這條）。M3 起 `preset` 另外決定三軸預算。
- `limit` int 1..500（預設 120）、`seed` int（預設 42）
- `labels` ∈ `auto` | `human`（選填）、`workspace` / `project` string（選填）、`version` int（選填）
  —— 只有 `source:"roboflow"` 用得到，逐欄與 §8.5 的 `POST /datasets/ingest` 相同（那顆按鈕與
  curl 單步教學走同一組函式，參數長得不一樣就是第二套邏輯）。預設資料集 `wm811k-paasr` /
  `wm811k` / v3。**刻意沒有 `api_key` 這一欄**：key 只在 server 讀 `.env` 的 `ROBOFLOW_API_KEY`。
  `labels` 在這支的預設是 `"auto"`（§8.5 那支仍是 `"human"`）：`human` 目前會停在 s02 的
  `409 CLASS_TABLE_SCHEMA_CONFLICT`，而一顆必定紅燈的按鈕不是功能是陷阱。
  下載（或沿用）在**開 run 之前同步做完**，沒 key / key 錯 / 連不上一律當場回 4xx 且**不開 run**
  —— 先回 202 再讓它死在 s01 的話，畫面上會多一條紅色的鬼 run，而真正的原因只有 log 看得到。
- `stages` string[]（選填，預設 `null` = 整條龍 s01→s08）。**教學用：只跑前幾段**，例
  `{"stages":["s01","s03"]}`。值只接受 `drive_run` 跑得到的段（見 §8.10 的 `pipeline`），
  表外的值回 `400 BAD_ENUM`。沒被選到的格子從一開始就是 `skipped`（不是 `pending` ——
  標 pending 等於叫學生等一個不會來的東西）。`s07` 會連帶跑 `s06`（凍結配方與起訓練是同一支 POST）。

`202 Accepted`：
```json
{
  "run_id": "r7",
  "ds_id": "ds3",
  "status": "running",
  "created_at": "2026-09-11T04:12:30.001Z",
  "events_url": "/api/v1/runs/r7/events?since=0",
  "ds_version": "v1",
  "label_version": "v1",
  "class_table_version": null,
  "stages": [
    {"id":"s01","name":"資料一張一張進來","owner":"dataset-truth","status":"pending"},
    {"id":"s02","name":"AI 自己做 labeling","owner":"dataset-truth","status":"skipped"},
    {"id":"s03","name":"生成 bbox","owner":"dataset-truth","status":"pending"},
    {"id":"s04","name":"落檔成 dataset","owner":"dataset-truth","status":"skipped"},
    {"id":"s05","name":"抓模型並自己決定最好的","owner":"training-engineer","status":"skipped"},
    {"id":"s06","name":"配方凍結與單一變因宣告","owner":"training-engineer","status":"skipped"},
    {"id":"s07","name":"訓練","owner":"training-engineer","status":"skipped"},
    {"id":"s08","name":"Evaluation + 圖表","owner":"metric-auditor","status":"skipped"},
    {"id":"s09","name":"自然語言討論","owner":"experiment-arbiter","status":"skipped"},
    {"id":"s10","name":"再討論一輪 → 重 train","owner":"experiment-arbiter","status":"skipped"},
    {"id":"s11","name":"自我進化模式","owner":"experiment-arbiter","status":"skipped"}
  ]
}
```
- `class_table_version` 在 s02 凍結前是 `null`。前端看到 `null` 要顯示「未分類」而不是空字串。
- `mode:"oneshot"` 的 `POST /runs` 會啟動 `console.drive_run()`，依 §8.10 的 `pipeline` 順序
  一路跑到 `s08`（每段**真的做完**才標 `done`，全部跑完才把 run 標 `done`）。
  `scripts/run_all.py` 呼叫的是同一個函式，不是第二套 pipeline。
- 同時只准一個 run 在跑（GPU 佇列深度 1）：已有 `status=running` 的 run → `409 RUN_IN_FLIGHT`，
  錯誤 body 多帶一個選填的 `run_id`（`{"code":"RUN_IN_FLIGHT","run_id":"r14","detail":"…"}`）。
  沒有它的話，前端重整頁面之後既接不回那條 run、也按不到 Stop —— 畫面等於叫使用者去 cancel，
  同時把 cancel 拿走。

### 8.2 `GET /api/v1/runs/{run_id}/events` — 全系統唯一 SSE

Query：`since` int ≥ 0（預設 0）。語意見 §5。錯誤：`run_id` 不存在 `404`；`since` 非整數或負數 `400`。

### 8.3 `GET /api/v1/runs/{run_id}` — 快照（後進場、長離線、SSE 掛掉都靠這支）

`200`：
```json
{
  "run_id": "r7",
  "status": "running",
  "mode": "oneshot",
  "preset": "teaching",
  "source": "demo",
  "ds_id": "ds3",
  "last_seq": 812,
  "round": 0,
  "stages": {"s01":"done","s02":"skipped","s03":"running","s04":"skipped","s05":"skipped","s06":"skipped","s07":"skipped","s08":"skipped","s09":"skipped","s10":"skipped","s11":"skipped"},
  "counts": {"total": 120, "loaded": 120, "labeled": 74},
  "budget": {"gpu_min_used":0,"gpu_min_cap":12,"llm_calls":0,"llm_calls_cap":24,"cost_usd":0,"usd_cap":0.5},
  "best_run": null,
  "artifacts": [{"kind":"demo_images","path":"01-raw-data/demo/"},{"kind":"gt","path":"01-raw-data/demo/gt.json"}],
  "autonomy": null,
  "provenance_ok": null,
  "ds_version": "v1",
  "label_version": "v1",
  "class_table_version": null,
  "split": {"train":48,"valid":12,"anchor":20,"sealed_test":40,"unassigned":0},
  "recipe": {"train_id":"r7-t2","status":"done","model":"yolov8n","epochs":10,"imgsz":320,
             "batch":8,"device":"mps","elapsed_s":22.3}
}
```
- `split` 🔒 = **s04 落檔後的分層真相**（`01-raw-data/datasets/<ds>/splits.json` 的摺疊）。
  s04 之前一律 `null`，**不是一組 0** —— 一組 0 會被讀成「都沒分到」，而那正是這一欄要修掉的謊：
  事件裡的 `ds.image.split` 永遠是 `null`（s04 指派時不補發 120 筆），所以前端自己數的側欄
  在一條龍訓練完、verdict TRUSTED 之後照樣寫「train 0 / valid 0 / test 0・還有 120 張沒指派」。
  **分層是後端決定的，就由後端回答**；前端不准自算。`sealed_test` 與 `anchor` 的**張數**本來就在
  `ds.gt_partition` 事件裡（公開的是數量，封印的是「哪幾張、什麼類別」，見 §8.7 的遮蔽）。
- `recipe` 🔒 = 這一輪**真的跑的**配方（s05 探針選的 `model` + s06 凍結的 `epochs`/`imgsz`），
  來源是 `04-experiments/<train_id>/recipe.json`。s07 之前 `null`。同一個 run 跑第二次訓練時取最後一筆。
- 類別分佈**不在這支**：class 真相表是 `GET /api/v1/datasets/{ds}/classes`（全系統唯一 class 來源），
  快照只帶 `class_table_version`，前端拿它當快取鍵去打那一支。

`budget` 在 M1 全部是 0 對 preset 上限（沒有 GPU、沒有 LLM），欄位照樣要在，前端的 budget 條 M1 就畫得出來（顯示 0/12）。

### 8.4 `POST /api/v1/runs/{run_id}/cancel` — 中止整條 run

Request body：無（或 `{}`）。`200`：
```json
{"run_id":"r7","status":"cancelled","stop_seq":813,"trains_killed":["r7-t2"],"children_killed":[43963]}
```
- 語意：停掉 driver、**對還活著的訓練/探針子行程 SIGTERM 整個 process group 並確認收乾**
  （逾時 SIGKILL，推 `train.cancelled`）、寫一筆 `stop {reason:"user_cancel"}`、run 標 `cancelled`。
  **確認死了才回**：回了 `cancelled` 而 GPU 還在燒，是比不回應更糟的假成功。
  `trains_killed` 是這次真的殺掉的 `train_id` 清單（沒有就空陣列）。autonomy 仍在 M7 補。
- `trains_killed` 是訓練/探針；`children_killed` 是其餘子行程的 pid（LLM 命名、noise floor 的
  訓練與推論、eval 推論 —— 它們都在 `to_thread` 裡用 `subprocess` 起，訊號是唯一停得掉的辦法）。
- 已經 `done`/`cancelled` 的 run 再打 → `409 RUN_NOT_CANCELLABLE`（不是 200，否則前端 Stop 鈕會假裝成功）。
  **連按兩次也算**：殺子行程的寬限期（8 秒）是阻塞的，那段時間狀態還是 `running`，
  所以實作要在進入 cancel 的第一時間就把這個 run 記進「正在 cancel」，第二個請求才吃得到 409
  （實測沒有這道門時兩個請求都回 200，事件流長出兩筆 `stop` 與兩筆 `train.cancelled`，違反 §6）。

### 8.5 `POST /api/v1/datasets/ingest` — 逐張進場（s01）

Request：`{"run_id":"r7","source":"demo","limit":120,"seed":42}`
`202`：
```json
{"ds_id":"ds3","run_id":"r7","total":120,"source":"demo","ds_version":"v1"}
```
產生事件：`ds.total` → 每張一筆 `ds.image`（前 200 筆）→ 每 10 張一筆 `ds.progress`。
`source:"local"` → `501`（未排程）。`source:"roboflow"` 已實作：`labels` ∈ `auto`|`human`
（預設 `human`）、`workspace`/`project`/`version` 選填（預設 `wm811k-paasr`/`wm811k`/v3）；
下載在端點內同步做完才回 202，錯誤碼見 §10 的 `ROBOFLOW_*`。
run 的 `status` 不在 `queued`/`running` → `409 RUN_NOT_LIVE`（這支是 `POST /runs` 之外的第二條入口，不守門就會在已收工的 run 上再長一段事件）。
同一個 run 的同一段**只准跑一次** → 第二次 `409 STAGE_ALREADY_RAN`（§6 的後端義務：重試 ingest
不可重發 120 筆 `ds.image` 到同一個 run，要重做就開新 run）。守門守在 driver 與這支共用的那個
函式裡，不是守在端點 —— 守在端點的話那顆按鈕那條路就沒人守。
`preset` 不在 body 裡：沿用該 run 建立時的值（`teaching` 逐張 0.12 秒、`real` 不停頓，見 §8.1）。

### 8.6 `POST /api/v1/label/auto` — auto-label（s02+s03）

Request：
```json
{"run_id":"r7","ds_id":"ds3","mode":"refine_only"}
```
- `mode:"refine_only"` = **只跑第一階段「無類別 bbox」**（缺陷遮罩 → `scipy.ndimage.label` 連通分量 → 形態學去雜點 → 外接框）。M1 唯一可用值。
- `mode:"cluster"` = 兩階段（描述子 → KMeans → montage → 一次 LLM 命名）→ M1 回 `501`（M2 實作）。
- `refine_params` 選填，M1 忽略（M2 起是可 patch 的旋鈕）。

`202`：
```json
{"run_id":"r7","ds_id":"ds3","mode":"refine_only","label_version":"v1","total":120}
```
產生事件：每張 `label.mask` → `label.bbox`（信心低的多一筆 `label.lowconf`）→ 全部跑完一筆 `label.anchor_iou`。
run 的 `status` 不在 `queued`/`running` → `409 RUN_NOT_LIVE`（同 §8.5）；同一段重打 → `409 STAGE_ALREADY_RAN`（同 §8.5）。

**M1 的 `cls` 與 `rule` 寫死** 🔒：
- `boxes[].cls = -1`（int，意思是「還沒有類別」）。M1 沒有 class 真相表，**在這裡填任何類別名就是偷看合成 GT 的答案**，直接違反零人工標註。
- `boxes[].rule = "cc_bbox"`（連通分量外接框）。`rule` enum 由 `dataset-truth` 在 M2 擴充（`ring_annulus` / `pca_axis` / …），M1 只有這一個值。
- `boxes[].conf` = 該分量面積佔最大分量面積的比例（0..1），低於 0.25 額外發 `label.lowconf`。

### 8.7 `GET /api/v1/datasets/{ds}/images` — 分頁快照（Roboflow search 相容形狀）

Query：`offset` int ≥ 0（預設 0）、`limit` int 1..500（預設 200）。
`200`：
```json
{
  "offset": 0,
  "total": 120,
  "results": [
    {"id":"demo0001","name":"wafer_0001.png","url":"/api/v1/images/demo0001",
     "split": null,
     "annotations": {"count": 3, "classes": {"unclassified": 3}}}
  ]
}
```
- 形狀刻意與 Roboflow `search` 相容，既有 `loadRoboflow()` 的映射幾乎不用改。
- M1 `annotations.classes` 一律單鍵 `{"unclassified": n_boxes}`；`split` 一律 `null`（s04 在 M2 才指派）。
- **`split` 的值域 🔒**：`"train"` / `"valid"` / `"anchor"` / `"sealed"` / `null`（= 還沒指派）。
  五個值以外一律不准出現 —— 尤其 `"sealed_test"`：封印那批在這張表上只露 `"sealed"` 且
  `annotations` 為 `null`（遮蔽見下一段）。前端的白名單必須逐值吃得下這五個，
  少吃一個就會把它當成「未指派」，於是側欄誠實寫 231/58/80、同一頁的卡片卻全部標「—」
  （實測 r130：`#fsplit=unassigned` 篩出 120/409）。`?selftest=1` 第 40 條逐值比對這一組。
- `ds` 不存在 → `404 DS_NOT_FOUND`。

### 8.8 `GET /api/v1/images/{image_id}` — 圖片 proxy

Query：`w` int 64..1024 選填（縮圖寬，等比；不給回原尺寸）。
`200`：`image/png` bytes + `ETag: "<sha1 前 16 碼>"` + `Cache-Control: public, max-age=3600`；帶 `If-None-Match` 命中回 `304`。
`image_id` 不存在 → `404 IMAGE_NOT_FOUND`（JSON 形狀，不是空 body）。
**理由**：API key 只留 server env、前端不再持 key，順手解掉「key 只能 localhost」那條部署禁令；M1 的 demo PNG 在本機磁碟，前端也不能用 `file://` 讀。

### 8.10 `GET /api/v1/stages` — stage 表與 driver 的執行順序

無參數。`200`：
```json
{
  "stages": [{"id":"s01","name":"資料一張一張進來","owner":"dataset-truth","status":"pending"}, "… 11 格"],
  "pipeline": ["s01","s03","s02","s04","s05","s06","s07","s08"]
}
```
- `stages` = `registry.STAGE_TABLE` 的 11 格（`status` 是「開一個 run 的話這格會不會跑」）。
- `pipeline` = `console.drive_run()` 的**執行順序**（不是顯示順序：`s03` 要先有框，`s02` 才分得了群）。
- 存在的理由：前端 rail 在後端回應到之前是拿本地那張表畫的，兩邊文案漂開畫面就會說謊。
  `?selftest=1` 的最後一條打這支逐欄比對，對不上或抓不到一律 FAIL。

### 8.9 人工 GO 閘門的明文豁免 🔒

既有 `training-runner.md` 規定訓練前要停下等使用者回 `GO`。本契約裁決：
- `mode:"manual"` 的單次訓練**保留 GO**：`s07` 進 `awaiting_go`，等 `POST /api/v1/train`（`gate:"go"`）放行。
- `mode:"oneshot"` 與 `mode:"autonomous"` **豁免 GO**，改用三軸預算 + `ETA×2` 硬 timeout + 前端常駐 Stop 鈕（收到 `train.cancelled` 才算停成功）取代。
- `ETA×2` 的實作下限是 **10 分鐘**：實測 `eta_min` 0.17 分（10 秒）而探針真的跑 17.8 秒 ——
  純 ETA×2 會把健康的一條龍砍死。這個 timeout 的用途是抓「子行程永遠不會結束」
  （逾時 → 收乾子行程 → `stage.failed` → run `failed`，讓進度條轉紅而不是靜靜卡住），
  不是拿來校準 ETA。
- 這條是**明文豁免**，不是默默跳過。M1 沒有訓練，所以豁免在 M3 才會被用到，但契約現在就寫死，免得 M3 當天又吵一次。

---

## 9. REST — M0 佔位（一律回 `501`）

**已凍結的是「路徑 + 方法 + 擁有者」，不是 body。** 這些端點在 M0 只掛簽章，回：

```json
{"code":"NOT_IMPLEMENTED","detail":"此端點於 M2 實作（擁有者 dataset-truth）"}
```
HTTP `501 Not Implemented`。**不准提前實作、不准改成 404、不准回假資料**（回假資料會讓前端以為自己接好了）。

| Method | Path | 擁有者 | 里程碑 |
|---|---|---|---|
| `POST` | `/api/v1/label/auto`（`mode:"cluster"`） | dataset-truth | M2 |
| `GET` | `/api/v1/datasets/{ds}/classes` | dataset-truth | M2 |
| `POST` | `/api/v1/datasets/{ds}/freeze` | dataset-truth | M2 |
| `GET` | `/api/v1/datasets/{ds}/report` | dataset-truth | M2 |
| `GET` | `/api/v1/models/candidates` | training-engineer | M3 |
| `POST` | `/api/v1/models/probe` | training-engineer | M3 |
| `POST` | `/api/v1/train` | training-engineer | M3 |
| `POST` | `/api/v1/train/{train_id}/cancel` | training-engineer | M3 |
| `GET` | `/api/v1/train/{train_id}/state` | training-engineer | M3 |
| `POST` | `/api/v1/sweep` | training-engineer | M6 |
| `POST` | `/api/v1/eval` | metric-auditor | M4 |
| `GET` | `/api/v1/eval/noise-floor/{ds_version}` | metric-auditor | M4 |
| `GET` | `/api/v1/eval/{run_id}/charts` | metric-auditor | M4 |
| `GET` | `/api/v1/eval/{run_id}/verdict` | metric-auditor | M4 |
| `GET` | `/api/v1/eval/{run_id}/predictions` | metric-auditor | M4 |
| `POST` | `/api/v1/final-test` | metric-auditor | M7 |
| `POST` | `/api/v1/chat` | experiment-arbiter | M6 |
| `POST` | `/api/v1/rounds` | experiment-arbiter | M6 |
| `GET` | `/api/v1/rounds/{round_id}` | experiment-arbiter | M6 |
| `POST` | `/api/v1/rounds/{round_id}/apply` | experiment-arbiter | M6 |
| `POST` | `/api/v1/autonomy` | experiment-arbiter | M7 |
| `GET` | `/api/v1/runs` | experiment-arbiter | M6 |
| `GET` | `/api/v1/runs/{run_id}/provenance` | console-owner | M6 |

**以下端點已實作，不在 501 之列**（本表只記「路徑 + 方法 + 擁有者」，形狀在擁有者的 notes）：

| Method | Path | 擁有者 | 落地 |
|---|---|---|---|
| `GET` | `/api/v1/eval/ceiling?run_id=` 或 `?ds=&scope=anchor\|pool` | metric-auditor | M5 |

- 回標註天花板（人工 GT vs 連通分量 auto-bbox 的 IoU）＋ `gate{abandon, reason:"label_ceiling"}`。
  **s03 一跑完就答得出來、不必先訓練** —— 放棄條件要的就是這個時機。
- 🔒 `scope` 只接受 `anchor` / `pool`，`sealed_test` 直接拒絕（封印的第五道側門）。

---

## 10. 錯誤碼表 🔒

| HTTP | `code` | 觸發條件 |
|---|---|---|
| `400` | `BAD_ENUM` | `mode` / `source` / `preset` / `split` / `mode:"refine_only"\|"cluster"` 等 enum 給了表外的值 |
| `400` | `BAD_RANGE` | `limit` 不在 1..500、`since` 負數或非整數、`w` 不在 64..1024、抽樣比例三個全 0 |
| `400` | `SPLIT_TEST_FORBIDDEN` | `POST /api/v1/eval` 的 `split` 傳 `"test"`（封印 test 的第二道鎖，結構性保證不靠自律） |
| `400` | `AUTONOMY_NO_CAP` | `POST /api/v1/autonomy {on:true}` 沒帶 `preset` 或缺任何一軸上限 —— **沒有「無限進化」這個選項** |
| `400` | `VALIDATION_ERROR` | pydantic body 驗證失敗（由 handler 從 422 轉成 400，統一錯誤形狀） |
| `403` | `NON_LOCAL_HOST` | `Host` header 非 `localhost` / `127.0.0.1`（安全邊界，套用全部 `/api/v1/*`） |
| `404` | `RUN_NOT_FOUND` / `DS_NOT_FOUND` / `IMAGE_NOT_FOUND` / `TRAIN_NOT_FOUND` / `ROUND_NOT_FOUND` | 對應資源不存在。**SSE 的 run 不存在也回 404，不准回空串流** |
| `409` | `RUN_IN_FLIGHT` | 已有 `status=running` 的 run（GPU 佇列深度 1） |
| `409` | `RUN_NOT_CANCELLABLE` | 對已 `done`/`cancelled`/`crashed` 的 run 打 cancel |
| `409` | `STAGE_ALREADY_RAN` | 對同一個 run 的同一段（`ingest` / `label.auto`）打第二次（§6：同一個邏輯事實不准發兩個 seq） |
| `409` | `RUN_NOT_LIVE` | 對已收工的 run 打 `POST /datasets/ingest` 或 `POST /label/auto`（事件只能追加在 `queued`/`running` 的 run 上，否則快照說 done、事件流還在長） |
| `409` | `SELFCHECK_FAILED` | `s04` selfcheck 有 FAIL 時打 `POST /api/v1/train`（前端訓練鈕本來就該 disabled，這是後端那道鎖） |
| `409` | `NOISE_FLOOR_MISSING` | 還沒量 noise floor 就要 arbiter 裁決或套用停止條件 |
| `409` | `NOT_STOPPED_YET` | `POST /api/v1/final-test` 但該 run 家族沒有 `stop` 事件（未 converged/abandoned）——「還沒停，不准看 test」 |
| `409` | `FINAL_TEST_ALREADY_USED` | 同一個 `ds_version` 第二次成功開封 sealed test |
| `409` | `INCOMPARABLE_CLASS_TABLE` | 跨 `class_table_version` 的指標比較請求（直接拒答並標「不可比」，不是回一個假的 Δ） |
| `409` | `PROVENANCE_INVALID` | 引用 `provenance_ok=false` 的 run 當證據 |
| `400` | `ROBOFLOW_KEY_MISSING` | `.env` 沒有 `ROBOFLOW_API_KEY`（或檔案不存在）。`detail` 必須寫清楚**去哪拿 key、填哪個檔** —— 這個錯的全部價值就在於照著做就能修好，所以前端把它放在整列寬的橫幅（14px），不是左欄 11px 小字 |
| `400` | `ROBOFLOW_KEY_INVALID` | key 格式不合（本機擋下，沒打網路）或 Roboflow 回 401 |
| `404` | `ROBOFLOW_NOT_FOUND` | workspace / project 不存在。**version 不存在不算**：退回 `max(version_ids)` 並在 `ds.total` 的 text 標一行 ⚠ |
| `409` | `ROBOFLOW_UNREACHABLE` | 連不到 Roboflow（沒網路 / 防火牆）。demo 那條線全程離線，不受影響 |
| `409` | `ROBOFLOW_DOWNLOAD_FAILED` | export 還在生成、zip 壞掉、解壓後找不到 `<split>/images/`、標註行不是 5 欄 YOLO |
| `409` | `CLASS_TABLE_SCHEMA_CONFLICT` | `labels:"human"` 的資料集配不出合法的 class 真相表（🔒 `class_table.schema.json` 是照「KMeans + LLM 命名」凍的：`nc∈[3,6]`、`names` 只能是那六個固定詞、`cluster_stats` 必填 —— WM-811K v3 的 `nc:1 names:['Donut']` 三條全踩）。**刻意不硬塞**：補到 3 類 = 發明類別，讓 KMeans 當類別體系而框留人工 = 框的 `cls` 靜默錯位。真實資料要跑完整條線就用 `labels:"auto"` |
| `409` | `CEILING_UNAVAILABLE` | 要 `GET /eval/ceiling` 但那個 ds 沒有人工 GT（或 s03 還沒跑完） |
| `501` | `NOT_IMPLEMENTED` | §9 的佔位端點；或傳了 `mode:"manual"/"autonomous"`、`source:"local"`（`source:"roboflow"` 已實作，2026-09-12） |

`500` 不在契約裡：未預期例外一律記一筆 `stage.failed` 事件再往上丟，讓前端把進度條轉紅，而不是靜靜卡住。

---

## 11. M1 離線資料契約 🔒（產生器與 selfcheck 的交界）

`scripts/gen_demo_wafers.py`（dataset-truth 擁有）與 `scripts/selfcheck.py`（console-owner 擁有）是兩個人寫的，所以交界寫在這裡。

**檔案佈局**（`PROJECT_ROOT` = `agent_group_projects/cv-self-evolving-console/`，一律 `pathlib`，不准硬編絕對路徑）：
```
01-raw-data/demo/wafer_0001.png … wafer_0120.png
01-raw-data/demo/gt.json
runs/runs.jsonl                  ← run registry（單 writer）
runs/<run_id>/events.jsonl       ← 事件總線（append-only）
runs/<run_id>/state.json         ← pid / 狀態（M3 起才有內容）
```
`runs/` 刻意不在 `--reload-dir src` 的監看範圍內。

**影像**：PNG、**256×256**、RGB、真點陣（不是 SVG data-URI —— SVG 沒有像素，連通分量跑不出東西）。4 種缺陷型態沿用既有 demo（`scratch` / `particle` / `ring` / `edge-loss`），seed 固定 42。

**id / 檔名規則** 🔒：`image_id = f"demo{i:04d}"`（i 從 1 起）、`name = f"wafer_{i:04d}.png"`。`GET /api/v1/images/demo0007` 解析到 `01-raw-data/demo/wafer_0007.png`。

**免費精確 GT**（`01-raw-data/demo/gt.json`）：
```json
{
  "version": 1,
  "seed": 42,
  "image_size": [256, 256],
  "images": [
    {"id":"demo0001","name":"wafer_0001.png",
     "shape":"scratch",
     "boxes":[{"cx":0.412,"cy":0.508,"w":0.233,"h":0.061}]}
  ]
}
```
- `shape` 是**合成時用的型態標籤，不是類別真相**：只准 `selfcheck.py` 與 M2 的命名事後驗證讀，**不准進任何 API 回應、不准餵進 auto-label**（否則就是偷讀答案）。
- GT box 同樣是正規化 `cx,cy,w,h`。

**M1 驗收的三條 assert**（`uv run python scripts/selfcheck.py`，全程 < 90 秒、零 GPU / 零 Roboflow / 零 LLM / 零網路）：
1. `events.jsonl` 完整讀 == 斷線後 `?since=` 續讀，**逐筆相同**（含 seq 與欄位）。
2. Python 分層抽樣 == 前端 `stratifiedSplit`，同 seed **逐筆相同**。測資是固定 fixture（含 4 個分層 + 一組無類別 `__none__`），**不依賴 API 回應** —— M1 所有圖都還是 `unclassified`，用 API 資料只會測到單一分層那條退化路徑。演算法三處必須照抄前端：主類別取 `cls[0]`（無類別歸 `__none__`）、分層鍵**字串排序**後才處理、每層 Fisher–Yates 洗牌用 `mulberry32(seed >>> 0)`、最大餘數法補足（餘數大的先拿，順序 `train,valid,test`）。
3. auto-bbox 對合成 GT 的 **IoU 中位數 ≥ 0.6**。

**兩把不同的尺，不要混** 🔒：
- **合成 GT**（M1）：門檻 **0.6**（程式生成的框，鬆了就是演算法有問題）。事件 `label.anchor_iou.source = "synthetic"`。
- **Roboflow 人工 anchor**（M3 起）：門檻 **0.4**，低於就停在 s03 宣告「問題在標註不在模型」（`label_ceiling`）。事件 `source = "roboflow_anchor"`。
`source` 欄位是 M0 對 DESIGN 的**加欄**（見附錄裁決 C）。

---

## 12. 變更流程 🔒

1. **這份契約由一個人擁有**：`console-owner`（team lead）。其他四位**不得直接編輯本檔**，一行都不行。
2. 要改任何 🔒 條目：提一則變更請求，四段講完 —— (a) 要改哪一條（引本檔的節號）、(b) 為什麼現有形狀做不到（附實際會壞掉的呼叫或錯誤訊息）、(c) 改完的完整 JSON 範例、(d) 誰要跟著改（哪些檔案、哪個里程碑）。console-owner 批准才動。
3. **加東西 vs 改東西**：
   - 在 `data` 物件裡**新增選填欄位**、在 `rule` / `code` 這類 enum 裡**新增值** → 向後相容，走「通知 + 本檔補一列」即可，仍由 console-owner 落筆。
   - 改欄名、改型別、改語意、刪欄位、改 HTTP 狀態碼 → **破壞式**，必須同一個 PR 一起改前端與所有呼叫方，並把 `v` 從 1 進到 2。
4. **M0 閘門未過不准 spawn 任何人**：本檔 + `_Context/team-roles.md` + `_Context/class_table.schema.json` + 空的 FastAPI 骨架（五個 router 掛進同一個 app）三件到位，才准開始平行。
5. 契約與實作不一致時，**契約是對的**，實作要改。反過來的那一天（「程式已經這樣寫了所以契約改一下」）就是整合當天爆掉的起點。
6. 每次變更在本檔底部加一行紀錄：日期 / 節號 / 改了什麼 / 批准人。

### 變更紀錄
- 2026-09-11 · 全檔 · M0 初次凍結（v=1）· console-owner
- 2026-09-11 · §4 · 前綴表補 `recipe.*` `augment.*` 兩個前綴給 `training-engineer`（§4 的 type 清單本來就有 `recipe.proposed` / `recipe.frozen` / `augment.decision`，但前綴表漏列，實作的越權守門已經放行 —— 比照 `arbiter.*` 那條的處理，加列而非改名，向後相容）· console-owner
- 2026-09-11 · §10 · 新增 `409 RUN_NOT_LIVE`（`POST /datasets/ingest` 與 `POST /label/auto` 這兩條平行入口在已收工的 run 上加事件，會讓事件流與快照對不上）· console-owner
- 2026-09-11 · §1 · Host 白名單明寫含 `::1` / `[::1]`（本來就是本機），並記下 `0.0.0.0` 與 `testserver` 已從實作移除 · console-owner
- 2026-09-12 · §8.1 · `POST /runs` body 加選填欄位 `stages`（教學用，只跑前幾段；預設整條龍 s01→s08）＋
  明寫 `oneshot` 會一路驅動到 s08 · 向後相容的加欄 · console-owner
- 2026-09-12 · §8.4 · cancel 改成「真的 SIGTERM 訓練子行程並確認收乾才回」，回應加選填欄位
  `trains_killed`（原本回 200 但子行程還活著，是假成功）· console-owner
- 2026-09-12 · §8.10 · 新增 `GET /api/v1/stages`（stage 表 + driver 執行順序；前端 rail 的文案比對靠它）·
  純新增端點，無破壞式改動 · console-owner
- 2026-09-12 · §4 · 新增 type `stage.note`（`console-owner`）：長工作（LLM 命名 ~150 秒、
  noise floor ~70 秒）**開始之前**先發一行旁白。原本兩段都是「做完才發一筆」，整條龍 322 秒裡
  有 218 秒（68%）畫面完全不動 · 純新增 type，無破壞式改動 · console-owner
- 2026-09-12 · §4 · `class.table.frozen` 的 `data` 加兩個選填欄位 `naming`（`"llm"|"fallback"`）
  與 `naming_error`：LLM 命名降級時 s02 的燈仍然是綠色「完成」、預算條 0 calls，前端沒有任何
  訊號可以把降級畫出來（線索只在 text 尾巴，而 `#log` 在 1440×900 是看不到的）· 向後相容加欄 · console-owner
- 2026-09-12 · §5.4 · 心跳從 `: ping` 註解行改成 `data:` 資料框 `{"v":1,"type":"heartbeat"}`：
  註解行瀏覽器不交給 JS，前端 45 秒看門狗（§5.5）看不到它，每個長 stage 都會固定跳一次
  假斷線重連 · 破壞式但只影響前端 reducer（已同 PR 加 case 擋掉）· console-owner
- 2026-09-12 · §5.10 · 新增「run 收工後事件一律拒收」：`to_thread` 裡的孤兒 thread 會在 run
  標成 cancelled 之後繼續追加事件（實測晚 24 秒、seq 比 stop 大）· console-owner
- 2026-09-12 · §8.1/§10 · `409 RUN_IN_FLIGHT` 的 body 加選填 `run_id`（前端要能接回還在跑的
  run；沒有它就是「叫你去 cancel，同時把 cancel 鈕拿走」）· 向後相容加欄 · console-owner
- 2026-09-12 · §8.4 · cancel 回應加選填 `children_killed`，並明寫「連按兩次第二次一定 409」 ·
  向後相容加欄 · console-owner
- 2026-09-12 · §8.5/§8.6/§10 · 新增 `409 STAGE_ALREADY_RAN`（同一個 run 的同一段重打）——
  §6 本來就寫死「重試 ingest 不可重發 120 筆 ds.image」，但沒有錯誤碼可回 · console-owner
- 2026-09-12 · §8.9 · `ETA×2` 硬 timeout 補一條 10 分鐘下限（實測 ETA 10 秒 / 實跑 17.8 秒，
  純 ETA×2 會砍死健康的 run）· console-owner
- 2026-09-12 · §0/§8.1/§8.5/§10 · `source:"roboflow"` 從 501 變成已實作（WM-811K 409 張＋人工
  bbox）：`POST /runs` 加四個選填欄位 `labels`/`workspace`/`project`/`version`（逐欄與 §8.5 相同，
  **沒有 api_key**，key 只在 server 讀 `.env`），§10 加 5 個 `ROBOFLOW_*` 錯誤碼，`NOT_IMPLEMENTED`
  那一列只剩 `local` · 向後相容的加欄 + 放寬 · console-owner
- 2026-09-12 · §4 · `stop` 的 `data` 加選填 `detail`，並記兩個已實作的 `reason`：`user_cancel`（§8.4）
  與 **`label_ceiling`**（s03 之後 driver 主動停）· 向後相容加欄 · console-owner
- 2026-09-12 · §8.7 · 明列 `split` 的值域（`train`/`valid`/`anchor`/`sealed`/`null`）——
  後端本來就只回這五個，但契約沒寫，前端白名單漏了 `anchor` 與 `sealed`，把「還有 N 張沒指派」
  那個謊從側欄搬到卡片層 · 純澄清，行為不變 · console-owner
- 2026-09-12 · §8.3 · 快照加兩個選填欄位 `split` 與 `recipe`：側欄的 split 統計與訓練設定本來是
  前端自算的，而前端手上沒有真相（`ds.image.split` 永遠 null、配方在 recipe.json）——
  實測一條龍訓練完、verdict TRUSTED，右欄照樣寫「train 0 / valid 0 / test 0・還有 120 張沒指派」，
  訓練設定寫著沒人用過的 `yolov8n / 50 / 640` · 向後相容加欄 · console-owner
- 2026-09-12 · §10 · 加 `409 CLASS_TABLE_SCHEMA_CONFLICT`（人工標註的資料集配不出合法的 class
  真相表 —— 🔒 schema 是照 KMeans + LLM 命名凍的）與 `409 CEILING_UNAVAILABLE` · console-owner
- 2026-09-12 · §4/§9 · `charts.series` 的 `data` 加選填 `ceiling`（合成 GT 的 IoU 1.0 是**退化值**，
  與真人工框的 0.6381 必須在畫面上分得出來）；§9 補一張「已實作」小表記 `GET /eval/ceiling` ·
  向後相容加欄 + 純新增端點 · console-owner
- 2026-09-11 · §8.1/§8.5/§8.6 · `preset` 在 M1 的行為定義：`teaching` 逐張停 0.12 秒（縮圖牆要看得見一張一張長出來、Stop 鈕要按得到），`real` 不停頓（selfcheck 與一鍵腳本走這條）。兩者是同一條 driver，只差 sleep · console-owner

---

## 附錄：與 DESIGN.md 的三處裁決（衝突紀錄）

| # | DESIGN 的兩句話 | 裁決 | 理由 |
|---|---|---|---|
| A | 專家 API 表把 `POST /datasets/ingest`、`/label/auto`、`/train` 等標成 `Stream: SSE`；傳輸層那節寫「一條流不是六條，五位專家不各開 SSE」 | **傳輸層勝**。所有會產生事件的 POST 回 `202` + JSON，事件走唯一出口 `GET /runs/{run_id}/events`；API 表的 `Stream: SSE` 讀作「此端點的產出以事件形式流出」 | `EventSource` 只能 GET，POST 端點在瀏覽器裡物理上訂閱不了；而且訓練是子行程，跨 process 只有 events.jsonl 這條路 |
| B | 線上框範例帶 `event: train.epoch`；前端規則寫「統一 `es.onmessage` → `switch(ev.type)`，不要 `addEventListener(type)`」 | **前端規則勝**，線上框**不送 `event:`** | 帶 `event:` 名稱的框不會觸發 `onmessage`；留著它就等於強迫前端綁 20 種 type，新增 type 必漏接 |
| C | s03 的 `label.anchor_iou {iou_median,iou_hist,verdict}` 只有一種來源；但 M1 量的是合成 GT（門檻 0.6）、M3 起量的是 Roboflow 人工 anchor（門檻 0.4） | `data` **加兩個欄位** `source` 與 `threshold`（向後相容的加欄） | 兩把尺混在同一個 type 裡，前端與 `label_ceiling` 判定分不出「0.55 是過還是不過」 |

> 另記一條分工調整：DESIGN 的開工順序把 `class_table.json` 的 schema 凍結掛在 `dataset-truth`，但同一條也寫「M0 閘門不過不准 spawn 任何人」。M0 現場只有 console-owner，因此 `_Context/class_table.schema.json` 由 console-owner 凍結欄位，`dataset-truth` 在 M2 **只填值不改欄位**；要改欄位走 §12。
