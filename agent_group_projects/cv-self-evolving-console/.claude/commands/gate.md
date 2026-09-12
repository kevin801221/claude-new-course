---
description: 閘門驗收 —— 照里程碑實跑指令、稽核越權、比對契約，印一張只認證據的驗收表
argument-hint: [M0|M1|M2|M3|M4|M5|M6|M7]
allowed-tools: Read, Grep, Bash(uv run:*), Bash(uv sync:*), Bash(curl:*), Bash(git status:*), Bash(git diff:*), Bash(ls:*), Bash(grep:*), Bash(awk:*), Bash(sed:*), Bash(sort:*), Bash(comm:*), Bash(tr:*)
---

# /gate $1 —— 交付驗收

你現在是這個專案的閘門。要驗的里程碑是 **$1**（沒給就當 `M1`）。

**唯一鐵律：沒跑過就不准寫 PASS。**
「看起來應該會過」「程式碼看起來有寫」「上一輪跑過」在這份指令裡一律等同 **FAIL**。
格子只有三種值：`PASS`（附你這一次的真實輸出）、`FAIL`（附真實輸出 + 真因）、
`沒驗到`（指令沒跑、server 沒開、里程碑還沒開工）。**`沒驗到` 不是 PASS**，不准四捨五入成 PASS。

為什麼這麼硬：這個專案五個人平行寫，整合當天唯一分得出「真的好了」與「自己說好了」的東西
就是**能重跑的指令**。一旦允許「我看程式碼有做」進驗收表，下一輪的 arbiter 就會拿這張表當證據做決策。

---

## 現場快照（已經先幫你抓好，不用重跑）

server 健康檢查：
!`curl -s -m 3 http://127.0.0.1:8000/healthz || echo "SERVER_DOWN —— 需要 server 的檢查一律標「沒驗到」，不准標 PASS"`

動過的檔案（repo 根相對路徑；`--untracked-files=all` 不能拿掉，理由見 §B）：
!`git status --short --untracked-files=all .`

已追蹤檔的改動量：
!`git diff --stat .`

---

## A. 里程碑驗收（實跑，不是讀文件）

### ⚠️ 先決定用哪個 interpreter，不然你會驗錯東西

2026-09-12 在 uv 0.12.7 上實測釐清（**舊版本文件寫錯，不要照抄**）：

| 指令 | sync 行為 | 對 torch |
|---|---|---|
| `uv run python …`（裸的） | inexact：只補缺的 | 不動它。實測 `torch 2.14.0` 連跑兩次都還在 |
| `uv run --no-sync python …` | 完全跳過 | 不動它，而且省掉 resolve |
| `uv sync`（不帶 extra） | **exact** | 💀 拆掉（`uv sync --dry-run` 印「Would uninstall 33 packages」） |

| 要驗的里程碑 | 用哪一個 | 為什麼 |
|---|---|---|
| M0 / M1 / M2 | `uv run --no-sync python …` | 這幾條本來就零 GPU，不需要 torch 也不該動 .venv |
| M3 / M4 / M5 | `uv run --extra train python …` | s05/s07/s08 會起需要 torch 的子行程；extra 保證它裝得到 |

⚠️ **「M1 沒偷跑 torch」不能用「裸 `uv run` 跑不到 torch」來驗** —— 裸 `uv run` 不會拆掉已裝的 torch，
這樣驗永遠抓不到偷跑的人。要驗偷跑用這兩條（都不會動到 .venv）：

```bash
sed -n '/^dependencies/,/]/p' pyproject.toml          # base 依賴應該只有那六個，沒有 torch / ultralytics
grep -rn "import torch\|from ultralytics" src/autolabel/ src/app/routers/dataset.py   # M1/M2 路徑應該零命中
```

**驗完 M1 又要驗 M5，只有在 .venv 真的沒有 torch 時才需要 `uv sync --extra train` 補**（先用
`uv run --no-sync python -c "import torch"` 確認，別無條件重灌 1.1 GB）。
這一條在 frontmatter 的 `allowed-tools` 開了 `Bash(uv sync:*)`（`--dry-run` 也吃這一條），
所以你按得下去；它是整份指令裡**唯一准許動 .venv 的指令**（159 MB → 1.1 GB，第一次要等），
其他一律 `uv run`。

### 各里程碑的驗收條件與指令

驗收條件的真相在 `_Context/team-roles.md` §3 與 `_Context/DESIGN.md`「開工順序」。以下是對應的可跑指令：

| $1 | 跑什麼 | 過關標準 |
|---|---|---|
| **M0** | `ls _Context/api-contract.md _Context/team-roles.md _Context/class_table.schema.json`<br>`grep -c include_router src/app/main.py`<br>`curl -s -m 3 http://127.0.0.1:8000/healthz` | 三份契約都在；`include_router` 剛好 **5** 行（五個 router 掛同一個 app）；healthz 回 `{"ok":true,…}` |
| **M1** | `uv run --no-sync python scripts/selfcheck.py`<br>`uv run --no-sync python tests/test_split_parity.py`<br>`uv run --no-sync python -m src.app.bus`（`registry` / `procs` / `src.autolabel.split` / `src.autolabel.geometry` 同樣各跑一次）<br>瀏覽器開 `http://127.0.0.1:8000/?selftest=1` | selfcheck **3/3 PASS 且總耗時 < 90 秒**（它自己會印秒數）；split parity **5 條 PASS**；五支 module 自檢各印一行 `… selfcheck PASS`；前端頁面底下印 **`全部通過（42 條）`**（40 條同步 + failSafe 409 + `GET /api/v1/stages`）。條數以畫面印的為準，不要信原始碼註解裡那個數字（它落後了） |
| **M2** | `uv run --no-sync python -m src.autolabel.cluster`<br>`uv run --no-sync python -m src.autolabel.freeze`<br>`ls 01-raw-data/demo \| grep -c png`<br>`curl -s http://127.0.0.1:8000/api/v1/datasets/<ds>/classes` | 兩支 module 自檢 PASS；demo 目錄張數 == 契約 §11 🔒 凍的 **120**；`/classes` 回 class 真相表而不是 404/501 |
| **M3** | `uv run --extra train python -m src.train.runner`<br>`uv run --extra train python -m src.train.select`<br>`curl -s http://127.0.0.1:8000/api/v1/models/candidates` | 兩支自檢 PASS；candidates 回 `yolov8n` / `yolov8s` 且 `weights_cached` 為 true |
| **M4** | `uv run --extra train python tests/test_ci.py`<br>`uv run --extra train python -m src.eval.metrics`（`checks` / `series` / `ceiling` 同樣各跑一次）<br>`curl -s http://127.0.0.1:8000/api/v1/eval/noise-floor/<ds_version>` | test_ci 全 PASS；四支自檢 PASS；noise-floor 回得出 `sigma` 而不是 409 |
| **M5** | `uv run --extra train python -m src.app.routers.console`<br>`uv run --extra train python -m src.app.procs`<br>`uv run --extra train python scripts/run_all.py --preset teaching --stages s01,s03`（要驗整條龍就拿掉 `--stages`）<br>**帳本回填**：見下方 §A.1 | console / procs 自檢 PASS；run_all 把要求的 stage 全跑到 `done`；**帳本那四欄不是 null** |
| **M6 / M7** | `ls src/evolve scripts/evolve.py _Context/prompts` | 這三個路徑**目前都不存在** → 整個里程碑一律 `沒驗到`。不准因為 `src/app/routers/round.py` 有檔就給分：它六支端點全部 raise 501 |

`<ds>` 用最近一次落檔的那一個：`ls -t 01-raw-data/datasets | sed -n 1p`。
`<ds_version>` 目前只有 `v1`（`runs/runs.jsonl` 的 `ds_version` 欄）。
**不要隨手挑一個 ds 就打**：不同 ds 的 source 不一樣（`demo` 沒有人工 GT），
拿 demo 的 ds 去打 `/eval/ceiling` 會回 409 `CEILING_UNAVAILABLE`，那是正確行為不是紅燈。

### A.1 帳本回填（M5 的隱形條件，最常被漏掉）

事件層說 `verdict: TRUSTED` **不等於**帳本寫進去了。逐欄數一次：

```
grep -c '"verdict": "' runs/runs.jsonl
grep -cE '"val_map5095": [0-9]' runs/runs.jsonl
grep -cE '"anchor_ok": (true|false)' runs/runs.jsonl
grep -cE '"provenance_ok": (true|false)' runs/runs.jsonl
```

四個數字任何一個是 **0**，M5 就是 **FAIL** —— 不管前端畫得多綠。
真因寫這句：判分結果沒有經 `registry.set_status` 寫回 `runs/runs.jsonl`，M6 的 arbiter 會看到一整排
一模一樣的 null run，等於拿空表做決策。

---

## B. 越權稽核（本專案唯一真的擋得住越權的機制）

hook 擋得住路徑，**擋不住身分**：dataset-truth 寫 `src/eval/` 的時候，檔案系統不會有任何意見。
所以這一關只能靠人對表。

1. 取動過的檔案清單。**一定要帶 `--untracked-files=all`**：
   ```
   git status --short --untracked-files=all .
   ```
   不帶的話整個專案只會回一行 `?? ./`（這個資料夾在上層 repo 裡整包還沒被追蹤），
   你會拿到一份「零個檔案被動過」的假綠燈。同理 `git diff --stat` 在這個狀態下**永遠是空的**，
   不要拿它的空輸出當「沒人改東西」的證據。

2. 逐檔對 `_Context/team-roles.md` §1 的擁有權表。對照如下（權威版在那份檔案，這裡是查表用）：

   | 路徑 | 擁有者 |
   |---|---|
   | `prototype/index.html`・`src/app/main.py`・`bus.py`・`registry.py`・`routers/console.py`・`scripts/run_all.py`・`scripts/selfcheck.py`・`_Context/api-contract.md`・`team-roles.md`・`class_table.schema.json`・`README.md`・`CLAUDE.md`・`pyproject.toml`・`.env.example`・`.gitignore`・`runs/` | console-owner |
   | `src/autolabel/`・`src/app/routers/dataset.py`・`scripts/gen_demo_wafers.py`・`scripts/prepare_dataset.py`・`_Context/dataset-notes.md`・`_Context/class_table.json`・`tests/test_split_parity.py`・`01-raw-data/`・`02-dataset/`・`03-sealed-test/` | dataset-truth |
   | `src/train/`・`src/app/routers/train.py`・`scripts/train_yolo.py`・`_Context/train-notes.md`・`00-weights/`・`04-experiments/` | training-engineer |
   | `src/eval/`・`src/app/routers/eval.py`・`_Context/eval-notes.md`・`tests/test_ci.py` | metric-auditor |
   | `src/evolve/`・`src/app/routers/round.py`・`_Context/prompts/*.md`・`scripts/evolve.py`・`tests/test_ofat.py` | experiment-arbiter |

3. 分三堆印出來：
   - ✅ **本人自己的目錄** —— 正常。
   - ❌ **寫到別人目錄** —— 標紅，寫清楚「誰寫了誰的什麼檔」。動到 🔒 共同地基
     （三份契約 + `main.py` + `bus.py` + `registry.py` + `pyproject.toml`）而且動的人不是
     console-owner，直接判整個閘門 FAIL：契約被非擁有者改過，後面所有比對都失去基準。
   - ⚠️ **不在任何人擁有表裡的檔** —— 也要標。這是 team-roles 的洞不是誰的錯，但**沒有擁有者的檔案
     下一輪就會有兩個人同時改**。目前已知的孤兒至少有 `src/app/procs.py`、`scripts/fetch_weights.py`、
     `scripts/run_m2.py`、`_Context/frontend-notes.md`、`WALKTHROUGH.md`。
     處理方式是提變更請求給 console-owner 補表（走 `api-contract.md` §12），不是自己認養。

4. 順手看一眼有沒有人違反**前綴即發言權**：
   ```
   grep -rn 'append_event' src/app/routers/<某人的 router>.py
   ```
   發了不屬於自己前綴的 type == 越權改別人的目錄（`bus.append_event()` 會丟 `ValueError` 擋下來，
   但擋的是 type 對不對，不是「這個人該不該發」）。

   **對表依據是契約 §4 與 `src/app/bus.py` 的 `TYPE_OWNERS`**（那個 dict 就是 runtime 真的在擋的東西），
   `_Context/team-roles.md` §2.2 那一句**不要拿來對**：

   | 對表來源 | training-engineer 的前綴 | 結果 |
   |---|---|---|
   | 契約 §4 ／ `bus.py:41-62` `TYPE_OWNERS` | `model. probe. train. augment. recipe. sweep.` 六個 | 正確 |
   | `team-roles.md` §2.2 那一句 | `model. probe. train. sweep.` 四個 | 過期，少 `augment.` 與 `recipe.` |

   契約 §12 變更紀錄 2026-09-11 那一行就是補這兩個前綴的，team-roles 當時沒跟著改。
   照 §2.2 對表，你會把合法的 `recipe.frozen` / `augment.decision` 判成越權 —— 一張把對的人判紅的
   稽核表比沒有稽核更糟：下一輪 arbiter 會拿它去砍人家真的該發的事件。
   驗一次再下筆：`sed -n '41,62p' src/app/bus.py`。順手把 §2.2 過期這件事寫進紅燈清單，
   走 §12 派給 console-owner 補表。

---

## C. 契約一致性（契約與實作不一致時，契約是對的）

### C.1 事件 type：實作發過的，契約 §4 有沒有那一列

```
awk '/^## 4\./,/^## 5\./' _Context/api-contract.md | grep -oE '^\| `[a-z][a-z._]*`' | tr -d '|` ' | sort -u > /tmp/gate-contract-types.txt
grep -ohE '"type": *"[^"]*"' runs/*/events.jsonl | sed 's/.*: *"//;s/"//' | sort -u > /tmp/gate-emitted-types.txt
comm -13 /tmp/gate-contract-types.txt /tmp/gate-emitted-types.txt
comm -23 /tmp/gate-contract-types.txt /tmp/gate-emitted-types.txt
```

| `comm` 哪一邊 | 意思 | 怎麼判 |
|---|---|---|
| 只在 emitted（第一個 `comm`） | 實作發了契約沒登記的 type | **FAIL**。前端的 switch 是照契約寫的，這種事件到前端等於靜默掉地上 |
| 只在 contract（第二個 `comm`） | 契約列了但從沒發過 | 對照 §4 那一列的里程碑欄：標 M6/M7 的正常（還沒開工），標 M1–M5 的就是**該做沒做** |

刻意拿 `runs/*/events.jsonl`（真的發生過的）當比對來源而不是 grep 程式碼：程式裡寫了但一次都沒
執行到的 `append_event` 是死碼，**死碼不算實作**。

### C.2 端點：契約 §9 說 501 的，現在是不是還 501

一次打完（server 沒開就整段標「沒驗到」）：

```
curl -s -m 5 -o /dev/null -w '%{http_code} %{url_effective}\n' \
  http://127.0.0.1:8000/api/v1/datasets/ds1/report \
  http://127.0.0.1:8000/api/v1/models/candidates \
  http://127.0.0.1:8000/api/v1/eval/noise-floor/v1 \
  http://127.0.0.1:8000/api/v1/runs \
  http://127.0.0.1:8000/api/v1/runs/r1/provenance
```

| 情況 | 判法 |
|---|---|
| §9 標 501、實際回 501 | 正常（未開工） |
| §9 標 501、實際回 **200** | 契約 §9 的表過期 —— **FAIL 在 console-owner 身上**，要走 §12 把那一列從 501 表搬到「已實作」表 |
| §9 沒列、實際回 501 | 端點漏登記，同樣 FAIL |
| 回 **404 或假資料** | 最嚴重。契約寫明「不准改成 404、不准回假資料」——回假資料會讓前端以為自己接好了，整合當天才發現 |

**已知紅燈（`/gate M3` 第一次跑一定會碰到）**：`GET /api/v1/models/candidates` 在
`src/app/routers/train.py:147` 已經實作、會回 200，契約 §9 卻還把它列在「一律回 501」那張表裡。
照上表這格就是 FAIL、記在 console-owner 身上 —— **這是閘門在做它該做的事，不是你的 agent 寫壞了**，
真因是契約 §9 沒跟上實作。修法是走 §12 把那一列從 501 表搬到下面的「已實作」表，不是去改程式。

錯誤形狀順便看一眼：一律要是 `{"code":"<MACHINE_CODE>","detail":"<繁中一句話>"}`，
冒出 FastAPI 預設的 `422` 就是 §1 的 handler 沒掛上。

### C.3 越權護欄還在不在

```
curl -s -m 5 -o /dev/null -w '%{http_code}\n' -H 'Host: evil.com' http://127.0.0.1:8000/api/v1/stages
```
不是 **403** 就 FAIL。這條是自主模式無人看管時唯一擋住「別人來燒你 GPU」的東西。

---

## D. 輸出（就印這一張表，不要寫感想）

```
閘門 $1 · <日期> · <PASS / FAIL：N 條紅燈>

A 里程碑驗收
| 條件 | 結果 | 證據 |
|---|---|---|
| <team-roles §3 的那一條> | PASS / FAIL / 沒驗到 | <你這次的真實輸出，最多兩行> |

B 越權稽核
| 檔案 | 動的人 | 該誰的 | 判定 |

C 契約一致性
| 條目 | 契約說 | 實作是 | 判定 |

紅燈清單（只列 FAIL，每條四段）
1. 哪一條標準
2. 跑了什麼指令
3. 真實輸出片段（原文貼，不要改寫）
4. 真因（第幾行第幾個函式）+ 該誰修（照 §B 的擁有權表）
```

收尾三條：

- **FAIL 不准自己動手修**。你是閘門不是實作者，修了就沒人驗你修的那一版。把紅燈照擁有者分好，
  用 `/dispatch <角色>` 派回去。
- 全綠才准說「$1 通過」，而且要附「這一版是在什麼 interpreter、什麼 server 狀態下驗的」——
  同一份程式碼在帶不帶 `--extra train` 之下結果不一樣，不寫清楚的綠燈下一個人重現不了。
- 有任何一格是「沒驗到」，結論只能寫「$1 未驗收完成」，不能寫通過。
