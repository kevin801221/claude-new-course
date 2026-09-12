---
name: uv-first
description: Use whenever a task involves installing, adding, removing, upgrading, or running Python packages — automatically rewrite pip/poetry/conda commands to use uv. Triggers on phrases like "pip install", "requirements.txt", "venv", "poetry add", "conda install", or whenever a new Python project is being bootstrapped. This repo's standard is uv-only; never use pip.
---

# uv-first

Kevin 的所有 Python 專案統一用 **uv** 管理套件、虛擬環境、執行。**任何 `pip` / `poetry` / `conda` / `venv` / `requirements.txt` 的指令都要被翻譯成 uv 對應指令**。

## 黃金規則

1. **不要 `pip install X`** → 改成 `uv add X`
2. **不要 `pip install -r requirements.txt`** → 改成 `uv sync`（並建議轉成 `pyproject.toml`）
3. **不要 `python script.py`** → 改成 `uv run script.py`
4. **不要 `python -m venv .venv`** → 改成 `uv venv`（uv 會自動建 `.venv`）
5. **不要 `poetry add X`** → 改成 `uv add X`
6. **不要 `conda install X`** → 改成 `uv add X`
7. **dev / test 套件用 `--dev`** → `uv add --dev pytest ruff`
8. **要鎖版本**：uv 自動產 `uv.lock`，commit 進 git

## 指令對照表

| 你想做的事 | ❌ 不要 | ✅ 改成 |
|---|---|---|
| 新建專案 | `python -m venv .venv && pip install ...` | `uv init && uv add ...` |
| 加套件 | `pip install requests` | `uv add requests` |
| 加 dev 套件 | `pip install pytest` | `uv add --dev pytest` |
| 移套件 | `pip uninstall X` | `uv remove X` |
| 同步環境 | `pip install -r requirements.txt` | `uv sync` |
| 跑 script | `python foo.py` | `uv run foo.py` |
| 跑單次工具 | `pip install ruff && ruff check` | `uvx ruff check` |
| 升 Python 版本 | (麻煩) | `uv python install 3.12 && uv python pin 3.12` |
| Jupyter | `pip install jupyter` | `uv add --dev jupyter && uv run jupyter lab` |

## 何時例外（不要硬轉）

- **既有專案明顯用 poetry/conda 管理且 lock 檔在用**：先問使用者要不要遷移
- **CI/CD pipeline 寫死 pip**：建議改但不擅自改
- **公司強制 pyenv + pip**：尊重，但提一句「Kevin 個人偏好 uv」

## 遇到 `requirements.txt` 怎麼辦

不是直接刪，是**轉換**：

```bash
# 把 requirements.txt 內容轉到 pyproject.toml
uv init  # 若還沒有 pyproject.toml
cat requirements.txt | xargs -I {} uv add {}

# 確認後再刪 requirements.txt
```

## 必要時提醒使用者

如果使用者明確說「我要用 pip」，提一次：「這個 repo 的 AGENTS.md 規則是用 uv，要不要改成 uv？」**問完尊重決定**，不要強行覆寫。

## 安裝 uv（給沒裝過的使用者）

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# 驗證
uv --version
```

## 一句話總結

> **「看到 pip 就轉 uv」是這個 repo 的反射動作。**
