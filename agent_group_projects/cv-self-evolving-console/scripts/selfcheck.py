#!/usr/bin/env python3
"""M1 驗收：三條 assert，全綠才算過。

    uv run python scripts/selfcheck.py

三條（DESIGN 護欄 11 / 契約 `_Context/api-contract.md` §11）：
  A. 事件重播一致 —— `events.jsonl` 完整讀 == SSE `?since=` 續讀，逐筆相同。
  B. 抽樣一致 —— Python `src/autolabel/split.py` == 前端 `prototype/index.html`
     的 `stratifiedSplit`，同 seed 逐筆相同。前端那份**是真的從 HTML 抽出來丟給 node 跑**，
     不是比對凍結常數（沒有 node 才退回比對 node 當初產的黃金值，並在輸出標明）。
  C. auto-bbox 對合成 GT 的 IoU 中位數 >= 0.6。

零 GPU / 零 Roboflow / 零 LLM / 零網路。自己起一個 uvicorn（隨機空 port，不會撞到
你手邊那個 8000），跑完自己收掉。擁有者：console-owner。
"""

from __future__ import annotations

import json
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.app import bus  # noqa: E402
from src.autolabel import demo, geometry, split  # noqa: E402

PROTOTYPE = PROJECT_ROOT / "prototype" / "index.html"
IOU_FLOOR = 0.6  # 契約 §11 的「合成 GT」那把尺。不准為了過而調低。
BOX_RATIO_CAP = 1.2  # auto 框數 / GT 框數 的上限（與 geometry._selfcheck 同一個數）
LIMIT, SEED = 120, 42


# ---------- HTTP（stdlib，不引 httpx/requests） ----------

def _json(method: str, url: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sse(base: str, run_id: str, since: int, until_seq: int) -> list[dict]:
    """讀 SSE 直到收到 seq == until_seq 就關掉（串流本身不會結束，靠 heartbeat 活著）。"""
    if until_seq <= since:
        return []
    out: list[dict] = []
    req = urllib.request.Request(f"{base}/api/v1/runs/{run_id}/events?since={since}")
    with urllib.request.urlopen(req, timeout=30) as r:
        for raw in r:  # HTTPResponse 逐行可迭代，SSE 一框一行 data:
            line = raw.decode()
            if not line.startswith("data:"):
                continue  # `id:` 與 `: ping` 不是 payload
            out.append(json.loads(line[5:]))
            if out[-1]["seq"] >= until_seq:
                break
    return out


# ---------- A. 事件重播一致 ----------

def assert_replay(base: str) -> tuple[bool, str]:
    # preset 走 "real"：teaching 會刻意放慢到每張 0.12 秒（台上看得見逐卡 append），
    # 驗收腳本要的是快，不是好看。兩者跑的是同一條 driver，只差 sleep。
    # `stages` 只取 s01/s03：這一條驗的是**事件重播一致**，不是整條龍。不限段的話這支會順便
    # 跑 LLM 命名與兩次訓練（實測 3 分鐘以上），60 秒的 deadline 必定紅，而它其實什麼都沒壞。
    # 整條龍的一鍵驗收在 `scripts/run_all.py`。
    try:
        run = _json("POST", f"{base}/api/v1/runs", {
            "mode": "oneshot", "source": "demo", "preset": "real", "limit": LIMIT, "seed": SEED,
            "stages": ["s01", "s03"],
        })
        run_id = run["run_id"]

        deadline = time.time() + 60
        while time.time() < deadline:
            snap = _json("GET", f"{base}/api/v1/runs/{run_id}")
            if snap["status"] != "running":
                break
            time.sleep(0.2)
        else:
            return False, f"run {run_id} 60 秒還沒跑完"
    except urllib.error.HTTPError as e:
        # 最常見的是 409 RUN_IN_FLIGHT（這個 repo 裡有別人的 run 還在跑）。
        # 印一句看得懂的話，不要讓驗收當天的第一個畫面是 traceback。
        return False, f"{e.code} {e.read().decode('utf-8', 'replace')[:160]}"
    if snap["status"] != "done":
        return False, f"run {run_id} 狀態是 {snap['status']}（不是 done）"

    full = bus.read_events(run_id, 0)  # 完整讀檔
    n = len(full)
    if n < 2:
        return False, f"events.jsonl 只有 {n} 筆，沒東西可比"

    seqs = [e["seq"] for e in full]
    if seqs != list(range(1, n + 1)):
        dup = len(seqs) - len(set(seqs))
        return False, f"seq 不是 1..{n} 連續（重複 {dup} 筆，最大 {max(seqs)}）"

    sse_full = _sse(base, run_id, 0, n)  # since=0 全段回放
    mid = n // 2
    sse_tail = _sse(base, run_id, mid, n)  # 斷線後續讀

    if sse_full != full:
        bad = next(i for i in range(max(len(full), len(sse_full)))
                   if full[i:i + 1] != sse_full[i:i + 1])
        return False, f"since=0 回放與檔案第 {bad} 筆起不同（{len(sse_full)} vs {n} 筆）"
    if sse_tail != full[mid:]:
        return False, f"since={mid} 續讀 {len(sse_tail)} 筆 != 檔案尾段 {n - mid} 筆"

    kinds = {}
    for e in full:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    return True, (
        f"{run_id} · events {n} 筆 seq 1..{n} 連續 · since=0 回放 {len(sse_full)} 筆逐筆相同 · "
        f"since={mid} 續讀 {len(sse_tail)} 筆 == 檔案 [{mid}:] · "
        f"counts {snap['counts']} · type {kinds}"
    )


# ---------- B. 抽樣一致 ----------

def _js_split() -> dict[str, str] | None:
    """從 prototype/index.html 原地抽出 mulberry32 + stratifiedSplit 丟給 node 跑。"""
    src = PROTOTYPE.read_text(encoding="utf-8")
    try:
        start = src.index("function mulberry32")
        end = src.index("\n}\n", src.index("function stratifiedSplit", start)) + 3
    except ValueError:
        return None  # 前端搬過家了 —— 讓它 FAIL，不要默默退回黃金值
    js = (
        src[start:end]
        + f"\nconsole.log(JSON.stringify(stratifiedSplit({json.dumps(split.FIXTURE)},"
        f"{json.dumps(split.FIXTURE_RATIOS)},{split.FIXTURE_SEED})));\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(js)
        path = fh.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    finally:
        Path(path).unlink(missing_ok=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def assert_split() -> tuple[bool, str]:
    py = split.stratified_split(split.FIXTURE, split.FIXTURE_RATIOS, split.FIXTURE_SEED)
    js = _js_split()
    if js is None:
        if not PROTOTYPE.exists():
            return False, f"找不到前端 {PROTOTYPE}"
        js, how = split.FIXTURE_EXPECTED, "node 不可用，退回比對 node 當初產的黃金值"
    else:
        how = f"node 現跑 {PROTOTYPE.name} 抽出的 stratifiedSplit"

    diff = {k: (py.get(k), v) for k, v in js.items() if py.get(k) != v}
    if diff or len(py) != len(js):
        return False, f"{len(diff)} 筆不同（py vs js）：{dict(list(diff.items())[:5])}"
    # 黃金值也要對得上，否則「兩邊一起錯」會綠燈
    if js != split.FIXTURE_EXPECTED:
        return False, "前端輸出與凍結的黃金值不同 —— 有人改了 stratifiedSplit"
    return True, (
        f"{how} · fixture {len(py)} 筆逐筆相同 · {split.split_counts(py)} · "
        f"seed {split.FIXTURE_SEED} · 分層鍵 {len({(i.get('cls') or ['__none__'])[0] for i in split.FIXTURE})} 組"
    )


# ---------- C. auto-bbox IoU ----------

def assert_iou() -> tuple[bool, str]:
    gt = demo.load_gt()
    if gt is None:
        return False, "沒有 01-raw-data/demo/gt.json —— 先跑 scripts/gen_demo_wafers.py"
    ious: list[float] = []
    n_auto = 0
    for rec in gt["images"]:
        out = geometry.label_image(demo.DEMO_DIR / rec["name"])
        n_auto += len(out["boxes"])
        ious += geometry.best_ious(out["boxes"], rec["boxes"])
    med = statistics.median(ious)
    # `best_ious()` 是「每個 GT 取最佳 IoU」，設計上對假陽性免疫 —— 去雜點整組失效
    # （min_area_px 從 64 改成 16，框數 155 → 942）時中位數照樣 1.0000。
    # 所以框數要一起判：中位數量「鬆不鬆」，框數比量「多不多」，兩把尺缺一不可。
    cap = len(ious) * BOX_RATIO_CAP
    ok = med >= IOU_FLOOR and n_auto <= cap
    return ok, (
        f"IoU 中位數 {med:.4f} {'>=' if med >= IOU_FLOOR else '<'} {IOU_FLOOR} · "
        f"平均 {statistics.fmean(ious):.4f} · 最小 {min(ious):.4f} · "
        f"GT {len(ious)} 框 / auto {n_auto} 框（{n_auto / len(ious):.2f}× "
        f"{'<=' if n_auto <= cap else '>'} 上限 {BOX_RATIO_CAP}×）· "
        f"直方圖 {geometry.iou_hist(ious)}"
    )


# ---------- main ----------

def main() -> int:
    t0 = time.perf_counter()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    (PROJECT_ROOT / "runs").mkdir(exist_ok=True)
    log = (PROJECT_ROOT / "runs" / "selfcheck-uvicorn.log").open("w")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT,
    )
    results: list[tuple[str, bool, str]] = []
    try:
        # 冷 .venv 之後「第一次」執行要為 scipy / sklearn / fastapi 編 .pyc，
        # 實測可以吃掉 20 秒以上 —— 原本 15 秒的上限會讓學生剛裝完跑第一次就看到紅字，
        # 第二次才 PASS。上限拉到 90 秒（= M1 的驗收預算），並在超過 15 秒時印一行說明，
        # 免得看起來像當掉。
        WARM_HINT_S, MAX_WAIT_S = 15.0, 90.0
        wait_t0, hinted = time.time(), False
        while True:
            if server.poll() is not None:
                print(f"uvicorn 起不來（exit {server.returncode}），看 runs/selfcheck-uvicorn.log")
                return 2
            try:
                _json("GET", f"{base}/healthz")
                break
            except (urllib.error.URLError, ConnectionError, socket.timeout):
                waited = time.time() - wait_t0
                if waited > MAX_WAIT_S:
                    print(f"uvicorn {MAX_WAIT_S:.0f} 秒沒回 /healthz，看 runs/selfcheck-uvicorn.log")
                    return 2
                if waited > WARM_HINT_S and not hinted:
                    hinted = True
                    print("（第一次執行要編 .pyc，通常 20–40 秒；第二次起只要 2 秒左右）")
                time.sleep(0.1)

        for name, fn in (
            ("A 事件重播一致（完整讀 == ?since= 續讀）", lambda: assert_replay(base)),
            ("B 抽樣一致（Python == 前端 stratifiedSplit）", assert_split),
            (f"C auto-bbox IoU 中位數 >= {IOU_FLOOR} 且框數 <= GT×{BOX_RATIO_CAP}", assert_iou),
        ):
            ok, detail = fn()
            results.append((name, ok, detail))
            print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()

    passed = sum(1 for _, ok, _ in results if ok)
    dt = time.perf_counter() - t0
    print(f"\n{passed}/{len(results)} PASS · {dt:.1f} 秒"
          f"{'' if dt < 90 else ' ⚠️ 超過 90 秒的驗收預算'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
