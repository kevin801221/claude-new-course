# PLAN · 資料集市集 + 多 workspace（提案，未動工）

> 狀態：**只是計畫**。動工前要 console-owner 批准，契約變更走 §12。
> 寫於 2026-09-13。所有「實測」數字都是這份計畫寫之前真的打過那支 API 得到的。

## 下一個 session 從這裡開始（交接）

**讀這三個就夠**：本檔 → `CLAUDE.md`「現在做到哪」→ `_Context/api-contract.md` §8。
DESIGN.md 的 M6–M8 是**另一件事**（討論迴圈 / 自主模式），這份計畫與它不衝突也不依賴它。

**先確認環境還活著**（1 分鐘）：

```bash
cd agent_group_projects/cv-self-evolving-console
uv run python scripts/selfcheck.py                      # 3/3 PASS · 約 2 秒
uv run --with pytest --extra train pytest -q            # 26 passed
CV_LLM_CMD=nope uv run --extra train python scripts/run_all.py --preset real --stages s01,s03,s02,s04
# → 0.9 秒跑完四段，零 GPU 零網路。整條龍要 --extra train 且約 60 秒
```

**派工對照（`/dispatch` 直接用這張表，目錄不重疊）**

| 關 | 負責角色 | 可寫目錄 |
|---|---|---|
| W1 三個 adapter + 統一資料卡 | **dataset-truth** | `src/sources/`、`src/app/routers/dataset.py` |
| W2 前端第五分頁 | **console-owner** | `prototype/index.html` |
| W3 workspace 隔離重構 | **console-owner**（地基）＋ dataset-truth / training-engineer / metric-auditor 各改自己那半 | `src/app/ws.py`(新)、各自 router 與模組 |
| W4 Roboflow 匯入 | **dataset-truth** | `src/sources/roboflow.py`、`src/autolabel/roboflow_src.py` |
| W5 批量上傳 | **dataset-truth** | `src/app/routers/dataset.py`、`src/sources/upload.py`(新) |
| W6 HF / Kaggle + normalizer | **dataset-truth** | `src/sources/` |
| W7 語意排序 | **dataset-truth** | `src/sources/rank.py` |
| 全部的契約條文與錯誤碼 | **console-owner**（走 §12） | `_Context/api-contract.md` |

**已知的紅線不變**：前端只有 console-owner 能改；契約只有 console-owner 能改；
事件只能經 `bus.append_event()`；`03-sealed-test/` 四道鎖；套件一律 `uv`。

---

## 0. 一句話

把「一個寫死的資料集 + 一條管線」變成「**搜尋 → 挑 → 下載 → 自己的 workspace 裡跑自動標註到訓練**」，
外加自己批量上傳圖片。

**真正的難點不是那三個 API**（都驗過了，加起來一天內接得完），
**是 workspace 隔離** —— 現在 `02-dataset/`、`03-sealed-test/`、`class_table.json`、`runs.jsonl`
全都是**單例**，實測引用處：`02-dataset` 36 處、`03-sealed-test` 13 處、`runs.jsonl` 17 處、
`CLASS_TABLE_GLOBAL` 3 處。不先把這件事做對，第二個 workspace 一開就會覆蓋第一個的 dataset 與裁決。

---

## 1. 三個平台實測（2026-09-13，這份計畫的地基）

| 平台 | 搜尋端點 | 要 key？ | 回什麼 |
|---|---|---|---|
| **Roboflow Universe** | `GET api.roboflow.com/universe/search?q=<q>&api_key=<key>` | 要（`.env` 已有） | `{query, page, page_size, count, results[]}`；每筆有 `projectId`・`workspace.url`・`latestVersion`・`images`・`classes[]`・`classCount`・`type`・`license`・`description`・`thumbnail`・`annotationThumbnail`・`downloads`・`stars`・`views`・`tags`・`url` |
| **HuggingFace** | `GET huggingface.co/api/datasets?search=<q>&limit=N` | **不用** | `id`・`downloads`・`likes`・`tags[]`；支援 `filter=task_categories:object-detection` |
| **Kaggle** | `GET kaggle.com/api/v1/datasets/list?search=<q>` | **實測不用**（`.env` 的 KAGGLE_* 是空的照樣回 20 筆） | `ref`（`owner/slug`）・`titleNullable`・`totalBytesNullable`・`licenseNameNullable`・`usabilityRatingNullable`・`thumbnailImageUrlNullable`・`currentVersionNumberNullable` |

**關鍵好消息**：Roboflow 搜尋結果的 `workspace.url` + `projectId` + `latestVersion`
**剛好就是**現有 `roboflow_src.ensure(workspace, project, version)` 要的三個參數 ——
Roboflow 那條「搜到就能下載」是**接上既有程式**，不是重寫。

**關鍵壞消息**：HF 與 Kaggle **不是 YOLO 格式**。HF 是 parquet/arrow（bbox 欄位每個 dataset 自己定），
Kaggle 是任意 zip（可能是 .mat、可能是一堆 png 沒有標註）。實測 Kaggle 那支
`datasets/download/<ref>` 不帶 key 回 200，**下載得到不等於用得了**。
「下載完就能跑自動標註」對這兩個平台**不成立**，要寫 normalizer，這是整個計畫最大的隱藏工（見 §9 風險）。

---

## 2. workspace 是什麼（資料模型）

```
workspaces/
  <ws_id>/                       ws_id = ws1, ws2, …（比照 ds 的發號規則）
    workspace.json               {id, name, created_at, source:{kind,ref}, quota_mb, notes}
    01-raw-data/                 這個 workspace 自己的圖（下載的或上傳的）
      datasets/<ds_id>/          manifest / labels / labels_human / montage（維持現況結構）
    02-dataset/                  ← 現在是全域，改成每個 workspace 一份
    03-sealed-test/              ← 同上（四道鎖逐 workspace 各自成立）
    04-experiments/              ← 同上
    runs/                        events.jsonl + runs.jsonl（帳本逐 workspace）
    class_table.json             ← 取代全域的 `_Context/class_table.json`
00-weights/                      **保持全域**：預訓練權重 28 MB，沒有理由每個 workspace 複製一份
```

### 兩個必須講清楚的邊界

1. **隔離資料，不隔離 GPU。**
   兩個 workspace 可以同時在標註，但**訓練佇列全域深度仍然是 1**（這台機器只有一顆 MPS）。
   `registry.running_run()` 從「全域唯一」變成「全域 GPU 鎖 + 逐 workspace 的 run 狀態」。
   不這樣做的話，三個 workspace 一起按訓練 = 三個 torch 行程搶同一顆 GPU，全部變慢且 OOM。

2. **事件信封 🔒 不動。**
   契約 §2 的信封是凍結的，加一個 `ws` 欄位是破壞式改動。
   workspace 體現在**檔案路徑**（`workspaces/<ws>/runs/<run>/events.jsonl`）與
   **REST 路徑**（`/api/v1/workspaces/{ws}/runs`）上，信封逐欄不變。
   代價：`run_id` 要全域唯一（不能兩個 workspace 都有 r1）—— 發號器改成全域遞增，這比改信封便宜。

---

## 3. 現況盤點（workspace 化要改的清單）

| 現在 | 改成 | 影響處數 |
|---|---|---|
| `freeze.DATASET_DIR` / `SEALED_DIR` 常數 | 已經是函式參數（`write_dataset(dataset_dir=…, sealed_dir=…)`），只要把預設值改成由 ws 推導 | 11 + 5 |
| `metrics.DATASET_DIR` / `SEALED_DIR` | 同上，evaluator 要吃 ws | 36 / 13 之中的一半 |
| `dataset.CLASS_TABLE_GLOBAL` | `ws_root(ws)/class_table.json` | 3 |
| `bus.RUNS_DIR` / `registry.RUNS_DIR` | `ws_root(ws)/runs` | 17 |
| `registry.DS_DIR` | `ws_root(ws)/01-raw-data/datasets` | 多處 |
| `running_run()` / `claim_or_crash()` | 全域 GPU 鎖 + 逐 ws 狀態 | 12 |
| 前端 5 處寫死 `02-dataset` 字樣 | 顯示用文字，改成從快照拿 | 5 |

**做法**：新增 `src/app/ws.py`（console-owner），唯一真相是 `ws_root(ws_id) -> Path`，
其他模組一律從它推導；`PROJECT_ROOT / "02-dataset"` 這種寫法全部消滅。
遷移現有資料：把目前的根目錄內容搬進 `workspaces/ws1/`，加一支 `scripts/migrate_to_ws.py`（可重跑、先 dry-run）。

---

## 4. 搜尋層

新增 `src/sources/`（擁有者 **dataset-truth**）：

```
src/sources/
  base.py          DatasetCard 的 dataclass + 統一形狀 + 三條驗證
  roboflow.py      search() / fetch()   ← fetch 直接呼叫既有的 roboflow_src.ensure
  huggingface.py   search() / fetch()
  kaggle.py        search() / fetch()
  rank.py          語意排序（一次 claude -p，失敗降級成關鍵字順序）
```

統一的資料卡（三個平台的欄位對齊到這一組，缺的就是 `None`，**不准編**）：

```python
DatasetCard = {
  "source": "roboflow|huggingface|kaggle",
  "id": "<source 內唯一>",          # rf: "study-teaqt/wafer-defect-gdeeb/3"；hf: "org/name"；kaggle: "owner/slug"
  "title": str, "description": str | None,
  "images": int | None,             # kaggle 給不出來 → None，畫面顯示「—」不是 0
  "classes": list[str] | None, "class_count": int | None,
  "task": "object-detection|instance-segmentation|unknown",
  "license": str | None,            # ⚠️ 必顯示，下載確認要把它寫在 Y/N 上
  "size_bytes": int | None,
  "thumbnail": str | None,          # 一律走自家 proxy，不讓前端直連第三方
  "popularity": {"downloads": int|None, "stars": int|None, "views": int|None},
  "url": str,                       # 原始頁面（人要能去看）
  "ready_to_train": bool,           # ← 這一欄決定「下載」按鈕旁邊要不要標「需轉檔」
}
```

**語意搜尋怎麼做（不要做成向量資料庫）**：
關鍵字先打三個平台拿 30–60 筆候選 → **一次** `claude -p` 把使用者的自然語言問題 + 候選卡片摘要丟進去
→ 回 `[{id, score, why}]` 重排 + 一句繁中理由。理由直接顯示在卡片上（「這個是晶圓圖、有人工框、CC BY」）。
沿用 s02 的 `CV_LLM_CMD` 逃生門：離線就退回關鍵字順序，畫面標黃「未經語意排序」。
**不引 embedding 模型、不建向量庫** —— 候選只有幾十筆，一次 LLM 呼叫就夠，而且理由可讀。

---

## 5. 搜尋 → 資料卡 → 下載（Y/N）

| 方法 | 路徑 | 說明 |
|---|---|---|
| `POST` | `/api/v1/catalog/search` | `{q, sources:["roboflow","huggingface","kaggle"], limit, semantic:true}` → `{query, ranked, results:[DatasetCard], llm:{cost_usd,…}\|null}`。同步做完才回（實測三個平台各 1–3 秒） |
| `GET` | `/api/v1/catalog/{source}/{id}` | 單張資料卡的完整版（描述、類別清單、授權、預覽圖、`ready_to_train` 與**不能直接訓練的原因**） |
| `POST` | `/api/v1/workspaces/{ws}/imports` | `{source, id, confirm:false}` → **不下載**，回 `{plan:{size_bytes, license, files, ready_to_train, warnings[]}}`（這就是 Y/N 對話框的內容） |
| `POST` | 同上 `{confirm:true}` | 真的下載 → 202 + `import_id`，事件 `ds.import.*` 走既有事件流 |
| `GET` | `/api/v1/workspaces/{ws}/imports/{id}` | 進度（bytes / 檔數 / 轉檔階段） |

**為什麼 confirm 要在後端而不只是前端的 window.confirm**：
一次誤點可能是 2 GB 下載。前端的 Y/N 是 UX，後端的 `confirm:true` 是閘門 ——
兩層都要，而且 plan 那一趟要**真的去問來源檔案有多大**，不是估的。
（Roboflow 那條可以精確：`images` × 平均；Kaggle 有 `totalBytesNullable`；HF 要打一次 `/api/datasets/{id}` 的 siblings。）

新錯誤碼：`CATALOG_SOURCE_UNAVAILABLE`(502)・`IMPORT_NOT_CONFIRMED`(409)・
`IMPORT_TOO_LARGE`(413，超過 workspace 配額)・`FORMAT_UNSUPPORTED`(422，下載得到但轉不了 YOLO)・
`LICENSE_UNKNOWN`(警告不是錯誤，寫進 plan.warnings)。

---

## 6. 批量上傳

| 方法 | 路徑 | 限制（信任邊界，不准偷懶） |
|---|---|---|
| `POST` | `/api/v1/workspaces/{ws}/uploads` | multipart；單檔 ≤ 20 MB、單次 ≤ 200 檔、workspace 總量 ≤ 配額（預設 2 GB） |

- **不信任副檔名**：用 `PIL.Image.open().verify()` 認真實格式（PIL 已經是既有依賴），只收 PNG/JPEG/BMP/TIFF。
- **不信任檔名**：一律重新發 id（`up<hash12>`），原檔名只當顯示用並過 `esc()`；杜絕路徑穿越。
- **去重**：sha256 相同就跳過並回報「N 張重複」—— 同一張圖進兩次會讓 split 洩漏。
- **EXIF 一律剝掉**（手機拍的含 GPS，這是個無人看管的本機服務，但備份出去就外流了）。
- 上傳完自動建一個 `ds`，manifest 的 `source` 記 `"upload"`，`labels` 記 `"none"`
  → 接上**標註器**（已經做好了）或 `labels:"auto"` 的自動標註。

---

## 7. 前端

- 頂部加 **workspace 切換器**（下拉 + 「新增 workspace」），切了整個畫面換一套（rail / 縮圖牆 / 圖表 / 標註器）。
- 新增**第五個分頁「資料集市集」**：搜尋框（自然語言）＋來源勾選＋結果卡片牆（縮圖、張數、類別、授權、熱門度、AI 理由）。
- 卡片點下去 → 右側抽屜顯示完整資料卡 → 「下載到這個 workspace」→ Y/N 確認框（顯示大小/授權/是否需轉檔）。
- 同一頁底部是**上傳區**（拖放 + 選檔），進度條走既有事件流。
- 規矩不變：單檔零依賴、無 build step、不准引 CDN、圖表 inline SVG、顏色只取 `--ds-*`。
- 縮圖一律走自家 proxy（`/api/v1/catalog/thumb?src=…`），**不讓前端直連第三方**（既有護欄：前端不持 key、不開 CORS）。

---

## 8. 契約變更清單（§12 要走的）

1. 新增 §8.12 catalog（搜尋 / 資料卡 / 匯入三支）
2. 新增 §8.13 uploads
3. 新增 §8.14 workspaces（建立 / 清單 / 切換 / 配額）
4. §4 新增 type：`ds.import.started|progress|done|failed`、`ds.upload.*`（前綴 `ds.` → dataset-truth，不用改前綴表）
5. §10 新增五個錯誤碼（見 §5）
6. §3 不動、§2 信封 🔒 不動（這是刻意的，見 §2 邊界 2）
7. §11 的 M1 離線資料契約不動（demo 那條路永遠留著當零網路教材）

---

## 9. 里程碑與驗收（每一關都要有可跑的證據）

| 關 | 內容 | 估時 | 驗收（實跑，不是「看起來對」） |
|---|---|---|---|
| **W1** | 三個 adapter + 統一卡片 + `POST /catalog/search`（**唯讀，不下載**） | 0.5 天 | `python -m src.sources.<x>` 三支自檢；一次搜尋回三個平台的結果且欄位齊全；離線時逐平台降級不整串炸 |
| **W2** | 前端第五分頁：搜尋 + 卡片牆 + 資料卡抽屜 | 0.5 天 | `?selftest=1` 加 6 條（卡片欄位缺值顯示「—」不是 0、授權必顯示、縮圖走 proxy、第三方字串一律 `esc()`） |
| **W3** | **workspace 隔離重構** + `scripts/migrate_to_ws.py` | **1.5–2 天** | 舊資料搬進 ws1 後，既有 52 條前端自檢 + 3 條 selfcheck + 26 pytest 全綠；兩個 workspace 各跑一條 run，事件與 `02-dataset` 互不污染；同時按訓練 → 第二個排隊不是 OOM |
| **W4** | Roboflow 匯入（plan → Y/N → 下載）| 0.5 天 | 從搜尋結果點一個真的 Universe 專案下載進 ws，接著跑完 s01→s04 |
| **W5** | 批量上傳 | 0.5 天 | 200 張上傳、重複檔被擋、假副檔名被擋、EXIF 被剝、超配額回 413 |
| **W6** | HF / Kaggle 下載 + **格式 normalizer** | **1 天＋**（最不確定） | 至少支援：HF 的 `image + objects{bbox,category}` parquet、Kaggle 的「資料夾即類別」與 YOLO zip；轉不了的**明確回 422 並在卡片上先標好**，不准下載完才發現 |
| **W7** | 語意排序（一次 `claude -p`） | 2 小時 | 同一個查詢有 / 無語意排序的前 5 名差異印出來；離線降級標黃 |

**合計約 5–6 天**，其中 W3 與 W6 是真正的風險。

**順序建議**：W1 → W2 → **W3（先做，越晚做越貴）** → W4 → W5 → W6 → W7。
W3 拖到後面做，等於要在三倍的程式碼上做同一個重構。

---

## 10. 風險與刻意不做的事

1. **HF / Kaggle 的「下載得到 ≠ 用得了」**。這是最大的坑。對策：`ready_to_train` 這一欄**在搜尋階段就算出來**，
   卡片上直接標「可直接訓練 / 需轉檔 / 不支援」，不要讓人下載完 2 GB 才發現是 .mat。
2. **授權**。三個平台混著 CC BY、非商業、未標示。資料卡必須顯示，Y/N 對話框必須重複一次。
   教學場合尤其重要 —— 學生會把下載的東西拿去做專案。
3. **磁碟**。現在單一 workspace 已經 3 GB（`04-experiments` 2.4 GB 是探針與 last.pt）。
   多 workspace 前要先做配額 + 那個「刪探針權重」的清理（DESIGN:64 本來就規劃了，一直沒做）。
4. **全域 GPU 佇列**。多 workspace 最容易做錯的地方，見 §2。
5. **sealed test 的四道鎖**要逐 workspace 各自成立，重構時最容易漏的就是這個（`metrics.split_dirs("test")` 那條）。
6. **刻意不做**：向量資料庫、跨 workspace 的指標比較（不同資料集的 mAP 本來就不可比）、
   線上協作 / 多人帳號、把服務 bind 到 `0.0.0.0`（護欄 10，永遠不做）。

---

## 11. 動工前要你拍板的三件事

1. **workspace 要不要能改名 / 刪除**？刪除牽涉到「連帶刪 3 GB 實驗」，要不要做成需要打名字確認？
2. **配額預設多少**？建議 2 GB/workspace，超過就擋匯入（但不擋標註）。
3. **W6 先支援哪一種 HF 格式**？建議只做 `object-detection` 標準欄位（`image` + `objects.bbox` + `objects.category`），
   其他一律標「不支援」而不是猜。
