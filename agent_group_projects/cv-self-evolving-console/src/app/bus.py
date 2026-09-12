"""事件總線 —— `runs/<run_id>/events.jsonl` 的唯一 writer + SSE tail。

契約：`_Context/api-contract.md` §2（事件信封）、§5（SSE 重連語意）、§7（大量事件游標）。
擁有者：console-owner（共同地基，其他四位不准改本檔，只准呼叫 `append_event()`）。

裁決 B（契約 §2）：只有本檔與訓練子行程的 `src/train/callbacks.py` 能寫 events.jsonl，
而後者**必須 import 本檔的 `append_event()`**（不准自己拼 JSON、不准自己算 seq）。
seq 的單調與重播語意只要有第二隻手寫就死，所以發號在這裡用檔案鎖做，跨 process 也成立。
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi.responses import StreamingResponse

# src/app/bus.py → parents[2] = 專案根（一律 pathlib 推導，不硬編絕對路徑）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "runs"

CONTRACT_VERSION = 1  # 事件信封的 v；破壞式改動才進到 2（契約 §12）

ACTORS = frozenset(
    {
        "dataset-truth",
        "training-engineer",
        "metric-auditor",
        "experiment-arbiter",
        "console-owner",
    }
)
STAGES = frozenset(f"s{i:02d}" for i in range(1, 12))

# 契約 §4：type 前綴即擁有者。發別人的 type 等同越權改別人的目錄，所以在唯一 writer 這裡擋。
TYPE_OWNERS: dict[str, str] = {
    "ds.": "dataset-truth",
    "label.": "dataset-truth",
    "class.": "dataset-truth",
    "model.": "training-engineer",
    "probe.": "training-engineer",
    "train.": "training-engineer",
    "augment.": "training-engineer",
    "recipe.": "training-engineer",
    "sweep.": "training-engineer",
    "eval.": "metric-auditor",
    "test.": "metric-auditor",
    "charts.": "metric-auditor",
    "chat.": "experiment-arbiter",
    "expert.": "experiment-arbiter",
    "arbiter.": "experiment-arbiter",
    "round.": "experiment-arbiter",
    "autonomy.": "experiment-arbiter",
    "run.": "console-owner",
    "stage.": "console-owner",
    "provenance.": "console-owner",
}
# 沒有點號的 type（契約 §4 只有這一個）
TYPE_OWNERS_EXACT: dict[str, str] = {"stop": "experiment-arbiter"}
# 明文例外：契約 §8.4 規定 console-owner 的 cancel 端點要寫一筆 `stop {reason:"user_cancel"}`，
# 與 §4 的擁有者表衝突。端點條文較具體，所以放行這一組（只有這一組）。
TYPE_OWNER_EXCEPTIONS = frozenset({("stop", "console-owner")})

# 心跳：**送 data 框不送 `: ping` 註解行**（契約 §5.4，2026-09-12 改）。註解行瀏覽器不交給 JS，
# 所以任何超過 45 秒的 stage（LLM 命名 ~150 秒、noise floor ~69 秒）都會固定觸發前端看門狗的
# 「45 秒沒有任何事件 → 主動 close 重開」，台上看起來像斷線兩次。心跳只在線上，不落檔。
HEARTBEAT_FRAME = 'data: {"v":1,"type":"heartbeat"}\n\n'

# 契約 §7.1：影像事件每個 run 只送並只落檔前 200 筆，之後由擁有者改送 ds.progress 游標。
IMAGE_EVENT_TYPES = frozenset({"ds.image", "test.image"})
IMAGE_EVENT_CAP = 200

SSE_PING_SECONDS = 15
_TAIL_POLL_SECONDS = 0.1

# ponytail: 影像事件計數放記憶體（每個 API process 一份）。M1 的 ingest 不可續跑，
# process 重啟就是開新 run，所以不需要持久化；真要跨重啟續寫影像事件再改成讀檔尾統計。
_image_counts: dict[str, int] = {}


def utc_now_iso() -> str:
    """ISO8601 / UTC / 毫秒 / 結尾 Z（契約 §1：事件要能按字串排序）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def events_path(run_id: str) -> Path:
    return run_dir(run_id) / "events.jsonl"


def _tail_seq(fh) -> int:
    """讀檔尾最後一行的 seq。空檔回 0（所以第一筆 seq = 1，`since=0` 自然是全段回放）。"""
    fh.seek(0, 2)
    size = fh.tell()
    if size == 0:
        return 0
    back = min(size, 65536)
    fh.seek(size - back)
    chunk = fh.read(back)
    for line in reversed(chunk.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if line:
            return int(json.loads(line)["seq"])
    return 0


def _validate(stage: str, type_: str, actor: str, data: Any) -> None:
    if actor not in ACTORS:
        raise ValueError(f"actor 不在契約 §2 的五個 slug 內：{actor!r}")
    if stage not in STAGES:
        raise ValueError(f"stage 不在 s01..s11 內：{stage!r}")
    if not isinstance(data, dict):
        raise ValueError("data 一律是 object（契約 §2：不准是陣列或純量）")
    owner = TYPE_OWNERS_EXACT.get(type_)
    if owner is None:
        for prefix, who in TYPE_OWNERS.items():
            if type_.startswith(prefix):
                owner = who
                break
    if owner is None:
        raise ValueError(f"type 前綴不在契約 §4 的命名空間內：{type_!r}")
    if owner != actor and (type_, actor) not in TYPE_OWNER_EXCEPTIONS:
        raise ValueError(f"越權：{actor} 不得發 {type_}（擁有者是 {owner}，契約 §4）")


def is_final(run_id: str) -> bool:
    """這個 run 已經收工了嗎（done/failed/cancelled/crashed）？

    `registry` import 本檔，所以只能在函式裡反向 import（模組層會變成循環 import）。
    讀的是 `state.json` 一份小檔，不是快取 —— 訓練子行程也走這條路，跨 process 只有檔案是真相。
    """
    from . import registry  # noqa: PLC0415 —— 循環 import，只能延後

    status = (registry.read_state(run_id) or {}).get("status")
    return status is not None and status not in registry.LIVE_STATUSES


def append_event(
    run_id: str,
    stage: str,
    type: str,
    actor: str,
    data: dict[str, Any],
    text: str | None = None,
) -> dict[str, Any] | None:
    """append 一筆事件到 `runs/<run_id>/events.jsonl` 並回傳完整信封。

    回 `None` 有兩種情形，都是刻意的：
      1. 超過 200 筆上限的影像事件（契約 §7.1，擁有者應改發 `ds.progress` 游標）。
      2. **run 已經收工**（契約 §8.5「事件只能追加在活的 run 上」）—— 被 cancel 掉的 run
         底下還有 `to_thread` 裡的孤兒 thread 在跑，它們會在 run 標成 cancelled 之後
         繼續追加事件（實測 cancel 後 24 秒還長出一筆 `eval.noise_floor`，seq 比 stop 還大），
         那會讓快照說 cancelled、事件流還在長。**終局的墓碑事件**（`stop` / `stage.failed`）
         要在翻狀態**之前**寫，不是之後。

    發號用 `fcntl.flock` + 讀檔尾，所以 API process 與訓練子行程同時寫也不會撞號。
    """
    _validate(stage, type, actor, data)
    if is_final(run_id):
        return None

    if type in IMAGE_EVENT_TYPES:
        n = _image_counts.get(run_id, 0) + 1
        _image_counts[run_id] = n
        if n > IMAGE_EVENT_CAP:
            return None

    path = events_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)  # close 時自動釋放
        event: dict[str, Any] = {
            "v": CONTRACT_VERSION,
            "seq": _tail_seq(fh) + 1,
            "ts": utc_now_iso(),
            "run_id": run_id,
            "stage": stage,
            "type": type,
            "actor": actor,
            "data": data,
        }
        if text is not None:
            event["text"] = text
        fh.write((json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
        fh.flush()  # flush 到 page cache 就能被別的 process 讀到，不必 fsync
    return event


def _read_from(path: Path, offset: int) -> tuple[list[str], int]:
    """從 byte offset 往後讀「完整的行」，回 (行清單, 新 offset)。

    只吃到最後一個換行為止 —— 另一個 process 可能正寫到一半，半行不能吐出去。
    """
    if not path.exists():
        return [], offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    if not chunk:
        return [], offset
    cut = chunk.rfind(b"\n")
    if cut < 0:
        return [], offset
    lines = [ln for ln in chunk[: cut + 1].decode("utf-8").splitlines() if ln.strip()]
    return lines, offset + cut + 1


def iter_raw(run_id: str, since: int = 0) -> Iterator[tuple[int, str]]:
    """逐筆吐出 `seq > since` 的 (seq, 原始 JSON 行)。SSE 直接送原始行，不重新序列化。"""
    lines, _ = _read_from(events_path(run_id), 0)
    for line in lines:
        seq = int(json.loads(line)["seq"])
        if seq > since:
            yield seq, line


def read_events(run_id: str, since: int = 0) -> list[dict[str, Any]]:
    """`seq > since` 的事件（已解析）。`since=0` = 全段回放。selfcheck 的 assert 1 用這支。"""
    return [json.loads(line) for _, line in iter_raw(run_id, since)]


def last_seq(run_id: str) -> int:
    path = events_path(run_id)
    if not path.exists():
        return 0
    with open(path, "rb") as fh:
        return _tail_seq(fh)


async def sse_events(run_id: str, since: int = 0):
    """SSE 框產生器：先回放 `seq > since`，再 tail 檔案；閒置每 15 秒一框 heartbeat。

    契約 §2 裁決 A/B：只送 `id:` + `data:`，**不送 `event:`**（帶 event 名稱的框不會觸發
    前端的 `onmessage`）。契約 §5.6：run 結束後不主動關連線，繼續 ping，由前端 close。
    """
    path = events_path(run_id)
    offset = 0
    last = since
    last_ping = time.monotonic()

    lines, offset = _read_from(path, 0)
    for line in lines:
        seq = int(json.loads(line)["seq"])
        if seq > since:
            last = seq
            yield f"id: {seq}\ndata: {line}\n\n"

    while True:
        lines, offset = _read_from(path, offset)
        sent = False
        for line in lines:
            seq = int(json.loads(line)["seq"])
            if seq <= last:
                continue  # 重播邊界重疊一筆是協議允許的，但同一條連線內不重送
            last = seq
            sent = True
            yield f"id: {seq}\ndata: {line}\n\n"
        now = time.monotonic()
        if sent:
            last_ping = now
        elif now - last_ping >= SSE_PING_SECONDS:
            last_ping = now
            yield HEARTBEAT_FRAME
        await asyncio.sleep(_TAIL_POLL_SECONDS)


def sse_response(run_id: str, since: int = 0) -> StreamingResponse:
    """契約 §1 規定的 SSE header 集中在這裡，router 不重複拼。"""
    return StreamingResponse(
        sse_events(run_id, since),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 不加這個，反向代理會把串流緩衝成一坨
            "Connection": "keep-alive",
        },
    )


# ---------- 地基自檢：`uv run python -m src.app.bus` ----------
# 非 trivial 邏輯留一個能跑的檢查（team-roles §4）。這裡驗的是總線最容易靜默壞掉的五件事：
# seq 發號、越權守門、`?since=` 續讀語意、影像 200 筆上限、跨 process 不撞號、SSE 框格式與心跳。

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import subprocess
    import sys
    import tempfile

    from . import registry

    global RUNS_DIR, SSE_PING_SECONDS
    RUNS_DIR = Path(tempfile.mkdtemp(prefix="bus-selfcheck-"))
    # `python -m src.app.bus` 時本檔是 `__main__`，registry 另外 import 了一份 `src.app.bus`，
    # 它的 RUNS_DIR 還指著真的 runs/ —— 不一起改，`is_final()` 會去問真的那些 run。
    registry.bus.RUNS_DIR = RUNS_DIR

    # 1. seq 從 1 起、單調遞增（so `since=0` 自然是全段回放）
    for i in range(5):
        ev = append_event("r1", "s01", "ds.progress", "dataset-truth", {"loaded": i, "total": 5})
        assert ev and ev["seq"] == i + 1, ev
    assert last_seq("r1") == 5

    # 2. type 前綴 = 發言權（契約 §4）；`stop` 由 console-owner 發是 §8.4 的明文例外
    try:
        append_event("r1", "s07", "train.epoch", "dataset-truth", {"epoch": 1})
        raise AssertionError("越權發別人的 type 沒有被擋下來")
    except ValueError:
        pass
    assert append_event("r1", "s01", "stop", "console-owner", {"reason": "user_cancel"})

    # 3. `?since=N` 續讀 == 全段讀切尾，逐筆相同（契約 §5.2，也是 selfcheck.py assert 1 的地基）
    full = read_events("r1", 0)
    assert [e["seq"] for e in full] == [1, 2, 3, 4, 5, 6], full
    assert read_events("r1", 3) == full[3:]

    # 4. 影像事件每個 run 只落檔前 200 筆（契約 §7.1；M8 的 409 張就靠這條不塞爆回放）
    kept = [
        append_event("r2", "s01", "ds.image", "dataset-truth", {"id": f"demo{i:04d}"})
        for i in range(1, 260)
    ]
    assert sum(1 for e in kept if e is not None) == IMAGE_EVENT_CAP
    assert last_seq("r2") == IMAGE_EVENT_CAP

    # 5. 跨 process 發號不撞（訓練子行程也走 append_event，flock + 讀檔尾就是為了這個）
    child = (
        "import sys; from pathlib import Path;"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r});"
        "from src.app import bus;"
        f"bus.RUNS_DIR = Path({str(RUNS_DIR)!r});"
        "[bus.append_event('r1','s07','train.epoch','training-engineer',{'epoch':i})"
        " for i in range(5)]"
    )
    subprocess.run([sys.executable, "-c", child], check=True, cwd=PROJECT_ROOT)
    seqs = [e["seq"] for e in read_events("r1", 0)]
    assert seqs == list(range(1, 12)), seqs  # 6 筆本 process + 5 筆子行程，號碼不重疊不跳號

    # 6. SSE：框格式、心跳、tail 到新事件
    SSE_PING_SECONDS = 0.2

    async def _probe() -> None:
        agen = sse_events("r1", 0)
        frames = [await agen.__anext__() for _ in range(last_seq("r1"))]
        assert frames[0].startswith("id: 1\ndata: {"), frames[0]
        assert frames[0].endswith("\n\n") and "event:" not in frames[0]  # 契約 §2 裁決 A
        ping = await asyncio.wait_for(agen.__anext__(), timeout=3)
        # 心跳必須是 **data 框**（前端 onmessage 收得到），不是 `: ping` 註解行 —— 註解行
        # 瀏覽器不交給 JS，前端 45 秒看門狗會把每個長 stage 都判成斷線。
        assert ping == HEARTBEAT_FRAME and ping.startswith("data: "), ping
        append_event("r1", "s01", "ds.total", "dataset-truth", {"total": 120, "source": "demo"})
        live = await asyncio.wait_for(agen.__anext__(), timeout=3)
        assert live.startswith("id: 12\ndata: {"), live
        await agen.aclose()

    asyncio.run(_probe())
    print(f"bus selfcheck PASS（暫存目錄 {RUNS_DIR}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
