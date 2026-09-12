# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Claude Code 公開課教學素材庫

> 這份 CLAUDE.md 給「**新 Cowork session / Claude Code CLI session**」一進來就能立刻接手用的。
> 主人 KevinLuo 的 Claude Code 公開課教學包，目前 338 頁完整版 PPT + 多個專案範例。

## 專案目標

幫 KevinLuo 維護「Claude Code 公開課教學包」，包含：
1. 完整版 PPT（給開發者）
2. 教學版 PPT（給混合 audience / 對外 demo）
3. 完整建構指南 .md
4. Projects/ 內多個可跑專案範例
5. agent_group_projects/ 多 agent 真實案例

## 檔案結構（重要！熟悉這個再開工）

```
claude-code-complete-tutorial/
├── CLAUDE.md                          ← 你正在讀的（給 AI 接手用）
├── README.md                          ← 給人類使用者看的
├── WALKTHROUGH.md                     ← 教學總指揮譜（依 PPT 順序帶課，何時切 IDE 開哪個資料夾）
├── Claude_Code_完整教學.pptx           ← 主簡報 (338 頁)
├── Claude_Code_教學版.pptx             ← 簡化版 (55 頁)
├── Claude_Code_建構指南.md             ← reference (1559 行)
├── build_ppt_完整版.js                ← 完整版 PPT 源碼 ⭐
├── build_ppt_教學版.js                ← 教學版 PPT 源碼
├── build_ppt/                         ← 同上的 backup 副本
├── .claude/                           ← 本 repo 自己的 Claude Code 設定（會生效！見下節）
│   ├── settings.json                  (掛了 guard-secrets + notify-done hook)
│   ├── hooks/                         (8 支教學 hook 腳本 + 對照表 README)
│   ├── skills/                        (commit-zh / walkthrough-style / tutorial-scaffold / uv-first / mac-mps-default)
│   └── commands/                      (changelog-zh / code-tour / daily-haiku / explain-this / hello-world / quiz)
├── docs/walkthroughs/                 ← 講師逐字教案（依 walkthrough-style 風格寫）
│   ├── course_12hr_walkthrough.md     (12 小時完整課帶課譜)
│   ├── agent_team_walkthrough.md      (08 用 agent team 蓋 Kanban 看板)
│   ├── four_skills_walkthrough.md     (四技能組合)
│   ├── hook_walkthrough.md            (Hooks 心智模型)
│   ├── gitnexus_walkthrough.md        (GitNexus knowledge graph 工具深入・Part 10/16)
│   ├── karpathy_skills_walkthrough.md (Karpathy 4 原則 + 最小 plugin 解剖・Part 6/11)
│   └── anthropics_marketplace_skills_walkthrough.md
├── Projects/                          ← 8 個 mini-project + 工具包（見 Projects/README.md）
│   ├── README.md
│   ├── 01-weekly-haiku/               (slash command)
│   ├── 02-recipe-genie/               (sub-agent)
│   ├── 03-youtube-notes/              (skill)
│   ├── 04-pomodoro/                   (hook)
│   ├── 05-organize-downloads/         (MCP)
│   ├── 06-discord-dm-bot/             (Agent SDK)
│   ├── 07-weekly-reports-skill/       (production-grade skill)
│   ├── 08-agent-team-review/          (agent teams：4 人團隊照 SPEC 蓋 Kanban)
│   ├── build-gitnexus-skill-commandline/ (/gitnexus 工具包)
│   ├── plugins-from-zero-to-marketplace/ (3 plugin marketplace 範例)
│   ├── Normal-RAG2Graph-Project-claude/  (RAG→Graph 案例)
│   └── llm-wiki-graph/               (LLM wiki graph 案例)
├── agent_group_projects/              ← 多 agent 真實案例集（有總覽 README.md）
│   ├── README.md                       (案例總覽表 + 晶圓三件套怎麼分 + 學習順序)
│   ├── computer-vision-wafer-detection/  ← 4-agent ML pipeline（含 20K 字 90 分鐘教案，完成版）
│   ├── computer-vision-wafer-template/   (空白模板，學生練習用)
│   ├── computer-vision-wafer-agents-detection-demo/ (只示範 /agents 建 agent 的 demo)
│   ├── computer-vision-allblank-forblank/ (全空白練習版，尚未設計)
│   ├── new-course-material2presentation-blank/ (課程工廠：sub-agent + skill 生產線)
│   └── stock-groups-skills/            (多 agent 個股研究團隊：4 分析師平行鏡頭 + 彙整，已設計)
├── agent_teams/                       ← 多 Claude 實例平行 git worktree 協作（有總覽 README.md）
│   └── ios-app-flutter-dev/            (原生 Agent teams 蓋 arXiv 每日論文閱讀器 App：研究先行→凍結規格與 API 契約→三隊友平行 backend/reader/breathing→整合驗收)
├── context-engineering-intro/         ← Context Engineering（PRP 流程）參考素材
└── 心法/                              ← user 放原始 PDF 心法資料
```

## 本 repo 自己的 `.claude/` 會生效（接手前先知道）

這個 repo 既是教材、也真的掛了一套 Claude Code 設定，**新 session 一進來就會套用**：

### 已啟用的 hook（`.claude/settings.json`）
| 事件 | 腳本 | 行為 |
|---|---|---|
| `PreToolUse`（Write/Edit/MultiEdit） | `guard-secrets.sh` | 寫 `.env` / `*.pem` / `credentials.json` 會被擋（exit 2） |
| `Stop` | `notify-done.sh` | 每輪結束叮咚 + 桌面通知 |

`.claude/hooks/` 還有 6 支**未啟用**的教學範例腳本（inject-*、enforce-prompt-format、audit-bash…）— 它們是教材，**不要為了「整理」就把它們刪掉或啟用**，啟用方式見 `.claude/hooks/README.md`。

### 會主動觸發的 skill（影響你怎麼做事，不只是教材）
| Skill | 何時觸發 | 你必須照做的事 |
|---|---|---|
| `commit-zh` | 任何 commit / 寫 commit message | 繁中、Kevin 個人風格、**不署名 Claude Code**、無 emoji（除非明講） |
| `walkthrough-style` | 寫/改 `docs/walkthroughs/` 或任何教學 .md | Phase 結構、emoji 標籤、講師金句、卡點對照表、配套教材 footer |
| `tutorial-scaffold` | 新增 `Projects/` 或 `agent_group_projects/` 子資料夾 | 用標準骨架（README/CLAUDE.md/.gitignore/.env.example） |
| `uv-first` | 任何 Python 套件安裝 | 一律改寫成 `uv`，**禁止 pip** |
| `mac-mps-default` | 跑 PyTorch / ML 模型 | 預設 MPS 而非 CUDA |

> 這些和全域 CLAUDE.md 的偏好一致（繁中、uv、commit 不署名）。衝突時：使用者明確指示 > skill > 預設。

### 講師教案走 `docs/walkthroughs/`
帶課/逐字教案寫在 `docs/walkthroughs/`，**動到這裡的 .md 一律先觸發 `walkthrough-style` skill**。`course_12hr_walkthrough.md` 是 12 小時完整課的總帶課譜（和根目錄 `WALKTHROUGH.md` 互補：後者是依 PPT 頁碼的指揮譜）。

## 怎麼修改 PPT

**不要直接編輯 .pptx！** 編輯 `build_ppt_完整版.js` 後 regen。

```bash
cd ~/claude-code-complete-tutorial
# 確保有裝
npm install pptxgenjs

# 改完 build_ppt_完整版.js 後跑
node build_ppt_完整版.js
# 輸出 Claude_Code_完整教學_v1.pptx

# Patch totalPages（footer 顯示「N / TOTAL」）
python3 -c "
import zipfile, io
TOTAL = '320'  # ← 改成 node 跑出來的 'Total slides: X' 數字
buf = io.BytesIO()
with zipfile.ZipFile('Claude_Code_完整教學_v1.pptx', 'r') as zin:
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith('ppt/slides/slide') and item.filename.endswith('.xml'):
                data = data.replace(b'TOTAL', TOTAL.encode())
            zout.writestr(item, data)
with open('Claude_Code_完整教學.pptx', 'wb') as f: f.write(buf.getvalue())
"
```

## PPT 設計慣例（必遵守！）

### 視覺風格
- 配色：Midnight Executive（navy `1E2761` + Anthropic 珊瑚 `D97757` accent）
- 字型：標題 Calibri，code block **必用 Menlo**（不要用 Consolas，user Mac 沒裝會破版）
- 16:9 layout

### Slide 類型（用既有 helper，不要重新造輪子）
- `coverSlide` — 封面
- `sectionSlide(num, title, desc)` — 章節分隔頁
- `contentSlide(title, kicker, bullets, opts?)` — 條列頁
- `codeSlide(title, kicker, intro, code, opts?)` — code block 頁
- `compareSlide(title, kicker, leftTitle, leftBullets, rightTitle, rightBullets)` — 兩欄對比
- `resourceSlide(title, kicker, resources[])` — 資源/連結卡片
- `statsSlide(title, kicker, stats[])` — 大數字
- `tocSlide(title, items[])` — 目錄
- `gridSlide(title, kicker, groups[], opts?)` — N 欄彩色卡分組陳列（每組 header + items）
- `timelineSlide(title, kicker, intro, events[], opts?)` — 垂直時間軸（events: { time, label, tag?, kind: human/skill/ship }）
- `pipelineSlide(title, kicker, intro, stages[], opts?)` — 橫向流程圖（stages: { label, sub, kind: input/cmd/file/output }；opts.loops 加迴圈標註）
- `qaSlide(title, kicker, qas[], opts?)` — 學生最常問 Q&A 卡片（qas: { q, a }）

### Code slide 防溢出（最常踩雷）
- 預設 `codeSize: 12` 太大，用 `{ codeSize: 11 }` 多空間
- 沒 intro 時用 `null` 做第 3 參數，多 0.25 吋
- code 太長 → trim 註解 / 合併指令（用 `&&`）
- 用 LibreOffice + pdftoppm 預覽（指令在 README.md）

### Emoji 在 LibreOffice 環境會炸（重要！）
LibreOffice 預覽用的字型沒包含 emoji。`❓ ✅ 🟢 🧠 🛠 🔍 🧰` 這類符號會：
1. 渲染成豆腐方框
2. 連帶把鄰近中文字「吃掉」（emoji 替代字元擠到下一格）
3. 整行排版錯位
**對策**：用文字標記取代 emoji。例：`Q1.` 取代 `❓`、`[PASS-GREEN]` 取代 `🟢`、`1. 設計階段` 取代 `🧠 設計階段`。

### 標題用 eyebrow（kicker）建立分類
格式：`"章節 · 子題目"` 例：`"案例 · agent 1/4"`、`"心法 1 · 場景"`

### 「實戰演練」footer 連結（每個 Part 自動繼承）
在 Part 開頭呼叫 `setPractice("Projects/0X-xxx/")`，該 Part 內所有 slide 的 footer 會自動把
「Claude Code 完整教學 · by KevinLuo」換成 coral 色的「▶ 實戰演練：<path>」。
Part 結尾用 `setPractice(null)` 清掉。當前對應表：
- Part 4 自訂 Slash Commands → `Projects/01-weekly-haiku/`
- Part 7 Sub-agents → `Projects/02-recipe-genie/ ・ agent_group_projects/computer-vision-wafer-detection/`
- Part 8 Skills → `Projects/03-youtube-notes/ ・ Projects/07-weekly-reports-skill/ ・ Projects/build-gitnexus-skill-commandline/`
- Part 9 Hooks → `Projects/04-pomodoro/`
- Part 10 MCP → `Projects/05-organize-downloads/`
- Part 11 Plugins → `Projects/plugins-from-zero-to-marketplace/`
- Part 12 Agent SDK → `Projects/06-discord-dm-bot/`
Section divider（深色 navy 底）會多顯示一張大字版的實戰演練 callout 在 desc 下方。

## User 偏好（被反覆強調過的，要記住）

1. **公開課專用，沒有公司元素** — 移除任何「律果」「法務」字樣
2. **直白、結構化、別講廢話** — 不要「希望這對你有幫助」之類
3. **A/B/C 選項清單會煩** — user 要動作不要分析
4. **教學內容放 claude-code-complete-tutorial** — 不要放 rag-langchain1.0-copilotkitchat（那是公司 repo）
5. **體感慢時不要硬講話** — 直接動手做，做完才報告
6. **「看 PPT 就能教學」是底線** — 每個 feature 要有 WHY/WHAT/HOW/WHEN/PITFALLS/TEST 才算合格

## Plugin 重要釐清（user 之前打臉過我）

**`~/.claude/plugins/` 確實存在**！它是 Claude Code 自動管的 plugin 安裝區（不是 user 手動放東西的地方）：
- `marketplaces/` — 加過的 marketplaces
- `data/` — 已安裝 plugin 的真實檔案
- `cache/` — metadata cache
- `installed_plugins.json` 等 state 檔

**寫 plugin 源碼放獨立 repo**，不要塞 `~/.claude/plugins/`。

## 心法集（可擴展架構）

PPT Part 18·5「心法集」是 user 給 PDF 我轉成 slide 的章節。
未來新增心法 PDF 用同樣 pattern：「心法 N: <PDF 標題>」。
PDF 來源放 `心法/` 資料夾。

## 常見任務 SOP

### 新增章節到 PPT
1. 在 `build_ppt_完整版.js` 找對的插入點（grep `// ===== PART X` 或 section divider）
2. 用既有 helper 寫
3. `node build_ppt_完整版.js` 看新總頁數
4. 用 LibreOffice 預覽幾張新 slide 檢查溢出
5. trim 直到都塞進 dark box
6. cp 到 user 教學資料夾（如果在不同位置）

### 加新 mini-project
1. 在 `Projects/` 建子資料夾 `0N-<name>/`
2. 結構：`.claude/<commands|agents|skills|hooks>/...` + README.md
3. 在 PPT 也加對應 slide（slide 章節結尾「應用實例」風格）
4. 更新 `Projects/README.md` 總覽表格

### 加新心法 PDF
1. user 把 PDF 放 `心法/` 資料夾
2. 用 Read tool 看 PDF（pages: "1-15"）
3. 整理重點為 ~10-15 張 slide
4. 在 PPT 「心法集」section 加新章節「心法 N: <PDF title>」
5. 用同樣 unified style + 既有 helper

## 用 Cowork 還是 Claude Code CLI

- **Cowork（推薦）** — 自動繼承 auto-memory（包含 user feedback 慣例）
- **Claude Code CLI** — 進這個資料夾跑 `claude`，會讀這份 CLAUDE.md

兩種都能接手。Cowork 順暢度更高，因為 user 之前說過的偏好都在 memory 裡。

## 一句話總結

「保持風格、避免溢出、每個概念都要有可執行範例、別碰公司 repo、寫 PPT 就改 .js 不要直接編輯 .pptx。」

---
最後更新：2026-05-13 by Claude (cowork session) — 加「實戰演練」footer 連結機制，Part 4/7/8/9/10/11/12 自動顯示對應 Projects 資料夾路徑
最後更新：2026-05-14 by Claude (cowork session) — Part 11 加 9 張 superpowers + pomocat 案例 slide（14 招齒輪圖、SKILL.md 拆解、Pomocat 時間軸、TDD 示範、踩坑、資源）；新增 gridSlide / timelineSlide 兩個 helper。指向 `important-plugins/superpowers/` 與 `important-plugins/project1-superpowers/`。
最後更新：2026-05-14 by Claude (cowork session) — Part 9 加 2 張 hooks 澄清頁（hook event 不是 slash command、/hooks TUI 是 read-only 檢視器）。新增根目錄 `WALKTHROUGH.md` 教學總指揮譜：依 PPT 順序，標明每個 Part 的頁碼、何時切 IDE、開哪個資料夾、開哪個 README/WALKTHROUGH。
最後更新：2026-05-14 by Claude (cowork session) — Part 18 加 2 張流程視覺化 slide（pipeline 流程圖 + IO 對照表），把 CLAUDE.md+INITIAL.md → /generate-prp → PRP.md → /execute-prp → 功能 講清楚，怕學生迷路。新增 pipelineSlide helper。PPT 338 頁。
最後更新：2026-05-16 by Claude (/init) — 加 init 標準檔頭；檔案結構更新到現況（補 docs/walkthroughs/、Projects/08 與其他、agent_group_projects/ 6 個、context-engineering-intro/、本 repo .claude/）；新增「本 repo 自己的 .claude/ 會生效」一節（已啟用 hook + 會觸發的 skill 對照表）。
最後更新：2026-05-16 by Claude (cowork session) — 全 deck 加教學細節：13 張「學生最常問」Q&A slide（Part 1-12 + 18 結尾各一張）+ 5 張 demo 腳本/深講解頁（Part 4/6/9/10 現場 demo 腳本、Part 7 sub-agent 省 context 心智模型）。新增 qaSlide helper。PPT 338 頁。
最後更新：2026-05-22 by Claude (cowork session) — 補三處缺口：(1) 設計 agent_group_projects/stock-groups-skills（多 agent 個股研究團隊：基本面/技術面/新聞情緒 3 分析師 + research-synthesizer 彙整，含 README/CLAUDE.md/WALKTHROUGH/4 agent/_Context/starter script，全程「非投資建議」免責）；(2) 設計 agent_teams/ios-app-flutter-dev（多 Claude 平行 git worktree 蓋 Flutter 冥想 App，研究先行→規格凍結→平行→merge，含 setup-worktrees.sh + 兩個論文搜集研究 worktree 說明）+ 新增 agent_teams/ 總覽 README；(3) 新增 agent_group_projects/ 總覽 README（案例表 + 晶圓三件套差異 + 學習順序）。註：computer-vision-allblank-forblank 仍待設計。
最後更新：2026-05-22 by Claude (cowork session) — important-plugins/ 再加兩個最受歡迎的官方 plugin 當教學解剖標的：ralph-wiggum（Anthropic 官方，Stop hook 自動迴圈，對應 Part 9 Hooks）、pr-review-toolkit（Anthropic 官方，6 個 review sub-agent + 調度，對應 Part 7 Sub-agents），皆從 anthropics/claude-code/plugins 取出、去巢狀 .git、保留原 README。各寫一份繁中 walkthrough（ralph_wiggum_walkthrough.md、pr_review_toolkit_walkthrough.md）。新增 important-plugins/README.md 索引（pillar 對照 + 出處/授權標註：superpowers=Skills、ralph-wiggum=Hooks、pr-review-toolkit=Sub-agents）。
最後更新：2026-05-22 by Claude (cowork session) — 新增 important-plugins/plugins_install_and_cases_walkthrough.md（三支一起的安裝+實戰教案）：marketplace add 指令（claude-plugins-official=anthropics/claude-plugins-official、claude-code-plugins=anthropics/claude-code、superpowers-marketplace=obra/superpowers-marketplace）、各 plugin install 指令（superpowers@claude-plugins-official、ralph-wiggum@claude-code-plugins、pr-review-toolkit 兩店皆有）、三個逐步 prompt 案例（superpowers 短網址 API、ralph todo API 自動迴圈、pr-review-toolkit PR 審查含 /pr-review-toolkit:review-pr namespace）。指令均對 marketplace.json 查證。

# 基於我可能會換很多的 AI Agent, 請在每一次執行後跟使用者討論要儲存什麼 changelog.md, 我會決定是否要保留。這麼做是因為要切換很多的 Agent 的時候可以無縫接軌（記憶）。