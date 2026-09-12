#!/usr/bin/env python3
"""逐張推論的**子行程入口** —— 把預測框存成 JSON，不存畫好的 PNG。

    uv run --extra train python -m src.eval.infer \
        --weights 04-experiments/r30-t1/yolo/weights/best.pt --split valid \
        --out 04-experiments/r30-t1/eval/valid.json

為什麼不存 PNG（DESIGN s08 / 前端規則）：畫死的圖不能 hover、不能跟 GT 對照、不能跨輪疊圖，
而且前端本來就要把框疊在 `GET /api/v1/images/{id}` 的原圖上。所以這裡只吐座標。

本檔是 metric-auditor 唯一 import torch / ultralytics 的地方 —— API process 不准 import 它，
只准 `subprocess` 起它（`src/app/routers/eval.py`）。

擁有者：metric-auditor。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# 必須在 import torch 之前設（沿用 train_yolo.py 的理由：MPS 沒實作的 op 會丟例外不會自動退 CPU）
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from src.eval import metrics  # noqa: E402

CONF = 0.001   # 推論門檻刻意壓到底：AP 要完整的 PR 曲線，篩掉低分框等於自己把 recall 砍掉
IOU_NMS = 0.7


def predict_split(*, weights: Path, images_dir: Path, labels_dir: Path,
                  imgsz: int = 320, device: str = "mps", conf: float = CONF) -> dict[str, Any]:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    files = sorted(images_dir.glob("*.png"))
    gt = metrics.load_gt(labels_dir)
    records: list[dict[str, Any]] = []
    speed = {"preprocess_ms": 0.0, "inference_ms": 0.0, "postprocess_ms": 0.0}

    for path in files:
        image_id = metrics.image_id_of(path.name)
        res = model.predict(str(path), imgsz=imgsz, device=device, conf=conf, iou=IOU_NMS,
                            verbose=False)[0]
        boxes = []
        for b in res.boxes:
            cx, cy, w, h = (float(x) for x in b.xywhn[0].tolist())
            boxes.append({"cls": int(b.cls.item()), "cx": round(cx, 5), "cy": round(cy, 5),
                          "w": round(w, 5), "h": round(h, 5), "conf": round(float(b.conf.item()), 5)})
        gt_boxes = gt.get(image_id, [])
        for k in speed:
            speed[k] += float(res.speed.get(k.replace("_ms", ""), 0.0))
        records.append({
            "image_id": image_id,
            "name": path.name,
            "url": f"/api/v1/images/{image_id}",
            "boxes": boxes,
            "gt_boxes": gt_boxes,
            "iou_vs_gt": metrics.best_iou_per_gt(boxes, gt_boxes),
        })

    # 作業點由這一份資料自己決定（最佳 F1），不是寫死的 0.25 —— 見 metrics.operating_point()
    op = metrics.operating_point(records, len(metrics.class_names()))
    metrics.annotate_iou(records, op)

    n = max(1, len(files))
    return {
        "op_conf": op,
        "weights": str(weights),
        "images_dir": str(images_dir),
        "n_images": len(files),
        "imgsz": imgsz, "device": device, "conf": conf, "iou_nms": IOU_NMS,
        "speed": {**{k: round(v / n, 3) for k, v in speed.items()},
                  "ms_per_image": round(sum(speed.values()) / n, 3)},
        "records": records,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True, type=Path)
    ap.add_argument("--split", required=True)          # valid / anchor / train / sealed-test
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--device", default="mps")
    args = ap.parse_args()

    # 封印 test 的資料層鎖：`--split test` 走不到 sealed-test，只有明文的 `sealed-test` 走得到，
    # 而呼叫方只有 `POST /final-test`（它自己還要先驗 stop 事件與「一個 ds_version 只開封一次」）。
    if args.split == "sealed-test":
        images_dir, labels_dir = metrics.sealed_dirs()
    else:
        images_dir, labels_dir = metrics.split_dirs(args.split)

    out = predict_split(weights=args.weights, images_dir=images_dir, labels_dir=labels_dir,
                        imgsz=args.imgsz, device=args.device)
    out["split"] = args.split
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False) + "\n", "utf-8")
    print(f"[eval.infer] {args.split}: {out['n_images']} 張圖 / "
          f"{sum(len(r['boxes']) for r in out['records'])} 個預測框 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
