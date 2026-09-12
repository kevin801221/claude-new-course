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
from ...autolabel import cluster, demo, freeze as freeze_mod, geometry, roboflow_src

router = APIRouter(tags=["dataset"])

SOURCES = ("demo", "roboflow", "local")
ROBOFLOW_SOURCE = "roboflow"
LABEL_MODES = ("refine_only", "cluster")

# 標註從哪來（DESIGN「兩種標註模式」；`POST /datasets/ingest` 的 `labels`）：
#   "auto"  = 連通分量幾何自己生框（合成資料的預設，零人工標註）
#   "human" = 直接用資料集自帶的人工 bbox 當 GT（真實 Roboflow 資料的預設）
# 決定一次就寫進 manifest，s02/s03/s04 一律讀那裡 —— 三個 stage 各帶一個參數的話，
# 有人漏傳就變成「人工框配自動類別」，而且錯得無聲無息。
LABEL_KINDS = ("auto", "human")
HUMAN_RULE = "human"  # `label.bbox` 的 rule（契約 §8.6：rule enum 由 dataset-truth 擴充）

# 契約 §11「兩把不同的尺」：合成 GT 0.6、Roboflow 人工 anchor 0.4（M3 起）
SYNTHETIC_IOU_THRESHOLD = 0.6
ROBOFLOW_IOU_THRESHOLD = 0.4

# class 真相表的 🔒 schema 本人（nc 上下界與固定詞彙表從它讀，不在程式裡再抄一份）
CLASS_TABLE_SCHEMA = bus.PROJECT_ROOT / "_Context" / "class_table.schema.json"


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


def _resolve_labels(source: str, labels: str | None) -> str:
    """`labels` 的預設與驗證：真實資料用人工框、合成資料用幾何自動框。"""
    if labels is None:
        return "human" if source == ROBOFLOW_SOURCE else "auto"
    if labels not in LABEL_KINDS:
        raise _err(400, "BAD_ENUM", f"labels 只接受 {list(LABEL_KINDS)}，收到 {labels!r}")
    if labels == "human" and source != ROBOFLOW_SOURCE:
        raise _err(400, "BAD_ENUM",
                   f'source:{source!r} 沒有人工標註可用 —— demo 的 gt.json 是**合成**的免費 GT'
                   '（契約 §11），叫它 human 就是把合成標籤當人工標註；要人工框請用 source:"roboflow"')
    return labels


async def _rf_ensure(
    workspace: str | None, project: str | None, version: int | None
) -> dict[str, Any]:
    """下載（或沿用）Roboflow 資料集，失敗一律轉成契約 §1 的 4xx 形狀。

    **在端點裡同步做完才回 202**，不丟背景：沒 key / key 錯 / 沒網路要當場回 4xx。
    丟背景的話這支回 202，錯誤只會出現在 server log，而前端看到的是一個空的成功。
    第一次下載約 9 MB / 實測 4.3 秒（之後沿用 index.json，不重抓、也不需要 key）。
    """
    try:
        return await asyncio.to_thread(
            roboflow_src.ensure,
            workspace or roboflow_src.WORKSPACE,
            project or roboflow_src.PROJECT,
            roboflow_src.VERSION if version is None else version,
        )
    except roboflow_src.RoboflowError as exc:
        raise _err(exc.status, exc.code, exc.detail) from exc


def _rf_index(manifest: dict[str, Any]) -> dict[str, Any]:
    """manifest 記的那份 Roboflow index（純檔案讀：不打網路、不需要 key）。"""
    rf = manifest.get("roboflow") or {}
    index = roboflow_src.load_index(
        roboflow_src.dataset_dir(rf.get("workspace", ""), rf.get("project", ""), rf.get("version", 0))
    )
    if index is None:
        raise _err(409, "ROBOFLOW_DOWNLOAD_FAILED",
                   f"找不到 {rf.get('slug')} 的 index.json（01-raw-data/roboflow/ 被刪掉了？）"
                   "—— 開一個新 run 重跑 ingest 就會重抓")
    return index


def _class_table_bounds() -> tuple[int, int, tuple[str, ...]]:
    """從 🔒 `class_table.schema.json` 本人讀 nc 上下界與固定詞彙表。

    不在程式裡抄一份：抄了就會走鐘，而走鐘的那天是「落檔出去的 cls 值意義全錯」。
    """
    spec = json.loads(CLASS_TABLE_SCHEMA.read_text("utf-8"))["properties"]["names"]
    return spec["minItems"], spec["maxItems"], tuple(spec["items"]["enum"])


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

# (run_id, seam) → 這一段在這個 run 上已經起跑過。契約 §6 的後端義務：「同一個邏輯事實不准發
# 兩個不同 seq（例如重試 ingest 不可重發 120 筆 ds.image 到同一個 run）；要重做就開新 run。」
# 守在**共用函式**裡而不是端點裡：那顆按鈕的 driver 與 curl 單步教學走的是同一支，
# 守在端點的話 driver 那條路就沒人守。重啟後 in-flight 的 run 一律被標 crashed
# （registry.claim_or_crash），所以這張表不需要跨 process 持久化。
_STARTED: set[tuple[str, str]] = set()
# run_id → 背景 task（curl 單步教學那條路沒有 driver，Stop 也要停得掉它們）
_BG: dict[str, set[asyncio.Task]] = {}


def _claim_seam(run_id: str, seam: str) -> None:
    """搶下「這個 run 的這一段」。搶不到就 409（不是靜靜再跑一次）。"""
    key = (run_id, seam)
    if key in _STARTED:
        raise _err(409, "STAGE_ALREADY_RAN",
                   f"run {run_id} 的 {seam} 已經跑過了；重打會把同一批事件用新的 seq 再發一次"
                   "（契約 §6），要重做請開新 run")
    _STARTED.add(key)


def cancel_background(run_id: str) -> int:
    """把這個 run 的背景 task 全部 cancel（Stop 呼叫它）。回 cancel 掉的數量。"""
    tasks = [t for t in _BG.pop(run_id, set()) if not t.done()]
    for t in tasks:
        t.cancel()
    return len(tasks)


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
    *, run_id: str, ds_id: str, source: str, limit: int, seed: int, preset: str = "real",
    labels: str | None = None, workspace: str | None = None, project: str | None = None,
    version: int | None = None,
) -> int:
    """s01 逐張進場。回傳實際進場張數。

    必須產生的事件（契約 §4 / §7）：
      `ds.total {total, source}` → 每張一筆 `ds.image {id,name,url,cls,counts,split:null}`
      → 每 10 張一筆 `ds.progress {loaded,total}`。
    一律經 `bus.append_event(..., actor="dataset-truth")`，不准自己寫 events.jsonl。
    §7.1 的「只送前 200 筆影像事件」由 `bus.append_event` 自己守（超過回 None）——
    demo 的 120 張走不到，Roboflow 的 409 張**每次都會走到**，第 201 張起畫面靠
    `ds.progress` 游標 + `GET /datasets/{ds}/images` 快照補。

    兩個來源：
      `demo`     —— 程式生成 120 張，id 規則凍死在契約 §11（`demo{i:04d}` / `wafer_{i:04d}.png`）。
      `roboflow` —— 真實資料集（預設 WM-811K 409 張，自帶人工 bbox）。沒下載過就下載、
                    下載過就沿用（教室重跑不該每次下載）；key 只在 server 端讀 `.env`。
    """
    kind = _resolve_labels(source, labels)
    _claim_seam(run_id, "s01")
    rf: dict[str, Any] | None = None
    if source == ROBOFLOW_SOURCE:
        # 端點已經先 ensure 過一次（那裡才回得了 4xx），這裡是沿用磁碟上的 index；
        # 走 driver（POST /runs）那條路時，第一次下載才會發生在這裡。
        rf = await _rf_ensure(workspace, project, version)
        picked = rf["images"][:limit]
        images = [{"id": r["id"], "name": r["name"]} for r in picked]
        human = {r["id"]: r["boxes"] for r in picked}
        names = rf["names"]
    elif source == "demo":
        # 沒先跑 gen_demo_wafers.py 也不該讓按鈕失敗：seed 對得上就沿用，否則現產（約 1.5 秒）
        gt = await asyncio.to_thread(demo.ensure, limit, seed)
        # 只留 id/name —— gt.json 的 `shape` 是合成標籤，契約 §11 明令不准進任何 API 回應
        images = [{"id": r["id"], "name": r["name"]} for r in gt["images"][:limit]]
        human, names = {}, []
    else:  # pragma: no cover - 端點先擋掉了，這裡是第二道
        raise _err(501, "NOT_IMPLEMENTED", f"source:{source!r} 還沒實作")
    total = len(images)

    # 🔒 封印的第六道（事件流那道側門）：`labels:"human"` 的 cls/counts 就是人工標註的答案，
    # 而 sealed-test 的答案整個 run 家族只准在 `/final-test` 開封一次。`list_images()` 已經把
    # 快照那條路遮掉（`annotations: null`），事件這條路不遮的話，照樣能從 ds.image 讀出
    # 「哪 80 張是考卷、每張什麼類別幾個框」—— 那封印就只是裝飾。
    # 這裡用的分法與 s04 落檔**同一支** `freeze_mod.gt_partition`（照 id 順序，重跑切到同一批）。
    sealed = set(freeze_mod.gt_partition([r["id"] for r in images])["sealed_test"]) if human else set()

    _write_json(
        _ds_dir(ds_id) / "manifest.json",
        {
            "ds_id": ds_id,
            "run_id": run_id,
            "source": source,
            "seed": seed,
            "total": total,
            "labels": kind,
            "names": names,
            **({"roboflow": {k: rf[k] for k in ("workspace", "project", "version", "slug",
                                                "nc", "names", "source_url")}} if rf else {}),
            "images": images,
        },
    )

    note = ""
    if rf is not None:
        note = f"｜{rf['slug']} · 人工框 {rf['box_total']} 個 · 標註 {kind}"
        if fb := rf.get("version_fallback"):
            note += f" ⚠ v{fb['want']} 不存在，退回 v{fb['used']}（可用 {fb['available']}）"
        if total < rf["total"]:
            note += f" ⚠ limit={limit} 只取前 {total}/{rf['total']} 張（GT 三分會跟著縮）"
    bus.append_event(
        run_id, "s01", "ds.total", "dataset-truth",
        {"total": total, "source": source},
        text=f"dataset: {total} 張進場（source={source}, seed={seed}）{note}",
    )
    for i, rec in enumerate(images, 1):
        # `labels:"human"` 時類別是現成的（資料集自帶）→ 直接填，縮圖牆第一眼就有 class chips。
        # `labels:"auto"` 一律空的：class 真相表要到 s02 才凍，在這裡填等於偷看人工答案 ——
        # 「零人工標註」的實際邊界就是這一行。
        counts = (
            roboflow_src.counts_of(human[rec["id"]], names)
            if human and kind == "human" and rec["id"] not in sealed
            else {}
        )
        bus.append_event(
            run_id, "s01", "ds.image", "dataset-truth",
            {
                "id": rec["id"],
                "name": rec["name"],
                "url": f"/api/v1/images/{rec['id']}",
                "cls": sorted(counts),
                "counts": counts,
                "split": None,    # split 由 s04 指派
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
    """s03 標註（auto = 無類別 bbox；human = 人工框）。回傳已標註張數。

    兩種標註模式（由 s01 寫進 manifest 的 `labels` 決定，這裡不再開第二個旋鈕）：
      `auto`  —— 連通分量幾何自己生框。契約 §8.6 寫死 `cls = -1`、`rule = "cc_bbox"`、
                 `conf` = 該分量面積佔最大分量面積的比例。**不准讀 gt.json 的 `shape`**。
      `human` —— 直接用資料集自帶的人工 bbox 當 GT：`cls` 是真類別、`conf = 1.0`、
                 `rule = "human"`。人工框就是標註本身，不必也不准再去精修它。

    事件：每張 `label.bbox`（auto 另有 `label.mask`；conf < 0.25 多一筆 `label.lowconf`）
    → 全部跑完一筆 `label.anchor_iou`。

    `label.anchor_iou` 的尺（契約 §11「兩把不同的尺」，靠 `source` 欄位分辨、不混用）：
      demo     → 合成 GT、全 120 張、門檻 0.6、`source="synthetic"`
      roboflow → 人工 GT、**只量 anchor 那 40 張**、門檻 0.4、`source="roboflow_anchor"`
    roboflow 為什麼只量 anchor：sealed-test 的人工框是考卷答案，在 s03 拿來當尺就是每輪偷看
    一次；anchor 本來就是 DESIGN 指定的「每輪量漂移」那把尺。
    `human` 模式量的是「**如果**走 auto 會有多準」—— 拿人工框自己和自己量一定是 1.0000，
    那不是天花板證據，那是一面鏡子。
    """
    _claim_seam(run_id, "s03")
    manifest = _read_json(_ds_dir(ds_id) / "manifest.json")
    if manifest is None:
        raise _err(404, "DS_NOT_FOUND", f"dataset {ds_id} 還沒進場（先跑 s01 ingest）")
    kind = manifest.get("labels", "auto")
    ids = [rec["id"] for rec in manifest["images"]]
    params = geometry.RefineParams()
    if manifest.get("source") == ROBOFLOW_SOURCE:
        human = roboflow_src.boxes_of(_rf_index(manifest))
        ruler = {i: human[i] for i in freeze_mod.gt_partition(ids)["anchor"] if human.get(i)}
        iou_source, iou_threshold, ruler_name = "roboflow_anchor", ROBOFLOW_IOU_THRESHOLD, \
            f"Roboflow 人工 anchor（{len(ruler)} 張）"
    else:
        # gt.json 只在這裡被讀，而且**只讀 boxes 不讀 shape**：框是用來量天花板的尺，
        # 型態標籤是答案。量尺可以看，答案不行 —— 這條線就是「零人工標註」的實際邊界。
        human = {}
        ruler = {r["id"]: r["boxes"] for r in (demo.load_gt() or {}).get("images", [])}
        iou_source, iou_threshold, ruler_name = "synthetic", SYNTHETIC_IOU_THRESHOLD, "合成 GT"

    labels: dict[str, list[dict[str, Any]]] = {}
    ious: list[float] = []
    labels_path = _ds_dir(ds_id) / "labels.json"

    for i, rec in enumerate(manifest["images"], 1):
        path = roboflow_src.path_of(rec["id"])
        if path is None:
            # 缺檔**不准靜默跳過**（和 496 行的 s02 同一條規則）：跳過的話 run 會全綠收工，
            # counts 還寫著 loaded:20 / labeled:10 —— 其中 10 個檔案根本不存在。
            # 更糟的是 `label.anchor_iou` 的 n 跟著無聲變小，IoU 天花板是用剩下那半算的，
            # 正是本檔 `_parse_label` 的 docstring 自己明文禁止的那件事。壞資料停在資料層。
            raise _err(404, "IMAGE_NOT_FOUND",
                       f"image {rec['id']}（{rec['name']}）的檔案不存在 —— "
                       "解壓中斷或檔案被移走。整批砍掉重抓：rm -rf 01-raw-data/roboflow/<slug>/，"
                       "再開一個新 run")
        if kind == "human":
            boxes = [
                {"cls": b["cls"], "cx": b["cx"], "cy": b["cy"], "w": b["w"], "h": b["h"],
                 "conf": 1.0, "rule": HUMAN_RULE}
                for b in human.get(rec["id"], [])
            ]
        else:
            out = await asyncio.to_thread(geometry.label_image, path, params)
            boxes = out["boxes"]
            bus.append_event(
                run_id, "s03", "label.mask", "dataset-truth",
                {
                    "image_id": rec["id"],
                    "defect_ratio": out["defect_ratio"],
                    "n_components": out["n_components"],
                },
            )
        labels[rec["id"]] = boxes

        bus.append_event(
            run_id, "s03", "label.bbox", "dataset-truth",
            {"image_id": rec["id"], "boxes": boxes},
            text=f"{rec['name']}  {len(boxes)} box  rule="
                 f"{HUMAN_RULE if kind == 'human' else geometry.M1_RULE}",
        )
        low = min((b["conf"] for b in boxes), default=1.0)
        if low < params.lowconf:
            bus.append_event(
                run_id, "s03", "label.lowconf", "dataset-truth",
                {"image_id": rec["id"], "conf": low},
                text=f"{rec['name']}  低信心 {low:.2f} → 送審清單",
            )
        if rec["id"] in ruler:
            # human 模式下 `boxes` 就是尺本身 → 另外跑一次幾何當「被量的那一方」
            pred = boxes
            if kind == "human":
                pred = (await asyncio.to_thread(geometry.label_image, path, params))["boxes"]
            ious += geometry.best_ious(pred, ruler[rec["id"]])
        if i % _LABEL_FLUSH_EVERY == 0:
            _write_json(labels_path, labels)
        await asyncio.sleep(PRESET_PACE.get(preset, 0.0))  # teaching：框也要一張一張疊上去

    _write_json(labels_path, labels)

    med = float(statistics.median(ious)) if ious else 0.0
    bus.append_event(
        run_id, "s03", "label.anchor_iou", "dataset-truth",
        {
            "source": iou_source,
            "iou_median": round(med, 4),
            "iou_hist": geometry.iou_hist(ious),
            "n": len(ious),
            "threshold": iou_threshold,
            "verdict": "pass" if med >= iou_threshold else "fail",
        },
        text=f"auto-bbox vs {ruler_name}：IoU 中位數 {med:.3f}（門檻 {iou_threshold}，n={len(ious)}）",
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


def _bill_llm_start(run_id: str) -> None:
    """呼叫**之前**就把 `llm_calls` 加一。

    原本是呼叫成功回來才記 —— 於是那一通正在跑的 240 秒裡（甚至剛燒完但失敗時），
    頂部預算條顯示「LLM 0/24 calls · USD 0.00」。README 的威脅模型就是「無人看管會燒你的
    GPU 與 token」，正在燒的那一通一定要看得見。金額還是等回應（`total_cost_usd` 由 CLI 給，
    不自己估）。
    """
    state = registry.read_state(run_id)
    if state is None:
        return
    state["budget"]["llm_calls"] = state["budget"].get("llm_calls", 0) + 1
    registry.write_state(state)


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
        state["budget"]["cost_usd"] = round(
            float(state["budget"].get("cost_usd", 0)) + float(llm.get("cost_usd", 0.0)), 6
        )
    registry.write_state(state)
    registry.set_status(run_id, state["status"], **extra)  # 同一個狀態再寫一次 = 帳本補一行


def _refuse_human_class_table(manifest: dict[str, Any]) -> None:
    """`labels:"human"` 的 s02：類別不必發現（人工標註自帶類別名），但 class 真相表**塞不進去**。

    `class_table.schema.json` 🔒 是照「KMeans + LLM 命名」凍的：`nc` ∈ [3,6]、`names` 只能是
    那六個固定詞、`cluster_stats` 必填 `k` / `silhouette` / `clusters`（minItems 3）。
    人工標註這條路一個都給不出誠實的值 —— 實測 WM-811K v3 的 yolov8 export 是
    `nc:1, names:['Donut']`，而人工標註沒有 silhouette 可言。

    所以這裡**停**，不硬塞。硬塞的兩種做法都更糟：
      (a) 把 `['Donut']` 補到 3 類 → 發明了兩個不存在的類別，data.yaml 與所有 `cls` 值從此說謊。
      (b) 讓 s02 照樣跑 KMeans 當「類別體系」、s03 留人工框 → 框的 `cls`（人工的 0）
          會對到 KMeans 的第 0 群名字，落檔出去的標籤**靜默錯位**。
    真正的修法是走契約 §12 改 schema（變更請求四段已寫在 `_Context/dataset-notes.md` §17.6）。
    `labels:"auto"` 那條不受影響：KMeans 給的 k 一定在 [3,6]、名字一定在詞彙表裡。
    """
    rf = manifest.get("roboflow") or {}
    names = list(rf.get("names") or manifest.get("names") or [])
    lo, hi, vocab = _class_table_bounds()
    raise _err(
        409, "CLASS_TABLE_SCHEMA_CONFLICT",
        f"人工標註的類別體系塞不進 class_table.schema.json 🔒："
        f"資料集是 nc={len(names)} names={names}，schema 要求 nc ∈ [{lo},{hi}]、"
        f"names ⊂ {list(vocab)}，且 cluster_stats 必填 k / silhouette / clusters"
        f"（人工標註沒有 silhouette）。"
        f"這是契約缺口，不是資料問題 —— 要跑完 s02→s08 有兩條路："
        f'(1) 改用 labels:"auto"（同一批真實影像走 KMeans + LLM 命名，schema 一定塞得下）；'
        f"(2) 走契約 §12 提變更請求給 console-owner（四段內容已寫在 "
        f"_Context/dataset-notes.md §17.6，複製過去即可）。"
        f's01 + s03 的人工框那條路不受影響：`GET /datasets/{manifest["ds_id"]}/images` '
        f"看得到每張的人工框與真實類別。",
    )


async def run_cluster(
    *, run_id: str, ds_id: str, preset: str = "real", seed: int = 42, use_llm: bool = True
) -> dict[str, Any]:
    """s02 類別從哪來：幾何描述子 → KMeans（silhouette 自選 k∈[3,6]）→ montage → **一次** LLM 命名。

    事件（契約 §4）：每張 `label.descriptor` → `class.cluster` → `class.table.frozen`。
    **全程不讀 `gt.json` 的 `shape`**（那是答案）；分群純度的事後驗證在
    `uv run python -m src.autolabel.cluster` 裡，不在這條產線上。
    """
    _claim_seam(run_id, "s02")
    manifest = _read_json(_ds_dir(ds_id) / "manifest.json")
    if manifest is None:
        raise _err(404, "DS_NOT_FOUND", f"dataset {ds_id} 還沒進場（先跑 s01 ingest）")
    if manifest.get("labels") == "human":
        _refuse_human_class_table(manifest)
    ids = [rec["id"] for rec in manifest["images"]]

    descriptors: list[dict[str, Any]] = []
    for rec in manifest["images"]:
        path = roboflow_src.path_of(rec["id"])
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
            [roboflow_src.path_of(ids[i]) for i in picks],
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

    if use_llm:
        # 兩件事都在呼叫「之前」做：
        #   1. 記帳 —— 正在燒的那一通必須看得見（頂部預算條在呼叫中顯示 0/24 等於在說謊）。
        #   2. 講一聲 —— 這一通最長 240 秒，期間一筆事件都沒有，畫面會整個不動（實測 148.8 秒）。
        _bill_llm_start(run_id)
        bus.append_event(
            run_id, "s02", "stage.note", "console-owner",
            {"stage": "s02", "kind": "llm_naming", "clusters": len(stats)},
            text=f"呼叫 LLM 命名 {len(stats)} 個群（一次呼叫，最長 {cluster.LLM_TIMEOUT_S} 秒）…",
        )
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
            # 契約 §4 的加欄（向後相容）：命名到底是 LLM 給的還是降級的機械命名，要**看得見**。
            # 只寫在 text 尾巴的話，畫面上 s02 是一枚綠色的「完成」，預算條顯示 0 —— 台上讀起來
            # 像「免費就完成了」，而不是「它沒發生」。
            "naming": naming["naming"],
            "naming_error": naming["error"],
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
    # anchor 與 sealed-test 的框來自**不經迴圈的 GT**（train/valid 才用 auto 的框）：
    #   demo     → gt.json 的免費精確 GT（合成遮罩的外接框）
    #   roboflow → 資料集自帶的人工 bbox（DESIGN「信任錨的免費來源」）
    # 沒有 GT 就沒有尺可用，寧可 409 也不要寫出一堆空的 label 檔假裝有封印 test。
    if manifest.get("source") == ROBOFLOW_SOURCE:
        gt_boxes = roboflow_src.boxes_of(_rf_index(manifest))
        gt_hint = "Roboflow 的人工 bbox 不見了 —— 開新 run 重跑 ingest 會重抓"
    else:
        gt_boxes = {r["id"]: r["boxes"] for r in (demo.load_gt() or {}).get("images", [])}
        gt_hint = "找不到 01-raw-data/demo/gt.json（先跑 scripts/gen_demo_wafers.py）"
    if not gt_boxes:
        raise _err(409, "SELFCHECK_FAILED",
                   f"anchor 與 sealed-test 要用不經迴圈的 GT 的框，但{gt_hint}")
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
        text=f"落檔 {out['path']} · ds_version {out['ds_version']} · 指紋 {out['ds_fingerprint']}"
             f"（data.yaml 只有 train / valid，封印那一份在 {out['sealed_path']}）",
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
    # 類別名的來源：class 表（auto 那條路）→ 沒有就用資料集自帶的（human 那條路）。
    # 這裡跟事件的 `ds.image.counts` 必須**長出同一組名字**：前端的 `fromSnapshot()`
    # 是同一件事實的第二個形狀，兩邊不一致的話同一張圖從快照進來與從事件進來會分家。
    names = table.get("names") or manifest.get("names") or []
    page = manifest["images"][offset : offset + limit]

    def _cls(image_id: str) -> str:
        return name_of.get(clusters.get(image_id), "unclassified")

    # 封印要遮的是**答案**。demo / `labels:"auto"` 那條路 labels.json 裝的是機器自己猜的框
    # （不是答案，遮了反而看不到 auto-label 在做什麼），所以遮蔽照舊跟著 s04 的 splits.json。
    # `labels:"human"` 那條路 labels.json 裝的就是人工 GT —— 那 80 張的答案從 s01 起就不准出現，
    # 不能等 s04 寫完 splits.json 才遮（s02 卡住、或只跑到 s03 就收工的 run 照樣會漏）。
    # 分法與 `run_ingest` / s04 同一支 `gt_partition`（照 id 順序，重跑切到同一批）。
    sealed_ids = (
        set(freeze_mod.gt_partition([r["id"] for r in manifest["images"]])["sealed_test"])
        if manifest.get("labels") == "human" else set()
    )

    def _classes(image_id: str) -> dict[str, int]:
        boxes = labels.get(image_id, [])
        # 人工框逐框自帶類別（一張圖可以有兩類）；auto 的框一律 cls=-1（契約 §8.6）→ 走 class 表。
        if names and any(b.get("cls", -1) >= 0 for b in boxes):
            return roboflow_src.counts_of(boxes, names)
        return {_cls(image_id): len(boxes)}

    def _row(rec: dict[str, Any]) -> dict[str, Any]:
        # 🔒 封印的第五道（側門）：sealed-test 的那 40 張在這張表上只留「這格被封起來了」。
        # 四道鎖守的是 /eval、/final-test 與資料層，但這支 REST 快照原本照樣吐出
        # `split:"sealed_test"` + `annotations{count,classes}` —— 不必開封、不必有 stop 事件，
        # 就能知道封印考卷有哪幾張、每張什麼類別幾個框。DESIGN 護欄 1 說封印是「結構性保證，
        # 不靠自律」，那這條路徑也不能靠自律。
        sealed = splits.get(rec["id"]) == "sealed_test" or rec["id"] in sealed_ids
        return {
            "id": rec["id"],
            "name": rec["name"],
            "url": f"/api/v1/images/{rec['id']}",
            "split": "sealed" if sealed else splits.get(rec["id"]),
            "annotations": None if sealed else {
                "count": len(labels.get(rec["id"], [])),
                "classes": _classes(rec["id"]),
            },
        }

    return {"offset": offset, "total": manifest["total"], "results": [_row(rec) for rec in page]}


def image_bytes(image_id: str, w: int | None) -> tuple[bytes, str]:
    """回 `(PNG bytes, etag)`；`etag` = 內容 sha1 的前 16 碼（契約 §8.8）。

    封印**不擋影像位元**，這是刻意的：`03-sealed-test/images/wafer_0054.png` 與
    `01-raw-data/demo/wafer_0054.png` 是同一個檔（實測 sha1 都是 ed0141fe…），而那 120 張
    本來就是 s01 逐張進場、M1 縮圖牆一張一張畫出來的公開素材 —— 封印的是**答案**
    （`03-sealed-test/labels/`）與「哪幾張是考卷、每張幾個框什麼類別」，不是那些像素。
    在這裡回 403/404 會讓 M1 縮圖牆 120 張裡有 40 張變空白，換到的保密量是 0。
    答案那一半的洩漏由 `list_images()` 的遮蔽擋（見該函式的註解）。
    """
    path = roboflow_src.path_of(image_id)
    if path is None:
        raise _err(404, "IMAGE_NOT_FOUND", f"image {image_id} 不存在")
    payload = path.read_bytes()
    # Roboflow 的 yolov8 export 是 jpg，而契約 §8.8 的回應寫死 `image/png` → 這裡轉一次。
    # 原本就是 png 且不縮圖時**一個位元組都不動**（ETag 因此和改這段之前相同）。
    to_png = path.suffix.lower() != ".png"
    if w is not None or to_png:
        with Image.open(io.BytesIO(payload)) as im:
            out = im
            if w is not None and im.width != w:
                h = max(1, round(im.height * w / im.width))
                out = im.resize((w, h), Image.NEAREST)  # 點陣圖用 NEAREST，別把 die 糊掉
            if out is not im or to_png:
                buf = io.BytesIO()
                out.convert("RGB").save(buf, "PNG")
                payload = buf.getvalue()
    return payload, hashlib.sha1(payload).hexdigest()[:16]


# ---------- M1 端點（殼已照契約寫好） ----------

class IngestBody(BaseModel):
    run_id: str
    source: str = "demo"
    limit: int = 120
    seed: int = 42
    # 契約 §8.5 的選填加欄（不帶 = 舊行為）：`labels` 兩種標註模式；ws/pj/ver 指定 Roboflow
    # 資料集（預設就是驗證過的 wm811k-paasr / wm811k / v3）。
    labels: str | None = None
    workspace: str | None = None
    project: str | None = None
    version: int | None = None


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
    if body.source == "local":
        raise _err(501, "NOT_IMPLEMENTED", f"source:{body.source!r} 未排程（契約 §8.5）")
    kind = _resolve_labels(body.source, body.labels)
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
    # roboflow：下載（或沿用）先在這裡同步做完 —— 沒 key / key 錯 / 沒網路要當場回 4xx，
    # 不能丟到背景變成「202 成功但縮圖牆永遠空著」。
    rf = await _rf_ensure(body.workspace, body.project, body.version) \
        if body.source == ROBOFLOW_SOURCE else None
    coro = run_ingest(
        run_id=body.run_id, ds_id=ds_id, source=body.source, limit=body.limit, seed=body.seed,
        preset=state["preset"], labels=kind, workspace=body.workspace, project=body.project,
        version=body.version,
    )
    await _spawn(body.run_id, coro)  # 202 = 立刻回應、事件走唯一那條 SSE（契約 §1 裁決 A）
    return JSONResponse(
        status_code=202,
        content={
            "ds_id": ds_id,
            "run_id": body.run_id,
            # demo 是程式生成的，total 等於 limit；roboflow 是真實張數（limit 只是上限）
            "total": min(body.limit, rf["total"]) if rf else body.limit,
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
        await _spawn(body.run_id, _cluster_stage(
            run_id=body.run_id, ds_id=body.ds_id, preset=state["preset"], seed=state.get("seed", 42)
        ))
    else:
        await _spawn(body.run_id, run_autolabel(
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
    # `ds_version` 由 freeze 依**落檔後的內容指紋**發（`freeze.assign_version()`），不是 create_run
    # 寫死的 "v1"：資料變了版本號才會動，雜訊帶與跨輪 Δ 的那把鎖才鎖得住。指紋一起記進 state，
    # 之後問「這個 run 到底訓練在哪一版資料上」有證據可查（帳本欄位只有 ds_version）。
    registry.set_status(body.run_id, state["status"], class_table_version=out["class_table_version"],
                        ds_version=out["ds_version"], ds_fingerprint=out["ds_fingerprint"])
    return JSONResponse(
        status_code=202,
        content={
            "ds_id": ds,
            "run_id": body.run_id,
            "class_table_version": out["class_table_version"],
            "ds_version": out["ds_version"],
            "ds_fingerprint": out["ds_fingerprint"],
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


async def _spawn(run_id: str, coro) -> None:
    """把 seam 丟到背景跑；還沒實作的 seam 會立刻 raise NotImplementedError → main.py 轉 501。

    task 要**記在 `_BG[run_id]`**：curl 單步教學那條路沒有 driver，不記的話 Stop 停不掉它
    （按了 Stop、run 標 cancelled，這條 coroutine 還在往事件流裡塞東西）。
    """
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0)  # 讓它先跑一步，未實作的 seam（與重複起跑的 409）當場現形
    if task.done():
        if (exc := task.exception()) is not None:
            raise exc
        return
    _BG.setdefault(run_id, set()).add(task)
    task.add_done_callback(lambda t: (_BG.get(run_id, set()).discard(t), _report_failure(t)))
