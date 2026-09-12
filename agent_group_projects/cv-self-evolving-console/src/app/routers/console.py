"""console-owner 的 router —— 那一顆按鈕、唯一的 SSE 出口、run 快照、Stop。

契約：`_Context/api-contract.md` §8.1–§8.4、§8.10（stage 表）、§5（SSE 重連）、§9（provenance 佔位）。
擁有者：console-owner。

那一顆按鈕 = `POST /runs` → `drive_run()` 一路驅動 **s01 → s08**：
    s01 進場 → s03 無類別 bbox → s02 分群+命名 → s04 落檔凍結 → s05 探針
    → s06+s07 凍結配方並訓練 → s08 評分與圖表
每段的實作都在**擁有者自己的模組**裡，這裡只負責「順序 + 等它真的結束 + 狀態 + 失敗要看得見」。
`scripts/run_all.py` 呼叫的就是 `drive_run()` 本人，不是第二套 pipeline。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...eval import ceiling as ceil_mod
from ...train import runner
from .. import bus, procs, registry
from . import dataset, eval as eval_router, train as train_router

router = APIRouter(tags=["console"])

MODES = ("oneshot", "manual", "autonomous")
SOURCES = ("demo", "roboflow", "local")
PRESETS = ("teaching", "real")
M1_MODE, M1_SOURCE = "oneshot", "demo"
CHILD_POLL_S = 0.2  # 等子行程退出的輪詢間隔（等的是 waitpid，不是事件，見 `_await_child`）
# 契約 §8.9：oneshot 豁免人工 GO，換來的條件是「三軸預算 + **ETA×2 硬 timeout** + Stop 鈕」。
# 下限 10 分鐘：實測 eta_min 0.17 分（10 秒）而探針真的跑 17.8 秒 —— 只用 ETA×2 的話，
# 健康的一條龍會被自己的 timeout 砍死。這個 timeout 是拿來抓「永遠不會結束」，不是校準 ETA。
CHILD_TIMEOUT_FLOOR_S = 600.0
LOG_TAIL_LINES = 3  # 子行程死掉時，錯誤訊息要帶上 train.log 的最後幾行

# run_id → driver task。Stop 鈕靠它停掉整條龍（訓練子行程另外 SIGTERM，見 `cancel_run`）。
_TASKS: dict[str, asyncio.Task] = {}
# 正在 cancel 的 run。cancel 要殺子行程（阻塞 8 秒寬限期），這段期間狀態還是 running，
# 第二個 Stop 請求會通過狀態檢查 —— 這張表就是那道門（契約 §8.4：第二次要 409）。
_CANCELLING: set[str] = set()


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


class CreateRun(BaseModel):
    mode: str = M1_MODE
    source: str = M1_SOURCE
    preset: str = "teaching"
    limit: int = 120
    seed: int = 42
    stages: list[str] | None = None  # 教學用：只跑前幾段（None = 整條龍 s01→s08）
    # 契約 §8.1 的選填加欄（2026-09-12）：`source:"roboflow"` 那條路的四個旋鈕，逐欄與
    # `POST /datasets/ingest`（§8.5）相同 —— 那顆按鈕與 curl 單步教學走的是同一組函式，
    # 參數長得不一樣就是第二套邏輯。**刻意沒有 api_key 這一欄**：key 只在 server 讀 `.env`。
    labels: str | None = None
    workspace: str | None = None
    project: str | None = None
    version: int | None = None


# ---------- 每個 stage 一段（實作都在擁有者的模組裡，這裡只負責串） ----------

def _state(run_id: str) -> dict[str, Any]:
    state = registry.read_state(run_id)
    if state is None:
        raise KeyError(run_id)
    return state


def _ensure_live(run_id: str) -> None:
    """run 被 cancel 掉就不要再往下跑。

    `task.cancel()` 對「正在 `to_thread` 裡跑推論」的 driver 不會立刻生效（executor 的
    future 收不到取消），所以每段之間再看一次狀態 —— 這是第二道，不是唯一那道。
    """
    if _state(run_id)["status"] not in registry.LIVE_STATUSES:
        raise asyncio.CancelledError


def _body(resp: JSONResponse) -> dict[str, Any]:
    """直接呼叫 router 函式時，202 的 body 還是 JSONResponse —— 這裡拆回 dict。"""
    return json.loads(resp.body)


async def _await_child(run_id: str, train_id: str) -> dict[str, Any]:
    """等訓練/探針子行程**真的退出並被 reap**，再把那筆紀錄結清。

    ⚠️ 不要改成等事件。`model.selected` / `train.done` 是子行程在退出**前**寫的，
    那一刻它還活著約 0.3 秒：立刻打下一支會撞 `409 RUN_IN_FLIGHT`（`_busy_train()` 看的是
    `runner.alive()`），而且沒人 `waitpid()` 它就會掛成 zombie（實測撐了 3 分鐘）。
    `runner.alive()` 內部走 `Popen.poll()`，輪到它就順手收屍，所以這個迴圈同時是 reaper。
    `runner.snapshot()` 才是結清的那一刻（status / stage / GPU 分鐘），少了它下一段會看到
    一筆還寫著 `running` 的訓練。
    """
    eta_min = float((runner.read_train(train_id) or {}).get("eta_min") or 0)
    budget = max(eta_min * 2 * 60, CHILD_TIMEOUT_FLOOR_S)
    deadline = time.monotonic() + budget
    while True:
        rec = runner.read_train(train_id) or {}
        if not runner.alive(train_id, rec.get("pid")):
            break
        _ensure_live(run_id)
        if time.monotonic() > deadline:
            # 契約 §8.9 的硬 timeout：逾時就收乾子行程並讓這一段 failed（進度條轉紅），
            # 不是靜靜卡在這個 while 裡 —— 卡住的話 run 永遠 running、那顆按鈕永久 409。
            await asyncio.to_thread(runner.cancel, train_id)
            raise RuntimeError(
                f"{train_id} 超過硬 timeout {budget / 60:.1f} 分鐘（ETA {eta_min} 分 ×2）還沒結束，"
                f"已強制收乾"
            )
        await asyncio.sleep(CHILD_POLL_S)
    snap = runner.snapshot(train_id) or {}
    if snap.get("status") != "done":
        raise RuntimeError(
            f"{train_id} 結束時狀態是 {snap.get('status')}（不是 done）{_log_tail(snap)}"
        )
    return snap


def _log_tail(snap: dict[str, Any]) -> str:
    """子行程的最後幾行 log。

    子行程的 stdout/stderr 全進 `04-experiments/<train_id>/train.log`（runner.start），而畫面上
    只看得到這個 RuntimeError —— 少了這幾行，台上那句「狀態是 crashed」沒有任何人猜得到真因是
    `ModuleNotFoundError: No module named 'ultralytics'`（server 沒帶 `--extra train` 起）。
    """
    path = snap.get("log_path")
    if not path:
        return ""
    file = bus.PROJECT_ROOT / path
    try:
        tail = [ln for ln in file.read_text("utf-8", "replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return "｜log: " + " / ".join(tail[-LOG_TAIL_LINES:]) if tail else ""


async def _stage_ingest(run_id: str) -> str:
    s = _state(run_id)
    rf = s.get("roboflow") or {}   # demo 是空的 → 四個參數全 None = 舊行為
    total = await dataset.run_ingest(
        run_id=run_id, ds_id=s["ds_id"], source=s["source"], limit=s["limit"], seed=s["seed"],
        preset=s["preset"], labels=s.get("labels"),
        workspace=rf.get("workspace"), project=rf.get("project"), version=rf.get("version"),
    )
    registry.set_counts(run_id, total=total, loaded=total)
    return f"{total} 張進場"


async def _stage_autolabel(run_id: str) -> str:
    s = _state(run_id)
    labeled = await dataset.run_autolabel(
        run_id=run_id, ds_id=s["ds_id"], mode="refine_only", preset=s["preset"]
    )
    registry.set_counts(run_id, labeled=labeled)
    return f"{labeled} 張有框"


async def _stage_cluster(run_id: str) -> str:
    s = _state(run_id)
    out = await dataset.run_cluster(
        run_id=run_id, ds_id=s["ds_id"], preset=s["preset"], seed=s["seed"]
    )
    return f"k={out['k']} · class 表 {out['class_table_version']} · 命名 {out['naming']}"


async def _stage_freeze(run_id: str) -> str:
    s = _state(run_id)
    out = _body(await dataset.freeze(s["ds_id"], dataset.FreezeBody(run_id=run_id, seed=s["seed"])))
    if not out["pass"]:
        bad = "、".join(c["name"] for c in out["checks"] if not c["pass"])
        raise RuntimeError(f"s04 selfcheck 沒過（{bad}）—— 資料集沒過閘門就訓練，數字不可比")
    return f"train {out['split']['train']} / valid {out['split']['valid']} · selfcheck 全過"


async def _stage_probe(run_id: str) -> str:
    out = _body(train_router.probe(train_router.ProbeBody(run_id=run_id, seed=_state(run_id)["seed"])))
    snap = await _await_child(run_id, out["probe_id"])
    return f"探針 {', '.join(out['names'])} → {snap.get('model') or '選好了'}（{snap.get('elapsed_s')} 秒）"


async def _stage_train(run_id: str) -> str:
    out = _body(train_router.train(train_router.TrainBody(run_id=run_id)))
    snap = await _await_child(run_id, out["train_id"])
    return f"{out['model']} · {snap.get('epoch')}/{snap.get('total')} epoch · {snap.get('elapsed_s')} 秒"


async def _stage_eval(run_id: str) -> str:
    ds_version = _state(run_id).get("ds_version") or "v1"
    try:
        out = _body(await asyncio.to_thread(eval_router.evaluate,
                                            eval_router.EvalBody(run_id=run_id, split="valid")))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") != "NOISE_FLOOR_MISSING":
            raise
        # 資料集重新 freeze 過（指紋變了）→ 舊的雜訊帶作廢。一條龍不該停在這裡等人下指令，
        # 但「量一次」是真的燒 GPU（3 次訓練，實測約 65 秒），所以只在缺的時候補。
        bus.append_event(
            run_id, stage="s08", type="stage.note", actor="console-owner",
            data={"stage": "s08", "kind": "noise_floor"},
            text="量 noise floor：同 config 訓 3 次再各推論一次 anchor，約 70 秒不會有事件…",
        )
        await asyncio.to_thread(eval_router.noise_floor, ds_version, measure=True, run_id=run_id,
                                force=True)
        out = _body(await asyncio.to_thread(eval_router.evaluate,
                                            eval_router.EvalBody(run_id=run_id, split="valid")))
    return (f"mAP50-95 {out['map5095']}（CI {out['ci']['lo']}–{out['ci']['hi']}）· "
            f"verdict {out['verdict']} · significant {out['significant']}")


# 一條龍的**執行順序**（不是 rail 的顯示順序：s03 要先有框，s02 才分得了群）。
# 名稱與擁有者的唯一真相是 `registry.STAGE_TABLE`，這裡只放 id，不再抄一次文案。
PIPELINE: tuple[tuple[tuple[str, ...], Any], ...] = (
    (("s01",), _stage_ingest),
    (("s03",), _stage_autolabel),
    (("s02",), _stage_cluster),
    (("s04",), _stage_freeze),
    (("s05",), _stage_probe),
    (("s06", "s07"), _stage_train),  # s06 凍結配方與 s07 起跑是同一支 POST /train
    (("s08",), _stage_eval),
)
PIPELINE_IDS = tuple(sid for ids, _ in PIPELINE for sid in ids)


def select_steps(stages: Sequence[str] | None) -> list[tuple[tuple[str, ...], Any]]:
    """`stages` 挑要跑的段（教學用：只跑前幾段）。None = 整條龍。順序永遠照 PIPELINE。"""
    if stages is None:
        return list(PIPELINE)
    want = set(stages)
    return [step for step in PIPELINE if want & set(step[0])]


async def _label_ceiling_stop(run_id: str) -> dict[str, Any] | None:
    """s03 跑完問一次 DESIGN 的 `label_ceiling` 放棄條件。要停才回 gate，否則 `None`。

    契約 §9：「s03 一跑完就答得出來、不必先訓練 —— 放棄條件要的就是這個時機」。
    ⛔ 這道閘門原本**沒接上**：`ceiling.gate()` 全 repo 只有 `GET /eval/ceiling` 一個非測試
    呼叫者，而那支端點前端從不打。實測把 auto 框縮成 0.45 倍跑 roboflow，s03 量到
    `iou_median 0.2096 < 門檻 0.4`、`gate()` 回 `{"abandon": true}`，driver 照樣一路訓練到
    s08 才說「數字不好」—— 正是 `src/eval/ceiling.py` 自己 docstring 禁止的那一種。
    合成那條路（demo / M1）是**退化值**，`gate()` 永遠 `abandon=False`（退化是「沒量到天花板」
    不是「天花板很低」），所以那顆按鈕的教學主線一步都不會被這道閘門擋下來。
    """
    state = _state(run_id)
    payload = await asyncio.to_thread(ceil_mod.for_run, run_id, ds_id=state.get("ds_id"))
    g = ceil_mod.gate(payload)
    return g if g.get("abandon") else None


# ---------- driver（`scripts/run_all.py` 呼叫同一個函式，不是第二套邏輯） ----------

async def drive_run(run_id: str, stages: Sequence[str] | None = None) -> dict[str, float]:
    """依序驅動 s01 → s08，**每段真的做完才標 done**，全部跑完才把 run 標 done。

    回傳 `{stage_id: 秒}` 給 CLI 印。未預期例外一律記一筆 `stage.failed` 再標 failed
    （契約 §10：`500` 不在契約裡，前端要把進度條轉紅而不是靜靜卡住）。
    """
    state = _state(run_id)
    steps = select_steps(stages if stages is not None else state.get("requested_stages"))
    registry.set_status(run_id, "running")
    timings: dict[str, float] = {}
    stage = steps[0][0][0] if steps else "s01"
    try:
        for i, (ids, fn) in enumerate(steps):
            _ensure_live(run_id)
            stage = ids[0]
            t0 = time.perf_counter()
            for sid in ids:
                registry.set_stage(run_id, sid, "running")
            detail = await fn(run_id)
            spent = round(time.perf_counter() - t0, 2)
            for sid in ids:
                registry.set_stage(run_id, sid, "done")
            timings["+".join(ids)] = spent
            print(f"[drive_run] {run_id} {'+'.join(ids)} done {spent}s — {detail}")
            if "s03" in ids and (g := await _label_ceiling_stop(run_id)) is not None:
                # 墓碑寫在翻狀態**之前**（契約 §5.10：run 收工後事件一律拒收）
                bus.append_event(
                    run_id, stage="s03", type="stop", actor="console-owner",
                    data={"reason": "label_ceiling", "detail": g["detail"],
                          "best_run": None, "best_pt": None},
                    text=f"stop — 停在 s03（標註天花板不足）：{g['detail']}",
                )
                for rest, _fn in steps[i + 1:]:
                    for sid in rest:
                        registry.set_stage(run_id, sid, "skipped")
                registry.set_status(run_id, "done", abandoned=True,
                                    abandon_reason="label_ceiling")
                print(f"[drive_run] {run_id} abandoned(label_ceiling) — {g['detail']}")
                return timings
        registry.set_status(run_id, "done")
    except asyncio.CancelledError:
        raise  # cancel 端點已經寫好 stop 事件與 cancelled 狀態，這裡不重複寫
    except Exception as exc:  # noqa: BLE001 —— 任何失敗都要變成看得見的事件
        detail = f"{type(exc).__name__}: {_exc_text(exc)}"
        # 順序不能反：`bus.append_event()` 會拒絕往終局的 run 追加事件（契約 §8.5），
        # 先標 failed 再寫墓碑的話，前端永遠收不到這筆 stage.failed。
        bus.append_event(
            run_id,
            stage=stage,
            type="stage.failed",
            actor="console-owner",
            data={"stage": stage, "detail": detail},
            text=f"stage.failed {stage} — {detail}",
        )
        registry.set_stage(run_id, stage, "failed")
        registry.set_status(run_id, "failed")
    finally:
        _TASKS.pop(run_id, None)
        # curl 單步教學起的背景 seam 跟著收工：run 收工後它們寫的事件一律被 bus 擋掉
        # （契約 §8.5），繼續跑只是白燒 CPU 並讓 stage 停在一半。
        dataset.cancel_background(run_id)
    return timings


def _exc_text(exc: Exception) -> str:
    """HTTPException 的 detail 是契約 §1 的 `{code, detail}`，直接 str() 會印成一坨 dict。"""
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        return f"{exc.detail.get('code')} {exc.detail.get('detail')}"
    return str(exc)


# ---------- 那一顆按鈕的四支 + stage 表 ----------

@router.get("/stages")
def stages_table() -> dict[str, Any]:
    """11 格 stage 的名稱／擁有者，以及那顆按鈕實際的執行順序（契約 §8.10）。

    前端有一份一樣的表當 fallback，兩邊文案不一致就是「畫面在說謊」——
    `?selftest=1` 的最後一條就是打這支來比對（抓不到或對不上一律 FAIL）。
    """
    return {"stages": registry.stage_skeleton(), "pipeline": list(PIPELINE_IDS)}


@router.post("/runs", status_code=202)
async def create_run(body: CreateRun) -> JSONResponse:
    """開一個 run（前端那一顆按鈕）。契約 §8.1。`mode:"oneshot"` = 一路跑完 s01→s08。"""
    for name, value, table in (
        ("mode", body.mode, MODES),
        ("source", body.source, SOURCES),
        ("preset", body.preset, PRESETS),
    ):
        if value not in table:
            raise _err(400, "BAD_ENUM", f"{name} 只接受 {list(table)}，收到 {value!r}")
    if body.mode != M1_MODE:
        raise _err(501, "NOT_IMPLEMENTED", f"mode:{body.mode!r} 於 M3/M7 實作；M1 只有 oneshot")
    if body.source == "local":
        raise _err(501, "NOT_IMPLEMENTED", f"source:{body.source!r} 未排程（契約 §8.5）")
    if not 1 <= body.limit <= 500:
        raise _err(400, "BAD_RANGE", f"limit 必須在 1..500，收到 {body.limit}")
    if body.stages is not None and (bad := [x for x in body.stages if x not in PIPELINE_IDS]):
        raise _err(400, "BAD_ENUM",
                   f"stages 只接受 {list(PIPELINE_IDS)}（那顆按鈕跑得到的段），收到 {bad}")
    if (busy := registry.running_run()) is not None:
        # 錯誤 body 多帶一個選填的 `run_id`（契約 §10 加欄）：沒有它的話，前端在重整頁面之後
        # 既接不回那條 run、也按不到 Stop（`cancelRun()` 第一行就 `if (!RUN) return`）——
        # 畫面等於叫使用者去 cancel，同時把 cancel 拿走。
        raise HTTPException(
            status_code=409,
            detail={"code": "RUN_IN_FLIGHT", "run_id": busy,
                    "detail": f"{busy} 還在跑（GPU 佇列深度 1），先 cancel 或等它結束"},
        )

    # `labels` 的 enum 驗證與預設由 dataset-truth 那支 `_resolve_labels` 決定 —— 兩支端點必須是
    # 同一組函式，在這裡抄第二份 enum 就是「兩條入口規則慢慢漂開」的起點。
    # ⚠️ 代價是跨模組用了底線函式：`dataset._resolve_labels` / `_rf_ensure` 改名這裡會 ImportError
    # （會當場炸，不是靜默錯），`python -m src.app.routers.console` 的自檢就會抓到。
    # 那顆按鈕走 roboflow 時**預設 auto** 而不是沿用 §8.5 的 human：human 那條路目前會停在
    # s02 的 `409 CLASS_TABLE_SCHEMA_CONFLICT`（🔒 class_table schema 是照 KMeans 命名凍的，
    # nc∈[3,6] + 固定詞彙表，WM-811K 的 nc:1 names:['Donut'] 三條全踩，見契約 §10 那一列）。
    # 一顆會讓整條龍必定紅燈的按鈕不是功能，是陷阱；要人工框那條路就明寫 labels:"human"。
    labels = dataset._resolve_labels(
        body.source, body.labels or ("auto" if body.source == "roboflow" else None)
    )
    # 下載（或沿用）**在開 run 之前同步做完**：沒 key / key 錯 / 沒網路要在這裡回 4xx，
    # 而不是先回 202 開一個 run、再讓它在 s01 死掉 —— 那樣畫面上會多一條紅色的鬼 run，
    # 而真正的原因（去哪拿 key、填哪個檔）只有 stage.failed 那一行看得到。
    # 第一次約 8.8 MB / 實測 4.3 秒，之後沿用 index.json（0 秒、不需要 key）。
    rf = await dataset._rf_ensure(body.workspace, body.project, body.version) \
        if body.source == "roboflow" else None

    # 教學用的 `stages`：只跑前幾段。沒選到的格子一開始就標 skipped，不是 pending ——
    # 標 pending 的話畫面會有五格永遠亮著「待跑」，學生會等一個不會來的東西。
    active = [sid for ids, _ in select_steps(body.stages) for sid in ids] if body.stages else None
    state = registry.create_run(
        mode=body.mode, source=body.source, preset=body.preset, limit=body.limit, seed=body.seed,
        stages=active,
    )
    run_id = state["run_id"]
    extra: dict[str, Any] = {"labels": labels}
    if rf is not None:
        extra["roboflow"] = {k: rf[k] for k in ("workspace", "project", "version", "slug")}
    if body.stages is not None:
        extra["requested_stages"] = body.stages
    registry.set_status(run_id, state["status"], **extra)
    stages = registry.stage_skeleton(active)
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
        text=f"$ autocv run --mode {body.mode} --source {body.source} --limit {body.limit}"
             + (f" --dataset {rf['slug']} --labels {labels}" if rf else ""),
    )
    _TASKS[run_id] = asyncio.create_task(drive_run(run_id, body.stages))
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

    四件事做完才回：停掉 driver task、**收乾所有子行程**（訓練/探針走 `runner.cancel()`，
    LLM 命名與 noise-floor/推論那些走 `procs.kill_all()`）、寫一筆 `stop {reason:"user_cancel"}`、
    把 run 標 cancelled。
    ⚠️ 少了收乾那件，回應會是漂亮的 `cancelled` 而 GPU 還在燒（實測回了 200、run 標 cancelled，
    44 秒後 `ps` 看 `claude -p` 還活著，訓練 thread 還自己起了下一顆 seed）。
    ⚠️ 連按兩次 Stop 只准第一次拿到 200：狀態檢查與 `set_status` 之間隔著 8 秒的阻塞寬限期，
    沒有 `_CANCELLING` 這道旗標的話，第二個請求會在窗口內照樣通過檢查 → 兩筆 stop、
    兩筆 train.cancelled（契約 §6：同一個邏輯事實不准發兩個 seq）。autonomy 仍在 M7 才有。
    """
    state = registry.read_state(run_id)
    if state is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 不存在")
    if run_id in _CANCELLING or state["status"] not in registry.LIVE_STATUSES:
        raise _err(
            409, "RUN_NOT_CANCELLABLE",
            f"run {run_id} 已經是 {'cancelling' if run_id in _CANCELLING else state['status']}，"
            "沒有東西可以停",
        )

    _CANCELLING.add(run_id)
    try:
        running_stage = next((s for s, st in state["stages"].items() if st == "running"), "s01")
        task = _TASKS.pop(run_id, None)
        if task is not None:
            task.cancel()  # 先斷 driver，免得子行程一死它就往下跑下一段

        # 訓練子行程不在這個 process 的控制流裡，task.cancel() 碰不到它 —— 只有訊號打得到。
        # `runner.cancel()` 會 SIGTERM 整個 process group → 等它收乾（逾時 SIGKILL）→ 推
        # `train.cancelled`，**確認死了才回**。8 秒的寬限期是阻塞的，所以丟去 thread。
        killed: list[str] = []
        for tid, rec in ((registry.read_state(run_id) or {}).get("train") or {}).items():
            if rec.get("status") in runner.LIVE and runner.alive(tid, rec.get("pid")):
                await asyncio.to_thread(runner.cancel, tid)
                killed.append(tid)
        # 其餘子行程（`claude -p` 命名、noise floor 的訓練與推論、eval 推論）都是在
        # `asyncio.to_thread` 裡用 `procs.run` 起的。`to_thread` 不可取消，殺子行程是唯一
        # 停得了那條 thread 的辦法。
        children = await asyncio.to_thread(procs.kill_all)
        dataset.cancel_background(run_id)  # curl 單步教學起的背景 seam（driver 之外那條路）

        # `runner.cancel()` 已經把它那一格標掉了；沒被它動到的才由這裡標，
        # 否則會把早就 done 的 s01 標成 failed（fallback 值）。
        if (registry.read_state(run_id) or state)["stages"].get(running_stage) == "running":
            registry.set_stage(run_id, running_stage, "failed")
        # stop 要寫在翻狀態**之前**：翻完之後 `bus.append_event()` 就會拒收（契約 §8.5）。
        event = bus.append_event(
            run_id,
            stage=running_stage,
            type="stop",
            actor="console-owner",  # 契約 §8.4 的明文例外（§4 的擁有者是 arbiter）
            data={"reason": "user_cancel", "best_run": None, "best_pt": None},
            text=f"stop — 使用者按下 Stop（收乾 {len(killed) + len(children)} 顆子行程）",
        )
        registry.set_status(run_id, "cancelled")
    finally:
        _CANCELLING.discard(run_id)
    return {"run_id": run_id, "status": "cancelled",
            "stop_seq": event["seq"] if event else bus.last_seq(run_id),
            "trains_killed": killed, "children_killed": children}


# ---------- M6 佔位 ----------

@router.get("/runs/{run_id}/provenance")
def provenance(run_id: str):
    """plan vs actual 逐欄比對（DESIGN 護欄 7；回應形狀見契約 §9，M6 由 console-owner 實作）。"""
    raise _err(501, "NOT_IMPLEMENTED", "此端點於 M6 實作（擁有者 console-owner）")


# ---------- 自檢：`uv run python -m src.app.routers.console`（不需要 torch / 不需要 GPU） ----------
# 驗這一支最容易靜默壞掉的兩件事：
#   1. PIPELINE 的 id 與 registry.STAGE_TABLE 對得上（rail 畫得出來、前端那張表才有東西可比）。
#   2. `_await_child()` 等的是「子行程退出」而不是「事件出現」—— 這條就是 M4 驗收踩到兩次的競態：
#      `train.done` 寫進事件流時子行程還活著約 0.3 秒，照事件往下走就會 409 RUN_IN_FLIGHT。

_FAKE_TRAIN = """
import sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from src.app import bus
bus.RUNS_DIR = Path({runs!r})
bus.append_event({run!r}, "s07", "train.done", "training-engineer",
                 {{"best_pt": "fake/best.pt", "elapsed_s": 0.1}}, text="train.done（假的）")
time.sleep(1.2)          # 事件先出、人還沒走：真正的競態長這樣
"""


async def _gather_cancel(run_id: str) -> list[Any]:  # pragma: no cover - 自檢用
    """同時送兩個 cancel（模擬手殘連按兩下）。"""
    return list(await asyncio.gather(cancel_run(run_id), cancel_run(run_id),
                                     return_exceptions=True))


def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import tempfile
    from pathlib import Path

    ids = [s["id"] for s in registry.STAGE_TABLE]
    assert set(PIPELINE_IDS) == set(registry.PIPELINE_STAGES), (PIPELINE_IDS, registry.PIPELINE_STAGES)
    assert len(set(PIPELINE_IDS)) == len(PIPELINE_IDS), "同一個 stage 被排了兩次"
    assert all(sid in ids for sid in PIPELINE_IDS), "PIPELINE 有 STAGE_TABLE 裡沒有的 stage"
    assert [ids for ids, _ in select_steps(["s01", "s03"])] == [("s01",), ("s03",)]
    assert select_steps(["s07"]) == [(("s06", "s07"), _stage_train)]  # 選 s07 = 連 s06 一起
    assert len(select_steps(None)) == len(PIPELINE)

    tmp = Path(tempfile.mkdtemp(prefix="console-selfcheck-"))
    bus.RUNS_DIR = tmp / "runs"
    registry.RUNS_DIR = bus.RUNS_DIR
    registry.RUNS_JSONL = bus.RUNS_DIR / "runs.jsonl"
    registry.DS_DIR = tmp / "ds"
    runner.EXPERIMENTS_DIR = tmp / "04-experiments"
    fake = tmp / "fake_train.py"

    state = registry.create_run(mode="oneshot", source="demo", preset="real", limit=1, seed=42)
    run_id = state["run_id"]
    registry.set_status(run_id, "running")
    fake.write_text(_FAKE_TRAIN.format(root=str(bus.PROJECT_ROOT), run=run_id,
                                       runs=str(bus.RUNS_DIR)), "utf-8")
    runner.TRAIN_SCRIPT = fake

    train_id = runner.next_train_id(run_id)
    rec = runner.start(run_id=run_id, train_id=train_id,
                       recipe={"kind": "train", "params": {"model": "fake", "epochs": 1}}, eta_min=0.1)

    async def _wait() -> float:
        # 事件先到（子行程一起跑就寫了），但 _await_child 不准就這樣回來
        for _ in range(100):
            if any(e["type"] == "train.done" for e in bus.read_events(run_id)):
                break
            await asyncio.sleep(0.05)
        assert runner.alive(train_id, rec["pid"]), "子行程太快就死了，這條驗不到競態"
        t0 = time.perf_counter()
        await _await_child(run_id, train_id)
        return time.perf_counter() - t0

    spent = asyncio.run(_wait())
    assert spent > 0.3, f"_await_child 只等了 {spent:.2f}s —— 它在等事件，不是等子行程退出"
    assert not runner.pid_alive(rec["pid"]), "子行程還活著就回來了"
    assert train_id not in runner._PROCS, "Popen handle 沒收掉 —— 沒人 waitpid 就是 zombie"
    assert runner.read_train(train_id)["status"] == "done", runner.read_train(train_id)
    assert registry.read_state(run_id)["stages"]["s07"] == "done"

    # 3. 子行程死掉時，錯誤訊息要帶上 train.log 的尾巴 —— 沒有這幾行，畫面上那句
    #    「結束時狀態是 crashed」沒有人猜得到真因是 server 沒帶 `--extra train` 起。
    log = tmp / "fake.log"
    log.write_text("Traceback (most recent call last):\n"
                   "ModuleNotFoundError: No module named 'ultralytics'\n", "utf-8")
    assert "ultralytics" in _log_tail({"log_path": str(log)}), _log_tail({"log_path": str(log)})
    assert _log_tail({}) == "" and _log_tail({"log_path": str(tmp / "沒這個檔")}) == ""

    # 4. 連按兩次 Stop：只准第一次 200、第二次 409，事件流只准長出一筆 stop 與一筆
    #    train.cancelled（契約 §8.4 / §6）。殺子行程的 8 秒寬限期是阻塞的，第二個請求
    #    就是在那個窗口裡溜進來的 —— 沒有 `_CANCELLING` 這道旗標就會兩個都回 200。
    slow = tmp / "slow_train.py"
    slow.write_text("import time\ntime.sleep(30)\n", "utf-8")
    runner.TRAIN_SCRIPT = slow
    registry.set_status(run_id, "running")
    tid2 = runner.next_train_id(run_id)
    rec2 = runner.start(run_id=run_id, train_id=tid2,
                        recipe={"kind": "train", "params": {"model": "fake", "epochs": 1}},
                        eta_min=0.1)

    outs = asyncio.run(_gather_cancel(run_id))
    oks = [o for o in outs if isinstance(o, dict)]
    errs = [o for o in outs if isinstance(o, HTTPException)]
    assert len(oks) == 1 and len(errs) == 1, outs
    assert errs[0].status_code == 409 and errs[0].detail["code"] == "RUN_NOT_CANCELLABLE", errs[0].detail
    events = bus.read_events(run_id)
    assert sum(1 for e in events if e["type"] == "stop") == 1, "第二次 Stop 也寫了一筆 stop"
    assert sum(1 for e in events if e["type"] == "train.cancelled") == 1
    assert not runner.pid_alive(rec2["pid"]), "回了 cancelled 但子行程還活著（假成功）"
    # 終局之後的遲到事件一律不准進事件流（契約 §8.5）
    last = bus.last_seq(run_id)
    assert bus.append_event(run_id, "s08", "eval.leakage", "metric-auditor", {}) is None
    assert bus.last_seq(run_id) == last

    print(f"console.drive_run selfcheck PASS（等了 {spent:.2f} 秒才放行；"
          f"連按兩次 Stop = 1×200 + 1×409；暫存目錄 {tmp}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
