"""console-owner 的 router —— 那一顆按鈕、唯一的 SSE 出口、run 快照、Stop。

契約：`_Context/api-contract.md` §8.1–§8.4（M1 實作）、§5（SSE 重連）、§9（provenance 佔位）。
擁有者：console-owner。M1 這四支是真的要能用的（其餘四個 router 在 M1 一律 501）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import bus, registry
from . import dataset

router = APIRouter(tags=["console"])

MODES = ("oneshot", "manual", "autonomous")
SOURCES = ("demo", "roboflow", "local")
PRESETS = ("teaching", "real")
M1_MODE, M1_SOURCE = "oneshot", "demo"

# run_id → driver task。Stop 鈕靠它停掉 s01–s03 的產生器（契約 §8.4 的 M1 語意）。
_TASKS: dict[str, asyncio.Task] = {}


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


class CreateRun(BaseModel):
    mode: str = M1_MODE
    source: str = M1_SOURCE
    preset: str = "teaching"
    limit: int = 120
    seed: int = 42


# ---------- driver（`scripts/run_all.py` 呼叫同一個函式，不是第二套邏輯） ----------

async def drive_run(run_id: str) -> None:
    """依序驅動 s01 → s03（M1 的範圍）。s02/s04..s11 一律 skipped。

    每個 stage 的實作在擁有者的模組裡（s01/s03 = dataset-truth 的 `routers/dataset.py`），
    這裡只負責「順序 + 狀態 + 失敗要看得見」。未預期例外一律記一筆 `stage.failed` 再標 failed
    （契約 §10：`500` 不在契約裡，前端要把進度條轉紅而不是靜靜卡住）。
    """
    state = registry.read_state(run_id)
    if state is None:
        raise KeyError(run_id)
    ds_id, limit, seed, source = state["ds_id"], state["limit"], state["seed"], state["source"]
    preset = state["preset"]  # teaching 會放慢逐張節奏，讓「一張一張進來」在台上看得見
    registry.set_status(run_id, "running")
    stage = "s01"
    try:
        registry.set_stage(run_id, "s01", "running")
        total = await dataset.run_ingest(
            run_id=run_id, ds_id=ds_id, source=source, limit=limit, seed=seed, preset=preset
        )
        registry.set_counts(run_id, total=total, loaded=total)
        registry.set_stage(run_id, "s01", "done")

        stage = "s03"
        registry.set_stage(run_id, "s03", "running")
        labeled = await dataset.run_autolabel(
            run_id=run_id, ds_id=ds_id, mode="refine_only", preset=preset
        )
        registry.set_counts(run_id, labeled=labeled)
        registry.set_stage(run_id, "s03", "done")

        registry.set_status(run_id, "done")
    except asyncio.CancelledError:
        raise  # cancel 端點已經寫好 stop 事件與 cancelled 狀態，這裡不重複寫
    except Exception as exc:  # noqa: BLE001 —— 任何失敗都要變成看得見的事件
        detail = f"{type(exc).__name__}: {exc}"
        registry.set_stage(run_id, stage, "failed")
        registry.set_status(run_id, "failed")
        bus.append_event(
            run_id,
            stage=stage,
            type="stage.failed",
            actor="console-owner",
            data={"stage": stage, "detail": detail},
            text=f"stage.failed {stage} — {detail}",
        )
    finally:
        _TASKS.pop(run_id, None)


# ---------- M1 實作的四支 ----------

@router.post("/runs", status_code=202)
async def create_run(body: CreateRun) -> JSONResponse:
    """開一個 run（前端那一顆按鈕）。契約 §8.1。"""
    for name, value, table in (
        ("mode", body.mode, MODES),
        ("source", body.source, SOURCES),
        ("preset", body.preset, PRESETS),
    ):
        if value not in table:
            raise _err(400, "BAD_ENUM", f"{name} 只接受 {list(table)}，收到 {value!r}")
    if body.mode != M1_MODE:
        raise _err(501, "NOT_IMPLEMENTED", f"mode:{body.mode!r} 於 M3/M7 實作；M1 只有 oneshot")
    if body.source != M1_SOURCE:
        raise _err(501, "NOT_IMPLEMENTED", f"source:{body.source!r} 於 M8 實作；M1 只有 demo")
    if not 1 <= body.limit <= 500:
        raise _err(400, "BAD_RANGE", f"limit 必須在 1..500，收到 {body.limit}")
    if (busy := registry.running_run()) is not None:
        raise _err(409, "RUN_IN_FLIGHT", f"{busy} 還在跑（GPU 佇列深度 1），先 cancel 或等它結束")

    state = registry.create_run(
        mode=body.mode, source=body.source, preset=body.preset, limit=body.limit, seed=body.seed
    )
    run_id = state["run_id"]
    stages = registry.stage_skeleton()
    bus.append_event(
        run_id,
        stage="s01",
        type="run.created",
        actor="console-owner",
        data={
            "run_id": run_id,
            "mode": body.mode,
            "source": body.source,
            "preset": body.preset,
            "stages": stages,
            "ds_id": state["ds_id"],
        },
        text=f"$ autocv run --mode {body.mode} --source {body.source} --limit {body.limit}",
    )
    _TASKS[run_id] = asyncio.create_task(drive_run(run_id))
    return JSONResponse(
        status_code=202,
        content={
            "run_id": run_id,
            "ds_id": state["ds_id"],
            "status": "running",
            "created_at": state["created_at"],
            "events_url": f"/api/v1/runs/{run_id}/events?since=0",
            "ds_version": state["ds_version"],
            "label_version": state["label_version"],
            "class_table_version": state["class_table_version"],
            "stages": stages,
        },
    )


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    since: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    """全系統唯一的 SSE 出口。契約 §5。

    `Last-Event-ID` 與 `?since=` 語意相同（回 `seq > N`），**兩者並存時 Last-Event-ID 勝** ——
    瀏覽器重連沿用原始 URL（可能還掛著 `since=0`），讓 URL 勝就會每次斷線都從頭重播。
    """
    if registry.read_state(run_id) is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 不存在")  # 不准回空串流，前端會一直等
    raw = last_event_id if last_event_id not in (None, "") else since
    cursor = 0
    if raw not in (None, ""):
        try:
            cursor = int(raw)
        except ValueError:
            raise _err(400, "BAD_RANGE", f"since 必須是非負整數，收到 {raw!r}") from None
        if cursor < 0:
            raise _err(400, "BAD_RANGE", f"since 必須是非負整數，收到 {cursor}")
    return bus.sse_response(run_id, cursor)


@router.get("/runs/{run_id}")
def run_snapshot(run_id: str) -> dict[str, Any]:
    """快照：後進場、長離線、SSE 全掛都靠這支（契約 §8.3）。"""
    snap = registry.snapshot(run_id)
    if snap is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 不存在")
    return snap


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    """整條 run 中止（契約 §8.4）。

    M1 語意：停掉 s01–s03 的產生器 + 寫一筆 `stop {reason:"user_cancel"}` + run 標 cancelled。
    **M1 不轉發 train/cancel、不關 autonomy**（M3/M7 才有那兩個對象）。
    """
    state = registry.read_state(run_id)
    if state is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 不存在")
    if state["status"] not in registry.LIVE_STATUSES:
        raise _err(
            409, "RUN_NOT_CANCELLABLE", f"run {run_id} 已經是 {state['status']}，沒有東西可以停"
        )

    task = _TASKS.pop(run_id, None)
    if task is not None:
        task.cancel()
    running_stage = next(
        (s for s, st in state["stages"].items() if st == "running"), "s01"
    )
    registry.set_stage(run_id, running_stage, "failed")
    registry.set_status(run_id, "cancelled")
    event = bus.append_event(
        run_id,
        stage=running_stage,
        type="stop",
        actor="console-owner",  # 契約 §8.4 的明文例外（§4 的擁有者是 arbiter）
        data={"reason": "user_cancel", "best_run": None, "best_pt": None},
        text="stop — 使用者按下 Stop",
    )
    return {"run_id": run_id, "status": "cancelled", "stop_seq": event["seq"]}


# ---------- M6 佔位 ----------

@router.get("/runs/{run_id}/provenance")
def provenance(run_id: str):
    """plan vs actual 逐欄比對（DESIGN 護欄 7；回應形狀見契約 §9，M6 由 console-owner 實作）。"""
    raise _err(501, "NOT_IMPLEMENTED", "此端點於 M6 實作（擁有者 console-owner）")
