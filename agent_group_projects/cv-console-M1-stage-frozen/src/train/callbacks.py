"""訓練子行程內的 Ultralytics callback → `bus.append_event()`。

契約：`_Context/api-contract.md` §2 裁決 B（**只有 bus.py 與本檔能寫 events.jsonl，
而本檔必須 import bus 的 `append_event()`**，不准自己拼 JSON、不准自己算 seq）、
§4（`train.epoch` / `probe.epoch` 的 data 欄位）。
擁有者：training-engineer。

為什麼是 callback 不是 parse stdout（DESIGN 訓練工程專家 (4)）：
實測 ultralytics 的 log 帶 ANSI 色碼與 `\\r` 進度條，用正則去撈數字是坑 ——
epoch 行會被進度條覆寫、mAP 欄位在不同 task 位置不同。callback 拿到的是 trainer 物件本身。

本檔在 **API process 內永遠不會被 import**（那裡沒有 torch）。torch 只在 `_mem_mb()`
裡 lazy import，所以 `python -m src.train.callbacks` 的自檢用假 trainer 就能跑。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app import bus  # noqa: E402  ← 裁決 B：發號與序列化只有這一隻手

ACTOR = "training-engineer"
# kind → (stage, 事件 type)。probe 是 s05 的短探針，正式訓練是 s07。
KINDS = {"train": ("s07", "train.epoch"), "probe": ("s05", "probe.epoch")}


def _flat(trainer: Any) -> dict[str, float]:
    """把 trainer 身上兩坨指標攤平成一個 dict：train loss（tloss）+ validator metrics。"""
    out: dict[str, float] = {}
    try:
        for k, v in trainer.label_loss_items(trainer.tloss, prefix="train").items():
            out[k] = float(v)
    except Exception:  # noqa: BLE001 —— 指標拿不到不該把訓練弄掛，寧可送 0.0
        pass
    for k, v in (getattr(trainer, "metrics", None) or {}).items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _pick(flat: dict[str, float], *cands: str, default: float = 0.0) -> float:
    """先整鍵命中，再用「最後一段去掉 (B)」比對。

    不用 `in` / `startswith` 是因為 `metrics/mAP50` 是 `metrics/mAP50-95(B)` 的前綴 ——
    用前綴比對會把 mAP50-95 的數字當成 mAP50 送出去，而且圖表看起來完全正常。
    """
    for c in cands:
        if c in flat:
            return flat[c]

    def tail(k: str) -> str:
        return k.rsplit("/", 1)[-1].removesuffix("(B)")

    for c in cands:
        for k, v in flat.items():
            if tail(k) == tail(c):
                return v
    return default


def steady_sec(secs: list[float]) -> float:
    """每 epoch 的穩定態秒數：有兩筆以上就丟掉第一筆（Metal shader 首次編譯），取中位數。"""
    if not secs:
        return 0.0
    rest = secs[1:] or secs
    ordered = sorted(rest)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _mem_mb() -> float:
    """MPS 驅動已配置的記憶體（MB）。拿不到就回 0，不讓它變成訓練失敗的理由。"""
    try:
        import torch

        if torch.backends.mps.is_available():
            return round(torch.mps.driver_allocated_memory() / (1 << 20), 1)
        return round(torch.cuda.max_memory_allocated() / (1 << 20), 1) if torch.cuda.is_available() else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def attach(
    model: Any,
    *,
    run_id: str,
    kind: str = "train",
    name: str = "yolov8n",
    total: int | None = None,
    plateau_eps: float | None = None,
    min_epochs: int = 2,
) -> dict[str, Any]:
    """掛 `on_fit_epoch_end`，每個 epoch 直接 append 一筆事件。回傳會被就地更新的統計 dict。

    `plateau_eps` 不是 None 時（探針用）：mAP50-95 相對歷史最佳的增幅 <= eps 就
    `trainer.stop = True` —— DESIGN 要的是「跑到曲線第一次持平」而不是固定 3 epochs
    （ultralytics `_do_train` 在 `on_fit_epoch_end` 之後就檢查 `self.stop` 並 break）。
    `min_epochs=2` 是硬下限：ETA 的 `scale_factor` 要靠前 2 個 epoch 的實際秒數算。
    """
    if kind not in KINDS:
        raise ValueError(f"kind 只接受 {list(KINDS)}，收到 {kind!r}")
    stage, ev_type = KINDS[kind]
    stats: dict[str, Any] = {
        "name": name,
        "epochs": [],          # 每個 epoch 一筆完整指標
        "epoch_sec": [],       # 每個 epoch 的牆鐘秒數（scale_factor 的原料）
        "best_map5095": 0.0,
        "stopped_early": False,
        "last_epoch": 0,
        "final_eval_skipped": 0,
        "keys_seen": [],       # 第一個 epoch 的原始鍵名，寫進 train-notes 當證據
    }
    clock = {"last": time.monotonic()}

    def on_fit_epoch_end(trainer: Any) -> None:
        epoch = int(getattr(trainer, "epoch", 0)) + 1  # trainer.epoch 是 0-based
        n_total = int(total or getattr(trainer, "epochs", 0) or epoch)
        # ⚠️ ultralytics 跑完之後的 **final_eval（驗 best.pt）也會再觸發一次同一個 callback**：
        # `final_eval()` 裡是 `self.epoch += 1` → `run_callbacks("on_fit_epoch_end")` → `self.epoch -= 1`，
        # 所以它永遠冒充「最後一個 epoch + 1」。只看 epoch 編號擋不掉提早停止的情形
        # （實測：探針在第 2 個 epoch 持平停下，final_eval 就送出一筆假的「epoch 3」）。
        # 真正分得出來的是 validator：迴圈內是 `self.validator(self)`（trainer 有值 → training=True），
        # final_eval 是 `self.validator(model=model)`（trainer=None → training=False）。
        in_loop = getattr(getattr(trainer, "validator", None), "training", True)
        if not in_loop or epoch <= stats["last_epoch"] or epoch > n_total:
            stats["final_eval_skipped"] += 1
            return

        now = time.monotonic()
        sec = now - clock["last"]
        clock["last"] = now
        stats["epoch_sec"].append(round(sec, 3))
        stats["last_epoch"] = epoch

        flat = _flat(trainer)
        if not stats["keys_seen"]:
            stats["keys_seen"] = sorted(flat)
        map5095 = _pick(flat, "metrics/mAP50-95(B)", "metrics/mAP50-95")
        map50 = _pick(flat, "metrics/mAP50(B)", "metrics/mAP50")
        box = _pick(flat, "train/box_loss", "box_loss")
        cls = _pick(flat, "train/cls_loss", "cls_loss")
        dfl = _pick(flat, "train/dfl_loss", "dfl_loss")
        # 已跑完的**穩定態** epoch 秒數 × 剩下幾個 —— 比抄講師機器的分鐘數誠實。
        # 第一個 epoch 要編 Metal shader（本機實測 18.8s vs 之後 1.2s，差 15 倍），
        # 把它算進平均會讓 ETA 高估一個數量級，所以有第二個 epoch 之後就不看它。
        eta_s = int(round(steady_sec(stats["epoch_sec"]) * max(n_total - epoch, 0)))

        record = {
            "epoch": epoch, "total": n_total, "box_loss": round(box, 4), "cls_loss": round(cls, 4),
            "dfl_loss": round(dfl, 4), "map50": round(map50, 4), "map5095": round(map5095, 4),
            "mem_mb": _mem_mb(), "eta_s": eta_s, "sec": round(sec, 3),
        }
        stats["epochs"].append(record)

        if kind == "train":
            data = {k: record[k] for k in
                    ("epoch", "total", "box_loss", "cls_loss", "dfl_loss", "map50", "map5095", "mem_mb", "eta_s")}
            # 契約 §2 的範例格式，前端 #log 既有渲染器直接印這一行，不在前端拼字
            text = f"epoch {epoch:>3}/{n_total}  box_loss {box:.3f}  mAP50 {map50:.3f}"
        else:
            data = {"name": name, "epoch": epoch, "map5095": round(map5095, 4)}
            text = f"probe {name}  epoch {epoch}/{n_total}  mAP50-95 {map5095:.4f}"
        bus.append_event(run_id, stage, ev_type, ACTOR, data, text=text)

        gain = map5095 - stats["best_map5095"]
        stats["best_map5095"] = max(stats["best_map5095"], map5095)
        if plateau_eps is not None and epoch >= min_epochs and gain <= plateau_eps:
            stats["stopped_early"] = True
            trainer.stop = True  # _do_train 在本 callback 之後就 `if self.stop: break`

    model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
    return stats


# ---------- 自檢：`uv run python -m src.train.callbacks`（不需要 torch / 不需要 GPU） ----------
# 用假 trainer 驗三件最容易靜默壞掉的事：鍵名比對不准把 mAP50-95 當成 mAP50、
# `text` 是契約 §2 那一行格式、plateau 停在第一次持平。

class _FakeValidator:  # pragma: no cover - 自檢用
    def __init__(self, training: bool) -> None:
        self.training = training


class _FakeTrainer:  # pragma: no cover - 自檢用
    def __init__(self, epoch: int, epochs: int, map50: float, map5095: float,
                 in_loop: bool = True) -> None:
        self.epoch, self.epochs, self.stop = epoch, epochs, False
        self.validator = _FakeValidator(in_loop)
        self.tloss = {"box_loss": 1.2345, "cls_loss": 0.8765, "dfl_loss": 1.0111}
        self.metrics = {
            "metrics/precision(B)": 0.5, "metrics/recall(B)": 0.4,
            "metrics/mAP50(B)": map50, "metrics/mAP50-95(B)": map5095,
            "val/box_loss": 9.9, "fitness": map5095,
        }

    def label_loss_items(self, loss_items=None, prefix="train"):
        return {f"{prefix}/{k}": v for k, v in (loss_items or {}).items()}


class _FakeModel:  # pragma: no cover - 自檢用
    def __init__(self) -> None:
        self.cb = None

    def add_callback(self, _event, fn):
        self.cb = fn


def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import json
    import tempfile

    bus.RUNS_DIR = Path(tempfile.mkdtemp(prefix="train-cb-selfcheck-"))

    # 1. train：欄位齊、mAP50 沒被 mAP50-95 汙染、text 是契約 §2 那一行
    m = _FakeModel()
    st = attach(m, run_id="r1", kind="train", total=50)
    m.cb(_FakeTrainer(epoch=11, epochs=50, map50=0.567, map5095=0.312))
    ev = bus.read_events("r1")[-1]
    assert ev["type"] == "train.epoch" and ev["stage"] == "s07" and ev["actor"] == ACTOR
    d = ev["data"]
    assert d["map50"] == 0.567 and d["map5095"] == 0.312, d   # 前綴比對會讓這兩個變同一個數字
    assert (d["box_loss"], d["cls_loss"], d["dfl_loss"]) == (1.2345, 0.8765, 1.0111), d
    assert set(d) == {"epoch", "total", "box_loss", "cls_loss", "dfl_loss", "map50", "map5095",
                      "mem_mb", "eta_s"}, sorted(d)
    assert ev["text"] == "epoch  12/50  box_loss 1.234  mAP50 0.567", repr(ev["text"])
    assert st["keys_seen"] and "metrics/mAP50-95(B)" in st["keys_seen"]

    # 2. probe：只有契約 §4 的三個欄位，stage 是 s05
    m2 = _FakeModel()
    attach(m2, run_id="r2", kind="probe", name="yolov8s", total=4)
    m2.cb(_FakeTrainer(epoch=0, epochs=4, map50=0.3, map5095=0.11))
    ev2 = bus.read_events("r2")[-1]
    assert ev2["type"] == "probe.epoch" and ev2["stage"] == "s05"
    assert set(ev2["data"]) == {"name", "epoch", "map5095"} and ev2["data"]["name"] == "yolov8s"

    # 3. plateau：min_epochs 之前不准停，持平的那個 epoch 要停
    m3 = _FakeModel()
    st3 = attach(m3, run_id="r3", kind="probe", name="yolov8n", total=8, plateau_eps=0.005)
    t1 = _FakeTrainer(epoch=0, epochs=8, map50=0.1, map5095=0.10)
    m3.cb(t1)
    assert t1.stop is False and st3["stopped_early"] is False, "第 1 個 epoch 不准停（scale_factor 要 2 個）"
    t2 = _FakeTrainer(epoch=1, epochs=8, map50=0.2, map5095=0.30)
    m3.cb(t2)
    assert t2.stop is False, "還在進步就不該停"
    t3 = _FakeTrainer(epoch=2, epochs=8, map50=0.2, map5095=0.302)  # +0.002 <= eps
    m3.cb(t3)
    assert t3.stop is True and st3["stopped_early"] is True, "曲線持平了卻沒停"

    assert len(json.loads(json.dumps(st3["epochs"]))) == 3

    # 4. ultralytics 的 final_eval 會再觸發一次 callback，不准變成多一個 epoch 的點
    m4 = _FakeModel()
    st4 = attach(m4, run_id="r4", kind="train", total=2)
    m4.cb(_FakeTrainer(epoch=0, epochs=2, map50=0.1, map5095=0.1))
    m4.cb(_FakeTrainer(epoch=1, epochs=2, map50=0.2, map5095=0.2))
    m4.cb(_FakeTrainer(epoch=2, epochs=2, map50=0.2, map5095=0.2, in_loop=False))  # final_eval
    eps = [e["data"]["epoch"] for e in bus.read_events("r4") if e["type"] == "train.epoch"]
    assert eps == [1, 2], eps                       # 不該有 epoch 3/2
    assert st4["final_eval_skipped"] == 1 and len(st4["epoch_sec"]) == 2

    # 4b. 提早停止時 final_eval 的假 epoch 編號**在範圍內**，只靠編號擋不掉（實測踩過）
    m5 = _FakeModel()
    st5 = attach(m5, run_id="r5", kind="probe", name="yolov8s", total=4, plateau_eps=0.005)
    m5.cb(_FakeTrainer(epoch=0, epochs=4, map50=0.3, map5095=0.1815))
    t_stop = _FakeTrainer(epoch=1, epochs=4, map50=0.3, map5095=0.1406)
    m5.cb(t_stop)
    assert t_stop.stop is True                      # 退步 → 持平判定成立，迴圈在這裡 break
    m5.cb(_FakeTrainer(epoch=2, epochs=4, map50=0.3, map5095=0.1826, in_loop=False))  # final_eval
    eps5 = [e["data"]["epoch"] for e in bus.read_events("r5") if e["type"] == "probe.epoch"]
    assert eps5 == [1, 2], eps5                     # 不該多一筆「epoch 3/4」
    assert st5["final_eval_skipped"] == 1

    # 5. 穩定態秒數要丟掉第一個 epoch（首次 Metal shader 編譯會讓 ETA 高估一個數量級）
    assert steady_sec([18.8, 1.2, 1.3]) == 1.3 or steady_sec([18.8, 1.2, 1.3]) == 1.25, steady_sec([18.8, 1.2, 1.3])
    assert steady_sec([18.8]) == 18.8 and steady_sec([]) == 0.0

    print(f"train.callbacks selfcheck PASS（暫存目錄 {bus.RUNS_DIR}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
