#!/usr/bin/env python3
"""下載 yolov8n.pt 到 00-weights/、記 sha256，並做一次真的 dummy forward 驗 MPS。

    uv sync --extra train                       # 先裝訓練依賴（base sync 沒有 torch）
    uv run python scripts/fetch_weights.py      # 三條 assert；任何一條掛就是紅的

做的事對應 DESIGN s05 `POST /api/v1/models/probe` 的「權重快取」那一段，
產出的三個欄位 name / bytes / sha256 / mps_ok 就是 M3 事件 `model.weights.fetched` 的內容。
這裡只落檔 + 自檢，**不碰 router、不推事件**（那是 M3 的事）。

「mps_ok」不是查 `torch.backends.mps.is_available()` 就算數 —— 那只是宣稱支援。
這支會把權重真的搬到 mps、forward 一張 640×640，並 assert 參數確實落在 mps 上。

擁有者：training-engineer。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

WEIGHTS_DIR = PROJECT_ROOT / "00-weights"
ASSET_RELEASE = "v8.3.0"  # ultralytics/assets 的 release tag，釘死才有可重現的 sha256
NAME = "yolov8n.pt"
URL = f"https://github.com/ultralytics/assets/releases/download/{ASSET_RELEASE}/{NAME}"

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


def fetch(name: str = NAME, url: str = URL) -> Path:
    """下載到 00-weights/（已存在就沿用，離線也還跑得動）。"""
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    pt = WEIGHTS_DIR / name
    if not pt.exists():
        print(f"下載 {url}")
        urllib.request.urlretrieve(url, pt)  # noqa: S310 — 固定 https 常數，非使用者輸入
    return pt


def sha256(pt: Path) -> str:
    h = hashlib.sha256()
    with pt.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dummy_forward(pt: Path, device: str = "mps") -> tuple[float, str]:
    """真的 forward 一次，回 (耗時秒, 權重實際所在 device)。"""
    import numpy as np
    from ultralytics import YOLO

    model = YOLO(str(pt))
    model.to(device)
    blank = np.zeros((640, 640, 3), dtype=np.uint8)
    t0 = time.perf_counter()
    model.predict(blank, device=device, imgsz=640, verbose=False)
    elapsed = time.perf_counter() - t0
    return elapsed, next(model.model.parameters()).device.type


def main() -> int:
    pt = fetch()
    digest = sha256(pt)
    size = pt.stat().st_size

    # assert A：檔案真的下載完整（yolov8n.pt 約 6.2 MB；截斷的檔案 load 時才爆太晚了）
    assert size > 5_000_000, f"FAIL A：{NAME} 只有 {size} bytes，八成沒下載完"

    elapsed, actual_device = dummy_forward(pt)

    # assert B：forward 真的跑在 mps，不是宣稱支援後默默掉回 cpu
    assert actual_device == "mps", f"FAIL B：權重落在 {actual_device}，不是 mps"

    # assert C：一張 640 的 forward 不該超過 10 秒（超過代表落到 cpu fallback 或裝置沒熱起來）
    # 本機實測（M5/24GB）：process 內第一次 4.5–6s（含 Metal shader 首編），第二次起 0.63s。
    assert elapsed < 10.0, f"FAIL C：dummy forward 花了 {elapsed:.2f}s，太慢"

    meta = {"name": NAME, "bytes": size, "sha256": digest, "mps_ok": True}
    (WEIGHTS_DIR / f"{NAME}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, ensure_ascii=False))
    print(f"dummy forward {elapsed:.3f}s on {actual_device}")
    print("PASS 3/3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
