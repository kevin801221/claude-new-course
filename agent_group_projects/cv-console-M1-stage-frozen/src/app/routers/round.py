"""experiment-arbiter 的 router —— 討論、記憶、budget 三件事（不擁有任何 ML 變因）。

契約：`_Context/api-contract.md` §9（全部 M0 佔位回 501）。設計：`_Context/DESIGN.md`
「實驗歸因裁決官」那節（API 表 6 支）＋「討論迴圈」「自我進化模式」「收斂與放棄條件」。
擁有者：**experiment-arbiter**（M6 起）。console-owner 只在 M0 建這份骨架讓 app 掛得起來。

M6/M7 實作時的三條硬規：
  1. 五位專家在**執行期**不是 `.claude/agents/*.md`，而是 `_Context/prompts/<slug>.md` +
     `claude -p --output-format stream-json` subprocess（Claude Code 沒有原生 agent-call-agent）。
  2. `POST /autonomy {on:true}` 不給 preset 或缺任何一軸上限 → 400 `AUTONOMY_NO_CAP`。
     **沒有「無限進化」這個選項。**
  3. 提案去重比對**凍結後 recipe diff 的實際鍵名**，不是自然語言描述。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["round"])


def _ni(milestone: str) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={
            "code": "NOT_IMPLEMENTED",
            "detail": f"此端點於 {milestone} 實作（擁有者 experiment-arbiter）",
        },
    )


@router.post("/chat")
def chat():
    """使用者自然語言 → {ask:{metric,class,direction}, forbid[], round_id} 並開一輪討論。"""
    raise _ni("M6")


@router.post("/rounds")
def open_round():
    """開一輪專家討論（平行 spawn 四位；console-owner 不參與，它刻意不懂 CV）。"""
    raise _ni("M6")


@router.get("/rounds/{round_id}")
def round_transcript(round_id: str):
    """一輪的完整逐字稿（關掉瀏覽器再回來靠這支補完整）。"""
    raise _ni("M6")


@router.post("/rounds/{round_id}/apply")
def apply_round(round_id: str):
    """裁決 → 下一個 run {next_run_id, parent_run, rerun_from, reused_stages[], est_min}。"""
    raise _ni("M6")


@router.post("/autonomy")
def autonomy():
    """自我進化開關。preset 決定三軸預算；不給上限直接 400 `AUTONOMY_NO_CAP`。"""
    raise _ni("M7")


@router.get("/runs")
def list_runs():
    """記憶讀出：runs.jsonl 全量（?format=md 產人讀版 experiments.md）。寫入一律經 registry。"""
    raise _ni("M6")
