---
name: commit-zh
description: Use whenever the user asks to commit changes, write a commit message, or run `git commit`. Produces Traditional Chinese commit messages in Kevin's personal style — concise, no "Codex" attribution, no emoji unless explicitly requested. Triggers on "commit", "幫我 commit", "寫 commit msg", "git commit", or when finalizing a series of edits.
---

# commit-zh

寫 **Kevin 風格的繁體中文 git commit message**。

## 黃金規則（不可協商）

1. **作者要是 Kevin 不是 Codex**
   - ❌ `Co-Authored-By: Codex` / `🤖 Generated with Codex`
   - ✅ 純淨 commit，**不加任何 AI 署名**
2. **繁體中文** — 不是簡體、不是英文（除非檔名 / 變數名）
3. **個人帳號 commit**（除非使用者明說「這是工作專案」）
   - `git config user.name "kevin801221"`
   - `git config user.email "kevin801221@users.noreply.github.com"`
4. **不用 conventional commit 強制前綴**（feat:/fix:/...）——可選用但不要硬塞
5. **第一行 ≤ 50 字元中英混合**，需要細節再寫第二段
6. **不空泛**：「更新檔案」「修 bug」這種一律重寫

## 結構

```
<簡短主旨：做了什麼、為什麼>

<可選 body：背景、影響範圍、注意事項>
```

## 範例（學這個寫法）

✅ **好的**：
```
新增 inference-runner agent 與 test set 視覺化

跑完 YOLOv8 test 推論並產 10 張帶 bbox 的 PNG，
mAP@0.5 0.991 / mAP@0.5:0.95 0.763。結果寫到
Projects/2026-001-mvp/05-results/。
```

✅ **好的（短）**：
```
修 bbox-labeler 切分比例計算錯誤
```

✅ **好的（多檔重構）**：
```
把 walkthrough 教材搬到 docs/walkthroughs/

3 份通用 Codex 教學（hooks / skills marketplace /
4-skill 工作流）跟 wafer 專案無關，搬出來統一管。
WALKTHROUGH.md 保留在 wafer 專案內。
```

❌ **不要的**：
```
update files

Co-Authored-By: Codex <noreply@anthropic.com>
🤖 Generated with [Codex](https://Codex.com/Codex)
```

❌ **不要的**：
```
fix
```

❌ **不要的**：
```
feat: 新增功能
```
（前綴沒問題，但主旨太空）

## 流程

當使用者說「幫我 commit」：

1. **跑 `git status` + `git diff --stat`** 看改動範圍
2. **分析**：是新增、修復、重構、文件、設定？影響哪些檔？
3. **問自己「為什麼」**：這個改動解決什麼問題？
4. **寫主旨**：一句話講「做了什麼」（必要時加「為什麼」）
5. **判斷要不要 body**：
   - 影響 < 3 檔且邏輯單純 → 只要主旨
   - 影響 > 3 檔或有 tradeoff → 加 body 解釋
6. **跑 commit**（用 HEREDOC 保留格式）：

```bash
git commit -m "$(cat <<'EOF'
<主旨>

<body if needed>
EOF
)"
```

7. **commit 完跑 `git status`** 確認乾淨

## 處理 pre-commit hook 失敗

1. **不要 `--no-verify`**（除非使用者明說）
2. 修 hook 抓到的問題
3. 重新 `git add` + **新 commit**（不要 `--amend`）

## 多 commit 拆法（當改動跨主題）

如果一次改動牽涉 3 個獨立主題，先問使用者要不要拆：

```
我看到改動跨 3 個主題：
1. 新增 inference-runner（功能）
2. 修 bbox-labeler 路徑 bug（修復）
3. 更新 README（文件）

要拆 3 個 commit 嗎？還是合一個？
```

## 大膽用中文，敢用粗的詞

| 太溫吞 | 改成 |
|---|---|
| 「進行調整」 | 「砍掉」「重寫」「拆出來」 |
| 「優化效能」 | 「平行化推論」「減少 50% latency」 |
| 「相關修改」 | 直接寫做了什麼 |
| 「進行重構」 | 「拆成 X 跟 Y」 |

## 一句話總結

> **commit msg = 給未來 6 個月後的自己讀的。中文白話、具體、不署名 AI。**
