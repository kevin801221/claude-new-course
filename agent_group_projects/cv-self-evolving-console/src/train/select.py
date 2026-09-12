"""選型 + 評分 + 本機 ETA 校準 + 本輪 recipe / augment 決策（s05・s06 的腦）。

契約：`_Context/api-contract.md` §4（`model.candidates` / `model.selected` /
`recipe.proposed` / `augment.decision` / `train.calibrated` / `recipe.frozen` 的 data 欄位）。
設計：`DESIGN.md` 訓練工程專家 (1)(2)(3) 與護欄 5。
擁有者：training-engineer。

⚠️ 本檔在 **API process 內被 import**，所以**一行 torch / ultralytics 都不准出現**
（base 的 `uv sync` 不裝那兩個，import 到就整個 app 起不來）。純資料與算術。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WEIGHTS_DIR = PROJECT_ROOT / "00-weights"
EXPERIMENTS_DIR = PROJECT_ROOT / "04-experiments"
DATA_YAML = PROJECT_ROOT / "02-dataset" / "data.yaml"
CALIBRATION_PATH = WEIGHTS_DIR / "calibration.json"

# ---------- 候選池（DESIGN (1)：實證而非查表，但池子本身是刻意寫死的） ----------

# params_m 是官方公布的常數，不是量出來的（要量就得 import torch，而這裡是 API process）。
# rel_cost 是「相對 yolov8n 的每 epoch 秒數」的先驗，只在**還沒探針過**時用來估 ETA；
# 探針跑完就整組換成實測值（真的量到的東西一律勝過先驗）。
CANDIDATES: list[dict[str, Any]] = [
    {"name": "yolov8n", "params_m": 3.2, "rel_cost": 1.0},
    {"name": "yolov8s", "params_m": 11.2, "rel_cost": 2.2},
]
# 刻意不進池，而且理由要印在前端上（DESIGN (1)）：不是「沒試過」，是「試過會被騙」。
EXCLUDED: list[dict[str, str]] = [
    {
        "name": "rtdetr-l",
        "why": "MPS 上 deformable attention 會靜默走 PYTORCH_ENABLE_MPS_FALLBACK 掉回 CPU，"
               "慢 10–50 倍且不報錯，把 budget 帳算爛",
    }
]

# 講師機（M4 Max）的錨點：8.89 分鐘 / 50 epochs = 10.67 秒 / epoch。
# ⚠️ 這個錨綁定「那台機器 + 那份 dataset + 那組 recipe」。所以 scale_factor 不是純硬體係數，
# 是「本機這一組條件相對講師那一組」的比值 —— 它唯一的用途就是讓學生機器的 ETA 不要抄講師的數字。
BASE_MIN_PER_50EP = 8.89
BASE_SEC_PER_EPOCH = BASE_MIN_PER_50EP * 60 / 50

# 評分函式（DESIGN「決定什麼」）：score = mAP50-95@probe − λ·正規化分鐘 − μ·正規化 ms/img
LAMBDA_MIN = 0.10
MU_MS = 0.05
# 探針還沒量到 CI 時的先驗半寬。實測 log 顯示 81 張 val 的相鄰 epoch mAP50-95 在 0.275–0.611
# 之間跳（DESIGN (1) 引的數字），所以「沒量到就假設很寬」才是安全的方向。
PRIOR_CI = 0.15


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def read_calibration() -> dict[str, Any]:
    """本機 ETA 校準。沒校準過就回 scale_factor 1.0 並明講 `calibrated: false`。

    前端要把「×1.0（未校準）」與「×3.4（已校準）」顯示成不同的東西 —— 否則學生會以為
    抄來的 8.89 分鐘是他自己機器的數字（DESIGN 護欄 5）。
    """
    cal = _read_json(CALIBRATION_PATH)
    if not isinstance(cal, dict) or "scale_factor" not in cal:
        return {"scale_factor": 1.0, "calibrated": False, "probe_2ep_sec": None,
                "basis": f"{BASE_MIN_PER_50EP} 分鐘 / 50 epochs（講師 M4 Max 錨點，未經本機校準）"}
    return {**cal, "calibrated": True}


def write_calibration(*, scale_factor: float, probe_2ep_sec: float, name: str,
                      epochs: int, imgsz: int, n_train: int, per_model_sec: dict[str, float]) -> dict[str, Any]:
    cal = {
        "scale_factor": round(scale_factor, 4),
        "probe_2ep_sec": round(probe_2ep_sec, 2),
        "measured_with": {"model": name, "epochs": epochs, "imgsz": imgsz, "n_train": n_train},
        "sec_per_epoch": {k: round(v, 3) for k, v in per_model_sec.items()},
        "basis": f"本機前 2 個 epoch 實測 ÷ 講師錨點 {BASE_SEC_PER_EPOCH:.2f} 秒/epoch",
    }
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(cal, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return cal


def est_min(name: str, epochs: int, cal: dict[str, Any] | None = None) -> float:
    """預估全訓分鐘數。有實測的 sec/epoch 就用實測，否則用錨點 × rel_cost × scale_factor。"""
    cal = cal or read_calibration()
    measured = (cal.get("sec_per_epoch") or {}).get(name)
    if measured:
        return round(measured * epochs / 60, 2)
    rel = next((c["rel_cost"] for c in CANDIDATES if c["name"] == name), 1.0)
    return round(BASE_SEC_PER_EPOCH * rel * cal.get("scale_factor", 1.0) * epochs / 60, 2)


def weights_meta(name: str) -> dict[str, Any]:
    """`00-weights/<name>.pt.json` 是 `scripts/fetch_weights.py` 落下的快取憑證。"""
    meta = _read_json(WEIGHTS_DIR / f"{name}.pt.json") or {}
    return {
        "weights_cached": (WEIGHTS_DIR / f"{name}.pt").exists(),
        "sha256": meta.get("sha256"),
        "mps_ok": meta.get("mps_ok"),  # None = 還沒真的 forward 過，不准當成 True
    }


def candidates(epochs: int = 10) -> list[dict[str, Any]]:
    """`GET /models/candidates` 的列（契約 §9 的欄位：name/params_m/weights_cached/sha256/mps_ok/est_min）。"""
    cal = read_calibration()
    return [
        {"name": c["name"], "params_m": c["params_m"], **weights_meta(c["name"]),
         "est_min": est_min(c["name"], epochs, cal)}
        for c in CANDIDATES
    ]


# ---------- 評分與裁決 ----------

def probe_ci(map_series: list[float]) -> tuple[float, str]:
    """探針自己的離散度當 CI 半寬的暫代值。

    ⚠️ 誠實話：DESIGN 要的是 metric-auditor 的 bootstrap CI（M4 才有）。在那之前用
    「同一次探針裡 mAP50-95 的全距 ÷ 2」，並把 `ci_source` 標成 `probe_spread`，
    免得前端把它畫成統計上的信賴區間。M4 上線後這裡整段換掉。
    """
    if len(map_series) < 2:
        return PRIOR_CI, "prior"
    return max((max(map_series) - min(map_series)) / 2, 1e-6), "probe_spread"


def score(map5095: float, minutes: float, ms_img: float | None,
          max_min: float, max_ms: float | None) -> float:
    """`score = mAP50-95 − λ·正規化分鐘 − μ·正規化 ms/img`（DESIGN 的式子，逐項照抄）。"""
    s = map5095 - LAMBDA_MIN * (minutes / max_min if max_min else 0.0)
    if ms_img is not None and max_ms:
        s -= MU_MS * (ms_img / max_ms)
    return round(s, 4)


def rank(results: list[dict[str, Any]], epochs: int = 10) -> list[dict[str, Any]]:
    """把探針結果算成 scoreboard（每列附 CI 與預估分鐘）。`results` 每筆要有 name / map_series。"""
    cal = read_calibration()
    rows = []
    for r in results:
        series = r.get("map_series") or []
        ci, ci_source = probe_ci(series)
        rows.append({
            "name": r["name"],
            "params_m": next((c["params_m"] for c in CANDIDATES if c["name"] == r["name"]), None),
            "map5095": round(max(series) if series else 0.0, 4),
            "ci": round(ci, 4), "ci_source": ci_source,
            "ms_img": r.get("ms_img"),
            "mem_mb": r.get("mem_mb"),
            "sec_per_epoch": r.get("sec_per_epoch"),
            "epochs_run": len(series),
            "est_min": est_min(r["name"], epochs, cal),
        })
    max_min = max((x["est_min"] or 0) for x in rows) or 1.0
    ms_vals = [x["ms_img"] for x in rows if x["ms_img"]]
    max_ms = max(ms_vals) if ms_vals else None
    for x in rows:
        x["score"] = score(x["map5095"], x["est_min"] or 0.0, x["ms_img"], max_min, max_ms)
    return rows


def choose(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """選一個。**差距落在 CI 內就選小的**（DESIGN (1)，防候選表變成隨機數排名）。"""
    if not rows:
        raise ValueError("沒有候選可選")
    if len(rows) == 1:
        r = rows[0]
        return {"name": r["name"], "score": r["score"], "why": f"只有一個候選 {r['name']}", "tie": False}
    best, second = sorted(rows, key=lambda x: -x["score"])[:2]
    delta = abs(best["map5095"] - second["map5095"])
    widest = max(best["ci"], second["ci"])
    if delta <= widest:
        small = min(rows, key=lambda x: (x["params_m"] or 0))
        return {
            "name": small["name"], "score": small["score"], "tie": True,
            "why": (f"mAP50-95 差距 {delta:.4f} 落在 CI 半寬 {widest:.4f} 內 → 無顯著差異，選小的"
                    f"（{small['name']} {small['params_m']}M，這裡的數字不可過度解讀）"),
        }
    return {
        "name": best["name"], "score": best["score"], "tie": False,
        "why": (f"{best['name']} 分數 {best['score']} 勝 {second['name']} {second['score']}；"
                f"mAP50-95 差距 {delta:.4f} > CI 半寬 {widest:.4f}，差異可讀"),
    }


# ---------- s06：本輪 recipe 與 augment 決策 ----------

# 教學 baseline **刻意調弱**（DESIGN「決定什麼」最後一句）：yolov8n / 10 epochs / imgsz 320。
# 從強 baseline 起跑（mAP50 0.977）的話每一輪都會判 no_gain，台上看到的「自我進化」
# 會是三輪「沒有進步」—— 這是教學效果的問題，不是技術偷懶。
BASELINE = {
    "model": "yolov8n",
    "epochs": 10,
    "imgsz": 320,
    "batch": 8,
    "lr0": 0.01,
    "optimizer": "auto",
    "patience": 100,   # 不讓 ultralytics 自己早停：早停會讓「改一個變因」的比較基準不一致
    "amp": False,      # MPS 上 check_amp 會另外下載 yolo26n.pt（多一條網路依賴），小模型也吃不到收益
    "seed": 42,
    "device": "mps",
    "workers": 0,      # macOS + fork 的 DataLoader worker 在子行程裡最常見的卡死來源
}

# 每個開關一行理由（DESIGN (2)：這是真正有對錯、會被挑戰的一條）。
AUGMENT_DECISIONS: list[dict[str, Any]] = [
    {"name": "degrees", "value": 180.0, "on": True,
     "why": "rotate 開啟 — 晶圓無固定方向，任意角度都是合法的同一片"},
    {"name": "fliplr", "value": 0.5, "on": True,
     "why": "水平翻轉開啟 — 同上，鏡像後仍是合法晶圓"},
    {"name": "flipud", "value": 0.5, "on": True,
     "why": "垂直翻轉開啟 — 同上（自然影像不敢開，晶圓可以）"},
    {"name": "mosaic", "value": 0.0, "on": False,
     "why": "mosaic 關閉 — 四張拼一張會讓 edge-ring / edge-loc 的缺陷出現在畫面正中間，破壞「靠邊」語意"},
    {"name": "translate", "value": 0.0, "on": False,
     "why": "random-crop/位移 關閉 — 平移會把靠邊缺陷推到中央，等於教模型錯的類別"},
    {"name": "scale", "value": 0.0, "on": False,
     "why": "隨機縮放 關閉 — 缺陷相對晶圓半徑的比例本身就是類別語意（center vs edge-ring）"},
    {"name": "erasing", "value": 0.0, "on": False,
     "why": "random erasing 關閉 — 缺陷本來就稀疏，抹掉就變成錯標"},
]


def augment_args() -> dict[str, float]:
    return {d["name"]: float(d["value"]) for d in AUGMENT_DECISIONS}


def build_recipe(*, model: str, run_id: str, train_id: str, overrides: dict[str, Any] | None = None,
                 data_yaml: Path = DATA_YAML) -> dict[str, Any]:
    """把 baseline + augment + 覆寫組成一份 recipe（子行程唯一的輸入）。"""
    params = {**BASELINE, "model": model}
    params.update({k: v for k, v in (overrides or {}).items() if k in BASELINE})
    return {
        "recipe_id": train_id,
        "run_id": run_id,
        "train_id": train_id,
        "kind": "train",
        "params": params,
        "augment": augment_args(),
        "data": str(data_yaml),
        "weights": str(WEIGHTS_DIR / f"{params['model']}.pt"),
        "project": str(EXPERIMENTS_DIR / train_id),
        "name": "yolo",
        "rationale": (
            f"教學 baseline 刻意調弱：{params['model']} / {params['epochs']} epochs / "
            f"imgsz {params['imgsz']} —— 從強 baseline 起跑的話每一輪都會判 no_gain，"
            "台上看到的「自我進化」會變成三輪「沒有進步」"
        ),
    }


def recipe_diff(new: dict[str, Any], old: dict[str, Any] | None) -> dict[str, list[Any]]:
    """凍結後 recipe 的鍵名級 diff（M6 的 OFAT 去重比對這個，不是自然語言描述）。"""
    if not old:
        return {}
    a, b = old.get("params", {}), new.get("params", {})
    diff = {k: [a.get(k), b[k]] for k in b if a.get(k) != b[k]}
    aa, bb = old.get("augment", {}), new.get("augment", {})
    diff.update({f"augment.{k}": [aa.get(k), bb[k]] for k in bb if aa.get(k) != bb[k]})
    return diff


# ---------- 自檢：`uv run python -m src.train.select`（不需要 torch / 不需要 GPU） ----------

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    global CALIBRATION_PATH
    import tempfile

    CALIBRATION_PATH = Path(tempfile.mkdtemp(prefix="select-selfcheck-")) / "calibration.json"

    # 1. 未校準時必須自己承認（前端要顯示「未校準」而不是假裝 ×1.0 是量出來的）
    cal = read_calibration()
    assert cal["calibrated"] is False and cal["scale_factor"] == 1.0, cal
    base_n = est_min("yolov8n", 10)
    assert abs(base_n - 8.89 * 10 / 50) < 0.02, base_n  # 錨點換算：10 epochs = 1.78 分鐘
    assert est_min("yolov8s", 10) > base_n, "yolov8s 不該估得比 yolov8n 快"

    # 2. 校準後 ETA 必須跟著本機實測跑（護欄 5 的全部意義）
    write_calibration(scale_factor=3.4, probe_2ep_sec=72.5, name="yolov8n", epochs=2,
                      imgsz=320, n_train=48, per_model_sec={"yolov8n": 36.25})
    assert read_calibration()["calibrated"] is True
    assert abs(est_min("yolov8n", 10) - 36.25 * 10 / 60) < 0.01, est_min("yolov8n", 10)
    # 沒實測過的候選退回錨點 × rel_cost × scale_factor（3.4 倍慢的機器要看到 3.4 倍的 ETA）
    assert abs(est_min("yolov8s", 10) - BASE_SEC_PER_EPOCH * 2.2 * 3.4 * 10 / 60) < 0.02

    # 3. 差距落在 CI 內 → 選小的（DESIGN (1) 的核心裁決）
    tie = rank([
        {"name": "yolov8n", "map_series": [0.30, 0.34], "ms_img": 3.0},
        {"name": "yolov8s", "map_series": [0.31, 0.36], "ms_img": 7.0},
    ])
    picked = choose(tie)
    assert picked["name"] == "yolov8n" and picked["tie"] is True, picked
    assert "無顯著差異" in picked["why"]

    # 4. 差距真的大於 CI → 選贏的那個（不能變成「永遠選小的」）
    clear = rank([
        {"name": "yolov8n", "map_series": [0.100, 0.101], "ms_img": 3.0},
        {"name": "yolov8s", "map_series": [0.600, 0.601], "ms_img": 7.0},
    ])
    picked2 = choose(clear)
    assert picked2["name"] == "yolov8s" and picked2["tie"] is False, picked2

    # 5. 代價項真的有作用：同樣的 mAP，慢的那個分數要低
    rows = rank([{"name": "yolov8n", "map_series": [0.5, 0.5]},
                 {"name": "yolov8s", "map_series": [0.5, 0.5]}])
    by = {r["name"]: r["score"] for r in rows}
    assert by["yolov8n"] > by["yolov8s"], by

    # 6. 教學 baseline 真的是調弱的那組（有人「順手優化」就會紅）
    rec = build_recipe(model="yolov8n", run_id="r1", train_id="r1-t1")
    assert (rec["params"]["epochs"], rec["params"]["imgsz"]) == (10, 320), rec["params"]
    assert rec["params"]["device"] == "mps"

    # 7. augment：旋轉翻轉開、mosaic/位移/縮放關，而且每一條都有理由
    aug = rec["augment"]
    assert aug["degrees"] == 180.0 and aug["fliplr"] == 0.5 and aug["flipud"] == 0.5, aug
    assert aug["mosaic"] == 0.0 and aug["translate"] == 0.0 and aug["scale"] == 0.0, aug
    assert all(d["why"].strip() for d in AUGMENT_DECISIONS)
    assert not any(c["name"].startswith("rtdetr") for c in CANDIDATES), "rtdetr 不准進池"

    # 8. diff 是鍵名級的（M6 去重靠它，不是靠自然語言）
    rec2 = build_recipe(model="yolov8n", run_id="r1", train_id="r1-t2", overrides={"imgsz": 640})
    assert recipe_diff(rec2, rec) == {"imgsz": [320, 640]}, recipe_diff(rec2, rec)
    assert recipe_diff(rec, rec) == {}
    print(f"train.select selfcheck PASS（暫存校準檔 {CALIBRATION_PATH}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
