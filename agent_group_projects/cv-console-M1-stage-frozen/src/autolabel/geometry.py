"""第一階段 auto-label：**無類別 bbox**（純幾何、零預訓練權重、零類別）。

DESIGN「資料真相與零標註專家」規格 (1) 第一階段寫死的流程：
    wafer map → 缺陷 die 二值遮罩 → `scipy.ndimage.label` 連通分量 → 形態學去雜點 → 外接框

契約：`_Context/api-contract.md` §8.6（M1 寫死 `cls=-1`、`rule="cc_bbox"`、
`conf` = 分量面積佔最大分量面積的比例、< 0.25 發 `label.lowconf`）。
擁有者：dataset-truth。

**這裡一個類別都沒有**。類別要等 M2 的 KMeans + 一次 LLM 命名才出現；
在 M1 填任何類別名就是偷看合成 GT 的答案，直接違反零人工標註。

`_Context/wafer-basics.md` 那 6 條精修規則**不在這裡**：它們要等 M2 的群命名之後才有指派對象
（「被命名為 donut 的那一群改取 30–80% 環的外接框」），在 M1 寫下去就是 5 條永遠跑不到的死碼
＋ 一個只有一個實作的 registry。規格與係數留在 `_Context/dataset-notes.md` 的表裡，
M2 連同「群 → 規則」的綁定一起實作，那時每一條才寫得出真的會失敗的檢查。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

Box = tuple[int, int, int, int]  # (x0, y0, x1, y1) 像素，右下開區間


@dataclass(frozen=True)
class RefineParams:
    """6 條精修規則與去雜點的全部旋鈕。M2 起 `refine_params` 可被 patch（一次只准改一個鍵）。"""

    redness: int = 40          # 缺陷判定：R - B 超過多少算缺陷 die
    inside_lum: int = 137      # 晶圓內判定：R+G+B 超過多少算在晶圓上（BG 108 / 良品 166）
    open_size: int = 3         # 形態學開運算的結構元素邊長（去 1px 毛邊與對角細橋）
    min_area_px: int = 64      # 去雜點主力：小於 4 顆 die 的分量丟掉（單顆壞 die = 16 px）
    lowconf: float = 0.25      # conf 低於此值額外發 label.lowconf（契約 §8.6）
    # M2 起會多出精修規則的係數（ring_lo / edge_frac / pca_pad …）—— 規則進來的那天一起加，
    # 現在放著就是 4 個沒有人讀的旋鈕，OFAT 改了也不會有任何效果。

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_PARAMS = RefineParams()


# ---------- 遮罩 ----------

def inside_mask(rgb: np.ndarray, p: RefineParams = DEFAULT_PARAMS) -> np.ndarray:
    """晶圓內的像素（亮度高於 `inside_lum`）。缺陷只能長在晶圓上，背景的雜點不算。

    M2 的環帶／靠邊規則還會需要圓心與半徑，那時再從這個遮罩推（質心 + 最大半徑，兩行）。
    """
    return rgb.astype(np.int32).sum(2) > p.inside_lum


def defect_mask(rgb: np.ndarray, p: RefineParams = DEFAULT_PARAMS) -> np.ndarray:
    """缺陷 die 二值遮罩：coral 的 die 紅通道明顯高於藍通道，良品 navy 反過來。

    這是唯一「讀圖」的地方 —— 讀的是像素，不是任何人標的答案。
    """
    a = rgb.astype(np.int16)
    return (a[..., 0] - a[..., 2]) > p.redness


def despeckle(mask: np.ndarray, p: RefineParams = DEFAULT_PARAMS) -> np.ndarray:
    """形態學去雜點：開運算修毛邊 → 面積門檻丟掉單點壞 die。

    實話：4 px 的 die 尺度下，**真正在做事的是面積門檻**（開運算對 4×4 實心方塊無效），
    開運算負責的是抗鋸齒毛邊與對角細橋。兩個都留著，因為 M2 換成真資料時像素更碎。
    """
    se = np.ones((p.open_size, p.open_size), bool)
    opened = ndimage.binary_opening(mask, se)
    labels, n = ndimage.label(opened, structure=np.ones((3, 3), int))
    if n == 0:
        return opened
    areas = np.bincount(labels.ravel())
    keep = np.zeros(areas.shape, bool)
    keep[1:] = areas[1:] >= p.min_area_px
    return keep[labels]


# ---------- 外接框 ----------

def _bbox(mask: np.ndarray) -> Box | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


M1_RULE = "cc_bbox"  # 契約 §8.6 寫死：每個框的 `rule` 欄位，M1 只有這一個值


# ---------- 主流程 ----------

def norm_box(box: Box, shape: tuple[int, int]) -> dict[str, float]:
    h, w = shape
    x0, y0, x1, y1 = box
    return {
        "cx": round((x0 + x1) / 2 / w, 6),
        "cy": round((y0 + y1) / 2 / h, 6),
        "w": round((x1 - x0) / w, 6),
        "h": round((y1 - y0) / h, 6),
    }


def label_array(rgb: np.ndarray, p: RefineParams = DEFAULT_PARAMS) -> dict[str, Any]:
    """一張圖 → `{defect_ratio, n_components, boxes:[{cx,cy,w,h,conf,rule}]}`。

    `conf` = 該分量面積 / 最大分量面積（契約 §8.6 寫死的定義）。
    """
    inside = inside_mask(rgb, p)
    mask = despeckle(defect_mask(rgb, p) & inside, p)
    labels, n = ndimage.label(mask, structure=np.ones((3, 3), int))
    areas = np.bincount(labels.ravel(), minlength=n + 1)
    areas[0] = 0
    top = int(areas.max()) if n else 0

    boxes = []
    for idx in range(1, n + 1):
        box = _bbox(labels == idx)
        if box is None:
            continue
        boxes.append(
            {
                "cls": -1,  # 契約 §8.6：M1 沒有 class 真相表，填任何類別名就是偷看答案
                **norm_box(box, rgb.shape[:2]),
                "conf": round(float(areas[idx]) / top, 4) if top else 0.0,
                "rule": M1_RULE,
            }
        )
    inside_px = int(inside.sum()) or rgb.shape[0] * rgb.shape[1]
    return {
        "defect_ratio": round(float(mask.sum()) / inside_px, 6),
        "n_components": len(boxes),
        "boxes": boxes,
    }


def label_image(path: Path, p: RefineParams = DEFAULT_PARAMS) -> dict[str, Any]:
    with Image.open(path) as im:
        return label_array(np.asarray(im.convert("RGB")), p)


def iou(a: dict[str, float], b: dict[str, float]) -> float:
    """兩個正規化 `cx,cy,w,h` 框的 IoU。"""
    ax0, ax1 = a["cx"] - a["w"] / 2, a["cx"] + a["w"] / 2
    ay0, ay1 = a["cy"] - a["h"] / 2, a["cy"] + a["h"] / 2
    bx0, bx1 = b["cx"] - b["w"] / 2, b["cx"] + b["w"] / 2
    by0, by1 = b["cy"] - b["h"] / 2, b["cy"] + b["h"] / 2
    iw, ih = max(0.0, min(ax1, bx1) - max(ax0, bx0)), max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def best_ious(pred: list[dict[str, float]], gt: list[dict[str, float]]) -> list[float]:
    """每個 GT 框取最佳 IoU（漏掉的 GT 記 0）—— 刻意用嚴格的算法，不挑對自己有利的配對。"""
    return [max((iou(p_, g) for p_ in pred), default=0.0) for g in gt]


def iou_hist(values: list[float], bins: int = 10) -> list[int]:
    """0.0–1.0 均分 10 格的直方圖（前端畫成天花板證據那張圖）。"""
    out = [0] * bins
    for v in values:
        out[min(int(v * bins), bins - 1)] += 1
    return out


# ---------- 自檢：`uv run python -m src.autolabel.geometry` ----------
# 這支同時是 M1 驗收第三條 assert 的量測工具：auto-bbox 對合成 GT 的 IoU 中位數 >= 0.6。

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import statistics
    import time

    from . import demo

    t0 = time.time()
    gt = demo.ensure()
    ious: list[float] = []
    per_shape: dict[str, list[float]] = {}
    boxes_total = 0
    for rec in gt["images"]:
        out = label_image(demo.DEMO_DIR / rec["name"])
        boxes_total += len(out["boxes"])
        vals = best_ious(out["boxes"], rec["boxes"])
        ious += vals
        per_shape.setdefault(rec["shape"], []).extend(vals)

    med = statistics.median(ious)
    print(f"圖 {len(gt['images'])} 張 · GT 框 {len(ious)} 個 · auto 框 {boxes_total} 個")
    print(f"IoU 中位數 {med:.4f} · 平均 {statistics.fmean(ious):.4f} · 最小 {min(ious):.4f}")
    for shape, vals in sorted(per_shape.items()):
        print(f"  {shape:<10} n={len(vals):<4} 中位數 {statistics.median(vals):.4f} 最小 {min(vals):.4f}")
    print(f"直方圖(0→1, 10 格) {iou_hist(ious)}")

    # 1. M1 驗收第三條：IoU 中位數 >= 0.6（合成 GT 的尺，契約 §11）
    assert med >= 0.6, f"IoU 中位數 {med:.4f} < 0.6 —— 調 RefineParams 直到過"
    # 2. 去雜點真的有效：auto 框數不該被幾十顆單點雜訊灌爆
    assert boxes_total <= len(ious) * 1.2, f"auto 框 {boxes_total} 個遠多於 GT，去雜點沒生效"
    print(f"geometry selfcheck PASS（{time.time() - t0:.1f} 秒）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
