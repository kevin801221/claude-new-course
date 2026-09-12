"""metric-auditor 的 router —— 唯一有權說「這算不算真進步」的人。

契約：`_Context/api-contract.md` §9（端點清單）、§4（`eval.*` / `charts.*` / `test.*` 的 data 欄位）、
§10（錯誤碼）。設計：`_Context/DESIGN.md` s08 ＋ 護欄 1（封印 test 四道鎖）與護欄 3（進步門檻是 CI 寬度）。
擁有者：**metric-auditor**（M4 起）。

四條不准鬆手的紀律（M4 實作後逐條對應到程式碼）：
  1. `POST /eval` 的 `split` **只接受 `valid` 與 `anchor`**，傳 `test` 回 400 `SPLIT_TEST_FORBIDDEN`。
     鎖在兩層：本檔的 enum 檢查，以及 `src/eval/metrics.split_dirs()`（資料層根本拿不到 sealed-test）。
  2. 沒量 noise floor 之前 `POST /eval` 回 409 `NOISE_FLOOR_MISSING` —— 沒有雜訊帶就沒有
     「進步」這個詞，先 `GET /eval/noise-floor/{ds_version}?measure=true`。
  3. 跨 `class_table_version` 的比較**直接拒答**（409 `INCOMPARABLE_CLASS_TABLE`），不回假的 Δ。
  4. `runs/runs.jsonl` **只讀不寫**（team-roles §2.3：判分的手不寫紀錄）。狀態一律經 `registry.py`。

torch / ultralytics 一行都不准出現在本檔 —— 推論走 `subprocess` 起 `src/eval/infer.py`。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...eval import checks as ck
from ...eval import metrics, series
from .. import bus, registry

router = APIRouter(tags=["eval"])

ACTOR = "metric-auditor"
STAGE = "s08"
SPLITS = ("valid", "anchor")          # 🔒 `test` 不在這裡，而且永遠不會被加進來
PROJECT_ROOT = bus.PROJECT_ROOT
EXPERIMENTS_DIR = PROJECT_ROOT / "04-experiments"
FINAL_TEST_DIR = EXPERIMENTS_DIR / "final-test"
INFER_TIMEOUT_S = 900


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


def _ni(milestone: str) -> HTTPException:
    return _err(501, "NOT_IMPLEMENTED", f"此端點於 {milestone} 實作（擁有者 metric-auditor）")


# ---------- 解析 run → train → best.pt（全程唯讀） ----------

def _state(run_id: str) -> dict[str, Any]:
    state = registry.read_state(run_id)
    if state is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 不存在")
    return state


def _weights_of(rec: dict[str, Any]) -> Path | None:
    recipe_path = rec.get("recipe_path")
    if not recipe_path:
        return None
    path = Path(recipe_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        recipe = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    best = Path(recipe["project"]) / recipe.get("name", "yolo") / "weights" / "best.pt"
    return best if best.exists() else None


def _resolve_train(state: dict[str, Any], train_id: str | None) -> tuple[str, Path]:
    """挑要評的那一次訓練。不指定就取**最後一次有 best.pt 的正式訓練**（探針不算數）。"""
    trains: dict[str, Any] = state.get("train") or {}
    if train_id is not None:
        rec = trains.get(train_id)
        if rec is None:
            raise _err(404, "TRAIN_NOT_FOUND", f"train {train_id} 不在 run {state['run_id']} 裡")
        weights = _weights_of(rec)
        if weights is None:
            raise _err(404, "TRAIN_NOT_FOUND", f"train {train_id} 沒有 best.pt，沒有東西可以評")
        return train_id, weights
    # 由新到舊。排序用數字尾碼而不是字串 —— 字串排序會把 t10 排在 t2 前面（第 10 次訓練當天才炸）。
    for tid in sorted(trains, key=lambda t: int(t.rsplit("-t", 1)[-1]) if t.rsplit("-t", 1)[-1].isdigit()
                      else -1, reverse=True):
        rec = trains[tid]
        # 探針不算數（4 epoch 的短跑），被 cancel / crash 的也不算 —— 那是半成品，
        # 拿它的 best.pt 去評等於用「跑到一半的模型」代表這一輪。
        if rec.get("kind") == "probe" or rec.get("status") != "done":
            continue
        weights = _weights_of(rec)
        if weights is not None:
            return tid, weights
    raise _err(404, "TRAIN_NOT_FOUND",
               f"run {state['run_id']} 沒有任何「已完成且帶 best.pt」的正式訓練"
               "（探針與被中止的訓練不算；先 POST /api/v1/train）")


def _eval_dir(train_id: str) -> Path:
    return EXPERIMENTS_DIR / train_id / "eval"


def _summary_path(train_id: str) -> Path:
    return _eval_dir(train_id) / "summary.json"


def _find_summary(run_id: str) -> dict[str, Any] | None:
    """某個 run 最後一份 eval 摘要（`GET /eval/{run_id}/*` 都靠這支，不重算）。"""
    cands = [p for p in EXPERIMENTS_DIR.glob("*/eval/summary.json")
             if json.loads(p.read_text("utf-8")).get("run_id") == run_id]
    if not cands:
        return None
    return json.loads(max(cands, key=lambda p: p.stat().st_mtime).read_text("utf-8"))


def _all_summaries() -> list[dict[str, Any]]:
    out = []
    for p in EXPERIMENTS_DIR.glob("*/eval/summary.json"):
        try:
            out.append(json.loads(p.read_text("utf-8")))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda s: s.get("evaluated_at") or "")


# ---------- 推論子行程（API process 不准 import torch） ----------

def _infer(*, weights: Path, split: str, out_path: Path, imgsz: int, device: str) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "src.eval.infer", "--weights", str(weights),
           "--split", split, "--out", str(out_path), "--imgsz", str(imgsz), "--device", device]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                          timeout=INFER_TIMEOUT_S)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise _err(500, "INFER_FAILED", f"推論子行程失敗：{' / '.join(tail) or '無輸出'}")
    return json.loads(out_path.read_text("utf-8"))


def _imgsz_device(train_rec: dict[str, Any]) -> tuple[int, str]:
    """推論要用**訓練當時那組** imgsz / device，不是預設值 —— 換了就不是同一把尺。"""
    recipe_path = train_rec.get("recipe_path")
    if recipe_path:
        path = Path(recipe_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        try:
            p = json.loads(path.read_text("utf-8"))["params"]
            return int(p["imgsz"]), str(p["device"])
        except (OSError, KeyError, json.JSONDecodeError):
            pass
    return 320, "mps"


# ---------- body ----------

class EvalBody(BaseModel):
    run_id: str
    train_id: str | None = None
    split: str = "valid"
    compare_to: str | None = None       # 明確指定要跟哪個 run 比（跨 class table 會被拒答）
    n_boot: int = metrics.N_BOOT


class FinalTestBody(BaseModel):
    run_id: str
    stop_event_seq: int
    train_id: str | None = None


# ---------- POST /eval ----------

@router.post("/eval", status_code=202)
def evaluate(body: EvalBody) -> JSONResponse:
    """對 `valid`（或 `anchor`）逐張推論 → per-class + bootstrap CI + 四項 check + verdict。

    契約 §1 裁決 A：會產生事件的 POST 回 `202` + JSON，不回串流（事件走唯一那條 SSE）。
    本端點是 `def`（不是 `async def`），FastAPI 會丟到 threadpool —— 推論子行程等得起，
    但 event loop 不能被它擋住，不然心跳會停、SSE 會卡死。
    """
    # 鎖 1（封印 test 的第二道鎖）：先判 test 再判其他 enum，錯誤碼才會是契約 §10 那一個
    if body.split == "test":
        raise _err(400, "SPLIT_TEST_FORBIDDEN",
                   "split=test 被封印：sealed-test 只有 POST /api/v1/final-test 讀得到，"
                   "而且要求 body 帶 stop_event_seq")
    if body.split not in SPLITS:
        raise _err(400, "BAD_ENUM", f"split 只接受 {list(SPLITS)}，收到 {body.split!r}")
    if not 1 <= body.n_boot <= 20000:
        raise _err(400, "BAD_RANGE", f"n_boot 必須在 1..20000，收到 {body.n_boot}")

    state = _state(body.run_id)
    ds_version = state.get("ds_version") or "v1"
    ctv = state.get("class_table_version")
    current_ctv = metrics.class_table_version()

    # 鎖 3：run 訓練當時的 class table 與現在磁碟上的不同 → 直接拒答（不回假的 Δ）
    if ctv is not None and current_ctv is not None and ctv != current_ctv:
        raise _err(409, "INCOMPARABLE_CLASS_TABLE",
                   f"run {body.run_id} 的 class_table_version 是 {ctv}，"
                   f"現在的標註是 {current_ctv} —— 兩套類別定義之間沒有可比的指標")

    # 鎖 2：沒量 noise floor 就沒有「進步」這個詞
    nf = metrics.read_noise_floor(ds_version)
    if nf is None:
        raise _err(409, "NOISE_FLOOR_MISSING",
                   f"{ds_version} 還沒量 noise floor（同 config × 3 seed 的 σ）；"
                   f"先打 GET /api/v1/eval/noise-floor/{ds_version}?measure=true")
    # `ds_version` 是人填的字串，重新 freeze 一次它不會變（實測 02-dataset 被重寫但仍叫 v1）。
    # 指紋一變，雜訊帶量的就不是這份資料集 —— 不准拿舊的帶去判新的 Δ。
    fingerprint = metrics.dataset_fingerprint()
    if nf.get("ds_fingerprint") and nf["ds_fingerprint"] != fingerprint:
        raise _err(409, "NOISE_FLOOR_MISSING",
                   f"{ds_version} 的 noise floor 是對指紋 {nf['ds_fingerprint']} 的資料集量的，"
                   f"現在的 02-dataset 是 {fingerprint}（資料集被重新 freeze 過但 ds_version 沒變）；"
                   f"重量一次：GET /api/v1/eval/noise-floor/{ds_version}?measure=true&force=true")

    train_id, weights = _resolve_train(state, body.train_id)
    train_rec = (state.get("train") or {})[train_id]
    imgsz, device = _imgsz_device(train_rec)
    names = metrics.class_names()
    registry.set_stage(body.run_id, STAGE, "running")

    bus.append_event(body.run_id, STAGE, "eval.noise_floor", ACTOR,
                     {"sigma": nf["sigma"], "noise_floor": nf["noise_floor"],
                      "seeds": [s["seed"] for s in nf["seeds"]]},
                     text=f"noise floor 2σ = {nf['noise_floor']:.4f}"
                          f"（σ {nf['sigma']:.4f}，{len(nf['seeds'])} 個 seed 同 config 各訓一次）；"
                          f"落在這個帶內的 Δ 一律記「試過、沒用」")

    # 推論（主 split）
    eval_dir = _eval_dir(train_id)
    pred = _infer(weights=weights, split=body.split, out_path=eval_dir / f"{body.split}.json",
                  imgsz=imgsz, device=device)
    result = metrics.with_ci(metrics.evaluate(pred["records"], names), n_boot=body.n_boot)

    # anchor gate：只當 gate 不當 rank（護欄 2）。評 valid 時順手量一次，20 張很便宜。
    anchor_recall = None
    if body.split == "valid":
        a_pred = _infer(weights=weights, split="anchor", out_path=eval_dir / "anchor.json",
                        imgsz=imgsz, device=device)
        a_res = metrics.evaluate(a_pred["records"], names)
        present = [p for p in a_res["per_class"] if p["support"] > 0]
        anchor_recall = round(sum(p["recall"] for p in present) / len(present), 4) if present else None

    # 跟誰比：預設取「同一個 class table 的上一份 eval」；明確指定跨版本 → 拒答
    prev = None
    incomparable = []
    for s in reversed(_all_summaries()):
        if s.get("run_id") == body.run_id or s.get("split") != body.split or s.get("final"):
            continue
        if body.compare_to and s.get("run_id") != body.compare_to:
            continue
        if s.get("class_table_version") != ctv or s.get("ds_fingerprint") != fingerprint:
            incomparable.append({"run_id": s.get("run_id"),
                                 "class_table_version": s.get("class_table_version"),
                                 "ds_fingerprint": s.get("ds_fingerprint"),
                                 "why": "class_table_version 不同" if s.get("class_table_version") != ctv
                                        else "資料集指紋不同（同一個 ds_version 底下資料已被換掉）"})
            continue
        prev = s
        break
    if body.compare_to and prev is None:
        if incomparable:
            raise _err(409, "INCOMPARABLE_CLASS_TABLE",
                       f"run {body.compare_to} 與本輪不可比（{incomparable[0]['why']}）—— 拒答，"
                       "不回一個假的 Δ")
        raise _err(404, "RUN_NOT_FOUND", f"找不到 run {body.compare_to} 的 eval 結果可以比")

    delta = None if prev is None else round(result["map5095"] - prev["map5095"], 4)
    sig = metrics.significant(delta, nf["noise_floor"], result["ci"]["width"])

    # 四項 check
    train_dir, _ = metrics.split_dirs("train")
    valid_dir, _ = metrics.split_dirs(body.split)
    curves = series.curves_from_csv(Path(train_rec.get("recipe_path", "")).parent /
                                    "yolo" / "results.csv" if train_rec.get("recipe_path") else None)
    check_list = [
        ck.split_leakage(train_dir, valid_dir),
        ck.test_size_power(result["per_class"], result["n_images"], result["ci"]["width"]),
        ck.anchor_drift(anchor_recall, (prev or {}).get("anchor_recall"), delta, nf["noise_floor"]),
        ck.overfit_gap(curves, nf["noise_floor"]),
    ]
    vd = ck.verdict(check_list)
    anchor_ok = next((c.get("anchor_ok") for c in check_list if c["name"] == "anchor_drift"), None)

    leak = check_list[0]
    bus.append_event(body.run_id, STAGE, "eval.leakage", ACTOR,
                     {"overlap_ids": leak["overlap_ids"], "near_dup": leak["near_dup"]},
                     text=f"leakage — {leak['detail']}")
    for p in result["per_class"]:
        bus.append_event(body.run_id, STAGE, "eval.perclass", ACTOR,
                         {"cls": p["cls"], "name": p["name"], "ap50": p["ap50"], "ap": p["ap"],
                          "recall": p["recall"], "precision": p["precision"],
                          "best_f1_conf": p["best_f1_conf"], "support": p["support"],
                          "ci_lo": p.get("ci_lo"), "ci_hi": p.get("ci_hi")},
                         text=(f"{p['name']:<10} AP50 {p['ap50'] if p['ap50'] is not None else '—'}"
                               f"  recall {p['recall']}  P {p['precision']}"
                               f"  support {p['support']}"
                               + ("  [樣本不足，僅供提示]" if 0 < p["support"] < metrics.MIN_SUPPORT
                                  else "")))

    chart = series.build(result=result, records=pred["records"], names=names,
                         results_csv=_results_csv(train_rec), speed=pred["speed"],
                         noise_floor=nf["noise_floor"], op_conf=pred.get("op_conf"))
    # ponytail: 整包 series 直接進事件（實測一筆 12 KB）。12 張 val / 6 類還好，
    # 接 Roboflow 409 張之後 PR 點會撐大它 —— 到時候改成事件只帶摘要 + charts_url，
    # 前端本來就有 `GET /eval/{run_id}/charts` 這條路。
    bus.append_event(body.run_id, STAGE, "charts.series", ACTOR,
                     {"curves": chart["curves"], "pr": chart["pr"],
                      "confusion": chart["confusion"], "per_class": chart["per_class"]},
                     text=f"charts.series — 曲線 {len(chart['curves'])} epoch、"
                          f"PR {len(chart['pr'])} 類、混淆矩陣 {len(chart['confusion']['labels'])}²")

    bus.append_event(body.run_id, STAGE, "eval.verdict", ACTOR,
                     {"verdict": vd["verdict"],
                      "checks": [{"name": c["name"], "pass": c["pass"], "detail": c["detail"]}
                                 for c in check_list],
                      "delta": delta, "significant": sig["significant"], "anchor_ok": anchor_ok},
                     text=f"verdict {vd['verdict'].upper()} — {vd['why']}；"
                          f"Δ mAP50-95 {'—' if delta is None else format(delta, '+.4f')}，{sig['why']}")

    summary = {
        "run_id": body.run_id, "train_id": train_id, "split": body.split,
        "weights": str(weights.relative_to(PROJECT_ROOT)),
        "ds_version": ds_version, "ds_fingerprint": fingerprint,
        "label_version": state.get("label_version"), "class_table_version": ctv,
        "map50": result["map50"], "map5095": result["map5095"], "ci": result["ci"],
        "per_class": [{k: v for k, v in p.items()} for p in result["per_class"]],
        "n_images": result["n_images"], "n_boxes": result["n_boxes"],
        "n_pred_boxes": sum(len(r["boxes"]) for r in pred["records"]),
        "anchor_recall": anchor_recall,
        "noise_floor": nf["noise_floor"], "sigma": nf["sigma"],
        "delta": delta, "compared_to": (prev or {}).get("run_id"),
        "incomparable": incomparable,
        "significant": sig["significant"], "significance_why": sig["why"],
        "checks": check_list, "verdict": vd["verdict"], "verdict_why": vd["why"],
        "anchor_ok": anchor_ok, "speed": pred["speed"], "op_conf": pred.get("op_conf"),
        "charts": chart, "final": False,
        "evaluated_at": bus.utc_now_iso(),
    }
    _summary_path(train_id).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                       "utf-8")
    registry.set_stage(body.run_id, STAGE, "done")

    return JSONResponse(status_code=202, content={
        "run_id": body.run_id, "train_id": train_id, "split": body.split,
        "map50": result["map50"], "map5095": result["map5095"], "ci": result["ci"],
        "noise_floor": nf["noise_floor"], "delta": delta, "significant": sig["significant"],
        "verdict": vd["verdict"], "anchor_ok": anchor_ok,
        "checks": [{"name": c["name"], "pass": c["pass"], "detail": c["detail"]}
                   for c in check_list],
        "n_images": result["n_images"], "n_pred_boxes": summary["n_pred_boxes"],
        "charts_url": f"/api/v1/eval/{body.run_id}/charts",
        "predictions_url": f"/api/v1/eval/{body.run_id}/predictions?split={body.split}",
        "events_url": f"/api/v1/runs/{body.run_id}/events?since=0",
    })


def _results_csv(train_rec: dict[str, Any]) -> Path | None:
    recipe_path = train_rec.get("recipe_path")
    if not recipe_path:
        return None
    path = Path(recipe_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        recipe = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return Path(recipe["project"]) / recipe.get("name", "yolo") / "results.csv"


# ---------- GET /eval/noise-floor/{ds_version} ----------

@router.get("/eval/noise-floor/{ds_version}")
def noise_floor(ds_version: str, measure: bool = False, run_id: str | None = None,
                model: str = "yolov8n", epochs: int = 10, force: bool = False) -> dict[str, Any]:
    """同 config × 3 seed 的 σ；noise floor = 2σ。**沒有這份，後面所有 Δ 判定都是自欺。**

    已量過就回快取（所以這支是冪等的）。`?measure=true` 才准真的燒 GPU 去量 ——
    一個 GET 偷偷跑三次訓練是最糟的 API 設計。
    """
    cached = metrics.read_noise_floor(ds_version)
    if cached is not None and not force:
        return {**cached, "cached": True}
    if not measure:
        raise _err(409, "NOISE_FLOOR_MISSING",
                   f"{ds_version} 還沒量 noise floor；要量就加 ?measure=true"
                   f"（同 config × {len(metrics.NOISE_SEEDS)} 個 seed 各訓一次，會真的燒 GPU）")
    payload = metrics.measure_noise_floor(ds_version=ds_version, model=model, epochs=epochs,
                                          force=force)
    if run_id is not None and registry.read_state(run_id) is not None:
        bus.append_event(run_id, STAGE, "eval.noise_floor", ACTOR,
                         {"sigma": payload["sigma"], "noise_floor": payload["noise_floor"],
                          "seeds": [s["seed"] for s in payload["seeds"]]},
                         text=f"noise floor 2σ = {payload['noise_floor']:.4f}"
                              f"（σ {payload['sigma']:.4f}；"
                              + "、".join(f"seed {s['seed']} {s['map5095']:.4f}"
                                          for s in payload["seeds"]) + "）")
    return {**payload, "cached": False}


# ---------- GET /eval/{run_id}/charts ----------

@router.get("/eval/{run_id}/charts")
def charts(run_id: str) -> dict[str, Any]:
    """圖表 series JSON（不送 PNG）—— 前端自畫 inline SVG 才能 hover 與跨輪疊圖。"""
    summary = _find_summary(run_id)
    if summary is None:
        _state(run_id)   # run 不存在就 404 RUN_NOT_FOUND，存在但沒評過是另一種錯
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 還沒跑過 eval（先 POST /api/v1/eval）")
    return {"run_id": run_id, "train_id": summary["train_id"], "split": summary["split"],
            **summary["charts"]}


# ---------- GET /eval/{run_id}/verdict ----------

@router.get("/eval/{run_id}/verdict")
def verdict(run_id: str) -> dict[str, Any]:
    """`{verdict, checks[], delta, ci, noise_floor, significant, weakest_class, anchor_ok}`。"""
    summary = _find_summary(run_id)
    if summary is None:
        _state(run_id)
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 還沒跑過 eval（先 POST /api/v1/eval）")
    scored = [p for p in summary["per_class"] if p["support"] > 0]
    weakest = min(scored, key=lambda p: p["ap50"]) if scored else None
    return {
        "run_id": run_id, "train_id": summary["train_id"], "split": summary["split"],
        "verdict": summary["verdict"], "why": summary["verdict_why"],
        "checks": summary["checks"],
        "map50": summary["map50"], "map5095": summary["map5095"], "ci": summary["ci"],
        "noise_floor": summary["noise_floor"], "sigma": summary["sigma"],
        "ds_version": summary["ds_version"], "ds_fingerprint": summary.get("ds_fingerprint"),
        "delta": summary["delta"], "compared_to": summary["compared_to"],
        "incomparable": summary["incomparable"],
        "significant": summary["significant"], "significance_why": summary["significance_why"],
        "weakest_class": weakest, "anchor_ok": summary["anchor_ok"],
        "anchor_recall": summary["anchor_recall"],
        "class_table_version": summary["class_table_version"],
        "evaluated_at": summary["evaluated_at"],
    }


# ---------- GET /eval/{run_id}/predictions ----------

@router.get("/eval/{run_id}/predictions")
def predictions(run_id: str, split: str = "valid", offset: int = 0,
                limit: int = 200) -> dict[str, Any]:
    """逐張預測快照（契約 §7.3：重連時前端先打快照再接串流，早期縮圖絕不依賴 replay）。

    回的是**座標不是圖** —— 前端把 boxes 疊在 `GET /api/v1/images/{id}` 的原圖上，
    才能 hover、能跟 gt_boxes 對照、能跨輪疊。
    """
    if split == "test":
        raise _err(400, "SPLIT_TEST_FORBIDDEN", "sealed-test 的預測只在 POST /final-test 的回應裡")
    if split not in SPLITS:
        raise _err(400, "BAD_ENUM", f"split 只接受 {list(SPLITS)}，收到 {split!r}")
    if offset < 0 or not 1 <= limit <= 500:
        raise _err(400, "BAD_RANGE", f"offset ≥ 0、limit 1..500；收到 offset={offset} limit={limit}")
    summary = _find_summary(run_id)
    if summary is None:
        _state(run_id)
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 還沒跑過 eval（先 POST /api/v1/eval）")
    path = _eval_dir(summary["train_id"]) / f"{split}.json"
    if not path.exists():
        raise _err(404, "RUN_NOT_FOUND", f"run {run_id} 沒有 {split} 的預測檔")
    pred = json.loads(path.read_text("utf-8"))
    records = pred["records"]
    return {"run_id": run_id, "train_id": summary["train_id"], "split": split,
            "offset": offset, "total": len(records), "speed": pred["speed"],
            "results": records[offset:offset + limit]}


# ---------- POST /final-test（唯一會讀 sealed-test 的端點） ----------

@router.post("/final-test", status_code=202)
def final_test(body: FinalTestBody) -> JSONResponse:
    """封印 test 的第三道鎖：要求 `stop_event_seq`，沒有 stop 事件回 409；

    同一個 `ds_version` 只准成功開封一次（第二次 409 `FINAL_TEST_ALREADY_USED`），
    而且結果標 `final:true` —— arbiter 的證據池明確排除它（護欄 1 第四道鎖）。
    """
    state = _state(body.run_id)
    ds_version = state.get("ds_version") or "v1"

    stops = [e for e in bus.read_events(body.run_id) if e["type"] == "stop"]
    if not stops:
        raise _err(409, "NOT_STOPPED_YET",
                   f"run {body.run_id} 沒有 stop 事件（未 converged/abandoned）—— 還沒停，不准看 test")
    if body.stop_event_seq not in {e["seq"] for e in stops}:
        raise _err(400, "BAD_RANGE",
                   f"stop_event_seq {body.stop_event_seq} 不是這個 run 的 stop 事件 seq"
                   f"（實際有 {sorted(e['seq'] for e in stops)}）")

    seal = FINAL_TEST_DIR / f"{ds_version}.json"
    if seal.exists():
        used = json.loads(seal.read_text("utf-8"))
        raise _err(409, "FINAL_TEST_ALREADY_USED",
                   f"{ds_version} 的 sealed-test 已於 {used['evaluated_at']} 被 run "
                   f"{used['run_id']} 開封過，一個 ds_version 只准一次")

    train_id, weights = _resolve_train(state, body.train_id)
    train_rec = (state.get("train") or {})[train_id]
    imgsz, device = _imgsz_device(train_rec)
    names = metrics.class_names()

    pred = _infer(weights=weights, split="sealed-test",
                  out_path=_eval_dir(train_id) / "sealed-test.json", imgsz=imgsz, device=device)
    result = metrics.with_ci(metrics.evaluate(pred["records"], names))

    for rec in pred["records"]:
        bus.append_event(body.run_id, STAGE, "test.image", ACTOR,
                         {"image_id": rec["image_id"], "pred_url": rec["url"],
                          "boxes": rec["boxes"], "iou_vs_label": rec["iou_vs_gt"]},
                         text=f"test {rec['image_id']}  {len(rec['boxes'])} 框  "
                              f"IoU vs GT {rec['iou_vs_gt']}")

    payload = {
        "run_id": body.run_id, "train_id": train_id, "split": "sealed-test", "final": True,
        "ds_version": ds_version, "class_table_version": state.get("class_table_version"),
        "stop_event_seq": body.stop_event_seq,
        "map50": result["map50"], "map5095": result["map5095"], "ci": result["ci"],
        "per_class": result["per_class"], "n_images": result["n_images"],
        "n_boxes": result["n_boxes"], "n_pred_boxes": sum(len(r["boxes"]) for r in pred["records"]),
        "speed": pred["speed"], "evaluated_at": bus.utc_now_iso(),
        "note": "final:true —— 這一筆不得進入 arbiter 的證據池，也不得用來排序任何 run（護欄 1）",
    }
    seal.parent.mkdir(parents=True, exist_ok=True)
    seal.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")

    bus.append_event(body.run_id, STAGE, "eval.verdict", ACTOR,
                     {"verdict": "final", "checks": [], "delta": None, "significant": None,
                      "anchor_ok": None},
                     text=f"final-test 開封 {ds_version}：mAP50 {result['map50']}、"
                          f"mAP50-95 {result['map5095']}（{result['n_images']} 張，一次性，不可重來）")
    return JSONResponse(status_code=202, content=payload)
