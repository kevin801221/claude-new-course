"""s04 落檔成 dataset（含硬閘門）：GT 三分 → 分層抽樣 → 寫 `02-dataset/` → selfcheck 清單。

DESIGN s04 / 契約 `_Context/api-contract.md` §4（`ds.split` / `ds.gt_partition` / `ds.written` /
`ds.selfcheck` 四個事件的欄位名）。擁有者：dataset-truth。

四條鐵律（任一條破了就是整條實驗白跑，所以都有對應的 selfcheck 項目）：
1. **`data.yaml` 只有 train / valid，`test` 一個字都不准出現** —— 封印 test 的第一道鎖是
   「訓練設定裡看不到它」。sealed-test 落在 `03-sealed-test/`，只有 `/final-test` 讀得到。
2. **anchor 與 sealed-test 用免費精確 GT 的框**，train/valid 用 auto-label 的框。
   anchor 是每輪量漂移的尺，尺不能跟著被量的東西一起動。
3. **分層抽樣沿用 `split.stratified_split`**（與前端 `stratifiedSplit` 逐筆相同），不另寫一套。
4. 類別只有一個來源：`class_table.json` 的 `names` 索引。data.yaml 的 nc/names 逐字照抄它。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import demo, roboflow_src, split as split_mod

PROJECT_ROOT = demo.PROJECT_ROOT
DATASET_DIR = PROJECT_ROOT / "02-dataset"
SEALED_DIR = PROJECT_ROOT / "03-sealed-test"

# GT 三分的上限（DESIGN 寫死的 40 / 80）。**上限不是固定值**：
# 分母是 Roboflow 那 409 張時 `409//6=68→40`、`409//3=136→80`、其餘 289 進 train+valid，
# 剛好就是 DESIGN 的 40/80/289；分母是離線 demo 的 120 張時退成 20/40/60。
# 寫成固定 40+80 的話，120 張會被切到 train+valid 剩 0 張 —— 那不是封印，那是沒有資料集。
ANCHOR_CAP, SEALED_CAP = 40, 80
ANCHOR_DIV, SEALED_DIV = 6, 3

# 抽樣比例：**test 一律 0**（最大餘數法在餘數 0 的那一份不會補，實測見 selfcheck 的 no_test 那條）
DEFAULT_RATIOS = {"train": 80, "valid": 20, "test": 0}
SPLITS = ("train", "valid")


def gt_partition(ids: list[str]) -> dict[str, list[str]]:
    """GT 三分：`anchor`（每輪量漂移）/ `sealed_test`（整個 run 家族只開封一次）/ `pool`（進 train+valid）。

    照 id 順序切（`demo0001` 起），不是隨機 —— 合成資料的型態是輪流發的，照順序切每份自動型態均衡，
    而且**同一個 ds 重跑會切到同一批**（sealed test 換一批就等於偷偷換考卷）。
    """
    n = len(ids)
    a, s = min(ANCHOR_CAP, n // ANCHOR_DIV), min(SEALED_CAP, n // SEALED_DIV)
    return {"anchor": ids[:a], "sealed_test": ids[a : a + s], "pool": ids[a + s :]}


def yolo_lines(boxes: list[dict[str, Any]], cls: int) -> list[str]:
    """YOLO 一行 = `cls cx cy w h`（正規化，契約 §1 的座標慣例）。

    `cls` 是**這張圖的類別**（auto-label 那條路每張只有一個類別：框來自幾何、類別來自分群）。
    人工標註（`labels:"human"`，Roboflow 自帶 bbox）是**逐框帶自己的類別**的，一張圖可以
    同時有兩類 —— 框自己帶 `cls >= 0` 時以框為準，硬套參數的 `cls` 會把多類圖壓成單類，
    落檔出去的 GT 就和人工標的不一樣。auto 的框依契約 §8.6 一律 `cls = -1`（還沒有類別）、
    `gt.json` 的框沒有 `cls` 鍵 —— 兩者都走參數那條，行為和改這行之前逐位元組相同。
    """
    return [
        f"{b['cls'] if b.get('cls', -1) >= 0 else cls} "
        f"{b['cx']:.6f} {b['cy']:.6f} {b['w']:.6f} {b['h']:.6f}"
        for b in boxes
        if b["w"] > 0 and b["h"] > 0
    ]


def _write_pair(img_src: Path, img_dir: Path, lbl_dir: Path, lines: list[str]) -> None:
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(img_src, img_dir / img_src.name)
    (lbl_dir / f"{img_src.stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")


def write_data_yaml(path: Path, names: list[str]) -> str:
    """`02-dataset/data.yaml`。手寫而不是 `yaml.dump`：PyYAML 不在 base 依賴裡（只有 train extra 有）。

    **註解裡一個 `test` 字都不准出現**（連「sealed-test 在哪」這種善意說明也不行）：驗收閘門是
    `grep -i test 02-dataset/data.yaml` 要空的，而 grep 不會幫你分辨那是 key 還是註解。
    封印本身靠的是「沒有那個 key」，這條只是不讓閘門被註解噪音吵到失效。

    **刻意沒有 `path:`** —— ultralytics 的相對 `path` 會相對到它自己的 `DATASETS_DIR`
    而不是這個 yaml 的目錄（`ultralytics/data/utils.py` 第 623–625 行），省略時才會用
    `Path(yaml_file).parent`。寫 `path: .` 會在別人的 cwd 下靜默指到錯的地方。
    """
    body = (
        "# 由 POST /api/v1/datasets/{ds}/freeze 產生，不要手改（改了 provenance 會對不上）。\n"
        "# 目錄名一律 valid（前端形狀勝）；key 用 val 是因為 ultralytics 只認 val。\n"
        "# 這個檔案只有 train / valid 兩份；封印起來的第三份不寫在這裡，\n"
        "# 只有專屬的開封端點讀得到，而且整個 run 家族只開封一次。\n"
        "# 版本與內容指紋見同目錄的 dataset_version.json。\n"
        "train: images/train\n"
        "val: images/valid\n"
        f"nc: {len(names)}\n"
        "names:\n" + "".join(f"  - {n}\n" for n in names)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, "utf-8")
    return body


def parse_data_yaml(path: Path) -> dict[str, Any]:
    """只認得自己寫出去的那五個鍵（不引 PyYAML）。selfcheck 用它把檔案**真的讀回來**比對。"""
    out: dict[str, Any] = {"names": []}
    for raw in path.read_text("utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            out["names"].append(line[4:].strip())
        elif ":" in line:
            k, v = line.split(":", 1)
            if k.strip() == "names":  # 「names:」那一行沒有值，值在下面的 `  - ` 清單裡
                continue
            out[k.strip()] = v.strip()
    if "nc" in out:
        out["nc"] = int(out["nc"])
    return out


# ---------- ds_version：綁內容，不綁「人填過一次」 ----------

VERSION_FILE = "dataset_version.json"


def labels_fingerprint(dataset_dir: Path = DATASET_DIR, splits: Sequence[str] = SPLITS) -> str:
    """`02-dataset/labels/{train,valid}` 的檔名 + 內容 sha1 前 16 碼。

    **演算法與 `src/eval/metrics.dataset_fingerprint()` 逐位元組相同**（那支是 metric-auditor 的，
    吃寫死的 `02-dataset` 路徑，落檔自檢用的暫存目錄餵不進去，所以這裡另寫一份）。
    兩份會不會走鐘？`_selfcheck()` 最後一條就是拿真的 `02-dataset` 讓兩支算一次比對，
    哪天有人改了其中一支，那條會紅。
    """
    h = hashlib.sha1()
    for split in splits:
        for txt in sorted((dataset_dir / "labels" / split).glob("*.txt")):
            h.update(txt.name.encode("utf-8"))
            h.update(txt.read_bytes())
    return h.hexdigest()[:16]


def assign_version(dataset_dir: Path = DATASET_DIR) -> dict[str, Any]:
    """發這次 freeze 的 `ds_version`，寫進 `02-dataset/dataset_version.json` 並回那筆紀錄。

    修的是什麼：`ds_version` 以前是 `registry.create_run()` 寫死的 `"v1"`，freeze 幾次都不會動 ——
    實測 2026-09-12 00:48 有人重 freeze、valid 的 GT 框從 13 變 15，版本欄位毫無反應，
    「跨版本不可比」那道鎖因此是死的。現在版本號由**落檔後的內容**決定：
    **一個指紋一個號，同一個號永遠指同一份內容**（`versions` 是指紋 → 版本的全表）。

    為什麼不是「每次 freeze 一律 +1」：版本號的用途是當雜訊帶與跨輪 Δ 的鍵
    （`04-experiments/noise-floor/<ds_version>.json`）。內容一字不差卻換號 = 每按一次那顆按鈕
    就作廢一次雜訊帶、白燒 3 次訓練（實測 65 秒），而且「一發出的版本不准改內容」會被稀釋成
    「版本號只是流水號」。

    為什麼不只是「跟上一筆比、不同就 +1」：那樣改回舊資料會拿到新號（v1 → v2 → 改回來變 v3），
    下一次真的改資料又會發 v2 —— **同一個號指到兩份不同的內容**，而 `noise-floor/v2.json`
    還躺在那裡。全表查得起（一次 freeze 幾十個 byte），就不要留這個坑。
    """
    path = dataset_dir / VERSION_FILE
    old = json.loads(path.read_text("utf-8")) if path.exists() else {}
    issued: dict[str, str] = dict(old.get("versions") or {})
    fingerprint = labels_fingerprint(dataset_dir)
    version = issued.get(fingerprint)
    if version is None:
        version = f"v{max((int(v.lstrip('v')) for v in issued.values()), default=0) + 1}"
        issued[fingerprint] = version
    record = {
        "ds_version": version,
        "ds_fingerprint": fingerprint,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "versions": issued,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")
    return record


# ---------- 主流程 ----------

def freeze_dataset(
    *,
    ids: list[str],
    class_table: dict[str, Any],
    cluster_of: dict[str, int],
    auto_boxes: dict[str, list[dict[str, Any]]],
    gt_boxes: dict[str, list[dict[str, Any]]],
    seed: int = 42,
    ratios: dict[str, float] | None = None,
    dataset_dir: Path = DATASET_DIR,
    sealed_dir: Path = SEALED_DIR,
) -> dict[str, Any]:
    """把一個 ds 落成 `02-dataset/` + `03-sealed-test/`，回 {counts, split, partition, data_yaml, ...}。

    `cluster_of` 缺鍵、或該群被 `excluded` 的圖 → 沒有類別 → 不落檔，計入 `unassigned`。
    """
    names = class_table["names"]
    cls_of_cluster = {r["cluster_id"]: i for i, r in enumerate(class_table["naming_rationale"])}

    part = gt_partition(ids)
    assigned = {i: cls_of_cluster.get(cluster_of.get(i, -1)) for i in ids}
    unassigned = [i for i in part["pool"] if assigned[i] is None]
    pool = [i for i in part["pool"] if assigned[i] is not None]

    # 分層抽樣沿用 split.py（與前端 stratifiedSplit 逐筆相同）。分層鍵 = 類別名。
    items = [{"id": i, "cls": [names[assigned[i]]]} for i in pool]
    where = split_mod.stratified_split(items, ratios or DEFAULT_RATIOS, seed)

    # 落檔前先清掉舊的，否則上一次 freeze 的檔案會留在目錄裡，張數對不上還很難查
    for d in (dataset_dir / "images", dataset_dir / "labels", sealed_dir):
        if d.exists():
            shutil.rmtree(d)

    counts = {"train": 0, "valid": 0, "anchor": 0, "sealed_test": 0}
    for image_id, where_ in where.items():
        if where_ not in SPLITS:  # test 比例是 0，理論上進不來；真的進來就是 split 壞了
            raise ValueError(f"{image_id} 被分到 {where_!r} —— data.yaml 只准有 train/valid")
        src = roboflow_src.path_of(image_id)  # demo 與 roboflow 共用同一支解析
        if src is None:  # 01-raw-data/ 被刪掉時要講人話，不要 AttributeError
            raise ValueError(
                f"{image_id} 的影像檔不見了（demo 先跑 scripts/gen_demo_wafers.py；"
                f"roboflow 先跑 POST /api/v1/datasets/ingest 重抓）"
            )
        _write_pair(
            src,
            dataset_dir / "images" / where_,
            dataset_dir / "labels" / where_,
            yolo_lines(auto_boxes.get(image_id, []), assigned[image_id]),
        )
        counts[where_] += 1

    # anchor / sealed-test 用**免費精確 GT 的框**（auto 的框是被量的對象，不能同時當尺）
    for kind, out_img, out_lbl in (
        ("anchor", dataset_dir / "images" / "anchor", dataset_dir / "labels" / "anchor"),
        ("sealed_test", sealed_dir / "images", sealed_dir / "labels"),
    ):
        for image_id in part[kind]:
            cls, src = assigned[image_id], roboflow_src.path_of(image_id)
            if cls is None or src is None:
                continue
            _write_pair(src, out_img, out_lbl, yolo_lines(gt_boxes.get(image_id, []), cls))
            counts[kind] += 1

    data_yaml = dataset_dir / "data.yaml"
    write_data_yaml(data_yaml, names)
    (sealed_dir / "manifest.json").write_text(
        json.dumps(
            {
                "class_table_version": class_table["version"],
                "count": counts["sealed_test"],
                "ids": part["sealed_test"],
                "note": "封印 test：不進 data.yaml，只有 POST /api/v1/final-test 讀得到，整個 run 家族只開封一次",
            },
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )

    version = assign_version(dataset_dir)  # 落檔完才算得出指紋，所以發號在最後

    return {
        "ds_version": version["ds_version"],
        "ds_fingerprint": version["ds_fingerprint"],
        "counts": counts,
        "split": {"train": counts["train"], "valid": counts["valid"], "unassigned": len(unassigned)},
        "partition": {
            "anchor": counts["anchor"],
            "sealed_test": counts["sealed_test"],
            "unused": len(part["pool"]),
        },
        "assignment": where,
        "unassigned_ids": unassigned,
        "data_yaml": rel(data_yaml),
        "path": rel(dataset_dir),
        "sealed_path": rel(sealed_dir),
    }


# ---------- 硬閘門：selfcheck 清單（任一 FAIL → 前端訓練鈕 disabled、/train 回 409） ----------

def rel(p: Path) -> str:
    """相對 `PROJECT_ROOT` 的路徑（事件與回應一律不吐絕對路徑）。自檢的暫存目錄不在樹裡就原樣回。"""
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def _files(d: Path, suffix: str) -> list[Path]:
    return sorted(d.glob(f"*{suffix}")) if d.exists() else []


# 影像的副檔名**不准寫死 `.png`**：demo 是 png、Roboflow 的 yolov8 export 是 jpg。
# 寫死的後果不是「少驗一條」而是四條一起錯：`dirs_exist` 回「0 圖」、`yolo_format` 說
# 每一張標註都「沒有對應影像」、`train_valid_disjoint` 與 `sealed_isolated` 拿空集合比 ——
# 四條 FAIL 全指錯方向，而真因只是副檔名。
IMG_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")


def _images(d: Path) -> dict[str, Path]:
    """`{檔名主幹: 路徑}`。主幹是 YOLO 的配對鍵（`x.jpg` ↔ `x.txt`），所以用它當鍵。"""
    return {p.stem: p for p in sorted(d.iterdir()) if p.suffix.lower() in IMG_SUFFIXES} \
        if d.is_dir() else {}


def images_in(d: Path) -> list[Path]:
    """目錄裡的影像檔，檔名排序。**全系統唯一一份影像清單**——上面那段警語不只適用 freeze：
    `eval/checks.py`、`eval/infer.py`、`routers/train.py`、`scripts/train_yolo.py` 原本各自
    寫死 `glob("*.png")`，Roboflow 的 jpg 一進來就全部讀到 0 張，而錯誤訊息說的是
    「讀不到影像（train 0 / valid 0）」——指向 split 而不是副檔名。一處修全部修。
    """
    return sorted(_images(d).values())


def selfcheck(
    class_table: dict[str, Any], dataset_dir: Path = DATASET_DIR, sealed_dir: Path = SEALED_DIR
) -> list[dict[str, Any]]:
    """回 `[{name, pass, detail}]`（契約 §4 的 `ds.selfcheck` 形狀）。**從磁碟真的讀回來驗**。"""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "pass": bool(ok), "detail": detail})

    yaml_path = dataset_dir / "data.yaml"
    if not yaml_path.exists():
        add("data_yaml_exists", False, f"{yaml_path} 不存在")
        return checks
    y = parse_data_yaml(yaml_path)
    nc, names = class_table["nc"], class_table["names"]

    add(
        "class_table_match",
        y.get("nc") == nc and y.get("names") == names,
        f"data.yaml nc={y.get('nc')} names={y.get('names')} vs class_table {class_table['version']} "
        f"nc={nc} names={names}",
    )

    raw = yaml_path.read_text("utf-8")
    body = "\n".join(ln.split("#", 1)[0] for ln in raw.splitlines())
    # 驗收閘門是 `grep -i test 02-dataset/data.yaml` 要空的 —— grep 不分 key 與註解，
    # 所以這條也看**整份原文**（含註解、不分大小寫），跟閘門量同一件事。
    hits = [ln for ln in raw.splitlines() if "test" in ln.lower()]
    add(
        "no_test_in_data_yaml",
        not hits and "val: images/valid" in body,
        f"整份 data.yaml（含註解）grep -i test 命中 {len(hits)} 行"
        + (f"：{hits[0][:60]}" if hits else "")
        + "，val 指向 images/valid（封印 test 第一道鎖）",
    )

    imgs_of = {s: _images(dataset_dir / "images" / s) for s in SPLITS}
    sizes = {}
    for s in SPLITS:
        sizes[s] = (len(imgs_of[s]), len(_files(dataset_dir / "labels" / s, ".txt")))
    add(
        "dirs_exist",
        all(i > 0 and i == l for i, l in sizes.values()),
        "、".join(f"{s}: {i} 圖 / {l} 標" for s, (i, l) in sizes.items()),
    )

    bad: list[str] = []
    lines_total = 0
    for s in SPLITS:
        for lbl in _files(dataset_dir / "labels" / s, ".txt"):
            if lbl.stem not in imgs_of[s]:
                bad.append(f"{lbl.name} 沒有對應影像")
            for ln, line in enumerate(lbl.read_text("utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                lines_total += 1
                parts = line.split()
                if len(parts) != 5:
                    bad.append(f"{lbl.name}:{ln} 欄位數 {len(parts)} != 5")
                    continue
                c, *xy = parts
                if not c.isdigit() or not 0 <= int(c) < nc:
                    bad.append(f"{lbl.name}:{ln} cls={c} 不在 0..{nc - 1}")
                v = [float(x) for x in xy]
                if not all(0.0 <= x <= 1.0 for x in v) or v[2] <= 0 or v[3] <= 0:
                    bad.append(f"{lbl.name}:{ln} 座標 {v} 不合法")
    add("yolo_format", not bad, f"{lines_total} 行全部合法" if not bad else "；".join(bad[:5]))

    ids = {s: set(imgs_of[s]) for s in SPLITS}
    overlap = ids["train"] & ids["valid"]
    digest = {
        s: {hashlib.sha1(p.read_bytes()).hexdigest() for p in imgs_of[s].values()}
        for s in SPLITS
    }
    dup = digest["train"] & digest["valid"]
    add(
        "train_valid_disjoint",
        not overlap and not dup,
        f"train {len(ids['train'])} / valid {len(ids['valid'])}，重複 id {len(overlap)} 個、"
        f"逐位元組相同的圖 {len(dup)} 對（近重複的 M2 版；pHash 等 M8 真資料）",
    )

    sealed_ids = set(_images(sealed_dir / "images"))
    anchor_ids = set(_images(dataset_dir / "images" / "anchor"))
    leak = sealed_ids & (ids["train"] | ids["valid"] | anchor_ids)
    add(
        "sealed_isolated",
        bool(sealed_ids) and not leak,
        f"sealed-test {len(sealed_ids)} 張、anchor {len(anchor_ids)} 張，"
        f"與 train/valid/anchor 的交集 {len(leak)} 張（要是 0）",
    )
    return checks


def all_pass(checks: list[dict[str, Any]]) -> bool:
    return all(c["pass"] for c in checks)


# ---------- 自檢：`uv run python -m src.autolabel.freeze` ----------
# 這條用假的 class 表 + 假的框跑一遍完整落檔，驗四件事：GT 三分的算術、
# data.yaml 沒有 test、selfcheck 全 PASS，以及**故意弄壞一件事之後 selfcheck 真的會 FAIL**。

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import tempfile

    gt = demo.ensure()
    ids = [r["id"] for r in gt["images"]]
    gt_boxes = {r["id"]: r["boxes"] for r in gt["images"]}

    # 1. GT 三分的算術：120 張 → 20/40/60；DESIGN 的 409 張 → 40/80/289（上限才咬得到）
    p120 = gt_partition(ids)
    assert [len(p120[k]) for k in ("anchor", "sealed_test", "pool")] == [20, 40, 60], p120
    p409 = gt_partition([f"x{i}" for i in range(409)])
    assert [len(p409[k]) for k in ("anchor", "sealed_test", "pool")] == [40, 80, 289], "409 張要退回 DESIGN 的 40/80"
    assert not (set(p120["anchor"]) & set(p120["sealed_test"]) & set(p120["pool"]))

    names = ["scratch", "donut", "edge-ring", "random"]
    table = {
        "version": "v1", "nc": 4, "names": names,
        "cluster_stats": {"k": 4, "silhouette": 0.4, "clusters": []},
        "naming_rationale": [{"cluster_id": c, "name": n, "rationale": "x", "evidence": "y"} for c, n in enumerate(names)],
        "montage_urls": [],
    }
    cluster_of = {i: k % 4 for k, i in enumerate(ids)}
    auto = {i: [{"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.1}] for i in ids}

    tmp = Path(tempfile.mkdtemp(prefix="freeze-selfcheck-"))
    out = freeze_dataset(
        ids=ids, class_table=table, cluster_of=cluster_of, auto_boxes=auto, gt_boxes=gt_boxes,
        dataset_dir=tmp / "02-dataset", sealed_dir=tmp / "03-sealed-test",
    )
    assert out["split"]["train"] + out["split"]["valid"] == 60, out["split"]
    assert out["partition"] == {"anchor": 20, "sealed_test": 40, "unused": 60}, out["partition"]

    checks = selfcheck(table, tmp / "02-dataset", tmp / "03-sealed-test")
    for c in checks:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['name']}: {c['detail'][:100]}")
    assert all_pass(checks), "乾淨的落檔不該有 FAIL"

    # 1.5 版本號誠實：連續兩次 freeze，資料沒變 → 同號同指紋；資料變了 → 換號換指紋。
    #     這條就是在守「ds_version 說謊」那個 blocker（重 freeze、GT 框 13 → 15，版本欄位不動）。
    assert out["ds_version"] == "v1" and len(out["ds_fingerprint"]) == 16, out["ds_version"]
    kw = dict(ids=ids, class_table=table, cluster_of=cluster_of, gt_boxes=gt_boxes,
              dataset_dir=tmp / "02-dataset", sealed_dir=tmp / "03-sealed-test")
    same = freeze_dataset(auto_boxes=auto, **kw)
    assert (same["ds_version"], same["ds_fingerprint"]) == ("v1", out["ds_fingerprint"]), \
        f"資料一字沒變卻換了號／指紋：{same['ds_version']} {same['ds_fingerprint']}"
    moved = {**auto, ids[-1]: [{"cx": 0.4, "cy": 0.4, "w": 0.3, "h": 0.2}]}  # 動一張圖的框
    bumped = freeze_dataset(auto_boxes=moved, **kw)
    assert bumped["ds_fingerprint"] != out["ds_fingerprint"], "改了一個框，指紋卻沒動"
    assert bumped["ds_version"] == "v2", f"資料變了版本號要跟著變，收到 {bumped['ds_version']}"
    again = freeze_dataset(auto_boxes=moved, **kw)
    assert (again["ds_version"], again["ds_fingerprint"]) == ("v2", bumped["ds_fingerprint"]), again
    back = freeze_dataset(auto_boxes=auto, **kw)  # 改回原本的框 → 內容就是 v1，號要拿回 v1
    assert (back["ds_version"], back["ds_fingerprint"]) == ("v1", out["ds_fingerprint"]), \
        f"改回舊內容卻發了新號 {back['ds_version']} —— 下次真的改資料就會重發 v2 撞到舊的雜訊帶"
    # 指紋這支是抄 metric-auditor 的演算法重寫的 —— 拿真的 02-dataset 讓兩支各算一次比對，走鐘就紅
    if (DATASET_DIR / "labels" / "train").exists():
        from src.eval import metrics
        assert labels_fingerprint(DATASET_DIR) == metrics.dataset_fingerprint(), \
            "freeze.labels_fingerprint 與 metrics.dataset_fingerprint 算出不同指紋（兩把尺）"

    # 2. 真的會失敗：把一行 YOLO 標成不存在的 cls、把一張 train 的圖複製到 valid
    lbl = next(iter((tmp / "02-dataset" / "labels" / "train").glob("*.txt")))
    lbl.write_text("9 0.5 0.5 0.2 0.1\n", "utf-8")
    shutil.copyfile(
        tmp / "02-dataset" / "images" / "train" / f"{lbl.stem}.png",
        tmp / "02-dataset" / "images" / "valid" / f"{lbl.stem}.png",
    )
    broken = {c["name"]: c["pass"] for c in selfcheck(table, tmp / "02-dataset", tmp / "03-sealed-test")}
    assert broken["yolo_format"] is False, "cls=9 沒有被抓到"
    assert broken["train_valid_disjoint"] is False, "train/valid 重複沒有被抓到"

    # 3. data.yaml 真的讀得回來，而且**整份原文**（含註解）grep -i test 是空的（驗收閘門那條）
    y = parse_data_yaml(tmp / "02-dataset" / "data.yaml")
    assert y["nc"] == 4 and y["names"] == names and y["val"] == "images/valid" and "test" not in y, y
    raw = (tmp / "02-dataset" / "data.yaml").read_text("utf-8")
    assert "test" not in raw.lower(), [ln for ln in raw.splitlines() if "test" in ln.lower()]
    print(f"freeze selfcheck PASS（暫存目錄 {tmp}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
