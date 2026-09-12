#!/usr/bin/env python3
"""產離線 demo 的 120 張真點陣 wafer PNG + 免費精確 GT。

    uv run python scripts/gen_demo_wafers.py [--count 120] [--seed 42]

輸出（契約 `_Context/api-contract.md` §11 凍結的佈局）：
    01-raw-data/demo/wafer_0001.png … wafer_0120.png   256×256 RGB
    01-raw-data/demo/gt.json                            免費且精確的 GT bbox

合成邏輯在 `src/autolabel/demo.py`（router 要 import 同一份，不能只活在 scripts/）。
擁有者：dataset-truth。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.autolabel import demo  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="產離線 demo wafer PNG + GT")
    ap.add_argument("--count", type=int, default=demo.DEFAULT_COUNT)
    ap.add_argument("--seed", type=int, default=demo.DEFAULT_SEED)
    ap.add_argument("--out", type=Path, default=demo.DEMO_DIR)
    args = ap.parse_args()

    gt = demo.generate(args.count, args.seed, args.out)

    # 只數「這次產的那幾張」。glob 整個目錄會把上一輪 --count 更大時的殘留檔一起數進來，
    # 於是 `--count 10` 會在 gt.json 已經正確寫好之後以 AssertionError 收場（學生想跑快一點
    # 的第一個動作就踩到）。殘留的孤兒 PNG 不歸這支管，gt.json 沒有它們就不會被任何 API 看到。
    pngs = [args.out / demo.image_name(i) for i in range(1, args.count + 1)]
    boxes = [b for rec in gt["images"] for b in rec["boxes"]]
    shapes = Counter(rec["shape"] for rec in gt["images"])
    areas = sorted(b["w"] * b["h"] for b in boxes)

    print(f"輸出目錄        {args.out}")
    print(f"PNG             {len(pngs)} 張 · {demo.IMAGE_PX}×{demo.IMAGE_PX} RGB · seed {args.seed}")
    print(f"型態分佈        {dict(shapes)}")
    print(f"GT bbox         {len(boxes)} 個 · 平均 {len(boxes)/len(gt['images']):.2f} 個/張")
    print(f"bbox 面積佔比   p50 {areas[len(areas)//2]:.4f} · min {areas[0]:.4f} · max {areas[-1]:.4f}")
    print(f"GT             {args.out / 'gt.json'}")

    # 產生器自己的 assert：數量、尺寸、每張至少一個框、框在 [0,1] 內（契約 §1 座標規則）
    assert len(gt["images"]) == args.count, f"GT 張數不對：{len(gt['images'])} != {args.count}"
    missing = [p_.name for p_ in pngs if not p_.exists()]
    assert not missing, f"這次該產的 PNG 少了 {len(missing)} 張：{missing[:3]}"
    assert all(rec["boxes"] for rec in gt["images"]), "有圖沒有任何 GT bbox"
    assert all(
        0 <= b["cx"] <= 1 and 0 <= b["cy"] <= 1 and 0 < b["w"] <= 1 and 0 < b["h"] <= 1
        for b in boxes
    ), "有 bbox 沒有正規化到 [0,1]"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
