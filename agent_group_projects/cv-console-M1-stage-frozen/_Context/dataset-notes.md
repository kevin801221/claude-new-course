# dataset-truth 決策紀錄（M1 · M2）

> 擁有者：`dataset-truth`。唯一設計真相是 `_Context/DESIGN.md`，欄位形狀以 `_Context/api-contract.md` 🔒 為準。
> 這份記的是**做了什麼決定、為什麼、實測數字是多少** —— 不是教學文件，是之後有人問「為什麼是 0.6 不是 0.9」時的證據。
> 最後更新：2026-09-11（M2 完成：class 真相表凍結 + dataset 落檔）

---

## 1. 6 條 bbox 精修規則的幾何定義

出處是 `_Context/wafer-basics.md` 那 6 條演算法草案。DESIGN 把它們從「類別專用的產框法」改成
**群命名後的精修規則** —— M2 的 KMeans + LLM 命名把每一群對應到一條規則之後才會被指派。

**M1 只有 `cc_bbox` 一條，而且是直接寫在主流程裡**（契約 §8.6 寫死 `rule="cc_bbox"`）。
其餘五條**目前不存在於程式碼裡，只存在於下面這張表**：M1 沒有類別，沒有類別就沒有
「這一群該用哪條規則」，寫下去就是 5 條永遠跑不到的死碼 ＋ 一個只有一個實作的 registry ＋
一組替死碼背書的斷言。M2 的 KMeans + LLM 命名產出群之後，連同「群 → 規則」的綁定一起實作，
那時每一條才寫得出真的會失敗的檢查。這張表就是那天的規格，直接照抄。

| 規則 | 對應型態 | 幾何定義（都以「這個連通分量的遮罩」為輸入） | 旋鈕 |
|---|---|---|---|
| `cc_bbox` | center / loc | 分量的外接框，不做任何修正 | — |
| `ring_annulus` | donut | 取 `ring_lo`–`ring_hi` 半徑環帶與分量的交集外接框；交集為空時退回分量外接框 | `ring_lo=0.30` `ring_hi=0.80` |
| `edge_local` | edge-loc | 只留半徑 > `edge_frac`·R 的部分，再取其中**最大一塊**的外接框；分量不靠邊回 `None` | `edge_frac=0.75` |
| `edge_ring` | edge-ring | 用**整張遮罩**（不只這個分量）半徑 > `edge_frac`·R 的全部像素取一個框 —— 外圈斷斷續續也算同一圈 | `edge_frac=0.75` |
| `pca_axis` | scratch | 對分量像素做 PCA，沿主軸把範圍乘上 `pca_pad` 再投影回影像座標取軸對齊外接框 | `pca_pad=1.15` |
| `skip` | random / none | 不畫框（單點雜訊不成型態） | — |

半徑 R 與圓心**從像素推**（`inside_mask()` 給的晶圓內遮罩取質心與最大半徑，兩行），
不是硬編 128,128 —— 換成 Roboflow 的真實影像時晶圓不會剛好置中。M1 只需要這張遮罩
（缺陷只能長在晶圓上），圓心與半徑等規則回來那天再推。

係數本身要是**可被 patch 的旋鈕**（`RefineParams` 是 frozen dataclass，
`POST /label/auto` 的 `refine_params` M2 起會餵進來）。這是刻意的：OFAT 每輪只准改一個鍵名，
`pca_pad` 這種東西必須是「一個鍵」而不是散在程式裡的魔術數字，不然 arbiter 沒東西可改。
M1 的 `RefineParams` 只留真的被讀的五個鍵（`redness` / `inside_lum` / `open_size` /
`min_area_px` / `lowconf`）；上表那四個係數跟規則同時進來，不先放著當裝飾。

---

## 2. auto-bbox 對合成 GT 的實測 IoU

量法（`uv run python -m src.autolabel.geometry`，120 張全跑，0.3 秒）：
**每個 GT 框取所有 auto 框裡的最佳 IoU，漏掉的 GT 記 0** —— 刻意用嚴格的算法，不挑對自己有利的配對。

| 型態 | GT 框數 | IoU 中位數 | 最小 |
|---|---|---|---|
| scratch | 30 | 1.0000 | 1.0000 |
| particle | 65 | 1.0000 | 1.0000 |
| ring | 30 | 1.0000 | 1.0000 |
| edge-loss | 30 | 1.0000 | 1.0000 |
| **全部** | **155** | **1.0000**（平均 1.0000） | 1.0000 |

直方圖（0→1 分 10 格）：`[0,0,0,0,0,0,0,0,0,155]`。auto 框 155 個 = GT 框 155 個，120 張的分量數**逐張相同**。
其他實測：`defect_ratio` p50 0.0354（min 0.0042 / max 0.1117）、每張框數 1 個 ×99 / 2 個 ×7 / 3 個 ×14、
`conf` 最小 0.185（4 張圖觸發 `label.lowconf`）、單張 `label_image` 2.7 ms。

### 為什麼是 1.0 而不是 0.8 —— 這個數字要怎麼讀

合成 GT 與抽取器**共用同一份遮罩定義**：GT 是「畫上去的缺陷 die」的連通分量外接框，
auto-bbox 是「從像素還原出來的缺陷 die」的連通分量外接框。門檻沒問題、顏色可分、
die 是 4×4 px 的實心方塊，所以還原是逐像素精確的 —— 1.0 是這條路**應有的**答案，不是巧合。

**所以這條 assert 抓的不是「模型好不好」，是參數迴歸。** 實測敏感度：

| 參數 | 值 | auto 框數 | IoU 中位數 |
|---|---|---|---|
| `min_area_px` | 16 | 942 | 1.0000 ← 中位數沒掉，但多了 6 倍假框 |
| `min_area_px` | **64（預設）** | **155** | **1.0000** |
| `min_area_px` | 256 | 147 | 1.0000（最小值已掉到 0） |
| `min_area_px` | 1024 | 66 | 0.0000 ← assert 在這裡才會紅 |
| `redness` | 90 | 116（前 40 張） | 0.8229 ← 強度弱的 die 被切掉，框碎了 |
| `redness` | 130 | 0 | 0.0000 |

`min_area_px=16` 那一列是關鍵：**IoU 中位數完全看不出問題，但框數從 155 爆到 942**。
所以 `geometry.py` 的自檢除了 IoU 還加了第二條 assert：`auto 框數 <= GT 框數 × 1.2`。
只看 IoU 會漏掉「把 263 顆單點壞 die 全當成缺陷」這種錯法（前 40 張實測：
去雜點前 317 個分量 → 去雜點後 54 個）。

### 兩把不同的尺（契約 §11）

| 來源 | 事件 `source` | 門檻 | 意義 |
|---|---|---|---|
| 合成 GT（M1） | `"synthetic"` | **0.6** | 程式生成的框，鬆了就是演算法有問題 |
| Roboflow 人工 anchor（M3 起） | `"roboflow_anchor"` | **0.4** | 低於就停在 s03 宣告「問題在標註不在模型」 |

M1 的 1.0 **不能拿來宣稱流水線的天花板**。真正的天花板要等 M3 拿 40 張人工 anchor 量出來，
那時候 IoU 才會掉下來，直方圖才會有形狀。這張圖在 M1 的作用是「證明這條路通了」，
不是「證明標得準」——教材寫的時候要講清楚，否則學生會以為 auto-label 已經解決了。

### 形態學去雜點：誰在做事

`despeckle()` = `binary_opening`（結構元素 `open_size=3`）→ 面積門檻 `min_area_px=64`。
實話：4 px 的 die 尺度下**真正在做事的是面積門檻**（3×3 開運算對 4×4 實心方塊不痛不癢，
侵蝕成 2×2 再膨脹回 4×4）。開運算留著是為了抗鋸齒毛邊與對角細橋 —— M3 換真實影像時像素會碎很多。
連通分量用 8-connectivity（`np.ones((3,3))`），否則斜著走的 scratch 會被切成好幾段。

---

## 3. 三套 class 表的裁決

repo 裡同時存在三套，M1 之前沒有人負責收斂：

| 出處 | 內容 | 裁決 |
|---|---|---|
| `_Context/wafer-basics.md` 範本 | 6 類：`center / donut / edge-loc / edge-ring / loc / scratch` | **降為 M2 LLM 命名的候選字彙**，不是真相表本身 |
| `scripts/prepare_dataset.py` | 硬編 `CLASS_NAMES = {0: "Donut"}`（`nc=1`） | **作廢**。它是「Roboflow 那份剛好只有 Donut」的副作用，不是設計 |
| 既有前端 demo | 4 類：`scratch / particle / ring / edge-loss` | **降為合成型態標籤**（`gt.json` 的 `shape`），**不是類別真相** |

**唯一 class 真相表 = `_Context/class_table.json`，由 M2 的 KMeans + 一次 LLM 命名產生，欄位照
`class_table.schema.json`（console-owner 凍結，dataset-truth 只填值）。**

理由：這三套沒有一套是「看著這批資料算出來的」——6 類是抄 WM-811K 的論文分類，
`nc=1` 是資料剛好長那樣，4 類是前端隨手寫的示範。DESIGN 要的是「類別來自非監督分群 +
一次可稽核的命名」，那三套任何一套被當成真相，都等於偷渡了一份沒有證據的先驗。

M1 的實作結果：`boxes[].cls = -1`、`class_table_version = null`、
`/datasets/{ds}/images` 的 `annotations.classes` 一律單鍵 `{"unclassified": n}`。
**M1 全系統沒有任何一個字串型態的類別名。**

---

## 4. `valid` vs `val` 目錄名的裁決

| 出處 | 用字 |
|---|---|
| 前端 `stratifiedSplit` / split 統計 / `#n-valid` | `valid` |
| `scripts/prepare_dataset.py` 的 `write_data_yaml()` | `val`，而且 `data.yaml` 裡**有 `test`** |

**裁決：一律 `valid`（前端形狀勝）。** 連帶三件事（M2 的 s04 落檔一起做）：
1. `prepare_dataset.py` 的 `write_data_yaml()` 改寫成 `names` 是 **list** 不是 dict、`path` 用相對路徑。
2. **`data.yaml` 只寫 `train` / `valid`，`test` 根本不出現** —— 封印 test 的第一道鎖是「訓練設定裡看不到它」。
3. 目錄 `02-dataset/{images,labels}/{train,valid}`。

理由：改前端要改 DOM id、split 統計、`?selftest=1` 的 assert 與所有配色 key；
改後端只要改一個 `write_data_yaml()`。Ultralytics 兩種都吃，所以這純粹是「誰改的成本低」。
（DESIGN「決定什麼」那段已經先寫死這條，這裡只是把理由記下來。）

---

## 5. 零人工標註的實際邊界（這條線很容易不小心跨過去）

`01-raw-data/demo/gt.json` 有兩種東西，待遇完全不同：

| 欄位 | 誰能讀 | 為什麼 |
|---|---|---|
| `boxes` | `run_autolabel()` 量 IoU、`selfcheck.py` | 這是**尺**。量尺可以看 —— 不看就沒有天花板證據 |
| `shape` | 只有 `selfcheck.py` 與 M2 的命名事後驗證 | 這是**答案**。餵進 auto-label 就是偷看，整個「AI 自己做 labeling」就是假的 |

程式上的落實：`run_ingest()` 寫 manifest 時只抄 `id` / `name` 兩個欄位，
`shape` 連進 `01-raw-data/datasets/<ds_id>/manifest.json` 的機會都沒有，
所以任何 API 回應都不可能帶到它（契約 §11 明令不准進 API 回應）。

---

## 6. M1 的落檔佈局（M2 接手前先知道）

```
01-raw-data/demo/wafer_0001.png … wafer_0120.png   契約 §11 凍結：256×256 RGB 真點陣
01-raw-data/demo/gt.json                            免費精確 GT（seed 42）
01-raw-data/datasets/<ds_id>/manifest.json          這個 ds 收了哪些圖（images 快照的來源）
01-raw-data/datasets/<ds_id>/labels.json            auto-label 的框（每 20 張落一次盤）
```

`demo/` 是**單一 seed 的成品，最後寫的人贏**：契約把 `GET /images/demo0007` 凍成
`01-raw-data/demo/wafer_0007.png`，所以換 seed 會整批重畫、連舊 run 的縮圖也跟著換。
`ensure()` 因此永遠產滿 120 張（不讓 `limit=12` 把目錄截短）。真要每個 run 的影像不可變，
得把 id → 路徑的規則改成帶 ds_id，那是契約 §12 的破壞式變更，M1 不值得。

---

## 7. 留給 M2 的東西

- `src/autolabel/cluster.py`（描述子 + KMeans + montage）還沒寫 —— M1 沒有類別是**設計**，不是缺工。
- 5 條精修規則沒有呼叫點，等群命名後由 arbiter 綁定。
- 近重複（pHash）、group leakage、GT 三分（anchor 40 / sealed-test 80 / unused）都還沒做：
  M1 的資料是程式生成的，沒有 Roboflow 那 409 張就沒有 anchor 可切。
- `src/autolabel/split.py` 已經可用（與前端逐筆一致，見下），但**還沒有人呼叫它** ——
  `ds.split` 是 s04 的事件，s04 在 M1 是 `skipped`。

---

## 8. 怎麼重跑這裡的每一個數字

```bash
uv run python scripts/gen_demo_wafers.py      # 120 張 PNG + gt.json（同 seed 逐位元組相同）
uv run python -m src.autolabel.geometry       # IoU 中位數 / 分佈 / 6 條規則的簽章自檢
uv run python -m src.autolabel.split          # 與前端 stratifiedSplit 逐筆比對（黃金值由 node 產）
```

`split.py` 的黃金值不是自己算的，是把 `claude-design_claude-code/prototype/index.html`
第 180–209 行那段 `mulberry32` + `stratifiedSplit` 原封不動丟給 node 跑出來的：

```bash
sed -n '180,209p' <prototype>/index.html > parity.js
cat >> parity.js <<'JS'
const FIXTURE = Array.from({length:40},(_,k)=>{const i=k+1;
  return {id:'demo'+String(i).padStart(4,'0'),
          cls: i%7===0 ? [] : [['scratch','particle','ring','edge-loss'][i%4]]};});
const r = mulberry32(42);
console.log(JSON.stringify([r(),r(),r(),r(),r()]));
console.log(JSON.stringify(stratifiedSplit(FIXTURE,{train:70,valid:20,test:10},42)));
JS
node parity.js
```

實測：`mulberry32(42)` 前 5 個值與 JS 浮點逐位相同（第一個 `0.6011037519201636` ——
自己憑感覺寫的 32 位元移植第一版就錯在這個數字上，所以這條一定要對 node 驗）；
40 筆 fixture 的 split 逐筆相同，`{train: 27, valid: 9, test: 4}`。

移植時會錯而且**不會報錯**的四個地方（照抄前端，不要「改良」）：
1. 主類別取 `cls[0]`，沒有類別歸 `__none__`
2. 分層鍵**字串排序**後才處理
3. 每層 Fisher–Yates 用**同一個** `mulberry32(seed >>> 0)` 連續往下取，不是每層重新 seed
4. 最大餘數法，餘數大的先拿；同餘數保持 `train, valid, test` 原順序（JS `sort` 與 Python `sorted` 都是穩定排序）

---

# M2（class 真相表 + dataset 凍結）

新增檔案：`src/autolabel/cluster.py`（描述子 + KMeans + montage + 一次 LLM 命名）、
`src/autolabel/freeze.py`（GT 三分 + 分層抽樣 + 落檔 + 硬閘門）、`scripts/run_m2.py`（一鍵實跑）。
`src/app/routers/dataset.py` 接上三支原本回 501 的端點（`/label/auto mode:"cluster"`、
`/datasets/{ds}/classes`、`/datasets/{ds}/freeze`）。`/datasets/{ds}/report` **仍是 501**（沒做）。

## 9. GT 三分的算術：40 / 80 是上限，不是固定值

DESIGN 寫 `anchor 40 / sealed-test 80 / unused`，那是對著 Roboflow 那 409 張說的。
**離線 demo 只有 120 張：40 + 80 = 120，train+valid 會剩 0 張** —— 那不是封印 test，那是沒有資料集。

裁決：寫成一條會隨分母縮放、但在大分母時**剛好還原 DESIGN 數字**的公式（`freeze.gt_partition()`）：

```
anchor      = min(40, n // 6)
sealed_test = min(80, n // 3)
pool        = 其餘
```

| n | anchor | sealed_test | pool（train+valid） |
|---|---|---|---|
| 120（離線 demo） | 20 | 40 | 60 |
| 240 | 40 | 80 | 120 |
| **409（DESIGN 的 Roboflow）** | **40** | **80** | **289** ← 與 DESIGN 逐字相同 |

切法是**照 id 順序**（`demo0001` 起）不是隨機：合成資料的 4 種型態是輪流發的，照順序切每份自動型態均衡，
而且同一個 ds 重跑一定切到同一批 —— sealed test 換一批就等於偷偷換考卷。
`freeze._selfcheck()` 兩條 assert 直接釘住上面那張表的第一列與第三列。

**anchor / sealed-test 的標註用免費精確 GT 的框，train/valid 用 auto-label 的框**（類別一律來自 class 表）。
anchor 是每輪量漂移的尺，尺不能跟著被量的東西一起動。

## 10. 分群實測（120 張合成圖，seed 42）

`uv run python -m src.autolabel.cluster`（0.4 秒）：

| k | 3 | 4 | 5 | **6** |
|---|---|---|---|---|
| silhouette | 0.3791 | 0.3881 | 0.3976 | **0.4208 ← 自選** |

| cluster | n | 主型態（gt 的 shape，事後驗證才讀） | 一致率 |
|---|---|---|---|
| 0 | 11 | scratch | 0.82 |
| 1 | 27 | scratch | 0.78 ← 最低 |
| 2 | 19 | ring | 0.95 |
| 3 | 18 | particle | 1.00 |
| 4 | 15 | ring | 0.80 |
| 5 | 30 | edge-loss | 1.00 |

**要誠實講的兩件事**：
1. silhouette 在 3→6 單調上升，**選到的是範圍上界**。也就是「k=6」是 `K_RANGE` 的邊界在說話，
   不是資料結構在說話。範圍是 schema 凍死的 3–6（`nc` 的上下界），要放寬得走契約 §12。
2. 合成資料的 4 種型態**不等於**它自然的分群結構：particle 的 1–3 顆團塊會依半徑落到不同群
   （靠中心的像 center、靠邊的像 edge-loc），scratch 依長度分成兩群。一致率 0.78 是真的，
   不是分群壞掉 —— 它在說「型態標籤不是唯一合理的切法」。

**負向測試**（證明那條 assert 不是裝飾）：把 `feature_vector()` 砍成只剩 `defect_ratio` 一維，
最低一致率掉到 **0.50**，assert 紅。

## 11. LLM 命名：一次呼叫、可稽核、失敗要降級

指令（`cluster.call_llm()`，全程只呼叫**一次**）：

```bash
claude -p "<montage 絕對路徑 + 每群統計>" \
  --output-format stream-json --verbose --max-turns 4 \
  --model sonnet --strict-mcp-config --mcp-config '{"mcpServers":{}}' --allowedTools Read
```

三個旗標都是實測逼出來的，不是抄的：

| 旗標 | 不加會怎樣（實測） |
|---|---|
| `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` | 本機 9 個 MCP server 的工具定義全進 system prompt，光工具定義 18K cache token，一次命名 **$0.19** 就沒了。另外：值寫 `{}` 會被擋成 `Invalid MCP configuration: mcpServers: Invalid input` —— 第一次實跑就是這樣掉進降級路徑的 |
| `--model sonnet` | 預設 opus。同一個 prompt 實測 opus $0.134 / sonnet $0.077（空跑），teaching preset 整輪 `usd_cap` 只有 0.5 |
| `--allowedTools Read` | montage 讀不到，只能靠數字命名。路徑要給**絕對路徑**，Read 不吃相對路徑 |

命名成本實測（同一份 6 群統計 + 6 張 montage）：

| 模型 | output token | 費用 | 牆鐘 | 品質 |
|---|---|---|---|---|
| `sonnet`（預設） | 16,497 | **$0.3274** | 178 秒 | 6 群全部對得上型態，理由每則都引數字 |
| `haiku`（`CV_LLM_MODEL=haiku`） | 7,831 | $0.0961 | 66 秒 | 名字合法但語意錯得明顯（ring 群叫 center、particle 群叫 edge-loc） |

一次 $0.33 是 teaching preset `usd_cap`（0.5）的 **65%**。M6/M7 的多輪討論要嘛換 haiku、
要嘛把 cap 調高 —— 這是 experiment-arbiter 的預算問題，數字先記在這裡。

**記帳**：`llm_calls` / `cost_usd` 從 `result` 框的 `usage` / `total_cost_usd` 累加進
`runs/<run_id>/state.json` 的 budget，再經 `registry.set_status()` 投影到 `runs/runs.jsonl`
（**不繞過 registry 自己寫帳本**）。token 明細（in / out / cache_w / cache_r / model / duration_ms）
`runs.jsonl` 沒有欄位（DESIGN 的 schema 只有 `llm_calls` 與 `cost_usd`），落在
`01-raw-data/datasets/<ds>/naming.json`；要進帳本得走契約 §12 加欄。

### 降級路徑（`naming: "fallback"`）

觸發條件：找不到 CLI / timeout / 非法 JSON / 用了詞彙表外的字 / 名字撞名 / 少了某一群。
**不重試、不整條線掛掉**，改用 `_mechanical_name()`（純門檻，同輸入同輸出）。

⚠️ **降級的名字不能是 `cluster_0..k`**（原始指示如此，但做不到）：
`class_table.schema.json` 把 `names` 凍成 `center/donut/edge-ring/edge-loc/scratch/random`
六個詞（console-owner 凍結，M2 只填值），寫 `cluster_0` 會產出一份不合 schema 的 class 表 ——
`freeze` 的 `class_table_match` 會 FAIL、`data.yaml` 的 names 跟著髒掉。
降級路徑的意義是「線不斷」，不是「線斷在下一站」。所以降級照樣從詞彙表挑，
痕跡記在三個地方：`naming.json` 的 `naming` 欄位、`class.table.frozen` 事件的 `text`、
以及每則 rationale 開頭的 `[fallback 機械命名]`（撞名時還會寫「首選 X 已被前一群用掉」）。

離線驗證：`uv run python scripts/run_m2.py --no-llm`（把 `CV_LLM_CMD` 指到不存在的指令，
走完整條降級路徑，2.1 秒、零 token）。

## 12. `support` 欄位的時間差

schema 說 `support` 是「這一群在 train split 的 bbox 實例數」，但 class 表在 **s02** 就凍結，
**s04** 才有 split —— 先有雞還是先有蛋。裁決：s02 填**全 ds 的實例數**，train 的實例數看
`ds.split` 事件與 `02-dataset/labels/train/`。不在 s04 回頭改 class 表，因為
「一發出的 version 不准改內容」（schema 明文）比欄位描述更硬。

## 13. `data.yaml` 的兩個坑

1. **key 用 `val`、目錄叫 `valid`**。§4 的裁決是「目錄一律 valid」，但 ultralytics 只認 `val:` 這個 key。
   兩者不衝突：`val: images/valid`。
2. **刻意不寫 `path:`**。ultralytics 的相對 `path` 是相對到它自己的 `DATASETS_DIR`，
   不是相對到 yaml 所在目錄（`ultralytics/data/utils.py` 623–625 行：
   `path = Path(extract_dir or data.get("path") or Path(data.get("yaml_file","")).parent)`，
   而且第 624 行是 `if not path.exists() and not path.is_absolute()` —— 寫 `path: .` 會因為 `.` 永遠
   存在而靜默解析成呼叫者的 cwd）。省略 `path` 時才會用 `Path(yaml_file).parent`，也就是 `02-dataset/`。

## 14. M2 的落檔佈局

```
01-raw-data/datasets/<ds>/descriptors.json    每張圖的幾何描述子
01-raw-data/datasets/<ds>/clusters.json       image_id → cluster_id
01-raw-data/datasets/<ds>/class_table.json    class 真相表（per-ds，真相）
01-raw-data/datasets/<ds>/naming.json         命名是 llm 還是 fallback + token/USD 明細
01-raw-data/datasets/<ds>/splits.json         image_id → train/valid/anchor/sealed_test
01-raw-data/datasets/<ds>/montage_c{N}.png    每群 9 張代表圖
_Context/class_table.json                     最後一次凍結的 class 表（schema 指名的全域路徑）
02-dataset/{images,labels}/{train,valid}      YOLO 訓練集（auto-label 的框）
02-dataset/{images,labels}/anchor             anchor（GT 的框，不進 data.yaml）
02-dataset/data.yaml                          只有 train / valid
03-sealed-test/{images,labels} + manifest.json  封印 test（GT 的框，不進 data.yaml）
```

實跑一次（`uv run python scripts/run_m2.py`，preset=real）：
120 張 → k=6 / silhouette 0.4208 → class 表 v1 `[center, scratch, donut, random, edge-loc, edge-ring]`
（命名 llm，$0.3274）→ GT 三分 20/40/60 → train 48 / valid 12 → selfcheck 6/6 PASS，總計 181 秒
（其中 178 秒是那一次 LLM 呼叫；`--no-llm` 是 2.1 秒）。

## 15. 留給 console-owner / M3 的四件事

1. **`drive_run()` 還沒串 s02/s04**。那顆按鈕目前跑 s01→s03 就把 run 標 `done`，
   而 `done` 的 run 再打 `/label/auto` 會回 `409 RUN_NOT_LIVE`。M2 的四個 stage 要進那顆按鈕，
   得由 console-owner 在 `drive_run()` 裡加兩段（呼叫 `dataset.run_cluster()` 與 `dataset.run_freeze()`）。
   在那之前走 `scripts/run_m2.py`（契約 §8 明文留的 curl 單步路徑）。
2. **缺一個錯誤碼**。「freeze 時還沒有 class 表 / 還沒有 auto 框」目前借用 `409 SELFCHECK_FAILED`，
   語意是歪的。建議走契約 §12 加一個 `409 DS_NOT_READY`。
3. **`registry` 的帳本沒有 token 欄位**（見 §11）。
4. `/datasets/{ds}/report` 還是 501（`anchor_iou_median` 那張直方圖在 M3 有真 anchor 才有意義）。
5. **背景 s02 掛掉時 SSE 看不到**：`POST /label/auto {mode:"cluster"}` 是 202 + 背景任務，
   失敗只會把 `stages.s02` 標成 `failed`（快照看得到）+ server log 一行，**不會有事件**——
   因為 `stage.failed` 的前綴屬於 console-owner，`bus.append_event()` 會擋下越權（契約 §4）。
   等 `drive_run()` 串進來就自然解決（driver 本來就 catch → 推 `stage.failed`）。
