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
