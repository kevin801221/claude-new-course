"""訓練子行程的起停與狀態 —— API 永遠不阻塞。

契約：`_Context/api-contract.md` §8.9（GO 閘門豁免）、§4（`train.start` / `train.cancelled`）。
設計：`DESIGN.md` 訓練工程專家 (4) 與護欄 6。
擁有者：training-engineer。

為什麼不是 `BackgroundTasks`（評審點名的致命缺口）：`model.train()` 是純阻塞碼，
塞進 ASGI event loop 會佔住唯一那條 thread —— 心跳停、SSE 卡死、cancel 收不到、
matplotlib 在非主執行緒崩。所以：`subprocess.Popen` 起一個真的獨立 process，
API 這邊只留 pid，狀態寫檔（`runs/<run_id>/state.json`），跨重啟也認得回來。

⚠️ 本檔在 API process 內被 import：**一行 torch / ultralytics 都不准出現**。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app import bus, procs, registry  # noqa: E402

ACTOR = "training-engineer"
TRAIN_SCRIPT = PROJECT_ROOT / "scripts" / "train_yolo.py"
EXPERIMENTS_DIR = PROJECT_ROOT / "04-experiments"

# train_id 內嵌 run_id（`r19-t1`），所以「由 train_id 找 run」不需要第二份索引檔。
SEP = "-t"
TERM_GRACE_SEC = 8.0  # SIGTERM 之後等它自己收乾的秒數，逾時才 SIGKILL
LIVE = frozenset({"running", "awaiting_go"})

# train_id → 本 process 起的 Popen handle。**必須留著**：子行程結束後若沒人 `wait()`，
# 它會變成 zombie，而 zombie 的 `os.kill(pid, 0)` 依然成功 —— 只看 pid 的話，
# 訓練跑完了 `alive` 會永遠是 True，cancel 的「等它收乾」也會變成無窮迴圈（自檢第 3 條抓到過）。
# API 重啟後這張表是空的，那時子行程已被 init 收養並收屍，退回純 pid 判斷才是對的。
_PROCS: dict[str, subprocess.Popen] = {}


def run_id_of(train_id: str) -> str:
    return train_id.rsplit(SEP, 1)[0]


def next_train_id(run_id: str) -> str:
    n = len(((registry.read_state(run_id) or {}).get("train") or {})) + 1
    return f"{run_id}{SEP}{n}"


# ---------- state.json 的 `train` 分區（console-owner 的檔，我們只加自己的鍵） ----------

def _load(run_id: str) -> dict[str, Any]:
    state = registry.read_state(run_id)
    if state is None:
        raise KeyError(run_id)
    return state


def read_train(train_id: str) -> dict[str, Any] | None:
    state = registry.read_state(run_id_of(train_id))
    return ((state or {}).get("train") or {}).get(train_id)


def _save_train(train_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    state = _load(run_id_of(train_id))
    trains = state.setdefault("train", {})
    rec = {**trains.get(train_id, {"train_id": train_id}), **patch}
    trains[train_id] = rec
    registry.write_state(state)
    return rec


def _rel(p: Path) -> str:
    """回專案相對路徑（前端與 provenance 都吃相對路徑）；自檢在暫存目錄跑時退回絕對路徑。"""
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def alive(train_id: str, pid: int | None) -> bool:
    """是不是還真的在跑。自己起的孩子看 `poll()`（順手收屍），別人的孩子才看 pid。"""
    proc = _PROCS.get(train_id)
    if proc is not None:
        if proc.poll() is None:
            return True
        _PROCS.pop(train_id, None)  # 已經 reap 掉，不會留 zombie
        return False
    return pid_alive(pid)


# ---------- 起跑 ----------

def start(*, run_id: str, train_id: str, recipe: dict[str, Any], eta_min: float) -> dict[str, Any]:
    """把 recipe 落檔並 `Popen` 起子行程。**立刻回**，不等它跑完。

    `start_new_session=True`：子行程自成一個 process group，cancel 時可以整組 SIGTERM，
    不會留下孤兒 dataloader worker（也不會讓 Ctrl-C 打到 uvicorn 時順手殺掉訓練）。
    """
    exp_dir = EXPERIMENTS_DIR / train_id
    exp_dir.mkdir(parents=True, exist_ok=True)
    recipe_path = exp_dir / "recipe.json"
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", "utf-8")

    start_seq = bus.last_seq(run_id)
    log_path = exp_dir / "train.log"  # 只當人看的除錯 log，**指標一律走事件不走這個檔**
    log_fh = log_path.open("ab")
    proc = subprocess.Popen(
        [sys.executable, str(TRAIN_SCRIPT), "--recipe", str(recipe_path)],
        cwd=str(PROJECT_ROOT),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1"},
    )
    log_fh.close()
    _PROCS[train_id] = proc

    rec = _save_train(train_id, {
        "train_id": train_id, "kind": recipe.get("kind", "train"), "status": "running",
        "pid": proc.pid, "started_at": bus.utc_now_iso(), "recipe_path": _rel(recipe_path),
        "log_path": _rel(log_path), "eta_min": eta_min,
        "model": recipe.get("params", {}).get("model"), "epochs": recipe.get("params", {}).get("epochs"),
        # 事件的 data 沒有 train_id 欄位（契約 §4 凍死了），所以「哪些事件屬於這一次訓練」
        # 用起跑時的 seq 當界線：同一個 run 跑第二次訓練時，才不會讀到上一次的 train.done。
        "start_seq": start_seq,
        "best_pt": None, "last_pt": None, "elapsed_s": None, "exit_code": None,
    })
    # run 層級的 pid 換成訓練子行程：registry.claim_or_crash() 重啟時就會認領它
    # （`os.kill(pid,0)` 活著 → 留著繼續跑；死了 → 標 crashed 並推 stage.failed）。
    state = _load(run_id)
    state["pid"] = proc.pid
    registry.write_state(state)
    return rec


# ---------- 狀態快照（不依賴 SSE，也不需要有人在 watch 子行程） ----------

def _terminal_from_events(run_id: str, since: int) -> tuple[str, dict[str, Any]] | None:
    """子行程自己講的結局。沒有人 wait() 它，所以「它做完了沒」的真相在事件裡。"""
    for ev in reversed(bus.read_events(run_id, since)):
        if ev["type"] == "train.done":
            return "done", ev["data"]
        if ev["type"] == "train.cancelled":
            return "cancelled", {}
        if ev["type"] == "stage.failed":
            return "failed", ev["data"]
        if ev["type"] == "model.selected":  # 探針的結局
            return "done", ev["data"]
    return None


def snapshot(train_id: str) -> dict[str, Any] | None:
    """`GET /train/{train_id}/state`。狀態在**讀的時候**才判定，所以 API 重啟不影響正確性。"""
    rec = read_train(train_id)
    if rec is None:
        return None
    run_id = run_id_of(train_id)
    is_alive = alive(train_id, rec.get("pid"))
    status = rec.get("status")

    # 進度只在「還活著」時從事件流撈；收工後凍結在結清當下的數字。
    # 事件沒有 train_id 欄位（契約 §4），只能用 seq 當下界 —— 但下界擋不住**後面**那一次訓練，
    # 同一個 run 跑第二次之後，t2 的快照會顯示 t3 的 epoch（實測踩過）。
    epoch, total = _progress(run_id, rec.get("start_seq", 0), rec.get("epochs"))
    if status not in LIVE:
        epoch = rec.get("epoch", epoch)
        total = rec.get("total", total)

    if status in LIVE and not is_alive:
        # 進程沒了但狀態還寫著 running：讓事件說話，事件也沒說就是 crashed
        # （子行程被 OOM kill 在前端會顯示成「進度條卡住」—— 這裡就是那個捕手，護欄 6）
        outcome = _terminal_from_events(run_id, rec.get("start_seq", 0))
        if outcome is None:
            status = "crashed"
            bus.append_event(
                run_id, "s07", "train.warn", ACTOR,
                {"msg": f"{train_id} 的子行程 pid {rec.get('pid')} 不見了，且沒有 train.done/cancelled"},
                text=f"train.warn — {train_id} 子行程消失（crashed）",
            )
        else:
            status, data = outcome
            rec.update({k: v for k, v in data.items() if k in {"best_pt", "elapsed_s"}})
        # 收工的那一刻把耗時也凍住：探針的結局事件是 model.selected（沒有 elapsed_s），
        # 不凍的話每次讀快照都用「現在 − 起跑時間」重算，收工三天後會顯示跑了三天。
        rec["elapsed_s"] = rec.get("elapsed_s") or _since_start(rec)
        rec = _save_train(train_id, {**rec, "status": status, "epoch": epoch, "total": total})
        # stage 與 GPU 分鐘數在「發現它結束了」的這一刻結算 —— 沒有人 wait() 子行程，
        # 所以沒有別的時機可以結。重複呼叫要無害（`charged` 旗標擋第二次記帳）。
        registry.set_stage(run_id, "s05" if rec.get("kind") == "probe" else "s07",
                           "done" if status == "done" else "failed")
        _charge_gpu(run_id, train_id, rec)


    # best/last 權重：train.done 只帶 best_pt，last.pt 要自己從 recipe 的落點推
    # （cancel 之後要 resume 就是靠 last.pt，前端也要顯示它存不存在）。
    weights = _weights_dir(rec)
    best_pt = rec.get("best_pt") or _existing(weights / "best.pt")
    last_pt = rec.get("last_pt") or _existing(weights / "last.pt")

    elapsed = rec.get("elapsed_s")
    if elapsed is None:
        elapsed = _since_start(rec)  # 還在跑：現在 − 起跑時間
    cal = _read_calibration()
    return {
        "train_id": train_id, "run_id": run_id, "kind": rec.get("kind", "train"),
        "status": status, "epoch": epoch, "total": total, "pid": rec.get("pid"), "alive": is_alive,
        "best_pt": best_pt, "last_pt": last_pt,
        "elapsed_s": elapsed, "eta_min": rec.get("eta_min"),
        "scale_factor": cal.get("scale_factor"), "calibrated": cal.get("calibrated"),
        "model": rec.get("model"), "recipe_path": rec.get("recipe_path"), "log_path": rec.get("log_path"),
    }


def _since_start(rec: dict[str, Any]) -> float | None:
    started = rec.get("started_at")
    return round(time.time() - _iso_epoch(started), 1) if started else None


def _progress(run_id: str, since: int, default_total: Any) -> tuple[int, Any]:
    for ev in reversed(bus.read_events(run_id, since)):
        if ev["type"] in {"train.epoch", "probe.epoch"}:
            return ev["data"]["epoch"], ev["data"].get("total", default_total)
    return 0, default_total


def _weights_dir(rec: dict[str, Any]) -> Path:
    """recipe 裡的 `project` + `name` 就是 ultralytics 的落點。"""
    recipe_path = rec.get("recipe_path")
    if not recipe_path:
        return Path("/nonexistent")
    path = Path(recipe_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        recipe = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return Path("/nonexistent")
    project = recipe.get("project")
    return Path(project) / recipe.get("name", "yolo") / "weights" if project else Path("/nonexistent")


def _existing(p: Path) -> str | None:
    return _rel(p) if p.exists() else None


def _charge_gpu(run_id: str, train_id: str, rec: dict[str, Any]) -> None:
    """把這次訓練燒掉的分鐘數記進 run 的 budget（前端那條 budget 條要是真的數字）。

    ponytail: 只記帳不擋人。三軸預算的**執行**是 M7 autonomy 的事，而且 §10 現在也沒有
    「超支」的錯誤碼；這裡先把 `gpu_min_used` 變成真的，免得 M7 當天才發現沒人在算。
    """
    if rec.get("charged"):
        return
    # 探針沒有 train.done（它的結局事件是 model.selected），秒數在結清時已由 `_since_start()` 補上。
    minutes = round(float(rec.get("elapsed_s") or _since_start(rec) or 0) / 60, 3)
    if minutes <= 0:
        return
    state = _load(run_id)
    state["budget"]["gpu_min_used"] = round(state["budget"].get("gpu_min_used", 0) + minutes, 3)
    state.setdefault("train", {}).setdefault(train_id, {})["charged"] = True
    registry.write_state(state)


def _read_calibration() -> dict[str, Any]:
    from . import select  # 延後 import，避免兩個模組互相 import

    return select.read_calibration()


def _iso_epoch(ts: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


# ---------- cancel = SIGTERM(pid)，收乾才算停（送訊號的那五行在 app/procs.py，只留一份） ----------

def cancel(train_id: str) -> dict[str, Any]:
    """SIGTERM 整個 process group → 等它收乾（逾時 SIGKILL）→ 推 `train.cancelled` 才算停成功。"""
    rec = read_train(train_id)
    if rec is None:
        raise KeyError(train_id)
    run_id = run_id_of(train_id)
    pid = rec.get("pid")
    stage = "s05" if rec.get("kind") == "probe" else "s07"

    if alive(train_id, pid):
        procs.signal_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + TERM_GRACE_SEC
        while time.monotonic() < deadline and alive(train_id, pid):
            time.sleep(0.1)
        if alive(train_id, pid):  # 不收乾就升級 —— 「按了 Stop 但它還在燒 GPU」是最糟的假成功
            procs.signal_group(pid, signal.SIGKILL)
            while alive(train_id, pid):
                time.sleep(0.05)
    _PROCS.pop(train_id, None)

    event = bus.append_event(
        run_id, stage, "train.cancelled", ACTOR, {},  # 契約 §4：data 就是 `{}`，細節放 text
        text=f"train.cancelled — {train_id} 已收乾（pid {pid}）",
    )
    epoch, total = _progress(run_id, rec.get("start_seq", 0), rec.get("epochs"))
    _save_train(train_id, {"status": "cancelled", "finished_at": bus.utc_now_iso(),
                           "elapsed_s": rec.get("elapsed_s") or _since_start(rec),
                           "epoch": epoch, "total": total})
    registry.set_stage(run_id, stage, "failed")
    # run 已經收工時 `append_event()` 會回 None（契約 §8.5）——「已經停了再停一次」不該變成 500
    return {"train_id": train_id, "status": "cancelled", "pid": pid,
            "stop_seq": event["seq"] if event else bus.last_seq(run_id)}


# ---------- 自檢：`uv run python -m src.train.runner`（不需要 torch / 不需要 GPU） ----------
# 用一支假的「訓練腳本」（只是 sleep 並寫事件）驗真正會壞掉的四件事：
# Popen 立刻回不阻塞、狀態寫得進 state.json、SIGTERM 真的殺得掉並推 train.cancelled、
# 子行程無聲消失會被判成 crashed（而不是永遠 running）。

_FAKE_TRAIN = """
import sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from src.app import bus
bus.RUNS_DIR = Path({runs!r})   # 自檢專用：不准污染真的 runs/
bus.append_event({run!r}, "s07", "train.epoch", "training-engineer",
                 {{"epoch":1,"total":9,"box_loss":1.0,"cls_loss":1.0,"dfl_loss":1.0,
                   "map50":0.1,"map5095":0.05,"mem_mb":0,"eta_s":9}},
                 text="epoch   1/9  box_loss 1.000  mAP50 0.100")
time.sleep(120)
"""


def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import tempfile

    global TRAIN_SCRIPT, EXPERIMENTS_DIR
    tmp = Path(tempfile.mkdtemp(prefix="runner-selfcheck-"))
    bus.RUNS_DIR = tmp / "runs"
    registry.RUNS_DIR = bus.RUNS_DIR
    registry.RUNS_JSONL = bus.RUNS_DIR / "runs.jsonl"
    registry.DS_DIR = tmp / "ds"
    EXPERIMENTS_DIR = tmp / "04-experiments"

    state = registry.create_run(mode="oneshot", source="demo", preset="teaching", limit=1, seed=42)
    run_id = state["run_id"]
    registry.set_status(run_id, "running")
    fake = tmp / "fake_train.py"
    fake.write_text(_FAKE_TRAIN.format(root=str(PROJECT_ROOT), run=run_id, runs=str(bus.RUNS_DIR)), "utf-8")
    TRAIN_SCRIPT = fake

    train_id = next_train_id(run_id)
    assert train_id == f"{run_id}-t1" and run_id_of(train_id) == run_id

    # 1. Popen 立刻回（不阻塞）：起一個 sleep 120 秒的子行程，start() 必須毫秒級返回
    t0 = time.monotonic()
    rec = start(run_id=run_id, train_id=train_id, recipe={"kind": "train", "params": {"model": "fake", "epochs": 9}}, eta_min=2.0)
    spent = time.monotonic() - t0
    assert spent < 3.0, f"start() 花了 {spent:.1f}s —— 它在等子行程，那就是阻塞了"
    assert pid_alive(rec["pid"])
    assert registry.read_state(run_id)["pid"] == rec["pid"], "run 的 pid 沒換成子行程，重啟認領會誤判"

    # 2. 狀態快照看得到 epoch（epoch 來自事件，不是 state.json —— 沒有第二個 writer）
    for _ in range(100):
        snap = snapshot(train_id)
        if snap["epoch"] == 1:
            break
        time.sleep(0.1)
    assert snap["status"] == "running" and snap["alive"] is True and snap["epoch"] == 1, snap
    assert snap["total"] == 9 and snap["elapsed_s"] is not None

    # 3. cancel = SIGTERM，真的死掉才推 train.cancelled
    out = cancel(train_id)
    assert not pid_alive(rec["pid"]), "cancel 回來了但 process 還活著（假的成功）"
    ev = bus.read_events(run_id)[-1]
    assert ev["type"] == "train.cancelled" and ev["data"] == {} and ev["seq"] == out["stop_seq"], ev
    assert snapshot(train_id)["status"] == "cancelled"
    # 耗時要在收工那一刻凍住（不凍的話每次讀都重算，收工三天後會顯示跑了三天）
    assert read_train(train_id)["elapsed_s"] is not None, read_train(train_id)

    # 4. 子行程無聲消失 → crashed（而不是永遠 running 讓前端進度條卡住）
    frozen = snapshot(train_id)["epoch"]
    assert frozen == 1, frozen

    ghost = next_train_id(run_id)
    _save_train(ghost, {"kind": "train", "status": "running", "pid": 999_999, "epochs": 9,
                        "start_seq": bus.last_seq(run_id), "started_at": bus.utc_now_iso()})
    assert snapshot(ghost)["status"] == "crashed", snapshot(ghost)  # start_seq 沒隔開就會讀到 t1 的 cancelled
    assert snapshot(ghost)["epoch"] == 0, "上一次訓練的 epoch 洩漏到這一次的快照"
    assert snapshot(train_id)["epoch"] == frozen, "收工的訓練的 epoch 被後來那一次蓋掉了"
    assert any(e["type"] == "train.warn" for e in bus.read_events(run_id))
    print(f"train.runner selfcheck PASS（暫存目錄 {tmp}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
