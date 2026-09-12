"""training-engineer 的 router —— 選型、配方、不阻塞的訓練。

契約：`_Context/api-contract.md` §9（端點清單與擁有者）、§4（事件欄位）、§8.9（GO 閘門豁免）、
§10（錯誤碼）。設計：`_Context/DESIGN.md` s05 / s06 / s07 與護欄 5、6。
擁有者：**training-engineer**（M3 起）。

三條寫死的紀律（DESIGN 已定案，不要重新設計）：
  1. `model.train()` 是阻塞碼，**不准塞 BackgroundTasks** —— `subprocess.Popen` 跑
     `scripts/train_yolo.py`，pid 與狀態寫 `runs/<run_id>/state.json`（`src/train/runner.py`）。
  2. per-epoch 指標由子行程內的 `on_fit_epoch_end` callback 直接 `bus.append_event()`
     （**不 parse stdout**，實測 log 帶 ANSI 與 `\\r` 進度條）。
  3. cancel = `SIGTERM(pid)`，**收到 `train.cancelled` 才算停成功**。

本檔與它 import 的 `src/train/{select,runner}.py` 在 API process 內跑，
所以**一行 torch / ultralytics 都不准出現**（base 的 `uv sync` 沒有那兩個）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...train import runner, select
from .. import bus, registry

router = APIRouter(tags=["train"])

ACTOR = "training-engineer"
GATES = ("go", "auto")
PROJECT_ROOT = bus.PROJECT_ROOT
EXPERIMENTS_DIR = PROJECT_ROOT / "04-experiments"


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


def _live_run(run_id: str) -> dict[str, Any]:
    state = registry.read_state(run_id)
    if state is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 不存在")
    if state["status"] not in registry.LIVE_STATUSES:
        raise _err(409, "RUN_NOT_LIVE",
                   f"run {run_id} 狀態是 {state['status']}，事件只能追加在活的 run 上")
    return state


def _busy_train(state: dict[str, Any]) -> str | None:
    """同時只准一個子行程在燒 GPU（佇列深度 1）。

    順手把「已經跑完但還沒有人去讀它狀態」的那幾筆結清（`snapshot()` 會把 stage 標成 done、
    把 GPU 分鐘記進 budget）—— 否則探針跑完之後 s05 會一直卡在 running，
    除非剛好有人打 `GET /train/{id}/state`。
    """
    busy = None
    for tid, rec in list((state.get("train") or {}).items()):
        if rec.get("status") not in runner.LIVE:
            continue
        if runner.alive(tid, rec.get("pid")):
            busy = busy or tid
        else:
            runner.snapshot(tid)  # 結清：status / stage / budget
    return busy


def _require_dataset(run_id: str) -> tuple[Path, list[dict[str, Any]]]:
    """`s04` 的 selfcheck 有 FAIL 就不准訓練（契約 §10 的 `409 SELFCHECK_FAILED`）。

    前端訓練鈕本來就該 disabled，這是後端那一道鎖 —— 兩道都要有，因為 curl 打得到。
    """
    data_yaml = select.DATA_YAML
    checks = next(
        (ev["data"].get("checks", []) for ev in reversed(bus.read_events(run_id))
         if ev["type"] == "ds.selfcheck"), None
    )
    if checks is None or not data_yaml.exists():
        # ⚠️ 借用的錯誤碼：語意上這裡是「dataset 還沒 freeze」而不是「selfcheck 沒過」。
        # dataset-truth 在 M2 也踩到同一個洞，已提請 console-owner 走 §12 加 409 DS_NOT_READY。
        raise _err(409, "SELFCHECK_FAILED",
                   f"run {run_id} 還沒跑完 s04（沒有 ds.selfcheck 事件或 {data_yaml.name} 不存在），"
                   "先 POST /datasets/{ds}/freeze")
    failed = [c for c in checks if not c.get("pass")]
    if failed:
        raise _err(409, "SELFCHECK_FAILED",
                   f"s04 selfcheck 有 {len(failed)} 條 FAIL（{', '.join(c['name'] for c in failed)}），"
                   "資料集沒過閘門就訓練，訓出來的數字不可比")
    return data_yaml, checks


def _last_recipe(exclude: str) -> dict[str, Any] | None:
    """上一份凍結過的正式訓練 recipe（M6 的 `diff_vs_last` 拿它比鍵名，不比自然語言）。"""
    files = sorted(EXPERIMENTS_DIR.glob("*/recipe.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        if f.parent.name == exclude:
            continue
        rec = json.loads(f.read_text("utf-8"))
        if rec.get("kind") == "train":
            return rec
    return None


def _selected_model() -> str | None:
    """探針選出來的那個。沒探針過就回 None（呼叫方退回 baseline）。"""
    files = sorted(EXPERIMENTS_DIR.glob("*/probe.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            return json.loads(f.read_text("utf-8"))["selected"]["name"]
        except (KeyError, json.JSONDecodeError):
            continue
    return None


def _split_counts() -> tuple[int, int]:
    root = select.DATA_YAML.parent
    return (len(list((root / "images" / "train").glob("*.png"))),
            len(list((root / "images" / "valid").glob("*.png"))))


# ---------- body ----------

class ProbeBody(BaseModel):
    run_id: str
    ds_version: str | None = None
    label_version: str | None = None
    names: list[str] | None = None
    seed: int = 42


class TrainBody(BaseModel):
    run_id: str
    ds_version: str | None = None
    label_version: str | None = None
    model: str | None = None
    recipe: dict[str, Any] | None = None
    gate: str = "auto"


# ---------- s05：候選看板與短探針 ----------

@router.get("/models/candidates")
def candidates(epochs: int = select.BASELINE["epochs"]) -> dict[str, Any]:
    """候選池（契約 §9 的欄位）。純讀，不推事件 —— 沒有 run_id 的東西不該長在誰的事件流上。

    `excluded` 一起回：rtdetr 不在池裡是**刻意的決定**，要印在畫面上，不是默默消失。
    """
    if not 1 <= epochs <= 1000:
        raise _err(400, "BAD_RANGE", f"epochs 必須在 1..1000，收到 {epochs}")
    cal = select.read_calibration()
    return {
        "candidates": select.candidates(epochs),
        "excluded": select.EXCLUDED,
        "scale_factor": cal["scale_factor"],
        "calibrated": cal["calibrated"],
        "eta_basis": cal["basis"],
        "baseline": select.BASELINE,
        "score_formula": "score = mAP50-95@probe − 0.10·正規化分鐘 − 0.05·正規化 ms/img；"
                         "差距落在 CI 內選小的",
    }


@router.post("/models/probe", status_code=202)
def probe(body: ProbeBody) -> JSONResponse:
    """短探針（s05）：同 seed / 同 dataset / 同 recipe，跑到 mAP50-95 曲線第一次持平。

    子行程跑，**立刻回 202**（契約 §1 裁決 A：會產生事件的 POST 不回串流）。
    順便產出 ETA 的 `scale_factor`（護欄 5），所以就算只有一個候選也該跑一次。
    """
    state = _live_run(body.run_id)
    _require_dataset(body.run_id)
    if (busy := _busy_train(state)) is not None:
        raise _err(409, "RUN_IN_FLIGHT", f"{busy} 還在跑（GPU 佇列深度 1），先 cancel 或等它結束")

    pool = [c["name"] for c in select.CANDIDATES]
    names = body.names or pool
    if bad := [n for n in names if n not in pool]:
        raise _err(400, "BAD_ENUM",
                   f"候選只有 {pool}，收到 {bad}（rtdetr 刻意不進池：MPS 上會靜默 fallback 到 CPU）")

    probe_id = runner.next_train_id(body.run_id)
    rows = select.candidates(select.BASELINE["epochs"])
    bus.append_event(
        body.run_id, "s05", "model.candidates", ACTOR,
        {"list": [r for r in rows if r["name"] in names], "excluded": select.EXCLUDED},
        text=f"候選 {', '.join(names)}（rtdetr 不進池：MPS 上 deformable attention 會靜默掉回 CPU）",
    )

    recipe = select.build_recipe(model=names[0], run_id=body.run_id, train_id=probe_id,
                                overrides={"seed": body.seed})
    recipe.update({"kind": "probe", "names": names,
                   "project": str(EXPERIMENTS_DIR / probe_id), "recipe_id": probe_id})
    registry.set_stage(body.run_id, "s05", "running")
    eta_min = round(sum(select.est_min(n, 4) for n in names), 2)
    rec = runner.start(run_id=body.run_id, train_id=probe_id, recipe=recipe, eta_min=eta_min)
    return JSONResponse(status_code=202, content={
        "probe_id": probe_id, "train_id": probe_id, "run_id": body.run_id,
        "pid": rec["pid"], "names": names, "eta_min": eta_min,
        "events_url": f"/api/v1/runs/{body.run_id}/events?since=0",
    })


# ---------- s06 + s07：凍結配方並起訓練 ----------

@router.post("/train", status_code=202)
def train(body: TrainBody) -> JSONResponse:
    """凍結 recipe（s06）→ `Popen` 起訓練（s07）→ 立刻回 `{train_id, pid, eta_min}`。

    GO 閘門（契約 §8.9）：`mode:"manual"` 的 run 保留人工閘門 —— `gate:"auto"` 只會把 s07 標成
    `awaiting_go` 並回 `status:"awaiting_go"`，要再打一次 `gate:"go"` 才真的起跑。
    `oneshot` / `autonomous` **明文豁免**（改用三軸預算 + 前端常駐 Stop 鈕）。
    """
    if body.gate not in GATES:
        raise _err(400, "BAD_ENUM", f"gate 只接受 {list(GATES)}，收到 {body.gate!r}")
    state = _live_run(body.run_id)
    data_yaml, _ = _require_dataset(body.run_id)
    if (busy := _busy_train(state)) is not None:
        raise _err(409, "RUN_IN_FLIGHT", f"{busy} 還在跑（GPU 佇列深度 1），先 cancel 或等它結束")

    model = body.model or _selected_model() or select.BASELINE["model"]
    pool = [c["name"] for c in select.CANDIDATES]
    if model not in pool:
        raise _err(400, "BAD_ENUM", f"model 只接受 {pool}，收到 {model!r}")

    if state["mode"] == "manual" and body.gate != "go":
        registry.set_stage(body.run_id, "s07", "awaiting_go")
        return JSONResponse(status_code=202, content={
            "train_id": None, "run_id": body.run_id, "status": "awaiting_go", "pid": None,
            "eta_min": select.est_min(model, select.BASELINE["epochs"]),
            "detail": "manual 模式保留人工 GO 閘門（契約 §8.9），"
                      "再打一次 POST /train {gate:\"go\"} 放行",
        })

    train_id = runner.next_train_id(body.run_id)
    recipe = select.build_recipe(model=model, run_id=body.run_id, train_id=train_id,
                                overrides=body.recipe or {}, data_yaml=data_yaml)
    eta_min = select.est_min(model, recipe["params"]["epochs"])
    n_train, n_valid = _split_counts()

    # s06：提案 → 每個 augment 開關一行理由 → 凍結
    bus.append_event(
        body.run_id, "s06", "recipe.proposed", ACTOR,
        {"params": recipe["params"], "augment": recipe["augment"],
         "rationale": recipe["rationale"], "eta_min": eta_min},
        text=f"$ autocv train --model {model} --epochs {recipe['params']['epochs']} "
             f"--imgsz {recipe['params']['imgsz']} --device {recipe['params']['device']}",
    )
    for d in select.AUGMENT_DECISIONS:
        bus.append_event(body.run_id, "s06", "augment.decision", ACTOR,
                         {"name": d["name"], "on": d["on"], "why": d["why"]}, text=d["why"])
    diff = select.recipe_diff(recipe, _last_recipe(train_id))
    bus.append_event(
        body.run_id, "s06", "recipe.frozen", ACTOR,
        {"recipe_id": recipe["recipe_id"], "diff_vs_last": diff},
        text=(f"dataset: {n_train} train / {n_valid} valid  ·  recipe {recipe['recipe_id']} 凍結"
              + (f"（本輪只改：{', '.join(f'{k} {v[0]} → {v[1]}' for k, v in diff.items())}）"
                 if diff else "（第一輪，無 diff）")),
    )
    registry.set_stage(body.run_id, "s06", "done")
    registry.set_stage(body.run_id, "s07", "running")

    rec = runner.start(run_id=body.run_id, train_id=train_id, recipe=recipe, eta_min=eta_min)
    bus.append_event(
        body.run_id, "s07", "train.start", ACTOR,
        {"run_id": body.run_id, "pid": rec["pid"], "eta_min": eta_min},
        text=f"train.start pid {rec['pid']}  ETA {eta_min} 分鐘"
             f"（{'已' if select.read_calibration()['calibrated'] else '未'}依本機 2-epoch 校準）",
    )
    return JSONResponse(status_code=202, content={
        "train_id": train_id, "run_id": body.run_id, "status": "running", "pid": rec["pid"],
        "eta_min": eta_min, "model": model, "recipe_id": recipe["recipe_id"],
        "diff_vs_last": diff, "events_url": f"/api/v1/runs/{body.run_id}/events?since=0",
    })


@router.post("/train/{train_id}/cancel")
def cancel(train_id: str) -> dict[str, Any]:
    """SIGTERM 子行程 → 確認收乾 → 推 `train.cancelled`。**確認死了才回**，不回假的成功。"""
    rec = runner.read_train(train_id)
    if rec is None:
        raise _err(404, "TRAIN_NOT_FOUND", f"train {train_id} 不存在")
    if rec.get("status") not in runner.LIVE:
        raise _err(409, "RUN_NOT_CANCELLABLE",
                   f"train {train_id} 已經是 {rec.get('status')}，沒有東西可以停")
    return runner.cancel(train_id)


@router.get("/train/{train_id}/state")
def state(train_id: str) -> dict[str, Any]:
    """不依賴 SSE 的狀態快照。API 重啟後靠這支 + `os.kill(pid,0)` 認領或標 crashed。"""
    snap = runner.snapshot(train_id)
    if snap is None:
        raise _err(404, "TRAIN_NOT_FOUND", f"train {train_id} 不存在")
    return snap


# ---------- M6 佔位 ----------

@router.post("/sweep")
def sweep():
    """2-factor 小 sweep（trials ≤ 4），補 OFAT 看不到交互作用的盲點。需 arbiter 批 budget。"""
    raise _err(501, "NOT_IMPLEMENTED", "此端點於 M6 實作（擁有者 training-engineer）")
