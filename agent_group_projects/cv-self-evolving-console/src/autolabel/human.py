"""人工標註 —— domain 專家自己圈的框：落檔、讀取、三種建議來源、人工 class 真相表。

契約：`_Context/api-contract.md` §8.8（標註器四支端點）、§4 的 `label.human` 事件、
      §10 的 `HUMAN_BOX_INVALID` / `HUMAN_CLASSES_MISSING` / `VLM_SUGGEST_FAILED`。
設計：`_Context/dataset-notes.md` §17.6 的契約變更請求（已由 console-owner 批准並落地：
      `class_table.schema.json` 加 `class_source`，`human` 時放寬 nc / names / cluster_stats）。
擁有者：dataset-truth。

## 三種來源只是「產生候選框」，落檔一律經 `save_boxes()`

| by | 誰產的 | 花費 |
|---|---|---|
| `manual` | 專家在前端拖出來的 | 0 |
| `geometry` | 現有的連通分量 auto-bbox（`geometry.label_image`） | 0，離線 |
| `vlm` | `claude -p` 用 Read 工具看圖回框（沿用 `cluster.call_llm` 同一條路） | 要網路、要錢 |
| `import` | 資料集自帶的人工 GT（Roboflow 那 413 個框） | 0，離線 |

**建議框不會自己落檔**：`suggest()` 只回候選，專家按了「採用」才 `save_boxes()`。
理由跟 s02 一樣 —— 一鍵生出來的東西如果直接變成真相，那就不是人工標註，是換個地方的 auto-label。

## 為什麼框存在 `labels_human.json` 而不是直接改 `labels.json`

`labels.json` 是 auto-label 的產物，每開一個新 run 就重寫。人工圈的框是**資產**，
壓在同一個檔裡下一輪就沒了。分開存還換到一件事：同一張圖的 auto 框與人工框可以逐框比 IoU
（那就是 `label_ceiling` 量的東西），存成同一份就再也比不了。

自檢：`uv run python -m src.autolabel.human`
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.autolabel import geometry  # noqa: E402

DS_DIR = PROJECT_ROOT / "01-raw-data" / "datasets"
STORE_NAME = "labels_human.json"
STORE_VERSION = 1

# 上限不是美學問題：PUT 進來的 body 是信任邊界，沒有上限的話一次 PUT 可以塞 10 萬個框把
# json 檔撐爆，而這個檔每次讀都整包進記憶體。
MAX_BOXES = 500
MAX_CLASSES = 64
MAX_NAME_LEN = 40
VLM_MAX_BOXES = 60          # 一鍵 VLM 的框數上限（prompt 裡也寫同一個數）
VLM_TIMEOUT_S = 180

BY_VALUES = ("manual", "geometry", "vlm", "import")


# ---------- 落檔 ----------

def store_path(ds_id: str) -> Path:
    return DS_DIR / ds_id / STORE_NAME


def _empty() -> dict[str, Any]:
    return {"version": STORE_VERSION, "classes": [], "images": {}}


def load(ds_id: str) -> dict[str, Any]:
    """壞檔當作沒標過（跟 `roboflow_src.load_index` 同一條規矩：半份檔要自癒，不要炸掉端點）。"""
    p = store_path(ds_id)
    if not p.exists():
        return _empty()
    try:
        out = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(out, dict) or not isinstance(out.get("images"), dict):
        return _empty()
    out.setdefault("classes", [])
    out.setdefault("version", STORE_VERSION)
    return out


def _write(ds_id: str, payload: dict[str, Any]) -> None:
    """tmp + replace 原子寫：中途斷電留下半份 JSON，等於這個 ds 的人工標註全部消失。"""
    p = store_path(ds_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    os.replace(tmp, p)


# ---------- 驗證（信任邊界：前端與 LLM 回的東西都要過這裡） ----------

def validate_classes(names: Any) -> list[str]:
    if not isinstance(names, list) or not names:
        raise ValueError("classes 要是非空的字串陣列")
    if len(names) > MAX_CLASSES:
        raise ValueError(f"類別最多 {MAX_CLASSES} 個（收到 {len(names)}）")
    out: list[str] = []
    for n in names:
        if not isinstance(n, str):
            raise ValueError(f"類別名要是字串：{n!r}")
        n = n.strip()
        if not n or len(n) > MAX_NAME_LEN:
            raise ValueError(f"類別名長度要在 1..{MAX_NAME_LEN}：{n!r}")
        if n in out:
            raise ValueError(f"類別名重複：{n!r}")
        out.append(n)
    return out


def validate_boxes(boxes: Any, nc: int) -> list[dict[str, Any]]:
    """回一份**清乾淨**的框（只留契約欄位）。任何一條不過就整批拒收，不做「跳過壞的那幾個」。

    跳過壞框比拒收更糟：專家圈了 30 個框、存完回 28 個，而畫面上不會有任何差別。
    """
    if not isinstance(boxes, list):
        raise ValueError("boxes 要是陣列")
    if len(boxes) > MAX_BOXES:
        raise ValueError(f"一張圖最多 {MAX_BOXES} 個框（收到 {len(boxes)}）")
    out: list[dict[str, Any]] = []
    for i, b in enumerate(boxes):
        if not isinstance(b, dict):
            raise ValueError(f"第 {i} 個框不是物件")
        try:
            cls = int(b["cls"])
            cx, cy, w, h = (float(b[k]) for k in ("cx", "cy", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"第 {i} 個框缺欄位或型別不對（要 cls/cx/cy/w/h）") from None
        if not 0 <= cls < nc:
            raise ValueError(f"第 {i} 個框的 cls={cls} 不在 0..{nc - 1}")
        if not (w > 0 and h > 0):
            raise ValueError(f"第 {i} 個框的寬高要 > 0（收到 w={w} h={h}）")
        # 正規化座標：中心 ± 半寬必須整個落在 [0,1]。超出去的框 YOLO train 會靜默 clip，
        # 但 IoU 與 mAP 就跟畫面上看到的不是同一個東西了。
        if not (0 <= cx - w / 2 and cx + w / 2 <= 1 and 0 <= cy - h / 2 and cy + h / 2 <= 1):
            raise ValueError(f"第 {i} 個框超出影像範圍（cx={cx} cy={cy} w={w} h={h}）")
        out.append({"cls": cls, "cx": round(cx, 6), "cy": round(cy, 6),
                    "w": round(w, 6), "h": round(h, 6)})
    return out


# ---------- 讀寫 API（給 router 用） ----------

def get_classes(ds_id: str) -> list[str]:
    return list(load(ds_id).get("classes") or [])


def set_classes(ds_id: str, names: list[str]) -> list[str]:
    """設類別清單。**只准往後加或改名，不准縮短**（縮短 = 既有框的 cls 指向不存在的類別）。"""
    store = load(ds_id)
    clean = validate_classes(names)
    old = list(store.get("classes") or [])
    used = {b["cls"] for im in store["images"].values() for b in im.get("boxes", [])}
    if used and max(used) >= len(clean):
        raise ValueError(
            f"已經有框用到 cls={max(used)}，類別表不能縮到 {len(clean)} 個"
            f"（先把那些框改類別或刪掉）。目前：{old}")
    store["classes"] = clean
    _write(ds_id, store)
    return clean


def get_boxes(ds_id: str, image_id: str) -> dict[str, Any] | None:
    return load(ds_id)["images"].get(image_id)


def save_boxes(ds_id: str, image_id: str, boxes: Any, *, by: str) -> dict[str, Any]:
    if by not in BY_VALUES:
        raise ValueError(f"by 要是 {list(BY_VALUES)} 之一（收到 {by!r}）")
    store = load(ds_id)
    nc = len(store.get("classes") or [])
    if nc == 0:
        raise LookupError("這個 ds 還沒有人工類別表 —— 先 PUT /datasets/{ds}/classes")
    clean = validate_boxes(boxes, nc)
    rec = {"boxes": clean, "by": by, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    store["images"][image_id] = rec
    _write(ds_id, store)
    return rec


def stats(ds_id: str) -> dict[str, Any]:
    store = load(ds_id)
    by: dict[str, int] = {}
    per_class: dict[int, int] = {}
    n_boxes = 0
    for rec in store["images"].values():
        by[rec.get("by", "?")] = by.get(rec.get("by", "?"), 0) + 1
        for b in rec.get("boxes", []):
            n_boxes += 1
            per_class[b["cls"]] = per_class.get(b["cls"], 0) + 1
    names = store.get("classes") or []
    return {
        "classes": names,
        "images_labeled": len(store["images"]),
        "boxes": n_boxes,
        "by": by,
        "per_class": {names[c] if c < len(names) else str(c): n for c, n in sorted(per_class.items())},
    }


# ---------- 三種建議來源 ----------

def suggest_geometry(path: Path) -> list[dict[str, Any]]:
    """現有的連通分量 auto-bbox。`cls` 一律 0 —— 幾何看不出類別，讓專家自己改。"""
    return [{"cls": 0, "cx": b["cx"], "cy": b["cy"], "w": b["w"], "h": b["h"],
             "why": b.get("rule", "cc_bbox")}
            for b in geometry.label_image(path)["boxes"]]


def build_vlm_prompt(rel_path: str, classes: list[str]) -> str:
    table = "、".join(f"{i}={n}" for i, n in enumerate(classes))
    return (
        f"你是晶圓瑕疵標註員。用 Read 工具看這張圖：{rel_path}\n"
        f"圈出圖上每一塊**瑕疵區域**（顏色與周圍不同的點或連成一片的區塊）。\n"
        f"類別索引：{table}\n"
        f"只回一個 JSON 陣列，不要任何其他文字。每個元素：\n"
        f'{{"cls":整數,"cx":0..1,"cy":0..1,"w":0..1,"h":0..1,"why":"繁中十字內理由"}}\n'
        f"cx/cy 是框中心、w/h 是框寬高，**全部正規化到 0..1**（不是像素）。\n"
        f"框要整個落在影像內（cx±w/2 與 cy±h/2 都要在 0..1）。最多 {VLM_MAX_BOXES} 個框。\n"
        f"看不到任何瑕疵就回 []。"
    )


def suggest_vlm(path: Path, classes: list[str], *, timeout: int = VLM_TIMEOUT_S) -> dict[str, Any]:
    """`claude -p` 讀圖回框。回 {boxes, cost_usd, model, duration_ms}。

    刻意在這裡 import `cluster`：那支會拉 scikit-learn，而 geometry 那條路（免費那顆按鈕）
    不需要它。錯誤一律往上丟，router 轉成 `VLM_SUGGEST_FAILED` —— 不要自己降級成幾何框，
    使用者按的是「AI 看圖」，靜默換成連通分量就是在騙人。
    """
    from src.autolabel import cluster  # 延後 import：避免 geometry 那條路被 sklearn 拖慢

    try:
        rel = str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        raise ValueError(f"影像不在專案目錄底下，Read 工具看不到：{path}") from None

    out = cluster.call_llm(build_vlm_prompt(rel, classes), timeout=timeout)
    raw = cluster._parse_json_array(out["text"])
    boxes = validate_boxes(
        [{k: b[k] for k in ("cls", "cx", "cy", "w", "h")} for b in raw[:VLM_MAX_BOXES]],
        len(classes),
    )
    for b, src in zip(boxes, raw):
        why = src.get("why")
        if isinstance(why, str) and why.strip():
            b["why"] = why.strip()[:40]
    return {"boxes": boxes, "cost_usd": out.get("cost_usd", 0.0),
            "model": out.get("model"), "duration_ms": out.get("duration_ms")}


# ---------- 人工 class 真相表（契約 §12 放寬後的 class_source:"human"） ----------

def to_class_table(ds_id: str, *, version: str = "v1") -> dict[str, Any]:
    """把人工類別清單包成 class 真相表。`cluster_stats` 是 null —— 人工標註沒有 silhouette。"""
    st = stats(ds_id)
    names = st["classes"]
    if not names:
        raise LookupError("還沒有人工類別表")
    return {
        "version": version,
        "class_source": "human",
        "nc": len(names),
        "names": names,
        "cluster_stats": None,
        "naming_rationale": [
            {"cluster_id": i, "name": n, "rationale": "人工標註的類別（domain 專家自己定的）",
             "evidence": f"01-raw-data/datasets/{ds_id}/{STORE_NAME}"}
            for i, n in enumerate(names)
        ],
        "montage_urls": [],
    }


# ---------- 自檢 ----------

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import tempfile

    global DS_DIR
    keep = DS_DIR
    with tempfile.TemporaryDirectory(prefix="human-selfcheck-") as tmp:
        DS_DIR = Path(tmp)
        ds = "dsX"

        # 1. 沒類別表不准存框
        try:
            save_boxes(ds, "im1", [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2}], by="manual")
            raise AssertionError("沒有類別表卻存得進去")
        except LookupError:
            pass

        assert set_classes(ds, ["scratch", "donut"]) == ["scratch", "donut"]

        # 2. 存得進去、讀得回來、逐欄相同
        rec = save_boxes(ds, "im1", [{"cls": 1, "cx": .5, "cy": .5, "w": .2, "h": .2}], by="manual")
        got = get_boxes(ds, "im1")
        assert got == rec and got["boxes"][0]["cls"] == 1, got
        assert get_boxes(ds, "沒這張") is None

        # 3. 驗證擋得住四種壞框
        for bad, why in [
            ([{"cls": 9, "cx": .5, "cy": .5, "w": .2, "h": .2}], "cls 超出類別數"),
            ([{"cls": 0, "cx": .95, "cy": .5, "w": .2, "h": .2}], "框超出右邊界"),
            ([{"cls": 0, "cx": .5, "cy": .5, "w": 0, "h": .2}], "寬 0"),
            ([{"cls": 0, "cx": .5, "cy": .5, "w": .2}], "缺 h"),
        ]:
            try:
                validate_boxes(bad, 2)
                raise AssertionError(f"沒擋住：{why}")
            except ValueError:
                pass
        # 整批拒收不是部分寫入：壞的那批存不進去，舊的那筆要原封不動
        try:
            save_boxes(ds, "im1", [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2},
                                   {"cls": 5, "cx": .5, "cy": .5, "w": .2, "h": .2}], by="manual")
            raise AssertionError("壞批次寫進去了")
        except ValueError:
            pass
        assert get_boxes(ds, "im1") == rec, "壞批次污染了舊資料"

        # 4. 類別表不准縮到讓既有框指空
        try:
            set_classes(ds, ["scratch"])
            raise AssertionError("縮短類別表沒被擋")
        except ValueError:
            pass
        assert set_classes(ds, ["刮痕", "甜甜圈"]) == ["刮痕", "甜甜圈"], "改名應該要過"

        # 5. 壞檔自癒（半份 JSON = 當作沒標過，不是 500）
        store_path(ds).write_text('{"version":1,"images":{', "utf-8")
        assert load(ds) == _empty(), "壞檔沒有自癒"

        # 6. class 表形狀（class_source=human、cluster_stats 是 null）
        set_classes(ds, ["Donut"])
        t = to_class_table(ds)
        assert t["class_source"] == "human" and t["nc"] == 1 and t["cluster_stats"] is None, t
        assert len(t["naming_rationale"]) == 1 and t["montage_urls"] == [], t

        # 7. stats 數得對（壞檔自癒的代價寫在這裡：im1 跟著沒了，這是刻意的 ——
        #    半份 JSON 沒有安全的合併方式，硬接回去只會得到一份說謊的標註）
        set_classes(ds, ["a", "b"])
        assert get_boxes(ds, "im1") is None, "自癒之後舊紀錄不該還在"
        save_boxes(ds, "im2", [{"cls": 0, "cx": .3, "cy": .3, "w": .1, "h": .1},
                               {"cls": 1, "cx": .7, "cy": .7, "w": .1, "h": .1}], by="vlm")
        save_boxes(ds, "im3", [{"cls": 0, "cx": .5, "cy": .5, "w": .1, "h": .1}], by="manual")
        s = stats(ds)
        assert s["images_labeled"] == 2 and s["boxes"] == 3, s
        assert s["by"] == {"manual": 1, "vlm": 1}, s
        assert s["per_class"] == {"a": 2, "b": 1}, s

        # 8. VLM prompt 帶得到類別索引與正規化要求
        p = build_vlm_prompt("01-raw-data/demo/wafer_000.png", ["a", "b"])
        assert "0=a、1=b" in p and "0..1" in p and str(VLM_MAX_BOXES) in p, p

    DS_DIR = keep
    print("human selfcheck PASS（落檔 / 驗證 / 類別表 / 自癒 / stats / prompt 共 8 組）")


if __name__ == "__main__":
    _selfcheck()
