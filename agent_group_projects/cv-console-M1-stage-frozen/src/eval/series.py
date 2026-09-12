"""圖表 series —— Ultralytics 的 results.csv 與逐張預測轉成 **JSON series**，不送 PNG。

契約：`_Context/api-contract.md` §4（`charts.series {curves|pr|confusion|per_class}`）。
設計：`DESIGN.md` s08「圖表面板一次鋪四張純 inline SVG」。
擁有者：metric-auditor。

為什麼不送 PNG（DESIGN「刻意不做的事」第 6 條）：PNG 不能 hover、不能跨輪疊圖、
不能在前端把落在 noise floor 灰帶內的點灰掉。後端只給座標，畫圖是前端的事。

⚠️ 不准 import torch / ultralytics / matplotlib。自檢：`uv run python -m src.eval.series`
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eval import metrics  # noqa: E402

CONF_OP = 0.01       # 退路而已；正常路徑由 metrics.operating_point() 給（最佳 F1 那一點）
IOU_OP = 0.45
PR_POINTS = 50       # PR 曲線每類最多幾個點 —— 曲線是拿來看形狀的，不是拿來存原始資料的

# results.csv 的欄名 → series 的欄名（ultralytics 的欄名帶 `(B)` 與斜線，前端不該碰這個）
CSV_MAP = {
    "train/box_loss": "train_box_loss",
    "train/cls_loss": "train_cls_loss",
    "train/dfl_loss": "train_dfl_loss",
    "val/box_loss": "val_box_loss",
    "val/cls_loss": "val_cls_loss",
    "val/dfl_loss": "val_dfl_loss",
    "metrics/precision(B)": "precision",
    "metrics/recall(B)": "recall",
    "metrics/mAP50(B)": "map50",
    "metrics/mAP50-95(B)": "map5095",
    "time": "time_s",
}


def curves_from_csv(results_csv: Path) -> list[dict[str, Any]]:
    """訓練曲線。讀 results.csv 而不是 parse stdout —— log 帶 ANSI 與 `\\r`，撈出來的數字會被覆寫。"""
    if not results_csv.exists():
        return []
    out = []
    with results_csv.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            point: dict[str, Any] = {"epoch": int(float(row["epoch"]))}
            for raw, key in CSV_MAP.items():
                v = (row.get(raw) or "").strip()
                if v:
                    point[key] = round(float(v), 5)
            out.append(point)
    return out


def pr_curves(result: dict[str, Any], names: Sequence[str],
              max_points: int = PR_POINTS) -> list[dict[str, Any]]:
    """每類一條 PR 曲線（IoU 0.5）。沒有 GT 的類別不畫 —— 畫一條全 0 的線只會誤導。"""
    images = result["_images"]
    out = []
    for c, name in enumerate(names):
        m = result["_matches"][c]
        support = sum(m["n_gt"].get(i, 0) for i in images)
        if support == 0:
            continue
        confs = np.concatenate([m["per_image"][i]["conf"] for i in images] or [np.array([])])
        tps = [m["per_image"][i]["tp"] for i in images]
        tp = np.concatenate(tps) if tps else np.zeros((0, len(metrics.IOU_THRESHOLDS)), dtype=bool)
        if confs.size == 0:
            out.append({"cls": c, "name": name, "support": support, "points": []})
            continue
        order = np.argsort(-confs)
        cum = np.cumsum(tp[order, 0])
        recall = cum / support
        precision = cum / np.arange(1, cum.size + 1)
        idx = np.unique(np.linspace(0, cum.size - 1, min(max_points, cum.size)).astype(int))
        out.append({
            "cls": c, "name": name, "support": int(support),
            "points": [{"r": round(float(recall[i]), 4), "p": round(float(precision[i]), 4),
                        "conf": round(float(confs[order][i]), 4)} for i in idx],
        })
    return out


def confusion(records: Sequence[dict[str, Any]], names: Sequence[str],
              conf: float = CONF_OP, iou_thr: float = IOU_OP) -> dict[str, Any]:
    """混淆矩陣（含 background 一列一欄）。`matrix[pred][gt]`，最後一格是 background。

    漏檢（GT 沒被任何框吃到）記在 `matrix[background][gt]`，誤報記在 `matrix[pred][background]`。
    沒有這兩列的混淆矩陣會讓「什麼都沒偵測到」看起來像完美對角線。
    """
    n = len(names)
    mat = np.zeros((n + 1, n + 1), dtype=int)
    for rec in records:
        preds = sorted((p for p in rec.get("boxes", []) if float(p["conf"]) >= conf),
                       key=lambda p: -float(p["conf"]))
        gts = list(rec.get("gt_boxes", []))
        taken: set[int] = set()
        for p in preds:
            best, best_j = iou_thr, -1
            for j, g in enumerate(gts):
                if j in taken:
                    continue
                v = metrics.iou(p, g)
                if v >= best:
                    best, best_j = v, j
            if best_j >= 0:
                taken.add(best_j)
                mat[int(p["cls"]), int(gts[best_j]["cls"])] += 1
            else:
                mat[int(p["cls"]), n] += 1        # 誤報：預測到 background
        for j, g in enumerate(gts):
            if j not in taken:
                mat[n, int(g["cls"])] += 1        # 漏檢：background 吃掉一個 GT
    return {"labels": list(names) + ["background"], "matrix": mat.tolist(),
            "conf": conf, "iou": iou_thr, "axes": {"rows": "pred", "cols": "gt"}}


def build(*, result: dict[str, Any], records: Sequence[dict[str, Any]], names: Sequence[str],
          results_csv: Path | None, speed: dict[str, Any] | None,
          noise_floor: float | None = None, op_conf: float | None = None) -> dict[str, Any]:
    """`GET /eval/{run_id}/charts` 的回應本體（四張圖一次到位）。"""
    op = op_conf if op_conf is not None else metrics.operating_point(records, len(names))
    return {
        "op_conf": op,
        "curves": curves_from_csv(results_csv) if results_csv else [],
        "pr": pr_curves(result, names),
        "confusion": confusion(records, names, conf=op),
        "per_class": [{k: p[k] for k in ("cls", "name", "ap50", "ap", "recall", "precision",
                                         "recall_at_conf", "best_f1_conf", "support",
                                         "ci_lo", "ci_hi") if k in p}
                      for p in result["per_class"]],
        "speed": speed or {},
        "noise_floor": noise_floor,
        "map50": result["map50"], "map5095": result["map5095"],
        "ci": result.get("ci"),
    }


# ---------- 自檢：`uv run python -m src.eval.series` ----------

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="series-selfcheck-"))

    # 1. results.csv → curves：欄名換成前端的名字，數字不變
    csv_path = tmp / "results.csv"
    csv_path.write_text(
        "epoch,time,train/box_loss,train/cls_loss,train/dfl_loss,metrics/precision(B),"
        "metrics/recall(B),metrics/mAP50(B),metrics/mAP50-95(B),val/box_loss,val/cls_loss,val/dfl_loss\n"
        "1,9.6,1.9189,3.7488,1.7539,0.2,0.3,0.2135,0.1602,1.9,3.9,1.6\n"
        "2,11.2,1.5851,2.7039,1.5562,0.3,0.4,0.5033,0.1901,1.8,3.0,1.5\n", "utf-8")
    cur = curves_from_csv(csv_path)
    assert len(cur) == 2 and cur[0]["map5095"] == 0.1602 and cur[1]["train_box_loss"] == 1.5851, cur
    assert "metrics/mAP50(B)" not in cur[0], "ultralytics 的欄名漏到前端了"

    names = ["a", "b"]
    # 2. 混淆矩陣：一個正確、一個類別錯、一個漏檢、一個誤報 —— 四格都要落在對的位置
    records = [
        {"image_id": "i1",
         "boxes": [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2, "conf": .9}],
         "gt_boxes": [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2}]},
        {"image_id": "i2",
         "boxes": [{"cls": 1, "cx": .5, "cy": .5, "w": .2, "h": .2, "conf": .9}],
         "gt_boxes": [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2}]},
        {"image_id": "i3", "boxes": [],
         "gt_boxes": [{"cls": 1, "cx": .5, "cy": .5, "w": .2, "h": .2}]},
        {"image_id": "i4",
         "boxes": [{"cls": 0, "cx": .1, "cy": .1, "w": .1, "h": .1, "conf": .9}], "gt_boxes": []},
    ]
    cm = confusion(records, names)
    assert cm["labels"] == ["a", "b", "background"]
    assert cm["matrix"][0][0] == 1, cm["matrix"]    # 對
    assert cm["matrix"][1][0] == 1, cm["matrix"]    # 把 a 判成 b
    assert cm["matrix"][2][1] == 1, cm["matrix"]    # 漏檢（background 吃掉 b）
    assert cm["matrix"][0][2] == 1, cm["matrix"]    # 誤報
    # 低於作業點的框不准進矩陣（不然調低 conf 就能把漏檢變好看）
    shy = [{"image_id": "i5",
            "boxes": [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2, "conf": .1}],
            "gt_boxes": [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2}]}]
    assert confusion(shy, names, conf=0.3)["matrix"][2][0] == 1        # 低於作業點 → 算漏檢
    assert confusion(shy, names, conf=0.05)["matrix"][0][0] == 1       # 高於作業點 → 算命中
    # 作業點由資料決定：信心全低的模型不該得到一張全空的矩陣（實測 r30-t1 就是這一格）
    auto = build(result=metrics.evaluate(shy, names), records=shy, names=names,
                 results_csv=None, speed=None)
    assert auto["confusion"]["matrix"][0][0] == 1, auto["confusion"]["matrix"]
    assert auto["op_conf"] <= 0.1, auto["op_conf"]

    # 3. PR 曲線：沒有 GT 的類別不畫；precision 從高 conf 端開始
    res = metrics.evaluate(records, names)
    pr = pr_curves(res, names)
    assert {p["name"] for p in pr} == {"a", "b"}, pr
    a = next(p for p in pr if p["name"] == "a")
    assert a["points"] and a["points"][0]["p"] == 1.0, a["points"][:2]

    # 4. build()：四把圖 + speed + noise_floor 一次到位（前端一支 API 畫四張）
    out = build(result=metrics.with_ci(res, n_boot=50), records=records, names=names,
                results_csv=csv_path, speed={"ms_per_image": 4.2}, noise_floor=0.05)
    assert set(out) >= {"curves", "pr", "confusion", "per_class", "speed", "noise_floor", "ci"}
    assert out["per_class"][0]["ci_lo"] is not None
    print(f"eval.series selfcheck PASS（暫存目錄 {tmp}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
