---
name: "source-command-code-tour"
description: "為整個 repo 產一份「給新手的導覽地圖」，含 ASCII 結構樹與重點檔案說明"
---

# source-command-code-tour

Use this skill when the user asks to run the migrated source command `code-tour`.

## Command Template

# Code Tour（Repo 導覽生成）

目標：讓一個第一次點進這個 repo 的人，**3 分鐘看懂全貌**。產物寫到 `TOUR.md`。

## 步驟

1. **建結構樹**
   - 用 `find . -maxdepth 3 -type d -not -path '*/\.*' -not -path '*/node_modules*' -not -path '*/Projects/hello-world-*'` 取目錄。
   - 用 ASCII 畫成樹狀圖，**只到第 3 層**，避免噪音。
   - 自動忽略：`.git`、`node_modules`、`__pycache__`、`.venv`、`dist`、`build` 之類。

2. **重點檔案清單**
   - 列出根目錄與每個一級子資料夾的「最該先讀的 1–2 個檔」（README.md 優先；其次是入口程式 / 設定檔）。
   - 每個檔附一句話：「這個檔在做什麼」。
   - 不要把每個檔都列出來，**精選**，目標控制在 15 個檔以內。

3. **章節對照**（如果有 `docs/`）
   - 從 `docs/` 子資料夾名稱推導章節編號，做一個「想學 X 看哪章」的小表格。

4. **最近活動**
   - `git log --oneline -10`，貼成最近 10 個 commit，讓讀者感覺這個 repo 還活著。

## 輸出格式（TOUR.md）

```markdown
# 🗺 Repo 導覽

> 給第一次點進來的你：3 分鐘看懂這個 repo。

## 一句話介紹
<從根目錄 README.md 第一句萃取>

## 目錄結構
\`\`\`
<ASCII tree>
\`\`\`

## 你該先讀的 5 個檔
1. **<path>** — <做什麼>
2. ...

## 我想學 X，該看哪章？
| 你想學 | 去這裡 |
|---|---|
| ... | ... |

## 最近活動
- <hash> <message>
- ...

---
*此檔由 `/code-tour` 自動生成於 <YYYY-MM-DD>，內容會過時，看 commit 時間判斷新鮮度。*
```

寫完後 echo 「TOUR.md 已更新（共 X 行）」並提示使用者可以 commit。
