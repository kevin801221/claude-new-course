---
name: "source-command-daily-haiku"
description: "用今天的 git commit 與工作脈絡，寫一首中文俳句紀念今天"
---

# source-command-daily-haiku

Use this skill when the user asks to run the migrated source command `daily-haiku`.

## Command Template

# Daily Haiku（今日俳句）

把今天的工作壓縮成一首中文俳句，寫進日記檔。這是 Codex 教學 repo 裡用來示範「Slash Command + Bash + 檔案輸出」的有趣案例。

## 步驟

1. 取得今日日期：`date +%Y-%m-%d`，記為 `<DATE>`。
2. 撈今天的 commit：

   ```bash
   git log --since="midnight" --pretty=format:"%h %s" --no-merges
   ```

   若今天沒任何 commit，改撈最近 24 小時：`git log --since="24 hours ago" --pretty=format:"%h %s" --no-merges`。仍空的話，請使用者用一句話描述「今天做了什麼」。

3. 也看一下今天的改動範圍：`git diff --stat HEAD~1 HEAD 2>/dev/null || true`，當作補充意境用。

4. 根據以上素材，創作一首**繁體中文俳句**：
   - 三行，**音節數 5 / 7 / 5**（中文以「字數」近似音節）
   - 必須帶有當日工作的具體意象（例如 commit 的關鍵字、改的檔名、做的事），不可以是空泛的「程式人生很美好」
   - 風格沉靜、不要塞表情符號
   - 同時附一句不超過 30 字的「題記」說明意境出處

5. 寫入 `Projects/haiku/<DATE>.md`（資料夾不存在就先 `mkdir -p`），格式：

   ```markdown
   # <DATE>

   <第一行 5>
   <第二行 7>
   <第三行 5>

   —— <題記>

   ## 今日素材
   - <commit 1>
   - <commit 2>
   ...
   ```

6. 完成後把俳句直接 echo 在對話視窗給使用者讀，並告知檔案路徑。
