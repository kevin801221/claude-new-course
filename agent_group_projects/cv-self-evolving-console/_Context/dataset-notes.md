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

3. **註解裡一個 `test` 字都不准出現**。封印 test 第一道鎖的驗收條件是
   `grep -i test 02-dataset/data.yaml` 要**空的**，而 grep 不分 key 與註解 —— 原本那段善意說明
   （「test 不在這裡」「sealed-test 在 03-sealed-test/」「POST /api/v1/final-test」）
   三處字面全都被 grep 命中，閘門於是永遠是紅的、久了就沒人再跑它。現在註解改寫成不帶那個字，
   `freeze.selfcheck()` 的 `no_test_in_data_yaml` 也改成看**整份原文（含註解、不分大小寫）**，
   跟閘門量同一件事 —— 以前它只看非註解內容，所以「註解髒了」它是看不見的。

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
02-dataset/data.yaml                          只有 train / valid（註解也不准出現 test）
02-dataset/dataset_version.json               ds_version + 內容指紋 + 指紋→版本全表
03-sealed-test/{images,labels} + manifest.json  封印 test（GT 的框，不進 data.yaml）
```

實跑一次（`uv run python scripts/run_m2.py`，preset=real）：
120 張 → k=6 / silhouette 0.4208 → class 表 v1 `[center, scratch, donut, random, edge-loc, edge-ring]`
（命名 llm，$0.3274）→ GT 三分 20/40/60 → train 48 / valid 12 → selfcheck 6/6 PASS，總計 181 秒
（其中 178 秒是那一次 LLM 呼叫；`--no-llm` 是 2.1 秒）。

## 15. 留給 console-owner / M3 的四件事

1. ~~**`drive_run()` 還沒串 s02/s04**~~ ✅ 已由 console-owner 串好：那顆按鈕現在跑
   s01→s03→s02→s04→s05→s06+s07→s08 一條龍，每段真的做完才標 done。`scripts/run_m2.py`
   仍然留著當 curl 單步教學路徑（契約 §8 明文），而且是唯一不需要 GPU 的那條。
2. **缺一個錯誤碼**。「freeze 時還沒有 class 表 / 還沒有 auto 框」目前借用 `409 SELFCHECK_FAILED`，
   語意是歪的。建議走契約 §12 加一個 `409 DS_NOT_READY`。
3. **`registry` 的帳本沒有 token 欄位**（見 §11）。
4. `/datasets/{ds}/report` 還是 501（`anchor_iou_median` 那張直方圖在 M3 有真 anchor 才有意義）。
5. **背景 s02 掛掉時 SSE 看不到**：`POST /label/auto {mode:"cluster"}` 是 202 + 背景任務，
   失敗只會把 `stages.s02` 標成 `failed`（快照看得到）+ server log 一行，**不會有事件**——
   因為 `stage.failed` 的前綴屬於 console-owner，`bus.append_event()` 會擋下越權（契約 §4）。
   等 `drive_run()` 串進來就自然解決（driver 本來就 catch → 推 `stage.failed`）。

---

## 16. `ds_version` 不再說謊：版本號綁內容指紋

### 病灶

`ds_version` 以前是 `registry.create_run()` 寫死的 `"v1"`，**freeze 幾次都不會動**。
metric-auditor 實測 2026-09-12 00:48 有人重 freeze，valid 的 GT 框從 13 變 15，
版本欄位毫無反應（`runs.jsonl` 裡三個 run 也都還是 `v1`）——
「跨版本不可比」那道鎖因此是**死的**：同一個 `v1` 底下躺著兩份不同的資料，
而 `04-experiments/noise-floor/v1.json` 只對其中一份有效。
（metric-auditor 的指紋補丁擋住了「拿錯雜訊帶」，但版本號本身還在說謊，見 `eval-notes.md` §4。）

### 裁決：一個指紋一個號，同一個號永遠指同一份內容

`freeze.assign_version()`（`src/autolabel/freeze.py`）在**落檔完成後**算內容指紋，
查 `02-dataset/dataset_version.json` 的 `versions`（指紋 → 版本全表）：查得到就沿用那個號，
查不到才發下一號。指紋演算法與 `metrics.dataset_fingerprint()` 逐位元組相同
（`labels/{train,valid}` 的檔名 + 內容 sha1 前 16 碼）。

```json
{"ds_version": "v1", "ds_fingerprint": "78cbc26eefc2a816", "frozen_at": "...",
 "versions": {"78cbc26eefc2a816": "v1", "cc4387bfc9d335d3": "v2"}}
```

**為什麼不是「每次 freeze 一律 +1」**（原始指示如此，這裡是刻意偏離，理由在此）：
版本號的用途是當雜訊帶與跨輪 Δ 的鍵（`noise-floor/<ds_version>.json`）。
內容一字不差卻換號 = 每按一次那顆按鈕就作廢一次雜訊帶、白燒 3 次訓練（實測 65 秒），
而且「一發出的版本不准改內容」會被稀釋成「版本號只是流水號」。
教室裡每跑一次都是同一批 120 張 demo wafer，流水號等於**每堂課多燒一分鐘 GPU 去確認同一件事**。

**為什麼不是「跟上一筆比、不同就 +1」**（第一版就是這樣寫的，被自己的檢查打掉）：
v1 → 改資料 v2 → 改回來就變 v3，下次真的改資料又發 v2 ——
**同一個號指到兩份不同的內容**，而 `noise-floor/v2.json` 還躺在那裡等著被誤用。
全表查得起（一次 freeze 幾十個 byte），就不留這個坑。

### 誰跟著改

- `POST /datasets/{ds}/freeze` 回應多兩欄 `ds_version` / `ds_fingerprint`，
  並把兩者寫進 `runs/<run_id>/state.json`（`registry.set_status(...)`，**不繞過 registry**）。
  `runs.jsonl` 本來就有 `ds_version` 欄位，現在它終於會動。
  契約 §9 凍的是「路徑 + 方法 + 擁有者，不是 body」，所以這是加欄不是改契約。
- `ds.written` 事件的 `text` 多印版本與指紋（`data` 欄位沒動）。
- `scripts/run_m2.py` 的 s04 那段一起印出來（curl 單步教學看得到這條鎖）。

### 實測（2026-09-12，走真的 HTTP 端點，ds 複製自 ds51）

| # | 動作 | `ds_version` | 指紋 | `grep -i test 02-dataset/data.yaml` |
|---|---|---|---|---|
| 1 | 原始 freeze | `v1` | `78cbc26eefc2a816` | 空的（exit 1） |
| 2 | 改一張 label 的框寬（demo0120 的 w 0.3125 → 0.15625）再 freeze | **`v2`** | **`cc4387bfc9d335d3`** | 空的 |
| 3 | 把 label 改回原樣再 freeze | **回到 `v1`** | `78cbc26eefc2a816` | 空的 |
| 4 | 什麼都不改再 freeze 一次 | `v1`（不動） | 不動 | 空的 |

每次 freeze 0.04–0.05 秒、s04 selfcheck 6/6 PASS、train/valid 48/12。
`state.json` 與 `runs.jsonl` 的 `ds_version` 逐次跟著動（實測 r54）。
現在 `02-dataset` 的指紋是 `78cbc26eefc2a816` —— 與 `noise-floor/v1.json` 記的那枚相同，
所以這次實驗**沒有**作廢既有的雜訊帶。

### 會失敗的檢查

`uv run python -m src.autolabel.freeze`（`_selfcheck()` 第 1.5 段）：連續 5 次 freeze 驗
「沒變 → 同號同指紋」「變了 → 換號換指紋」「改回來 → 拿回舊號」，
最後拿真的 `02-dataset` 讓 `freeze.labels_fingerprint()` 與 `metrics.dataset_fingerprint()`
各算一次比對（兩支演算法走鐘就紅）。
實證它會紅：把 `assign_version` 換成「永遠回 v1」（＝修之前的行為）→
`AssertionError: 資料變了版本號要跟著變，收到 v1`。

### 還留著的一個洞（要 console-owner 收）

`registry.create_run()` 仍然寫死 `"ds_version": "v1"`。**跑到 s04 的 run 沒問題**（freeze 會覆蓋），
但 M6 的最小重跑如果跳過 s04（patch 只動 `train.*`，`rerun_from` 從 s07 起），
那個 run 的帳本列會掛著 `v1` 而磁碟上可能已經是 `v2`。
正解一行：`create_run()` 讀 `02-dataset/dataset_version.json` 的 `ds_version` 當初值
（沒有這個檔才退回 `"v1"`）。`registry.py` 是共同地基，不是 dataset-truth 能改的。

附帶一筆：驗證過程中的測試 run `r52` 在帳本留了 `ds_version=v2/v3` 三列
（那是第一版「跟上一筆比就 +1」的產物，也是它被打掉的原因）。r52 沒有任何指標、
不進任何比較；`versions` 全表裡沒有 `v3`，所以未來的 v3 是乾淨的。

---

## 17. `source="roboflow"` 真實資料（2026-09-12）

`_Context/dataset-notes.md` 之前寫的「M3 起的 Roboflow 人工 anchor」這一輪落地了：
`src/autolabel/roboflow_src.py` + `dataset.py` 的四個 seam 接上 `source="roboflow"`。
**實跑驗過**（WM-811K，workspace `wm811k-paasr` / project `wm811k` / v3 / format `yolov8`）：
409 張、413 個人工框、`nc:1 names:['Donut']`、下載 8.8 MB / 4.3 秒。

### 17.1 抄了什麼、偏離了什麼

抄 `agent_group_projects/computer-vision-wafer-detection/scripts/download_dataset.py`
（驗證過能跑）：四個參數一個字沒改，「指定版本不存在 → 退回 `max(version_ids)`」的 fallback 照搬
（退版會寫進 `ds.total` 的 text，不是靜靜換一份資料）。

**偏離一處**：那支用 `roboflow` 套件，這支用 stdlib `urllib` + `zipfile`（**零新依賴**）。
理由不是品味，是硬規矩「真實資料那條路只准有一個啟動指令」：`roboflow` 是新依賴，
而 `pyproject.toml` 是 console-owner 的共同地基 —— 加了就多一個 `--extra`（或 `--with`），
而 `--extra train` 這個坑已經害過講師一次。零依賴的結果是**啟動指令一個字都沒變**：

```bash
uv run --extra train uvicorn src.app.main:app --reload --reload-dir src   # 和之前完全一樣
curl -sX POST localhost:8000/api/v1/datasets/ingest \
  -H 'content-type: application/json' \
  -d '{"run_id":"rN","source":"roboflow","limit":500}'                     # 人工框（預設）
```

套件在做的就是三個 HTTP 呼叫，實測回應都寫在 `roboflow_src` 的 docstring 裡：
1. `GET api.roboflow.com/{ws}/{pj}?api_key=` → 專案資訊 + `versions[]`（版本 fallback 的依據）
2. `GET api.roboflow.com/{ws}/{pj}/{v}/yolov8?api_key=` → `export.link`（簽名 zip 連結）
3. `GET <export.link>` → zip → `extractall`

課堂節奏提醒：`preset="teaching"` 是每張停 0.12 秒，409 張 = s01 約 49 秒 + s03 約 49 秒。
要現場快跑就開 `preset:"real"`（不停頓，實測 s01 約 2 秒、s03 human 約 4 秒）。

`python-dotenv` 沿用（`load_dotenv`）—— 它是 `uvicorn[standard]` 的既有相依，
`uv.lock` 查得到，base `uv sync` 就有，所以不算新依賴。

### 17.2 兩種標註模式（`labels`）

`POST /datasets/ingest` 多一個**選填** `labels`（不帶 = 舊行為），決定一次就寫進
`manifest.json`，s02/s03/s04 一律讀那裡 —— 三個 stage 各帶一個參數的話，
有人漏傳就變成「人工框配自動類別」，而且錯得無聲無息。

| | `labels:"human"`（roboflow 預設） | `labels:"auto"`（demo 預設） |
|---|---|---|
| 框從哪來 | 資料集自帶的人工 bbox | 連通分量幾何（`cc_bbox`） |
| `label.bbox` | `rule:"human"`、`conf:1.0`、`cls` 是真類別 | `rule:"cc_bbox"`、`cls:-1`（契約 §8.6） |
| `label.mask` | **不發**（人工框沒有遮罩） | 每張一筆 |
| `ds.image.cls/counts` | 真實類別（`["Donut"]` / `{"Donut":1}`） | 空的 —— 在 s01 填就是偷看人工答案 |
| `label.anchor_iou` | 另外跑幾何當「被量的一方」 | 直接量自己的框 |

`demo` + `labels:"human"` → `400 BAD_ENUM`：`gt.json` 是**合成**的免費 GT（契約 §11），
叫它 human 就是把合成標籤當人工標註。

### 17.3 尺只量 anchor 那 40 張

`label.anchor_iou` 在 roboflow 這條路是 `source:"roboflow_anchor"` / 門檻 **0.4**（契約 §11），
而且**只量 `gt_partition()` 切出來的 anchor 40 張**。sealed-test 的人工框是考卷答案，
在 s03 拿來當尺就等於每輪偷看一次。

`human` 模式量的是「**如果**走 auto 會有多準」：拿人工框自己和自己量一定是 1.0000，
那不是天花板證據，那是一面鏡子。

**實測（409 張真實 WM-811K，anchor 40 張）**：IoU 中位數 **0.8258** → `verdict: pass`
（門檻 0.4）。兩種模式量到同一個數字（同一批 anchor、同一支幾何），這本身就是一致性訊號。
為什麼真實資料還有 0.83：這個資料集的人工框幾乎是整張晶圓的外框
（實測第一張 `w:0.9996 h:1.0`），而連通分量抓到的也是整片晶圓區 —— 這個數字讀作
「這批標註的粒度很粗」，不是「我們的幾何很準」。真正細粒度的資料集會掉下來，那時
0.4 這道閘門才會發揮作用（停在 s03 宣告「問題在標註不在模型」）。

### 17.4 GT 三分：DESIGN 的 40 / 80 / 289 對上了

`freeze.gt_partition()` 的上限式（`min(40, n//6)` / `min(80, n//3)`）在 409 張上實測落成
**anchor 40 / sealed-test 80 / pool 289**，與 DESIGN 寫的數字逐位相同（`_selfcheck()` 早就
有這一條，這次是拿真的 409 個 id 走完 s04）。落檔實測：train 231 / valid 58 / unassigned 0、
s04 selfcheck **6/6 PASS**、`data.yaml` `grep -i test` 0 行。

順手修掉一顆地雷：`freeze.selfcheck()` 原本把影像副檔名寫死 `.png`，而 Roboflow 的 yolov8
export 是 **jpg** —— 不改的話 `dirs_exist` 回「0 圖」、`yolo_format` 說每張標註都「沒有對應
影像」、另外兩條拿空集合比，四條 FAIL 全指錯方向而真因只是副檔名。現在走 `_images()`
（png/jpg/jpeg/bmp，以檔名主幹當配對鍵）。

`yolo_lines()` 同時改成「框自己帶 `cls >= 0` 就以框為準」：人工標註是逐框帶類別的，
一張圖可以有兩類，硬套「一張圖一個類別」會把多類圖壓成單類。auto 的框一律 `cls:-1`、
`gt.json` 的框沒有 `cls` 鍵 → 兩者都走舊路徑，demo 的落檔逐位元組不變。

### 17.5 封印的第六與第七道（這一輪新長出來的側門）

`labels:"human"` 把**人工答案**帶進了 `ds.image.counts` 與 `labels.json`，於是封印多出兩條路：

| 側門 | 擋法 |
|---|---|
| 事件流：`ds.image` 的 cls/counts | sealed 的 80 張一律送空的（分法用同一支 `gt_partition`） |
| REST 快照：`annotations.classes` | 原本只看 s04 寫的 `splits.json` → **s02 卡住或只跑到 s03 的 run 會漏**；現在 human 模式從 s01 就用 `gt_partition` 遮 |

demo / `labels:"auto"` 不受影響：那條路的 `labels.json` 裝的是機器自己猜的框（不是答案），
遮了反而看不到 auto-label 在做什麼 —— 遮蔽照舊跟著 `splits.json`。

`GET /images/{id}` **刻意不遮影像位元**（理由見 `dataset.py` 的 `image_bytes` docstring）：
封印的是答案，不是像素。Roboflow 的 jpg 在這裡轉成 png（契約 §8.8 寫死 `image/png`）；
原本就是 png 且不縮圖時一個位元組都不動，所以 demo 的 ETag 沒變。

### 17.6 🔒 契約缺口：`labels:"human"` 生不出合法的 class 真相表 ✅ 已批准並落地（2026-09-12）

> **結案**：console-owner 照下面這份請求批了 §12 變更 —— schema 加 `class_source`，
> `"human"` 時放寬 `nc` / `names` / `cluster_stats`（契約變更紀錄 2026-09-12 那三條）。
> 實作：`_refuse_human_class_table()` → `_freeze_human_class_table()`（不分群、不呼叫 LLM、
> 順手寫 `clusters.json` 當分層鍵，落檔的每一行 `cls` 仍以框自己的為準）。
> 實跑 r149 `labels:"human"` s01→s04 全綠（train 24 / valid 6 / anchor 10 / sealed 20、0 未指派）。
> 以下原文保留當教材：一個「停在那裡不硬塞」的契約缺口長什麼樣。

這是那一輪唯一沒做完的事，**刻意停在那裡**而不是硬塞。

- **(a) 要改哪一條**：`_Context/class_table.schema.json` 🔒 的 `nc`（`minimum:3 maximum:6`）、
  `names`（`minItems:3`、`items.enum` 六個固定詞）、`cluster_stats.required = [k, silhouette, clusters]`
  與 `clusters.minItems:3`。
- **(b) 為什麼現有形狀做不到**：schema 是照「KMeans + LLM 命名」凍的。真實資料集自帶的
  類別體系三條全踩：實測 WM-811K v3 的 yolov8 export 是 `nc:1 names:['Donut']`
  （大寫、不在詞彙表裡、也不足 3 類），而人工標註**沒有 silhouette 可言**。
  現況：`POST /label/auto {mode:"cluster"}` 在 human 模式回
  `409 CLASS_TABLE_SCHEMA_CONFLICT`，訊息裡直接附這份變更請求的位置。
  兩種硬塞法都更糟：把 `['Donut']` 補到 3 類 = 發明兩個不存在的類別，`data.yaml` 與所有
  `cls` 值從此說謊；讓 s02 照樣跑 KMeans 當類別體系而 s03 留人工框 = 框的 `cls`（人工的 0）
  對到 KMeans 第 0 群的名字，**落檔的標籤靜默錯位**。
- **(c) 改完的形狀**（建議：多一個來源欄位，既有 auto 那條路一個字不用改）：
  ```json
  {
    "version": "v1",
    "class_source": "human",
    "nc": 1,
    "names": ["Donut"],
    "cluster_stats": null,
    "naming_rationale": [
      {"cluster_id": 0, "name": "Donut", "rationale": "資料集自帶的人工類別名",
       "evidence": "413 個人工框 / 409 張"}
    ],
    "montage_urls": []
  }
  ```
  即：`class_source: "cluster"|"human"` 必填；`class_source=="human"` 時 `nc` 放寬到
  `minimum:1`、`names` 不受詞彙表約束（但仍 `uniqueItems`）、`cluster_stats` 允許 `null`。
- **(d) 誰要跟著改**：schema（console-owner）＋ `api-contract.md` §9 的 `/classes` 回應
  與 §10 錯誤碼表（console-owner）＋ `dataset.py` 的 `_refuse_human_class_table()` 換成真的
  建表（dataset-truth，約 15 行）＋ `eval` 那邊凡是假設 `nc>=3` 的統計力判準（metric-auditor）。

**在這之前，真實資料要跑完 s02→s08 就用 `labels:"auto"`**：同一批真實影像走 KMeans +
LLM 命名，k 一定落在 [3,6]、名字一定在詞彙表裡（實測 `k=3 names:['random','center','donut']`
silhouette 0.3014，機械命名 fallback 也照樣合法）。

### 17.7 錯誤碼（要 console-owner 補進契約 §10）

| HTTP | code | 什麼時候 | 訊息裡有什麼 |
|---|---|---|---|
| 400 | `ROBOFLOW_KEY_MISSING` | `.env` 不存在或 `ROBOFLOW_API_KEY=` 空的 | 去哪拿 Private key、`cp .env.example .env` 填哪一行 |
| 400 | `ROBOFLOW_KEY_INVALID` | 格式不像 key（本機擋，**沒打網路**）或 API 回 401/403 | 明講「沒有打網路」vs「連得上但權限不對」 |
| 409 | `ROBOFLOW_UNREACHABLE` | 連不上 `api.roboflow.com` | 這是網路/防火牆，key 還沒被驗到；教室沒網路就用 `source:"demo"` |
| 404 | `ROBOFLOW_NOT_FOUND` | workspace / project / version 不存在 | 三個值各自印出來 |
| 409 | `ROBOFLOW_DOWNLOAD_FAILED` | export 還在生成 / zip 壞了 / 目錄佈局不對 / 標註行不合法 | 砍哪個目錄重抓 |
| 409 | `CLASS_TABLE_SCHEMA_CONFLICT` | human 模式的 class 表塞不進 🔒 schema（見 §17.6） | 兩條路：改用 `labels:"auto"` 或走 §12 |

契約本文也要跟著改兩句（console-owner）：§8.5 的「`source:"roboflow"` → `501`（M8）」
與 §10 的 `NOT_IMPLEMENTED` 那一列（現在只剩 `source:"local"` 是 501）。

下載這件事**在端點裡同步做完才回 202**，不丟背景：丟背景的話這支回 202，
錯誤只會出現在 server log，而前端看到的是一個空的成功。

### 17.8 id 規則與「已下載就沿用」

- `image_id = "rf" + sha1(f"{slug}/{相對路徑}")[:12]`。**不用序號**：序號在「同一台機器抓了
  第二個 Roboflow 資料集」時會撞，而 `GET /images/{id}` 只有 id、沒有 ds，撞了就回錯的圖。
  同一個 zip 重抓 → 同一組 id（**重跑不換考卷**，sealed-test 才穩）。
- 順序是**檔名排序**，因為 `gt_partition()` 照 id 順序切 anchor / sealed-test。
- `index.json` 在且有圖 → 直接沿用，**不重抓也不讀 key**（抓過一次之後，沒有 key 也能重跑
  整條線）。要重抓：`rm -rf 01-raw-data/roboflow/<slug>/`。
- `01-raw-data/` 本來就在 `.gitignore` 裡，11 MB 的真資料不會進 repo。

### 17.9 會失敗的檢查

`uv run python -m src.autolabel.roboflow_src`（15 條，實測全過 / 0.7 秒）：

1. **沒 key**（`.env` 指到不存在的檔 + 清空環境變數）→ code 是 `ROBOFLOW_KEY_MISSING`、
   status 4xx、訊息含 `app.roboflow.com` 與 `.env` 那一行。
2. **key 格式錯 vs 網路/權限**：`your-key-here` → 本機擋（訊息明講「沒有打網路」）；
   `ffffffffffffffffffff`（格式合法的假 key）→ 打真的 API，回 `ROBOFLOW_KEY_INVALID`
   （401）或 `ROBOFLOW_UNREACHABLE`（沒網路），**都不會是 KEY_MISSING**。
3. **index 一致性**：id 不重複、id 真的帶 slug 雜湊、每張解析得到檔案、人工框數 > 0。
4. **壞掉的標註行要 raise**（少一框 = IoU 天花板無聲降低）：4 欄的行、`w=1.4` 的行都 → `ROBOFLOW_DOWNLOAD_FAILED`。

另外三條走真的 API 驗過（不在 `_selfcheck()` 裡，因為它們每次都要打網路）：
不存在的 project → `404 ROBOFLOW_NOT_FOUND`；要 `version=99` → 退回 `max(version_ids)=3`
並沿用已下載的 409 張（版本 fallback 真的跑到）；格式合法的假 key → 401 → `ROBOFLOW_KEY_INVALID`。

實證它會紅（真的跑過這個 mutation）：把 `api_key()` 的空值那三行拿掉 →
`[FAIL] code == ROBOFLOW_KEY_MISSING  ROBOFLOW_KEY_INVALID`，訊息變成
「`ROBOFLOW_API_KEY` 的格式不像 Roboflow key（10–64 碼英數，**收到 0 碼**）」。
也就是「你還沒填 .env」會被講成「你的 key 格式不對」—— 講師會去檢查那串貼錯了沒，
而真因是那一行還是空的。這條檢查守的就是這個誤導。

### 17.10 這一輪沒驗到的

- **`POST /runs` 那顆按鈕還是 501**：`console.py` 第 315 行 `if body.source != M1_SOURCE`
  是 console-owner 的檔案，沒動。roboflow 這條路目前只能從
  `POST /api/v1/datasets/ingest` 進（curl 單步教學那條）。要讓按鈕能按，console-owner 要：
  (1) 放開那一行；(2) `RunBody` 加 `labels`（否則按鈕只能跑 human，而 human 會停在 §17.6）。
- **前端 `fromSnapshot()` 不填 `cls`**（`prototype/index.html` 第 1247 行的註解自己講了
  「兩條路進來的同一張圖必須長出同一個 cls」）。409 張時前 200 張從事件進來有
  `cls:["Donut"]`、第 201 張起從快照進來只有 `counts` 而 `cls:[]` —— 篩選與前端那份
  stratifiedSplit 會把 209 張當成沒有類別。後端的 split 不受影響（s04 自己算）。
  一行修法（前端擁有者才能動）：`fromSnapshot()` 裡 `cls: Object.keys(counts)`。
- **真的訓練沒跑過**：roboflow 的圖是變尺寸 jpg（實測 345×369、419×369），
  demo 是固定 256×256 png。s05–s08 沒有在真資料上跑過一次。
