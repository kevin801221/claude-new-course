---
name: tutorial-scaffold
description: Use when creating a new tutorial subdirectory under Projects/ or agent_group_projects/ — bootstraps a standard structure with README.md, AGENTS.md, .gitignore, .env.example, and folder skeleton. Triggers on "新教學專案", "建一個新範例", "start a new tutorial project", "scaffold", "新增 Projects/", or whenever creating a new sub-folder for teaching purposes.
---

# tutorial-scaffold

Kevin 的 tutorial repo 底下會持續長新的教學子專案（`Projects/<NN-name>/` 或 `agent_group_projects/<name>/`）。**每個新子專案開的時候有固定起手式**。

## 觸發時機

使用者說「我要在 Projects/ 底下開一個新的 X」「建一個教 Y 的範例專案」「scaffold 一個 Z 教學」這類，**就跑這套流程**。

## 標準目錄結構

```
Projects/<NN-name>/                    # 或 agent_group_projects/<name>/
├── README.md                          # 給學生看的入口
├── AGENTS.md                          # 給 Codex 看的規則
├── .gitignore                         # 標準 ignore
├── .env.example                       # 環境變數樣板（不含真值）
├── pyproject.toml                     # uv 管理（若 Python 專案）
├── src/                               # 或主程式碼資料夾
└── _Context/                          # 可選：放給 Codex 看的背景
    └── lesson-flow.md
```

## 起手式 6 步

### Step 1：確認名字 + 編號

問使用者：
- **名字**（短、kebab-case）：例如 `wafer-detection`、`fastapi-rag`
- **編號**（看 `ls Projects/` 抓下一個）：`07`、`08`...
- **要放哪**：`Projects/` (個人專案教學) vs `agent_group_projects/` (多 agent 協作)

格式：`Projects/<NN>-<name>/` 例如 `Projects/07-fastapi-rag/`

### Step 2：建資料夾

```bash
mkdir -p Projects/<NN>-<name>/{src,_Context}
cd Projects/<NN>-<name>
```

### Step 3：寫 README.md（給學生看）

模板：

```markdown
# <NN>. <名字>

> **學什麼**：一句話講核心目標
> **時長**：N 分鐘
> **產出**：學完會有什麼可帶走

## 為什麼這個範例存在？

<2–3 句說明這個範例在 Codex 教學脈絡裡的位置>

## 你會用到的 Codex 功能

- [ ] /agents
- [ ] hooks
- [ ] skills
- [ ] slash commands
- [ ] sub-agents

## 起手式

```bash
cd Projects/<NN>-<name>
uv sync
```

## 流程

1. ...
2. ...
3. ...

## 進階閱讀

- 配套教材：`../../docs/walkthroughs/<相關 walkthrough>.md`
```

### Step 4：寫 AGENTS.md（給 Codex 看的規則）

模板：

```markdown
# <NN>. <名字> — Codex 規則

教學用乾淨資料夾。

## 目標
<一句話>

## 規則
- Python 套件用 uv 管理
- Mac 環境（MPS）
- 路徑用 pathlib.Path
- 回覆繁體中文
- <其他專案特定規則>

## 不要做的事
- 不要動 `_Context/` 的內容（那是教學素材）
- 不要 hardcode API key（用 .env）
```

### Step 5：寫 .gitignore

```
# Python
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/

# uv
.python-version

# 環境變數
.env
.env.local

# OS
.DS_Store

# 教學產出（每次跑會重生）
Projects/
output/
*.log

# 模型 / 大檔
*.pt
*.pth
*.onnx
data/raw/
data/processed/
```

（若是專案根的 `.gitignore` 已存在，子資料夾不一定要再寫）

### Step 6：寫 .env.example

```bash
# 範例環境變數（複製成 .env 並填真值）
# DO NOT COMMIT .env

# Example:
# ROBOFLOW_API_KEY=your_key_here
# OPENAI_API_KEY=sk-...
```

### Step 7：若是 Python 專案 → `uv init`

```bash
uv init --no-readme  # 因為我們自己寫 README
# 編輯 pyproject.toml 填 name / description
uv add <核心套件>
```

### Step 8：（可選）寫 `_Context/lesson-flow.md`

如果這是給學生跟著做的教學專案，寫一份「lesson flow」放 `_Context/`，內含：
- 課程節奏（Phase 1, 2, 3...）
- 每段時長
- 卡點對照表

## 何時不要全套跑

- **使用者只要 1 個檔的小範例**（例如 hello-world）→ 只建 README + 1 個檔
- **使用者明說「我自己會弄」**→ 只開資料夾退出
- **既有資料夾要重整**→ 先 `ls` 看現況，不要覆蓋

## 完成後回報格式

```markdown
✅ 已建立 Projects/<NN>-<name>/

結構：
- README.md（給學生看）
- AGENTS.md（給 Codex 看）
- .gitignore / .env.example
- pyproject.toml（uv 已 init）
- src/ 空資料夾

下一步建議：
1. 編輯 README 補上具體 lesson flow
2. uv add <你會用到的套件>
3. 建第一個 src/ 檔開始寫
```

## 命名慣例

- 子資料夾：`<NN>-<kebab-case-name>`（雙位數編號 + 連字號名字）
- Python 套件：底線分隔 `wafer_detection`
- README 標題：「<NN>. <人類友善名稱>」

## 編號規則

- 看 `ls Projects/` 找最大編號 + 1
- 不要跳號（07 之後就是 08，不要 10）
- 不要重複編號

## 一句話總結

> **新範例 = 6 個檔（README、Codex、.gitignore、.env.example、pyproject.toml、src/）。少於 6 個問為什麼，多於 6 個也問為什麼。**
