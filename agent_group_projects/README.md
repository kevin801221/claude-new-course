# agent_group_projects — 多 agent 協作真實案例集

> 這裡收「**一個任務拆給一群 agent 協作**」的完整教學案例。
> 跟 `../Projects/`(單一功能的 mini-project)不同,這裡每個資料夾都是一條**多 agent 的生產線**。
> 想看「多 Claude 實例平行開發」的型態,去隔壁 [`../agent_teams/`](../agent_teams/README.md)。

## 案例總覽

| 資料夾 | 一句話 | 性質 | 主軸 feature |
|---|---|---|---|
| [`computer-vision-wafer-detection/`](computer-vision-wafer-detection/CLAUDE.md) | 4-agent 跑通晶圓瑕疵 YOLO pipeline(抓資料→標註→訓練→推論) | **完成版 / 講師答案** | sub-agents 分階段協作 |
| [`computer-vision-wafer-template/`](computer-vision-wafer-template/README.md) | 同上的**空白模板**,學生用 `/agents` 親手建 4 個 agent | **學生練習版** | 親手建 sub-agent |
| [`computer-vision-wafer-agents-detection-demo/`](computer-vision-wafer-agents-detection-demo/CLAUDE.md) | 只示範「用 `/agents` 建 4 個 agent」的乾淨 demo,不真的跑訓練 | **課堂 demo 版** | `/agents` 指令本身 |
| [`computer-vision-allblank-forblank/`](computer-vision-allblank-forblank/) | 比模板更徹底的全空白練習版(連 _Context 都讓學生自己長) | **待設計** | 從零搭整個工作區 |
| [`new-course-material2presentation-blank/`](new-course-material2presentation-blank/README.md) | 丟主題自動產出整套課程(大綱/投影片/範例/評量/講師備忘)的課程工廠 | **空白模板** | sub-agent + skill 組生產線 |
| [`stock-groups-skills/`](stock-groups-skills/README.md) | 4 個分析師(基本面/技術面/新聞情緒)+ 1 個彙整,平行鏡頭研究同一檔股票 | **完成版** | 平行鏡頭 + 收斂 |
| [`cv-self-evolving-console/`](cv-self-evolving-console/README.md) | 5 位職責不重疊的專家 + 一條事件流,蓋出「AI 自己標資料→自己選模型→自己判進步→自己討論下一輪」的主控台 | **M1–M5 完成,M6–M7 未開工** | 契約先凍結 + 球員不得兼裁判 |

## 晶圓三件套怎麼分(學生最容易搞混)

同一個 wafer 案例有三個資料夾,用途不同:

| 你想… | 開哪個 |
|---|---|
| 看完成的答案、對 demo 給客戶 | `wafer-detection`(完成版) |
| 自己動手從空白建一遍 | `wafer-template`(學生版) |
| 只想學 `/agents` 怎麼建 agent,不跑訓練 | `wafer-agents-detection-demo`(demo 版) |
| 連工作區架構都想自己設計(最進階) | `computer-vision-allblank-forblank`(待設計) |

> **教學金句**:「completed 是答案、template 是考卷、demo 是示範題 —— 同一題,三種教法。」

## 兩種 agent team 型態(這裡 vs agent_teams/)

| | 這裡(agent_group_projects) | 隔壁(agent_teams) |
|---|---|---|
| 型態 | 主對話委派 sub-agents,分工協作 | 多個 Claude 實例,各自一個 git worktree |
| 隔離 | 各自獨立 context | 檔案系統層級(各自一份 checkout) |
| 收斂 | 主對話彙整 | git merge |
| 範例 | wafer 系列、stock、course-factory | `ios-app-flutter-dev`(Flutter 冥想 App) |

## 推薦學習順序

1. **wafer-agents-detection-demo** —— 最輕,先學 `/agents` 怎麼建一個 agent
2. **wafer-template** —— 自己動手把 4-agent pipeline 建起來
3. **wafer-detection** —— 對照完成版,看「自動優化」怎麼往上長(Phase 2-4)
4. **stock-groups-skills** —— 換個型態:沒有先後階段的「平行鏡頭 + 收斂」
5. **new-course-material2presentation-blank** —— 進階:sub-agent + skill 組成生產線
6. **computer-vision-allblank-forblank** —— 最進階:從零設計整個工作區(待設計)
7. **cv-self-evolving-console** —— 最進階:5 位專家先凍契約再平行開工,學「分權」怎麼防 AI 自欺

## 對應教學文件

| 想看 | 看哪 |
|---|---|
| 概念 + 為什麼這樣設計 | `../Claude_Code_完整教學.pptx`(Part 7 Sub-agents) |
| sub-agent 省 context 的心智模型 | `../docs/walkthroughs/agent_team_walkthrough.md` |
| 單一功能的小範例(slash command / hook / skill…) | `../Projects/README.md` |
| 多 Claude 平行 worktree | `../agent_teams/README.md` |

每個案例都可以**獨立完成**,照各自的 README / CLAUDE.md / WALKTHROUGH.md 跑即可。
