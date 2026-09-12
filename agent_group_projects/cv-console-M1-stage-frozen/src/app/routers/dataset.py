"""dataset-truth 的 router —— 逐張進場、auto-label、影像快照與 proxy。

契約：`_Context/api-contract.md` §8.5–§8.8（M1 實作）、§9（M2 佔位）。
擁有者：**dataset-truth**。這份是 console-owner 在 M0 閘門建的骨架（五個 router 要能掛進
同一個 app 才算過關），HTTP 殼與錯誤碼已照契約寫好，**M1 的實作只要把下面三個 seam 填掉**：

    run_ingest()    ← s01 逐張進場（配 scripts/gen_demo_wafers.py）
    run_autolabel() ← s03 無類別 bbox（遮罩 → scipy.ndimage.label → 外接框）
    list_images() / image_bytes()  ← 快照與圖片

沒填之前一律 501（NotImplementedError 由 main.py 轉成 `{"code":"NOT_IMPLEMENTED"}`），
**不准回假資料** —— 回假資料會讓前端以為自己接好了。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import statistics
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel

from .. import bus, registry
from ...autolabel import cluster, demo, freeze as freeze_mod, geometry

router = APIRouter(tags=["dataset"])

SOURCES = ("demo", "roboflow", "local")
LABEL_MODES = ("refine_only", "cluster")
# 契約 §11「兩把不同的尺」：合成 GT 0.6、Roboflow 人工 anchor 0.4（M3 起）
SYNTHETIC_IOU_THRESHOLD = 0.6


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


# ---------- seam：M1 由 dataset-truth 填，console-owner 的 driver 直接呼叫這兩支 ----------

# ds 級的落檔（資料交換走檔案系統，不走 in-memory —— 換 process 也接得回來）：
#   01-raw-data/demo/wafer_*.png + gt.json        ← 契約 §11 凍結的佈局，gen_demo_wafers.py 產
#   01-raw-data/datasets/<ds_id>/manifest.json    ← 這個 ds 收了哪些圖（images 快照的來源）
#   01-raw-data/datasets/<ds_id>/labels.json      ← auto-label 的框（重連後補 annotations.count）
DS_DIR = registry.DS_DIR  # 唯一真相在 registry（ds id 發號要掃這個目錄，不能兩邊各寫一份）
_LABEL_FLUSH_EVERY = 20  # 每 20 張落一次盤：重連時 annotations.count 不會停在 0

# preset 的唯一行為差別（M1）：逐張之間的節奏。
# teaching 0.12 秒/張 —— M1 的驗收句子是「縮圖牆逐卡 append」，而後端不停頓的話 120 張
# 0.4 秒就寫完，台上看到的是「瞬間長完」，連 Stop 鈕都按不到；兩個 stage 合計約 30 秒，
# 仍在 90 秒驗收預算的三分之一內。real 0 秒（selfcheck 與 M5 的一鍵腳本走這條，2 秒跑完）。
# M3 起 preset 還會決定三軸預算（registry.PRESET_BUDGETS），那是另一件事。
PRESET_PACE = {"teaching": 0.12, "real": 0.0}


def _ds_dir(ds_id: str) -> Path:
    return DS_DIR / ds_id


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    tmp.replace(path)  # 原子寫：讀的人不會看到半份 JSON


async def run_ingest(
    *, run_id: str, ds_id: str, source: str, limit: int, seed: int, preset: str = "real"
) -> int:
    """s01 逐張進場。回傳實際進場張數。

    必須產生的事件（契約 §4 / §7）：
      `ds.total {total, source}` → 每張一筆 `ds.image {id,name,url,cls:[],counts:{},split:null}`
      （**只送前 200 筆**，之後改送游標）→ 每 10 張一筆 `ds.progress {loaded,total}`。
    一律經 `bus.append_event(..., actor="dataset-truth")`，不准自己寫 events.jsonl。
    id 規則凍死在契約 §11：`image_id = f"demo{i:04d}"`、`name = f"wafer_{i:04d}.png"`。
    """
    # 沒先跑 gen_demo_wafers.py 也不該讓按鈕失敗：seed 對得上就沿用，否則現產（約 1.5 秒）
    gt = await asyncio.to_thread(demo.ensure, limit, seed)
    images = gt["images"][:limit]
    total = len(images)

    _write_json(
        _ds_dir(ds_id) / "manifest.json",
        {
            "ds_id": ds_id,
            "run_id": run_id,
            "source": source,
            "seed": seed,
            "total": total,
            # 只留 id/name —— gt.json 的 `shape` 是合成標籤，契約 §11 明令不准進任何 API 回應
            "images": [{"id": r["id"], "name": r["name"]} for r in images],
        },
    )

    bus.append_event(
        run_id, "s01", "ds.total", "dataset-truth",
        {"total": total, "source": source},
        text=f"dataset: {total} 張進場（source={source}, seed={seed}）",
    )
    for i, rec in enumerate(images, 1):
        bus.append_event(
            run_id, "s01", "ds.image", "dataset-truth",
            {
                "id": rec["id"],
                "name": rec["name"],
                "url": f"/api/v1/images/{rec['id']}",
                "cls": [],        # M1 沒有類別（class 真相表 M2 才凍）
                "counts": {},
                "split": None,    # split 由 s04 指派，M2 才有
            },
        )
        if i % 10 == 0 or i == total:
            bus.append_event(
                run_id, "s01", "ds.progress", "dataset-truth",
                {"loaded": i, "total": total},
                text=f"ingest {i}/{total}",
            )
        # 交出 event loop，SSE 才是逐張流出去而不是最後一次噴完；teaching 另外放慢到看得見
        await asyncio.sleep(PRESET_PACE.get(preset, 0.0))
    return total


async def run_autolabel(
    *, run_id: str, ds_id: str, mode: str = "refine_only", preset: str = "real"
) -> int:
    """s03 無類別 bbox（第一階段）。回傳已標註張數。

    必須產生的事件：每張 `label.mask` → `label.bbox`（conf < 0.25 多一筆 `label.lowconf`）
    → 全部跑完一筆 `label.anchor_iou {source:"synthetic", threshold:0.6, ...}`。
    M1 寫死（契約 §8.6）：`boxes[].cls = -1`、`rule = "cc_bbox"`、
    `conf` = 該分量面積佔最大分量面積的比例。**不准讀 gt.json 的 `shape`**（那是偷看答案）。
    """
    manifest = _read_json(_ds_dir(ds_id) / "manifest.json")
    if manifest is None:
        raise _err(404, "DS_NOT_FOUND", f"dataset {ds_id} 還沒進場（先跑 s01 ingest）")
    params = geometry.RefineParams()
    # gt.json 只在這裡被讀，而且**只讀 boxes 不讀 shape**：框是用來量天花板的尺，
    # 型態標籤是答案。量尺可以看，答案不行 —— 這條線就是「零人工標註」的實際邊界。
    gt = {r["id"]: r["boxes"] for r in (demo.load_gt() or {}).get("images", [])}

    labels: dict[str, list[dict[str, Any]]] = {}
    ious: list[float] = []
    labels_path = _ds_dir(ds_id) / "labels.json"

    for i, rec in enumerate(manifest["images"], 1):
        path = demo.image_path(rec["id"])
        if path is None:
            continue
        out = await asyncio.to_thread(geometry.label_image, path, params)
        boxes = out["boxes"]
        labels[rec["id"]] = boxes

        bus.append_event(
            run_id, "s03", "label.mask", "dataset-truth",
            {
                "image_id": rec["id"],
                "defect_ratio": out["defect_ratio"],
                "n_components": out["n_components"],
            },
        )
        bus.append_event(
            run_id, "s03", "label.bbox", "dataset-truth",
            {"image_id": rec["id"], "boxes": boxes},
            text=f"{rec['name']}  {len(boxes)} box  rule={geometry.M1_RULE}",
        )
        low = min((b["conf"] for b in boxes), default=1.0)
        if low < params.lowconf:
            bus.append_event(
                run_id, "s03", "label.lowconf", "dataset-truth",
                {"image_id": rec["id"], "conf": low},
                text=f"{rec['name']}  低信心 {low:.2f} → 送審清單",
            )
        if rec["id"] in gt:
            ious += geometry.best_ious(boxes, gt[rec["id"]])
        if i % _LABEL_FLUSH_EVERY == 0:
            _write_json(labels_path, labels)
        await asyncio.sleep(PRESET_PACE.get(preset, 0.0))  # teaching：框也要一張一張疊上去

    _write_json(labels_path, labels)

    # 天花板證據（契約 §11「兩把不同的尺」）：合成 GT 的門檻是 0.6，
    # M3 起的 Roboflow 人工 anchor 是 0.4，靠 `source` 欄位分辨，不混用。
    med = float(statistics.median(ious)) if ious else 0.0
    bus.append_event(
        run_id, "s03", "label.anchor_iou", "dataset-truth",
        {
            "source": "synthetic",
            "iou_median": round(med, 4),
            "iou_hist": geometry.iou_hist(ious),
            "n": len(ious),
            "threshold": SYNTHETIC_IOU_THRESHOLD,
            "verdict": "pass" if med >= SYNTHETIC_IOU_THRESHOLD else "fail",
        },
        text=f"auto-bbox vs 合成 GT：IoU 中位數 {med:.3f}（門檻 {SYNTHETIC_IOU_THRESHOLD}，n={len(ious)}）",
    )
    return len(labels)


# ---------- M2 seam：s02 分群 + 一次 LLM 命名、s04 落檔（同一組函式，driver 與 curl 共用） ----------

# class 真相表的兩個落點：per-ds 是真相（class 表是從這個 ds 算出來的），
# `_Context/class_table.json` 是 `class_table.schema.json` 指名的全域路徑（M3 起找得到最後凍結的那份）。
CLASS_TABLE_GLOBAL = bus.PROJECT_ROOT / "_Context" / "class_table.json"


def _class_table_path(ds_id: str) -> Path:
    return _ds_dir(ds_id) / "class_table.json"


def _next_class_table_version(ds_id: str) -> str:
    """`v1`、`v2`…。**一發出的 version 不准改內容**（schema 明文），所以重跑一定是發新號。"""
    old = _read_json(_class_table_path(ds_id))
    if not old:
        return "v1"
    return f"v{int(str(old['version']).lstrip('v')) + 1}"


def _bill_llm(run_id: str, llm: dict[str, Any] | None, **extra: Any) -> None:
    """把 LLM 帳記進 `runs/<run_id>/state.json` 的 budget，並讓它落到 `runs/runs.jsonl`。

    帳本那一行由 `registry._sync_ledger()` 從 state 投影，公開入口是 `set_status()` ——
    所以這裡用「維持原狀態再寫一次」的方式同步，**不繞過 registry 自己寫 runs.jsonl**
    （team-roles §2.3：寫入一律經 registry，判分的手只讀）。
    token 明細 runs.jsonl 沒有欄位（DESIGN 的 schema 只有 `llm_calls` / `cost_usd`），
    落在 `01-raw-data/datasets/<ds>/naming.json`，要進帳本得走契約 §12。
    """
    state = registry.read_state(run_id)
    if state is None:
        return
    if llm is not None:
        state["budget"]["llm_calls"] = state["budget"].get("llm_calls", 0) + 1
        state["budget"]["cost_usd"] = round(
            float(state["budget"].get("cost_usd", 0)) + float(llm.get("cost_usd", 0.0)), 6
        )
    registry.write_state(state)
    registry.set_status(run_id, state["status"], **extra)  # 同一個狀態再寫一次 = 帳本補一行


async def run_cluster(
    *, run_id: str, ds_id: str, preset: str = "real", seed: int = 42, use_llm: bool = True
) -> dict[str, Any]:
    """s02 類別從哪來：幾何描述子 → KMeans（silhouette 自選 k∈[3,6]）→ montage → **一次** LLM 命名。

    事件（契約 §4）：每張 `label.descriptor` → `class.cluster` → `class.table.frozen`。
    **全程不讀 `gt.json` 的 `shape`**（那是答案）；分群純度的事後驗證在
    `uv run python -m src.autolabel.cluster` 裡，不在這條產線上。
    """
    manifest = _read_json(_ds_dir(ds_id) / "manifest.json")
    if manifest is None:
        raise _err(404, "DS_NOT_FOUND", f"dataset {ds_id} 還沒進場（先跑 s01 ingest）")
    ids = [rec["id"] for rec in manifest["images"]]

    descriptors: list[dict[str, Any]] = []
    for rec in manifest["images"]:
        path = demo.image_path(rec["id"])
        if path is None:
            raise _err(404, "IMAGE_NOT_FOUND", f"image {rec['id']} 不存在")
        d = await asyncio.to_thread(cluster.describe_image, path)
        descriptors.append(d)
        bus.append_event(
            run_id, "s02", "label.descriptor", "dataset-truth",
            {   # 契約 §4 凍結的五個欄位，其餘描述子（radial_hist / n_components / defect_ratio）
                # 在 class_table 的 descriptor_mean 裡，不往事件裡加欄位（加欄要走 §12）
                "image_id": rec["id"],
                "ring_ness": d["ring_ness"],
                "center_ness": d["center_ness"],
                "elongation": d["elongation"],
                "edge_frac": d["edge_frac"],
            },
        )
        await asyncio.sleep(PRESET_PACE.get(preset, 0.0))

    res = await asyncio.to_thread(cluster.cluster_descriptors, descriptors, seed)
    k, labels = res["k"], res["labels"]
    members = cluster.cluster_members(labels, k)

    montages: list[str] = []
    for cid, idxs in enumerate(members):
        picks = cluster.representative(res["X"], idxs, cluster.MONTAGE_GRID ** 2)
        out = await asyncio.to_thread(
            cluster.write_montage,
            [demo.image_path(ids[i]) for i in picks],
            _ds_dir(ds_id) / f"montage_c{cid}.png",
        )
        montages.append(freeze_mod.rel(out))

    bus.append_event(
        run_id, "s02", "class.cluster", "dataset-truth",
        {
            "k": k,
            "silhouette": res["silhouette"],
            "cluster_sizes": [len(m) for m in members],
            "montage_urls": montages,
        },
        text=f"KMeans k={k}（silhouette {res['silhouette']}，候選 {res['silhouette_by_k']}）"
             f" 群大小 {[len(m) for m in members]}",
    )

    # support：這一群的 bbox 實例數。schema 的描述是「train split 的」，但 s02 還沒有 split，
    # 所以先填全 ds 的實例數；train 的實例數在 s04 的 ds.split 之後才算得出來。
    boxes = _read_json(_ds_dir(ds_id) / "labels.json") or {}
    support = {cid: sum(len(boxes.get(ids[i], [])) for i in idxs) for cid, idxs in enumerate(members)}
    stats = cluster.cluster_stats(descriptors, labels, k, support)

    naming = await asyncio.to_thread(cluster.name_clusters, stats, montages, use_llm=use_llm)
    version = _next_class_table_version(ds_id)
    table = cluster.build_class_table(
        version=version, res=res, stats=stats, naming=naming, montages=montages
    )

    _write_json(_ds_dir(ds_id) / "descriptors.json", dict(zip(ids, descriptors)))
    _write_json(_ds_dir(ds_id) / "clusters.json", {i: int(c) for i, c in zip(ids, labels)})
    _write_json(_class_table_path(ds_id), table)
    _write_json(CLASS_TABLE_GLOBAL, table)
    _write_json(
        _ds_dir(ds_id) / "naming.json",
        {
            "class_table_version": version,
            "naming": naming["naming"],          # "llm" | "fallback"
            "error": naming["error"],            # 降級的原因（成功時 None）
            "llm": naming["llm"],                # model / usage / cost_usd / duration_ms
        },
    )

    bus.append_event(
        run_id, "s02", "class.table.frozen", "dataset-truth",
        {
            "version": version,
            "nc": table["nc"],
            "names": table["names"],
            "naming_rationale": table["naming_rationale"],
        },
        text=f"class 真相表 {version} 凍結：nc={table['nc']} names={table['names']}"
             f"（命名 {naming['naming']}"
             + (
                 f"，{naming['llm']['usage']['input_tokens'] + naming['llm']['usage']['output_tokens']} token"
                 f" / ${naming['llm']['cost_usd']:.4f}）" if naming["llm"] else "）"
             ),
    )
    _bill_llm(run_id, naming["llm"], class_table_version=version)
    return {
        "k": k, "silhouette": res["silhouette"], "silhouette_by_k": res["silhouette_by_k"],
        "class_table_version": version, "nc": table["nc"], "names": table["names"],
        "naming": naming["naming"], "llm": naming["llm"], "naming_error": naming["error"],
    }


def run_freeze(*, run_id: str, ds_id: str, seed: int = 42, ratios: dict[str, float] | None = None) -> dict[str, Any]:
    """s04 落檔成 dataset（含硬閘門）。同步跑（純檔案 IO，120 張約 1 秒），呼叫端用 to_thread 包。

    事件（契約 §4）：`ds.gt_partition` → `ds.split` → `ds.written` → `ds.selfcheck`。
    """
    manifest = _read_json(_ds_dir(ds_id) / "manifest.json")
    if manifest is None:
        raise _err(404, "DS_NOT_FOUND", f"dataset {ds_id} 不存在")
    table = _read_json(_class_table_path(ds_id))
    clusters = _read_json(_ds_dir(ds_id) / "clusters.json")
    auto = _read_json(_ds_dir(ds_id) / "labels.json")
    if table is None or clusters is None:
        raise _err(409, "SELFCHECK_FAILED",
                   f"dataset {ds_id} 還沒有 class 真相表，先跑 POST /api/v1/label/auto {{mode:\"cluster\"}}")
    if auto is None:
        raise _err(409, "SELFCHECK_FAILED",
                   f"dataset {ds_id} 還沒有 auto-label 的框，先跑 POST /api/v1/label/auto {{mode:\"refine_only\"}}")

    ids = [rec["id"] for rec in manifest["images"]]
    # anchor 與 sealed-test 的框來自免費精確 GT（train/valid 才用 auto 的框）。
    # 沒有 gt.json 就沒有尺可用，寧可 409 也不要寫出一堆空的 label 檔假裝有封印 test。
    gt_boxes = {r["id"]: r["boxes"] for r in (demo.load_gt() or {}).get("images", [])}
    if not gt_boxes:
        raise _err(409, "SELFCHECK_FAILED",
                   "找不到 01-raw-data/demo/gt.json —— anchor 與 sealed-test 要用它的框（先跑 scripts/gen_demo_wafers.py）")
    out = freeze_mod.freeze_dataset(
        ids=ids, class_table=table, cluster_of={i: int(c) for i, c in clusters.items()},
        auto_boxes=auto, gt_boxes=gt_boxes, seed=seed, ratios=ratios,
    )
    # splits.json 給 `GET /datasets/{ds}/images` 用。anchor / sealed_test 也寫進去：
    # 只寫 train/valid 的話，那 60 張在快照裡是 `split: null`，和「沒分到」長得一模一樣，
    # 而前端的「GT 三分」卡就是要把這三種分開顯示。
    part = freeze_mod.gt_partition(ids)
    _write_json(
        _ds_dir(ds_id) / "splits.json",
        {
            **{i: "anchor" for i in part["anchor"]},
            **{i: "sealed_test" for i in part["sealed_test"]},
            **out["assignment"],
        },
    )

    bus.append_event(
        run_id, "s04", "ds.gt_partition", "dataset-truth", out["partition"],
        text=f"GT 三分：anchor {out['partition']['anchor']} 張（每輪量漂移）／"
             f"sealed-test {out['partition']['sealed_test']} 張（整個 run 家族只開封一次，不進 data.yaml）／"
             f"其餘 {out['partition']['unused']} 張進 train+valid",
    )
    bus.append_event(
        run_id, "s04", "ds.split", "dataset-truth", out["split"],
        text=f"分層抽樣 seed={seed}：train {out['split']['train']} / valid {out['split']['valid']}"
             f"（未分類 {out['split']['unassigned']}）",
    )
    bus.append_event(
        run_id, "s04", "ds.written", "dataset-truth",
        {"path": out["path"], "data_yaml": out["data_yaml"]},
        text=f"落檔 {out['path']}（data.yaml 只有 train / valid，test 在 {out['sealed_path']} 封著）",
    )
    checks = freeze_mod.selfcheck(table)
    ok = freeze_mod.all_pass(checks)
    bus.append_event(
        run_id, "s04", "ds.selfcheck", "dataset-truth", {"checks": checks},
        text="selfcheck " + "・".join(f"{c['name']}={'PASS' if c['pass'] else 'FAIL'}" for c in checks),
    )
    return {**out, "checks": checks, "pass": ok, "class_table_version": table["version"]}


def list_images(ds_id: str, offset: int, limit: int) -> dict[str, Any]:
    """`GET /datasets/{ds}/images` 的 body（Roboflow search 相容形狀，契約 §8.7）。"""
    manifest = _read_json(_ds_dir(ds_id) / "manifest.json")
    if manifest is None:
        raise _err(404, "DS_NOT_FOUND", f"dataset {ds_id} 不存在")
    labels = _read_json(_ds_dir(ds_id) / "labels.json") or {}
    # s02 之前：沒有 class 表 → 一律單鍵 unclassified（有框但還沒有類別，這就是零標註的中間態）。
    # s02 之後：類別來自分群 + class 表；s04 之後 split 才不是 null。
    table = _read_json(_class_table_path(ds_id)) or {}
    clusters = _read_json(_ds_dir(ds_id) / "clusters.json") or {}
    splits = _read_json(_ds_dir(ds_id) / "splits.json") or {}
    name_of = {r["cluster_id"]: r["name"] for r in table.get("naming_rationale", [])}
    page = manifest["images"][offset : offset + limit]

    def _cls(image_id: str) -> str:
        return name_of.get(clusters.get(image_id), "unclassified")

    return {
        "offset": offset,
        "total": manifest["total"],
        "results": [
            {
                "id": rec["id"],
                "name": rec["name"],
                "url": f"/api/v1/images/{rec['id']}",
                "split": splits.get(rec["id"]),
                "annotations": {
                    "count": len(labels.get(rec["id"], [])),
                    "classes": {_cls(rec["id"]): len(labels.get(rec["id"], []))},
                },
            }
            for rec in page
        ],
    }


def image_bytes(image_id: str, w: int | None) -> tuple[bytes, str]:
    """回 `(PNG bytes, etag)`；`etag` = 內容 sha1 的前 16 碼（契約 §8.8）。"""
    path = demo.image_path(image_id)
    if path is None:
        raise _err(404, "IMAGE_NOT_FOUND", f"image {image_id} 不存在")
    payload = path.read_bytes()
    if w is not None:
        with Image.open(io.BytesIO(payload)) as im:
            if im.width != w:
                h = max(1, round(im.height * w / im.width))
                buf = io.BytesIO()
                im.resize((w, h), Image.NEAREST).save(buf, "PNG")  # 點陣圖用 NEAREST，別把 die 糊掉
                payload = buf.getvalue()
    return payload, hashlib.sha1(payload).hexdigest()[:16]


# ---------- M1 端點（殼已照契約寫好） ----------

class IngestBody(BaseModel):
    run_id: str
    source: str = "demo"
    limit: int = 120
    seed: int = 42


class AutoLabelBody(BaseModel):
    run_id: str
    ds_id: str
    mode: str = "refine_only"
    refine_params: dict[str, Any] | None = None


class FreezeBody(BaseModel):
    run_id: str
    seed: int = 42
    ratios: dict[str, float] | None = None


async def _cluster_stage(*, run_id: str, ds_id: str, preset: str, seed: int) -> dict[str, Any]:
    """背景版的 s02：跑完自己把 stage 標掉，失敗標 failed（背景任務沒有 driver 幫忙接例外）。"""
    try:
        out = await run_cluster(run_id=run_id, ds_id=ds_id, preset=preset, seed=seed)
    except Exception:
        registry.set_stage(run_id, "s02", "failed")
        raise
    registry.set_stage(run_id, "s02", "done")
    return out


@router.post("/datasets/ingest", status_code=202)
async def ingest(body: IngestBody) -> JSONResponse:
    """逐張進場（s01）。契約 §8.5。`POST /runs` 走的是同一組函式，不是第二套邏輯。"""
    if body.source not in SOURCES:
        raise _err(400, "BAD_ENUM", f"source 只接受 {list(SOURCES)}，收到 {body.source!r}")
    if body.source != "demo":
        raise _err(501, "NOT_IMPLEMENTED", f"source:{body.source!r} 於 M8 實作；M1 只有 demo")
    if not 1 <= body.limit <= 500:
        raise _err(400, "BAD_RANGE", f"limit 必須在 1..500，收到 {body.limit}")
    state = registry.read_state(body.run_id)
    if state is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {body.run_id} 不存在")

    if state["status"] not in registry.LIVE_STATUSES:
        raise _err(409, "RUN_NOT_LIVE",
                   f"run {body.run_id} 狀態是 {state['status']}，事件只能追加在活的 run 上"
                   "（否則快照說 done、事件流還在長，兩邊對不上）")

    ds_id = state["ds_id"]
    coro = run_ingest(
        run_id=body.run_id, ds_id=ds_id, source=body.source, limit=body.limit, seed=body.seed,
        preset=state["preset"],
    )
    await _spawn(coro)  # 202 = 立刻回應、事件走唯一那條 SSE（契約 §1 裁決 A）
    return JSONResponse(
        status_code=202,
        content={
            "ds_id": ds_id,
            "run_id": body.run_id,
            "total": body.limit,  # demo 是程式生成的，total 等於 limit
            "source": body.source,
            "ds_version": state["ds_version"],
        },
    )


@router.post("/label/auto", status_code=202)
async def label_auto(body: AutoLabelBody) -> JSONResponse:
    """auto-label（契約 §8.6）。`refine_only` = s03 無類別 bbox；`cluster` = s02 分群 + 一次 LLM 命名。

    兩個 mode 是**兩個 stage**，不是二選一：正常順序是先 `refine_only`（有框）再 `cluster`（有類別），
    freeze 兩個都要。分開是因為 M6 的最小重跑只會重跑其中一段（patch 動 autolabel.* 才從 s03 起）。
    """
    if body.mode not in LABEL_MODES:
        raise _err(400, "BAD_ENUM", f"mode 只接受 {list(LABEL_MODES)}，收到 {body.mode!r}")
    state = registry.read_state(body.run_id)
    if state is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {body.run_id} 不存在")

    if state["status"] not in registry.LIVE_STATUSES:
        raise _err(409, "RUN_NOT_LIVE",
                   f"run {body.run_id} 狀態是 {state['status']}，事件只能追加在活的 run 上")

    if body.mode == "cluster":
        registry.set_stage(body.run_id, "s02", "running")
        await _spawn(_cluster_stage(
            run_id=body.run_id, ds_id=body.ds_id, preset=state["preset"], seed=state.get("seed", 42)
        ))
    else:
        await _spawn(run_autolabel(
            run_id=body.run_id, ds_id=body.ds_id, mode=body.mode, preset=state["preset"]
        ))
    return JSONResponse(
        status_code=202,
        content={
            "run_id": body.run_id,
            "ds_id": body.ds_id,
            "mode": body.mode,
            "label_version": state["label_version"],
            "total": state["counts"]["total"],
        },
    )


@router.get("/datasets/{ds}/images")
def images(ds: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """分頁快照（契約 §8.7）。重連時補齊早期縮圖靠這支，**早期縮圖絕不依賴 replay**。"""
    if offset < 0:
        raise _err(400, "BAD_RANGE", f"offset 必須 ≥ 0，收到 {offset}")
    if not 1 <= limit <= 500:
        raise _err(400, "BAD_RANGE", f"limit 必須在 1..500，收到 {limit}")
    return list_images(ds, offset, limit)


@router.get("/images/{image_id}")
def image(
    image_id: str,
    w: int | None = None,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    """圖片 proxy（契約 §8.8）。API key 只留 server env，前端不持 key、也不能用 file:// 讀本機圖。"""
    if w is not None and not 64 <= w <= 1024:
        raise _err(400, "BAD_RANGE", f"w 必須在 64..1024，收到 {w}")
    payload, etag = image_bytes(image_id, w)
    quoted = f'"{etag}"'
    headers = {"ETag": quoted, "Cache-Control": "public, max-age=3600"}
    if if_none_match == quoted:
        return Response(status_code=304, headers=headers)
    return Response(content=payload, media_type="image/png", headers=headers)


# ---------- M2 佔位（一律 501，不准提前實作、不准改成 404、不准回假資料） ----------

@router.get("/datasets/{ds}/classes")
def classes(ds: str) -> dict[str, Any]:
    """class 真相表（DESIGN s02 / 契約 §9）。全系統唯一 class 來源。

    回的是 `class_table.json` 原封不動的六個欄位（schema 凍結），外加 `naming` / `llm` 兩個
    稽核欄位：命名到底是 LLM 給的還是降級後的機械命名、花了多少 token 與 USD。
    """
    table = _read_json(_class_table_path(ds))
    if table is None:
        if _read_json(_ds_dir(ds) / "manifest.json") is None:
            raise _err(404, "DS_NOT_FOUND", f"dataset {ds} 不存在")
        raise _err(404, "DS_NOT_FOUND",
                   f"dataset {ds} 還沒有 class 真相表（先跑 POST /api/v1/label/auto {{mode:\"cluster\"}}）")
    naming = _read_json(_ds_dir(ds) / "naming.json") or {}
    return {**table, "ds_id": ds, "naming": naming.get("naming"), "llm": naming.get("llm")}


@router.post("/datasets/{ds}/freeze", status_code=202)
async def freeze(ds: str, body: FreezeBody) -> JSONResponse:
    """分層抽樣 + 落檔 02-dataset/ + data.yaml + selfcheck（DESIGN s04 / 契約 §9）。

    落檔是純檔案 IO（120 張約 1 秒），所以**同步做完才回 202** —— 回應裡直接帶 selfcheck 清單，
    curl 單步教學不必再去撈事件。前端仍然只認 `ds.selfcheck` 事件（任一 FAIL → 訓練鈕 disabled）。
    """
    state = registry.read_state(body.run_id)
    if state is None:
        raise _err(404, "RUN_NOT_FOUND", f"run {body.run_id} 不存在")
    if state["status"] not in registry.LIVE_STATUSES:
        raise _err(409, "RUN_NOT_LIVE",
                   f"run {body.run_id} 狀態是 {state['status']}，事件只能追加在活的 run 上")
    if body.ratios is not None and sum(body.ratios.values()) <= 0:
        raise _err(400, "BAD_RANGE", "抽樣比例三個全 0")
    if body.ratios is not None and body.ratios.get("test"):
        raise _err(400, "SPLIT_TEST_FORBIDDEN",
                   "freeze 的抽樣比例不准有 test —— sealed-test 由 GT 三分切，不由比例切")

    registry.set_stage(body.run_id, "s04", "running")
    try:
        out = await asyncio.to_thread(
            run_freeze, run_id=body.run_id, ds_id=ds, seed=body.seed, ratios=body.ratios
        )
    except Exception:
        registry.set_stage(body.run_id, "s04", "failed")
        raise
    # 刻意不把 train/valid 塞進 `counts`：契約 §8.3 的 counts 只有 total/loaded/labeled，
    # 加鍵等於改快照形狀。split 的數字在 `ds.split` 事件與本端點的回應裡。
    registry.set_stage(body.run_id, "s04", "done" if out["pass"] else "failed")
    # 每個 run 都要記 class_table_version：只跑 s04 的最小重跑（M6）沒有經過 s02，
    # 不在這裡補的話帳本會留 null，跨版本比較的那道鎖（409 INCOMPARABLE_CLASS_TABLE）就失效
    registry.set_status(body.run_id, state["status"], class_table_version=out["class_table_version"])
    return JSONResponse(
        status_code=202,
        content={
            "ds_id": ds,
            "run_id": body.run_id,
            "class_table_version": out["class_table_version"],
            "split": out["split"],
            "gt_partition": out["partition"],
            "path": out["path"],
            "data_yaml": out["data_yaml"],
            "sealed_path": out["sealed_path"],
            "checks": out["checks"],
            "pass": out["pass"],
        },
    )


@router.get("/datasets/{ds}/report")
def report(ds: str):
    """難度剖面 + 標註品質（anchor_iou_median = 整條流水線的天花板證據；契約 §9）。"""
    raise _err(501, "NOT_IMPLEMENTED", "此端點於 M2 實作（擁有者 dataset-truth）")


def _report_failure(task: asyncio.Task) -> None:
    """背景 seam 掛掉時至少要有一行 log —— curl 單步教學走這條路，沒有 driver 幫忙接例外。"""
    if not task.cancelled() and task.exception() is not None:
        print(f"[dataset] 背景工作失敗：{task.exception()!r}")


async def _spawn(coro) -> None:
    """把 seam 丟到背景跑；還沒實作的 seam 會立刻 raise NotImplementedError → main.py 轉 501。"""
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0)  # 讓它先跑一步，未實作的 seam 當場現形而不是靜靜吞掉
    if task.done():
        if (exc := task.exception()) is not None:
            raise exc
        return
    task.add_done_callback(_report_failure)
