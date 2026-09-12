"""四項 check 與 verdict —— 「這算不算真進步」的判準，每一項都回 `{name, pass, detail}`。

契約：`_Context/api-contract.md` §4（`eval.leakage` / `eval.verdict` 的 data 欄位）。
設計：`DESIGN.md` s08（verdict badge 點開列出四項 check）與護欄 1/2/3。
擁有者：metric-auditor。

四項各自在防哪一種「數字很漂亮但不是真的」：
  1. `split_leakage`  — valid 的圖在 train 裡出現過（或近重複）。mAP 就是在背答案。
  2. `test_size_power` — 樣本太少，CI 寬到任何 Δ 都不可讀；support < 30 的類別只能當提示。
  3. `anchor_drift`   — val 漲但人工 GT 的 recall 掉 → 模型學到的是 pseudo-label 的偏誤。
  4. `overfit_gap`    — train loss 一直降但 val mAP 平 → 在背訓練集。

⚠️ 不准 import torch / ultralytics（本檔在 API process 內跑）。自檢：`uv run python -m src.eval.checks`
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eval.metrics import MIN_SUPPORT  # noqa: E402

# 近重複的門檻**不寫死**，用資料自己校準：train 內部「最接近的兩張不同影像」就是這個資料集
# 影像有多像的物理下限（實測晶圓圖 0.0125 —— 大片相同背景 + 小塊缺陷，本來就長得像）。
# 比那個下限再近一半才算可疑；真正的複製貼上距離是 0.0，抓得到。
# 寫死 0.02 的版本實測在乾淨的 split 上誤報 5 對、把 verdict 打成 invalid（假警報比沒有警報更糟）。
NEAR_DUP_RATIO = 0.5
THUMB = 32
OVERFIT_LOSS_DROP = 0.10  # train loss 相對降幅 ≥ 10% 才算「模型真的在學」
VERDICTS = ("trusted", "suspect", "invalid")


def _check(name: str, ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(ok), "detail": detail, **extra}


# ---------- 1. split leakage ----------

def _thumbs(paths: Sequence[Path]) -> np.ndarray:
    from PIL import Image

    out = np.zeros((len(paths), THUMB * THUMB), dtype=np.float32)
    for i, p in enumerate(paths):
        with Image.open(p) as im:
            out[i] = np.asarray(im.convert("L").resize((THUMB, THUMB), Image.BILINEAR),
                                dtype=np.float32).ravel() / 255.0
    return out


def split_leakage(train_dir: Path, valid_dir: Path, *,
                  ratio: float = NEAR_DUP_RATIO) -> dict[str, Any]:
    """train ∩ valid 的 image id、位元級重複、以及近重複（縮圖 RMSE，門檻由資料自己校準）。

    只比 id 是不夠的：同一張圖換個檔名複製過去，id 不重疊但答案已經洩了。

    ponytail: O(n²) 全對全比。120 張是 5760 對、毫秒級；接 Roboflow 409 張也只有 4 萬對。
    真的上萬張再換成 LSH / 先分桶。
    """
    train = sorted(train_dir.glob("*.png"))
    valid = sorted(valid_dir.glob("*.png"))
    if not train or not valid:
        return _check("split_leakage", False, f"讀不到影像（train {len(train)} / valid {len(valid)}）",
                      overlap_ids=[], near_dup=[])

    from src.eval.metrics import image_id_of

    t_ids = {image_id_of(p.name): p for p in train}
    v_ids = {image_id_of(p.name): p for p in valid}
    overlap_ids = sorted(set(t_ids) & set(v_ids))

    t_sha = {hashlib.sha1(p.read_bytes()).hexdigest(): p.name for p in train}
    exact = [(t_sha[h], p.name) for p in valid
             if (h := hashlib.sha1(p.read_bytes()).hexdigest()) in t_sha]

    tv, vv = _thumbs(train), _thumbs(valid)
    # train 內部最近的一對「確定不同」的影像 = 這個資料集的相似度物理下限，拿它當校準基準
    inner = np.sqrt(((tv[:, None, :] - tv[None, :, :]) ** 2).mean(axis=2))
    np.fill_diagonal(inner, np.inf)
    floor = float(inner.min()) if len(train) > 1 else 0.0
    threshold = floor * ratio
    # RMSE 矩陣：(valid, train)。12×48×1024 個 float，一次算完不用迴圈
    rmse = np.sqrt(((vv[:, None, :] - tv[None, :, :]) ** 2).mean(axis=2))
    near_dup = [
        {"valid": valid[i].name, "train": train[j].name, "rmse": round(float(rmse[i, j]), 5)}
        for i, j in zip(*np.where(rmse < threshold))
    ]
    # 落在下限與門檻之間的只算「提醒」，不判 FAIL —— 不然合成資料天生的相似度會天天誤報
    watch = int((rmse < floor).sum()) - len(near_dup)
    closest = float(rmse.min())
    ok = not overlap_ids and not exact and not near_dup
    detail = (f"train {len(train)} 張 / valid {len(valid)} 張；id 重疊 {len(overlap_ids)}、"
              f"位元級重複 {len(exact)}、近重複 {len(near_dup)}"
              f"（門檻 RMSE < {threshold:.5f} = train 內部最近一對 {floor:.5f} × {ratio}；"
              f"實測 valid–train 最接近 {closest:.5f}）"
              + (f"；另有 {watch} 對落在下限內側，列為提醒不判 FAIL" if watch > 0 else ""))
    return _check("split_leakage", ok, detail, overlap_ids=overlap_ids,
                  exact_dup=exact, near_dup=near_dup, closest_rmse=round(closest, 5),
                  inner_floor=round(floor, 5), threshold=round(threshold, 5), watch=watch)


# ---------- 2. test size / statistical power ----------

def test_size_power(per_class: Sequence[dict[str, Any]], n_images: int,
                    ci_width: float | None = None,
                    min_support: int = MIN_SUPPORT) -> dict[str, Any]:
    """樣本數夠不夠。support < 30 的類別**只能當提示**，不得作為停止或達標依據（護欄 3）。"""
    weak = [{"cls": p["cls"], "name": p["name"], "support": p["support"]}
            for p in per_class if 0 < p["support"] < min_support]
    empty = [p["name"] for p in per_class if p["support"] == 0]
    ok = not weak and not empty
    detail = (f"valid {n_images} 張 / {sum(p['support'] for p in per_class)} 個 GT 框；"
              f"support < {min_support} 的類別 {len(weak)} 個"
              + (f"（{', '.join(w['name'] + ' ' + str(w['support']) for w in weak)}）" if weak else "")
              + (f"；完全沒有 GT 的類別 {len(empty)} 個（{', '.join(empty)}）" if empty else "")
              + (f"；整體 mAP50-95 的 95% CI 寬度 {ci_width:.4f}" if ci_width is not None else ""))
    return _check("test_size_power", ok, detail, weak_classes=weak, empty_classes=empty,
                  n_images=n_images, min_support=min_support, ci_width=ci_width)


# ---------- 3. anchor drift ----------

def anchor_drift(anchor_recall: float | None, prev_anchor_recall: float | None,
                 val_delta: float | None, noise_floor: float | None) -> dict[str, Any]:
    """對 anchor（人工 GT）的 recall 有沒有跟著漲。**只當 gate 不當 rank**（護欄 2）。

    最惡毒的那一格：val 主指標上升、anchor recall 下降 —— 那是學到 pseudo-label 的偏誤，
    護欄 2 規定立刻 abandoned，不給第二次機會。
    """
    if anchor_recall is None:
        return _check("anchor_drift", False, "這一輪沒有量 anchor recall（gate 缺席就不能說可信）",
                      anchor_recall=None, anchor_ok=False)
    if prev_anchor_recall is None:
        return _check("anchor_drift", True,
                      f"第一輪：anchor recall {anchor_recall:.4f} 記為基準，沒有可比的前一輪",
                      anchor_recall=anchor_recall, prev=None, anchor_ok=True)
    drop = prev_anchor_recall - anchor_recall
    band = noise_floor if noise_floor is not None else 0.0
    if drop > band and (val_delta or 0) > 0:
        return _check("anchor_drift", False,
                      f"val 漲 {val_delta:+.4f} 但 anchor recall 掉 {drop:.4f}（> noise floor {band:.4f}）"
                      "—— 護欄 2：立刻 abandoned，這是學到 pseudo-label 偏誤的指紋",
                      anchor_recall=anchor_recall, prev=prev_anchor_recall, drop=round(drop, 4),
                      anchor_ok=False)
    if drop > 2 * band and band > 0:
        return _check("anchor_drift", False,
                      f"anchor recall 掉 {drop:.4f}（> 2× noise floor {band:.4f}），與 val 方向無關都不可信",
                      anchor_recall=anchor_recall, prev=prev_anchor_recall, drop=round(drop, 4),
                      anchor_ok=False)
    return _check("anchor_drift", True,
                  f"anchor recall {prev_anchor_recall:.4f} → {anchor_recall:.4f}"
                  f"（Δ {-drop:+.4f}，noise floor {band:.4f} 內或同向）",
                  anchor_recall=anchor_recall, prev=prev_anchor_recall, drop=round(drop, 4),
                  anchor_ok=True)


# ---------- 4. overfit gap ----------

def overfit_gap(curves: Sequence[dict[str, Any]], noise_floor: float | None) -> dict[str, Any]:
    """train loss 一直降但 val mAP 平 —— 在背訓練集。前後半段各取平均比，不看單點。"""
    pts = [c for c in curves if c.get("map5095") is not None]
    if len(pts) < 4:
        return _check("overfit_gap", True, f"只有 {len(pts)} 個 epoch，太短，不下結論",
                      inconclusive=True)
    half = len(pts) // 2
    loss = [float(c.get("train_box_loss", 0)) + float(c.get("train_cls_loss", 0)) for c in pts]
    maps = [float(c["map5095"]) for c in pts]
    l0, l1 = float(np.mean(loss[:half])), float(np.mean(loss[half:]))
    m0, m1 = float(np.mean(maps[:half])), float(np.mean(maps[half:]))
    loss_drop = (l0 - l1) / l0 if l0 else 0.0
    map_gain = m1 - m0
    band = noise_floor if noise_floor is not None else 0.0
    # 最佳 val 落在最後 25% 的 epoch = 曲線還在往上。那是**訓練不足**，不是過擬合 ——
    # 兩者的處方完全相反（一個要加 epoch，一個要加資料 / 早停），判錯會把下一輪帶去錯的方向。
    tail_start = len(pts) - max(1, len(pts) // 4)
    still_climbing = int(np.argmax(maps)) >= tail_start
    ok = still_climbing or not (loss_drop >= OVERFIT_LOSS_DROP and map_gain < band)
    detail = (f"train loss {l0:.3f} → {l1:.3f}（降 {loss_drop*100:.1f}%）、"
              f"val mAP50-95 {m0:.4f} → {m1:.4f}（{map_gain:+.4f}，noise floor {band:.4f}）"
              + (f"；最佳 val 落在 epoch {pts[int(np.argmax(maps))]['epoch']}/{pts[-1]['epoch']}"
                 "，曲線還在往上 → 這是訓練不足不是過擬合" if still_climbing
                 else "" if ok else " —— loss 一直在降，val 的漲幅卻落在雜訊帶內："
                      "分不出是學到東西還是抖動，這一輪不得宣告進步"))
    return _check("overfit_gap", ok, detail, loss_drop=round(loss_drop, 4),
                  map_gain=round(map_gain, 4), noise_floor=band,
                  still_climbing=still_climbing,
                  best_epoch=pts[int(np.argmax(maps))]["epoch"])


# ---------- verdict ----------

def verdict(checks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """`trusted | suspect | invalid`。

    洩漏是**資格問題**不是程度問題 —— train 跟 valid 有重疊，這個數字根本不是 val 分數，
    所以直接 invalid（前端會把「進化下一輪」整條路徑擋住）。其餘 FAIL 是 suspect：
    數字可以看、可以討論，但不准拿來當「進步了」的證據。
    """
    failed = [c["name"] for c in checks if not c["pass"]]
    if "split_leakage" in failed:
        v = "invalid"
    elif failed:
        v = "suspect"
    else:
        v = "trusted"
    return {"verdict": v, "failed": failed,
            "why": {"invalid": "split leakage：valid 與 train 有重疊，這個數字不是 val 分數",
                    "suspect": f"{len(failed)} 項 check 沒過（{', '.join(failed)}），數字可讀但不得當證據",
                    "trusted": "四項 check 全過"}[v]}


# ---------- 自檢：`uv run python -m src.eval.checks` ----------

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import tempfile

    from PIL import Image

    rng = np.random.default_rng(0)

    # 1. 乾淨的 split：沒有 id 重疊、沒有近重複 → PASS
    tmp = Path(tempfile.mkdtemp(prefix="checks-selfcheck-"))
    tr, va = tmp / "train", tmp / "valid"
    tr.mkdir(); va.mkdir()
    for i in range(1, 7):
        Image.fromarray(rng.integers(0, 255, (64, 64), dtype=np.uint8)).save(tr / f"wafer_{i:04d}.png")
    for i in range(7, 10):
        Image.fromarray(rng.integers(0, 255, (64, 64), dtype=np.uint8)).save(va / f"wafer_{i:04d}.png")
    clean = split_leakage(tr, va)
    assert clean["pass"] and not clean["overlap_ids"], clean

    # 2. 把 train 的一張換個檔名放進 valid → id 不重疊，但必須抓到（只比 id 的實作會在這裡紅）
    (va / "wafer_0099.png").write_bytes((tr / "wafer_0001.png").read_bytes())
    leaked = split_leakage(tr, va)
    assert not leaked["pass"] and leaked["exact_dup"], leaked
    assert leaked["near_dup"], "近重複偵測沒有抓到位元級相同的那一張"
    assert leaked["near_dup"][0]["rmse"] == 0.0, leaked["near_dup"]
    # 門檻是資料自己校準出來的，不是寫死的常數（寫死 0.02 在真的晶圓圖上誤報 5 對）
    assert 0 < leaked["threshold"] < leaked["inner_floor"], leaked

    # 3. support < 30 的類別要被點名，而且整體判 FAIL（護欄 3：那種數字只能當提示）
    per_class = [{"cls": 0, "name": "scratch", "support": 3},
                 {"cls": 1, "name": "center", "support": 40},
                 {"cls": 2, "name": "donut", "support": 0}]
    power = test_size_power(per_class, n_images=12, ci_width=0.31)
    assert not power["pass"] and [w["name"] for w in power["weak_classes"]] == ["scratch"], power
    assert power["empty_classes"] == ["donut"]
    assert test_size_power([{"cls": 0, "name": "a", "support": 40}], 100)["pass"]

    # 4. anchor gate：val 漲但 anchor 掉 → FAIL（護欄 2 的那一格）
    bad = anchor_drift(0.50, 0.70, val_delta=+0.08, noise_floor=0.05)
    assert not bad["pass"] and bad["anchor_ok"] is False, bad
    assert anchor_drift(0.68, 0.70, val_delta=+0.08, noise_floor=0.05)["pass"]   # 掉在雜訊帶內
    assert anchor_drift(0.70, None, val_delta=None, noise_floor=0.05)["pass"]    # 第一輪

    # 5. overfit：loss 降 50% 但 mAP 平 → FAIL
    flat = [{"epoch": i, "train_box_loss": 2.0 - 0.15 * i, "train_cls_loss": 0.0,
             "map5095": 0.30 + 0.001 * (i % 2)} for i in range(10)]
    assert not overfit_gap(flat, 0.05)["pass"], overfit_gap(flat, 0.05)
    rising = [{"epoch": i, "train_box_loss": 2.0 - 0.15 * i, "train_cls_loss": 0.0,
               "map5095": 0.10 + 0.03 * i} for i in range(10)]
    assert overfit_gap(rising, 0.05)["pass"]
    # 漲幅小於雜訊帶、但最佳值落在最後一個 epoch → 訓練不足，不准判成過擬合
    #（實測 r30-t1 就是這一格：0.0135 一路爬到 0.2618，舊版把它判成「模型在背訓練集」）
    slow = [{"epoch": i, "train_box_loss": 2.0 - 0.15 * i, "train_cls_loss": 0.0,
             "map5095": 0.01 + 0.012 * i} for i in range(10)]
    got = overfit_gap(slow, 0.15)
    assert got["pass"] and got["still_climbing"], got

    # 6. verdict：洩漏 → invalid、其他 FAIL → suspect、全過 → trusted
    assert verdict([leaked, power, bad, flat and overfit_gap(flat, 0.05)])["verdict"] == "invalid"
    assert verdict([clean, power])["verdict"] == "suspect"
    assert verdict([clean])["verdict"] == "trusted"

    print(f"eval.checks selfcheck PASS（暫存目錄 {tmp}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
