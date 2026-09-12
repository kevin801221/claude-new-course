"""第二階段 auto-label：**類別從哪來**（幾何描述子 → KMeans → montage → 一次 LLM 命名）。

DESIGN「資料真相與零標註專家」規格 (1) 第二階段寫死的流程：
    每張圖算幾何描述子（徑向分佈直方圖 8 bin、ring_ness = 分量平均半徑/標準差、center_ness、
    PCA 長短軸比、邊緣佔比、分量數、總缺陷率）
    → sklearn KMeans（k 由 silhouette 在 3–6 之間自選）
    → 每群抽 9 張代表圖拼 montage
    → **一次** LLM 呼叫看 montage + 群統計後給每群命名

契約：`_Context/api-contract.md` §4（`label.descriptor` / `class.cluster` / `class.table.frozen`
三個事件的欄位名）、`_Context/class_table.schema.json`（class 真相表欄位，console-owner 凍結，
這裡**只填值不改欄位**）。擁有者：dataset-truth。

**這裡讀的是像素，不是答案**：`gt.json` 的 `shape` 只有本檔最下面的 `_selfcheck()` 會讀
（事後驗證分群純度），分群與命名全程看不到它。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from ..app import procs
from . import demo, geometry

PROJECT_ROOT = demo.PROJECT_ROOT

K_RANGE = (3, 4, 5, 6)       # schema 的 nc 上下界就是這個範圍（class_table.schema.json）
RADIAL_BINS = 8              # 徑向分佈直方圖 8 bin（DESIGN 寫死）
EDGE_R = 0.75                # 「靠邊」的定義：r > 0.75R（與 dataset-notes 的 edge_frac 同一個數）
MONTAGE_GRID = 3             # 3×3 = 每群 9 張代表圖（DESIGN 寫死）
MONTAGE_GAP = 4
MIN_CLUSTER_SIZE = 5         # 小於這個就 excluded（schema 允許，且要寫 exclude_reason）
KMEANS_SEED = 42

# class_table.schema.json 凍結的固定詞彙表：LLM 只能從這六個挑，不准自己發明新詞
VOCAB = ("center", "donut", "edge-ring", "edge-loc", "scratch", "random")

# 一次 LLM 呼叫的旋鈕（M2 唯一的 LLM 呼叫；失敗要降級，不准整條線掛掉）
LLM_CMD = "claude"
# 命名是「看圖 + 引數字寫一行理由」，sonnet 綽綽有餘：實測同一個 prompt opus $0.134 / sonnet $0.077，
# 而 teaching preset 的整輪 usd_cap 只有 0.5（registry.PRESET_BUDGETS）。要換模型用 CV_LLM_MODEL。
LLM_MODEL = "sonnet"
LLM_TIMEOUT_S = 240
# 一定要夠它把每一群的 montage 都 Read 完再回答：6 群 = 6 次 Read + 最後一輪回答。
# 原本寫 4，實測本機兩次都撞上限 → `is_error: true`（result 是 null）→ 整條命名降級成機械命名，
# 而 s02 的教學主張正是「AI 自己命名」。留一點餘裕，用不到就不會花。
LLM_MAX_TURNS = 12
NAMING_LLM = "llm"
NAMING_FALLBACK = "fallback"

# 背景色取 design-system tokens 的 --ds-navy-900（montage 的格線縫隙，不自己發明 hex）
MONTAGE_BG = (0x14, 0x1B, 0x3D)


# ---------- 幾何描述子（純像素，零類別、零預訓練權重） ----------

def _wafer_frame(inside: np.ndarray) -> tuple[float, float, float]:
    """晶圓的圓心與半徑**從像素推**（質心 + 最大半徑），不硬編 128,128 —— 真實影像不會剛好置中。"""
    ys, xs = np.nonzero(inside)
    if len(xs) == 0:
        h, w = inside.shape
        return w / 2, h / 2, max(w, h) / 2
    cx, cy = float(xs.mean()), float(ys.mean())
    r = np.hypot(xs - cx, ys - cy)
    return cx, cy, max(float(r.max()), 1.0)


def describe_array(rgb: np.ndarray, p: geometry.RefineParams = geometry.DEFAULT_PARAMS) -> dict[str, Any]:
    """一張圖 → 七個幾何描述子（+ 8 bin 徑向直方圖）。缺陷遮罩沿用 geometry 那三支，不另寫一套。"""
    inside = geometry.inside_mask(rgb, p)
    mask = geometry.despeckle(geometry.defect_mask(rgb, p) & inside, p)
    _, n_comp = ndimage.label(mask, structure=np.ones((3, 3), int))
    inside_px = int(inside.sum()) or rgb.shape[0] * rgb.shape[1]

    ys, xs = np.nonzero(mask)
    if len(xs) == 0:  # 整張沒有缺陷：描述子全 0，KMeans 照樣吃得下
        return {
            "radial_hist": [0.0] * RADIAL_BINS, "ring_ness": 0.0, "center_ness": 0.0,
            "elongation": 1.0, "edge_frac": 0.0, "n_components": 0, "defect_ratio": 0.0,
            "mean_r": 0.0, "std_r": 0.0,
        }

    cx, cy, R = _wafer_frame(inside)
    r = np.hypot(xs - cx, ys - cy) / R          # 正規化半徑（0 = 圓心、1 = 晶圓邊）
    mean_r, std_r = float(r.mean()), float(r.std())

    hist, _ = np.histogram(np.clip(r, 0, 0.999), bins=RADIAL_BINS, range=(0.0, 1.0))
    radial_hist = (hist / max(hist.sum(), 1)).astype(float)

    # PCA 長短軸比：把缺陷像素座標的共變異數矩陣拆特徵值。scratch 會很大、ring 接近 1
    cov = np.cov(np.stack([xs - xs.mean(), ys - ys.mean()]).astype(float))
    ev = np.sort(np.linalg.eigvalsh(np.atleast_2d(cov)))[::-1]
    elongation = float(np.clip(np.sqrt(max(ev[0], 0.0) / max(ev[-1], 1e-6)), 1.0, 50.0))

    return {
        "radial_hist": [round(float(v), 6) for v in radial_hist],
        # ring_ness = 平均半徑 / 標準差（DESIGN 寫死的定義）：環帶集中 → 大；散開 → 小
        "ring_ness": round(float(np.clip(mean_r / max(std_r, 1e-6), 0.0, 20.0)), 4),
        "center_ness": round(1.0 - mean_r, 4),
        "elongation": round(elongation, 4),
        "edge_frac": round(float((r > EDGE_R).mean()), 4),
        "n_components": int(n_comp),
        "defect_ratio": round(float(mask.sum()) / inside_px, 6),
        "mean_r": round(mean_r, 4),
        "std_r": round(std_r, 4),
    }


def describe_image(path: Path, p: geometry.RefineParams = geometry.DEFAULT_PARAMS) -> dict[str, Any]:
    with Image.open(path) as im:
        return describe_array(np.asarray(im.convert("RGB")), p)


FEATURE_KEYS = ("ring_ness", "center_ness", "elongation", "edge_frac", "n_components", "defect_ratio")


def feature_vector(d: dict[str, Any]) -> np.ndarray:
    """描述子 → KMeans 的輸入向量（8 bin 直方圖 + 6 個純量 = 14 維）。scaler 之後才進 KMeans。"""
    return np.array(list(d["radial_hist"]) + [float(d[k]) for k in FEATURE_KEYS], dtype=float)


# ---------- KMeans + silhouette 自選 k ----------

def cluster_descriptors(
    descriptors: list[dict[str, Any]], seed: int = KMEANS_SEED
) -> dict[str, Any]:
    """k 由 silhouette 在 3–6 之間自選。回 {k, silhouette, silhouette_by_k, labels, order}。

    `order` 是「群 id 重新編號」的對照：**照群平均半徑由小到大重排**，讓 cluster_0 永遠是最靠中心
    的那群。KMeans 的 label 編號本身沒有意義（換 seed 就換號），不重排的話 class id 會在兩次
    freeze 之間無聲換位 —— 而 class id 換位等於把所有既有 label 的意義換掉。
    """
    X = StandardScaler().fit_transform(np.stack([feature_vector(d) for d in descriptors]))
    by_k: dict[int, float] = {}
    best: tuple[float, int, np.ndarray] | None = None
    for k in K_RANGE:
        if k >= len(X):
            continue
        labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(X)
        score = float(silhouette_score(X, labels))
        by_k[k] = round(score, 4)
        if best is None or score > best[0]:
            best = (score, k, labels)
    if best is None:
        raise ValueError(f"樣本數 {len(X)} 太少，k∈{K_RANGE} 一個都跑不動")

    score, k, labels = best
    mean_r = [float(np.mean([descriptors[i]["mean_r"] for i in np.nonzero(labels == c)[0]])) for c in range(k)]
    order = np.argsort(mean_r)                      # 舊 label → 新 id 的排序
    remap = {int(old): new for new, old in enumerate(order)}
    return {
        "k": k,
        "silhouette": round(score, 4),
        "silhouette_by_k": by_k,
        "labels": [remap[int(v)] for v in labels],
        "X": X,
    }


def cluster_members(labels: list[int], k: int) -> list[list[int]]:
    return [[i for i, c in enumerate(labels) if c == cid] for cid in range(k)]


def representative(X: np.ndarray, idxs: list[int], n: int) -> list[int]:
    """離群心最近的 n 張（montage 要有代表性，不是隨便挑前 9 張）。"""
    center = X[idxs].mean(0)
    return sorted(idxs, key=lambda i: float(np.linalg.norm(X[i] - center)))[:n]


# ---------- montage（每群 9 張代表圖） ----------

def write_montage(paths: list[Path], out: Path, cell: int = 160) -> Path:
    """3×3 拼圖。不足 9 張就留底色，不重複填 —— 重複填會讓人以為那群比實際大。"""
    g, gap = MONTAGE_GRID, MONTAGE_GAP
    side = g * cell + (g + 1) * gap
    canvas = Image.new("RGB", (side, side), MONTAGE_BG)
    for i, p in enumerate(paths[: g * g]):
        with Image.open(p) as im:
            thumb = im.convert("RGB").resize((cell, cell), Image.NEAREST)  # 點陣圖用 NEAREST
        canvas.paste(thumb, (gap + (i % g) * (cell + gap), gap + (i // g) * (cell + gap)))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


# ---------- 一次 LLM 命名（本專案第一次真的用 LLM） ----------

def build_prompt(clusters: list[dict[str, Any]], montages: list[str]) -> str:
    """餵給 LLM 的東西只有兩樣：montage 路徑 + 群統計。**不給任何人工標籤、不給 gt.json**。"""
    lines = [
        "你在做晶圓缺陷圖(wafer map)的非監督分群命名。以下每一群都是 KMeans 從幾何描述子分出來的，",
        "沒有任何人工標籤。請看 montage 圖（每群 9 張代表圖，用 Read 工具讀檔）與群統計，替每一群命名。",
        "",
        "只准從這六個詞挑，不准自創、不准改拼法：" + " / ".join(VOCAB),
        "每個詞最多用一次。名字意義：center=集中在晶圓中心、donut=中間半徑的閉合環、",
        "edge-ring=沿著晶圓外緣的環或弧、edge-loc=靠邊的局部團塊、scratch=細長刮痕、random=散落無型態。",
        "",
        "描述子定義：ring_ness=缺陷像素平均半徑/標準差（越大越集中在同一個半徑）、",
        "center_ness=1-平均正規化半徑（越大越靠中心）、elongation=PCA 長短軸比（越大越細長）、",
        "edge_frac=半徑 > 0.75R 的像素佔比、radial_hist=徑向分佈 8 bin（由圓心到邊緣）。",
        "",
    ]
    for c in clusters:  # montages 的索引是 cluster_id（不是 enumerate 的序號），excluded 的群會跳號
        d, m = c["descriptor_mean"], montages[c["cluster_id"]]
        m = str(PROJECT_ROOT / m) if not m.startswith("/") else m  # Read 工具只吃絕對路徑
        lines.append(
            f"cluster_{c['cluster_id']}  size={c['size']}  montage={m}\n"
            f"  ring_ness={d['ring_ness']:.2f}  center_ness={d['center_ness']:.2f}  "
            f"elongation={d['elongation']:.2f}  edge_frac={d['edge_frac']:.2f}  "
            f"n_components={d['n_components']:.2f}  defect_ratio={d['defect_ratio']:.4f}\n"
            f"  radial_hist={[round(v, 3) for v in d['radial_hist']]}"
        )
    lines += [
        "",
        "只輸出一個 JSON 陣列，不要任何其他文字、不要 markdown 圍欄：",
        '[{"cluster_id":0,"name":"donut","rationale":"繁體中文一行，必須引用上面的數字"}]',
    ]
    return "\n".join(lines)


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError(f"LLM 回應裡找不到 JSON 陣列：{text[:200]!r}")
    return json.loads(text[start : end + 1])


def call_llm(prompt: str, *, cwd: Path = PROJECT_ROOT, timeout: int = LLM_TIMEOUT_S) -> dict[str, Any]:
    """`claude -p --output-format stream-json` 呼叫**一次**，回 {text, usage, cost_usd, model}。

    記帳的數字全部從最後那筆 `type:"result"` 的 `usage` / `total_cost_usd` 來，不自己估算。
    `--strict-mcp-config --mcp-config {}` 是為了把 MCP 的工具定義擋在 system prompt 外 ——
    實測不擋的話光是 tool 定義就 18K cache token，一次命名要 $0.19（teaching 預算的 38%）。
    """
    # `CV_LLM_CMD` 是給教學現場的逃生門：指到不存在的東西就會走降級路徑（離線也能跑完 M2）
    exe = os.environ.get("CV_LLM_CMD", LLM_CMD)
    if shutil.which(exe) is None:
        raise FileNotFoundError(f"找不到 {exe} CLI")
    cmd = [
        exe, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--max-turns", str(LLM_MAX_TURNS),       # 讀 montage（每群一次 Read）+ 回答
        "--model", os.environ.get("CV_LLM_MODEL", LLM_MODEL),
        # 空的 mcpServers（不是 "{}"，那個會被擋成 Invalid MCP configuration）：把 MCP 的工具定義
        # 擋在 system prompt 外。實測不擋的話光工具定義就 18K cache token，一次命名 $0.19。
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--allowedTools", "Read",
    ]
    # 起子行程走 `procs.run`（登記進表 + 自成一組）：這一顆被 `asyncio.to_thread` 包著、
    # 最長跑 240 秒，沒登記的話按了 Stop 也殺不到（實測 cancel 後 44 秒 claude 還在燒 token）。
    proc = procs.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    )
    result: dict[str, Any] | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            result = obj
    if result is None:
        raise RuntimeError(f"claude 沒有吐出 result 框（returncode={proc.returncode}）：{proc.stderr[:300]}")
    if result.get("is_error"):
        raise RuntimeError(f"claude 回報錯誤：{str(result.get('result'))[:300]}")

    u = result.get("usage") or {}
    return {
        "text": result.get("result", ""),
        "model": next(iter(result.get("modelUsage", {})), None),
        "cost_usd": float(result.get("total_cost_usd") or 0.0),
        "duration_ms": result.get("duration_ms"),
        "usage": {
            "input_tokens": int(u.get("input_tokens", 0)),
            "output_tokens": int(u.get("output_tokens", 0)),
            "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens", 0)),
            "cache_read_input_tokens": int(u.get("cache_read_input_tokens", 0)),
        },
    }


def _mechanical_name(d: dict[str, Any], taken: set[str]) -> tuple[str, str]:
    """降級用的機械命名：只看描述子門檻，完全不呼叫任何東西，同樣的輸入永遠同樣的輸出。

    ⚠️ 這裡**不能**回 `cluster_0` 這種名字：`class_table.schema.json` 把 `names` 凍成那六個詞
    （console-owner 凍結，M2 只填值），寫非詞彙表的字串會產出一份不合 schema 的 class 表，
    freeze 的 selfcheck 會 FAIL、data.yaml 的 names 也跟著髒掉 —— 降級路徑的意義是「線不斷」，
    不是「線斷在下一站」。降級的痕跡記在 `naming` 欄位與 rationale 的前綴，不是靠亂編名字。
    """
    if d["edge_frac"] >= 0.5:
        pick = "edge-ring" if d["ring_ness"] >= 4 else "edge-loc"
    elif d["elongation"] >= 3:
        pick = "scratch"
    elif d["ring_ness"] >= 4 and 0.25 <= (1 - d["center_ness"]) <= 0.8:
        pick = "donut"
    elif d["center_ness"] >= 0.6:
        pick = "center"
    else:
        pick = "random"
    first = pick
    if pick in taken:
        pick = next((v for v in VOCAB if v not in taken), pick)
    return pick, ("" if pick == first else f"（首選 {first} 已被前一群用掉，退到 {pick}）")


def name_clusters(
    clusters: list[dict[str, Any]], montages: list[str], *, use_llm: bool = True
) -> dict[str, Any]:
    """回 {naming: "llm"|"fallback", rationale: [...], llm: {...}|None, error: str|None}。

    `montages` 的索引是 **cluster_id**（長度 = k，含被 excluded 的群），不是「入選群的第幾個」——
    schema 要求 `montage_urls` 的順序對齊 `cluster_stats.clusters`，兩份用不同索引就會靜默錯位。

    LLM 只呼叫**一次**。失敗（沒裝 CLI / timeout / 回傳不是合法 JSON / 用了詞彙表外的字 /
    名字撞名 / 少了某一群）一律降級成機械命名並標 `naming="fallback"`，不重試、不整條線掛掉。
    """
    included = [c for c in clusters if not c["excluded"]]
    error: str | None = None
    if use_llm:
        try:
            got = call_llm(build_prompt(included, montages))
            items = {int(x["cluster_id"]): x for x in _parse_json_array(got["text"])}
            names: list[str] = []
            rationale = []
            for c in included:
                x = items[c["cluster_id"]]          # 少一群 → KeyError → 降級
                name = str(x["name"]).strip()
                if name not in VOCAB:
                    raise ValueError(f"LLM 用了詞彙表外的名字 {name!r}")
                if name in names:
                    raise ValueError(f"LLM 把 {name!r} 用在兩群上")
                names.append(name)
                rationale.append(
                    {
                        "cluster_id": c["cluster_id"],
                        "name": name,
                        "rationale": str(x.get("rationale", ""))[:200] or f"{name}（LLM 未給理由）",
                        "evidence": montages[c["cluster_id"]],
                    }
                )
            return {"naming": NAMING_LLM, "rationale": rationale, "llm": got, "error": None}
        except Exception as exc:  # noqa: BLE001 —— 任何失敗都降級，這就是降級路徑存在的理由
            error = f"{type(exc).__name__}: {exc}"[:300]

    taken: set[str] = set()
    rationale = []
    for c in included:
        d = c["descriptor_mean"]
        name, clash = _mechanical_name(d, taken)
        taken.add(name)
        rationale.append(
            {
                "cluster_id": c["cluster_id"],
                "name": name,
                "rationale": (
                    f"[fallback 機械命名] ring_ness {d['ring_ness']:.2f}、edge_frac {d['edge_frac']:.2f}、"
                    f"elongation {d['elongation']:.2f} → {name}{clash}"
                )[:200],
                "evidence": montages[c["cluster_id"]],
            }
        )
    return {"naming": NAMING_FALLBACK, "rationale": rationale, "llm": None, "error": error}


# ---------- 組出 class 真相表（欄位照 class_table.schema.json，只填值不改欄位） ----------

def cluster_stats(
    descriptors: list[dict[str, Any]], labels: list[int], k: int, support: dict[int, int] | None = None
) -> list[dict[str, Any]]:
    out = []
    for cid, idxs in enumerate(cluster_members(labels, k)):
        picked = [descriptors[i] for i in idxs]
        mean = {
            key: round(float(np.mean([d[key] for d in picked])), 4) if picked else 0.0
            for key in FEATURE_KEYS
        }
        mean["radial_hist"] = [
            round(float(np.mean([d["radial_hist"][b] for d in picked])), 4) if picked else 0.0
            for b in range(RADIAL_BINS)
        ]
        small = len(idxs) < MIN_CLUSTER_SIZE
        rec: dict[str, Any] = {
            "cluster_id": cid,
            "size": len(idxs),
            "support": int((support or {}).get(cid, 0)),
            "descriptor_mean": mean,
            "excluded": small,
        }
        if small:
            rec["exclude_reason"] = f"樣本只有 {len(idxs)} 張（< {MIN_CLUSTER_SIZE}），指標不可信，不進 class 表"
        out.append(rec)
    return out


def build_class_table(
    *,
    version: str,
    res: dict[str, Any],
    stats: list[dict[str, Any]],
    naming: dict[str, Any],
    montages: list[str],
) -> dict[str, Any]:
    """頂層剛好六個欄位（schema `additionalProperties: false`）—— naming 是 llm 還是 fallback
    記在 ds 的 `naming.json` 與事件 text，不塞進這份檔案。"""
    names = [r["name"] for r in naming["rationale"]]
    return {
        "version": version,
        "nc": len(names),
        "names": names,
        "cluster_stats": {
            "k": res["k"],
            "silhouette": res["silhouette"],
            "silhouette_by_k": {str(k): v for k, v in res["silhouette_by_k"].items()},
            "clusters": stats,
        },
        "naming_rationale": naming["rationale"],
        "montage_urls": montages,
    }


def class_id_of_cluster(table: dict[str, Any]) -> dict[int, int]:
    """cluster_id → class id（names 的索引）。excluded 的群不在裡面，其成員沒有類別。"""
    return {r["cluster_id"]: i for i, r in enumerate(table["naming_rationale"])}


# ---------- 自檢：`uv run python -m src.autolabel.cluster` ----------
# 這條是真的會失敗的檢查：合成資料知道正確型態（gt.json 的 `shape`，只有這裡讀得到），
# 所以「每群的主要瑕疵型態一致率」是可以被打臉的數字 —— 描述子選錯、scaler 拿掉、
# k 選錯都會讓它掉到 0.7 以下。

PURITY_FLOOR = 0.7


def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import tempfile
    import time

    t0 = time.time()
    gt = demo.ensure()
    recs = gt["images"]
    descriptors = [describe_image(demo.DEMO_DIR / r["name"]) for r in recs]
    t_desc = time.time() - t0

    res = cluster_descriptors(descriptors)
    k, labels = res["k"], res["labels"]
    print(f"圖 {len(recs)} 張 · 描述子 {t_desc:.1f} 秒 · silhouette_by_k {res['silhouette_by_k']}")
    print(f"選中 k={k}  silhouette={res['silhouette']}")

    # 1. k 落在 schema 與 DESIGN 都寫死的 3–6
    assert 3 <= k <= 6, f"k={k} 不在 3–6"

    # 2. 每群的主要瑕疵型態一致率 >= 0.7（gt 的 shape 只有這裡讀得到）
    worst = 1.0
    for cid, idxs in enumerate(cluster_members(labels, k)):
        shapes = [recs[i]["shape"] for i in idxs]
        top = max(set(shapes), key=shapes.count)
        purity = shapes.count(top) / len(shapes)
        worst = min(worst, purity)
        d = descriptors[idxs[0]]
        print(
            f"  cluster_{cid}  n={len(idxs):<4} 主型態 {top:<10} 一致率 {purity:.2f}  "
            f"（{ {s: shapes.count(s) for s in sorted(set(shapes))} }）"
        )
        assert purity >= PURITY_FLOOR, f"cluster_{cid} 一致率 {purity:.2f} < {PURITY_FLOOR}：分群沒抓到型態"
        assert d  # 描述子要真的有算出東西

    # 3. montage 真的產得出來（9 張、3×3、檔案存在）
    tmp = Path(tempfile.mkdtemp(prefix="montage-selfcheck-"))
    idxs = representative(res["X"], cluster_members(labels, k)[0], MONTAGE_GRID**2)
    out = write_montage([demo.DEMO_DIR / recs[i]["name"] for i in idxs], tmp / "c0.png")
    assert out.exists() and out.stat().st_size > 1024, out

    # 4. 機械命名（降級路徑）永遠產得出合法詞彙表的名字，且不撞名
    stats = cluster_stats(descriptors, labels, k)
    fb = name_clusters(stats, [f"01-raw-data/x{c}.png" for c in range(k)], use_llm=False)
    table = build_class_table(version="v1", res=res, stats=stats, naming=fb, montages=[f"01-raw-data/x{c}.png" for c in range(k)])
    assert table["nc"] == len(table["names"]) == len(table["naming_rationale"]), table
    assert set(table) == {"version", "nc", "names", "cluster_stats", "naming_rationale", "montage_urls"}
    names = [r["name"] for r in fb["rationale"]]
    assert fb["naming"] == NAMING_FALLBACK
    assert all(n in VOCAB for n in names) and len(set(names)) == len(names), names

    print(f"cluster selfcheck PASS（最低一致率 {worst:.2f}，{time.time() - t0:.1f} 秒）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
