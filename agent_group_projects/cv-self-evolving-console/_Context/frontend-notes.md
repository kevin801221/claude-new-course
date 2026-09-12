# 前端筆記（console-owner・M1）

> 唯一前端檔：`prototype/index.html`。**其他四位一行都不准改**（team-roles §0 紅線 1）。
> 來源：`claude-design_claude-code/prototype/index.html`（455 行，唯讀）→ `cp` 過來演進，原檔一個字都沒動。
> 行數：**455 → 860**（+405）。DESIGN 估最終約 1600 行，M1 只長該長的那一半。

---

## 1. 改了哪幾塊

| 區塊 | 舊（455 行版） | 新（M1） | 為什麼 |
|---|---|---|---|
| `render()` | 整面 `wall.innerHTML = ''` 再逐張 `appendChild` | 拆成 `syncCard(im)`（有就更新、沒有就 append 一張）+ `render()`（只有篩選/指派這種使用者動作才全掃）。篩選改用 `el.hidden`，**不再清空 wall** | 48 張整面重繪 OK，120 張逐張串流會整面抖動。DESIGN s01 明文要求 |
| 資料來源 | `loadRoboflow()` 瀏覽器直打 `api.roboflow.com` + `#key` 存 localStorage | 整段刪除。改成 `POST /api/v1/runs {mode:"oneshot",source,preset,limit,seed}` 一顆按鈕 | key 搬 server env（護欄 10）。前端不再持 key，順手解掉「只能 localhost」那條部署禁令的成因 |
| demo 資料 | `loadDemo()` + `waferSVG()` 產 48 張 SVG data-URI | 整段刪除。改吃 `scripts/gen_demo_wafers.py` 的真點陣 PNG，走 `GET /api/v1/images/{id}` | SVG 沒有像素，連通分量跑不出東西（DESIGN 資料真相 (3)） |
| 事件 | 無（`runTraining()` 是 `setInterval` 假 loss 曲線） | `EventSource` client：**單一 `es.onmessage` → `JSON.parse` → `switch(ev.type)`** | 契約裁決 A/B：線上框不送 `event:`，用 `addEventListener(type)` 綁 20 種，新增 type 必漏接 |
| 假訓練 | 60ms 一跳的模擬 epoch | 刪掉，`#train` 鈕 disabled 標「未實作 M3」 | 「不要假裝在跑」。有真後端之後，假曲線是謊 |
| stage | 無 | `.rail` 11 格骨架（`s01..s11`），M2+ 灰底標「未實作（M2+）」 | 契約 §0：骨架 M0 定死，前端只寫一次版面，M2 加回來版面不跳 |
| bbox | 無 | 每張縮圖疊一層 `<svg class="ov">`，coral 描邊；點框彈「用了哪條規則」 | DESIGN s03。`rule` → 人話的對照表留了 fallback，M2 加 `ring_annulus`/`pca_axis` 不用改結構 |
| 天花板證據 | 無 | 右欄 inline SVG 直方圖 + 中位數 + 門檻虛線 + PASS/FAIL 一行裁決 | DESIGN s03：「這張圖是整條流水線的天花板證據，不是裝飾」。純自畫，沒引任何圖表庫 |
| 錯誤 | `alert()` | 單一 `showErr({code,detail})` 渲染器 → 左欄紅底 banner + log 一行 | 契約 §1：錯誤只有一種形狀，所以前端只有一套渲染器 |
| selftest | 9 條 | **15 條** | 新增的非 trivial 邏輯（冪等、去重、分頁游標）一定要有能跑的檢查 |

## 2. 刻意保留、一個字都沒動的東西

這幾塊是「後端要對上前端」，不是反過來，所以**故意不碰**：

1. **`#log` 渲染器**：`log.textContent += s+'\n'; log.scrollTop = log.scrollHeight`。
   事件的 `text` 欄位是後端**已經格式化好**的一行，前端直接印，不再拼字（契約 §2）。
   四行格式（`$ autocv ...` / `dataset: N ...` / `epoch  12/50 ...` / `done → best.pt`）由後端負責產，M3 的 `train.epoch` 接上來時**這裡不用改一行**。
   → 副作用：`startRun()` 自己那行 `$ autocv run ...` 刪掉了，因為 `run.created.text` 已經有一行，兩行會重複。
2. **`#pbar`**：仍然是 `style.width = 百分比`。M1 由 `ds.progress` 的 `loaded/total` 驅動，M3 換成 `epoch/total`，元素與寫法不變。
3. **`stratifiedSplit()` + `mulberry32()`**：**逐字未改**。`scripts/selfcheck.py` 的 assert 2 要拿 Python 版逐筆比對，改這裡就要同步改 Python。四處關鍵：主類別取 `cls[0]`（無類別 `__none__`）／分層鍵字串排序後才處理／每層 Fisher–Yates 用 `mulberry32(seed >>> 0)`／最大餘數法補足，順序 `train,valid,test`。
4. **匯出**：`splits.json`（鍵名 `generated`/`source`/`seed`/`splits`）與 `data.yaml`（`val: images/valid`）原樣保留。M2 的 `/datasets/{ds}/freeze` 落檔要對上的就是這個形狀。
5. **選取互動**：shift 範圍選取、`1/2/3/0` 快捷鍵、`#bar`/`#health`/`#dist`/`#shown`/`#selcount` 的統計渲染全部沿用。
6. **`--ds-*` token**：一個 hex 都沒新發明，code 與數字一律 Menlo。

## 3. 契約上的幾個實作決定（讀 code 前先看這段）

- **冪等（契約 §6）**：`KEYED` 這組 type（`ds.image`/`label.bbox`/`label.mask`/`label.lowconf`/`ds.progress`/`train.epoch`）永遠跑 reducer（upsert）；其餘一次性事件 `seq <= SEEN` 直接丟掉。`ds.image` 依 `data.id` upsert（`IDX` map），`label.bbox` **整包取代**（`BOXES.set` + `svg.innerHTML` 重寫）——append 會在同一張圖疊兩層框，IoU 看起來還會變好，是最惡毒的那種 bug。
- **重連順序（契約 §7.3）**：`resync()` 寫死三步：`GET /runs/{id}` 快照 → `GET /datasets/{ds}/images?offset=` 分頁到 total → 才帶 `?since=<自己收到的最大 seq>` 接串流。**早期縮圖絕不依賴 replay**（第 201 筆起後端根本不落檔影像事件）。`since` 用前端自己的 `SEEN` 不是快照的 `last_seq`——影像快照只補 `ds.image`，不補 `label.bbox`，用 server 的 last_seq 會把框吃掉。
- **游標分支**：`ds.progress` 一旦發現 `loaded - IMAGES.length >= 50` 或 `loaded===total` 但卡片不足，就去打影像快照。M1 是 120 張（<200）走不到這條，但它現在就在，且被 selftest 的 `pagePlan(409,200)=[0,200,400]` 蓋住 —— M8 接 Roboflow 是 409 張，當天才寫就是當天才爆。
- **stage 狀態的權威是快照不是前端推論**：事件只負責把 `pending` 點亮成 `running`（立即回饋），`done`/`failed`/`skipped` 一律由 3 秒一次的 `GET /runs/{id}` 快照覆蓋。前端不自己推「s01 應該結束了」——那是第二套狀態機。
- **45 秒自救重連**：瀏覽器**不會**把 `: ping` 註解行交給 JS（`EventSource` 沒有任何 handler 看得到它），所以這裡量的是「45 秒沒有 data 框」，不是「45 秒沒有位元組」。誤觸發的代價只是一次帶 `since` 的重連（server 回零框），比半開連線卡死便宜太多。這條寫進 specDeviations。
- **run 結束就自己 close**（契約 §5.6）：server 發完最後一框會保持連線繼續 ping，所以收到快照 `status != running` 時是前端自己 `close()` + 停 poll，不是等 server 關。

## 4. `?selftest=1` 的 15 條（`http://127.0.0.1:8000/?selftest=1`）

1–9 分層抽樣（原有九條，一字未改）
10. `stratifiedSplit`：M1 形狀（120 張、`cls:[]` 全部未分類）同 seed 兩次逐筆相同 —— 蓋住 `__none__` 那條退化路徑，也是 `selfcheck.py` assert 2 的對照組
11. 同一批 `ds.image` 重放兩次，卡片數不變（3 → 3，DOM 也是 3）
12. `label.bbox` 重放 → 框數不變（2，不是 4）
13. 舊 `seq` 的一次性事件被丟掉且 `SEEN` 不倒退
14. `pagePlan(120,200)=[0]`、`pagePlan(409,200)=[0,200,400]`
15. 未知 type（`charts.series`）與 `v!=1` 只記一行、不炸掉 reducer

## 5. 已驗過的（Chromium 實跑，不是推論）

- `?selftest=1` → 15 條全 PASS。
- 按那一顆按鈕 → 120 張真點陣 PNG（`naturalWidth 256`）逐卡 append、155 個 bbox 疊上去、`#pbar` 100%、`#shown` `120 / 120`、rail `s01 done / s03 done`、其餘 skipped 灰。
- 點 bbox → 彈出「連通分量外接框（cc_bbox）／cls 未分類（M1 沒有 class 表）／conf 1.00／cx cy w h」。
- `resync()`（模擬斷線）→ 卡片 120 不變、框 155 不變、`SEEN` 不倒退。
- dataset seam 還沒接上時（M0 狀態）：`stage.failed` → `s01` 轉紅 + 錯誤 banner，不是進度條卡住。
- 沒有橫向捲動（`scrollWidth <= innerWidth`）。

## 6. 下一個里程碑要動哪裡（M2/M3 的人看這段就好）

- M2 `class.table.frozen` / `ds.split` / `ds.selfcheck`：在 `applyEvent` 的 `switch` 加 case，rail 的 `s02`/`s04` 會自動從 `skipped` 變色（狀態來自快照）。**不用改版面**。
- M3 `train.epoch`：`#log` 印 `text`、`#pbar` 改吃 `epoch/total`，兩者都已經在位；要新加的只有雙軸折線與 Stop 鈕。
- `rule` enum 擴充（`ring_annulus`/`pca_axis`）：只要在 `RULES` 對照表加一行人話，沒加也不會壞（fallback 印原始 enum）。

---

# M4 追加（console-owner・s08 評分端上畫面）

> 行數：**860 → 1541**（+681）。一樣單檔零依賴、無 build step、**零圖表庫**（四張圖全部 inline SVG 自畫）。
> 顏色一個新 hex 都沒有，全部取既有 `--ds-*`；數字與 code 一律 Menlo。

## 7. M4 加了什麼（三塊）

| 區塊 | 位置 | 說明 |
|---|---|---|
| verdict badge | `header` 最上方一枚 `#vbadge` | trusted 綠 / suspect 黃 / invalid 紅（`final` 走紫）。點開 `#vpanel` 列四項 check（PASS 綠 / FAIL 紅 + 後端給的 detail 原文）。**invalid 時 `#evolve`「進化下一輪」標 blocked 並寫出被擋的理由**，`EVOLVE_BLOCKED` 同時擋掉點擊路徑 |
| 四張圖 | `main` 的「評分圖表 · s08」分頁 | 訓練曲線（loss 雙軸 + 飽和點）／PR curve（每類一條）／混淆矩陣 heatmap／per-class AP50 + CI 誤差線 |
| 推論圖牆 | 「推論圖牆 · valid」分頁 | **valid split**，每張疊預測框（coral）與 GT 框（綠色虛線），hover 出 class + conf + IoU；下方一行統計 |

`main` 改成三個分頁（`.tabs` + `.view`）。**縮圖牆、`#log` 渲染器、`#pbar`、`stratifiedSplit()`、
匯出、選取互動一個字都沒動**，只是被包進 `#view-data`。

## 8. 四張圖的畫法與「為什麼這樣畫」

- **一支 API 打完**：`GET /api/v1/eval/{run}/charts` 一次拿到 `curves / pr / confusion / per_class /
  op_conf / noise_floor / ci`；`/verdict` 拿裁決；`/predictions?split=valid` 拿逐張座標。
  **前端一個指標都不自己算** —— 算指標的手只有 metric-auditor 一隻。
- **noise floor 灰帶：四張圖都有一條**（`?selftest=1` 第 20 條在數）。四張的單位不同，所以帶也不同：
  | 圖 | 帶畫在哪 | 為什麼是這個 |
  |---|---|---|
  | 訓練曲線 | mAP 軸上 `[best-nf, best]` | 落在帶內的 epoch 與最佳值讀不出差別 → 飽和點就標在帶的左緣 |
  | PR curve | precision 軸 `0..nf` | precision 與 AP 同樣是 0..1，帶當「量尺」：比這一格窄的落差讀不出來 |
  | 混淆矩陣 | 色階條上 `0..nf×GT框數` | 把 2σ 換算成「差幾格」：0.1479 × 15 = **2.2 格以內讀不出來** |
  | per-class AP | AP 軸 `0..nf` 的直帶 | 比帶窄的 AP 差距一律是雜訊 |
- **Δ 落在帶內就灰掉標「雜訊」**：`isNoise()` / `deltaHTML()`，門檻**含等號**（正好等於 noise floor
  仍然讀不出來）。第 22 條守它 —— 第一版用字串比對「雜訊」兩個字，帶外那句寫著「超出雜訊帶」
  同樣中招，**該條 FAIL 過一次才改成比 `class="noise"`**。會失敗的檢查就是這樣用的。
- **飽和點的定義**：第一個「與最佳值差距已落在雜訊帶內」的 epoch。r30-t1 實測 = **epoch 8**
  （best 0.2618，epoch 8 是 0.1271，差 0.1347 < 0.1479）。若只有最後一個 epoch 符合 → 顯示
  **未飽和**，並直說「這是訓練不足不是過擬合」（與後端 `overfit_gap` 的 `still_climbing` 同一件事）。
- **loss 的定義抄後端**：`train_box_loss + train_cls_loss`，與 `src/eval/checks.overfit_gap` 逐字相同。
  換定義前端就會跟後端的裁決講不同的故事。
- **support < 30 自動加註「樣本不足，僅供提示」**：PR 圖畫虛線、per-class 長條半透明 + 數字標黃、
  legend 每一列都寫 support。r30-t1 實測**六類全部都是**（1/4/2/3/2/3）。
- **作業點不寫死 0.25**：混淆矩陣與圖牆的疊框門檻一律吃 `charts.op_conf`
  （實測 yolov8n 0.01575 vs yolov8s 0.40165，差 25 倍）。

## 9. 推論圖牆：座標基準與縮圖牆逐字相同

`.thumb`（aspect-ratio 1）+ `.ov`（`viewBox 0 0 100 100` + `preserveAspectRatio="none"`）——
跟 M1 的縮圖牆同一組 class，所以框與像素同一個基準。
`?selftest=1` **第 19 條**是真的量：用 `getScreenCTM()` 把 `.ov` 的 user 座標換算成螢幕像素，
再跟「影像內容在 `object-fit:cover` 之後實際落在哪」（契約 §11：256×256）比，**差 > 1px 就 FAIL**。
第 16 條守 CSS 比例、第 19 條守實際像素，兩條是互補的：只有比例那條時，M1 那個 blocker
（4:3 的槽裝 1:1 的圖）的 IoU 數字還是漂亮的 1.000。實測 `worstDeltaPx = 0.00`（現場真圖也是 0）。

框太多時的預設：只畫 **conf ≥ op_conf** 的框（r30-t1 的 valid：483 個預測框裡只有 **8 個**過作業點），
勾「顯示作業點以下的框」才把其餘 475 個用淡色畫出來 —— 這一格本身就是教學點。

## 10. `?selftest=1` 從 18 條 → **22 條**

19. 推論框與影像**逐像素**對齊（`getScreenCTM` 對 `object-fit:cover` 之後的內容框，差 > 1px 就 FAIL）
20. 四張圖都有 noise floor 灰帶，且 per-class 的帶寬 == noise floor、貼齊 x=0（尺度沒畫錯）
21. verdict badge 三態都渲染得出來，**只有 invalid 會擋住「進化下一輪」**
22. Δ 雜訊判定的邊界（帶內灰掉、正好等於也灰掉、超過才算數；判的是有沒有被灰掉，不是字串）

第 15 條的「未知 type」探針從 `charts.series` 換成 `sweep.trial`（M6 還沒實作），
順手多餵一包 **空 data 的 `charts.series`** —— 有 case 的 type 收到空 data 同樣不准炸。

## 11. 已驗過的（Chromium 實跑）

- `?selftest=1` → **22/22 PASS**（原有 18 條一條都沒壞）。
- `r30` 走 REST：badge suspect、四張圖都出來、`Δ +0.0101` 被灰掉標雜訊、
  per-class 六類全部標「樣本不足」、推論牆 12 張 / 8 個過作業點的框 / 15 個 GT 框。
- 走 **SSE 真事件**（`?since=526` 重播 r30 第一次 eval 的 10 筆）：`charts.series` 一包就把四張圖畫完，
  `eval.verdict` 把 badge 打成 **invalid** 並擋住進化路徑（那一次 eval 真的是 invalid：三項 check 沒過）。
- M1 主流程沒被弄壞：按鈕跑 24 張 → 24 卡 / 33 個 bbox / IoU 中位數 1.000 PASS / s01·s03 done。
- 400px 寬：三個分頁 `scrollWidth == innerWidth`，沒有任何元素超出視窗。

## 12. 兩個要記住的邊界

1. **`eval.verdict` 事件不帶 `why`**（只有 REST 的 `/verdict` 有）。缺的時候前端用「沒過的 check 名字」
   補一句，不留「已擋住：」後面空白的句子 —— 那看起來像壞掉，不像裁決。
2. **s08 的燈只在「rail 上就是這個 run」時才由 `loadEval()` 點亮**；別的 run 在跑時載入舊評分不會動它。
   stage 狀態的權威仍然是 `GET /runs/{id}` 快照，前端不自己推。

## 13. M5 整合驗收補的一刀：`autoShowEval()` 要走 `loadEval()`（2026-09-12）

一鍵跑完 r57 時實測到的：四張圖與推論牆都自己出來了，**裁決卡卻是一整排「—」**
（mAP50 —、mAP50-95 —、95% CI [—, —]、ds — · 指紋 —、train —、split —），
只有 badge 寫著 `TRUSTED`。原因是 `eval.verdict` 事件照契約 §4 只帶
`{verdict, checks, delta, significant, anchor_ok}`，**指標與 CI 只有 REST 的 `/verdict` 與 `/charts` 有**，
而 `autoShowEval()` 當時只補了推論牆（`loadPredictions`）。
現場效果就是：台上按完那顆按鈕，還要再手動點一次「載入 s08 評分」主角數字才出現。

改法一行：`autoShowEval()` 改呼叫 `loadEval(runId)`（和那顆「載入 s08 評分」鈕同一支，
內含 `loadPredictions`，不要再載一次）。實測 r59 一鍵跑完，裁決卡自己長出
`mAP50 0.6355 · mAP50-95 0.3036 · 95% CI [0.2015, 0.4906]（寬 0.2891）`、
`noise floor 2σ 0.1479（σ 0.0740）`、`Δ vs r57 +0.0000 雜訊`、`ds v1 · 指紋 78cbc26e`。

`?selftest=1` 27 → **28 條**：新那條把 `loadEval` 換成 spy，餵一包 `eval.verdict` 事件，
事件一到就必須有人拿這個 run 去打 REST。**這條會紅**：把 `autoShowEval` 退回只載推論牆，
實測 `FAIL s08 裁決一到就去載 /charts ・ /verdict（spy 收到 []）`。

## 14. 側欄說謊修正 + `source:"roboflow"`（2026-09-12）

### 14.1 實測抓到的謊（r81 一條龍截圖）

一條龍**訓練完、verdict TRUSTED** 之後，右欄還停在 M1 時代：

| 畫面上寫的 | 後端真的是 | 為什麼會這樣 |
|---|---|---|
| `TRAIN 0 / VALID 0 / TEST 0` ＋「還有 120 張沒指派，訓練前要清乾淨」 | train 48 / valid 12 / anchor 20 / sealed 40 | 前端數 `IMAGES[].split`，而 `ds.image` 的 `split` **永遠是 null**（s01 寫死，s04 指派時不補發 120 筆） |
| 類別分佈「（無資料）」 | class 表 v1、6 個命名好的類別 | 前端數 `IMAGES[].cls`，而 auto 那條路 s01 一律送 `cls:[]`（在 s01 填類別就是偷看答案） |
| `照類別分層抽樣（M2）`、`訓練設定（M3 才會動）` | M2/M3 早就跑得動 | 標籤寫死在 HTML，沒人回來改 |
| `yolov8n / 50 epoch / imgsz 640` | 這一輪真的跑的是 `yolov8n / 10 / 320`（r105 的探針甚至選了 `yolov8s`） | 那四個欄位是 M1 的預設值，**沒有任何人用過** |

**共同病因只有一個**：這三塊的真相在後端，而前端手上沒有。所以修法也只有一個 ——
**一律由後端快照驅動**（契約 §8.3 加選填 `split` / `recipe`；類別走 `GET /datasets/{ds}/classes`），
前端一個數字都不自己算。

### 14.2 三塊各自的來源

| 側欄區塊 | 來源 | s04/s02/s07 之前顯示什麼 |
|---|---|---|
| Split 狀態（`renderSplit`） | `GET /runs/{id}.split` | **「—」不是 0** —— 0 會被讀成「都沒分到」，那正是原本那個謊 |
| 類別分佈（`renderDist`） | `GET /datasets/{ds}/classes`（`class_table_version` 當快取鍵，同一版不重抓） | 「s02 還沒凍結 class 表」 |
| 訓練設定（`renderRecipe`） | `GET /runs/{id}.recipe`（源頭是 `04-experiments/<tid>/recipe.json`） | 「配方還沒凍結」——**不要寫「s05 還沒跑」**：s05 做完、s06 還沒凍的那幾十秒它就是假的 |

`applySnapshot()` 用 `'split' in s` 而不是 `s.split || …`：它同時吃 `startRun()` 那個只有五個欄位的
部份快照，用 `||` 的話上一輪的數字會留在畫面上。

### 14.3 順手刪掉的東西（保留可點的入口 ≠ 保留程式碼）

- 「照類別分層抽樣」＋兩顆匯出鈕：s04 真的在後端分層落檔了，前端再分一次只會產出**伺服器沒有的** split。
  `stratifiedSplit()` 本人留著 —— 契約 §11 assert 2 要拿它跟 Python 版逐筆比對。
- `download()`（沒有呼叫點了）、`.ratio-row` / `.err-banner` 兩段死 CSS、`renderDist` 裡重複的第二支 `esc`。
- 工具列的「指派 train/valid/test」在 `SPLIT` 存在時 disabled，`assign()` 本人也擋一次
  —— 鍵盤 `1/2/3/0` 繞得過按鈕的 disabled。

### 14.4 錯誤橫幅搬家（`#err` → `#alert`）

`ROBOFLOW_KEY_MISSING` 這種錯的全部價值就是**被讀懂**（照著做就能修好），而它原本印在左欄 11px
的小字裡，投影機上等於沒有。現在掛在 rail 底下、整列寬、14px、可關掉。`#err` 留給黃色的降級提示。

⚠️ **`.alert{display:flex}` 的特異性（0,1,0）贏過瀏覽器預設的 `[hidden]{display:none}`（0,0,1）**，
所以要補一行 `.alert[hidden]{display:none}` —— 沒有它，沒錯誤時畫面上會常駐一條空的紅框。
這個是**截圖才抓到的**：DOM 查詢 `el.hidden` 回 true，看起來一切正常。`?selftest=1` 第 37 條
改用 `getComputedStyle().display` 守它。

### 14.5 `source` 選擇器

`demo` / `roboflow` / `local`。選 roboflow 才露出 workspace / project / version
（預設 `wm811k-paasr` / `wm811k` / `3`）。**前端沒有 API key 欄位**，key 只在 server 讀 `.env`。
`$('#source').onchange()` 開頁時也跑一次：瀏覽器重整會還原 `<select>` 的選擇，只綁事件的話會變成
「source 是 roboflow，但那三個欄位看不到也改不了」。

按鈕送的是 `labels:"auto"`（人工框那條路停在 s02 的 `409 CLASS_TABLE_SCHEMA_CONFLICT`，
見契約 §10）—— 一顆必定紅燈的按鈕不是功能是陷阱。

### 14.6 `drawIou()` 的退化值（metric-auditor 留的那條）

合成 GT 的 IoU 1.000 與真人工框的 0.638 本來講**同一句**「這是整條流水線的天花板」。
現在 `degenerate`（或 `source==='synthetic'`）時直方圖轉灰 + 文案改成「退化值：…不是天花板證據」。

### 14.7 已驗過的（Chromium 實跑，不是推論）

1. `?selftest=1` **37/37**（32 → 37：側欄數字 == 快照、s04 之前是「—」、訓練設定 == 快照 recipe、
   過期標籤不存在、空橫幅不佔版面）。
2. **突變測試**：把 `renderSplit` 退回前端自算 → 立刻長回原本那個謊
   `FAIL 側欄 split 數字 == 快照（0 / 0 / 0，健康狀態「還有 3 張沒指派」）`；
   把 `（M2）` 放回標題 → 第 36 條 FAIL。
3. **r106 一條龍（瀏覽器按鈕、teaching、120 張、demo）**：done / verdict TRUSTED /
   548 筆事件 / $0.32。側欄 `48 / 12 / 40` 與 `GET /runs/r106` 的 `split` 逐欄相同，
   recipe 顯示 `yolov8n · 10 · 320 · mps · r106-t2 · done（20.2 秒）` == 快照，過期標籤 0 個。
   截圖 `run-r106-sidebar.png`。
4. **r108（瀏覽器按鈕、roboflow 真圖）**：`#rf` 三欄出現、左欄查無任何 key 欄位、
   `POST /runs` 帶 ws/pj/ver + `labels:"auto"`。
5. **沒 key**：`.env` 與 `01-raw-data/roboflow/` 一起藏起來 → `400 ROBOFLOW_KEY_MISSING` +
   繁中一句話 + **沒有留下鬼 run**（`GET /runs/r105` 回 404）。

### 14.8 `source:"roboflow"` 一條龍實跑（r108，瀏覽器按鈕，`limit=24`）

**s01→s08 八格全過、零 failed**（`recipe` `yolov8n · 10 · imgsz 320 · mps · 17.0 秒`）——
上一棒列為「完全沒跑過」的 s05–s08 真實資料路徑，這一輪驗到了：
Roboflow 是變尺寸 jpg（345×369 / 419×369），ultralytics 自己 letterbox 到 imgsz，
**沒有 imgsz/letterbox 的坑**。

側欄逐欄等於後端：`train 5 / valid 1 / sealed 8`、`unassigned 6`（右欄誠實寫「還有 6 張沒指派」）、
verdict **INVALID**。**這些都是 `limit=24` 的正確答案，不是畫面壞掉。**

⛔ 但這一跑順手翻出一顆**別人家的地雷**（dataset-truth）：
`limit=24` 時 s02 寫出的 `class_table.json` 是 **`nc:2 names:['edge-loc','donut']`**
（6 群裡 4 群因為太小被 `excluded`），而 🔒 `class_table.schema.json` 寫死
`nc` 最小 3、`names` `minItems: 3` —— **schema 被違反了，而 s04 的 freeze selfcheck 放它過去，
一條龍照樣跑完八格全綠**。契約與實作不一致時契約是對的（CLAUDE.md），
所以應該是 s02 產表時就擋（或 §12 改 schema），不是靠講師不要調小 `limit`。
本輪只在 WALKTHROUGH 標了「不要調小」，**真正的修復在 `src/autolabel/cluster.py` / `dataset.py`，
那是 dataset-truth 的目錄**。
