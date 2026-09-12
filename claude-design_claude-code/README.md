# Claude Design × Claude Code：CV 自我訓練介面

> **學什麼**：在 Claude Code 裡用 `/design-sync` + `/design`，從設計系統一路畫到可跑的 CV 訓練 console
> **時長**：90 分鐘
> **產出**：一個能從 Roboflow 載圖、前端預覽、自己分 train/valid/test、匯出 `splits.json` / `data.yaml` 的介面

![CV Self-Training Console](screenshot.png)

## 為什麼這個範例存在？

Claude Code 教學裡「叫 AI 寫個好看的頁面」是最常見也最沒紀律的用法——每次改版顏色都不一樣。這個範例示範正確順序：**先把品牌變成 design system 推上 claude.ai/design，再讓 Claude Design 畫**。順便把 CV 工作流裡真正容易出錯的那一段（分層抽樣）做對。

## 怎麼跑

```bash
cd claude-design_claude-code
python3 -m http.server 8777
```

| 網址 | 看什麼 |
|---|---|
| http://127.0.0.1:8777/prototype/index.html | 成品。按「載入示範資料（離線）」→「照類別分層抽樣」 |
| http://127.0.0.1:8777/prototype/index.html?selftest=1 | 分層抽樣的 9 條 assert |
| http://127.0.0.1:8777/design-system/colors.html | 設計系統元件卡 |

要接真資料：左欄填 Roboflow workspace slug / project slug / **private** API key。
`cp .env.example .env` 記你自己的值（`.env` 已被 gitignore）。

⛔ API key 存在瀏覽器 `localStorage`。**本機自己用可以，不要把這頁部署到公開網址。**

## 檔案

| 路徑 | 是什麼 |
|---|---|
| `WALKTHROUGH.md` | ⭐ 講師逐字教案（Phase 0–7 + 卡點對照表） |
| `design-system/tokens.css` | 色票 / 間距 / 字級的單一真相 |
| `design-system/*.html` | 三個元件預覽，第一行有 `@dsCard` 標記供 `/design-sync` 索引 |
| `prototype/index.html` | 成品，單檔零依賴，含離線示範資料與 selftest |

## 這個範例不做什麼

真的訓練。「開始訓練」是模擬的假 loss 曲線——這堂課教介面。要真的跑訓練去 `../agent_group_projects/computer-vision-wafer-detection/`。
