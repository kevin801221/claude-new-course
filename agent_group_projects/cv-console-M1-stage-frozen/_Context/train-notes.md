# training-engineer 決策紀錄（M3）

> 擁有者：`training-engineer`。唯一設計真相是 `_Context/DESIGN.md`，欄位形狀以 `_Context/api-contract.md` 🔒 為準。
> 這份記的是**做了什麼決定、為什麼、實測數字是多少** —— 不是教學文件，是之後有人問
> 「為什麼 scale_factor 是 0.1」「為什麼 mosaic 關掉」時的證據。
> 最後更新：2026-09-11（M3 完成：s05 選型 + s06 配方凍結 + s07 不阻塞訓練）
> 機器：Apple M5 / 24 GB · torch 2.14.0 · ultralytics 8.4.147 · device `mps`

---

## 1. 候選池：只有兩列，而且第三列的缺席要印在畫面上

| 候選 | params | 進池？ | 理由 |
|---|---|---|---|
| `yolov8n` | 3.2 M | ✅ | 教學 baseline 的起點；權重 6.5 MB，sha256 `f59b3d833e2f…` |
| `yolov8s` | 11.2 M | ✅ | 對照組；權重 22.6 MB，sha256 `1f47a78bf100…` |
| `rtdetr-l` | 32 M | ⛔ | **MPS 上 deformable attention 會靜默走 `PYTORCH_ENABLE_MPS_FALLBACK` 掉回 CPU**，慢 10–50 倍而且不報錯 —— 它不會失敗，它會讓 budget 帳算爛 |

`GET /api/v1/models/candidates` 回應裡 `excluded` 是**第一等公民**，不是註解：
學生要看到「有人試過、而且知道為什麼不用」，不是看到一個少了一列的表。

`weights_cached` / `sha256` / `mps_ok` 三欄的真相在 `00-weights/<name>.pt.json`，
由 `scripts/fetch_weights.py`（M2 就寫好的）或探針第一次跑到那個候選時落檔。
**`mps_ok` 不是查 `torch.backends.mps.is_available()`** —— 那只是宣稱支援；
它是「真的把權重搬到 mps、forward 一張 640×640、再 assert 參數確實在 mps 上」的結果。
沒驗過就是 `null`，前端不准把 `null` 當 `true`。

---

## 2. 探針規則（s05）

DESIGN 寫死的是：**同一組 seed、同一份 dataset、同一組 recipe、跑到 mAP50-95 曲線第一次持平**
（不是固定 3 epochs）。實作：

- `PROBE_MAX_EPOCHS = 4`（上限，不是目標）、`PROBE_PLATEAU_EPS = 0.005`、`PROBE_MIN_EPOCHS = 2`。
- 持平判定在 `src/train/callbacks.py`：`map5095 − 歷史最佳 <= eps` 就 `trainer.stop = True`
  （ultralytics `_do_train` 在 `on_fit_epoch_end` 之後就 `if self.stop: break`，所以這一行真的會停）。
- `min_epochs=2` 是硬下限：ETA 校準要兩個 epoch 的實際秒數。

**實測（r29-t1，120 張 → train 48 / valid 12，imgsz 320，batch 8，seed 42）**

| 候選 | 實跑 epochs | mAP50-95 逐 epoch | 停的理由 | ms/img | 秒/epoch（穩定態） |
|---|---|---|---|---|---|
| yolov8n | 3 / 4 | 0.0071 → 0.0592 → 0.0592 | 第 3 個 epoch 持平（Δ≈0） | 4.256 | 1.138 |
| yolov8s | 2 / 4 | 0.1815 → 0.1406 | 第 2 個 epoch 退步（Δ<0 也算持平） | 4.778 | 1.590 |

整段探針 **17.2 秒**（含兩次權重載入與 MPS 暖機）。

---

## 3. 評分函式與 CI（這一節有一句誠實話，不要拿掉）

```
score = mAP50-95@probe − 0.10 · (est_min / max est_min) − 0.05 · (ms_img / max ms_img)
差距落在 CI 內 → 選小的
```

實測 r29-t1：

| 候選 | mAP50-95 | CI 半寬 | est_min | ms/img | score |
|---|---|---|---|---|---|
| yolov8n | 0.0592 | ±0.0261 | 0.19 | 4.256 | **−0.0557** |
| yolov8s | 0.1815 | ±0.0204 | 0.27 | 4.778 | **+0.0315** |

裁決：`yolov8s`，理由字串直接寫進事件 ——
「mAP50-95 差距 0.1223 > CI 半寬 0.0204，差異可讀」。

> ⚠️ **CI 目前是假的，而且刻意標明是假的。** DESIGN 要的是 metric-auditor 的 bootstrap CI（M4 才有）。
> 在那之前用「同一次探針裡 mAP50-95 的全距 ÷ 2」，`ci_source` 欄位標成 `probe_spread`
> （只有一個 epoch 時退回先驗 `PRIOR_CI = 0.15`，標 `prior`）。
> 前端不准把它畫成統計上的信賴區間。M4 上線後 `select.probe_ci()` 整段換掉，
> 呼叫端不用改（`rank()` 只吃 `ci` 與 `ci_source` 兩欄）。

`valid` 只有 12 張 / 15 個框，mAP 的取值本來就是粗粒度的：
實測 10 epochs 裡出現過兩次「連續兩個 epoch 的 mAP50-95 完全一樣」（0.1901、0.3051），
而同期 `box_loss` 每個 epoch 都在動。**這正是 noise floor 存在的理由**，也是 M4 的工作。

---

## 4. ETA 本機校準（DESIGN 護欄 5，不可省）

`scale_factor = 本機穩定態秒/epoch ÷ 講師錨點 10.67 秒/epoch`（= 8.89 分鐘 / 50 epochs，M4 Max）。

| 機器 | dataset | imgsz | 秒/epoch | `scale_factor` | 量測方式 |
|---|---|---|---|---|---|
| M4 Max（講師錨點，DESIGN 引用） | Roboflow 409 張 | 640 | 10.67 | 1.0（定義） | — |
| **Apple M5 / 24 GB（本機實測）** | 合成 120 張 → train 48 | 320 | **1.138**（yolov8n）／1.590（yolov8s） | **0.1067** | `04-experiments/r29-t1/probe.json` |

> ⚠️ **`scale_factor` 不是純硬體係數。** 錨點綁的是「那台機器 + 那份 dataset + 那組 recipe」，
> 本機這 0.1067 裡面，dataset 從 409 張變 48 張、imgsz 從 640 變 320 的貢獻遠大於 CPU/GPU 差異。
> 它唯一的用途就是 DESIGN 要的那件事：**讓學生機器的 ETA 不要抄講師的分鐘數**。
> 要拿它比較硬體，必須跑同一份 dataset 與同一組 recipe。

**warmup 陷阱（實測）**：第 1 個 epoch 要編 Metal shader，**18.8 秒**；第 2 個之後 **1.2 秒**，差 15 倍。
把第一個 epoch 算進平均，10 epochs 的 ETA 會高估一個數量級。
所以 `callbacks.steady_sec()`：兩筆以上就丟掉第一筆再取中位數。
`train.calibrated` 事件的兩個欄位因此意思不同，都要照字面讀：
- `probe_2ep_sec = 10.35` —— 真的跑前兩個 epoch 花的牆鐘秒數（**含 warmup**）。
- `scale_factor = 0.1067` —— 穩定態的比值（**不含 warmup**），所有 ETA 乘它。

`00-weights/calibration.json` 是這台機器的校準檔（探針每跑一次就覆寫）。
沒有它時 `GET /models/candidates` 回 `calibrated: false` + `scale_factor: 1.0`，
前端要顯示成「未校準」而不是假裝那是量出來的。

---

## 5. augment 決策（DESIGN (2)：真正有對錯、會被挑戰的一條）

每一條都會發一筆 `augment.decision {name, on, why}`，`why` 直接印在前端。

| 旋鈕 | 值 | 開/關 | 理由 |
|---|---|---|---|
| `degrees` | 180 | ✅ 開 | rotate — 晶圓無固定方向，任意角度都是合法的同一片 |
| `fliplr` | 0.5 | ✅ 開 | 水平翻轉 — 同上，鏡像後仍是合法晶圓 |
| `flipud` | 0.5 | ✅ 開 | 垂直翻轉 — 同上（自然影像不敢開，晶圓可以） |
| `mosaic` | 0.0 | ⛔ 關 | 四張拼一張會讓 edge-ring / edge-loc 的缺陷出現在畫面正中間，**破壞「靠邊」語意** |
| `translate` | 0.0 | ⛔ 關 | 平移會把靠邊缺陷推到中央，等於教模型錯的類別 |
| `scale` | 0.0 | ⛔ 關 | 缺陷相對晶圓半徑的比例本身就是類別語意（center vs edge-ring） |
| `erasing` | 0.0 | ⛔ 關 | 缺陷本來就稀疏，抹掉就變成錯標 |

後四條是同一個論證的四個面向：**這個資料集的類別定義是「缺陷長在哪」**，
任何會搬動缺陷相對位置或相對尺度的 augment 都是在製造錯標，不是在增加多樣性。

---

## 6. 教學 baseline 刻意調弱 —— 以及它跟探針的衝突怎麼裁

`BASELINE = yolov8n / 10 epochs / imgsz 320 / batch 8 / lr0 0.01 / optimizer auto`。
DESIGN 說得很白：從強 baseline 起跑（mAP50 0.977）的話每一輪都會判 no_gain，
台上看到的「自我進化」會是三輪「沒有進步」。

**衝突**：baseline 寫的是 `yolov8n`，但探針實測選了 `yolov8s`（0.1815 vs 0.0592，差距遠大於 CI）。
**裁決：探針勝。** 理由是 DESIGN 護欄 (1)「選型實證而非查表」—— 如果選型結果可以被一行常數推翻，
整個 s05 就是裝飾。baseline 的「弱」由 `epochs=10` 與 `imgsz=320` 維持，
實測 10 epochs 收在 mAP50 **0.677** / mAP50-95 **0.3012**，離天花板很遠，M6 有的是進步空間。
要強制用 baseline 的模型：`POST /train {"model": "yolov8n"}`（人工覆寫會留事件痕）。

另外兩個被寫進 baseline 的非預設值：
- `patience = 100`（等同關掉 ultralytics 自己的早停）。開著的話「只改一個變因」的兩次訓練
  會在不同 epoch 停下，**比較基準就不一致了**，OFAT 的歸因當場失效。
- `amp = False`。MPS 上 `check_amp()` 會**另外下載 `yolo26n.pt`** 做一致性檢查
  （多一條網路依賴、多 30 秒），而這個尺寸的模型在 MPS 上吃不到 fp16 的收益。
- `workers = 0`。macOS + fork 的 DataLoader worker 是子行程裡最常見的卡死來源，
  48 張圖也不需要多進程餵資料。

---

## 7. process 邊界：為什麼是 `subprocess.Popen`，以及四個真的會咬人的細節

`model.train()` 是純阻塞碼。塞進 `BackgroundTasks` 會佔住 ASGI 的 event loop ——
心跳停、SSE 卡死、cancel 收不到。定案（DESIGN (4)）：獨立 process + 檔案狀態。

**(1) zombie 陷阱（自檢抓到的）**：子行程結束後若沒人 `wait()`，它會變成 zombie，
而 **zombie 的 `os.kill(pid, 0)` 依然成功**。只看 pid 的話，訓練跑完了 `alive` 會永遠是 `True`，
cancel 的「等它收乾」會變成無窮迴圈。所以 `runner._PROCS` 留著自己起的 `Popen` handle，
`alive()` 先看 `poll()`（順手收屍），沒有 handle（API 重啟後）才退回純 pid 判斷 ——
那時子行程已經被 init 收養並收屍，pid 判斷才是對的。

**(2) 事件沒有 `train_id` 欄位**（契約 §4 凍死了 `train.epoch` / `train.done` / `train.cancelled` 的 data）。
所以「哪些事件屬於這一次訓練」用起跑時的 `bus.last_seq()` 當下界（`state.json` 的 `start_seq`）。
下界擋得住前一次訓練，**擋不住後一次** —— 同一個 run 跑第二次之後，
t2 的快照會顯示 t3 的 epoch（實測踩過）。修法：收工的那一刻把 `epoch` / `total` / `elapsed_s`
一起凍進紀錄，終局狀態不再回去掃事件。

**(3) cancel 是整個 process group**：`os.killpg(os.getpgid(pid), SIGTERM)`，
`start_new_session=True` 讓子行程自成一組。8 秒沒收乾就升級 `SIGKILL`，
**確認 pid 真的不在了才推 `train.cancelled` 並回應** ——
「按了 Stop 但它還在燒 GPU」是最糟的一種假成功。

**(4) 狀態在讀的時候才判定**：沒有人 `wait()` 子行程，所以「它做完了沒」的真相在事件裡。
`GET /train/{id}/state` 看到「pid 不在了但狀態還寫著 running」就去事件流找結局
（`train.done` → done、`train.cancelled` → cancelled、都沒有 → **crashed** 並補一筆 `train.warn`）。
這就是 DESIGN 護欄 6 要的那個捕手：子行程被 OOM kill，前端要看到紅色而不是卡住的進度條。
`POST /models/probe` 與 `POST /train` 進來時也會順手結清別的訓練（`_busy_train()`），
否則探針跑完之後 s05 會一直卡在 running，除非剛好有人去讀它的狀態。

**OOM 退避**：`_fit()` 抓到 `out of memory` 就 `batch //= 2` 重試一次並推 `train.warn`。
本機 24 GB + batch 8 + imgsz 320 峰值只有 **1.24–1.30 GB**，這條路徑這輪沒被踩到（未實測）。

---

## 8. ultralytics 8.4 的三個坑（都會靜默出錯）

**(1) `final_eval()` 會多觸發一次 `on_fit_epoch_end`。**
原始碼是 `self.epoch += 1` → `run_callbacks("on_fit_epoch_end")` → `self.epoch -= 1`
（`engine/trainer.py::final_eval`），所以它永遠冒充「最後一個 epoch + 1」。
只擋 `epoch > total` 擋不掉**提早停止**的情形：實測探針在第 2 個 epoch 持平停下，
final_eval 就送出一筆假的「epoch 3/4」，而且那個 mAP 是 best.pt 的、跟訓練曲線不同源。
真正分得出來的是 validator：迴圈內是 `self.validator(self)`（trainer 有值 → `training=True`），
final_eval 是 `self.validator(model=model)`（trainer=None → `training=False`）。
→ `callbacks.py` 用 `trainer.validator.training` 當主要閘門，epoch 編號當備援。

**(2) `metrics/mAP50` 是 `metrics/mAP50-95(B)` 的前綴。**
用 `startswith` / `in` 撈指標，會把 mAP50-95 的數字填進 mAP50 欄位，**而且圖表看起來完全正常**。
→ `_pick()` 先整鍵命中，再用「最後一段去掉 `(B)`」做**全等**比對。自檢第 1 條就是守這件事。

**(3) log 帶 ANSI 與 `\r`。** 這是 DESIGN 不准 parse stdout 的實證：
實測一行 epoch 進度會被 `\r` 覆寫五次、帶 `[K` 與顏色碼。
`04-experiments/<train_id>/train.log` 只留給人看，**指標一律走事件**。

實測到的 trainer 指標鍵名（`result.json` 的 `keys_seen`，M4 接手時直接用）：
`metrics/mAP50(B)`、`metrics/mAP50-95(B)`、`metrics/precision(B)`、`metrics/recall(B)`、
`train/box_loss`、`train/cls_loss`、`train/dfl_loss`、`val/box_loss`、`val/cls_loss`、`val/dfl_loss`。

---

## 9. 實測：真的訓練 10 epochs（r29-t2）

`POST /api/v1/train {"run_id":"r29"}` → `202 {train_id:"r29-t2", pid:16482, eta_min:0.27}`
模型 `yolov8s`（探針選的）、10 epochs、imgsz 320、batch 8、device `mps`、seed 42。

| epoch | box_loss | cls_loss | dfl_loss | mAP50 | mAP50-95 | mem_mb | eta_s |
|---:|---|---|---|---|---|---|---|
| 1 | 1.919 | — | — | 0.214 | 0.1602 | 1295.7 | 86 |
| 2 | 1.585 | — | — | 0.503 | 0.1901 | 1247.7 | 13 |
| 3 | 1.778 | — | — | 0.503 | 0.1901 | 1263.7 | 11 |
| 4 | 1.615 | — | — | 0.619 | 0.1826 | 1263.7 | 9 |
| 5 | 1.342 | — | — | 0.769 | 0.2903 | 1263.7 | 8 |
| 6 | 1.270 | — | — | 0.610 | 0.3051 | 1247.7 | 6 |
| 7 | 1.417 | — | — | 0.610 | 0.3051 | 1259.7 | 5 |
| 8 | 1.396 | — | — | 0.780 | **0.3702** | 1259.7 | 3 |
| 9 | 1.258 | — | — | 0.663 | 0.2982 | 1259.7 | 2 |
| 10 | 1.306 | — | — | 0.677 | 0.3012 | 1243.7 | 0 |

（`cls_loss` / `dfl_loss` 每個 epoch 都有值，在 `04-experiments/r29-t2/result.json` 裡；
表格只列 DESIGN 的 log 四行格式會用到的欄位。）

- **總耗時 24.6 秒**（0.41 分鐘）。第 1 個 epoch 18.8 秒是 Metal shader 首編，之後 1.2 秒。
- `best.pt` = `04-experiments/r29-t2/yolo/weights/best.pt`（`last.pt` 同目錄，cancel 後要 resume 靠它）。
- **訓練期間 `curl /healthz` 61 次：中位數 0.9 ms、最大 2.0 ms。** 阻塞的話這裡會是幾十秒或逾時。
- 訓練中 `GET /train/r29-t2/state` → `{"status":"running","epoch":3,"alive":true,"elapsed_s":13.4,…}`。
- 收工後同一支 → `{"status":"done","epoch":10,"best_pt":"…/best.pt","elapsed_s":24.6}`，
  run 快照的 `stages.s07` 變 `done`、`budget.gpu_min_used` 累加到 **0.697** 分鐘（探針 + 訓練）。

**cancel（r29-t3）**：跑到第 2 個 epoch 按 `POST /train/r29-t3/cancel`
→ **106 ms** 回應、`ps -p <pid>` 已不存在、`train.cancelled` 事件 1 筆、state 變 `cancelled`。
再按一次 → `409 RUN_NOT_CANCELLABLE`（不是 200，前端的 Stop 鈕不准假裝成功）。

**API 重啟（DESIGN M3 驗收，r30-t1）**：訓練到第 2 個 epoch 時 `SIGKILL` uvicorn。
- API 死掉的 2 秒內，子行程照樣寫事件（`last_seq` 517 → 518）。
- 重開之後 `GET /runs/r30` → `status: running`（**不是 crashed**，`claim_or_crash()` 用 `os.kill(pid,0)` 認領到活的子行程）。
- 一路跑到 `train.done`：`seq` 連續無洞、`train.epoch` 完整 1..10、`stages.s07` 收成 `done`。

**錯誤路徑**（實打）：

| 請求 | 回應 |
|---|---|
| `POST /train {"model":"rtdetr-l"}` | `400 BAD_ENUM`（候選只有 yolov8n / yolov8s） |
| `GET /train/r999-t1/state` | `404 TRAIN_NOT_FOUND` |
| `POST /models/probe {"run_id":"r999"}` | `404 RUN_NOT_FOUND` |
| 對 `status=done` 的 run 打 `POST /train` | `409 RUN_NOT_LIVE` |
| 還沒 freeze 就打 `POST /train` | `409 SELFCHECK_FAILED`（見下面的 blocker） |

---

## 10. 落檔佈局（M4 接手前先知道）

```
00-weights/<name>.pt            ← 權重（gitignored）
00-weights/<name>.pt.json       ← {name, bytes, sha256, mps_ok} = model.weights.fetched 的 data
00-weights/calibration.json     ← 本機 ETA 校準（scale_factor / probe_2ep_sec / sec_per_epoch）
04-experiments/<train_id>/recipe.json   ← 子行程唯一的輸入；provenance 比對讀它
04-experiments/<train_id>/train.log     ← 給人看的 stdout（**不是**指標來源）
04-experiments/<train_id>/result.json   ← 正式訓練：逐 epoch 指標 + keys_seen + best_pt
04-experiments/<train_id>/probe.json    ← 探針：rows / selected / calibration / results
04-experiments/<train_id>/yolo/weights/{best,last}.pt
runs/<run_id>/state.json 的 "train" 分區 ← {train_id: {status, pid, start_seq, epoch, elapsed_s, …}}
```

`train_id = f"{run_id}-t{n}"`（`r29-t2`）。內嵌 run_id，所以「由 train_id 找 run」不需要第二份索引檔。

---

## 11. 怎麼重跑這裡的每一個數字

```bash
uv sync --extra train                      # 這一組不在 base 依賴裡（.venv 159 MB → 1.1 GB）
uv run python -m src.train.callbacks       # 指標鍵名 / text 格式 / plateau / final_eval 去重（不需要 GPU）
uv run python -m src.train.select          # ETA 校準 / 評分函式 / CI 內選小的 / augment / recipe diff
uv run python -m src.train.runner          # Popen 不阻塞 / SIGTERM 收乾 / 子行程消失判 crashed
uv run python scripts/fetch_weights.py     # 權重 + sha256 + 真的 forward 驗 mps
# 端到端（要先有 freeze 過的 02-dataset/data.yaml）：
uv run uvicorn src.app.main:app --reload --reload-dir src
curl -s localhost:8000/api/v1/models/candidates | jq
curl -s -XPOST localhost:8000/api/v1/models/probe -H 'content-type: application/json' -d '{"run_id":"rN"}'
curl -s -XPOST localhost:8000/api/v1/train -H 'content-type: application/json' -d '{"run_id":"rN"}'
curl -s localhost:8000/api/v1/train/rN-t2/state | jq
curl -s -XPOST localhost:8000/api/v1/train/rN-t2/cancel
```

三支 module 自檢都是**會失敗的**檢查，不是裝飾。想確認的話把
`callbacks._pick()` 的全等比對換回 `startswith`（第 1 條紅：mAP50 會變成 mAP50-95 的值）、
把 `select.choose()` 的 CI 判斷拿掉（第 3 條紅：明明沒顯著差異卻選了大的）、
或把 `runner.cancel()` 的 `SIGKILL` 升級拿掉再把 grace 設成 0（第 3 條紅：回來了但 process 還活著）。

---

## 12. 留給後面的人 / 待決事項

**給 metric-auditor（M4）**
- `select.probe_ci()` 是暫代品，`ci_source: "probe_spread"`。真的 bootstrap CI 上線後換掉這個函式即可，
  `rank()` / `choose()` 不用改。
- 逐 epoch 指標在 `04-experiments/<train_id>/result.json`，鍵名見 §8。
- `best_pt` 在 `train.done` 事件與 `GET /train/{id}/state` 兩處都有。

**給 experiment-arbiter（M6）**
- `recipe.frozen.diff_vs_last` 已經是**鍵名級**的 diff（`{"imgsz": [320, 640]}`），
  不是自然語言描述 —— OFAT 去重直接比它。
- `POST /train` 的 `recipe` 欄位就是 patch 的入口（只接受 `select.BASELINE` 有的鍵，其餘忽略）。
- `04-experiments/<train_id>/recipe.json` 是 provenance 比對的 plan 那一側。

**blockers / 要 console-owner 走 §12 裁決的三件**
1. **缺一個錯誤碼 `409 DS_NOT_READY`**。「還沒 freeze 就打 `/train`」目前借用 `409 SELFCHECK_FAILED`，
   語意是歪的（selfcheck 沒 FAIL，是根本還沒跑）。dataset-truth 在 M2 已提過同一件事。
2. **`runs.jsonl` 沒有 `scale_factor` 欄位**。DESIGN 護欄 5 說 scale_factor 要寫進 runs.jsonl，
   但 `registry._sync_ledger()`（console-owner 的檔）的欄位表裡沒有它。
   現在落在 `00-weights/calibration.json` 與 `train.calibrated` 事件，帳本查不到。
   同理 `cost_min`（GPU 分鐘）有欄位但沒人寫，M3 把它累加在 `state.json` 的 `budget.gpu_min_used`。
3. **`drive_run()` 還沒串 s05–s07**。那顆按鈕跑完 s01→s03 就把 run 標 done，
   done 的 run 再打 `/train` 會 `409 RUN_NOT_LIVE`（M2 也卡在同一條）。
   串法：`drive_run()` 在 s04 之後依序 `POST /models/probe` → 等 `model.selected` → `POST /train`
   → 等 `train.done`，或直接呼叫 `routers/train.py` 的同名函式。

**這輪沒做的（刻意）**
- `POST /api/v1/sweep` 仍是 `501`（M6）。
- 預算**只記帳不擋人**：`budget.gpu_min_used` 會累加，但超支不會拒絕起跑 ——
  §10 沒有「超支」的錯誤碼，而且三軸預算的執行是 M7 autonomy 的事。
- 非 best 權重的清理沒做：ultralytics 預設只留 `best.pt` + `last.pt`（`save_period=-1`），
  一次訓練 12 MB，沒有 DESIGN (5) 擔心的「數 GB」問題。真要跑 6 輪自主模式再說。
- `gate:"go"` 的人工閘門寫好了但**沒實測**：`POST /runs` 在 M1 只接受 `mode:"oneshot"`，
  `manual` 回 501，所以那條路現在跑不到（契約 §8.9 明文豁免 oneshot / autonomous）。
