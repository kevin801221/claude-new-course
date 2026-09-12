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
