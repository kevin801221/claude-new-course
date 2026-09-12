# metric-auditor 決策紀錄（M4）

> 擁有者：`metric-auditor`。唯一設計真相是 `_Context/DESIGN.md`，欄位形狀以 `_Context/api-contract.md` 🔒 為準。
> 這份記的是**量了什麼、量出多少、為什麼判成這樣** —— 之後有人問「為什麼 0.677 不算進步」時的證據。
> 最後更新：2026-09-12（M4：noise floor + bootstrap CI + 四項 check + verdict + 圖表 series + 逐張預測）
> 機器：Apple M5 / 24 GB · torch 2.14.0 · ultralytics 8.4.147 · device `mps`

---

## 0. 一句話結論

**這份 dataset 的 valid split（12 張 / 15 個 GT 框）上，noise floor（2σ）= 0.1479 mAP50-95，
而全部三次訓練的 mAP50-95 都落在 0.157–0.305 之間。**
也就是說：雜訊帶的寬度跟「模型有多好」是同一個數量級。
在這個 split 上，**目前沒有任何兩個 run 的差距是可讀的** —— 包含 yolov8n vs yolov8s。

---

## 1. noise floor：同 config × 3 seed（本階段最重要的數字）

`04-experiments/noise-floor/v1.json`，指令：`GET /api/v1/eval/noise-floor/v1?measure=true`
config：`yolov8n / 10 epochs / imgsz 320 / batch 8 / device mps`（DESIGN 護欄 4 的教學 baseline）

| seed | val mAP50-95 | val mAP50 | 秒 |
|---|---|---|---|
| 42 | **0.3045** | 0.6279 | 21.4 |
| 43 | **0.2328** | 0.4957 | 22.4 |
| 44 | **0.1566** | 0.3088 | 20.4 |

- 平均 0.2313、全距 0.1479（0.1566–0.3045）
- **σ = 0.07396**（樣本標準差，n−1；3 個 seed 的自由度只有 2，這件事要講出來）
- **noise floor = 2σ = 0.14792**

怎麼讀這個數字：`|Δ| < 0.1479` 的任何比較一律記「試過、沒用」，不准宣告進步。
以 0.2313 的平均值來說，這等於 **±64% 的相對雜訊**。

**同 seed 是可重現的**：把 seed 42 再訓一次（`04-experiments/noise-floor/_repro-s42/`）
拿到一模一樣的 0.3045 / 0.6279。所以上面的離散度是**真的 seed 效應**，
不是 MPS 的非決定性 —— 這兩者要分開，不然會去修錯的東西。

---

## 2. 實測 verdict（M4 收工時的兩個 run）

| run | train | 模型 | val mAP50 | val mAP50-95 | 95% CI (mAP50-95) | CI 寬度 | anchor recall | verdict |
|---|---|---|---|---|---|---|---|---|
| r30 | r30-t1 | yolov8n | 0.5750 | **0.2662** | 0.1989 – 0.4101 | 0.2112 | 0.6600 | `suspect` |
| r29 | r29-t2 | yolov8s | 0.5472 | **0.2561** | 0.1621 – 0.3909 | 0.2288 | 0.7867 | `suspect` |

**Δ(yolov8s − yolov8n) = −0.0101，noise floor 是 0.1479 → 不顯著，記「試過、沒用」。**
s05 的探針之所以選了 yolov8s，是因為探針自己的 `probe_spread` CI 太窄；
換成真的 noise floor 之後，這個選型差距根本讀不出來 —— 這一條要回饋給 training-engineer。

CI 寬度 0.21–0.23 比 noise floor 還寬：**兩道門檻都沒過的 Δ，一律不是進步。**

---

## 3. 四項 check 的實測結果

| check | r30 | 說明 |
|---|---|---|
| `split_leakage` | ✅ PASS | id 重疊 0、位元級重複 0、近重複 0 |
| `test_size_power` | ❌ FAIL | 12 張 / 15 個 GT 框；**六個類別的 support 全部 < 30**（1/4/2/3/2/3） |
| `anchor_drift` | ✅ PASS | anchor recall 0.66（第一輪基準）→ r29 的 0.7867，同向 |
| `overfit_gap` | ✅ PASS | loss 降 29%，val mAP 最佳落在 epoch 10/10 → **訓練不足，不是過擬合** |

verdict 因此是 `suspect`：數字可以看、可以討論，**但不得作為「進步了」的證據**。

### 三個一開始判錯、修掉的地方（都是「假訊號比沒有訊號更糟」）

1. **近重複門檻寫死 0.02 → 誤報 5 對，verdict 被打成 `invalid`。**
   晶圓圖天生長得像（大片相同背景 + 一小塊缺陷）。改成**由資料自己校準**：
   `train 內部最接近的兩張不同影像` 的距離（實測 0.01249）就是這個資料集的相似度物理下限，
   比它再近一半（< 0.00624）才算可疑。真的複製貼上距離是 0.0，照樣抓得到。
   落在下限內側的（實測 1 對，0.00912）列為提醒，不判 FAIL。
2. **`recall` 用固定 conf 0.25 → 全類別 0.0000，anchor gate 變成永遠讀 0 的裝飾品。**
   10 epoch 的教學 baseline 最高信心只有 0.2。改成**最佳 F1 作業點**（ultralytics 的 R 欄同義），
   anchor recall 才從 0.0000 變成有意義的 0.6600。`recall_at_conf`（固定 0.25）一起回，但不當 gate。
   同一個 bug 讓混淆矩陣整張空白（只有 background 列有數字），一併修掉：
   作業點由 `metrics.operating_point()`（全類別彙總最佳 F1）決定，而且**每個模型不一樣**：
   實測 r30-t1（yolov8n）是 **0.01575**、r29-t2（yolov8s）是 **0.40165** —— 差了 25 倍。
   任何寫死的門檻都會在其中一個模型上量錯。
3. **`overfit_gap` 把「還在爬」判成「在背訓練集」。**
   r30-t1 的 val mAP 從 0.0135 一路爬到 0.2618，最佳值就在最後一個 epoch。
   漲幅（+0.1309）小於 noise floor 是真的，但那叫**訓練不足**，處方是加 epoch；
   判成過擬合的話處方會變成加資料 / 早停，方向剛好相反。加了「最佳 val 落在最後 25% → 還在爬」這一格。

---

## 4. ⚠️ 給 console-owner / dataset-truth：`ds_version` 說謊了

實測 2026-09-12 00:48 有人重跑了 freeze，`02-dataset/` 的 valid 成員與框數都換了
（r30-t1 訓練當時是 12 張 / **13** 個 GT 框，現在是 12 張 / **15** 個），
但 `ds_version` 仍然是 `v1`，`runs.jsonl` 裡三個 run 的 `ds_version` 也都還是 `v1`。
**跨輪比較的前提（同一把尺）已經被破壞，而版本欄位沒有任何反應。**

暫時的補救（已實作，在 metric-auditor 這一側）：
`metrics.dataset_fingerprint()` 對 `02-dataset/labels/{train,valid}` 的檔名 + 內容取 sha1 前 16 碼，
寫進 noise floor 檔與每一份 eval 摘要。
- `POST /eval` 發現 noise floor 的指紋與現況不同 → **409 `NOISE_FLOOR_MISSING`**（要求重量）。
- 挑「上一輪」做 Δ 比較時，指紋不同的一律進 `incomparable`，不算 Δ。
目前指紋：`78cbc26eefc2a816`。

**這是補丁不是解法。** 正解是 freeze 一次就把 `ds_version` 往上推（v1 → v2），
由 dataset-truth 落實、console-owner 在 `runs.jsonl` 帶上。請走 §12。

---

## 5. 判準的定義（有人挑戰時引這一節）

- **AP**：COCO 慣例，IoU 0.50:0.05:0.95 十格平均；AP50 是第一格。all-point 內插，不是 11 點。
- **重複框**：同一個 GT 只准被信心最高的那一個框吃掉，其餘算 FP（不然把 conf 門檻調低就能刷分）。
- **recall / precision**：IoU 0.5 的 PR 曲線上**最佳 F1** 那一點。
- **整體 mAP**：只平均 `support > 0` 的類別。沒有 GT 的類別是「不可評（null）」，不是 0 分。
- **bootstrap**：percentile bootstrap，1000 次，**重抽單位是「影像」不是「框」**。
  一張 donut 圖的 3 個框會一起對或一起錯，對框重抽會把 CI 算窄，然後每輪都宣告顯著進步。
- **σ**：樣本標準差（n−1）。noise floor = 2σ。少於 2 個 seed 直接 `ValueError`。
- **顯著**：`|Δ| ≥ noise floor` **且** `|Δ| ≥ CI 半寬`。兩道都要過。

---

## 6. 封印 test：四道鎖的實測狀態

| # | 鎖 | 實測 |
|---|---|---|
| 1 | sealed-test 不在 `data.yaml` | ✅ `data.yaml` 只有 `train` / `val`，`tests/test_ci.py` 守著 |
| 2 | `/eval` 的 `split=test` → 400 | ✅ `400 SPLIT_TEST_FORBIDDEN`；`GET /eval/{run}/predictions?split=test` 同樣 400。裡層 `metrics.split_dirs("test")` 直接 `ValueError`，不靠 router 自律 |
| 3 | 只有 `/final-test` 讀得到，且要 `stop_event_seq` | ✅ 實測 r30 → `409 NOT_STOPPED_YET`。**目前沒有任何 run 有 `stop` 事件，所以成功路徑無法在 M4 驗** —— 而且 `bus` 擋著 `stop` 前綴（那是 experiment-arbiter 的），metric-auditor 連偽造一筆都做不到。這正是這道鎖要的效果 |
| 4 | 一個 `ds_version` 只准開封一次 | ⏳ 程式已寫（`04-experiments/final-test/<ds_version>.json` 存在即 `409 FINAL_TEST_ALREADY_USED`），要等第一次真的開封才驗得到 |

sealed-test 的**推論路徑**已煙霧測試過（40 張圖 / 51 個 GT 框 / 1635 個預測框，
輸出寫在 session 暫存區、不留在 repo）—— 但**一個指標都沒有算**。
封印還沒開，看數字就是開封。

---

## 7. 落檔位置與檔案

```
04-experiments/noise-floor/v1.json              ← noise floor（含 ds_fingerprint）
04-experiments/noise-floor/nf-v1-yolov8n-s{42,43,44}/  ← 三次 seed 訓練的完整產物
04-experiments/<train_id>/eval/valid.json       ← 逐張預測（座標，不是畫好的 PNG）
04-experiments/<train_id>/eval/anchor.json
04-experiments/<train_id>/eval/summary.json     ← verdict / CI / per-class / charts series
04-experiments/final-test/<ds_version>.json     ← 開封紀錄（存在 = 已用掉）
```

`runs/runs.jsonl` **全程只讀**（team-roles §2.3：判分的手不寫紀錄）。
唯一的寫入是 `registry.set_stage(run_id, "s08", ...)`（state.json）與 `bus.append_event()`。
noise floor 的三次訓練事件寫在 `runs/rnf/`，**不污染任何真的 run 的事件流**。

---

## 8. 契約：需要 console-owner 走 §12 補一列的加欄（向後相容）

`eval.perclass` 的 `data` 在契約 §4 是 `{cls, ap50, recall, support, ci_lo, ci_hi}`，
實作多回了四個**選填**欄位（加欄，不改既有欄名 / 型別 / 語意）：

| 欄位 | 為什麼需要 |
|---|---|
| `name` | 前端不該自己拿 `cls` 去 class_table 對表；而且跨 class_table_version 對錯表就是靜默錯誤 |
| `ap` | 主指標是 mAP50-95 不是 mAP50（護欄 4），只回 ap50 等於逼前端用錯的那一個 |
| `precision` | recall 單獨看沒有意義（全部都報就是 recall 1.0） |
| `best_f1_conf` | 前端疊框要知道作業點在哪；每個模型不一樣（實測 0.0037–0.0517） |

另外 `test.image` 的 `pred_url` 實作指向 `/api/v1/images/{image_id}`（**原圖**），
因為後端不產畫好的 PNG；框在同一包的 `boxes` 裡，由前端疊。
預測檔裡的欄位叫 `iou_vs_gt`，事件裡照契約叫 `iou_vs_label`，兩者同義。

---

## 8.5 M4 對抗審查後的修正（2026-09-12，判準本身被改動的四處）

| 改了什麼 | 為什麼（不改會怎樣） |
|---|---|
| `split_leakage` 的門檻只取**正的**最近距離，且 train 內部位元級重複本身判 FAIL | 舊版 `floor = inner.min()`：train 裡只要有一對完全相同的圖，門檻就變 0，`rmse < 0` 永遠 false → 近重複偵測靜默關掉（fail-open），只剩 sha1，改一個像素就繞過。實測門檻已回到 0.00624（floor 0.01249 × 0.5） |
| noise floor 多量一條 **anchor recall 的 2σ**（`anchor_sigma` / `anchor_noise_floor`），`anchor_drift` 只吃這一條，沒量到就拒答 | 舊版拿 val mAP50-95 的 2σ（0.14792）判 anchor recall 的跌幅。實測同三顆 seed 的 anchor recall 0.7533 / 0.4467 / 0.5700 → 自己的 2σ 是 **0.30855**（2.1 倍）。用錯的那條，純 seed 抖動會被判成「pseudo-label 偏誤 → 立刻 abandoned」 |
| `bootstrap_ci` 回報 `dropped`；丟棄率 > 5% 的類別不給 CI，改給 `ci_note` | 低 support 的類別 GT 全擠在 1 張圖上，實測 center 338/1000、random 332/1000 的重抽抽不到它 → 那些 replicate 被丟掉，剩下的 CI 是「條件在它出現過」下的 CI，卻以無條件 95% CI 的身分畫成誤差線，假性變窄 |
| `POST /eval` 的 `n_boot` 下限 1 → **200**（`metrics.MIN_BOOT`）；nan 的 CI 一律轉 `None` | `n_boot=1` 回 `width 0.0`，`significant()` 的「\|Δ\| < CI 半寬」那道判準恆為 false（護欄 3 的一半被一個參數關掉），而且 nan 會寫成裸 `NaN` 進 summary.json，`/charts` 回它時 starlette 直接 500 |
| `test_size_power` 的 `MIN_SUPPORT` 由達標門檻降格成**註記門檻**（weak 只註記，empty 才 FAIL） | 6 類 × 30 = 180 個 GT 框，出貨的 valid 只有 15 個 → 這一項永遠 FAIL、verdict 永遠 suspect、`trusted` 結構性不可達，DESIGN 的 `plateau_ok`（明文要求 trusted）永遠不會成立。永遠 FAIL 跟永遠 PASS 一樣是零資訊 |

`04-experiments/noise-floor/v1.json` 因此多三個欄位（`anchor_metric` / `anchor_sigma` / `anchor_noise_floor`）。
舊的快取（只有 val 那條）不算量完：`metrics.noise_floor_complete()` 會判它沒量完，
`?measure=true` 會**只補跑 3 次 anchor 推論**（不重訓，實測 8.7 秒）。

## 9. 怎麼重跑（照順序）

```bash
uv sync --extra train                       # torch / ultralytics 只在這個 extra 裡
uv run python -m src.eval.metrics           # bootstrap CI 覆蓋率 + /eval split=test → 400
uv run python -m src.eval.checks
uv run python -m src.eval.series
uv run python tests/test_ci.py              # 10 條，不需要 pytest

uv run --extra train uvicorn src.app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir src
curl "http://127.0.0.1:8000/api/v1/eval/noise-floor/v1?measure=true"      # 約 65 秒（3 次訓練）
curl -X POST http://127.0.0.1:8000/api/v1/eval -H 'Content-Type: application/json' \
     -d '{"run_id":"r30","split":"valid"}'                                # 約 5 秒
curl "http://127.0.0.1:8000/api/v1/eval/r30/verdict"
curl "http://127.0.0.1:8000/api/v1/eval/r30/charts"
curl "http://127.0.0.1:8000/api/v1/eval/r30/predictions?split=valid&offset=0&limit=200"
```

---

## 10. 下一位（experiment-arbiter / M6）拿得到什麼、不准拿什麼

**拿得到**：`GET /eval/{run_id}/verdict` 的 `verdict` / `delta` / `significant` / `anchor_ok` /
`noise_floor` / `weakest_class`、`GET /eval/{run_id}/charts` 的四組 series。

**不准拿**：
- `verdict != "trusted"` 的 run 當「進步了」的證據（`invalid` 連討論都不該進）。
- `support < 30` 的類別指標當停止或達標依據 —— 目前**六個類別全部都是**。
- `anchor_recall` 當最佳化目標（護欄 2：只當 gate 不當 rank，只讀 `anchor_ok: bool`）。
- `final: true` 的紀錄進證據池。
- 跨 `class_table_version` **或跨 `ds_fingerprint`** 的 Δ —— 前者 409 拒答，後者列 `incomparable`。

**而且**：在這個 12 張的 valid split 上，noise floor 0.1479 意味著 arbiter 大概率每一輪都會收到
「不顯著」。那不是系統壞了，那是 DESIGN 護欄 3 預言的結果 ——
處方是**補 valid 資料**（或改用 anchor 的 40 張人工 GT 擴充 valid），不是調參。

---

## 11. 標註天花板（M5+：真實資料進來之後，這個數字第一次有意義）

> 最後更新：2026-09-12（metric-auditor：`src/eval/ceiling.py` + 第五項 check `label_ceiling`）
> 一句話：**以前那個 IoU 1.0000 是退化值，真的天花板是 0.6381。**

### 11.1 兩個數字，差一個等級的可信度

| 來源 | 尺 | n | IoU 中位數 | 95% CI | 可信嗎 |
|---|---|---|---|---|---|
| 合成 GT（demo，M1–M5 全部的綠燈） | `synthetic`，門檻 0.6 | 155 框 | **1.0000** | — | ❌ **退化值** |
| Roboflow 人工 GT · anchor 40 張 | `roboflow_anchor`，門檻 0.4 | 40 框 | **0.8258** | 0.6917 – 0.9169 | ✅ 但取樣有偏 |
| Roboflow 人工 GT · pool 289 張 | 同上 | 293 框 | **0.6381** | 0.6035 – 0.6783 | ✅ **這才是天花板** |

合成那條為什麼必然是 1.0：`gt.json` 的框與 auto-bbox **是同一套連通分量定義生出來的**，
量的是鏡子不是尺。實測 r81 的直方圖 `[0,0,0,0,0,0,0,0,0,155]` —— 155 個框全擠在最後一格。
它只抓得到「抽取器整個壞掉」，抓不到任何標註品質。`scripts/selfcheck.py` 的 assert C
（IoU 中位數 ≥ 0.6）在這條路上永遠是 1.0000，那條 assert 守的是抽取器不是標註。

真人工 GT 那條（WM-811K v3、409 張、413 個人工框、`nc=1 names=['Donut']`）：
`auto-bbox vs 人工框`，逐 GT 取最佳 IoU（漏掉的 GT 記 0，`geometry.best_ious`，
不挑對自己有利的配對）。實測 4.2 秒跑完 289 張。

### 11.2 天花板的 mAP 版本（教學上最有用的一句）

就算模型**完美複製** auto-label 的框，它對人工真相的分數上限就是這批 IoU 的分佈：

| scope | mAP50 上限 | mAP50-95 上限 |
|---|---|---|
| anchor 40 | 0.9250 | 0.6100 |
| **pool 289（進 train+valid 的那批）** | **0.7304** | **0.3399** |

對照 noise floor 2σ = 0.1479：**雜訊帶佔了整個可達區間（0 → 0.34）的 44%。**
「這條線上模型調參能贏多少」的答案是：很少。處方是修 auto-label 或補人工框，不是調參。

### 11.3 ⚠️ anchor 那 40 張是**樂觀**的樣本（新發現）

`freeze.gt_partition()` 照 id 順序切前 40 張當 anchor —— **不是隨機樣本**。
實測 anchor 的人工框中位面積 0.4301、pool 只有 0.2857（缺陷區塊大的比較好框）：

```
anchor 0.8258  CI [0.6917, 0.9169]
pool   0.6381  CI [0.6035, 0.6783]      ← 兩段 CI 不重疊，差 +0.1877 > CI 寬度 0.0748
```

所以 s03 事件裡那個 0.8258 **不能讀成「這個資料集的天花板」**，它是 anchor 切片的天花板。
`ceiling.for_run()` 因此同時回 `pool`（主數字）與 `anchor`（s03 的那筆），差太多時多帶一句
`sampling_note`。DESIGN 指定 anchor 當「每輪量漂移」那把尺沒問題（同一批圖逐輪比較），
問題只在拿它當整體天花板。

### 11.4 退化偵測有兩條腿（任一條成立就是退化）

1. **出處**：`source == "synthetic"` —— 結構事實，不必量。
2. **實測**：`frac(IoU >= 0.999) >= 0.98` —— 抓「拿人工框自己量自己」那面鏡子。
   `labels:"human"` 那條路只要有人把 pred 直接餵成 GT，出處欄位會照樣寫 `roboflow_anchor`，
   **只有第二條腿抓得到**。突變測試驗過：把第二條腿拿掉，`tests/test_ci.py` 立刻紅。

### 11.5 接進 verdict：第五項 check `label_ceiling`

| 情形 | check | verdict | 為什麼 |
|---|---|---|---|
| 真人工 GT、中位數 ≥ 門檻 | PASS + `credible:true` | 不影響 | detail 帶 CI 與 mAP 版天花板 |
| 真人工 GT、中位數 < 門檻 | **FAIL + `fatal`** | **invalid** | DESIGN 的 `label_ceiling`：問題在標註不在模型，這個 mAP 不是模型的成績 |
| 合成退化值 | PASS + **`credible:false`** | 不影響（燈照樣綠） | 「沒量到天花板」≠「天花板很高」；判 FAIL 會把 demo 教學線永久釘在 suspect（同 §8.5 `test_size_power` 那個教訓） |
| s03 沒有 `label.anchor_iou` | FAIL（**不** fatal） | suspect | 不知道 ≠ 已知很糟 |

`verdict()` 多一個 `fatal` 出口（原本只有 `split_leakage` 一條路通往 invalid），
而且 **trusted 的 `why` 會黏上一句「⚠️ 但天花板是退化值不可信」** ——
不然「trusted」會被讀成「連標註品質都驗過了」。實測 r30 的 why：

```
5 項 check 全過；⚠️ 但天花板是退化值不可信（synthetic）：這一輪沒有標註品質證據，
也不得與真人工 GT 那幾輪互比
```

### 11.6 第三把比較的鎖：`ceiling_source`

原本兩把鎖是 `class_table_version` 與 `ds_fingerprint`。現在加第三把：
**跨天花板來源（合成退化值 ↔ 真人工 GT）的 Δ 一律進 `incomparable`，不算 Δ。**
舊的 summary 沒有這個欄位 → 回去讀那個 run 自己的 `label.anchor_iou` 事件
（`ceiling.source_of_run()`），**不用預設值猜**；實測 r30 因此照樣比得到上一輪（Δ −0.0374）。

⚠️ 不要拿 `state.json` 的 `source` 判：實測 r90/r97/r98 是從 `POST /datasets/ingest`
進 roboflow 資料的，`state.source` 仍然是建 run 時寫的 `"demo"`。事件裡的 `source` 才是真的。

### 11.7 s03 放棄閘門與新端點

`GET /api/v1/eval/ceiling?run_id=rNN`（**s03 跑完就問得到，不必先訓練** —— 放棄條件要的就是這個時機）
或 `?ds=dsNN&scope=pool|anchor`。回應是 §11.1 那份摘要 + `gate`：

```json
{"abandon": false, "reason": null, "credible": true,
 "detail": "天花板 0.6381（pool 人工 GT，門檻 0.4）：就算模型完美複製 auto-label，mAP50-95 也不會超過 ~0.34"}
```

`abandon:true`（中位數 < 門檻）的處方是**停在 s03**，不是照樣訓練然後在 s08 說「數字不好」。
🔒 `scope` 只接受 `anchor` / `pool`；`sealed_test` 直接 `ValueError`（第五道側門：那 80 個
人工框是考卷答案）。快取在 `04-experiments/ceiling/<slug>__<scope>.json`，
`RefineParams` 或 `n_boot` 一變就自動重量（幾何參數換了，舊數字量的不是同一條規則）。

### 11.8 順手擋掉一個會 500 的地雷：class 真相表被換過而版本沒動

實測今天 07:24 `_Context/class_table.json` 被後面一個 run 覆蓋成
`nc=3 names=['random','center','donut']`，但 `version` 還是 `v1`，而 `02-dataset` 的標籤
用到 `cls 5`（六類）。結果：`POST /eval` 對**任何**既有 run 都是
`IndexError: index 5 is out of bounds`（HTTP 500，訊息指不到真因）。
這跟 §4 的 `ds_version 說謊` 是同一個病，只是換到 class table 上。

修法（在 metric-auditor 這一側，不碰別人的檔）：`metrics.class_table_fits_labels()`
比對「表的 nc」與「標籤裡真的出現過的最大 class id」，對不上就
**409 `INCOMPARABLE_CLASS_TABLE` + 一句話講清楚怎麼修**，不再 500。
比爆掉更糟的是不爆的那一種：nc 夠大但名字換過，per-class 指標會靜默對錯表。

**正解仍在別人手上**：freeze 一次就把 `class_table_version` 往上推（v1 → v2），
`_Context/class_table.json` 與 `02-dataset/` 要一起換。請走 §12。

### 11.9 怎麼重跑（這一節的全部證據）

```bash
.venv/bin/python -m src.eval.ceiling      # 9 條自檢，含真資料 anchor/pool 兩把（4.6 秒）
.venv/bin/python -m src.eval.checks       # label_ceiling 三種結局 + verdict 階梯
.venv/bin/python -m src.eval.series       # charts.ceiling 照原樣送出去
.venv/bin/python tests/test_ci.py         # 21 條（原 15 + 天花板 6）
curl "http://127.0.0.1:8000/api/v1/eval/ceiling?run_id=r90"          # 真人工 GT：0.6381
curl "http://127.0.0.1:8000/api/v1/eval/ceiling?run_id=r81"          # 合成：退化 1.0
```

### 11.10 🔒 給 console-owner 的四件事（契約 §12 變更請求）

1. **§4 `charts.series` 的 `data` 加一個選填欄位 `ceiling`**（向後相容的加欄）：
   內容是 §11.1 那份摘要（含 `degenerate` / `credible` / `ci` / `map5095_ceiling` /
   `anchor` / `sampling_note`）。前端那張直方圖要靠它把兩種情境畫成兩種可信度。
2. **§9 端點表加一列** `GET /api/v1/eval/ceiling`（metric-auditor，M5）；
   **§10 錯誤碼表加一列** `409 CEILING_UNAVAILABLE`（s03 還沒量 / 這份 ds 沒有人工 GT /
   scope 不合法）。
3. **前端 `drawIou()` 現在把退化值與真人工 GT 講成同一句話**（實測兩者都印
   「這是整條流水線的天花板，模型再好也不會超過它」）。一行就分得開：
   ```js
   const degen = d.degenerate ?? (d.source === 'synthetic');
   // bar 用 var(--ds-ink-dim) + 文案換成
   // 「⚠️ 退化值：合成 GT 與 auto-bbox 同一套連通分量定義，IoU 必然 1.0 —— 不是天花板證據」
   ```
   更完整的一份（CI、mAP 上限、anchor vs pool）在 `GET /eval/ceiling?run_id=`，
   **s03 一跑完就拿得到**，不必等 s08。
4. **driver 要接放棄閘門**：`console.drive_run()` 跑完 s03 之後
   ```python
   from src.eval import ceiling
   g = ceiling.gate(ceiling.for_run(run_id, ds_id=ds_id))
   if g["abandon"]:   # reason = "label_ceiling"
       ...  # 發 stage.failed / 停在 s03，不要進 s02→s08
   ```
   目前**沒有人接**：天花板低於門檻的 run 照樣會一路訓練到 s08，只是 verdict 會被判 invalid。
