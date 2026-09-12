"""指標核心 —— noise floor（本階段最重要的數字）、bootstrap CI、per-class AP / recall / support。

契約：`_Context/api-contract.md` §4（`eval.noise_floor` / `eval.perclass` 的 data 欄位）、
§10（`409 NOISE_FLOOR_MISSING`）。設計：`DESIGN.md` s08 與護欄 3。
擁有者：metric-auditor。

為什麼 noise floor 排在所有事情前面（護欄 3，逐字）：
「同 config × 3 seed 量出 σ，沒量出來，arbiter 的裁決與所有停止條件回 409。」
12 張 val / 13 個 GT 框的 mAP50-95 在 seed 之間就會跳好幾個百分點 —— 不先量，
之後每一輪的 Δ 都是在解讀雜訊，整條「自我進化」就是自欺。

⚠️ 本檔被 API process import：**不准 import torch / ultralytics**（那一側在 `infer.py`）。
只用 numpy + stdlib。

自檢：`uv run python -m src.eval.metrics`
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 子行程一律走 procs.run（登記進表 + 自成一組）：noise floor 這六顆（3 次訓練 + 3 次 anchor
# 推論）包在 `asyncio.to_thread` 裡，Stop 只有訊號打得到（契約 §8.4）。
from src.app import procs  # noqa: E402

DATASET_DIR = PROJECT_ROOT / "02-dataset"
SEALED_DIR = PROJECT_ROOT / "03-sealed-test"          # 🔒 封印：只有 /final-test 走得到這裡
EXPERIMENTS_DIR = PROJECT_ROOT / "04-experiments"
NOISE_FLOOR_DIR = EXPERIMENTS_DIR / "noise-floor"
CLASS_TABLE = PROJECT_ROOT / "_Context" / "class_table.json"
TRAIN_SCRIPT = PROJECT_ROOT / "scripts" / "train_yolo.py"

# COCO 慣例：AP = AP@[.50:.05:.95] 的平均，AP50 = 第一格。
IOU_THRESHOLDS: tuple[float, ...] = tuple(round(0.5 + 0.05 * i, 2) for i in range(10))
# `recall` 報的是**最佳 F1 作業點**上的值（ultralytics 的 R 欄同義），不是固定 conf 門檻。
# 踩過的坑：10 epoch 的教學 baseline 最高信心只有 0.2，用固定 0.25 量出來的 recall 全類別都是
# 0.0000 —— anchor gate 於是變成一個永遠讀 0 的死閘門，看起來「有 gate」其實什麼都沒守。
# `recall_at_conf` 仍然一起回，因為前端疊框用的就是那個作業點。
RECALL_CONF = 0.25
MIN_SUPPORT = 30        # 護欄 3：support < 30 的類別只能當提示，不得當停止/達標依據
N_BOOT = 1000
MIN_BOOT = 200       # 低於這個量，百分位撐不住（n_boot=1 會回寬度 0 的假 CI）
CI_DROP_MAX = 0.05   # 丟棄率上限：超過就不給 CI，只給一句「重抽無法給 CI」
NOISE_SEEDS: tuple[int, ...] = (42, 43, 44)   # 同 config × 3 seed（護欄 3）
# noise floor = 2σ。σ 用樣本標準差（n−1）—— 3 個 seed 要誠實承認自由度只有 2。
NOISE_K = 2.0

SPLIT_DIRS: dict[str, tuple[Path, Path]] = {
    "train": (DATASET_DIR / "images" / "train", DATASET_DIR / "labels" / "train"),
    "valid": (DATASET_DIR / "images" / "valid", DATASET_DIR / "labels" / "valid"),
    "anchor": (DATASET_DIR / "images" / "anchor", DATASET_DIR / "labels" / "anchor"),
    # test 不在這張表的公開路徑上：`split_dirs()` 會擋，只有 `sealed_dirs()` 拿得到。
}


def split_dirs(split: str) -> tuple[Path, Path]:
    """封印 test 的第一道鎖在資料層：這支函式**拿不到 sealed-test**，傳 test 直接 ValueError。"""
    if split == "test":
        raise ValueError("split=test 被封印（契約 §10 SPLIT_TEST_FORBIDDEN）；只有 /final-test 讀得到")
    if split not in SPLIT_DIRS:
        raise ValueError(f"split 只接受 {sorted(SPLIT_DIRS)}，收到 {split!r}")
    return SPLIT_DIRS[split]


def sealed_dirs() -> tuple[Path, Path]:
    """唯一通往 `03-sealed-test/` 的函式。呼叫它的只准有 `POST /final-test` 一處。"""
    return SEALED_DIR / "images", SEALED_DIR / "labels"


def dataset_fingerprint(splits: Sequence[str] = ("train", "valid")) -> str:
    """02-dataset 的內容指紋（檔名 + 標註內容）。

    為什麼需要它：`ds_version` 是**人填的字串**，重新 freeze 一次資料集它不會自己變 ——
    實測 2026-09-12 00:48 有人重跑 freeze，valid 的成員與框數都換了（13 → 15 個 GT 框），
    `ds_version` 卻還是 `v1`。指紋不會說謊：它一變，noise floor 與所有跨輪 Δ 立刻作廢。

    ponytail: 只雜湊標註不雜湊影像位元（60 張 PNG 每次 eval 都讀一遍太貴）。
    影像被換掉但標註一字不差的情況抓不到；真要抓就把 image 的 sha1 也塞進來。
    """
    h = hashlib.sha1()
    for split in splits:
        _, labels_dir = SPLIT_DIRS[split]
        for txt in sorted(labels_dir.glob("*.txt")):
            h.update(txt.name.encode("utf-8"))
            h.update(txt.read_bytes())
    return h.hexdigest()[:16]


def class_names() -> list[str]:
    table = json.loads(CLASS_TABLE.read_text("utf-8"))
    return list(table["names"])


def class_table_fits_labels(splits: Sequence[str] = ("train", "valid")) -> dict[str, Any]:
    """class 真相表的 `nc` 蓋不蓋得住 `02-dataset` 標籤裡真的出現的 class id。

    ⚠️ 這一條在 `class_table_version` 之外**另外**要查，因為版本字串會說謊：
    實測 2026-09-12 07:24 `_Context/class_table.json` 被後面一個 run 覆蓋成
    `nc=3 names=['random','center','donut']`，而 `version` 仍然是 `v1` ——
    02-dataset 的標籤用到 `cls 5`，於是 `series.confusion()` 直接
    `IndexError: index 5 is out of bounds`（HTTP 500，錯誤訊息指不到真因）。
    比 500 更糟的是沒有爆的那一種：nc 夠大但名字換過，per-class 指標會**靜默對錯表**。

    回 `{ok, nc, max_cls, names}`。標籤還沒落檔（`max_cls = -1`）就當通過（M1 的順序）。
    """
    names = class_names()
    max_cls = -1
    for split in splits:
        _, labels_dir = split_dirs(split)
        if not labels_dir.exists():
            continue
        for txt in labels_dir.glob("*.txt"):
            for line in txt.read_text("utf-8").splitlines():
                head = line.split()[:1]
                if head and head[0].lstrip("-").isdigit():
                    max_cls = max(max_cls, int(head[0]))
    return {"ok": max_cls < len(names), "nc": len(names), "max_cls": max_cls, "names": names}


def class_table_version() -> str | None:
    if not CLASS_TABLE.exists():
        return None
    return json.loads(CLASS_TABLE.read_text("utf-8")).get("version")


def image_id_of(name: str) -> str:
    """`wafer_0061.png` → `demo0061`（契約 §11 的 id 規則，前端疊圖靠這個對得上原圖）。"""
    stem = Path(name).stem
    tail = stem.rsplit("_", 1)[-1]
    if tail.isdigit():
        return f"demo{int(tail):04d}"
    # 非 demo 命名 = Roboflow 落檔。id 是 sha1(slug/相對路徑)，從檔名推不出來，
    # 要回頭查 index.json；查不到才退回 stem（本機檔／未來來源）。
    from src.autolabel import roboflow_src
    return roboflow_src.id_of_filename(name) or stem


def load_gt(labels_dir: Path) -> dict[str, list[dict[str, float]]]:
    """讀 YOLO txt 標註 → `{image_id: [{cls,cx,cy,w,h}]}`。空檔 = 有圖沒缺陷，不是缺資料。"""
    out: dict[str, list[dict[str, float]]] = {}
    for txt in sorted(labels_dir.glob("*.txt")):
        boxes = []
        for line in txt.read_text("utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 5:
                c, cx, cy, w, h = parts[:5]
                boxes.append({"cls": int(c), "cx": float(cx), "cy": float(cy),
                              "w": float(w), "h": float(h)})
        out[image_id_of(txt.name)] = boxes
    return out


# ---------- IoU 與逐圖配對 ----------

def iou(a: dict[str, float], b: dict[str, float]) -> float:
    """正規化 `cx,cy,w,h` 的 IoU（契約 §1：座標一律這個形狀）。"""
    ax1, ay1 = a["cx"] - a["w"] / 2, a["cy"] - a["h"] / 2
    ax2, ay2 = a["cx"] + a["w"] / 2, a["cy"] + a["h"] / 2
    bx1, by1 = b["cx"] - b["w"] / 2, b["cy"] - b["h"] / 2
    bx2, by2 = b["cx"] + b["w"] / 2, b["cy"] + b["h"] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def best_iou_per_gt(preds: Sequence[dict[str, Any]], gts: Sequence[dict[str, Any]],
                    conf: float = RECALL_CONF) -> float:
    """一張圖的定位品質：每個 GT 取「與任一預測框的最佳 IoU」再平均（不分類別）。

    前端測試牆用它排序「最爛的幾張」。沒有 GT 回 1.0（沒東西可以錯），有 GT 沒預測回 0.0。
    """
    kept = [p for p in preds if p.get("conf", 1.0) >= conf]
    if not gts:
        return 1.0
    return round(sum(max((iou(g, p) for p in kept), default=0.0) for g in gts) / len(gts), 4)


def match_class(records: Sequence[dict[str, Any]], cls: int) -> dict[str, Any]:
    """把「一個類別」的逐圖配對算一次，之後 bootstrap 只是重抽圖、不重配對。

    關鍵：貪婪配對是**逐圖內**做的（一個 GT 只能被一個預測吃掉），所以重抽圖不會改變
    任何一張圖內部的 tp/fp 標記。這就是 bootstrap 能重用這份結果的原因 ——
    每次重抽都重跑一次配對的話，1000 次 × 6 類 × 10 個 IoU 門檻會慢到沒人跑。
    """
    per_image: dict[str, dict[str, np.ndarray]] = {}
    n_gt: dict[str, int] = {}
    for rec in records:
        img = rec["image_id"]
        gts = [g for g in rec.get("gt_boxes", []) if int(g["cls"]) == cls]
        dets = sorted((d for d in rec.get("boxes", []) if int(d["cls"]) == cls),
                      key=lambda d: -float(d["conf"]))
        n_gt[img] = len(gts)
        tp = np.zeros((len(dets), len(IOU_THRESHOLDS)), dtype=bool)
        ious = np.array([[iou(d, g) for g in gts] for d in dets], dtype=float) if (dets and gts) \
            else np.zeros((len(dets), len(gts)))
        for ti, thr in enumerate(IOU_THRESHOLDS):
            taken = set()
            for di in range(len(dets)):          # dets 已按 conf 由高到低排好
                best, best_j = thr, -1
                for gj in range(len(gts)):
                    if gj in taken:
                        continue
                    if ious[di, gj] >= best:
                        best, best_j = ious[di, gj], gj
                if best_j >= 0:
                    taken.add(best_j)
                    tp[di, ti] = True
        per_image[img] = {"conf": np.array([float(d["conf"]) for d in dets]), "tp": tp}
    return {"per_image": per_image, "n_gt": n_gt}


def _ap_from_curve(tp_cum: np.ndarray, n_gt: int, n_det: int) -> np.ndarray:
    """all-point 內插 AP（每個 IoU 門檻一個值）。n_gt=0 回 nan —— 沒有 GT 的類別不是 0 分，是不可評。"""
    if n_gt == 0:
        return np.full(len(IOU_THRESHOLDS), np.nan)
    if n_det == 0:
        return np.zeros(len(IOU_THRESHOLDS))
    ranks = np.arange(1, n_det + 1)[:, None]
    recall = tp_cum / n_gt
    precision = tp_cum / ranks
    aps = []
    for ti in range(len(IOU_THRESHOLDS)):
        r = np.concatenate(([0.0], recall[:, ti], [recall[-1, ti]]))
        p = np.concatenate(([1.0], precision[:, ti], [0.0]))
        p = np.maximum.accumulate(p[::-1])[::-1]          # 單調包絡
        aps.append(float(np.sum(np.diff(r) * p[1:])))
    return np.array(aps)


def class_metrics(match: dict[str, Any], images: Sequence[str]) -> dict[str, float]:
    """對「給定的一組圖（可重複，bootstrap 就是這樣用）」算 ap50 / ap / recall / support。"""
    per_image, n_gt_map = match["per_image"], match["n_gt"]
    confs = np.concatenate([per_image[i]["conf"] for i in images if i in per_image] or [np.array([])])
    tps = [per_image[i]["tp"] for i in images if i in per_image]
    tp = np.concatenate(tps) if tps else np.zeros((0, len(IOU_THRESHOLDS)), dtype=bool)
    support = int(sum(n_gt_map.get(i, 0) for i in images))
    order = np.argsort(-confs) if confs.size else np.array([], dtype=int)
    tp_sorted = tp[order]
    tp_cum = np.cumsum(tp_sorted, axis=0)
    aps = _ap_from_curve(tp_cum, support, int(confs.size))
    if support == 0:   # 沒有 GT 的類別不可評（nan），不是 0 分
        return {"ap50": float("nan"), "ap": float("nan"), "recall": 0.0, "precision": 0.0,
                "recall_at_conf": 0.0, "best_f1_conf": None, "support": 0}
    if confs.size == 0:
        return {"ap50": float(aps[0]), "ap": float(np.nanmean(aps)), "recall": 0.0,
                "precision": 0.0, "recall_at_conf": 0.0, "best_f1_conf": None, "support": support}
    # IoU 0.5 的 PR 曲線 → 取最佳 F1 那一點當作業點（跟 ultralytics 的 P/R 欄同一種讀法）
    cum50 = tp_cum[:, 0]
    recall_c = cum50 / support
    precision_c = cum50 / np.arange(1, cum50.size + 1)
    f1 = np.divide(2 * precision_c * recall_c, precision_c + recall_c,
                   out=np.zeros_like(recall_c, dtype=float), where=(precision_c + recall_c) > 0)
    bi = int(np.argmax(f1))
    keep = confs[order] >= RECALL_CONF
    return {"ap50": float(aps[0]), "ap": float(np.nanmean(aps)),
            "recall": float(recall_c[bi]), "precision": float(precision_c[bi]),
            "best_f1_conf": float(confs[order][bi]),
            "recall_at_conf": float(tp_sorted[keep, 0].sum() / support) if keep.any() else 0.0,
            "support": support}


def operating_point(records: Sequence[dict[str, Any]], n_classes: int,
                    floor: float = 0.01) -> float:
    """全類別彙總 PR 曲線上**最佳 F1** 的那個 conf —— 混淆矩陣與疊框都用它，不用寫死的 0.25。

    寫死 0.25 的版本在 10 epoch 的教學 baseline 上實測：所有預測框信心都 < 0.2，
    混淆矩陣整張空白（只有 background 列有數字）、每張圖的 `iou_vs_gt` 全是 0.0。
    那不是「模型很爛」的資訊，那是**量錯了** —— 作業點要由資料決定。
    """
    entries: list[tuple[float, bool]] = []
    n_gt = 0
    for c in range(n_classes):
        m = match_class(records, c)
        n_gt += sum(m["n_gt"].values())
        for d in m["per_image"].values():
            entries.extend(zip((float(x) for x in d["conf"]), (bool(x) for x in d["tp"][:, 0])))
    if not entries or n_gt == 0:
        return floor
    entries.sort(key=lambda e: -e[0])
    tp = np.cumsum([e[1] for e in entries], dtype=float)
    ranks = np.arange(1, len(entries) + 1, dtype=float)
    precision, recall = tp / ranks, tp / n_gt
    f1 = np.divide(2 * precision * recall, precision + recall,
                   out=np.zeros_like(recall), where=(precision + recall) > 0)
    return max(floor, round(float(entries[int(np.argmax(f1))][0]), 5))


def annotate_iou(records: Sequence[dict[str, Any]], conf: float) -> None:
    """就地把每張圖的 `iou_vs_gt` 重算在指定作業點上（原地改，因為它本來就是衍生欄位）。"""
    for rec in records:
        rec["iou_vs_gt"] = best_iou_per_gt(rec.get("boxes", []), rec.get("gt_boxes", []), conf=conf)


def evaluate(records: Sequence[dict[str, Any]], names: Sequence[str]) -> dict[str, Any]:
    """整個 split 的 per-class 指標 + 整體 mAP。整體一律**只平均有 GT 的類別**。"""
    images = [r["image_id"] for r in records]
    matches = {c: match_class(records, c) for c in range(len(names))}
    per_class = []
    for c, name in enumerate(names):
        m = class_metrics(matches[c], images)
        per_class.append({"cls": c, "name": name,
                          **{k: (None if isinstance(v, float) and np.isnan(v) else
                                 round(v, 4) if isinstance(v, float) else v)
                             for k, v in m.items()}})
    present = [p for p in per_class if p["support"] > 0]
    return {
        "per_class": per_class,
        "map50": round(float(np.mean([p["ap50"] for p in present])), 4) if present else 0.0,
        "map5095": round(float(np.mean([p["ap"] for p in present])), 4) if present else 0.0,
        "n_images": len(records),
        "n_boxes": int(sum(p["support"] for p in per_class)),
        "_images": images,
        "_matches": matches,   # 給 bootstrap / PR 曲線重用，不外流到 JSON
    }


# ---------- bootstrap CI ----------

def bootstrap_ci(units: Sequence[Any], stat: Callable[[Sequence[Any]], float],
                 n_boot: int = N_BOOT, seed: int = 42,
                 alpha: float = 0.05) -> tuple[float, float, int]:
    """percentile bootstrap：對 `units` 重抽樣（有放回）算 `stat`，回 `(lo, hi, dropped)`。

    `units` 在這裡一律是**影像**，不是框 —— 同一張圖的框彼此不獨立（一張 donut 圖的 3 個框
    一起對或一起錯），對框重抽會把 CI 算得比真的窄，然後每一輪都宣告「顯著進步」。

    `dropped` = `stat` 回 nan 而被丟掉的 replicate 數。**一定要回報**：低 support 的類別
    （GT 全擠在 1 張圖上）有三分之一的重抽根本抽不到那張圖 → 那些 replicate 被丟掉，
    剩下的是「條件在該類至少出現一次」下的 CI，卻會以無條件 95% CI 的身分被畫成誤差線，
    看起來又窄又篤定。丟棄率由 `with_ci` 判，超過門檻就不給 CI。
    """
    if len(units) < 2:
        v = stat(units)
        return (v, v, 0)
    rng = random.Random(seed)
    n = len(units)
    vals = []
    dropped = 0
    for _ in range(n_boot):
        sample = [units[rng.randrange(n)] for _ in range(n)]
        v = stat(sample)
        if isinstance(v, float) and np.isnan(v):
            dropped += 1
        else:
            vals.append(v)
    if not vals:
        return (float("nan"), float("nan"), dropped)
    vals.sort()
    lo = vals[max(0, int(alpha / 2 * len(vals)) - 1)]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return (float(lo), float(hi), dropped)


def _ci_fields(lo: float, hi: float, dropped: int, n_boot: int) -> dict[str, Any]:
    """丟棄率過高或算出 nan → 不給 CI（None），只留一句話說明為什麼不給。

    一條「看起來很窄」的誤差線比沒有誤差線更糟：護欄 3 把 CI 寬度當進步門檻，
    假性變窄的 CI 會讓每一輪都宣告顯著。
    """
    bad = bool(np.isnan(lo) or np.isnan(hi)) or dropped > CI_DROP_MAX * n_boot
    if bad:
        return {"lo": None, "hi": None, "width": None, "dropped": dropped}
    return {"lo": round(lo, 4), "hi": round(hi, 4), "width": round(hi - lo, 4), "dropped": dropped}


def with_ci(result: dict[str, Any], n_boot: int = N_BOOT, seed: int = 42) -> dict[str, Any]:
    """把 `evaluate()` 的結果補上 per-class 與整體的 95% CI（前端誤差線與灰帶靠它）。"""
    images = result["_images"]
    matches = result["_matches"]
    for p in result["per_class"]:
        c = p["cls"]
        if p["support"] == 0:
            p["ci_lo"], p["ci_hi"], p["ci_dropped"] = None, None, None
            p["ci_note"] = "這一類在本 split 沒有 GT，不可評"
            continue
        lo, hi, dropped = bootstrap_ci(images, lambda s, c=c: class_metrics(matches[c], s)["ap50"],
                                       n_boot=n_boot, seed=seed)
        f = _ci_fields(lo, hi, dropped, n_boot)
        p["ci_lo"], p["ci_hi"], p["ci_dropped"] = f["lo"], f["hi"], dropped
        if f["lo"] is None:
            n_img = sum(1 for i in set(images) if matches[c]["n_gt"].get(i, 0) > 0)
            p["ci_note"] = (f"該類只落在 {n_img} 張圖上，{dropped}/{n_boot} 次重抽沒抽到它 → "
                            "重抽無法給 CI（給了也只是「條件在它出現過」下的 CI，會假性變窄）")
        else:
            p.pop("ci_note", None)

    def overall(sample: Sequence[str]) -> float:
        vals = [class_metrics(m, sample)["ap"] for m in matches.values()
                if sum(m["n_gt"].get(i, 0) for i in sample) > 0]
        return float(np.mean(vals)) if vals else float("nan")

    lo, hi, dropped = bootstrap_ci(images, overall, n_boot=n_boot, seed=seed)
    result["ci"] = {"metric": "map5095", **_ci_fields(lo, hi, dropped, n_boot),
                    "n_boot": n_boot, "unit": "image"}
    return result


def anchor_recall_of(records: Sequence[dict[str, Any]], names: Sequence[str]) -> float | None:
    """anchor split 的整體 recall = 有 GT 的類別的 recall 平均（只當 gate 不當 rank，護欄 2）。

    router 與 `measure_noise_floor()` **共用這一支** —— 兩邊各算一次就是兩把尺，
    雜訊帶與被它判的數字必須是同一種算法算出來的。
    """
    res = evaluate(records, names)
    present = [p for p in res["per_class"] if p["support"] > 0]
    return round(sum(p["recall"] for p in present) / len(present), 4) if present else None


# ---------- noise floor（同 config × 3 seed） ----------

def noise_floor_path(ds_version: str) -> Path:
    return NOISE_FLOOR_DIR / f"{ds_version}.json"


def read_noise_floor(ds_version: str) -> dict[str, Any] | None:
    path = noise_floor_path(ds_version)
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def noise_floor_complete(payload: dict[str, Any] | None) -> bool:
    """量完 = val 的帶**與** anchor recall 的帶都有。

    只有 val 那條的舊快取不算量完：anchor gate 沒有自己的帶就只能拒答，
    而「快取存在」這件事會讓兩個不同的地方（GET 端點與 measure_noise_floor）都直接回舊的。
    """
    return bool(payload) and payload.get("anchor_noise_floor") is not None


def sigma_of(values: Sequence[float]) -> tuple[float, float]:
    """(σ, noise_floor)。σ 是樣本標準差（n−1），noise floor = 2σ。"""
    if len(values) < 2:
        raise ValueError("noise floor 至少要 2 個 seed，1 個算不出離散度")
    sd = statistics.stdev(values)
    return round(sd, 5), round(NOISE_K * sd, 5)


def measure_noise_floor(*, ds_version: str, model: str = "yolov8n", epochs: int = 10,
                        seeds: Sequence[int] = NOISE_SEEDS, nf_run_id: str = "rnf",
                        force: bool = False) -> dict[str, Any]:
    """同 config × N 個 seed 各訓一次，量 val mAP50-95 的 σ。**這是本階段最重要的數字。**

    訓練走既有的 `scripts/train_yolo.py` 子行程（不是第二套邏輯）；API process 不 import torch。
    產物落在 `04-experiments/noise-floor/`，事件寫進 `runs/rnf/`（不污染任何真的 run 的事件流）。
    """
    cached = read_noise_floor(ds_version)
    # 舊的快取沒有 anchor 雜訊帶 → 不能直接回：anchor gate 沒有自己的帶就只能拒答。
    # 這條路徑不會重訓（result.json 還在就跳過訓練），只會補跑 3 次 anchor 推論。
    if noise_floor_complete(cached) and not force:
        return cached

    from src.train import select  # 延後 import：純資料模組，但仍留在需要時才拉進來

    NOISE_FLOOR_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for seed in seeds:
        train_id = f"nf-{ds_version}-{model}-s{seed}"
        project = NOISE_FLOOR_DIR / train_id
        recipe = select.build_recipe(model=model, run_id=nf_run_id, train_id=train_id,
                                     overrides={"seed": seed, "epochs": epochs})
        recipe["project"] = str(project)
        project.mkdir(parents=True, exist_ok=True)
        recipe_path = project / "recipe.json"
        recipe_path.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", "utf-8")
        result_path = project / "result.json"
        if force or not result_path.exists():
            procs.run([sys.executable, str(TRAIN_SCRIPT), "--recipe", str(recipe_path)],
                      cwd=str(PROJECT_ROOT), check=True)
        result = json.loads(result_path.read_text("utf-8"))
        last = result["epochs"][-1]
        # anchor gate 的門檻要用 **anchor recall 自己的 2σ**，不是 val mAP50-95 的 σ
        #（實測差 2.1 倍，拿錯的那條會把純 seed 抖動判成「pseudo-label 偏誤 → 立刻 abandoned」）。
        # 量它的資料本來就在手上：同樣這三顆 seed 的 best.pt，多跑一次 20 張 anchor 推論而已。
        anchor_json = project / "anchor.json"
        best = project / recipe.get("name", "yolo") / "weights" / "best.pt"
        if (force or not anchor_json.exists()) and best.exists():
            procs.run([sys.executable, "-m", "src.eval.infer", "--weights", str(best),
                       "--split", "anchor", "--out", str(anchor_json),
                       "--imgsz", str(recipe["params"]["imgsz"]),
                       "--device", str(recipe["params"]["device"])],
                      cwd=str(PROJECT_ROOT), check=True)
        a_recall = (anchor_recall_of(json.loads(anchor_json.read_text("utf-8"))["records"],
                                     class_names()) if anchor_json.exists() else None)
        runs.append({"seed": seed, "train_id": train_id,
                     "map5095": last["map5095"], "map50": last["map50"],
                     "anchor_recall": a_recall,
                     "elapsed_s": result["elapsed_s"],
                     "evidence": str(result_path.relative_to(PROJECT_ROOT))})

    values = [r["map5095"] for r in runs]
    sigma, floor = sigma_of(values)
    a_values = [r["anchor_recall"] for r in runs if r["anchor_recall"] is not None]
    a_sigma, a_floor = sigma_of(a_values) if len(a_values) >= 2 else (None, None)
    payload = {
        "ds_version": ds_version,
        "ds_fingerprint": dataset_fingerprint(),
        "metric": "map5095",
        "config": {"model": model, "epochs": epochs, "imgsz": select.BASELINE["imgsz"],
                   "device": select.BASELINE["device"], "batch": select.BASELINE["batch"]},
        "seeds": runs,
        "mean": round(float(np.mean(values)), 5),
        "min": min(values), "max": max(values),
        "sigma": sigma,
        "noise_floor": floor,
        "anchor_metric": "anchor_recall",
        "anchor_sigma": a_sigma,
        "anchor_noise_floor": a_floor,
        "k": NOISE_K,
        "measured_at": _now(),
        "note": f"Δ 的絕對值小於 {floor} 一律記「試過、沒用」，不准宣告進步（護欄 3）；"
                f"anchor gate 用自己的帶 {a_floor}（不是 {floor}）",
    }
    noise_floor_path(ds_version).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return payload


def _now() -> str:
    from src.app import bus

    return bus.utc_now_iso()


def significant(delta: float | None, noise_floor: float | None,
                ci_width: float | None = None) -> dict[str, Any]:
    """進步的門檻是 CI 寬度不是點估計（護欄 3）。兩道都要過才叫「可讀」。"""
    if delta is None or noise_floor is None:
        return {"significant": False, "why": "沒有可比的前一輪或還沒量 noise floor"}
    if abs(delta) < noise_floor:
        return {"significant": False,
                "why": f"|Δ| {abs(delta):.4f} < noise floor {noise_floor:.4f} → 雜訊，記「試過、沒用」"}
    if ci_width is not None and abs(delta) < ci_width / 2:
        return {"significant": False,
                "why": f"|Δ| {abs(delta):.4f} < 本輪 CI 半寬 {ci_width/2:.4f} → 點估計看得到、統計上看不到"}
    return {"significant": True,
            "why": f"|Δ| {abs(delta):.4f} ≥ noise floor {noise_floor:.4f}，方向 "
                   f"{'上升' if delta > 0 else '下降'}"}


# ---------- 自檢：`uv run python -m src.eval.metrics`（零 GPU、零 torch） ----------

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    # 1. IoU：同框 = 1、不相交 = 0、半重疊算得出正確值
    a = {"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}
    assert abs(iou(a, a) - 1.0) < 1e-9
    assert iou(a, {"cx": 0.9, "cy": 0.9, "w": 0.1, "h": 0.1}) == 0.0
    half = iou(a, {"cx": 0.6, "cy": 0.5, "w": 0.2, "h": 0.2})
    assert abs(half - 1 / 3) < 1e-9, half

    # 2. 完美預測 → AP50 = 1、recall = 1；全錯類別 → AP50 = 0 但 support 照算
    names = ["a", "b"]
    perfect = [{"image_id": f"i{i}",
                "boxes": [{"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2, "conf": 0.9}],
                "gt_boxes": [{"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}]}
               for i in range(10)]
    res = evaluate(perfect, names)
    got = res["per_class"][0]
    assert (got["ap50"], got["ap"], got["recall"], got["support"]) == (1.0, 1.0, 1.0, 10), got
    assert got["precision"] == 1.0 and got["best_f1_conf"] == 0.9, got
    assert res["per_class"][1]["support"] == 0 and res["map50"] == 1.0

    # 3. 重複框（同一個 GT 被預測兩次）必須只有一個算 TP —— 少了這條，把 conf 門檻調低就能「刷分」
    dup = [{"image_id": "i0",
            "boxes": [{"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2, "conf": 0.9},
                      {"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2, "conf": 0.8}],
            "gt_boxes": [{"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}]}]
    m = match_class(dup, 0)
    assert m["per_image"]["i0"]["tp"][:, 0].tolist() == [True, False], m["per_image"]["i0"]["tp"]

    # 3b. 模型信心整體偏低（10 epoch 的教學 baseline 就是這樣）時，recall 不准塌成 0 ——
    #     那會讓 anchor gate 變成永遠讀 0 的裝飾品（實測 r30-t1 全類別 recall 0.0000）
    shy = [{"image_id": f"i{i}",
            "boxes": [{"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2, "conf": 0.05}],
            "gt_boxes": [{"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}]}
           for i in range(10)]
    shy_m = class_metrics(match_class(shy, 0), [r["image_id"] for r in shy])
    assert shy_m["recall"] == 1.0, shy_m          # 最佳 F1 作業點上其實全中
    assert shy_m["recall_at_conf"] == 0.0, shy_m  # 固定 0.25 門檻下看起來是 0（兩個都要報）

    # 3c. 作業點由資料決定：信心全部偏低時 op 要跟著降下來（不然混淆矩陣整張空白）
    op = operating_point(shy, 2)
    assert op <= 0.05, f"作業點 {op} 沒有跟著模型的信心分布走"
    annotate_iou(shy, op)
    assert shy[0]["iou_vs_gt"] == 1.0, shy[0]

    # 4. bootstrap CI 的覆蓋率：已知分布下 95% CI 要涵蓋真值「約 95%」
    #    （這是整份 CI 邏輯唯一能被證偽的地方；寫錯成「對框重抽」或百分位取錯，覆蓋率會掉下來）
    rng = random.Random(0)
    true_mean, n, trials = 0.5, 40, 300
    covered = 0
    for t in range(trials):
        sample = [1.0 if rng.random() < true_mean else 0.0 for _ in range(n)]
        lo, hi, _ = bootstrap_ci(sample, lambda s: sum(s) / len(s), n_boot=300, seed=t)
        covered += lo <= true_mean <= hi
    coverage = covered / trials
    assert 0.88 <= coverage <= 0.99, f"bootstrap 95% CI 覆蓋率 {coverage:.3f} 不在 0.88..0.99"

    # 4b. 低 support 類別的 CI：GT 全擠在 1 張圖上時，三分之一的重抽根本抽不到那張圖 ——
    #     那些 replicate 被丟掉，剩下的不是無條件 95% CI。寧可不給線，也不給一條假性很窄的線。
    box = {"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}
    rare = [{"image_id": f"i{i}", "boxes": [], "gt_boxes": []} for i in range(9)]
    rare.append({"image_id": "i9", "boxes": [{**box, "conf": 0.9}], "gt_boxes": [box]})
    rare_ci = with_ci(evaluate(rare, ["a"]), n_boot=300)["per_class"][0]
    assert rare_ci["ci_lo"] is None and rare_ci["ci_hi"] is None, rare_ci
    assert rare_ci["ci_dropped"] > 0.05 * 300 and "重抽無法給 CI" in rare_ci["ci_note"], rare_ci
    # 對照組：每張圖都有這一類 → 重抽抽得到，CI 照給（不是一律不給）
    many = [{"image_id": f"i{i}", "boxes": [{**box, "conf": 0.9}], "gt_boxes": [box]}
            for i in range(10)]
    many_ci = with_ci(evaluate(many, ["a"]), n_boot=300)["per_class"][0]
    assert many_ci["ci_lo"] is not None and many_ci["ci_dropped"] == 0, many_ci

    # 5. σ 與 noise floor：2σ，而且 1 個 seed 一定要拒絕（不准用一次訓練假裝量過了）
    sd, floor = sigma_of([0.30, 0.32, 0.28])
    assert abs(sd - statistics.stdev([0.30, 0.32, 0.28])) < 1e-9 and abs(floor - 2 * sd) < 1e-9
    try:
        sigma_of([0.3])
        raise AssertionError("1 個 seed 也讓它算出 σ 了")
    except ValueError:
        pass

    # 6. 落在 noise floor 內的 Δ 一律不顯著（護欄 3 的那一條線）
    assert significant(0.01, 0.05)["significant"] is False
    assert significant(0.09, 0.05, ci_width=0.06)["significant"] is True
    assert significant(0.09, 0.05, ci_width=0.30)["significant"] is False  # CI 寬到看不出來

    # 7. 指紋對標註內容敏感（ds_version 這種人填的字串不會自己變，指紋會）
    import tempfile as _tf
    d = Path(_tf.mkdtemp(prefix="fp-selfcheck-")); (d / "a.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    global SPLIT_DIRS
    keep = SPLIT_DIRS
    SPLIT_DIRS = {"train": (d, d), "valid": (d, d)}
    before = dataset_fingerprint()
    (d / "a.txt").write_text("1 0.5 0.5 0.2 0.2\n")     # 只改類別編號
    assert dataset_fingerprint() != before, "改了標註內容指紋卻沒變 —— 那它擋不住任何東西"
    SPLIT_DIRS = keep

    # 8. 封印 test：資料層就拿不到（不是靠 router 自律）
    try:
        split_dirs("test")
        raise AssertionError("split_dirs('test') 沒有被擋下來")
    except ValueError:
        pass

    # 9. 端點層也要真的 400（資料層擋得住不代表 router 沒有另開一條路進去）
    from fastapi.testclient import TestClient

    from src.app.main import app

    # 不用 `with`：進 context manager 會跑 lifespan，而 lifespan 會動別人的 run 狀態
    c = TestClient(app, base_url="http://127.0.0.1")   # testserver 不在 Host 白名單內
    r = c.post("/api/v1/eval", json={"run_id": "r1", "split": "test"})
    assert r.status_code == 400 and r.json()["code"] == "SPLIT_TEST_FORBIDDEN", (r.status_code, r.text)

    print(f"eval.metrics selfcheck PASS（bootstrap 覆蓋率 {coverage:.3f}、"
          f"/eval split=test → {r.status_code} {r.json()['code']}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
