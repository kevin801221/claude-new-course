#!/usr/bin/env python3
"""訓練 / 探針的**子行程入口**：吃一份 recipe.json，跑 Ultralytics YOLO。

    uv sync --extra train
    uv run python scripts/train_yolo.py --recipe 04-experiments/r19-t1/recipe.json

正常情況下不是人手動跑的 —— `src/train/runner.py` 用 `subprocess.Popen` 起它
（API process 不准 import torch，更不准阻塞 event loop）。手動跑只是為了單獨 debug。

指標怎麼出去：**子行程內掛 `on_fit_epoch_end` callback 直接 append events.jsonl**
（`src/train/callbacks.py`，契約 §2 裁決 B）。不 parse stdout —— 實測 ultralytics 的 log
帶 ANSI 色碼與 `\\r` 進度條，撈出來的數字會被進度條覆寫。本檔的 stdout 只是給人看的 log。

擁有者：training-engineer。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))  # fetch_weights.py 不是套件，用路徑取用

# 必須在 import torch 之前設。MPS 沒實作的 op 會靜默丟例外而不是自動退 CPU，
# 少了這一行，訓練會在某個 op 上整個掛掉（而不是慢一點）。
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from src.app import bus  # noqa: E402
from src.train import callbacks, select  # noqa: E402

ACTOR = "training-engineer"
PROBE_MAX_EPOCHS = 4      # 上限；真正的停點是「mAP50-95 曲線第一次持平」
PROBE_PLATEAU_EPS = 0.005
PROBE_MIN_EPOCHS = 2      # scale_factor 要靠前 2 個 epoch 的實際秒數


def _load_recipe(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def _split_counts(data_yaml: Path) -> tuple[int, int]:
    from src.autolabel.freeze import images_in

    root = data_yaml.parent
    n = lambda d: len(images_in(root / "images" / d))  # noqa: E731
    return n("train"), n("valid")


def _ensure_weights(run_id: str, name: str) -> Path:
    """權重快取 + sha256 + 真的 forward 一次驗 MPS → `model.weights.fetched`。

    已經有 `<name>.pt.json` 且 `mps_ok` 為真就沿用（離線環境也還跑得動，DESIGN 的
    「離線回既有快取並標 offline，不讓整條流程卡在下載」）。
    """
    import fetch_weights as fw

    url = f"https://github.com/ultralytics/assets/releases/download/{fw.ASSET_RELEASE}/{name}.pt"
    pt = fw.fetch(f"{name}.pt", url)
    meta_path = fw.WEIGHTS_DIR / f"{name}.pt.json"
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else None
    if not (meta and meta.get("mps_ok")):
        _, device = fw.dummy_forward(pt)
        meta = {"name": f"{name}.pt", "bytes": pt.stat().st_size, "sha256": fw.sha256(pt),
                "mps_ok": device == "mps"}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "utf-8")
    bus.append_event(
        run_id, "s05", "model.weights.fetched", ACTOR, meta,
        text=f"weights {meta['name']}  {meta['bytes']/1e6:.1f} MB  sha256 {meta['sha256'][:12]}…  mps_ok={meta['mps_ok']}",
    )
    return pt


def _fit(*, weights: Path, args: dict[str, Any], run_id: str, kind: str, name: str,
         total: int, plateau_eps: float | None) -> tuple[Any, dict[str, Any]]:
    """跑一次 `model.train()`，OOM 時 batch 減半重試一次（DESIGN (5)）。"""
    from ultralytics import YOLO

    for attempt in (1, 2):
        model = YOLO(str(weights))
        stats = callbacks.attach(model, run_id=run_id, kind=kind, name=name, total=total,
                                 plateau_eps=plateau_eps, min_epochs=PROBE_MIN_EPOCHS)
        try:
            model.train(**args)
            return model, stats
        except (RuntimeError, MemoryError) as exc:
            oom = "out of memory" in str(exc).lower() or isinstance(exc, MemoryError)
            if not oom or attempt == 2 or args.get("batch", 1) <= 1:
                raise
            args["batch"] = max(1, int(args["batch"]) // 2)
            bus.append_event(
                run_id, "s07", "train.warn", ACTOR,
                {"msg": f"OOM，batch 退避到 {args['batch']} 重試一次"},
                text=f"train.warn — OOM，batch → {args['batch']} 重試",
            )
    raise RuntimeError("unreachable")


def _train_args(recipe: dict[str, Any], *, epochs: int | None = None, sub: str | None = None) -> dict[str, Any]:
    p = recipe["params"]
    return {
        "data": recipe["data"],
        "epochs": int(epochs or p["epochs"]),
        "imgsz": int(p["imgsz"]),
        "batch": int(p["batch"]),
        "lr0": float(p["lr0"]),
        "optimizer": p["optimizer"],
        "patience": int(p["patience"]),
        "amp": bool(p["amp"]),
        "seed": int(p["seed"]),
        "device": p["device"],
        "workers": int(p["workers"]),
        "project": recipe["project"],
        "name": sub or recipe.get("name", "yolo"),
        "exist_ok": True,
        "plots": False,   # 圖表一律前端 inline SVG 自畫，後端不產 PNG
        "val": True,
        "verbose": True,
        **recipe.get("augment", {}),
    }


# ---------- kind=train：正式訓練（s07） ----------

def run_train(recipe: dict[str, Any]) -> int:
    run_id, params = recipe["run_id"], recipe["params"]
    weights = _ensure_weights(run_id, params["model"])
    args = _train_args(recipe)
    t0 = time.perf_counter()
    model, stats = _fit(weights=weights, args=args, run_id=run_id, kind="train",
                        name=params["model"], total=args["epochs"], plateau_eps=None)
    elapsed = round(time.perf_counter() - t0, 1)

    save_dir = Path(getattr(model.trainer, "save_dir", Path(args["project"]) / args["name"]))
    best = save_dir / "weights" / "best.pt"
    bus.append_event(
        run_id, "s07", "train.done", ACTOR,
        {"best_pt": str(best.relative_to(PROJECT_ROOT)) if best.is_relative_to(PROJECT_ROOT) else str(best),
         "elapsed_s": elapsed},
        text=f"done → {best.name}（{elapsed:.0f}s，{len(stats['epochs'])} epochs）",
    )
    (Path(args["project"]) / "result.json").write_text(
        json.dumps({"epochs": stats["epochs"], "keys_seen": stats["keys_seen"],
                    "elapsed_s": elapsed, "best_pt": str(best)}, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    print(f"[train_yolo] done best={best} elapsed={elapsed}s")
    return 0


# ---------- kind=probe：短探針選型 + ETA 校準（s05 / s06） ----------

def run_probe(recipe: dict[str, Any]) -> int:
    run_id, params = recipe["run_id"], recipe["params"]
    names = recipe.get("names") or [c["name"] for c in select.CANDIDATES]
    results, per_model_sec = [], {}

    for name in names:
        weights = _ensure_weights(run_id, name)
        args = _train_args(recipe, epochs=PROBE_MAX_EPOCHS, sub=f"probe-{name}")
        model, stats = _fit(weights=weights, args=args, run_id=run_id, kind="probe", name=name,
                            total=PROBE_MAX_EPOCHS, plateau_eps=PROBE_PLATEAU_EPS)
        secs = stats["epoch_sec"]
        # 穩定態秒/epoch（丟掉第一個 epoch 的 Metal shader 首編）。ETA 要用這個，
        # 不能用含 warmup 的平均 —— 實測第 1 個 epoch 18.8s、之後 1.2s，差 15 倍。
        per_model_sec[name] = callbacks.steady_sec(secs)
        speed = getattr(getattr(model, "trainer", None), "validator", None)
        ms_img = None
        try:
            ms_img = round(float(speed.speed["inference"]), 3)
        except Exception:  # noqa: BLE001 —— 拿不到就讓評分函式少一項，不是讓探針失敗
            pass
        results.append({
            "name": name,
            "map_series": [e["map5095"] for e in stats["epochs"]],
            "epoch_sec": secs,
            "warmup_sec": secs[0] if secs else None,
            "ms_img": ms_img,
            "mem_mb": max((e["mem_mb"] for e in stats["epochs"]), default=0.0),
            "sec_per_epoch": round(per_model_sec[name], 3),
            "stopped_early": stats["stopped_early"],
        })

    # ETA 校準（護欄 5）：本機實測秒/epoch ÷ 講師錨點。之後所有 ETA 都乘這個數。
    # 錨定第一個候選（yolov8n）—— scale_factor 必須綁一個固定機型，不然兩台機器比的是不同東西。
    anchor = results[0]["name"]
    # probe_2ep_sec = 真的跑前 2 個 epoch 的牆鐘秒數（含 warmup，這是「跑兩輪要多久」的誠實答案）；
    # scale_factor 則用穩定態，否則 warmup 會讓 10 epochs 的 ETA 高估一個數量級。
    probe_2ep_sec = round(sum((results[0].get("epoch_sec") or [0])[:PROBE_MIN_EPOCHS]), 2)
    scale_factor = round(per_model_sec[anchor] / select.BASE_SEC_PER_EPOCH, 4)
    n_train, n_valid = _split_counts(Path(recipe["data"]))
    cal = select.write_calibration(
        scale_factor=scale_factor, probe_2ep_sec=probe_2ep_sec, name=anchor,
        epochs=PROBE_MIN_EPOCHS, imgsz=params["imgsz"], n_train=n_train, per_model_sec=per_model_sec,
    )
    bus.append_event(
        run_id, "s06", "train.calibrated", ACTOR,
        {"scale_factor": cal["scale_factor"], "probe_2ep_sec": cal["probe_2ep_sec"]},
        text=(f"ETA 已依本機 2-epoch 校準 ×{cal['scale_factor']}"
              f"（{cal['probe_2ep_sec']}s / 2 epochs，講師錨點 {select.BASE_SEC_PER_EPOCH:.1f}s/epoch）"),
    )

    rows = select.rank(results, epochs=select.BASELINE["epochs"])
    picked = select.choose(rows)
    bus.append_event(
        run_id, "s05", "model.selected", ACTOR,
        {"name": picked["name"], "score": picked["score"], "why": picked["why"]},
        text=f"model.selected {picked['name']}  score {picked['score']}  — {picked['why']}",
    )
    (Path(recipe["project"]) / "probe.json").write_text(
        json.dumps({"rows": rows, "selected": picked, "calibration": cal, "results": results},
                   ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"[train_yolo] probe done selected={picked['name']} scale_factor={scale_factor}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", required=True, type=Path)
    args = ap.parse_args()
    recipe = _load_recipe(args.recipe)
    run_id = recipe["run_id"]
    try:
        return run_probe(recipe) if recipe.get("kind") == "probe" else run_train(recipe)
    except Exception as exc:  # noqa: BLE001
        # `stage.failed` 是 console-owner 的前綴（bus 會擋越權），子行程能發的只有 train.warn。
        # API 那側的 `runner.snapshot()` 看到「pid 沒了又沒有 train.done」就會判 crashed。
        bus.append_event(
            run_id, "s07", "train.warn", ACTOR,
            {"msg": f"{type(exc).__name__}: {exc}"},
            text=f"train.warn — 子行程失敗 {type(exc).__name__}: {exc}",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
