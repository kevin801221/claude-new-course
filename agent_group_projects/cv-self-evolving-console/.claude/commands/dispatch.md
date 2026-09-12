---
description: 派工單：把一個里程碑拆成互不重疊的平行任務書，逐位交給五位專家 subagent
argument-hint: <M6 | M7 | M8 | fix-anchor-cls | ledger-writeback | 任何一句要辦的事>
allowed-tools: Read, Grep, Glob, Task, Bash(ls:*), Bash(jq:*)
---

# /dispatch —— 派工

你現在是 **console-owner（team lead）本人**。這一輪要派的是：**$ARGUMENTS**

派工的產物是**任務書**，不是程式碼。你自己一行 `src/` 都不要改 —— 你只負責
「誰做、做哪幾個檔、交什麼、怎麼驗、什麼時候回頭喊人」。

---

## 現況快照（以下兩段是展開這個指令時真的跑出來的，不是記憶）

現有 subagent、M6 三個路徑在不在、契約與資料落點：

!`ls -d .claude/agents/*.md src/evolve _Context/prompts scripts/evolve.py src/app/routers/round.py 02-dataset/data.yaml 03-sealed-test/manifest.json _Context/class_table.json 2>&1`

run 帳本 `runs/runs.jsonl`（M6 的開工前提就看這裡的 `verdict非null`）：

!`jq -s -r '{"帳本列數":length,"狀態分佈":(group_by(.status)|map({(.[0].status):length})|add),"verdict非null":(map(select(.verdict!=null))|length),"val_map5095非null":(map(select(.val_map5095!=null))|length),"最後一個run":(.[-1].run_id)}' runs/runs.jsonl`

> 看到 `No such file or directory` 就是**那一塊還沒開工**，不是壞掉。
> `verdict非null: 0` 代表判分結果從來沒寫回帳本 —— 這是 M6 的硬閘門，往下看第 4 步。

---

## 第 1 步：讀真相，不要憑印象

動筆前一定要讀這三份，**照這個順序**：

| 檔案 | 讀什麼 | 怎麼讀 |
|---|---|---|
| `_Context/team-roles.md` | §1 角色 × 可寫目錄、§0 三條紅線、§3 階段閘門 | 整份讀，只有 9KB |
| `_Context/DESIGN.md` 「開工順序」 | 這個里程碑的天數、派誰、**驗收條件那一句** | Read `offset 532 / limit 21`（= §開工順序 M0–M8 整段），不要整份讀（93KB） |
| `_Context/api-contract.md` | §4 type 前綴表（決定誰能發什麼事件）、§10 錯誤碼、§12 變更流程 | Read `offset 118 / limit 70`（§4）＋ `offset 507 / limit 90`（§10–§12） |

用 Read 的 `offset/limit` 而不是 `sed -n`：這支指令的 `allowed-tools` 只放行 `ls` 與 `jq` 兩個 Bash 指令
（見檔頭 frontmatter），派工是**只讀**的工作，開 `Bash(sed:*)` 等於順手把 `sed -i` 的改檔權也發出去。
行號會隨契約改動漂移，讀完發現段落對不上就往下捲，不要憑印象補。

擁有權的唯一真相是 `team-roles.md` §1 那張表。你等一下寫進任務書的「可寫檔案」清單，
**逐行都要能在那張表裡指到**。指不到就是你在發明權限。

---

## 第 2 步：出派工單（表格，先給人看）

先印一張表，Kevin 要在投影幕上唸這張：

```
## 派工單：<里程碑>

| # | 派給 | 做什麼（一句話） | 可寫目錄 | 依賴 | 平行/序列 |
|---|---|---|---|---|---|
| 1 | dataset-truth | … | src/autolabel/… | 無 | 平行組 A |
| 2 | training-engineer | … | src/train/… | 無 | 平行組 A |
| 3 | metric-auditor | … | src/eval/… | 等 #1 | 序列 |
```

判「平行還是序列」只有一條規則：**兩個人的可寫清單有沒有交集**。
有交集就改成序列，不要僥倖 —— 兩隻 agent 同時寫同一個檔，後寫的那隻靜默蓋掉前一隻，
而你要到整合當天跑 selfcheck 才會發現少了一半的改動，那時已經分不出是誰蓋的。

里程碑 → 派誰，照 DESIGN「開工順序」，不要自己重排：

| 里程碑 | 派誰 | DESIGN 寫死的驗收條件 |
|---|---|---|
| M2 class 真相凍結 | dataset-truth | `/datasets/{ds}/freeze` 的 selfcheck 全 PASS，FAIL 時前端訓練鈕 disabled |
| M3 訓練不阻塞 | training-engineer（+ console-owner 收契約） | 訓練中殺掉 uvicorn 再開，`since=` 續流一條不漏；Stop 收到 `train.cancelled` |
| M4 noise floor + verdict | metric-auditor | 落在灰帶內的 Δ 在前端灰掉標「雜訊」 |
| M5 一鍵端到端 | console-owner 自己（**不派 agent**） | teaching preset 一顆按鈕 5 分鐘內看完 s01→s08 |
| M6 討論迴圈 | experiment-arbiter | 打一句「scratch 抓不到」→ 四份帶檔案行號的提案 + 裁決卡 patch JSON → 按同意只重跑受影響 stage |
| M7 自主模式 + 停止條件 | experiment-arbiter（+ console-owner 收契約） | teaching preset 跑滿 3 輪自動停，timeline 三個徽章 + 跨輪 sparkline |
| M8 Roboflow 真資料 + 教材 | dataset-truth + console-owner | key 在 server env、圖片走自家 proxy、walkthrough 落檔 |

M0 / M1 已經過了（見 `CLAUDE.md`「現在做到哪」），不要重派。

---

## 第 3 步：每位一段任務書，可以直接丟 Task

一位專家一個 Task 呼叫，`subagent_type` 用 `.claude/agents/` 裡那個名字
（現在只有 `dataset-truth` / `training-engineer` / `metric-auditor` / `experiment-arbiter` 四位）。
每段任務書**六段都要有**，少一段就退回去補：

```
【做什麼】一句話講清楚這一輪的成品是什麼。不要寫「改善」「優化」這種沒有完成判準的詞。

【為什麼現在做】哪一條審查/哪一個 assert/哪一行程式在痛。附檔名行號，例如
  src/autolabel/freeze.py:63、src/app/routers/eval.py:316-317。
  沒有證據就不要派 —— 沒有證據的工單 agent 會自己去猜，猜出來的是第二個 bug。

【可寫檔案】逐檔列出（不是列目錄）。這份清單就是它的權限上限。
  ⛔ 清單以外一個字都不准改，包含「順便」。

【交付什麼】檔案 + 事件 type + 契約條文。事件 type 一定要在你自己的前綴裡
  （ds./label./class. → dataset-truth，model./probe./train./sweep./recipe./augment. → training-engineer，
   eval./test./charts. → metric-auditor，chat./expert./arbiter./round./autonomy./stop → experiment-arbiter）。
  bus.append_event() 會直接丟 ValueError 擋下越權的 type，這不是靠自律。

【驗收指令】要能複製貼上就跑、跑完看得出綠或紅。這個專案真的能跑的有：
  uv run --no-sync python -m src.autolabel.geometry   # 模組自檢，實測 0.9 秒 PASS
  uv run --no-sync python -m src.autolabel.split
  uv run --no-sync python -m src.app.bus
  uv run --no-sync python -m src.app.registry
  uv run --no-sync python -m src.eval.metrics
  uv run --no-sync python -m src.train.runner
  uv run --no-sync python scripts/selfcheck.py        # 三條 assert（冷啟動實測 3.0 秒）
  uv run --no-sync --with pytest pytest tests/ -q     # 實測 26 passed（pytest 故意沒進 pyproject，用 --with）
  uv run --no-sync python tests/test_split_parity.py  # 不裝 pytest 也能跑，實測 5 條 PASS
  前端 prototype/index.html?selftest=1        # 42 條（最後一條要打真 server，
                                              #   file:// 開的話它必紅，不是你改壞了）
  ⚠️ 要動到訓練一律 uv run --extra train …（它保證 torch 裝得到；會把 torch 拆掉的是不帶 extra 的 uv sync，
     s05 紅燈而錯誤訊息指不到真因）。

【什麼時候停下來喊 console-owner】至少寫滿這四條：
  1. 要動 _Context/ 三份契約、src/app/main.py、bus.py、registry.py、pyproject.toml
     → 🔒 共同地基，一行都不准自己改，改成提變更請求（契約 §12 四段：改哪一條 /
       現有形狀為什麼做不到 / 改完的完整 JSON / 誰要跟著改）。
  2. 要前端顯示任何新東西 → 只交 JSON 契約，不准開 prototype/index.html。
  3. 發現該發的事件 type 不在自己前綴裡 → 停，不要改用別人的前綴繞過去。
  4. 驗收指令紅了三次還在同一個地方 → 停下來回報現象，不要開始改驗收條件。
```

---

## 硬規矩（違反就是整合當天的爆點）

| ⛔ | 規矩 | 為什麼 |
|---|---|---|
| 1 | **M0 契約閘門沒過不准平行開工** | 四個人照一份還會變的契約寫，四份都要重寫。這一條 `team-roles.md` §3 與契約 §12.4 各寫一次。 |
| 2 | **前端工作不派 agent** | 前端只有 console-owner 一個擁有者（§0 紅線 1）。`prototype/index.html` 是唯一沒辦法用目錄切開的東西，五個人同時改就是互相覆蓋。要改前端走 `/design-brief` → `/design` → `/design-pull`，Kevin 本人操作。 |
| 3 | **同一批派出去的人，可寫清單不准有交集** | 有交集就改序列。`src/app/routers/` 是四個人共用的目錄，所以擁有權切到**檔案層**：一個 router 一個檔一個主人。 |
| 4 | **不准派「順便把 X 也改一下」** | 跨目錄的順便就是越權。順便改掉的那行沒有人 review、不在任何驗收指令的覆蓋範圍裡。 |
| 5 | **契約與實作不一致時，契約是對的** | 反過來那一天（「程式已經這樣寫了所以契約改一下」）就是整合當天爆掉的起點。 |
| 6 | **M6 以後的規格讀了不要現在動手** | DESIGN 裡 M6–M8 的細節是後續里程碑的規格，提前做出來的東西沒有對應的驗收條件可以判生死。 |

---

## 第 4 步：開工前的閘門檢查（最後印，紅的就不要派）

照這張表逐條判，**每一條都要寫「過 / 沒過 + 證據」**，不要只寫「OK」：

| 里程碑 | 前置條件 | 怎麼確認 |
|---|---|---|
| 任何一個 | M0 契約閘門 | `_Context/` 三份契約在 + 五個 router 都掛進同一個 app（上面快照已列） |
| M2 | class 表 schema 凍結 | `_Context/class_table.schema.json` 在，欄位不准動 |
| M3 | M2 的 `02-dataset/data.yaml` 落檔 | 上面快照看得到 `02-dataset/data.yaml` |
| M4 | M3 有真的訓練產物 | `04-experiments/` 有 run 目錄、`00-weights/` 有 `.pt` |
| **M6** | 🔴 **判分結果要先寫回帳本** | 不變量：上面快照的 `verdict非null` 必須 > 0（arbiter 要拿它排序挑 best run）。**帳本每跑一個 run 就會長，不要背列數**，要確認就當場重跑：`jq -s -r '[length,(map(select(.verdict!=null))\|length)]\|"共 \(.[0]) 列・verdict 非 null \(.[1]) 列"' runs/runs.jsonl`。目前非 null 是 **0** —— 事件層說 trusted、帳本說 null，arbiter 一開工看到的是一整排分不出高下的 run，拿空表做決策。修法：`registry.set_status(run_id, "done", verdict=…, val_map5095=…, ci=…, anchor_ok=…, provenance_ok=…)`（`set_status` 本來就吃 `**extra`，見 `src/app/registry.py:219`），metric-auditor 出欄位與值、console-owner 落筆寫入。**這一條沒綠，M6 不准開工。** |
| M7 | M6 的 round 迴圈能跑完一輪 | `src/evolve/` 存在且 `scripts/evolve.py` 能跑 |
| M8 | server 讀得到 `ROBOFLOW_API_KEY` | `.env` 在、`.env.example` 有那個變數名，**前端不持 key** |

---

## 已知工單（審查實跑抓到的，可以直接當 `$ARGUMENTS` 派）

派這幾條時，上面的【為什麼現在做】直接抄這裡的證據，不要自己重新調查：

| 代號 | 派給 | 證據 |
|---|---|---|
| `fix-anchor-cls` | dataset-truth | 🔴 `02-dataset/labels/anchor` 40 個框全 `cls 0`、`03-sealed-test/labels` 80 個框也全 `cls 0`，但 ds138 分群是 anchor 25 donut / 14 scratch / 1 random、sealed 71 scratch / 9 donut。真因 `src/autolabel/freeze.py:63`：框自帶的 `cls >= 0` 會蓋掉參數的 cls，而 Roboflow 的框自帶它自己類別空間的 0。 |
| `fix-freeze-selfcheck` | dataset-truth | `uv run --no-sync python -m src.autolabel.freeze` 是紅的（`AssertionError at freeze.py:428`）—— 自檢吃共用的 `01-raw-data/demo`，該目錄被大 limit 的 run 撐到 300 張。改成 hermetic（自己造 id），順便把 demo 目錄拉回契約 §11 凍的 120 張。 |
| `fix-manual-gate` | training-engineer | `src/app/routers/train.py:230-237` 的人工 GO 閘門是打不到的死碼 —— `src/app/routers/console.py:355` 對 `mode != oneshot` 一律 501。 |
| `fix-oom-retry` | training-engineer | `scripts/train_yolo.py:93` 的 OOM batch 減半靠字串比對 `"out of memory"`，MPS 的 OOM 訊息未必長這樣，從未觸發過。 |
| `ledger-writeback` | metric-auditor 出欄位 + console-owner 落筆 | 🔴 見上表 M6 那一列。 |
| `fix-overfit-gap` | metric-auditor | `src/app/routers/eval.py:316-317` 的 `results.csv` 是相對路徑 + 硬編 `'yolo'`，server 不從 PROJECT_ROOT 起就靜默變成**永遠 PASS**。要改成拒答，不是 PASS —— 一個永遠 PASS 的 check 比沒有 check 更危險，它會讓人以為這件事有人在看。 |
| `M6` | experiment-arbiter | `src/evolve/`、`_Context/prompts/`、`scripts/evolve.py` 三個路徑都不存在，`src/app/routers/round.py` 六支端點全部 raise 501。⚠️ M2 踩過的坑要繼承：`claude -p` 子行程一定要經 `src/app/procs.py` 的 `run()` 登記，否則按了 Stop 殺不到（實測 cancel 後 44 秒 `claude` 還在燒 token）。 |
| `M7` | experiment-arbiter | 三軸預算**只記帳不執法**：`gpu_min_cap` / `usd_cap` / `llm_calls_cap` 零比較零攔截，契約 §10 的 `400 AUTONOMY_NO_CAP` 只活在 docstring。teaching preset 的 `usd_cap` 是 $0.5，而一次 `claude -p` 命名實測 $0.19–0.31，兩次半就爆。 |

---

## 輸出順序（照這個印，不要加前言）

1. **派工單表格**
2. **每位一段任務書**（六段齊全）
3. **開工前閘門檢查**（逐條「過 / 沒過 + 證據」）
4. 最後一行：`要我現在把這幾位 spawn 出去嗎？（Task × N）` —— **停在這裡等 Kevin 點頭再呼叫 Task**，
   課堂上要看得到派工單本身，直接 spawn 會把畫面洗掉。
