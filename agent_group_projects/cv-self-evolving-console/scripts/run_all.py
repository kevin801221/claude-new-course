#!/usr/bin/env python3
"""一鍵跑完整條龍（s01 → s08），不開瀏覽器也不需要 uvicorn。

    uv run --extra train python scripts/run_all.py                 # 整條龍，preset=real
    uv run --extra train python scripts/run_all.py --preset teaching
    uv run --extra train python scripts/run_all.py --stages s01,s03   # 教學：只跑前兩段

**沒有第二套 pipeline**：這支呼叫的是 `POST /api/v1/runs` 的同一個函式
（`routers/console.create_run()` → `drive_run()`），所以事件、stage 狀態、錯誤處理與那顆按鈕
一字不差。`--extra train` 不是可選的 —— s05/s07/s08 會起需要 torch 的子行程。
擁有者：console-owner。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.app import registry  # noqa: E402
from src.app.routers import console  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="一鍵跑完 s01 → s08")
    ap.add_argument("--preset", default="real", choices=("real", "teaching"))
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stages", default=None,
                    help=f"只跑這幾段，逗號分隔（{','.join(console.PIPELINE_IDS)}）；預設整條龍")
    args = ap.parse_args()
    stages = args.stages.split(",") if args.stages else None

    # 一次只准一個 run（GPU 佇列深度 1）。端點本來就會回 409，這裡先講人話。
    if (busy := registry.running_run()) is not None:
        print(f"{busy} 還在跑。先 POST /api/v1/runs/{busy}/cancel 或等它結束。")
        return 2

    body = json.loads((await console.create_run(console.CreateRun(
        mode="oneshot", source="demo", preset=args.preset, limit=args.limit, seed=args.seed,
        stages=stages,
    ))).body)
    run_id = body["run_id"]
    task = console._TASKS[run_id]  # driver 已經在跑了，這裡只是接住它
    print(f"run {run_id} · ds {body['ds_id']} · preset {args.preset} · limit {args.limit}")
    try:
        timings = await task
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl-C 不能只殺自己：訓練子行程是 `start_new_session=True` 起的，不殺它就變孤兒燒 GPU
        out = await console.cancel_run(run_id)
        print(f"\n已中止 {run_id}（stop_seq {out['stop_seq']}，殺掉 {out['trains_killed'] or '無'}）")
        return 130

    snap = registry.snapshot(run_id) or {}
    for label, spent in timings.items():
        print(f"  {label:<9} {spent:>7.2f}s")
    print(f"  {'總計':<8} {sum(timings.values()):>7.2f}s")
    print("stage：" + "  ".join(f"{k}={v}" for k, v in snap.get("stages", {}).items()))
    print(f"run {run_id} → {snap.get('status')}"
          f" · GPU {snap.get('budget', {}).get('gpu_min_used')} 分"
          f" · LLM {snap.get('budget', {}).get('llm_calls')} 次"
          f" · ${snap.get('budget', {}).get('cost_usd')}")
    if snap.get("status") == "done":
        print(f"圖表：GET /api/v1/eval/{run_id}/charts ・裁決：GET /api/v1/eval/{run_id}/verdict")
    return 0 if snap.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
