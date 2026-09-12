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
from pathlib import Path
from typing import Any

from . import bus

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
# M1 只跑 s01→s03；其餘一律 skipped（前端要畫出灰色的它們，否則 M2 加回來版面會跳）。
M1_ACTIVE_STAGES = ("s01", "s03")

STAGE_STATUSES = frozenset(
    {"pending", "running", "done", "failed", "skipped", "reused", "awaiting_go"}
)
RUN_STATUSES = frozenset({"queued", "running", "done", "failed", "cancelled", "crashed"})
LIVE_STATUSES = frozenset({"queued", "running"})

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
    *, mode: str, source: str, preset: str, limit: int, seed: int, pid: int | None = None
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
        "stages": {
            s["id"]: ("pending" if s["id"] in M1_ACTIVE_STAGES else "skipped") for s in STAGE_TABLE
        },
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


def stage_skeleton() -> list[dict[str, str]]:
    """`POST /api/v1/runs` 回應裡的 11 格骨架（契約 §8.1）。"""
    return [
        {**s, "status": "pending" if s["id"] in M1_ACTIVE_STAGES else "skipped"}
        for s in STAGE_TABLE
    ]


def snapshot(run_id: str) -> dict[str, Any] | None:
    """`GET /api/v1/runs/{run_id}` 的回應（契約 §8.3，逐欄照抄，內部欄位 pid/limit/seed 不外流）。"""
    state = read_state(run_id)
    if state is None:
        return None
    return {
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


def claim_or_crash() -> list[str]:
    """啟動時掃 `status=running`/`queued` 的 run：pid 活著就留著，死了標 crashed 並推 stage.failed。

    不這樣做的話，「子行程被 OOM kill」在前端會顯示成「進度條卡住」（DESIGN 護欄 6）。
    """
    crashed: list[str] = []
    for run_id, rec in load_runs().items():
        state = read_state(run_id) or rec
        if state.get("status") not in LIVE_STATUSES:
            continue
        pid = state.get("pid")
        if isinstance(pid, int) and _pid_alive(pid) and pid != os.getpid():
            continue  # 還活著（M3 起的訓練子行程）→ 讓它跑，SSE 會 tail 到它的事件
        stuck = next(
            (s for s, st in state.get("stages", {}).items() if st in {"running", "pending"}),
            "s01",
        )
        detail = f"API 重啟時 run {run_id} 仍標記 {state.get('status')}，但 pid {pid} 已不存在"
        set_status(run_id, "crashed")
        set_stage(run_id, stuck, "failed")
        bus.append_event(
            run_id,
            stage=stuck,
            type="stage.failed",
            actor="console-owner",
            data={"stage": stuck, "detail": detail},
            text=f"stage.failed {stuck} — {detail}",
        )
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

    # 1. 開 run：id 發號 + M1 的 11 格骨架 + teaching 預算
    state = create_run(mode="oneshot", source="demo", preset="teaching", limit=120, seed=42)
    assert (state["run_id"], state["ds_id"]) == ("r1", "ds1"), state
    assert state["stages"] == {
        s["id"]: ("pending" if s["id"] in M1_ACTIVE_STAGES else "skipped") for s in STAGE_TABLE
    }
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
        assert read_state(live_run["run_id"])["status"] == "running"
        assert read_state(dead_run["run_id"])["status"] == "crashed"
        failed = [e for e in bus.read_events(dead_run["run_id"]) if e["type"] == "stage.failed"]
        assert len(failed) == 1 and failed[0]["data"]["stage"] == "s01", failed
    finally:
        alive.kill()
        alive.wait()
    print(f"registry selfcheck PASS（暫存目錄 {RUNS_DIR}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
