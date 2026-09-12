"""run registry —— `runs/runs.jsonl`（append-only 帳本）+ `runs/<run_id>/state.json`（可變狀態）。

契約：`_Context/api-contract.md` §8.1 / §8.3（快照形狀）、§3（狀態 enum）、§11（檔案佈局）。
擁有者：console-owner（共同地基）。
權限鎖（team-roles §2.3）：**判分的手不寫紀錄** —— metric-auditor 只讀 runs.jsonl，
任何寫入一律經本檔，這樣「讓結論通過」的最短路徑就不會是改 baseline。
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import bus, procs

RUNS_DIR = bus.RUNS_DIR
RUNS_JSONL = RUNS_DIR / "runs.jsonl"
# ds 的落檔目錄（佈局由 dataset-truth 決定，但 id 發號在這裡，所以路徑的唯一真相放這）。
DS_DIR = bus.PROJECT_ROOT / "01-raw-data" / "datasets"

# 契約 §8.1 的 11 格 stage 骨架（M0 就定死，前端只寫一次版面）。
STAGE_TABLE: list[dict[str, str]] = [
    {"id": "s01", "name": "資料一張一張進來", "owner": "dataset-truth"},
    {"id": "s02", "name": "AI 自己做 labeling", "owner": "dataset-truth"},
    {"id": "s03", "name": "生成 bbox", "owner": "dataset-truth"},
    {"id": "s04", "name": "落檔成 dataset", "owner": "dataset-truth"},
    {"id": "s05", "name": "抓模型並自己決定最好的", "owner": "training-engineer"},
    {"id": "s06", "name": "配方凍結與單一變因宣告", "owner": "training-engineer"},
    {"id": "s07", "name": "訓練", "owner": "training-engineer"},
    {"id": "s08", "name": "Evaluation + 圖表", "owner": "metric-auditor"},
    {"id": "s09", "name": "自然語言討論", "owner": "experiment-arbiter"},
    {"id": "s10", "name": "再討論一輪 → 重 train", "owner": "experiment-arbiter"},
    {"id": "s11", "name": "自我進化模式", "owner": "experiment-arbiter"},
]
# 那顆按鈕（`console.drive_run()`）會走完的格子：s01→s08。s09–s11 仍是 skipped（M6/M7）。
# 執行順序不是這裡的順序（s03 在 s02 之前跑），順序的唯一真相是 `console.PIPELINE`。
PIPELINE_STAGES = ("s01", "s02", "s03", "s04", "s05", "s06", "s07", "s08")

STAGE_STATUSES = frozenset(
    {"pending", "running", "done", "failed", "skipped", "reused", "awaiting_go"}
)
RUN_STATUSES = frozenset({"queued", "running", "done", "failed", "cancelled", "crashed"})
LIVE_STATUSES = frozenset({"queued", "running"})
ORPHAN_GRACE_SEC = 5.0  # 重啟時收孤兒子行程的 SIGTERM 寬限期，逾時 SIGKILL

# DESIGN「自我進化模式」三軸硬預算。M1 用不到（沒有 GPU、沒有 LLM），但欄位要在，
# 前端的 budget 條 M1 就畫得出來（顯示 0/12）。
PRESET_BUDGETS: dict[str, dict[str, float]] = {
    "teaching": {"gpu_min_cap": 12, "llm_calls_cap": 24, "usd_cap": 0.5, "max_rounds": 3},
    "real": {"gpu_min_cap": 60, "llm_calls_cap": 60, "usd_cap": 3.0, "max_rounds": 6},
}


# ---------- 檔案層 ----------

def state_path(run_id: str) -> Path:
    return bus.run_dir(run_id) / "state.json"


def read_state(run_id: str) -> dict[str, Any] | None:
    path = state_path(run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text("utf-8"))


def write_state(state: dict[str, Any]) -> dict[str, Any]:
    """原子寫（tmp + os.replace）—— 讀的人不會看到半份 JSON。"""
    state["updated_at"] = bus.utc_now_iso()
    path = state_path(state["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)
    return state


def append_ledger(record: dict[str, Any]) -> None:
    """append 一行到 runs.jsonl（單 writer + flock）。同一個 run 允許多行，讀的人取最後一筆。"""
    RUNS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS_JSONL, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_runs() -> dict[str, dict[str, Any]]:
    """runs.jsonl 摺疊成 {run_id: 最後一筆}。append-only 帳本 + 最後一筆勝，不原地改行。"""
    out: dict[str, dict[str, Any]] = {}
    if not RUNS_JSONL.exists():
        return out
    for line in RUNS_JSONL.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            rec = json.loads(line)
            out[rec["run_id"]] = rec
    return out


def _next_id(prefix: str, existing: list[str]) -> str:
    nums = [int(x[len(prefix) :]) for x in existing if x.startswith(prefix) and x[len(prefix) :].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1}"


def next_run_id() -> str:
    known = list(load_runs()) + [p.name for p in RUNS_DIR.glob("r*") if p.is_dir()]
    return _next_id("r", known)


def next_ds_id() -> str:
    """比照 `next_run_id()`，帳本與落檔目錄都要掃。

    ds 的資料落在 `01-raw-data/datasets/<ds_id>/`，但常見的重置指令
    `rm -rf 01-raw-data/demo runs` 不會刪它 —— 只看 runs.jsonl 就會把 ds1 再發一次，
    而 `ds1/labels.json` 還是上一輪的內容：新 run 的 s01 只覆寫 manifest.json，
    `GET /datasets/ds1/images` 在 s03 落盤前回的 `annotations.count` 會是上一個 run 的數字。
    """
    known = [rec.get("ds_id") or "" for rec in load_runs().values()]
    known += [p.name for p in DS_DIR.glob("ds*") if p.is_dir()]
    return _next_id("ds", known)


# ---------- run 生命週期 ----------

def running_run() -> str | None:
    """同時只准一個 run 在跑（GPU 佇列深度 1，契約 §8.1 的 409 RUN_IN_FLIGHT）。"""
    for run_id, rec in load_runs().items():
        state = read_state(run_id) or rec
        if state.get("status") in LIVE_STATUSES:
            return run_id
    return None


def create_run(
    *, mode: str, source: str, preset: str, limit: int, seed: int, pid: int | None = None,
    stages: Sequence[str] | None = None,
) -> dict[str, Any]:
    run_id = next_run_id()
    ds_id = next_ds_id()
    budgets = PRESET_BUDGETS[preset]
    state: dict[str, Any] = {
        "run_id": run_id,
        "status": "queued",
        "mode": mode,
        "preset": preset,
        "source": source,
        "ds_id": ds_id,
        "created_at": bus.utc_now_iso(),
        "round": 0,
        "limit": limit,
        "seed": seed,
        "pid": pid if pid is not None else os.getpid(),
        "stages": _stage_states(stages),
        "counts": {"total": 0, "loaded": 0, "labeled": 0},
        "budget": {
            "gpu_min_used": 0,
            "gpu_min_cap": budgets["gpu_min_cap"],
            "llm_calls": 0,
            "llm_calls_cap": budgets["llm_calls_cap"],
            "cost_usd": 0,
            "usd_cap": budgets["usd_cap"],
        },
        "best_run": None,
        "artifacts": (
            [
                {"kind": "demo_images", "path": "01-raw-data/demo/"},
                {"kind": "gt", "path": "01-raw-data/demo/gt.json"},
            ]
            if source == "demo"
            else []
        ),
        "autonomy": None,
        "provenance_ok": None,
        "ds_version": "v1",
        "label_version": "v1",
        "class_table_version": None,  # M1 一律 null（class 真相表 M2 才凍）
    }
    write_state(state)
    _sync_ledger(state)
    return state


def _sync_ledger(state: dict[str, Any]) -> None:
    """把 state 投影成 runs.jsonl 的一筆紀錄（沿用 DESIGN 的 experiments schema，不另發明欄位）。"""
    append_ledger(
        {
            "run_id": state["run_id"],
            "parent_run": state.get("parent_run"),
            "status": state["status"],
            "mode": state["mode"],
            "preset": state["preset"],
            "source": state["source"],
            "ds_id": state["ds_id"],
            "created_at": state["created_at"],
            "updated_at": state.get("updated_at"),
            "patch": state.get("patch"),
            "params": {"limit": state["limit"], "seed": state["seed"]},
            "ds_version": state["ds_version"],
            "label_version": state["label_version"],
            "class_table_version": state["class_table_version"],
            "val_map5095": state.get("val_map5095"),
            "ci": state.get("ci"),
            "verdict": state.get("verdict"),
            "anchor_ok": state.get("anchor_ok"),
            "cost_min": state.get("cost_min"),
            "cost_usd": state["budget"]["cost_usd"],
            "llm_calls": state["budget"]["llm_calls"],
            "provenance_ok": state.get("provenance_ok"),
            "arbiter_reason": state.get("arbiter_reason"),
        }
    )


def set_status(run_id: str, status: str, **extra: Any) -> dict[str, Any]:
    if status not in RUN_STATUSES:
        raise ValueError(f"run 狀態不在契約 §3 的 enum 內：{status!r}")
    state = read_state(run_id)
    if state is None:
        raise KeyError(run_id)
    state["status"] = status
    state.update(extra)
    write_state(state)
    _sync_ledger(state)  # 終局狀態要進帳本，metric-auditor / arbiter 只讀這裡
    return state


def set_stage(run_id: str, stage: str, status: str) -> dict[str, Any]:
    if status not in STAGE_STATUSES:
        raise ValueError(f"stage 狀態不在契約 §3 的 enum 內：{status!r}")
    state = read_state(run_id)
    if state is None:
        raise KeyError(run_id)
    state["stages"][stage] = status
    return write_state(state)


def set_counts(run_id: str, **counts: int) -> dict[str, Any]:
    state = read_state(run_id)
    if state is None:
        raise KeyError(run_id)
    state["counts"].update(counts)
    return write_state(state)


def _stage_states(stages: Sequence[str] | None = None) -> dict[str, str]:
    """這一輪要跑的格子 = `pending`，其餘 `skipped`（前端畫灰的，不假裝在跑）。"""
    active = frozenset(stages if stages is not None else PIPELINE_STAGES)
    return {s["id"]: ("pending" if s["id"] in active else "skipped") for s in STAGE_TABLE}


def stage_skeleton(stages: Sequence[str] | None = None) -> list[dict[str, str]]:
    """`POST /api/v1/runs` 回應裡的 11 格骨架（契約 §8.1）。"""
    states = _stage_states(stages)
    return [{**s, "status": states[s["id"]]} for s in STAGE_TABLE]


def split_counts(state: dict[str, Any]) -> dict[str, int] | None:
    """s04 落檔後的 split 真相（`01-raw-data/datasets/<ds>/splits.json`）。s04 之前回 `None`。

    ⛔ 這一欄的存在理由是實測抓到的謊：側欄那三個數字本來是前端自己數 `IMAGES[].split`，
    而那個欄位在事件裡**永遠是 null**（`ds.image` 的 `split` 由 s01 寫死 null，s04 指派時
    不會再補發一次 120 筆）。結果是一條龍訓練完、verdict TRUSTED，右欄照樣寫
    「TRAIN 0 / VALID 0 / TEST 0 ＋ 還有 120 張沒指派，訓練前要清乾淨」。
    分層是**後端**決定的，那就由後端回答，前端不自算（契約 §8.3 的選填加欄）。
    """
    path = DS_DIR / state["ds_id"] / "splits.json"
    try:
        splits: dict[str, str] = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None  # s04 還沒跑（或檔壞了）→ null，不是一組 0（0 會被讀成「都沒分到」）
    out: dict[str, int] = {"train": 0, "valid": 0, "anchor": 0, "sealed_test": 0}
    for value in splits.values():
        out[value] = out.get(value, 0) + 1
    total = state.get("counts", {}).get("total") or len(splits)
    out["unassigned"] = max(0, total - len(splits))
    return out


def frozen_recipe(state: dict[str, Any]) -> dict[str, Any] | None:
    """這一輪**真的跑的**配方（s05 探針選的模型 + s06 凍結的 epochs/imgsz）。s07 之前 `None`。

    畫面上那組 `yolov8n / 50 epoch / imgsz 640` 是 M1 寫死的預設值，跟真的跑的那一組
    （實測 `yolov8n / 10 epoch / imgsz 320`）不一樣 —— 講完「它自己挑了模型」再讓學生讀到
    一組沒人用過的數字，就是最容易被當場抓包的那種謊。真相在 `runner.start` 落的 recipe.json。
    """
    trains = [r for r in (state.get("train") or {}).values() if r.get("kind") == "train"]
    if not trains:
        return None
    rec = trains[-1]  # 同一個 run 跑第二次訓練時，最後一筆才是這一輪的配方
    params: dict[str, Any] = {}
    try:
        params = json.loads(
            (bus.PROJECT_ROOT / rec["recipe_path"]).read_text("utf-8")
        ).get("params") or {}
    except (OSError, KeyError, json.JSONDecodeError):
        pass  # recipe.json 不見了也不准讓快照 500：退回 train 紀錄上那兩欄
    return {
        "train_id": rec.get("train_id"),
        "status": rec.get("status"),
        "model": params.get("model") or rec.get("model"),
        "epochs": params.get("epochs") or rec.get("epochs"),
        "imgsz": params.get("imgsz"),
        "batch": params.get("batch"),
        "device": params.get("device"),
        "elapsed_s": rec.get("elapsed_s"),
    }


def snapshot(run_id: str) -> dict[str, Any] | None:
    """`GET /api/v1/runs/{run_id}` 的回應（契約 §8.3，逐欄照抄，內部欄位 pid/limit/seed 不外流）。"""
    state = read_state(run_id)
    if state is None:
        return None
    return {
        # 契約 §8.3 的兩個選填加欄（2026-09-12）：側欄的 split 統計與訓練設定一律由快照驅動。
        "split": split_counts(state),
        "recipe": frozen_recipe(state),
        "run_id": state["run_id"],
        "status": state["status"],
        "mode": state["mode"],
        "preset": state["preset"],
        "source": state["source"],
        "ds_id": state["ds_id"],
        "last_seq": bus.last_seq(run_id),
        "round": state["round"],
        "stages": state["stages"],
        "counts": state["counts"],
        "budget": state["budget"],
        "best_run": state["best_run"],
        "artifacts": state["artifacts"],
        "autonomy": state["autonomy"],
        "provenance_ok": state["provenance_ok"],
        "ds_version": state["ds_version"],
        "label_version": state["label_version"],
        "class_table_version": state["class_table_version"],
    }


# ---------- 重啟認領 ----------

def _pid_alive(pid: int) -> bool:
    # ponytail: os.kill(pid,0) 認不出 pid 回收（重啟後剛好撞到別人的 pid 會被當成還活著）。
    # 真要無誤判就得存 pid 的 start time 比對；M1 的 run 活在 API process 內，重啟必死，夠用。
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不是我的 process
    return True


def _train_pids(state: dict[str, Any]) -> set[int]:
    return {r.get("pid") for r in (state.get("train") or {}).values() if isinstance(r.get("pid"), int)}


def claim_or_crash() -> list[str]:
    """啟動時掃 `status=running`/`queued` 的 run，三種情形三種處置：

    1. **pid 是另一個活著的 API process** → 不碰（那個 run 是它的，我們沒有它的 driver）。
    2. **pid 是活著的訓練/探針子行程** → 它是孤兒：driver task 只活在舊 process 的記憶體裡
       （`console._TASKS`），沒有人會把它推到下一段、也沒有人會結清它。**殺掉它再標 crashed**。
       不這樣做的話 run 會永遠停在 `running`，那顆按鈕永久回 `409 RUN_IN_FLIGHT`，
       而且要再重啟第二次 server 才會被標成 crashed（實測 kill -9 uvicorn 之後就是這樣）。
    3. **pid 已不存在** → 直接標 crashed（DESIGN 護欄 6：「子行程被 OOM kill」不准顯示成
       「進度條卡住」）。

    墓碑事件（`stage.failed`）寫在翻狀態**之前** —— `bus.append_event()` 現在會拒絕往終局的
    run 追加事件（契約 §8.5）。
    """
    crashed: list[str] = []
    for run_id, rec in load_runs().items():
        state = read_state(run_id) or rec
        if state.get("status") not in LIVE_STATUSES:
            continue
        pid = state.get("pid")
        live = isinstance(pid, int) and pid != os.getpid() and _pid_alive(pid)
        if live and pid not in _train_pids(state):
            continue  # 情形 1：另一個 API process 還活著，run 是它的
        why = f"pid {pid} 已不存在"
        if live:  # 情形 2：孤兒子行程 —— 先收乾再標，不能既不管又讓它保持 running
            procs.signal_group(pid, signal.SIGTERM)
            deadline = time.monotonic() + ORPHAN_GRACE_SEC
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.1)
            if _pid_alive(pid):
                procs.signal_group(pid, signal.SIGKILL)
            why = f"孤兒子行程 pid {pid} 已被收掉（API 重啟後沒有 driver 接得回它）"
        # 正在跑的那一格才是死在半路的那一格；沒有 running 的才退回第一個 pending
        # （先撞到 pending 就標它的話，s07 訓練中被殺會把還沒輪到的 s01 標成 failed）。
        stages = state.get("stages", {})
        stuck = next((sid for sid, st in stages.items() if st == "running"),
                     next((sid for sid, st in stages.items() if st == "pending"), "s01"))
        detail = f"API 重啟時 run {run_id} 仍標記 {state.get('status')}，{why}"
        bus.append_event(
            run_id,
            stage=stuck,
            type="stage.failed",
            actor="console-owner",
            data={"stage": stuck, "detail": detail},
            text=f"stage.failed {stuck} — {detail}",
        )
        set_stage(run_id, stuck, "failed")
        set_status(run_id, "crashed")
        crashed.append(run_id)
    return crashed


# ---------- 地基自檢：`uv run python -m src.app.registry` ----------
# 驗的是 registry 最容易靜默壞掉的四件事：id 發號、快照逐欄與契約 §8.3 相同、
# 帳本摺疊（最後一筆勝）、重啟認領（活的留著、死的標 crashed 並推 stage.failed）。

SNAPSHOT_KEYS = frozenset(
    {
        "run_id", "status", "mode", "preset", "source", "ds_id", "last_seq", "round",
        "stages", "counts", "budget", "best_run", "artifacts", "autonomy",
        "provenance_ok", "ds_version", "label_version", "class_table_version",
        "split", "recipe",
    }
)


def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import subprocess
    import sys
    import tempfile

    global RUNS_DIR, RUNS_JSONL, DS_DIR
    RUNS_DIR = Path(tempfile.mkdtemp(prefix="registry-selfcheck-"))
    RUNS_JSONL = RUNS_DIR / "runs.jsonl"
    # ds 發號會掃落檔目錄，自檢也要指到暫存區 —— 不然它會看到你真的跑過的那些 ds 而發出 ds5
    DS_DIR = RUNS_DIR / "datasets"
    bus.RUNS_DIR = RUNS_DIR

    # 1. 開 run：id 發號 + 11 格骨架（s01–s08 pending） + teaching 預算
    state = create_run(mode="oneshot", source="demo", preset="teaching", limit=120, seed=42)
    assert (state["run_id"], state["ds_id"]) == ("r1", "ds1"), state
    assert state["stages"] == {
        s["id"]: ("pending" if s["id"] in PIPELINE_STAGES else "skipped") for s in STAGE_TABLE
    }
    # 只跑前兩段（教學用的 `stages` 參數）：沒選到的格子一律 skipped，不是 pending
    partial = _stage_states(["s01", "s03"])
    assert partial["s01"] == partial["s03"] == "pending" and partial["s04"] == "skipped", partial
    assert [x["status"] for x in stage_skeleton(["s01"])][:2] == ["pending", "skipped"]
    assert state["budget"]["gpu_min_cap"] == 12 and state["budget"]["usd_cap"] == 0.5
    assert state["class_table_version"] is None  # M1 一律 null
    assert running_run() == "r1" and next_run_id() == "r2"

    # 2. 快照逐欄 == 契約 §8.3（多一欄少一欄都會讓前端當天才發現）
    set_stage("r1", "s01", "done")
    set_counts("r1", total=120, loaded=120)
    snap = snapshot("r1")
    assert snap and set(snap) == SNAPSHOT_KEYS, sorted(set(snap or {}) ^ SNAPSHOT_KEYS)
    assert snap["stages"]["s01"] == "done" and snap["counts"]["loaded"] == 120
    assert snap["last_seq"] == 0  # 還沒發任何事件

    # 2b. 側欄不准說謊：split 與 recipe 都由快照回答，前端不自算（實測抓到的 bug 的回歸測）。
    #     s04 之前一律 None（給一組 0 的話畫面會寫「都沒分到」，那正是原本那個謊）。
    assert snap["split"] is None and snap["recipe"] is None, snap
    (DS_DIR / "ds1").mkdir(parents=True, exist_ok=True)
    (DS_DIR / "ds1" / "splits.json").write_text(
        json.dumps({f"demo{i:04d}": s for i, s in enumerate(
            ["train"] * 48 + ["valid"] * 12 + ["anchor"] * 20 + ["sealed_test"] * 30)}), "utf-8"
    )
    sp = snapshot("r1")["split"]
    assert sp == {"train": 48, "valid": 12, "anchor": 20, "sealed_test": 30, "unassigned": 10}, sp
    # recipe 的數字要從 recipe.json 讀（train 紀錄上只有 model/epochs，沒有 imgsz/device）——
    # 少了這一條，側欄會退回顯示 M1 寫死的 imgsz 640，而真的跑的是 320。
    exp = RUNS_DIR / "04-experiments" / "r1-t1"
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "recipe.json").write_text(json.dumps(
        {"params": {"model": "yolov8n", "epochs": 10, "imgsz": 320, "device": "mps"}}), "utf-8")
    st1 = read_state("r1")
    st1["train"] = {
        "r1-t0": {"train_id": "r1-t0", "kind": "probe", "status": "done", "model": "yolov8s"},
        "r1-t1": {"train_id": "r1-t1", "kind": "train", "status": "done", "elapsed_s": 22.3,
                  # 正式環境是 `04-experiments/<tid>/recipe.json` 這種相對路徑；
                  # `PROJECT_ROOT / 絕對路徑` 在 pathlib 就是那個絕對路徑，自檢直接給暫存區。
                  "model": "yolov8n", "epochs": 10, "recipe_path": str(exp / "recipe.json")},
    }
    write_state(st1)
    rcp = frozen_recipe(read_state("r1"))
    assert rcp["train_id"] == "r1-t1" and rcp["model"] == "yolov8n", rcp  # 探針那筆不是配方
    assert (rcp["epochs"], rcp["imgsz"], rcp["device"]) == (10, 320, "mps"), rcp
    st1["train"]["r1-t1"]["recipe_path"] = "04-experiments/沒這個檔/recipe.json"
    write_state(st1)
    assert frozen_recipe(read_state("r1"))["imgsz"] is None, "recipe.json 不見了不該讓快照爆掉"
    del st1["train"]
    write_state(st1)

    # 3. 帳本是 append-only，讀的人取最後一筆
    set_status("r1", "done")
    ledger = [json.loads(x) for x in RUNS_JSONL.read_text("utf-8").splitlines() if x.strip()]
    assert [r["status"] for r in ledger] == ["queued", "done"], ledger  # 只有狀態變化進帳本
    assert load_runs()["r1"]["status"] == "done"  # 摺疊取最後一筆
    assert running_run() is None

    # 4. 重啟認領：pid 還活著的留著、死掉的標 crashed 並推 stage.failed（DESIGN 護欄 6）
    alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    try:
        live_run = create_run(
            mode="oneshot", source="demo", preset="teaching", limit=1, seed=1, pid=alive.pid
        )
        set_status(live_run["run_id"], "running")
        dead_run = create_run(
            mode="oneshot", source="demo", preset="teaching", limit=1, seed=1, pid=dead.pid
        )
        set_status(dead_run["run_id"], "running")
        set_stage(dead_run["run_id"], "s01", "running")

        crashed = claim_or_crash()
        assert crashed == [dead_run["run_id"]], crashed
        # pid 活著且**不是**這個 run 的訓練子行程 → 那是另一個 API process 的 run，不碰
        assert read_state(live_run["run_id"])["status"] == "running"
        assert _pid_alive(alive.pid), "把別人的 API process 殺掉了"
        assert read_state(dead_run["run_id"])["status"] == "crashed"
        failed = [e for e in bus.read_events(dead_run["run_id"]) if e["type"] == "stage.failed"]
        assert len(failed) == 1 and failed[0]["data"]["stage"] == "s01", failed

        # 5. 孤兒訓練子行程：pid 還活著，但 driver 只活在舊 process 的記憶體裡 ——
        #    必須「殺掉它 + 標 crashed」，不能既不管又讓 run 永遠 running（按鈕永久 409）。
        orphan_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        orphan = create_run(mode="oneshot", source="demo", preset="teaching", limit=1, seed=1,
                            pid=orphan_proc.pid)
        st = read_state(orphan["run_id"])
        st["train"] = {f"{orphan['run_id']}-t1": {"pid": orphan_proc.pid, "status": "running"}}
        write_state(st)
        set_status(orphan["run_id"], "running")
        set_stage(orphan["run_id"], "s07", "running")

        assert claim_or_crash() == [orphan["run_id"]]
        assert read_state(orphan["run_id"])["status"] == "crashed"
        assert read_state(orphan["run_id"])["stages"]["s07"] == "failed"
        orphan_proc.wait(timeout=10)
        assert not _pid_alive(orphan_proc.pid), "孤兒子行程還活著（它會繼續燒 GPU 到跑完）"
        # （live_run 還掛著，它是「另一個 API process 的 run」那條路徑，本來就不該被動到）
        assert running_run() != orphan["run_id"], "run 還卡在 running，那顆按鈕會永久 409"
        # 終局之後不准再追加事件（契約 §8.5）
        assert bus.append_event(orphan["run_id"], "s07", "train.warn", "training-engineer",
                                {"msg": "遲到的事件"}) is None
    finally:
        alive.kill()
        alive.wait()
    print(f"registry selfcheck PASS（暫存目錄 {RUNS_DIR}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
