# CV 自我進化主控台 — 設計定案（唯一真相）
> 由 4 種分解軸平行設計 + 對抗評審後綜合而成。**這份是規格，不是建議。**
> 使用者已拍板：類別走非監督分群 + LLM 命名；兩個 preset（teaching / real）；> 自主模式上限 USD 3 / 60 calls per run；專案開在 `agent_group_projects/cv-self-evolving-console/`。
## 總綱

五位專家：資料真相（零人工標註的 auto-label + class 表 + split + 信任錨）、訓練工程（選型 + 配方 + 不阻塞的訓練）、指標審計（唯一有權說「這算不算真進步」、持有封印 test 的鑰匙）、實驗裁決（OFAT + 討論主持 + GPU 與 token 雙預算）、主控台（前端 + 唯一事件總線 + 一鍵腳本 + provenance 比對）。切線畫在「證據來源不同」與「球員不得兼裁判」上，不是畫在 pipeline 步驟上。

---

## 專家編制（5 位）

### 資料真相與零標註專家 dataset-truth — `dataset-truth`
**一句話**：唯一有權說「這批資料長什麼樣、有幾類、每張的框在哪」的人：逐張串流進場、在零人工標註下自己生 bbox 與類別、凍結 class 真相表與 split，並把 Roboflow 的人工 GT 切成永不進迴圈的信任錨。

**研究什麼**

**(1) 零人工標註的 auto-label 兩階段落地（本案最硬的一塊，寫死如下）**。第一階段「無類別 bbox」：wafer map → 缺陷 die 二值遮罩 → `scipy.ndimage.label` 連通分量 → 形態學去雜點 → 每個分量取外接框。純幾何、**不需要任何類別、不需要任何預訓練權重**，`scipy` 與 `opencv-python` 已在既有 `uv.lock`（ultralytics 的傳遞依賴，實測確認），不用 `uv add` 任何東西。第二階段「類別從哪來」：對每張圖算幾何描述子（徑向分佈直方圖 8 bin、ring-ness = 分量平均半徑/標準差、center-ness、PCA 長短軸比、邊緣佔比、分量數、總缺陷率）→ `sklearn` KMeans（k 由 silhouette 在 3–6 之間自選）→ 每群抽 9 張代表圖拼 montage → **一次 LLM 呼叫看 montage + 群統計後給每群命名**（center / donut / edge-ring / edge-loc / scratch / random）。這才是真的「AI 自己做 labeling」：類別來自非監督分群 + 一次可稽核的命名，不是偷讀人工標籤。`_Context/wafer-basics.md` 那 6 條草案不丟 —— 它們變成「群命名後的 bbox 精修規則」（被命名為 donut 的群，其成員 bbox 改取 30–80% radius 環的外接框；scratch 群用 PCA 主軸擴框係數，係數本身是可被 patch 的旋鈕）。
**(2) 信任錨的免費來源**：Roboflow 那 409 張已有人工 bbox，切成 `anchor`(40 張，每輪量漂移) / `sealed-test`(80 張，整個 run 家族只開封一次) / `unused`。這直接消掉「human held-out 沒有生產者」這個致命缺口 —— 不需要任何手工畫框 UI。
**(3) 離線 demo 模式必須能真的跑**：既有 48 張 SVG data-URI 合成圖沒有像素，連通分量跑不出東西。改寫 `scripts/gen_demo_wafers.py` 用 numpy 產真點陣 wafer PNG（沿用既有 4 種缺陷型態），**GT bbox 因為是程式生成的所以免費且精確**，IoU 校準在離線模式一樣成立。
**(4) 洩漏與命名裁決**：pHash 跨 split 近重複、group leakage（同片 wafer 的多張圖不得分家）、三套 class 表（6 類範本 / `prepare_dataset.py` 硬編 `CLASS_NAMES={0:"Donut"}` / demo 4 類）與 `valid` vs `val` 兩套目錄名的一次性裁決。
**(5) 抽樣一致性**：把前端 `mulberry32` + 最大餘數法移植到 Python，同 seed 同輸入必須與前端 `stratifiedSplit` 逐筆相同（留 assert）。

**決定什麼**

frozen `class_table.json`（nc + names + version + 每群的命名理由與 montage 路徑）；每類的 bbox 精修規則與信心門檻；GT 三分（anchor 40 / sealed-test 80 / unused）與各自的用途鎖；split 目錄一律 `valid`（前端勝，`prepare_dataset.py` 的 `write_data_yaml()` 改寫成 names list + 相對 path，且 **data.yaml 只寫 train/valid，test 根本不出現**）；哪些圖信心太低進送審清單；近重複要剔除或強制同 split；`ds_version` / `label_version` 的發放（一發出就不准改內容）。

**依賴**：console-owner

**為什麼這個角色不能跟其他四位合併成同一個**（球員不能兼裁判，白話解釋見 `_Context/README.md` Q1）

不能併進 metric-auditor：這位製造量尺（anchor 的組成、auto-label 的框），metric-auditor 判分。量尺的製造者兼判分者，anchor 就會被調整到剛好通過。也不能併進 training-engineer：它產出的是**訓練的前提**（ground truth），訓練者產出的是**對前提的擬合**；合併就是自己出考卷自己改，auto-label 的偏誤會直接被當成 mAP 的勝利。既有 `data-hunter`（抓資料）與 `bbox-labeler`（格式驗證）整批降為它的 executor 工具層，檔案不動 —— `bbox-labeler.md` 明文的「不要重新生成 bbox」翻轉為「Roboflow 有框走原驗證路徑（anchor/test 用），auto 模式走 propose 路徑（train/valid 用）」，驗證程式碼全留。

**API**

| Method | Path | Stream | 用途 |
|---|---|---|---|
| `POST` | `/api/v1/datasets/ingest` | SSE | 逐張進場。body {source:"roboflow"|"demo"|"local", limit, seed} → {ds_id, total}。roboflow 走 server env 的 ROBOFLOW_API_KEY（前端不再持 key），demo 走 gen_demo_wafers.py。每張推一個 ds.image 事件。 |
| `GET` | `/api/v1/datasets/{ds}/images` | — | 分頁快照，回應形狀刻意與 Roboflow search 相容 {offset,total,results:[{id,name,url,split,annotations:{count,classes}}]}，既有 loadRoboflow() 幾乎不用改；也是 SSE 重連後補齊早期縮圖的來源。 |
| `POST` | `/api/v1/label/auto` | SSE | 兩階段 auto-label：{ds_id, mode:"cluster"|"refine_only", refine_params?} → 逐張推 label.bbox / label.lowconf，群命名完推 class.table.frozen。這就是規格 2+3 的入口，全程不讀任何人工標籤。 |
| `GET` | `/api/v1/datasets/{ds}/classes` | — | class 真相表 {version, nc, names[], cluster_stats, naming_rationale, montage_urls[]}。全系統唯一 class 來源；所有 run 記 class_table_version，metrics 對跨版本比較直接拒答。 |
| `POST` | `/api/v1/datasets/{ds}/freeze` | SSE | 分層抽樣 + 寫 02-dataset/{images,labels}/{train,valid} + data.yaml，回 selfcheck 清單（nc/names 與 class_table 相符、兩目錄實體存在、每行 YOLO 格式合法、train∩valid 無近重複）。任一 FAIL 前端訓練鈕保持 disabled。 |
| `GET` | `/api/v1/datasets/{ds}/report` | — | 難度剖面 + 標註品質：{imbalance_ratio, bbox_area_p50, small_object_pct, per_class_support, dup_pairs, anchor_iou_median, anchor_iou_hist}。anchor_iou_median 就是整條流水線天花板的證據，前端畫成直方圖。 |
| `GET` | `/api/v1/images/{image_id}` | — | 圖片 proxy（含縮圖尺寸與 ETag），讓 API key 只留 server env、解掉「key 只能 localhost」那條部署禁令。 |

**產出檔案**

- `src/app/routers/dataset.py（7 支 API）`
- `src/autolabel/geometry.py（連通分量 + 6 條精修規則）`
- `src/autolabel/cluster.py（描述子 + KMeans + montage）`
- `scripts/gen_demo_wafers.py（真點陣合成圖 + 免費 GT）`
- `scripts/prepare_dataset.py 改寫（valid 目錄 / names list / 只寫 train+valid）`
- `_Context/dataset-notes.md（anchor IoU 對照表 + 三套 class 表的裁決紀錄）`
- `tests/test_split_parity.py（Python 分層抽樣 == 前端 stratifiedSplit）`

### 訓練工程專家 training-engineer — `training-engineer`
**一句話**：抓模型、用同 seed 短探針自己選型、決定超參與 augment（含 wafer 對稱性該開哪些）、跑訓練並逐 epoch 串流真實指標；訓練是獨立 subprocess，不阻塞 API。

**研究什麼**

**(1) 選型實證而非查表**：候選只留 `yolov8n` / `yolov8s`（`rtdetr-l` 砍掉 —— MPS 上 deformable attention 會靜默走 `PYTORCH_ENABLE_MPS_FALLBACK` 掉回 CPU，慢 10–50 倍，把 budget 帳算爛）。探針規則刻意寫死以免產出雜訊排名：**同一組 seed、同一份 dataset、同一組 recipe、跑到 mAP50-95 曲線第一次持平（不是固定 3 epochs）**，並且 scoreboard 上每個候選都要附 metric-auditor 給的 CI，差距落在 CI 內就選小的（實測 log 顯示 81 張 val 的相鄰 epoch mAP50-95 在 0.275–0.611 之間跳，這一條是防止候選表變成隨機數的唯一辦法）。
**(2) wafer 對稱性的領域判斷（真正有對錯、會被挑戰的一條）**：rotate/flip 合法（晶圓無固定方向），`mosaic` 與 random-crop 必須關 —— 會破壞 edge-ring / edge-loc 的「靠邊」語意。每個 augment 開關旁必須印一行理由。
**(3) ETA 硬體校準（不可省）**：`8.89 分鐘 / 50 epochs` 是 M4 Max 的數字，學生的 M1 Air 慢 3–10 倍。開跑前先跑 2 epochs 得 `scale_factor` 寫進 runs.jsonl，之後所有 ETA 乘上去。這是預算閘門能不能成立的前提。
**(4) process 邊界（評審點名的致命缺口）**：`model.train()` 是阻塞碼，塞進 `BackgroundTasks` 會佔住 ASGI event loop（心跳停、SSE 卡死、cancel 無效、matplotlib 非主執行緒崩）。定案：`subprocess.Popen` 跑既有 `scripts/train_yolo.py`；per-epoch 指標由**子行程內掛 Ultralytics `on_fit_epoch_end` callback 直接 append events.jsonl**（不 parse stdout —— 實測 training.log 帶 ANSI 與 \r 進度條，是坑）；cancel = `SIGTERM(pid)`；pid 與狀態寫 `runs/<run_id>/state.json`。
**(5) OOM 與中斷**：batch 8→4 退避、`PYTORCH_ENABLE_MPS_FALLBACK=1`、`resume=True` 的條件；每輪結束刪非 best 權重（自主跑 6 輪的 runs/ 會吃掉數 GB）。吸收 WALKTHROUGH 的 hyper-tuner search space（lr ∈ [1e-4,1e-2]、imgsz ∈ {416,640}）。

**決定什麼**

候選池與入選理由；評分函式（`score = map5095_probe − λ·normalized_min − μ·normalized_ms`，差距落在 CI 內選小的）；本輪 recipe（lr0/optimizer/epochs/batch/imgsz）；每個 augment 開關 + 理由；ETA 與 scale_factor；要不要申請一次 2-factor sweep；非 best 權重何時刪。**教學 baseline 刻意調弱**（起點 `yolov8n / 10 epochs / imgsz 320`），否則從 mAP50 0.977 起跑，每一輪都會判 no_gain，台上看到的「自我進化」是三輪「沒有進步」。

**依賴**：dataset-truth, console-owner

**為什麼這個角色不能跟其他四位合併成同一個**（球員不能兼裁判，白話解釋見 `_Context/README.md` Q1）

不能併進 metric-auditor —— 這是全案最重要的一條分權：訓練者兼判分者會出現「跑到 val 最好的那個 epoch 就收手並拿它當成績」，這正是 metric 刷分最常見的形式。它只能回報曲線與 gap，不能宣布誰贏。**反過來說，架構選型與超參配方刻意合併成這一位**（四份評審全部提出這個合併）：拆成兩位的理由「避免 confounding」其實是 arbiter 的 OFAT 裁決在執行 —— patch 一次只發一個鍵名，紀律不變；而拆開的實際代價是兩個角色搶同一張 GPU 卻各有預算，還多一條沒人審計的探針證據線。合併後省 5 支 endpoint 與整個候選看板的第二套實作，且 backbone × lr 的交互作用終於有人負責。既有 `training-runner` 併入此位。

**API**

| Method | Path | Stream | 用途 |
|---|---|---|---|
| `GET` | `/api/v1/models/candidates` | — | {name, params_m, weights_cached, sha256, mps_ok, est_min}（est_min 用 8.89min/50ep 基準 × 本機 scale_factor 換算）。前端 #model 從硬寫三個換成這支回應 + 「AI 建議」標記，仍可人工覆寫（覆寫會留事件痕）。 |
| `POST` | `/api/v1/models/probe` | SSE | 短探針 {ds_version, label_version, names[], seed} → 逐個候選推 probe.epoch / probe.rank。權重從 ultralytics CDN 下載到 00-weights/ 並記 sha256 + 做一次 dummy forward 驗 mps 可跑；離線環境回既有快取並標 offline，不讓整條流程卡在下載。 |
| `POST` | `/api/v1/train` | SSE | 正式訓練 {ds_version, label_version, model, recipe, gate:"go"|"auto"} → {train_id, pid, eta_min}。subprocess 起跑，立即回應不阻塞。gate:"go" 時停在 awaiting_go（手動模式保留既有人工閘門）。 |
| `POST` | `/api/v1/train/{train_id}/cancel` | — | SIGTERM 子行程並確認收乾，推 train.cancelled 才算停成功。前端 Stop 鈕與預算超限都走這條（既有前端完全沒有停止鈕，這是必補的）。 |
| `GET` | `/api/v1/train/{train_id}/state` | — | 不依賴 SSE 的狀態快照 {status, epoch, pid, alive, best_pt, last_pt, elapsed_s, scale_factor}。API 重啟後靠這支 + os.kill(pid,0) 認領或標 crashed。 |
| `POST` | `/api/v1/sweep` | SSE | 2-factor 小 sweep {factors[2], trials<=4, epochs:8}，每 trial 推 sweep.trial 並寫 runs.jsonl。需 arbiter 批 budget（每 3 輪最多一次），用來補 OFAT 看不到交互作用的盲點。 |

**產出檔案**

- `src/app/routers/train.py（6 支 API）`
- `src/train/runner.py（subprocess + pid 管理 + 重啟認領）`
- `src/train/callbacks.py（on_fit_epoch_end → events.jsonl）`
- `src/train/select.py（探針 + 評分函式）`
- `scripts/train_yolo.py 改寫（掛 callback、吃 recipe.json、印 scale_factor）`
- `_Context/train-notes.md（候選對照表 + augment 決策理由 + 各機型 scale_factor 實測）`

### 指標可信度審計官 metric-auditor — `metric-auditor`
**一句話**：唯一有權說「這個數字算不算真的進步」的人：算 noise floor 與 bootstrap CI、跑 evaluation 與 testing、產所有圖表的 series，並持有 sealed test 的鑰匙。

**研究什麼**

**(1) noise floor 先量再談進步**：同 config × 3 seed 重跑量 mAP50-95 標準差（實測 log 的相鄰 epoch 就在 0.275–0.611 之間跳，不先量這個數字，後面所有 Δ 判定都是自欺）。noise floor = 2σ。
**(2) bootstrap CI**：81 張 val 的 CI 有多寬、n<30 的類別其指標一律只能當提示不能當停止依據（樣本量 gate）。
**(3) 洩漏與可信度四項 check**：train/valid 的 image id 交集與近重複、測試集統計力、overfit gap（train loss 降但 val mAP 平）、**anchor drift（auto-label 訓出來的模型對 Roboflow 人工 GT 的 recall 有沒有跟著漲）**。
**(4) 圖表 series 與降採樣**：Ultralytics 產的是 PNG，PNG 不能 hover 也不能跨輪疊圖 → 反向做成 JSON（per-class 矩陣、PR 取樣點、曲線）。純 inline SVG 自畫（維持單檔零依賴、不引 CDN），配色只准取 `--ds-*` token，數字字型 Menlo。
**(5) 主指標的選擇**：不是 mAP50（實測已 0.977，頂天沒有資訊量），而是 `val mAP50-95` + 最弱類別 recall 的組合。

**決定什麼**

主指標的定義（在 change_plan 裡標 `locked`，任何一輪不准改 —— 防「換個 metric 就贏了」）；noise floor 的數值；每個 run 的 `verdict ∈ trusted|suspect|invalid` 與理由清單；一個 Δ 算 improved / noise / regressed；哪些圖表能當討論證據；**對所有提案的否決權**（被判 invalid 的 run 不得被任何人引用）；sealed test 何時可以開封。

**依賴**：training-engineer, dataset-truth

**為什麼這個角色不能跟其他四位合併成同一個**（球員不能兼裁判，白話解釋見 `_Context/README.md` Q1）

它是整個自我進化迴圈唯一的煞車，必須是**沒有 budget 壓力、不擁有任何變因、不提任何提案**的角色。合進 training-engineer 是球員兼裁判；合進 arbiter 更糟 —— arbiter 想早點收工，會傾向把 suspect 判成 trusted 好宣告收斂；合進 dataset-truth 則是量尺製造者兼判分者。它也只能**讀** runs.jsonl 不能寫（寫紀錄的手與判分的手分開，否則「讓結論通過」的最短路徑是改 baseline）。既有 `inference-runner` 與 WALKTHROUGH 的 `evaluator` 設計稿併入此位。

**API**

| Method | Path | Stream | 用途 |
|---|---|---|---|
| `POST` | `/api/v1/eval` | SSE | {train_id, split:"valid"|"anchor"} → 逐步推 eval.leakage / eval.perclass / eval.ci / eval.verdict。split 只接受這兩個值；傳 "test" 一律 400。 |
| `GET` | `/api/v1/eval/noise-floor/{ds_version}` | — | {sigma, noise_floor, seeds[], n_val, warning?}。沒有這份，arbiter 的裁決與所有停止條件拒絕執行（回 409 並說明要先量 noise floor）。 |
| `GET` | `/api/v1/eval/{run_id}/charts` | — | 圖表資料（series JSON，不送 PNG）：{curves[], pr[], confusion{labels,matrix}, per_class[{cls,ap50,ap,recall,ci_lo,ci_hi,support}], speed{pre,infer,post}}。前端自畫 SVG 才能 hover 與跨輪疊圖。 |
| `GET` | `/api/v1/eval/{run_id}/verdict` | — | {verdict, checks:[{name,pass,detail}], main_metric, delta, ci, noise_floor, significant:bool, weakest_class, anchor_ok:bool}。checks 至少含 split_leakage / test_size_power / anchor_drift / overfit_gap。跨 class_table_version 的比較直接拒答並標「不可比」。 |
| `POST` | `/api/v1/final-test` | SSE | 唯一會讀 sealed test 的端點。需 body {run_id, stop_event_seq}；沒有 stop 事件（未 converged/abandoned）一律 409「還沒停，不准看 test」；同一個 ds_version 只准成功一次，第二次 409。結果寫 runs.jsonl 並標 final:true。 |
| `GET` | `/api/v1/eval/{run_id}/predictions` | — | 逐張預測快照 ?offset=&limit=（seed 42 抽樣，沿用既有 inference-runner 慣例），[{image_id, pred_url, boxes, iou_vs_label}]，供測試牆重連補齊。 |

**產出檔案**

- `src/app/routers/eval.py（6 支 API）`
- `src/eval/metrics.py（bootstrap CI + noise floor + per-class）`
- `src/eval/checks.py（四項 check + verdict）`
- `src/eval/series.py（Ultralytics 產物 → JSON series）`
- `_Context/eval-notes.md（noise floor 實測 + 症狀→歸責對照表）`
- `tests/test_ci.py（bootstrap CI 的 assert 級檢查）`

### 實驗歸因裁決官 experiment-arbiter — `experiment-arbiter`
**一句話**：不擁有任何 ML 變因，只裁決「這一輪只准改哪一個」：主持討論、翻譯使用者的話、記 budget（GPU 分鐘 + LLM 花費）、決定什麼時候停。

**研究什麼**

**(1) 討論的執行體怎麼落地（評審點名最大的懸空處）**：Claude Code 沒有原生 agent-call-agent（wafer WALKTHROUGH 行 519 自承）。定案：五位專家在執行期 = 五份 system prompt 檔（`_Context/prompts/<slug>.md`）+ `claude -p --output-format stream-json` subprocess，由 arbiter 平行 spawn 並 tail stdout 轉事件。`.claude/agents/*.md` 是**開發期的作者身分**，不是執行期的發言者 —— 這條必須寫進教材，否則學生會以為 subagent 可以被 HTTP 叫起來。
**(2) LLM 成本記帳**：從 `claude -p` 的 usage 欄位累加 token 與 USD，寫進 runs.jsonl。每輪固定 7 calls（4 提案 + 1 挑戰 + 1 裁決 + 1 回覆），所以上限可預估。
**(3) OFAT 與去重**：讀 runs.jsonl 做跨 run 歸因；**去重比對凍結後 recipe diff 的實際鍵名，不是自然語言描述**（封死「同一個變因換個名字再提」）；提升幅度沒超過 metric-auditor 給的 CI 寬度就視為沒提升。
**(4) 自然語言 → 結構化約束**：使用者的否定句（「別再加 epoch」）必須編譯成硬約束 `forbid:["epochs↑"]`，不然下一輪還是有人提。
**(5) 最小重跑**：patch 的最上游階段決定 `rerun_from`（只動 train.* → 從 s07；動 autolabel.* → 從 s03；動 split.seed → 從 s04），沒動的 stage 標「沿用上輪」。

**決定什麼**

每一輪的唯一變因與 patch；哪些提案因無證據 / 重複 / 違反 forbid 而作廢；budget 批不批（sweep、加輪數）；收斂 / 放棄 / 繼續；使用者自然語言 → ask + forbid 的翻譯；討論輪的發言順序與挑戰回合上限。

**依賴**：metric-auditor, training-engineer, dataset-truth, console-owner

**為什麼這個角色不能跟其他四位合併成同一個**（球員不能兼裁判，白話解釋見 `_Context/README.md` Q1）

它是四位之外的第五種判斷：不是「該用什麼」，而是「這一輪值不值得花這 9 分鐘、誰的主張有歸因證據」。任何一位專家兼任都會偏向自己的變因（training-engineer 每輪都想調 lr），OFAT 紀律立刻崩。它也是唯一擁有 budget 與停止權的人 —— 這個權力給了有立場的人，自主模式就會整夜燒 GPU 與 token。**但它刻意不再兼 API gateway**（兩份評審都點名這點）：`/pipeline/run`、`/events`、`/runs/{id}` 快照全部移交 console-owner 當基礎設施，arbiter 只留討論、記憶、budget 三件事。WALKTHROUGH 的 `orchestrator` 設計稿併入此位（機械執行那半歸 console-owner）。

**API**

| Method | Path | Stream | 用途 |
|---|---|---|---|
| `POST` | `/api/v1/chat` | SSE | 使用者自然語言進來 {run_id, text} → {ask:{metric,class,direction}, forbid[], round_id}，並開一輪討論。逐條推 chat.user / arbiter.ask / expert.msg。前端先回顯一行 ask 讓人確認，3 秒沒動作自動採納（不擋流程）。 |
| `POST` | `/api/v1/rounds` | SSE | 開一輪專家討論 {run_id, ask?, forbid?, trigger:"user"|"autonomy"} → {round_id}。平行 spawn 四位（console-owner 不參與，它刻意不懂 CV），逐條推 expert.msg / expert.challenge / arbiter.decision。 |
| `GET` | `/api/v1/rounds/{round_id}` | — | 一輪的完整逐字稿 {ask, forbid, proposals:[{expert,factor,direction,expected_delta,evidence_path,cost_min,status}], challenges[], decision:{one_factor,patch,reason,budget_left}}。關掉瀏覽器再回來靠這條補完整。 |
| `POST` | `/api/v1/rounds/{round_id}/apply` | — | 把裁決變成下一個 run {round_id} → {next_run_id, parent_run, rerun_from, reused_stages[], est_min}。手動模式要前端按「同意並重訓」才打這支。 |
| `POST` | `/api/v1/autonomy` | — | 自我進化開關 {run_id, on, preset:"teaching"|"real"}。preset 決定三軸預算；不給上限直接回 400（沒有無限進化這個選項）。on=false 同時 cancel 進行中的訓練。 |
| `GET` | `/api/v1/runs` | — | 記憶讀出：runs.jsonl 全量（run_id, parent_run, patch, params, ds_version, label_version, class_table_version, val_map5095, ci, verdict, anchor_ok, cost_min, cost_usd, llm_calls, provenance_ok, arbiter_reason），?format=md 產人讀版 experiments.md（沿用既有 schema，不另發明）。 |

**產出檔案**

- `src/app/routers/round.py（6 支 API）`
- `src/evolve/driver.py（迴圈 + 三軸 budget + 停止條件，獨立 process 不是 BackgroundTask）`
- `src/evolve/llm.py（claude -p subprocess + usage 記帳）`
- `src/evolve/ofat.py（提案去重 + 排序 + patch 產生，純函式可 assert）`
- `_Context/prompts/*.md（五份 system prompt）`
- `scripts/evolve.py（CLI 入口）`
- `tests/test_ofat.py（給定五份提案與預算，輸出同一個 patch）`

### 主控台與事件總線 console-owner（team lead） — `console-owner`
**一句話**：擁有前端那一整塊、唯一的事件總線、run registry、一鍵腳本與 API 契約；刻意不懂 CV（不算 metric、不碰 .pt、不提訓練參數），所以它的 provenance 否決才可信。

**研究什麼**

**(1) 前端真的要改什麼（評審點名「沒有人擁有前端」是致命缺陷，這位就是答案）**：既有 `prototype/index.html` 455 行，`render()` 目前是整面 `wall.innerHTML` 重繪（48 張 OK，逐張串流會抖）→ 改成 append 單卡。要新增：EventSource client、Stop 鈕、候選看板、verdict badge、四張 inline SVG 圖表（訓練曲線 / PR / 混淆矩陣 / per-class AP + CI 誤差線 + noise floor 灰帶）、四條 lane 的討論時間軸、budget 條、跨輪 sparkline、log ring buffer 500 行。誠實估：455 → 約 1600 行。**其他四位只准交 JSON 與 chart series 契約，一行前端都不准改**（照 `agent_teams/_Context/team-roles.md` 的目錄不重疊原則）。
**(2) SSE 重播與背壓**：append-only `events.jsonl` + 單調 seq + `Last-Event-ID`/`?since=`、15 秒 heartbeat、前端 45 秒無訊息自行 close 重開、大量事件改走 REST 快照 + 游標、冪等 upsert。
**(3) 重啟認領**：掃 runs.jsonl 中 `status=running` 的 run，用 `os.kill(pid,0)` 判活 → 活著重新 tail、死了標 `crashed` 並推 stage.failed（否則前端把「子行程死了」顯示成「進度條卡住」）。
**(4) uvicorn --reload 的坑**：`events.jsonl` 寫在專案樹內，`--reload` 會每寫一筆就重啟並殺掉 driver → 定案 `--reload-dir src`，且 driver 是獨立 process。
**(5) 契約凍結流程**：照 arxiv 那份 `api-contract.md` 的 🔒 範式（每條 endpoint 附完整 JSON 範例與錯誤行為，改契約要走批准）。

**決定什麼**

eventEnvelope 的唯一真相與 stage 代號表（`s01`..`s11`）；每位專家的 `type` 前綴命名空間；SSE 協議與重連語意；前端所有面板與配色（只准取 `--ds-*` token，code 與數字一律 Menlo）；**provenance 判定**（change_plan 宣稱 vs 實際 run args / ds_version / label_version / class_table_version 逐欄比對，不符標 invalid 且不得進入任何比較）；一鍵腳本的 CLI 介面；API 契約的變更批准。

**依賴**：（無）

**為什麼這個角色不能跟其他四位合併成同一個**（球員不能兼裁判，白話解釋見 `_Context/README.md` Q1）

不能併回 arbiter：一個是確定性的執行與事件排序層（可以位元級重播驗證、收斂規則能寫 assert），一個是非確定性的 LLM 討論層；合併後「重播」這個能力就死了，而且 LLM 的延遲會卡住事件流的背壓處理。不能併進任何 ML 專家：它刻意沒有 ML 判斷權，正因為沒有立場，provenance 否決才可信；一旦它也有 ML 意見，它就會變成那個順手把自己提案寫進 plan 的人。它也是**前端的唯一擁有者** —— 這一位不存在，五個人就會同時改同一個 index.html，整合當天互相覆蓋。

**API**

| Method | Path | Stream | 用途 |
|---|---|---|---|
| `POST` | `/api/v1/runs` | — | 前端那一顆按鈕。{mode:"oneshot"|"manual"|"autonomous", source, preset, budget?} → {run_id, stages[], events_url}。依序驅動 s01→s08，回應裡就有完整 stage 骨架讓前端立刻畫出來。scripts/run_all.py 呼叫同一個函式（不是第二套邏輯）。 |
| `GET` | `/api/v1/runs/{run_id}/events` | SSE | 全系統唯一的 SSE（?since=<seq> 或 Last-Event-ID 重播）。tail runs/<run_id>/events.jsonl，五位專家的事件都在這一條裡，UI 用 actor 分流。since=0 全段回放。 |
| `GET` | `/api/v1/runs/{run_id}` | — | run 快照 {stages:{s01..s11:status}, last_seq, round, budget:{gpu_min_used,gpu_min_cap,llm_calls,cost_usd}, best_run, artifacts[], autonomy}。後進場或長離線先拉這支把畫面一次補到位，再帶 last_seq 續流。 |
| `POST` | `/api/v1/runs/{run_id}/cancel` | — | 整條 run 中止：轉發 train/cancel、關 autonomy、寫 stop 事件。前端的大顆 Stop 走這條。 |
| `GET` | `/api/v1/runs/{run_id}/provenance` | — | {plan_vs_actual_diff, provenance_ok, invalid_reason?}。不通過的 run 一律不得進入任何比較 —— 這是「討論說要改的跟實際跑的不一樣」的唯一捕手。 |

**產出檔案**

- `prototype/index.html 演進版（唯一前端，約 1600 行，單檔零依賴、?selftest=1 擴充到 15 條 assert）`
- `src/app/main.py（單一 FastAPI app，五個 router 掛進來）`
- `src/app/bus.py（events.jsonl 單 writer + seq + SSE tail + heartbeat）`
- `src/app/registry.py（runs.jsonl + state.json + 重啟認領 + provenance 比對）`
- `scripts/run_all.py（一鍵 CLI）`
- `scripts/selfcheck.py（三條 assert：事件重播一致 / 抽樣一致 / auto-bbox IoU 下限）`
- `_Context/api-contract.md 🔒`
- `_Context/team-roles.md（角色 × 可寫目錄）`

---

## 前端事件流（按一顆按鈕之後依序發生）

### `s01` 01 資料一張一張進來（逐張串流）
**擁有者**：`dataset-truth`

**事件**

- `run.created {run_id,stages,preset}`
- `ds.total {total,source}`
- `ds.image {id,name,url,cls:[],counts:{},split:null}`
- `ds.progress {loaded,total}`

**前端看到什麼**

縮圖牆從 0 張逐卡 append（不再整面 innerHTML 重繪，避免抖動），`#src` 顯示來源字串、`#shown` 的「可見/總數」邊跑邊長，`#key` 輸入欄消失（API key 搬到 server env）。離線模式走新的真點陣合成圖產生器（不是原本的 SVG），所以後面每一個 stage 在沒有網路、沒有 Roboflow 帳號的情況下都是真的在算，不是假動畫。

### `s02` 02 AI 自己做 labeling（零人工標註，兩階段）
**擁有者**：`dataset-truth`

**事件**

- `label.mask {image_id,defect_ratio,n_components}`
- `label.descriptor {image_id,ring_ness,center_ness,elongation,edge_frac}`
- `class.cluster {k,silhouette,cluster_sizes,montage_urls}`
- `class.table.frozen {version,nc,names,naming_rationale}`

**前端看到什麼**

每張卡先長出「已算遮罩・N 個分量」狀態點；分群完成後中欄切出一排 montage（每群 9 張代表圖），旁邊印 LLM 的命名理由（例：「這群 ring_ness 0.91、平均半徑集中在 0.55R → donut」）。右欄出現 class 真相表卡片（nc / names / version），把 repo 裡 6 類 / 1 類 / 4 類三套並存一次收斂成一套，並顯示每群的 support 與哪些類因樣本太少被排除。

### `s03` 03 生成這個模型的 bbox（含天花板證據）
**擁有者**：`dataset-truth`

**事件**

- `label.bbox {image_id,boxes:[{cls,cx,cy,w,h,conf,rule}]}`
- `label.lowconf {image_id,conf}`
- `label.anchor_iou {iou_median,iou_hist,verdict}`

**前端看到什麼**

縮圖上即時疊出 bbox（coral 描邊）與 class chips（`scratch ×4`），點任一框彈出「用了哪條規則」一行理由（連通分量外接框 / 30–80% 環 / PCA 主軸擴框）。低信心卡描 err 色邊框並進「送審清單」。下方固定一張直方圖：**auto bbox vs Roboflow 人工 GT 的 IoU 分佈 + 中位數**，以及一行裁決（中位數 ≥ 0.4 才准往下走，否則直接停在這裡宣告「問題在標註不在模型」）。這張圖是整條流水線的天花板證據，不是裝飾。

### `s04` 04 落檔成 dataset（含硬閘門）
**擁有者**：`dataset-truth`

**事件**

- `ds.split {train,valid,unassigned}`
- `ds.gt_partition {anchor:40,sealed_test:80,unused}`
- `ds.written {path,data_yaml}`
- `ds.selfcheck {checks:[{name,pass,detail}]}`

**前端看到什麼**

`#n-train`/`#n-valid`、`#bar` 比例條、`#health`、`#dist` 全部改由後端真實數字驅動（不再前端自算）。新增一塊「GT 三分」卡：anchor 40 張（每輪量漂移）／sealed-test 80 張掛鎖徽章「整個 run 家族只開封一次」／unused。selfcheck 以 PASS/FAIL 列表顯示（nc 與 class_table 相符、兩目錄實體存在、YOLO 行格式合法、train∩valid 無近重複），**任一 FAIL 訓練鈕保持 disabled**。旁邊明示「data.yaml 只有 train / valid，test 不在裡面」。

### `s05` 05 抓模型並自己決定最好的
**擁有者**：`training-engineer`

**事件**

- `model.candidates {list}`
- `model.weights.fetched {name,bytes,sha256,mps_ok}`
- `probe.epoch {name,epoch,map5095}`
- `model.selected {name,score,why}`

**前端看到什麼**

候選看板表格（只有 yolov8n / yolov8s 兩列，rtdetr 刻意不在池裡並標明「MPS 上會靜默 fallback 到 CPU」）：每列 mAP50-95@probe **附 CI 誤差線** / ms per image / 峰值記憶體 / 預估全訓分鐘（已乘本機 scale_factor）/ 分數。探針跑時該列數字即時跳動；選中列 coral 高亮，下方印評分函式與一行理由 —— 若兩者差距落在 CI 內，理由會直接寫「無顯著差異，選小的」，學生一眼看到「這裡的數字不可過度解讀」。

### `s06` 06 配方凍結與單一變因宣告
**擁有者**：`training-engineer`

**事件**

- `recipe.proposed {params,augment,rationale,eta_min}`
- `augment.decision {name,on,why}`
- `train.calibrated {scale_factor,probe_2ep_sec}`
- `recipe.frozen {recipe_id,diff_vs_last}`

**前端看到什麼**

既有訓練設定表單被後端回填並鎖住（旁邊有「我要手動覆寫」解鎖鈕）；augment 開關清單各附一行理由（`mosaic 關閉 — 會破壞 edge-ring 靠邊語意`、`rotate 開啟 — 晶圓無固定方向`）。最上方一條 coral 標語「本輪只改：imgsz 416 → 640」。ETA 顯示分鐘數並標明「已依本機 2-epoch 校準 ×1.0／×3.4」，不是抄講師 M4 Max 的數字。

### `s07` 07 訓練
**擁有者**：`training-engineer`

**事件**

- `train.start {run_id,pid,eta_min}`
- `train.epoch {epoch,total,box_loss,cls_loss,dfl_loss,map50,map5095,mem_mb,eta_s}`
- `train.warn {msg}`
- `train.done {best_pt,elapsed_s}`
- `train.cancelled`

**前端看到什麼**

log 區維持既有四行格式（`$ autocv train ...` / `dataset: N train / N valid` / `epoch  12/50  box_loss 1.234  mAP50 0.567` / `done → best.pt`），事件的 `text` 欄位直接印，現成渲染器一行不改；`#pbar` 用 epoch/total。新增即時雙軸折線（loss 與 mAP50-95）邊訓練邊長。**Stop 鈕首次出現**（前端原本沒有）。訓練中殺掉 server 再重開，靠 `?since=` 續流一條事件都不漏。

### `s08` 08 Evaluation + 圖表（含 verdict 與雜訊帶）
**擁有者**：`metric-auditor`

**事件**

- `eval.noise_floor {sigma,noise_floor,seeds}`
- `eval.leakage {overlap_ids,near_dup}`
- `eval.perclass {cls,ap50,recall,support,ci_lo,ci_hi}`
- `eval.verdict {verdict,checks,delta,significant,anchor_ok}`
- `charts.series {curves|pr|confusion|per_class}`

**前端看到什麼**

最上方一枚 verdict badge（trusted 綠 / suspect 黃 / invalid 紅），點開列出四項 check（洩漏、測試集統計力、anchor drift、overfit gap）；invalid 時整個「進化下一輪」路徑被擋住。圖表面板一次鋪四張純 inline SVG（訓練曲線並標飽和點、PR curve 每類一條、混淆矩陣 heatmap、per-class AP 含 CI 誤差線）。**全圖有一條 noise floor 灰帶，落在帶內的 Δ 直接灰掉標「雜訊」** —— 這是 81 張 val 上唯一誠實的顯示方式。support < 30 的類別數字自動加註「樣本不足，僅供提示」。

### `s09` 09 AI 用自然語言跟使用者討論怎麼改
**擁有者**：`experiment-arbiter`

**事件**

- `chat.user {text}`
- `arbiter.ask {metric,class,direction,forbid}`
- `expert.msg {expert,factor,direction,expected_delta,evidence_path,cost_min,status}`

**前端看到什麼**

右欄下半變成「專家討論」時間軸，四條顏色 lane（dataset / train / metric / arbiter）＋底部裁決卡，使用者輸入框在最下。arbiter 先回一行結構化 ask 與 forbid 讓人確認（3 秒沒動作自動採納，不擋流程）。**每條發言都掛一個可點的證據路徑**（`04-experiments/exp007/results.csv#L31`），點了會把對應圖表高亮；沒有證據的發言直接顯示 invalid 灰底，學生一眼看到「附和會被打掉」。頂端常駐 LLM 花費計數（calls / USD）。

### `s10` 10 專家再討論一輪 → 重 train → 進化
**擁有者**：`experiment-arbiter`

**事件**

- `round.opened {round_id,ask}`
- `expert.challenge {from,target,reason,alternative}`
- `arbiter.decision {one_factor,patch,reason,budget_left}`
- `provenance.check {plan_vs_actual_diff,provenance_ok}`
- `round.closed {next_run_id,rerun_from,reused_stages}`

**前端看到什麼**

挑戰以縮排回覆掛在被挑戰的發言下面（metric-auditor 的否決用紅色 veto 標記，且必須附「可接受的替代條件」否則顯示為無效票）。裁決卡印出 **patch JSON 本體**（`{"imgsz":[416,640]}`）與「為什麼不是別人的提案」。按同意後 stage 骨架只重新點亮受影響的階段，沒動的標「沿用上輪」並顯示「這輪 9 分鐘不是 20 分鐘」。跑完蓋一枚 provenance 徽章 —— 宣稱與實際不符的 run 直接灰掉標 invalid 並退回討論。頂端出現「第 3 輪 / 上限 6」與 best mAP50-95 的跨輪 sparkline。

### `s11` 11 AI 自我進化模式（無人參與，全程可見）
**擁有者**：`experiment-arbiter`

**事件**

- `autonomy.on {preset,max_rounds,gpu_min_cap,llm_calls_cap,usd_cap}`
- `autonomy.tick {round,gpu_min_used,llm_calls,cost_usd,best_map5095}`
- `stop {reason,best_run,best_pt}`
- `stage.failed {stage,detail}`

**前端看到什麼**

同一條討論時間軸繼續長（ask 由 arbiter 自己從最弱類別產生，發言人標「自動」），頂端多三樣：round N/6、**兩條 budget 進度條（GPU 37/60 分鐘、LLM 28/60 calls・USD 1.4/3.0）**、大顆 Stop。圖表每輪重畫並保留上一輪淡色殘影做對比。關掉瀏覽器不影響進化（事件都在 events.jsonl），回來先拉快照再 `?since=` 續流，或 `since=0` 從第一張圖全段回放（這也是課前預錄、上課播放的教具）。停止時顯示終局卡：停止理由 + 最佳 run + best.pt 路徑 + 「本次為 OFAT 搜尋，未探索交互作用」，並出現唯一一顆「開封 sealed test（只有一次）」按鈕。

---

## 傳輸層

**SSE 單向串流 + 普通 POST 上行，全系統只開一條匯流 endpoint**：`GET /api/v1/runs/{run_id}/events?since=<seq>`。

為什麼不是 WebSocket：上行需求極低（按鈕、一句話、Stop），為此維護雙向連線、心跳、重連狀態機不划算；`EventSource` 是瀏覽器原生（既有前端是 455 行單檔零依賴、無 build step），FastAPI 端一個 `StreamingResponse` 就結束，不必引 websockets 依賴。為什麼不是輪詢：per-epoch 指標與逐張 bbox 要 <1s 延遲且 log 一行不能漏，既有 `JOB_STORE + GET /status/{job_id}` 只有單一字串狀態，撐不起規格 1/8/11。

**一條流，不是六條**（這是 C 軸自我矛盾的地方，在這裡定案）：五位專家不各開 SSE。所有事件由 console-owner 這個**單一 writer** append 到 `runs/<run_id>/events.jsonl`，SSE handler 只是 tail 這個檔案 + 一個 in-process asyncio queue。理由：HTTP/1.1 同源 6 連線上限，多開兩個 tab 就撞頂；而且訓練是 subprocess（callback → append jsonl），跨 process 只有檔案這條路走得通 —— 順帶符合 repo「多 agent 走檔案系統不走 in-memory」的慣例。要單獨 debug 某位專家就 `jq 'select(.actor=="training-engineer")' events.jsonl`，不需要為此多開五支端點。

**長時間訓練與斷線重連**：
1. `id: <seq>` → `EventSource` 斷線自動帶 `Last-Event-ID`，server 從該 seq 之後 seek 續發；`?since=0` 全段回放。
2. 每 15 秒 `: ping` 註解行撐 proxy / 背景分頁節流；前端 45 秒無訊息主動 `close()` 重開（某些半開連線 EventSource 不會自己重連）。
3. 大量事件（`ds.image`、`test.image`）只在 SSE 送前 200 筆，之後改送游標；ring buffer 只保最後 2000 筆；重連時前端先打 REST 快照（`/datasets/{ds}/images?offset=`、`/eval/{run}/predictions?offset=`）再接串流 —— 早期縮圖絕不依賴 replay。
4. 事件冪等是契約要求（`ds.image` 依 id upsert、`train.epoch` 依 `(run_id,epoch)` upsert），因為重播邊界會重疊一筆。
5. 後進場或超長離線先 `GET /api/v1/runs/{run_id}` 拿狀態快照把畫面一次補到位，再帶 `last_seq` 續流，不用重播幾千筆去重建 UI。
6. 每個 stage 掛 watchdog，超時無新事件就發 `stage.failed`，前端把進度條轉紅 —— 否則「子行程被 OOM kill」會被顯示成「進度條卡住」。
7. 不依賴 SSE 的退路：`GET /api/v1/runs/{id}` 與 `GET /api/v1/runs` 是純快照，SSE 全掛也能重新整理看到現況。

**uvicorn 的坑（必寫）**：`uv run uvicorn src.app.main:app --reload --reload-dir src`。`events.jsonl` 寫在專案樹內，預設 `--reload` 會每寫一筆事件就重啟一次並殺掉 driver，逐張串流時是每秒數次的 reload 迴圈。

---

## 事件信封（唯一真相・改要走批准）

**唯一真相，由 console-owner 凍結（改要走批准）。所有專家只准發自己 `actor` 前綴的 `type`。**

落檔（`runs/<run_id>/events.jsonl`，append-only、單 writer）每行一包：
```json
{"v":1,"seq":1042,"ts":"2026-09-11T04:12:33.412Z","run_id":"r7","stage":"s07",
 "type":"train.epoch","actor":"training-engineer",
 "data":{"epoch":12,"total":50,"box_loss":1.234,"cls_loss":0.88,"dfl_loss":1.02,
         "map50":0.567,"map5095":0.331,"mem_mb":3120,"eta_s":412},
 "text":"epoch  12/50  box_loss 1.234  mAP50 0.567"}
```
欄位鎖定：`v`(int, 目前 1) / `seq`(單調遞增 int，全 run 唯一) / `ts`(ISO8601 UTC) / `run_id` / `stage`(`s01`..`s11`) / `type`(`<域>.<動作>`) / `actor`(專家 slug) / `data`(物件，型別隨 type) / `text`(可選，已格式化好的一行字串，前端 `#log` 現有渲染器直接印，不用再拼)。

SSE 線上框（`GET /api/v1/runs/{run_id}/events?since=<seq>`）：
```
id: 1042
event: train.epoch
data: {"v":1,"seq":1042,...整包同上...}

```
- `id:` = `seq`，所以 `EventSource` 斷線自動帶 `Last-Event-ID` 回來，server 從該 seq+1 seek 續發；`since=0` 全段回放。
- 前端統一 `es.onmessage` → `const ev = JSON.parse(e.data)` → `switch(ev.type)`；不要用 `addEventListener(type)` 綁 20 種（新增 type 就漏接）。
- 每 15 秒發一行 `: ping` 註解撐 proxy / 背景分頁節流；前端 45 秒沒收到任何東西就主動 `close()` 重開（某些半開連線 EventSource 不會自己重連）。
- **冪等是契約要求**：任何事件重放兩次畫面不得壞。`ds.image` 依 `data.id` upsert（不是 push）、`train.epoch` 依 `(run_id,epoch)` upsert。重播邊界會重疊一筆。
- **大量事件不靠 SSE 補齊**：`ds.image` / `test.image` 只在 SSE 送前 200 筆，之後改送 `ds.progress {loaded,total}` 游標；ring buffer 只保最後 2000 筆。重連時前端先打 REST 快照（`/datasets/{ds}/images?offset=`、`/eval/{run}/predictions?offset=`）再接串流 —— 早期縮圖絕不依賴 replay。
- **一條連線**：五位專家不各開 SSE（HTTP/1.1 同源 6 連線上限，多開兩個 tab 就撞頂）。全部 append 到同一份 events.jsonl，console-owner 的單一 endpoint tail 它，UI 用 `actor` 分流到四條討論 lane。

`type` 命名空間（前綴即擁有者）：`ds.* label.* class.*`→dataset-truth；`model.* probe.* train.* sweep.*`→training-engineer；`eval.* test.* charts.*`→metric-auditor；`chat.* expert.* round.* autonomy.* stop`→experiment-arbiter；`run.* stage.* provenance.*`→console-owner。

---

## 討論迴圈

六步，每步都有對應 endpoint 與事件，不是概念圖。**執行體寫死**：五位專家在執行期 = 五份 system prompt 檔（`_Context/prompts/<slug>.md`）+ `claude -p --output-format stream-json` subprocess，由 arbiter 平行 spawn。`.claude/agents/*.md` 是**開發期的作者身分**，HTTP 叫不起來（wafer WALKTHROUGH 行 519 已自承），這條曖昧必須講白，否則學生會以為 subagent 能被 HTTP 呼叫。

**1. 翻譯（arbiter）**：使用者打「scratch 這類老是抓不到，別再加 epoch 了」→ `POST /api/v1/chat`。arbiter 翻成 `{ask:{metric:"per_class_recall", class:"scratch", direction:"up"}, forbid:["epochs↑"]}`。`forbid` 是硬約束不是語氣 —— 否則下一輪還是有人提加 epoch。前端回顯一行讓人確認，3 秒沒動作自動採納（不擋流程）。

**2. 平行提案（沿用 stock-groups 的平行→收斂型態，不另發明）**：arbiter 把同一份 context（`ask` + `forbid` + runs.jsonl 最後 K 筆 + 上輪 verdict + charts 摘要）平行發給四位（console-owner 不參與，它刻意不懂 CV）。每位只准回**一份、100 字內**的結構化提案：`{factor, direction, expected_delta, evidence_path, cost_min}`。硬規定：**證據必須是可點開的檔案路徑加行號**（`04-experiments/exp007/results.csv#L31`、`datasets/ds3/report.json#small_object_pct`），沒有證據的提案直接判 `invalid` 不進池。典型四份：dataset-truth「scratch 群的 PCA 擴框係數太緊，對 anchor GT 的 box 平均小 18%，改 1.3×」／training-engineer「imgsz 416→640」／metric-auditor「scratch 在 val 只有 11 個實例，CI 寬 ±0.21，先補資料再談」／（第四位是 arbiter 自己不提案，改由 training-engineer 額外提一個 backbone 方向，但一輪只能出一個變因）。

**3. 挑戰回合（硬上限 1 輪）**：四位互看提案，各可提一條 `challenge {target, reason}`。**metric-auditor 的挑戰帶否決權** —— 它宣告 invalid 的 run，任何人不得拿來當證據。否決**必須附「可接受的替代條件」**，未附即無效票（防守門員擺爛式否決）。

**4. 裁決（OFAT）**：arbiter 從存活提案挑出**唯一一個變因**，排序鍵 `expected_delta / cost_min`；剔除「過去 3 輪已試過同變因同方向且 Δ 未超過 CI 寬度」的提案 —— **去重比對凍結後 recipe diff 的實際鍵名，不是自然語言描述**（封死「同一個變因換個名字再提」）。違反 `forbid` 的直接丟掉。輸出就是一個 patch：`{"imgsz":[416,640]}` 或 `{"autolabel.scratch.expand":[1.0,1.3]}`。

**5. 落地 + 最小重跑**：patch 套到上輪凍結的 recipe/profile → 寫 `04-experiments/exp00N/recipe.json`（`parent_run` 指上一輪）→ `rerun_from` 由 patch 的最上游階段決定：只動 train.* → 從 s07；動 autolabel.* → 從 s03（重標 + 重 freeze）；動 split.seed → 從 s04。前端把沒動的 stage 標「沿用上輪」並顯示「這輪 9 分鐘不是 20 分鐘」。**跑完 console-owner 做 provenance 比對**：實際 run args / ds_version / label_version / class_table_version 與 change_plan 逐欄比對，不符就標 `invalid` 且不得進入任何比較，自動退回討論。這是「log 寫了改但實際沒改」的唯一捕手。

**⚠️ patch 打在上游時的可比性保護**：重標只作用在 **train split**；`valid` 的標註在 s04 凍結後全程不變，`anchor` 與 `sealed-test` 永遠是 Roboflow GT。所以第 N 輪與第 N+1 輪永遠對著同一把尺量 —— 這是第一名原設計最大的漏洞（重標會改寫 valid/test 的 GT 讓跨輪 mAP 不可比），在這裡被結構性補掉。

**6. 回答原問題並寫記憶**：新一輪 eval 出來後 arbiter 產一句話回覆 ask（「scratch recall 0.61 → 0.74，CI 仍寬，下一輪建議補標而非再調參」），append 一筆 `runs.jsonl`，同時 render 人讀版 `experiments.md`（沿用既有 schema，不另發明）。使用者可在同一個輸入框接話開下一輪。

---

## 自我進化模式

開關：`POST /api/v1/autonomy {run_id, on:true, preset:"teaching"|"real"}`。自主模式與有人模式**走同一條程式路徑**，唯一差別是 ask 由誰產生、以及不等人按「同意」（沒有第二套 pipeline，這是刻意的教學要求）。

ask 自動生成：arbiter 每輪把「使用者那一格」換成 metric-auditor 的 `weakest_class` + `main_metric`，永遠鎖定最弱那一項，不亂調。

**三軸硬預算（缺一不可，LLM 那一軸是評審最常挑漏的）**
| 軸 | teaching preset | real preset | 觸頂行為 |
|---|---|---|---|
| 輪數 | 3 | 6（人可手動加到 12，寫死上限） | 停，寫 stop reason |
| GPU wall-clock | 12 分鐘 | 60 分鐘 | 立刻 SIGTERM 當前訓練，不等它跑完 |
| LLM 呼叫 | 24 calls | 60 calls | 停；每輪固定 7 calls（4 提案 + 1 挑戰 + 1 裁決 + 1 回覆），所以次數可預估 |
| LLM 花費 | USD 0.5 | USD 3 | 停（由 claude -p 的 usage 欄位累加，寫進 runs.jsonl 的 `cost_usd`） |

GPU 預算用 **wall-clock 實測**記帳，不是累加預估值；開跑前先檢查「剩餘分鐘 ≥ ETA×1.2」，不足直接收斂。每個訓練 subprocess 帶 `ETA×2` 硬 timeout。

**人工 GO 閘門的明文裁決**：既有 `training-runner.md` 規定訓練前要停下等使用者回 GO。手動單次訓練**保留 GO 不變**；一鍵與自主模式**豁免 GO**，改用三軸預算 + `ETA×2` timeout + 前端常駐 Stop 鈕（收到 `train.cancelled` 才算停成功）取代。豁免寫進 `_Context/api-contract.md` 條文，不是默默跳過。

**前端全程看得到，且不靠前端在場**：事件不因為沒有訂閱者就不產生。`events.jsonl` 是 append-only，關掉瀏覽器進化照跑；回來先 `GET /api/v1/runs/{id}` 拿快照補畫面、再帶 `since=<last_seq>` 續流；`since=0` 就是從第一張圖全段回放（這順手是教具：課前跑一份 events.jsonl，上課播放，不用現場等 60 分鐘）。

**重啟不自動續跑**：autonomy 狀態雖然存在 `runs/<run_id>/state.json`，server 或 driver 重啟後一律歸零，必須人重新開 —— 避免 crash-loop 在夜裡反覆自動開訓。

---

## 收斂與放棄條件

停止條件全部由 **metric-auditor 判定**（提案者無權宣告自己進步），arbiter 只執行。每次停止都必發一條 `stop` 事件並在前端顯示終局卡（停止理由 + best run + best.pt 路徑 + 「本次為 OFAT 搜尋，未探索交互作用」）。

**主指標**：`val mAP50-95`（不是 mAP50 —— 實測 mAP50 已 0.977 頂天沒有資訊量）。比較基準是 **noise floor**（同 config × 3 seed 的標準差 × 2，由 metric-auditor 實測；實測 log 顯示 81 張 val 的 mAP50-95 在 0.275–0.611 之間跳，這個數字不量出來，一切判定都是假的）。

**算成功（converged）** —— 任一成立：
1. `plateau_ok`：連續 2 輪 Δ 落在 noise floor 內，**且** verdict = trusted，**且** anchor gate 通過，**且** 最弱類別 recall ≥ 0.70。
2. `target_met`：val mAP50-95 ≥ baseline + 0.08，且 bootstrap 95% CI 下界仍高於 baseline。
3. `proposal_exhausted`：提案池空（所有變因方向都被試過，或被判重複/無證據）。

**算放棄（abandoned，不准再繞）** —— 任一成立：
1. `search_exhausted`：連續 3 輪 Δ 都在 noise floor 內且未達標。
2. `untrustworthy_metrics`：連續 2 輪 verdict = invalid（洩漏 / 統計力不足）→ 停止並要求補資料，不准再訓練（繼續訓練只是在優化假指標）。
3. `anchor_divergence`：**val 主指標上升但 anchor recall 下降** → 立刻停，不給第二次機會。這是 pseudo-label 自欺的教科書訊號。
4. `label_ceiling`：auto-label 對 anchor GT 的 IoU 中位數 < 0.4 → 停在 s03，宣告「問題在標註不在模型」，再換架構或調參都無效。
5. `stage_failing`：同一 stage 連續 2 次 error（OOM 退避後仍失敗）→ 把該旋鈕列黑名單；黑名單吃掉所有可動旋鈕就停。
6. `deadlock`：連續 2 輪零提案通過（互相否決）→ 停（無效輪不計入輪數，但計入 deadlock）。

**硬上限（不管達不達標）**：輪數（teaching 3 / real 6，人可加到 12，寫死）、GPU 60 分鐘 wall-clock、LLM 60 calls / USD 3。單一提案的 `cost_min` 超過剩餘預算一半就不准套用（避免最後一輪燒光還沒跑完）。

**反刷分**：Δ 的比較基準是 CI 寬度不是點估計；落在 CI 內一律計入「連續無提升」計數。主指標定義欄位在 change_plan 裡標 `locked`，任何一輪都不准改（防「換個 metric 就贏了」）。

**最終 test 只在停止之後跑一次**：`POST /api/v1/final-test` 在沒有 `stop` 事件時回 409。跑完就寫入 runs.jsonl 並標 `final:true`，之後不可再跑。沒有「無限進化」這個選項 —— `autonomy {on:true}` 不給上限直接回 400。

---

## 護欄（結構性保證，不靠自律）

1. **test set 絕不參與調參 —— 結構性保證，四道鎖，不靠自律**：(1) sealed test 的影像與標註放在 `03-sealed-test/`，**完全不出現在 data.yaml 裡**（data.yaml 只有 train / valid），Ultralytics 在物理上讀不到它；(2) `POST /api/v1/eval` 的 `split` 參數只接受 `valid` 與 `anchor`，傳 `test` 回 400；(3) 唯一會讀它的是 `POST /api/v1/final-test`，而它要求 body 帶 `stop_event_seq` —— 沒有 stop 事件（未 converged/abandoned）回 409，同一個 ds_version 只准成功一次，第二次 409；(4) arbiter 的裁決函式（`src/evolve/ofat.py`，純函式可 assert）**只接受 `metrics.val` 當排序鍵**，runs.jsonl 中 `final:true` 的紀錄被明確排除在證據池之外。這四道是程式碼層面的，不是紀律層面的。

2. **pseudo-label 自欺的雙層防線**：auto-label 只作用在 **train split**；`valid` 的標註在 s04 凍結後全程不變（patch 打在上游重標時，**valid 不重標**）—— 所以第 N 輪與第 N+1 輪永遠對著同一把尺量 mAP，跨輪可比。`anchor`(40 張 Roboflow 人工 GT) 每輪評一次，但**只當 gate 不當 rank**：arbiter 只讀 `anchor_ok: bool`，不能把 anchor recall 當最佳化目標。`val 主指標上升但 anchor recall 下降` → 立刻 abandoned，不給第二次機會。

3. **進步的門檻是 CI 寬度不是點估計**：metric-auditor 必須先量 noise floor（同 config × 3 seed），沒量出來，arbiter 的裁決與所有停止條件回 409 拒絕執行。實測 log 顯示 81 張 val 的 mAP50-95 在 0.275–0.611 之間跳，這不先量就全是自欺。Δ 落在 noise floor 內一律記「試過、沒用」並計入連續無提升計數。`support < 30` 的類別指標只能當提示，不得作為停止或達標依據。

4. **教學 baseline 刻意調弱**：起點 `yolov8n / 10 epochs / imgsz 320`，主指標用 `mAP50-95`（不是已經 0.977 頂天的 mAP50）。從強 baseline 起跑，每輪都會判 no_gain，台上看到的「自我進化」會是三輪「沒有進步，請回去補資料」—— 科學正確但 demo 全死。這一條連同「為什麼 0.977 不適合當進化起點」一起寫進教材當教學點，不是隱形前提。

5. **ETA 必須本機校準**：`8.89 分鐘 / 50 epochs` 是 M4 Max 的錨。每個 run 開跑前先跑 2 epochs 得 `scale_factor` 寫進 runs.jsonl，所有預估乘上去。否則 M1 Air 的學生第一輪就被預算閘門判「超支不起跑」。

6. **訓練不阻塞 API，狀態在檔案，重啟接得回**：`subprocess.Popen` 跑 `scripts/train_yolo.py`；per-epoch 指標由子行程內的 Ultralytics `on_fit_epoch_end` callback 直接 append `events.jsonl`（不 parse stdout —— 實測 log 帶 ANSI 與 \r）；cancel = `SIGTERM(pid)`；pid 與狀態在 `runs/<run_id>/state.json` + `runs.jsonl`。API 重啟掃 `status=running` 的 run，`os.kill(pid,0)` 判活：活著重新 tail、死了標 `crashed` 並推 `stage.failed`（否則前端把子行程死掉顯示成進度條卡住）。uvicorn 必須 `--reload-dir src`（events.jsonl 在 runs/，預設 --reload 會每寫一筆就重啟並殺掉 driver）。driver 是獨立 process，**不是 BackgroundTask**。

7. **provenance：討論說要改的，跟實際跑的，逐欄比對**。console-owner 比對 change_plan 與實際 run args / ds_version / label_version / class_table_version，不符就標 `invalid` 且不得進入任何比較，自動退回討論。這是「log 寫了已套用但實際沒改」的唯一捕手，而且比對是機械的，由沒有 ML 立場的人做。

8. **class_table_version 綁在每個 run 上，跨版本比較直接拒答並在前端標「不可比」**。這順手宣告既有 `mAP50 0.977 / nc=1 Donut` 那組 baseline 在新 class table 下作廢，而不是偷偷沿用。`valid` vs `val`、names list vs dict、path 相對 vs 絕對三處衝突由 dataset-truth 一次裁決（前端形狀勝），`prepare_dataset.py` 改寫對齊。

9. **否決必須附「可接受的替代條件」，未附即無效票**；連續 2 輪零提案通過（互相否決）→ deadlock 停。提案去重比對凍結後 recipe diff 的**實際鍵名**，不是自然語言描述。

10. **安全邊界**：後端預設 `bind 127.0.0.1`，`/api/v1/runs` 檢查 Host header 非 localhost 回 403；`ROBOFLOW_API_KEY` 只在 server 讀 `.env`（沿用既有變數名不改名），圖片一律走自家 proxy，前端不再持 key；repo 只留 `.env.example`，既有 `guard-secrets.sh` hook 擋寫入。README 第一行寫「不可部署到公開網址」—— 自主模式無人看管 + 公開網址 = 任何人都能替你燒 GPU 與 token。

11. **每個非 trivial 邏輯留一個能跑的檢查**（沿用前端 `?selftest=1` 那 9 條的慣例）：`scripts/selfcheck.py` 三條 assert —— events.jsonl 完整讀 == 斷線後 `since=` 續讀逐筆相同；Python 分層抽樣 == 前端 `stratifiedSplit` 逐筆相同（同 seed）；auto-bbox 對合成 GT 的 IoU 中位數 ≥ 0.6。另加 `tests/test_ofat.py`（給定五份提案與預算，輸出同一個 patch）與 `tests/test_ci.py`。

---

## 開工順序

1. M0 契約閘門（半天，五人一起，不准平行開工前）：console-owner 凍結 `_Context/api-contract.md` 🔒（eventEnvelope、stage 代號、REST 形狀、錯誤碼）+ `_Context/team-roles.md`（角色 × 可寫目錄，刻意不重疊）+ 空的 FastAPI app 骨架（五個 router 掛進同一個 app、`uv run uvicorn src.app.main:app --reload --reload-dir src`）。dataset-truth 同時凍 `class_table.json` 的 schema（值待 M2 填）。此閘門不過，不准 spawn 任何人。

2. ★M1 第一個可驗收里程碑（1–2 天，只要 console-owner + dataset-truth，零 GPU / 零 Roboflow / 零 LLM）：`scripts/gen_demo_wafers.py` 產 120 張真點陣 wafer PNG + 免費精確 GT → 按前端那顆按鈕 → 縮圖牆逐張 append 長出來 → 每張即時疊出 connected-component bbox。驗收條件：`uv run python scripts/selfcheck.py` 三條 assert 全綠（events.jsonl 完整讀 == 斷線續讀、Python 分層抽樣 == 前端 stratifiedSplit、auto-bbox 對合成 GT 的 IoU 中位數 ≥ 0.6），全程 < 90 秒跑完。這一步就已經覆蓋規格 1+2+3 的骨幹，可以直接上台 demo。

3. M2 class 真相凍結（1 天，dataset-truth）：幾何描述子 → KMeans（silhouette 自選 k∈[3,6]）→ 每群 montage → 一次 LLM 命名 → 寫 `class_table.json` v1。同時把 Roboflow 409 張 GT 切成 `anchor`(40) / `sealed-test`(80) / `unused`，`prepare_dataset.py` 的 `write_data_yaml()` 改寫成前端形狀（`valid` 目錄、`names` 用 list、相對 path），data.yaml **只寫 train/valid**。驗收：`/datasets/{ds}/freeze` 的 selfcheck 全 PASS，FAIL 時前端訓練鈕 disabled。

4. M3 訓練不阻塞 + 真 per-epoch 串流（1–2 天，training-engineer + console-owner）：subprocess.Popen 跑 `scripts/train_yolo.py`，子行程內掛 Ultralytics `on_fit_epoch_end` 直接 append events.jsonl（不 parse stdout）；cancel = SIGTERM(pid)；API 重啟掃 runs.jsonl 的 running run 用 `os.kill(pid,0)` 認領或標 crashed。前端 #pbar / #log 四行格式不改渲染器就動起來，Stop 鈕首次出現。驗收：訓練中殺掉 uvicorn 再重開，前端 `since=` 續流一條事件都不漏；按 Stop 收到 train.cancelled。

5. M4 noise floor + verdict + 圖表（2 天，metric-auditor + console-owner）：先做最無聊也最重要的那一步 —— 同 config × 3 seed 量 noise floor（實測 log 顯示 81 張 val 的 mAP50-95 在 0.275–0.611 之間跳，這個數字一定要先量出來，否則後面所有 Δ 判定都是假的）。然後 bootstrap CI、per-class、anchor gate、四張 inline SVG 圖（訓練曲線 / PR / 混淆矩陣 / per-class AP + CI 誤差線 + noise floor 灰帶）。驗收：落在灰帶內的 Δ 在前端灰掉標「雜訊」。

6. M5 一鍵端到端（0.5 天，console-owner）：`POST /api/v1/runs {mode:"oneshot"}` 串起 M1–M4；`scripts/run_all.py` 呼叫**同一個函式**（不是第二套邏輯）。驗收：teaching preset 一顆按鈕 5 分鐘內看完 stage s01→s08。

7. M6 討論迴圈（2–3 天，experiment-arbiter）：`claude -p` subprocess × 5 份 prompt 檔、提案 schema + 證據路徑強制、挑戰回合、OFAT 裁決、runs.jsonl 去重（比對 recipe diff 鍵名）、`rerun_from` 最小重跑、provenance 比對（plan vs actual 不符標 invalid 且不得當證據）。驗收：使用者打一句「scratch 抓不到」→ 看到四份帶檔案行號的提案 + 一張裁決卡印出 patch JSON → 按同意 → 只重跑受影響 stage。

8. M7 自主模式 + 停止條件（1 天，arbiter + console-owner）：三軸預算記帳、autonomy.tick、終局卡（停止理由 + best run + best.pt 路徑 + 「本次為 OFAT 搜尋，未探索交互作用」）、`POST /api/v1/final-test`（只有在 stop 事件之後才不回 409）。驗收：teaching preset 跑滿 3 輪自動停，前端 timeline 有三個徽章與一條跨輪 sparkline。

9. M8 Roboflow 真資料 + 教材（1 天）：key 搬到 server env、圖片走自家 proxy、`agent_group_projects/README.md` 補一列、`docs/walkthroughs/cv_self_evolving_walkthrough.md` 走 walkthrough-style。

---

## 刻意不做的事與理由

1. **SAM / GroundingDINO / OWL-ViT 等 zero-shot 路線**。wafer map 是分佈外輸入（不是自然影像），文字 prompt「scratch」「donut pattern」在上面沒有意義；SAM 只切遮罩不給類別，等於還是要另一套命名機制。GB 級權重、教室沒網路就掛。連通分量 + 幾何描述子 + KMeans 用既有 `uv.lock` 的 scipy/sklearn 就能跑，零新依賴。

2. **rtdetr-l 與 yolov8m 以上的候選**。MPS 上 deformable attention 會靜默走 `PYTORCH_ENABLE_MPS_FALLBACK` 掉回 CPU，慢 10–50 倍，會把整個 budget 帳算爛；286 張餵 m 級容量幾乎保證 overfit。候選只留 n / s 兩個，而且差距落在 CI 內就選小的。

3. **Optuna**。用 Ultralytics 內建 `model.tune` 或乾脆只做 2-factor 小 sweep（trials ≤ 4）。多一個依賴、多一套 study 儲存，換來的是在 81 張 val 上量不出來的精細搜尋。

4. **WebSocket**。上行只有四顆按鈕，雙向能力用不到，卻要自己做心跳、重連、序號補齊，而且沒有 `Last-Event-ID` 這種現成重播語意。

5. **六支 stage-level 的 `/{stage}/events` SSE 端點**。作者自己說「前端不開，給 curl debug 用」—— 那就是 `jq 'select(.actor==...)' events.jsonl`，六支不被使用的端點是純膨脹，還會讓 seq 單調與重播語意分裂成六份。

6. **外部圖表庫（Chart.js / D3 / Plotly）**。既有前端是單檔零依賴、CSP 只允許幾個 CDN，圖表配色又必須只取 `--ds-*` token。四張圖用 inline SVG 自畫（曲線、PR、heatmap、橫條 + 誤差線），寫一次就結束，而且能 hover、能跨輪疊圖、能留 noise floor 灰帶。

7. **人工畫框 UI**。信任錨直接用 Roboflow 那 409 張現成人工 GT（anchor 40 + sealed-test 80），不發明手工步驟 —— 這同時消掉「30 張 human held-out 沒有生產者」這個原設計最容易被跳過的保險絲。低信心圖只做「送審清單」標記，不做編輯器。

8. **多 run 併發 / GPU 佇列深度 > 1**。一台 Mac 一張 GPU，深度 1 的單槽就是正解。想同時看兩個 run 要 HTTP/2，這一版不做。

9. **session / 使用者系統 / 公開部署**。只 bind 127.0.0.1，非 localhost 的 Host header 回 403。教學專案的威脅模型是「不小心 --host 0.0.0.0 把 key 和 GPU 一起送出去」，最便宜的解法是不給這個選項。

10. **per-expert 微服務 / 多 process / 多埠號**。五位專家寫的是**同一個 FastAPI app 的五個 router**，目錄擁有範圍不重疊但共用一個 process（訓練除外，那是 subprocess）。六個 process 會帶來 CORS、埠號漂移、事件 writer 競爭三類 bug，換不到任何好處。

11. **交互作用的完整探索**。OFAT 每輪只改一個變因，換來的是可歸因；代價是 imgsz × augment、backbone × lr 這類交互作用可能永遠找不到。緩解只到「每 3 輪可申請一次 2-factor sweep（trials ≤ 4）」，**終局卡必須註明「本次為 OFAT 搜尋，未探索交互作用」** —— 把方法論的已知盲點寫進成品輸出，而不是讓 UI 很有說服力地宣告收斂。

---

## 現況盤點（必須接手，不要另起一套）

### 既有 agent

| 名稱 | 職責 | 路徑 |
|---|---|---|
| data-hunter | 抓資料：從 Roboflow Universe 下載 WM-811K（workspace `wm811k-paasr` / project `wm811k` / version 3 / format `yolov8`）到 `Projects/2026-001-mvp/01-raw-data/`。讀專案根 `.env` 的 `ROBOFLOW_API_KEY`，用 uv 裝 roboflow + python-dotenv。tools: Read, Write, Edit, Bash, Glob | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-detection/.claude/agents/data-hunter.md` |
| bbox-labeler | 標註驗證 + dataset 整理：驗證 YOLO `.txt` 每行 `class_id cx cy w h`（class_id 非負整數、cx/cy/w/h 在 [0,1]），把 `01-raw-data/` 重組成 `02-dataset/{images,labels}/{train,val,test}` + 產 `data.yaml`。明文規定「**不要重新生成 bbox**，Roboflow 已有 bbox 直接用」——這點和新規格要的 auto-labeling 相反。tools: Read, Write, Edit, Bash, Glob, Grep | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-detection/.claude/agents/bbox-labeler.md` |
| training-runner | 訓練：`yolov8n.pt` + `02-dataset/data.yaml`，預設 epochs=50 / batch=8 / imgsz=416 / device=`mps` / project=`04-experiments` / name=`exp001`。內建三段式流程：環境檢查 → **預估時間並停下來等使用者回 GO**（human gate）→ 訓練 → 回報 best.pt + mAP。錯誤處理寫了 `PYTORCH_ENABLE_MPS_FALLBACK=1`、OOM 改 batch=4、中斷用 `resume=True`。tools: Read, Write, Edit, Bash, Glob | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-detection/.claude/agents/training-runner.md` |
| inference-runner | 推論 + 評估 + 視覺化：用 `04-experiments/exp001/weights/best.pt` 對 test set 隨機抽 10 張（`random.seed(42)`）畫 bbox/class/confidence 存 `05-inference/pred_<name>.png`，再跑 `model.val(split="test")` 印 `metrics.box.map50` / `metrics.box.map`，最後寫 `05-inference/summary.md`。tools: Read, Write, Edit, Bash, Glob | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-detection/.claude/agents/inference-runner.md` |
| evaluator（只有設計稿，無 agent 檔案） | 讀 `04-experiments/exp001/results.csv` + confusion matrix → 寫 `evaluation.md`（訓練曲線健康度 / overfit 偵測 / 失敗類別 / 3 條下一輪具體建議）。骨架 frontmatter 與職責已寫在 WALKTHROUGH Phase 2（行 351-396），但 `.claude/agents/evaluator.md` **不存在**。這正是新規格第 9 項「AI 用自然語言討論怎麼改」的現成骨架。 | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-detection/WALKTHROUGH.md#L351-396` |
| hyper-tuner（只有設計稿，無 agent 檔案） | 讀 evaluator 的 `evaluation.md` → 生 search space（lr ∈ [1e-4,1e-2]、imgsz ∈ {416,640}）→ 跑 N 個 trial（每個 10 epochs）→ 輸出 `best_params.json`，每個 trial 寫進 `experiments.md`。已寫兩種實作法：Ultralytics `model.tune(iterations=10)` 或 Optuna。規定跑 HPO 前必須使用者確認。WALKTHROUGH 行 398-458。 | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-detection/WALKTHROUGH.md#L398-458` |
| orchestrator（只有概念圖，無 agent 檔案） | 自我改進迴圈的大腦：讀 `experiments.md`（唯一真相來源 / 記憶）決定下一步 action ∈ {train, tune, audit, stop}，迴圈驅動其他 agent。WALKTHROUGH 行 460-530 有 ASCII 架構圖、`experiments.md` 記憶 schema（exp001: params / mAP@0.5 / 觀察 / 下一步）、`while not converged:` 偽碼，並明確指出「Claude Code 還沒原生支援 agent-call-agent，需要外部 driver script 或 workflow tool」+ 「orchestrator 要有 budget cap」。這就是新規格第 10/11 項（自我進化模式）的設計來源。 | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-detection/WALKTHROUGH.md#L460-530` |
| wafer-agents-detection-demo 的同名 4 支 | `/agents` 建 agent 的 demo 版，agent 檔名與完成版相同（data-hunter / bbox-labeler / training-runner / inference-runner），用途是課堂示範建 agent、不真的跑訓練。 | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-agents-detection-demo/.claude/agents/` |
| stock-groups-skills 4 支（平行討論的既有範式） | fundamentals-analyst / technicals-analyst / news-sentiment-analyst 平行研究同一標的 + research-synthesizer 收斂。這是 repo 裡唯一「多專家平行 → 彙整」的既有實作（新規格第 10 項『subagent 專家再討論一次』可直接沿用這個型態，不用重新發明）。 | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/stock-groups-skills/.claude/agents/` |
| computer-vision-wafer-template/.claude/agents/ | 空目錄（學生練習版，agent 由學生自己建）。 | `/Users/awesomeartengineer01/claude-new-course/agent_group_projects/computer-vision-wafer-template/.claude/agents/` |

### 可重用資產

- 前端成品（單檔零依賴，455 行，可直接當新系統的 UI 殼）：`/Users/awesomeartengineer01/claude-design_claude-code/prototype/index.html` — 實際路徑 `/Users/awesomeartengineer01/claude-new-course/claude-design_claude-code/prototype/index.html`。已有 3 欄 grid（270px / 1fr / 320px）、header device 指示、Roboflow 載圖、篩選（檔名/類別/split）、縮圖牆（shift 範圍多選、鍵盤 1/2/3/0 指派）、split 統計 + 比例條 + health 警示、分層抽樣、訓練設定表單、log 區 + progress bar、splits.json / data.yaml 匯出、`?selftest=1` 9 條 assert。新系統只要把 `runTraining()` 的假 setInterval 換成真 API 串流即可。

- 設計 token 單一真相：`/Users/awesomeartengineer01/claude-new-course/claude-design_claude-code/design-system/tokens.css`（46 行）。變數名精確：`--ds-navy-900:#141B3D`（app 背景）、`--ds-navy-800:#1E2761`、`--ds-navy-700:#263173`、`--ds-coral:#D97757`、`--ds-coral-700:#C2603F`、`--ds-ink:#F2F4FB`、`--ds-ink-dim:#A7AEC8`、`--ds-ink-faint:#6B7399`、`--ds-line:#2E3768`；split 四語意色 `--ds-train:#4FB286` / `--ds-valid:#E3B341` / `--ds-test:#8B7BE8` / `--ds-unassigned:#5A628C`；狀態色 `--ds-ok/--ds-warn/--ds-err`（#4FB286/#E3B341/#E06C75）；間距 `--ds-1..--ds-6`（4/8/12/16/24/32）、圓角 `--ds-r-sm/--ds-r/--ds-r-lg`(6/10/16)、字級 `--ds-t-xs..--ds-t-2xl`(11/12/14/18/24/34)、字型 `--ds-font` / `--ds-mono: Menlo,...`、`--ds-shadow`。新增面板（metric 圖表、agent 討論 log）一律只能取這些變數。

- 三個 `@dsCard` 元件預覽（`/design-sync` 會索引第一行的 `@dsCard` 註解）：`design-system/colors.html`（group="Colors"）、`design-system/buttons.html`、`design-system/image-card.html`（group="Components"，CV 介面核心元件：縮圖 + split badge + class chips + 選取態）。

- Claude Design 畫布 artboard：`design-canvas/canvas.json`（artboards: Main.dc.html @1440x900 `is_interactive:true`、Components.dc.html @880x700；annotations brief / ds-note；`launch.view:"canvas"`）、`design-canvas/Main.dc.html`（`<x-dc>` + `<helmet>` + `sc-for list="{{images}}" as="item" hint-placeholder-count="8"` + `<script data-dc-script data-props='{"accent":{editor:"color"...},"density":{editor:"enum" comfortable|compact},"$preview":{width:1440,height:900}}'>` + `class Component extends DCLogic`，state = `{images, sel}`，方法 `seedImages/prng/stratify/assign/renderVals`）、`design-canvas/Components.dc.html`。新 artboard 照這個 x-dc/DCLogic 慣例加即可（注意：`./support.js` 由 canvas runtime 提供，本機目錄沒有這個檔）。

- 已匯出的自帶 runtime 畫布（2.5MB、11009 行，內嵌整份 files JSON）：`design-canvas/cv-self-training-console.html` — 可直接開給人看，不要手改。

- 分層抽樣邏輯（同一套寫了兩份，最大餘數法、mulberry32 PRNG、按主類別 `cls[0]` 分層、同 seed 同結果）：`prototype/index.html` 的 `stratifiedSplit(items, ratios, seed)` + `mulberry32(a)`；`design-canvas/Main.dc.html` 的 `stratify()` / `prng(a)`。新後端若要在 server 端切 split，照這套搬（或直接吃前端算好的 splits.json）以保證數字一致。

- 離線示範資料產生器：`prototype/index.html` 的 `loadDemo()` + `waferSVG(i,cls,rnd)`，48 張 SVG data-URI 合成晶圓圖、4 類別（scratch / particle / ring / edge-loss）、seed 固定 7。新系統「逐張串流進來」的 demo 模式可直接用它當資料源，不需網路與 Roboflow 帳號。

- 已驗證的 Roboflow API 事實（`claude-design_claude-code/WALKTHROUGH.md` Phase 5，行 326-392）：`POST https://api.roboflow.com/{workspace}/{project}/search?api_key=KEY`，body `{limit, offset, fields}`；`fields` 可選 `id name annotations labels split tags owner url embedding created`，**不指定預設只回 `["id","created"]`**（第一個坑）；回傳 `{offset, total, results[]}`，`results[].url` 直接是圖片 URL（別自己拼）、`results[].annotations = {count, classes:{類別:數量}}`；preflight 回 `access-control-allow-origin: *`，**瀏覽器可直打、不需要 proxy**；key 放前端只限 localhost。

- 真的能跑的訓練/資料腳本（pathlib、無硬編碼）：`agent_group_projects/computer-vision-wafer-detection/scripts/download_dataset.py`（含版本探測 fallback：v3 不存在改用 max(version_ids)）、`scripts/prepare_dataset.py`（label 驗證統計 `total_images/total_labels/empty_labels/total_bboxes/class_counter/invalid/missing_labels`、SEED=42、SPLIT_RATIO 70/20/10、最後寫 data.yaml）、`scripts/train_yolo.py`（已設 `PYTORCH_ENABLE_MPS_FALLBACK=1`、device="mps"、回報 best.pt 路徑與耗時）。

- 已驗證的真實訓練基準（`agent_group_projects/computer-vision-wafer-detection/training.log`）：Ultralytics 8.4.48 / torch 2.11.0 / MPS (Apple M4 Max)、286 train + 81 val、nc=1、yolov8n.pt、imgsz=416、batch=8、50 epochs = **8.89 分鐘**（0.143 hours），best.pt 驗證結果 P 0.952 / R 0.975 / mAP50 0.977 / mAP50-95 0.724，推論 32.2ms/image。前端進度條與時間預估可直接用這組數字校準。注意 log 內路徑仍是舊機器 `/Users/kevinluo/claude-code-complete-tutorial/...`。

- uv 專案骨架：`agent_group_projects/computer-vision-wafer-detection/pyproject.toml`（requires-python >=3.11；deps: ultralytics>=8.4.48, roboflow>=1.3.8, matplotlib, scikit-learn, python-dotenv）+ `uv.lock` 已存在。新後端加 fastapi/uvicorn 直接 `uv add`。

- FastAPI 後端的兩份現成範本（新系統的 API 層可照抄）：`Projects/Normal-RAG2Graph-Project-claude/backend/app/main.py`（CORS `allow_origins=["*"]`、`BackgroundTasks`、`JOB_STORE[job_id]` 狀態機 parsing→chunking→vectorizing→completed/failed、`GET /health`、`POST /api/v1/documents/upload` 回 `{job_id, doc_id}`、`GET /api/v1/status/{job_id}` 回 `{job_id, status}`），以及 `Projects/Normal-RAG2Graph-Project-claude/.claude/skills/fastapi-uv-setup/SKILL.md`（強制 `uv init --package` + `uv sync` + `uv run uvicorn app.main:app --reload`、禁 requirements.txt、禁 pip、build-system 用 hatchling）。

- 「專家各寫自己那塊 API，靠凍結契約接前端」的既有範式：`agent_teams/ios-app-flutter-dev/arxiv-1-完成版/_Context/api-contract.md`（team lead 凍結 🔒、每條 endpoint 附完整 JSON 範例與 404 行為、共用層介面簽章、改契約要走 mailbox 批准）+ `_Context/team-roles.md`（角色 × 擁有檔案範圍表、目錄刻意不重疊、三道階段閘門：研究閘門 → 契約/共用層閘門 → 開發閘門）+ 真的能跑的 `backend/app/`（arxiv/gemini/report/storage/models/config 分檔 + tests）。新系統的多專家分工直接沿用這個「先凍契約再平行」的順序。

- 自我改進迴圈的記憶 schema：`experiments.md`（每個 exp 記 params / mAP@0.5 / 觀察 / 下一步），定義在 wafer WALKTHROUGH 行 485-503。新系統的「自我進化模式」要有可讀回的記憶，這是既有 schema，別另發明。

- 教學骨架 skill：`.claude/skills/tutorial-scaffold/SKILL.md`（新專案固定起手式：README.md 給學生 / CLAUDE.md 給 AI / .gitignore / .env.example / pyproject.toml / src/ / _Context/lesson-flow.md，README 要有「學什麼 / 時長 / 產出」三行 header）。

- 既有 .env 樣板：`claude-design_claude-code/.env.example`（`ROBOFLOW_API_KEY` / `ROBOFLOW_WORKSPACE` / `ROBOFLOW_PROJECT`）與 wafer 專案的 `ROBOFLOW_API_KEY`（從專案根 .env 載入）。新後端沿用同名環境變數，不要改名。

### 硬規矩

- Python 套件一律 `uv`，**禁止 pip / requirements.txt**（root `.claude/skills/uv-first`、wafer 專案 `.claude/rule/uv-dependeies.md`、`fastapi-uv-setup` SKILL 都重複同一條）。

- PyTorch / ML 預設 `device="mps"` 不是 cuda，並設 `PYTORCH_ENABLE_MPS_FALLBACK=1`（root `.claude/skills/mac-mps-default`、`scripts/train_yolo.py` 開頭就 `os.environ.setdefault`）。前端 `#device` select 的第一個選項也是 mps。

- 所有路徑用 `pathlib.Path`，以 `PROJECT_ROOT = Path(__file__).resolve().parent...` 推導，**不硬編碼絕對路徑**（wafer CLAUDE.md 明列）。

- 訓練這種重工前要先預估時間、**停下來等使用者回 GO**（training-runner Step 2「必須等使用者明確說 GO/開始/繼續」；hyper-tuner 也規定 HPO 前要確認）。新系統的「按一顆按鈕跑完整條」要和這條 human-gate 慣例明確對齊或明確豁免。

- 語言：繁體中文（agent description、README、WALKTHROUGH、log 訊息、註解全繁中；`lang="zh-Hant"`）。

- commit：繁中、Kevin 個人風格、**不署名 Claude Code**、無 emoji（root `.claude/skills/commit-zh`）。

- 教學 .md 走 `walkthrough-style`：Phase 結構、emoji 標籤、講師金句、卡點對照表、配套教材 footer；新教學資料夾走 `tutorial-scaffold`（README/CLAUDE.md/.gitignore/.env.example/pyproject.toml/src/_Context）。

- code 字型一律 Menlo（`--ds-mono: Menlo, ui-monospace, "SF Mono", monospace`，tokens.css 註明「Kevin 規定」；PPT 也同一條）。

- 顏色不准在元件裡寫 hex，只能取 `--ds-*` token（canvas.json 的 ds-note 明講：train/valid/test/unassigned 四個語意色會同時出現在 badge、統計數字、比例條、類別分佈，所以必須是 token）。例外：`.dc.html` artboard 因為 canvas runtime 不吃外部 CSS，是硬碼同值 hex。

- 秘密：`.env` 進 `.gitignore`、repo 只留 `.env.example`；API key 只准 localhost + localStorage，**不可部署到公開網址**；repo root 有生效的 hook `guard-secrets.sh`（寫 .env/*.pem/credentials.json 會 exit 2 擋掉）與 `git-commit-secret-guard.sh`（commit 前掃敏感資料）。

- 非 trivial 邏輯要留一個能跑的檢查：前端 `?selftest=1` 9 條 assert（分層抽樣）、arxiv backend 有 `tests/`。新後端的 split / metric 計算要比照留 self-check。

- agent 檔案格式：`.claude/agents/<name>.md`，frontmatter 只有 `name` / `description`（繁中、寫明「當使用者要…時使用」）/ `tools`（逗號分隔明列，例 `Read, Write, Edit, Bash, Glob`），正文固定「任務 / 預設參數 / 執行步驟（Step 1..N）/ 程式碼範本 / 完成標準 / 錯誤處理」。

- 多 agent 專案的資料交換走**檔案系統**不走 in-memory（wafer WALKTHROUGH Q1：data-hunter 寫 `01-raw-data/`、bbox-labeler 讀它再寫 `02-dataset/`，這樣才能跨 session 接續）。編號資料夾流水線：`01-raw-data / 02-dataset / 04-experiments/<expNNN> / 05-inference`。

- 跨技術棧平行開發的順序是「研究 → 凍結規格與 API 契約 → 才 spawn 平行隊友 → 整合驗收」，且隊友擁有的目錄**不可重疊**，共同地基（契約、shared/）只有 team lead 能改（`agent_teams/.../_Context/team-roles.md` 三道閘門）。

- 教學專案分「完成版 / 模板 / demo 版」三型（agent_group_projects/README.md：completed 是答案、template 是考卷、demo 是示範題），新資料夾要在 `agent_group_projects/README.md` 的案例總覽表補一列。

- LibreOffice 預覽會炸 emoji（豆腐框 + 吃掉鄰近中文）→ PPT 一律用文字標記取代 emoji；PPT 只改 `build_ppt_完整版.js` 再 regen，**不直接編輯 .pptx**。

- 公開課專用：不得出現公司元素（「律果」「法務」等）；教材只放 claude-code-complete-tutorial 這個 repo。

### 前端既有契約

## 1. 前端內部資料模型（`prototype/index.html`，全域 `IMAGES` 陣列）
每筆：`{ id, name, url, cls, counts, split }`
- `id: string`（Roboflow image id；demo 模式為 `"demo"+i`）
- `name: string`（檔名，如 `wafer_0012.jpg`）
- `url: string`（圖片 URL 或 SVG data-URI）
- `cls: string[]`（類別名稱陣列，來源 `Object.keys(annotations.classes)`；分層抽樣只用 `cls[0]` 當主類別）
- `counts: {[className: string]: number}`（每類 bbox 數量，chip 顯示成 `scratch ×4`）
- `split: "train" | "valid" | "test" | null`（**注意是 `valid` 不是 `val`**）
其他前端 state：`SELECTED: Set<id>`、`lastIdx`（shift 範圍選取用）。

## 2. 前端「消費」的 Roboflow 回應（已實測）
`POST https://api.roboflow.com/{workspace}/{project}/search?api_key=KEY`，body `{ limit, offset, fields: ["id","name","owner","url","split","tags","annotations"] }`
回應 `{ offset, total, results: [ { id, name, owner, url, split, tags, annotations: { count, classes: {類別:數量} } } ] }`
前端映射：`url` 缺省時 fallback `https://source.roboflow.com/${r.owner}/${r.id}/thumb.jpg`；`split` 只接受 `train|valid|test`，其他一律變 `null`。
→ **新後端若要代替 Roboflow 餵圖，必須回這個形狀（至少 id/name/url/annotations.classes/split + total），前端 `loadRoboflow()` 才不用改。**

## 3. 前端「產生」的 `splits.json`（`#ex-json`）
```json
{
  "generated": "<new Date().toISOString()>",
  "source": "<#src 的文字，如 'Roboflow · ws/pj · 60/292 張' 或 '示範資料（離線合成）· 48 張'>",
  "seed": 42,
  "splits": {
    "train": [ { "id": "...", "name": "...", "url": "..." } ],
    "valid": [ ... ],
    "test":  [ ... ]
  }
}
```
鍵名精確為 `generated` / `source` / `seed` / `splits`，且三份為 **train / valid / test**。

## 4. 前端「產生」的 `data.yaml`（`#ex-yaml`）
```yaml
path: ./dataset
train: images/train
val: images/valid      # ← key 是 val，值指向 valid 目錄
test: images/test
nc: <類別數>
names: ['edge-loss', 'particle', 'ring', 'scratch']   # 去重 + sort 後的 flat list
```

## 5. ⚠️ 與 Python 端的既有衝突（新後端必須先裁決一種）
`scripts/prepare_dataset.py` 的 `write_data_yaml()` 產出：`path: <02-dataset 絕對路徑>` / `train: images/train` / `val: images/val` / `test: images/test` / `nc` / `names` 為 **dict**（`{0: "Donut"}`）。
差異三處：(a) 目錄名 `valid`(前端) vs `val`(python)；(b) `path` 相對 `./dataset` vs 絕對路徑；(c) `names` 是 list vs dict。
另外 `bbox-labeler.md` 的 data.yaml 範本是 6 類（0 center / 1 donut / 2 edge-loc / 3 edge-ring / 4 loc / 5 scratch），但 `prepare_dataset.py` 硬編 `CLASS_NAMES = {0: "Donut"}`、真實 training.log 也是 `nc=1`；前端 demo 資料又是另一套 4 類（scratch / particle / ring / edge-loss）。**三套 class 表並存，新系統要選一個真相來源。**

## 6. 前端訓練設定（送給後端的參數，取自 DOM）
`#model` ∈ {yolov8n, yolov8s, yolov8m}、`#epochs`(預設 50)、`#imgsz`(預設 640，step 32；但 wafer 實跑是 416)、`#device` ∈ {mps, cpu, cuda}；`runTraining()` 組成 `cfg = { model, epochs, imgsz, device }`。
抽樣參數：`#r-train`/`#r-valid`/`#r-test`（預設 70/20/10，可不等於 100，函式內會正規化）、`#seed`（預設 42）。
載入參數：`#ws`/`#pj`/`#key`/`#limit`（預設 60，max 500）；`#ws,#pj,#key` 存在 `localStorage['rf'] = {ws,pj,key}`。
前置守門：`IMAGES.length === 0` → alert「先載入資料」；仍有 `split === null` → alert「還有 N 張沒指派 split」→ **不送訓練**。

## 7. 前端目前「假造」的訓練 log 格式（新後端串流要對上這個渲染器）
寫進 `#log`（`.log` 是等寬、height 92px、auto scroll）的四種行：
1. `$ autocv train --model yolov8n --epochs 50 --imgsz 640 --device mps`
2. `dataset: 34 train / 9 valid / 5 test`
3. `epoch  12/50  box_loss 1.234  mAP50 0.567`（epoch 數 padStart(3)）
4. `done → runs/train/weights/best.pt`
進度：`#pbar.style.width = (epoch/epochs*100)+'%'`；訓練期間 `#train.disabled = true`。
→ 真實 Ultralytics 每 epoch 可提供的欄位（training.log 實證）：`box_loss` / `cls_loss` / `dfl_loss` / `Instances` / `imgsz` / GPU mem，驗證行為 `Class Images Instances Box(P R mAP50 mAP50-95)`，總結 `all 81 81 0.952 0.975 0.977 0.724` + `Speed: 0.4ms preprocess, 32.2ms inference, 3.6ms postprocess per image`。

## 8. 前端已有的其他顯示綁定（後端要餵的統計）
`#n-train`/`#n-valid`/`#n-test`（計數）、`#bar`（train/valid/test/unassigned 四段寬度比）、`#health`（三種訊息：仍有未指派 / valid=0 警告 / 全部指派完成）、`#dist`（每類 `train/valid/test` + 未指派 `+N`）、`#src`（資料來源字串）、`#shown`（`可見 / 總數`）、`#selcount`（已選 N 張）、`#dev`（header device 指示，跟 `#device` 連動）。

## 9. Claude Design artboard 端的契約（`Main.dc.html`）
props：`accent`（color，default `#D97757`，options 含 #4FB286/#8B7BE8/#E3B341）、`density`（enum comfortable|compact）、`$preview` 1440×900。
DCLogic state：`{ images: [{ id, name, cls(單一字串，非陣列), count, dots[], split }], sel: {} }`；事件插槽 `{{selectAll}} {{selectNone}} {{toTrain}} {{toValid}} {{toTest}} {{toClear}} {{item.pick}}`；列表用 `<sc-for list="{{images}}" as="item" hint-placeholder-count="8">`。
⚠️ artboard 的 `cls` 是單一 string、prototype 的 `cls` 是 string[]，兩邊模型不同名不同型，新系統要同步時得挑一邊。

### 現況缺口

- **完全沒有後端**。`claude-design_claude-code/` 全是靜態檔（`python3 -m http.server 8777` 而已），wafer 專案只有 CLI 腳本。沒有任何 HTTP server、沒有 job 佇列、沒有 run id。前端與訓練之間目前**零連線**（README 明講：「開始訓練」是模擬的假 loss 曲線，要真訓練請去 wafer 專案）。

- **沒有串流機制**。全 repo grep 不到 `text/event-stream` / `EventSource` / `WebSocket` / `StreamingResponse` 的任何實作；唯一的非同步範式是 `Projects/Normal-RAG2Graph-Project-claude` 的 `JOB_STORE[job_id]` + 輪詢 `GET /api/v1/status/{job_id}`（只有單一字串狀態，沒有 log 串流、沒有 per-epoch metric）。規格第 1 項「資料一張一張進來」與第 11 項「前端全程看得到 log」都要新做。

- **沒有 auto-labeling**。現有 `bbox-labeler` 明文寫「**不要重新生成 bbox**，Roboflow 已有 bbox 直接用」，只做格式驗證與目錄重組。規格第 2/3 項（AI 自己標、自己生 bbox）沒有任何實作；唯一資產是 `_Context/wafer-basics.md` 的 6 條**演算法草案**（Center/Loc 取最大連通分量外接框、Donut 取 30%-80% radius 環、Edge-Loc 邊緣最大分量、Edge-Ring 整個外圈、Scratch 用 PCA 主軸擴框、Random/None 跳過）——是設計稿，沒有 code，也沒有裝 opencv/scipy（pyproject 只有 ultralytics/roboflow/matplotlib/scikit-learn/python-dotenv）。

- **沒有 model selection（抓模型 / 自己決定最好的模型）**。`training-runner` 硬寫 `yolov8n.pt`，前端 `#model` 只有 yolov8n/s/m 三個選項且是人選的。沒有任何「比較候選模型後自動挑」的邏輯、沒有候選清單、沒有評分函式。

- **evaluator / hyper-tuner / orchestrator 三支只有 WALKTHROUGH 裡的設計稿，`.claude/agents/` 下不存在檔案**（實際只有 4 支）。規格第 9/10/11 項（自然語言討論、專家再討論、自我進化）的 agent 全要新建。

- **沒有 agent 之間對話的落地方式**。wafer WALKTHROUGH 行 519 已自己承認「Claude Code 還沒原生支援 agent-call-agent，需要外部 driver script 或 workflow tool（LangGraph / Vercel Workflow）」。規格第 10/11 項的「專家在背景不斷討論」需要一個 driver（loop + budget cap + 停止條件），repo 內完全沒有這個東西。相近的既有範式只有 `stock-groups-skills`（平行 → synthesizer 收斂，但是一次性、非迴圈）與 `agent_teams` 的 mailbox（跨 Claude 實例、非程式可驅動）。

- **`experiments.md` 記憶只有 schema 沒有實體**。`04-experiments/` 因 `.gitignore` 排除 `Projects/` 而不在 repo 內，沒有任何 run 紀錄檔、沒有機器可讀格式（schema 是 markdown 給人看的）。自我進化迴圈需要可程式讀寫的 run 紀錄（JSON/SQLite），現在沒有。

- **Metric 圖表一張都沒有**。前端只有一個 6px 進度條 + 純文字 log；沒有任何圖表庫、沒有 canvas/svg 折線圖、tokens.css 也沒有圖表配色。Ultralytics 會產 `results.csv` / confusion matrix / PR curve PNG 在 `04-experiments/exp001/`，但**沒有任何把它們送到前端的路徑**。規格第 8 項要新做（注意 repo 有 dataviz/artifact 慣例，但這個 console 是自帶 CSS 的單檔，外部 CDN 要自己決定）。

- **沒有逐張串流載入**。`loadRoboflow()` 是一次 `fetch` 全部再 `render()` 一次；`loadDemo()` 一次生 48 張。要「一張一張進來」得改成增量 append（前端 render 目前是整個 `wall.innerHTML` 重繪，48 張 OK，逐張串流時會重繪抖動，需要改成 append 單卡）。

- **沒有「按一顆按鈕跑完整條」的腳本**。前端 `#train` 只觸發本地 setInterval 模擬；wafer 端要人工依序跑 download → prepare → train，且 `training-runner` 還刻意設了「等使用者回 GO」的人工閘門。規格要的一鍵端到端腳本不存在，且**與既有 human-gate 慣例衝突**，需要明確裁決（例如：一鍵模式豁免 GO，但保留 budget/時間上限）。

- **class 表三套並存、split 目錄命名兩套並存**（見 frontendContract §5）：6 類(bbox-labeler 範本) vs 1 類 Donut(prepare_dataset.py + 真實 nc=1) vs 4 類 demo(scratch/particle/ring/edge-loss)；`valid`(前端) vs `val`(python)。新後端接上去之前必須先凍結一份真相。

- **沒有 CORS / 安全邊界的答案**。前端目前把 Roboflow API key 存 `localStorage` 並直打第三方 API，WALKTHROUGH 明訂「部署到公開網址 ⛔ 絕對不行」。新後端一旦存在，key 應搬到 server env，前端載圖也應改走自家 API——這是既有文件已預告但尚未做的那一層 proxy。

- **沒有 GPU/長工時的管理**。實測 50 epochs = 8.89 分鐘（286 張、imgsz 416、M4 Max）；自我進化模式要跑 N 輪重訓，沒有任何併發控制、佇列、取消（前端也沒有「停止訓練」按鈕）、或 budget cap 實作（WALKTHROUGH 只在文字上提醒 orchestrator 要有 budget cap）。

- **新專案資料夾還不存在**。這套系統跨 `claude-design_claude-code/`（前端）與 `agent_group_projects/computer-vision-wafer-detection/`（訓練）兩個互不相干的目錄，且後者 `.gitignore` 排掉 `Projects/`、`*.pt`、`runs/`。依 `tutorial-scaffold` 慣例應另開一個新子專案（README/CLAUDE.md/.gitignore/.env.example/pyproject.toml/_Context）並在 `agent_group_projects/README.md` 總覽表補列，而不是把新後端塞進現有兩個資料夾。

- **`design-canvas/support.js` 本機不存在**（`Main.dc.html` / `Components.dc.html` 都 `<script src="./support.js">`）。artboard 只能在 Claude Design canvas runtime 裡跑，`.dc.html` 不是能直接 serve 的頁面；要在本機看只能開 2.5MB 的 `cv-self-training-console.html` 匯出檔。新前端的真實可跑版本應繼續走 `prototype/index.html` 這條線。

---

_由 workflow `cv-expert-roster-design` (14 agents) 產出並綜合。_
