---
name: walkthrough-style
description: Use when writing or editing teaching walkthroughs, tutorial docs, lesson plans, or step-by-step guides in this repo. Enforces Kevin's established walkthrough style — Phase-based structure, emoji-tagged sections, "講師金句" callouts, 卡點對照表, 講師私房筆記, and 配套教材 footer. Triggers on "walkthrough", "教學", "lesson", "tutorial doc", "教材", or when creating .md files in docs/walkthroughs/.
---

# walkthrough-style

Kevin 的教學文件有**固定風格**。看現有 4 份（WALKTHROUGH.md / hook_walkthrough.md / anthropics_marketplace_skills_walkthrough.md / four_skills_walkthrough.md）來抓 pattern。

## 標準骨架（每份都要有）

```markdown
# <主題> Walkthrough

> **對象**：誰會看這份
> **形式**：講師現場帶 / 自學
> **時長**：N 分鐘
> **產出**：學完會有什麼可帶走
> **核心方法**：一句話總結教學手法

---

## 開場（5 分鐘）：為什麼要學這個？

<跟其他機制對比的表>

> **教學金句**：「<一句記得住的話>」

---

## 📁 <檔案結構 / 關鍵概念示意圖>

```
<ASCII 樹或 code block>
```

---

## Phase 0：環境準備（5–15 分鐘）
<檢查清單、必裝、必設>

## Phase 1：第一個 <X> ⭐ 最詳細
<逐步驟、含畫面 / 程式碼>

## Phase 2–N：剩下的步驟
<同樣節奏但加速>

---

## 整合 demo：一條龍跑完
<把前面學的串起來>

---

## 常見問題 / FAQ
<學生會問的 3–5 題>

---

## 卡點對照表 ⭐
| 卡點 | 真實原因 | 處理 |
|---|---|---|

---

## 講師私房筆記
- 教學順序心法
- 故意踩坑（比口頭講有效）
- 不同學員角色推薦
- Kevin 親自驗證的真實狀況

---

## 一句話總結

> **<把全篇精華壓成一句>**

---

## 進階閱讀
- 🔗 link 1
- 🔗 link 2

---

_Last updated: YYYY-MM-DD_
_Maintainer: Kevin (kevin@legalsign.ai)_
_配套教材（同目錄）：<sibling files>_
```

## 風格規則

### 1. Emoji 使用（克制但一致）

| 用途 | 用什麼 |
|---|---|
| 階段標題 | 🧠 📋 ⚙️ 📝 🛠 🔌 📄 🎨（每個 phase 一個固定圖案） |
| 重要 | ⭐ ⭐⭐⭐ |
| 警告 | ⚠️ ⛔ |
| 提示 | 💡 |
| 完成 | ✅ |
| 失敗 | ❌ |
| 教學金句段落 | > **教學金句**：「...」 |

**不要**到處亂貼 emoji，每個只在固定情境用。

### 2. 講師金句格式

```markdown
> **教學金句**：「<一句精華>」
```

每個 phase / skill 至少 1 句。寫法**像口語但有記憶點**：

✅ 好的：「Hook 不會問 Codex 同意才跑——它是『管 Codex 的 harness』。」
❌ 不好：「Hooks are useful for automation.」

### 3. 表格優先於條列

兩件以上的對比 → 用表格，不要 bullet。

### 4. 卡點對照表必備

學完一定要有「**真實會卡的點 + 處理方法**」對照表。**不是想像的**，是真的踩過的坑。

### 5. 講師私房筆記必備

包含：
- 教學節奏建議（哪段花多少時間）
- 故意踩坑的點（比口頭講有效）
- 給不同角色的推薦
- Kevin 自己驗過的真實狀況（「我親自測過 X」）

### 6. 「一句話總結」結尾

通篇精華壓成 1–2 句，**粗體**包起來。

## 語氣

- **繁體中文為主**
- 技術術語保留英文（hook, agent, skill, MPS, YOLOv8）
- **白話**，不要學術腔
- 用「你」直接對讀者說話，不要「使用者」「學員」這種第三人稱
- 敢用比喻：「像書店裡的書」「像給新人手冊」

## 不要

- ❌ 不要用簡體中文
- ❌ 不要全英文
- ❌ 不要堆超長 paragraph（拆成短句、表格、code block）
- ❌ 不要寫成「官方文件式」枯燥列表
- ❌ 不要漏掉「為什麼」（每個 step 都要說明 why，不只 what）
- ❌ 不要忽略教學金句（這是 Kevin 風格的辨識度）

## 檔名規則

- 通用 Codex 教學：`<topic>_walkthrough.md`，放 `docs/walkthroughs/`
- 專案特定：`WALKTHROUGH.md` 放專案根
- 全部小寫 + 底線分隔，**除非**是專案級的就大寫 `WALKTHROUGH.md`

## 配套教材 footer

```markdown
_Last updated: 2026-MM-DD_
_Maintainer: Kevin (kevin@legalsign.ai)_
_配套教材（同目錄）：<其他 .md>_
_專案實戰案例：<../../path/to/project/WALKTHROUGH.md>（若有）_
```

## 寫之前先問自己

1. **目標讀者是誰？**（新手 / 中階 / 老手）
2. **時長多少？**（90 / 120 / 150 分鐘）
3. **學完帶走什麼？**（具體 artifact，不是「對 X 有了解」）
4. **跟現有教材關係？**（接續 / 並行 / 替代）

回答完再動手寫。

## 一句話總結

> **Kevin 的 walkthrough = Phase 結構 + 教學金句 + 卡點表 + 私房筆記。沒這 4 件就不像他的風格。**
