---
name: "source-command-weekly-haiku"
description: "用本週 git log 產出一份可交付主管的正式週報"
---

# source-command-weekly-haiku

Use this skill when the user asks to run the migrated source command `weekly-haiku`.

## Command Template

請依以下步驟產出一份「**可直接交付主管的正式週報**」（繁體中文）：

1. 跑以下指令蒐集本週工作紀錄：
   - `git log --since="7 days ago" --date=short --pretty=format:"%ad | %s" --author="$(git config user.email)"`
   - `git log --since="7 days ago" --pretty=format:"%s" --shortstat --author="$(git config user.email)"`（取檔案/行數異動量）
2. 讀完所有 commit message，將工作**分類歸納**為：
   - 功能開發（新增能力）
   - 問題修復（bug / 線上問題）
   - 重構與優化（不改行為、改體質）
   - 文件與教材
   - 其他（設定、雜項）
3. 依下方「週報格式」輸出。語氣務必**正式、客觀、以成果為導向**，講「完成了什麼、帶來什麼價值」，不要工程口語、不要 emoji、不要俳句。
4. 沒在 commit log 出現的事**不要編造**；若某分類本週無內容，直接寫「本週無」。

## 週報格式

```
# 工作週報

**期間**：YYYY/MM/DD ~ YYYY/MM/DD
**提交人**：<git user.name>

## 一、本週重點摘要
（2-4 句話，主管只看這段就能掌握本週產出與價值）

## 二、完成事項
### 功能開發
- <成果導向描述>（commit: <精簡 hash 或主旨>）

### 問題修復
- ...

### 重構與優化
- ...

### 文件與教材
- ...

## 三、量化指標
- 本週提交次數：N 次
- 影響範圍：約 X 檔、+Y / -Z 行

## 四、風險與待辦
- （從 commit 看得出的未完成項 / 已知問題；無則寫「目前無重大風險」）

## 五、下週計畫
- （由本週脈絡合理推導的接續工作，最多 3 點，標明為「規劃」）
```

注意事項：
- 「本週重點摘要」要寫給**沒看過 commit 的主管**看，不要貼 commit 原文
- 完成事項用**成果語言**（「上線 X 功能，支援 Y」），不要寫「改了某檔某行」
- 「下週計畫」如為推測，要明確標示「規劃」，不要講得像已完成
- 失敗或卡關也據實寫進「風險與待辦」，不美化
