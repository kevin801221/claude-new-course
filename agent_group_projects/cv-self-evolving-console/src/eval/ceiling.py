"""標註天花板（label ceiling）—— auto-bbox 對**人工** GT 的 IoU 分佈。

契約：`_Context/api-contract.md` §11「兩把不同的尺」（合成 0.6 / Roboflow 人工 0.4）、
      §4 `label.anchor_iou`（s03 由 dataset-truth 發，本檔**只讀不寫**：`label.*` 前綴不是
      metric-auditor 的發言權，`bus.append_event` 會擋下來）。
設計：`DESIGN.md`「anchor_iou_median = 整條流水線的天花板證據」與 `label_ceiling` 放棄條件。
擁有者：metric-auditor。

## 為什麼要獨立一支檔案（而不是直接信 s03 那個數字）

合成資料（`source="demo"`）的 GT 與 auto-bbox **是同一套連通分量定義生出來的**，
所以 IoU 必然 1.0000 —— 實測 r81：155 個框全部落在最後一格 `[0,0,0,0,0,0,0,0,0,155]`。
那個 1.0 只抓得到「抽取器整個壞掉」，抓不到任何標註品質，**它是退化值不是天花板**。
真實人工 GT（Roboflow WM-811K v3）上同一套幾何實測中位數 **0.6381**（pool 289 張）——
這才是 DESIGN 講的那個天花板：**模型再好也不會超過它**。

兩者同一個欄位、同一張直方圖、同一句「PASS」，但可信度差一個等級。本檔的工作就是
**把可信度標出來，並且擋掉跨來源的比較**（跟 `class_table_version` 同等級的鎖）。

## 退化偵測有兩條腿（任一條成立就是退化）

1. **出處**：`source == "synthetic"` —— 合成資料的結構事實，不必量。
2. **實測**：`frac(IoU >= 0.999) >= 0.98` —— 抓「拿人工框自己量自己」那面鏡子
   （`labels:"human"` 那條路如果有人把 pred 直接餵成 GT，出處欄位會照樣寫 roboflow_anchor，
   只有這條腿抓得到）。

⚠️ 不准 import torch / ultralytics（本檔會被 API process import）。
⚠️ `sealed_test` 那 80 張人工框是考卷答案：`measure(scope="sealed_test")` 直接 `ValueError`，
   不靠自律（比照 `metrics.split_dirs("test")`）。

自檢：`uv run python -m src.eval.ceiling`
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.autolabel import freeze, geometry, roboflow_src  # noqa: E402
from src.eval import metrics  # noqa: E402

# 契約 §11 的兩把尺（值跟著 `label.anchor_iou.threshold` 走，這裡只是沒帶時的退路）
SYNTHETIC = "synthetic"
ROBOFLOW_ANCHOR = "roboflow_anchor"
THRESHOLD = {SYNTHETIC: 0.6, ROBOFLOW_ANCHOR: 0.4}

DEGENERATE_IOU = 0.999   # 這條線以上算「同一個框」
DEGENERATE_FRAC = 0.98   # 98% 的框都貼在 1.0 → 量的是鏡子不是尺
COCO_THRESHOLDS = [round(0.5 + 0.05 * i, 2) for i in range(10)]

DATASETS_DIR = PROJECT_ROOT / "01-raw-data" / "datasets"
CEILING_DIR = PROJECT_ROOT / "04-experiments" / "ceiling"
SCOPES = ("anchor", "pool")   # 🔒 `sealed_test` 不在這裡，而且永遠不會被加進來


# ---------- 統計 ----------

def _median(per_image: Sequence[Sequence[float]]) -> float:
    flat = [v for unit in per_image for v in unit]
    return statistics.median(flat) if flat else float("nan")


def summarize(per_image: Sequence[Sequence[float]], *, source: str, scope: str,
              threshold: float | None = None, n_boot: int = metrics.N_BOOT,
              seed: int = 42) -> dict[str, Any]:
    """IoU（**逐 GT 框**，漏掉的 GT 記 0）→ 天花板摘要。

    `per_image` 是「每張影像一串 IoU」而不是攤平的一串：bootstrap 的重抽單位必須是影像
    （eval-notes §5），同一張圖的框一起對或一起錯，對框重抽會把 CI 算窄。
    """
    flat = [float(v) for unit in per_image for v in unit]
    if not flat:
        raise ValueError("一個 IoU 都沒有 —— 沒有人工 GT 就沒有天花板可言，不要回 0.0 當數字")
    thr = float(THRESHOLD.get(source, 0.4) if threshold is None else threshold)
    flat_sorted = sorted(flat)
    n = len(flat_sorted)
    med = float(statistics.median(flat_sorted))
    frac_top = sum(1 for v in flat_sorted if v >= DEGENERATE_IOU) / n
    mirror = frac_top >= DEGENERATE_FRAC
    degenerate = source == SYNTHETIC or mirror

    lo, hi, dropped = metrics.bootstrap_ci(list(per_image), _median, n_boot=n_boot, seed=seed)
    ci = metrics._ci_fields(lo, hi, dropped, n_boot)

    # 「就算模型完美複製 auto-label，最多也只能拿到多少」—— 天花板的 mAP 版本。
    # COCO 的 AP 是十個 IoU 門檻的平均，所以天花板也要照十個門檻平均，不能只看 0.5。
    map50_ceiling = sum(1 for v in flat_sorted if v >= 0.5) / n
    map_ceiling = sum(sum(1 for v in flat_sorted if v >= t) / n for t in COCO_THRESHOLDS) \
        / len(COCO_THRESHOLDS)

    why = ("合成 GT 與 auto-bbox 同一套連通分量定義 → IoU 必然 1.0" if source == SYNTHETIC
           else f"{frac_top*100:.0f}% 的框 IoU >= {DEGENERATE_IOU} —— 量的是鏡子不是尺"
           if mirror else "")
    return {
        "source": source,
        "scope": scope,
        "credible": not degenerate,
        "degenerate": degenerate,
        "degenerate_why": why,
        "n_images": len(per_image),
        "n_boxes": n,
        "iou_median": round(med, 4),
        "iou_mean": round(statistics.fmean(flat_sorted), 4),
        "iou_min": round(flat_sorted[0], 4),
        "iou_p10": round(flat_sorted[max(0, int(0.1 * n) - 1)], 4),
        "iou_hist": geometry.iou_hist(flat_sorted),
        "ci": ci,
        "frac_below_threshold": round(sum(1 for v in flat_sorted if v < thr) / n, 4),
        "frac_ge_999": round(frac_top, 4),
        "map50_ceiling": round(map50_ceiling, 4),
        "map5095_ceiling": round(map_ceiling, 4),
        "threshold": thr,
        "verdict": "pass" if med >= thr else "fail",
        "note": _note(source, scope, med, thr, degenerate, map_ceiling),
        "params": geometry.RefineParams().to_dict(),
        "measured_at": metrics._now(),
    }


def _note(source: str, scope: str, med: float, thr: float,
          degenerate: bool, map_ceiling: float) -> str:
    if degenerate:
        return (f"⚠️ 退化值（{source}）：中位數 {med:.4f} 不是天花板證據，"
                "只代表「抽取器沒有整個壞掉」。真天花板要人工 GT 才量得到")
    if med < thr:
        return (f"天花板 {med:.4f} < 門檻 {thr}：**問題在標註不在模型** —— "
                "停在 s03，先修 auto-label 規則或補人工框，不要再訓練")
    return (f"天花板 {med:.4f}（{scope} 人工 GT，門檻 {thr}）：就算模型完美複製 auto-label，"
            f"mAP50-95 也不會超過 ~{map_ceiling:.2f}")


# ---------- 真實量測（Roboflow 人工 GT vs 連通分量 auto-bbox） ----------

def _manifest(ds_id: str) -> dict[str, Any] | None:
    path = DATASETS_DIR / ds_id / "manifest.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def _index_of(*, ds_id: str | None = None, slug: str | None = None) -> dict[str, Any]:
    """ds_id 或 slug → Roboflow `index.json`（那份資料集的唯一真相）。"""
    if slug is None:
        man = _manifest(ds_id or "")
        if man is None:
            raise ValueError(f"找不到 dataset {ds_id} 的 manifest（還沒 ingest？）")
        if man.get("source") != "roboflow":
            raise ValueError(f"dataset {ds_id} 的 source 是 {man.get('source')!r} "
                             "—— 只有 roboflow 有人工 GT 可以當尺")
        slug = (man.get("roboflow") or {}).get("slug")
    index = roboflow_src.load_index(roboflow_src.RAW_DIR / str(slug))
    if index is None:
        raise ValueError(f"01-raw-data/roboflow/{slug} 沒有 index.json —— 資料集還沒抓下來")
    return index


def _fingerprint(ids: Sequence[str]) -> str:
    """被量的那一批 id 的指紋 —— 快取鍵的一部分。

    只用 `<slug>__<scope>` 當鍵的話，limit=24 的 run 會直接讀到 limit=409 那次的快取，
    而那份量的是完全不同的 289 張（實測重疊 0 張）。
    """
    return hashlib.sha1("\n".join(ids).encode()).hexdigest()[:12]


def _cache_path(slug: str, scope: str, fp: str) -> Path:
    return CEILING_DIR / f"{slug}__{scope}__{fp}.json"


def measure(*, ds_id: str | None = None, slug: str | None = None, scope: str = "pool",
            n_boot: int = metrics.N_BOOT, force: bool = False,
            use_cache: bool = True) -> dict[str, Any]:
    """對 Roboflow 的同一批影像同時跑「人工 GT」與「連通分量 auto-bbox」，算真實 IoU 分佈。

    `scope`：
      `pool`   —— 進 train+valid 的那 289 張。**這是天花板該用的那批**：它們的 auto 框就是
                  訓練標籤，模型學的上限由它們決定。
      `anchor` —— s03 量漂移用的那 40 張（重現 `label.anchor_iou` 的數字）。
    🔒 `sealed_test` 不接受：那 80 張人工框是考卷答案，在 s03/s08 拿來當尺就是每輪偷看一次。

    快取在 `04-experiments/ceiling/<slug>__<scope>.json`（天花板是資料集的性質，不是 run 的，
    每輪重量 4 秒純浪費）；`RefineParams` 變了就自動重量 —— 幾何參數換了，舊的數字量的不是
    同一條 auto-label 規則。
    """
    if scope not in SCOPES:
        raise ValueError(f"scope 只接受 {list(SCOPES)}，收到 {scope!r}"
                         "（sealed_test 的人工框是考卷答案，任何 scope 都拿不到它）")
    index = _index_of(ds_id=ds_id, slug=slug)
    slug_ = index["slug"]
    # 🔒 量的必須是**這一輪真的 ingest 到的那批**，不是整份 index。
    # 原本一律拿 index 的 409 張再切 pool（289 張）：實測 r108 的 run 只有 24 張、
    # train+valid 12 張，與被量的 289 張**重疊 0 張**，而 `ck.label_ceiling` 會把那個數字
    # 寫成「這一輪就算模型完美複製 auto-label 也只到 0.3399」掛在它的 verdict 上。
    # 前端 limit 預設 120，所以從 UI 按 roboflow 的預設路徑同樣是 0 重疊。
    man = _manifest(ds_id) if ds_id else None
    ids = [r["id"] for r in man["images"]] if man else [r["id"] for r in index["images"]]
    fp = _fingerprint(ids)
    cache = _cache_path(slug_, scope, fp)
    params = geometry.RefineParams().to_dict()
    if use_cache and not force and cache.exists():
        cached = json.loads(cache.read_text("utf-8"))
        # 快取要連 `n_boot` 一起比：CI 的寬度是 n_boot 的函數，拿 200 次重抽的 CI
        # 回答一個問 1000 次的請求，等於悄悄換掉誤差線的意義。
        if (cached.get("params") == params and cached.get("n_boxes")
                and cached.get("n_boot") == n_boot):
            cached["cached"] = True
            return cached

    human = roboflow_src.boxes_of(index)
    per_image: list[list[float]] = []
    picked = freeze.gt_partition(ids)[scope]
    for image_id in picked:
        gt = human.get(image_id) or []
        path = roboflow_src.path_of(image_id)
        if not gt or path is None:
            continue
        pred = geometry.label_image(path)["boxes"]
        per_image.append(geometry.best_ious(pred, gt))

    try:
        out = summarize(per_image, source=ROBOFLOW_ANCHOR, scope=scope, n_boot=n_boot)
    except ValueError as exc:   # 子集太小 / 這一段沒有人工框 → 照既有規矩拒答，不拿別批的數字補
        raise ValueError(
            f"{exc}（ds={ds_id or '—'}：這一輪 {len(ids)} 張，{scope} 切出 {len(picked)} 張，"
            f"其中有人工框的 {len(per_image)} 張）"
        ) from exc
    out.update({"slug": slug_, "names": index.get("names"), "n_boot": n_boot, "cached": False,
                # 量的是哪一批要**寫在回應裡**：掛在 run 名下的天花板必須看得出用了幾張
                "ds_id": ds_id, "n_ids": len(ids), "ids_fingerprint": fp})
    if use_cache:   # 自檢用 `use_cache=False` 跑，才不會拿 200 次重抽的 CI 蓋掉正式的那份
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return out


# ---------- s03 那一筆事件（run 自己的紀錄，只讀） ----------

def from_event(run_id: str) -> dict[str, Any] | None:
    """讀 run 的 `label.anchor_iou`（最後一筆）。沒有就回 `None` —— 不要猜一個。

    直接掃檔而不是 `bus.read_events()`：整條龍 548 筆事件裡只要這一筆，
    先用字串比對過濾再 parse。
    """
    path = PROJECT_ROOT / "runs" / run_id / "events.jsonl"
    if not path.exists():
        return None
    found = None
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if '"label.anchor_iou"' in line:
                found = json.loads(line).get("data")
    return found


def source_of_run(run_id: str) -> str | None:
    """這個 run 的天花板是拿哪把尺量的（`synthetic` / `roboflow_anchor`）。

    ⚠️ 不要改用 `state.json` 的 `source`：實測 r90/r97/r98 從 `POST /datasets/ingest` 進
    roboflow 資料，`state.source` 仍然是建 run 時寫的 `"demo"`。事件裡的 `source` 才是
    「s03 真的拿哪把尺量的」。
    """
    ev = from_event(run_id)
    return None if ev is None else ev.get("source")


def _from_event(ev: dict[str, Any]) -> dict[str, Any]:
    """`label.anchor_iou` 的 data → 天花板摘要的形狀（沒有 CI，因為事件裡沒有逐框 IoU）。"""
    source = ev.get("source") or SYNTHETIC
    n = int(ev.get("n") or 0)
    hist = list(ev.get("iou_hist") or [])
    med = float(ev.get("iou_median") or 0.0)
    thr = float(ev.get("threshold") or THRESHOLD.get(source, 0.4))
    # 事件裡只有 10 格直方圖，量不到 `>= 0.999` 的比例：最後一格吃掉全部 + 中位數貼在 1.0
    # 就是同一個訊號（實測 r81 `[0,…,0,155]` / 中位數 1.0）。
    mirror = bool(n) and hist[-1:] == [n] and med >= DEGENERATE_IOU
    degenerate = source == SYNTHETIC or mirror
    return {
        "source": source, "scope": "anchor", "credible": not degenerate,
        "degenerate": degenerate,
        "degenerate_why": ("合成 GT 與 auto-bbox 同一套連通分量定義 → IoU 必然 1.0"
                           if source == SYNTHETIC else
                           "整個直方圖擠在最後一格且中位數貼在 1.0 —— 量的是鏡子不是尺"
                           if mirror else ""),
        "n_images": None, "n_boxes": n, "iou_median": round(med, 4), "iou_hist": hist,
        "ci": None, "threshold": thr, "verdict": ev.get("verdict"),
        "note": _note(source, "anchor", med, thr, degenerate, 0.0),
        "from_event": True,
    }


def for_run(run_id: str, *, ds_id: str | None = None, measure_real: bool = True,
            n_boot: int = metrics.N_BOOT) -> dict[str, Any] | None:
    """這個 run 的天花板（`POST /eval` 與 `GET /eval/{run}/verdict` 用的那一份）。

    合成那條路**不重量**：退化是結構事實，重量一次只會再拿到 1.0000。
    真人工 GT 那條路量 `pool`（進 train+valid 的那批，天花板該用的就是它們），
    同時把 s03 的 anchor 數字留在 `anchor` 欄裡 —— 兩個 scope 差多少本身就是證據
    （實測 anchor 0.8258 vs pool 0.6381，CI 不重疊：anchor 那 40 張的缺陷面積偏大）。
    """
    ev = from_event(run_id)
    if ev is None:
        return None
    base = _from_event(ev)
    if base["source"] == SYNTHETIC or not measure_real:
        return base
    try:
        real = measure(ds_id=ds_id, scope="pool", n_boot=n_boot)
    except (ValueError, OSError) as exc:      # 圖被砍了 / manifest 不見了 → 退回事件那筆
        base["measure_error"] = str(exc)
        return base
    anchor_med = base["iou_median"]
    real["anchor"] = {"iou_median": anchor_med, "n_boxes": base["n_boxes"],
                      "iou_hist": base["iou_hist"], "threshold": base["threshold"]}
    width = (real["ci"] or {}).get("width")
    if width and abs(anchor_med - real["iou_median"]) > width:
        real["sampling_note"] = (
            f"s03 的 anchor 切片量到 {anchor_med:.4f}，pool 量到 {real['iou_median']:.4f}"
            f"（差 {anchor_med - real['iou_median']:+.4f} > CI 寬度 {width:.4f}）——"
            "anchor 是照 id 順序切的前 40 張，不是隨機樣本；用它當『整個資料集的天花板』會樂觀"
        )
    return real


# ---------- s03 放棄閘門（DESIGN 的 `label_ceiling`） ----------

def gate(payload: dict[str, Any] | None) -> dict[str, Any]:
    """s03 之後問一次：這一輪該不該停在這裡？

    回 `{abandon, reason, credible, detail}`。`abandon=True` 的處方是**停在 s03**，
    不是照樣訓練然後在 s08 說「數字不好」—— 標註是天花板，模型再好也頂不破它。

    退化值（合成）**不會 abandon**：它不是「天花板很低」，是「沒量到天花板」。
    把它判成 abandon 會讓 demo 那條教學路徑整條停掉。
    """
    if payload is None:
        return {"abandon": False, "reason": None, "credible": False,
                "detail": "s03 沒有 label.anchor_iou 事件 —— 天花板沒量過，不知道不等於很糟"}
    med, thr = payload.get("iou_median"), payload.get("threshold")
    if payload.get("degenerate"):
        return {"abandon": False, "reason": None, "credible": False,
                "detail": f"退化值（{payload.get('source')}）：{payload.get('degenerate_why')}"
                          "—— 這一輪沒有天花板證據，不得宣告『標註沒問題』"}
    if med is None or thr is None or med >= thr:
        return {"abandon": False, "reason": None, "credible": True,
                "detail": payload.get("note", "")}
    return {
        "abandon": True, "reason": "label_ceiling", "credible": True,
        "detail": f"天花板 IoU 中位數 {med:.4f} < 門檻 {thr}（{payload.get('scope')} 人工 GT、"
                  f"n={payload.get('n_boxes')}）：**問題在標註不在模型**。停在 s03 —— "
                  "先修 auto-label 規則（RefineParams）或補人工框，訓練多少 epoch 都頂不破標註",
    }


def comparable(a: str | None, b: str | None) -> bool:
    """兩輪的天花板來源能不能互比。跨來源（合成退化值 vs 真人工 GT）一律不可比。"""
    return a is not None and b is not None and a == b


# ---------- 會失敗的檢查：`uv run python -m src.eval.ceiling` ----------

def _jitter(gt: list[dict[str, float]], scale: float, seed: int) -> list[dict[str, float]]:
    """人工風格的抖動框：人不會照連通分量畫框，會偏移一點、框鬆一點。"""
    import random

    rng = random.Random(seed)
    out = []
    for g in gt:
        out.append({"cx": g["cx"] + rng.uniform(-scale, scale) * g["w"],
                    "cy": g["cy"] + rng.uniform(-scale, scale) * g["h"],
                    "w": g["w"] * (1 + rng.uniform(-scale, scale)),
                    "h": g["h"] * (1 + rng.uniform(-scale, scale))})
    return out


def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import time

    t0 = time.time()
    rng_gt = [[{"cls": 0, "cx": 0.3 + 0.004 * i, "cy": 0.5, "w": 0.22, "h": 0.18}]
              for i in range(40)]

    # 1. GT 與 pred 完全相同 → 中位數 1.0，而且**一定要被標成退化**、credible=False。
    #    （這一格就是合成資料那條路：同一套連通分量定義生 GT 也生框）
    same = [geometry.best_ious(g, g) for g in rng_gt]
    mirror = summarize(same, source=ROBOFLOW_ANCHOR, scope="anchor", n_boot=200)
    assert mirror["iou_median"] == 1.0, mirror["iou_median"]
    assert mirror["degenerate"] and not mirror["credible"], mirror
    assert "鏡子" in mirror["degenerate_why"], mirror["degenerate_why"]
    assert "退化值" in mirror["note"], mirror["note"]
    # 出處說是人工 anchor 也照樣抓到 —— 退化偵測不靠出處欄位自律
    assert mirror["frac_ge_999"] == 1.0, mirror["frac_ge_999"]
    # 退化的不會被判 abandon（那是「沒量到」不是「很糟」），但 credible 必須是 False
    g_mirror = gate(mirror)
    assert not g_mirror["abandon"] and not g_mirror["credible"], g_mirror

    # 2. 人工風格的抖動框 → 真實 IoU **必須 < 1.0**，而且不准被標成退化
    jit = [geometry.best_ious(_jitter(g, 0.18, seed=i), g) for i, g in enumerate(rng_gt)]
    real = summarize(jit, source=ROBOFLOW_ANCHOR, scope="pool", n_boot=200)
    assert real["iou_median"] < 1.0, real["iou_median"]
    assert not real["degenerate"] and real["credible"], real
    assert real["threshold"] == 0.4, real["threshold"]
    assert real["ci"]["lo"] is not None and real["ci"]["hi"] <= 1.0, real["ci"]
    assert real["map5095_ceiling"] <= real["map50_ceiling"], real
    assert not gate(real)["abandon"], gate(real)

    # 3. 抖到門檻底下 → gate 必須 abandon，reason 是 DESIGN 的 `label_ceiling`
    bad = [geometry.best_ious(_jitter(g, 0.75, seed=100 + i), g) for i, g in enumerate(rng_gt)]
    low = summarize(bad, source=ROBOFLOW_ANCHOR, scope="pool", n_boot=200)
    assert low["iou_median"] < 0.4, low["iou_median"]
    assert low["verdict"] == "fail", low
    g_low = gate(low)
    assert g_low["abandon"] and g_low["reason"] == "label_ceiling", g_low
    assert "問題在標註不在模型" in g_low["detail"], g_low["detail"]

    # 4. 合成出處一律退化，就算數字看起來像真的（0.65 也不准當證據）
    syn = summarize(jit, source=SYNTHETIC, scope="anchor", n_boot=200)
    assert syn["degenerate"] and not syn["credible"], syn
    assert syn["threshold"] == 0.6, syn["threshold"]

    # 5. 跨來源不可比（跟 class_table_version 同等級的鎖）
    assert comparable(SYNTHETIC, SYNTHETIC) and comparable(ROBOFLOW_ANCHOR, ROBOFLOW_ANCHOR)
    assert not comparable(SYNTHETIC, ROBOFLOW_ANCHOR), "跨來源竟然可比"
    assert not comparable(None, SYNTHETIC), "來源不明竟然可比"

    # 6. 🔒 封印：任何 scope 都拿不到 sealed_test 的人工框
    for forbidden in ("sealed_test", "test", "all"):
        try:
            measure(slug="x", scope=forbidden)
        except ValueError as exc:
            assert "scope 只接受" in str(exc), exc
        else:
            raise AssertionError(f"scope={forbidden} 竟然被放行 —— 考卷答案的封印破了")

    # 7. 空的 IoU 不准回 0.0 當數字（0.0 會被讀成「天花板超低」→ 誤判 abandon）
    try:
        summarize([], source=ROBOFLOW_ANCHOR, scope="pool")
    except ValueError as exc:
        assert "沒有人工 GT" in str(exc), exc
    else:
        raise AssertionError("空的 IoU 竟然算出天花板")

    # 8. 事件形狀：r81 的合成那筆（實測 `[0,…,0,155]` / 1.0）→ 退化；真人工那筆 → 可信
    syn_ev = _from_event({"source": SYNTHETIC, "iou_median": 1.0, "n": 155,
                          "iou_hist": [0] * 9 + [155], "threshold": 0.6, "verdict": "pass"})
    assert syn_ev["degenerate"] and not syn_ev["credible"], syn_ev
    rf_ev = _from_event({"source": ROBOFLOW_ANCHOR, "iou_median": 0.8258, "n": 40,
                         "iou_hist": [0, 0, 0, 2, 1, 6, 6, 4, 5, 16], "threshold": 0.4,
                         "verdict": "pass"})
    assert not rf_ev["degenerate"] and rf_ev["credible"], rf_ev
    # 人工框自己量自己（鏡子）：出處寫 roboflow_anchor 也要抓到
    assert _from_event({"source": ROBOFLOW_ANCHOR, "iou_median": 1.0, "n": 40,
                        "iou_hist": [0] * 9 + [40], "threshold": 0.4})["degenerate"]

    # 9. 真資料（有抓過才跑）：真人工 GT 的天花板**必須遠低於 1.0**，而且不准是退化值
    try:
        idx = _index_of(slug=roboflow_src.slug(roboflow_src.WORKSPACE, roboflow_src.PROJECT,
                                               roboflow_src.VERSION))
    except ValueError:
        print("（沒有 Roboflow 資料集在盤上，跳過第 9 條真資料檢查）")
    else:
        for scope in SCOPES:
            m = measure(slug=idx["slug"], scope=scope, n_boot=200, use_cache=False)
            assert not m["degenerate"], f"{scope} 竟然是退化值 —— 人工 GT 被當成 pred 了？"
            assert 0.4 <= m["iou_median"] < 0.99, (scope, m["iou_median"])
            assert m["ci"]["hi"] < 1.0, (scope, m["ci"])
            print(f"  {scope:<7} n_img={m['n_images']:<4} n_box={m['n_boxes']:<4} "
                  f"中位數 {m['iou_median']:.4f} CI [{m['ci']['lo']:.4f}, {m['ci']['hi']:.4f}] "
                  f"mAP50 天花板 {m['map50_ceiling']:.4f} / mAP50-95 {m['map5095_ceiling']:.4f}")

        # 9b. 子集只量自己那批：拿前 200 張假造一個 ds，量到的 n_images 必須跟著變小，
        #     而且指紋不同（同一個快取鍵回同一份數字，就是把別批的天花板掛到這一輪身上）
        sub_ids = [r["id"] for r in idx["images"]][:200]
        full_ids = [r["id"] for r in idx["images"]]
        assert _fingerprint(sub_ids) != _fingerprint(full_ids), "子集與全量的快取鍵竟然一樣"
        n_sub = len(freeze.gt_partition(sub_ids)["pool"])
        n_full = len(freeze.gt_partition(full_ids)["pool"])
        assert n_sub != n_full, (n_sub, n_full)
        print(f"  子集 200 張 → pool {n_sub} 張（全量 {len(full_ids)} 張 → pool {n_full} 張）"
              f"，快取鍵 {_fingerprint(sub_ids)} != {_fingerprint(full_ids)}")

    print(f"eval.ceiling selfcheck PASS（{time.time() - t0:.1f} 秒）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
