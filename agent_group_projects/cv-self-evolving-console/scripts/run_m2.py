#!/usr/bin/env python3
"""M2 一鍵實跑：s01 進場 → s03 無類別 bbox → s02 分群 + 一次 LLM 命名 → s04 落檔凍結。

    uv run python scripts/run_m2.py                # 走真的 LLM 命名
    uv run python scripts/run_m2.py --no-llm       # 把 LLM 指到不存在的指令，驗降級路徑
    uv run python scripts/run_m2.py --preset teaching

自己起一個 uvicorn（隨機空 port，不會撞到你手邊那個 8000），**全程打真的 HTTP 端點**，
跑完自己收掉。和 `scripts/selfcheck.py` 一樣不引 httpx/requests，只用 stdlib。

為什麼 run 是用 `registry.create_run()` 開而不是 `POST /api/v1/runs`：那支會立刻啟動
console-owner 的 driver（s01→s03 跑完就把 run 標成 `done`），而 `done` 的 run 再打
`/label/auto` 會依契約 §10 回 `409 RUN_NOT_LIVE`。M2 的四個 stage 要怎麼串進那顆按鈕是
console-owner 的 `drive_run()` 的事（契約 §8.1）；這支腳本走的是契約 §8 明文留的
「curl 單步教學與 M2 之後單獨重跑」那條路。擁有者：dataset-truth。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.app import bus, registry  # noqa: E402


def _json(method: str, url: str, body: dict | None = None, timeout: int = 60) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {url} → {e.code} {e.read().decode()[:300]}") from None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_event(run_id: str, type_: str, timeout: float) -> dict[str, Any]:
    """等 events.jsonl 出現某個 type（背景任務跑完的信號）。順手把 stage.failed 當成中止條件。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for ev in bus.read_events(run_id):
            if ev["type"] == type_:
                return ev
            if ev["type"] == "stage.failed":
                raise SystemExit(f"stage.failed：{ev['data']}")
        time.sleep(0.2)
    raise SystemExit(f"等 {type_} 超過 {timeout} 秒")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="real", choices=("real", "teaching"))
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-llm", action="store_true", help="把 LLM 指到不存在的指令，驗降級路徑")
    args = ap.parse_args()

    t0 = time.perf_counter()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    (PROJECT_ROOT / "runs").mkdir(exist_ok=True)
    log = (PROJECT_ROOT / "runs" / "m2-uvicorn.log").open("w")
    env = {"CV_LLM_CMD": "claude-not-installed-on-purpose"} if args.no_llm else {}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, **env},
    )
    try:
        for _ in range(150):
            if server.poll() is not None:
                print(f"uvicorn 起不來（exit {server.returncode}），看 runs/m2-uvicorn.log")
                return 2
            try:
                _json("GET", f"{base}/healthz", timeout=2)
                break
            except (urllib.error.URLError, ConnectionError, socket.timeout):
                time.sleep(0.1)
        else:
            print("uvicorn 15 秒沒回 /healthz")
            return 2

        state = registry.create_run(
            mode="oneshot", source="demo", preset=args.preset, limit=args.limit, seed=args.seed
        )
        run_id, ds_id = state["run_id"], state["ds_id"]
        registry.set_status(run_id, "running")
        print(f"run {run_id} · ds {ds_id} · preset {args.preset} · limit {args.limit} · port {port}")

        # s01
        registry.set_stage(run_id, "s01", "running")
        r = _json("POST", f"{base}/api/v1/datasets/ingest",
                  {"run_id": run_id, "source": "demo", "limit": args.limit, "seed": args.seed})
        ev = wait_event(run_id, "ds.progress", 180)
        while ev["data"]["loaded"] < ev["data"]["total"]:
            time.sleep(0.2)
            ev = [e for e in bus.read_events(run_id) if e["type"] == "ds.progress"][-1]
        registry.set_counts(run_id, total=r["total"], loaded=r["total"])
        registry.set_stage(run_id, "s01", "done")
        print(f"  s01 ds.total={r['total']}")

        # s03（無類別 bbox；cluster 要的框就是它產的）
        registry.set_stage(run_id, "s03", "running")
        _json("POST", f"{base}/api/v1/label/auto",
              {"run_id": run_id, "ds_id": ds_id, "mode": "refine_only"})
        iou = wait_event(run_id, "label.anchor_iou", 300)["data"]
        registry.set_stage(run_id, "s03", "done")
        print(f"  s03 IoU 中位數 {iou['iou_median']}（門檻 {iou['threshold']}，{iou['verdict']}）")

        # s02（分群 + 一次 LLM 命名）
        t_cluster = time.perf_counter()
        _json("POST", f"{base}/api/v1/label/auto",
              {"run_id": run_id, "ds_id": ds_id, "mode": "cluster"})
        frozen = wait_event(run_id, "class.table.frozen", 600)["data"]
        cl = [e for e in bus.read_events(run_id) if e["type"] == "class.cluster"][-1]["data"]
        table = _json("GET", f"{base}/api/v1/datasets/{ds_id}/classes")
        print(f"  s02 k={cl['k']} silhouette={cl['silhouette']} 群大小={cl['cluster_sizes']}"
              f" （{time.perf_counter() - t_cluster:.1f} 秒）")
        print(f"      class 表 {frozen['version']} nc={frozen['nc']} names={frozen['names']}"
              f" 命名={table['naming']}")
        for r_ in frozen["naming_rationale"]:
            print(f"      cluster_{r_['cluster_id']} → {r_['name']}：{r_['rationale']}")
        if table.get("llm"):
            u = table["llm"]["usage"]
            print(f"      LLM {table['llm']['model']} · in {u['input_tokens']} / out {u['output_tokens']}"
                  f" / cache_w {u['cache_creation_input_tokens']} / cache_r {u['cache_read_input_tokens']}"
                  f" · ${table['llm']['cost_usd']:.4f} · {table['llm']['duration_ms']} ms")
        else:
            naming = json.loads((PROJECT_ROOT / "01-raw-data" / "datasets" / ds_id / "naming.json").read_text())
            print(f"      降級原因：{naming['error']}")

        # s04（GT 三分 + 分層抽樣 + 落檔 + 硬閘門）
        fz = _json("POST", f"{base}/api/v1/datasets/{ds_id}/freeze",
                   {"run_id": run_id, "seed": args.seed}, timeout=300)
        print(f"  s04 GT 三分 {fz['gt_partition']} · split {fz['split']} · {fz['data_yaml']}")
        print(f"      ds_version {fz['ds_version']} · 內容指紋 {fz['ds_fingerprint']}"
              f"（版本號綁指紋：內容變了才 +1，沒變就沿用同一號）")
        for c in fz["checks"]:
            print(f"      [{'PASS' if c['pass'] else 'FAIL'}] {c['name']}: {c['detail'][:110]}")

        snap = _json("GET", f"{base}/api/v1/runs/{run_id}")
        registry.set_status(run_id, "done")
        ledger = [json.loads(x) for x in (PROJECT_ROOT / "runs" / "runs.jsonl").read_text().splitlines() if x.strip()]
        mine = [x for x in ledger if x["run_id"] == run_id][-1]
        print(f"  帳：budget {snap['budget']['llm_calls']} 次 / ${snap['budget']['cost_usd']}"
              f" · runs.jsonl llm_calls={mine['llm_calls']} cost_usd={mine['cost_usd']}"
              f" class_table_version={mine['class_table_version']} ds_version={mine['ds_version']}")
        print(f"\n{'PASS' if fz['pass'] else 'FAIL'} · {time.perf_counter() - t0:.1f} 秒 · "
              f"data.yaml = {(PROJECT_ROOT / fz['data_yaml']).read_text().splitlines()[-len(frozen['names']) - 2:]}")
        return 0 if fz["pass"] else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
