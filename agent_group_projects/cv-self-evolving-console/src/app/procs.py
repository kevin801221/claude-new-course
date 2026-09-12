"""子行程登記處 —— Stop 要殺得到**每一顆**（契約 §8.4）。

擁有者：console-owner（共同地基）。

`runner.start()` 起的訓練/探針子行程登記在 `state.json` 的 `train` 分區，cancel 掃得到；
但這三處是 `subprocess.run` 直接起的，從來沒進過任何一張表：

    src/autolabel/cluster.py    `claude -p` 命名（LLM_TIMEOUT_S=240）
    src/eval/metrics.py         noise floor 的 3 次訓練 + 3 次 anchor 推論
    src/app/routers/eval.py     `_infer`（INFER_TIMEOUT_S=900）

而且三處都包在 `asyncio.to_thread` 裡 —— `to_thread` 不可取消，driver task 被 cancel 之後
那條 thread 會把整個函式跑完（實測按 Stop 後 7 秒它「自己再起了下一顆 seed 的訓練」）。
訊號是唯一打得到它們的東西，所以起子行程一律走本檔的 `run()`：`start_new_session` 自成一組
＋登記進表，`kill_all()` 對整張表 SIGTERM → 收乾 → 逾時 SIGKILL。

契約 §8.4：「回了 cancelled 而 GPU 還在燒，是比不回應更糟的假成功。」
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any

TERM_GRACE_SEC = 8.0  # SIGTERM 之後等它自己收乾的秒數，逾時才 SIGKILL（與 runner 同一個數）

# ponytail: 一張表不分 run —— 契約 §8.1 寫死「同時只准一個 run」（GPU 佇列深度 1），
# 所以 cancel 就是「把本 process 起的子行程全部收掉」。真的要並行多個 run 再改成 per-run 表。
_LOCK = threading.Lock()
_LIVE: set[subprocess.Popen] = set()


def signal_group(pid: int | None, sig: int) -> None:
    """整組送訊號（dataloader worker 那種孫子也要死），拿不到 group 就退回單一 pid。

    ⚠️ **只有自成一組的才准整組殺**：子行程若還在我們自己的 process group 裡
    （沒帶 `start_new_session`），`killpg` 會連 API process 一起打死。
    """
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        pgid = None
    if pgid is not None and pgid != os.getpgid(0):
        try:
            os.killpg(pgid, sig)
            return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run(cmd: list[str], *, timeout: float | None = None, check: bool = False,
        capture_output: bool = False, **kw: Any) -> subprocess.CompletedProcess:
    """`subprocess.run` 的替身：自成一個 session ＋登記進表，Stop 才殺得到。

    行為與 `subprocess.run` 相同（含 `check` / `timeout` / `capture_output`），
    差別只有「它是可被 `kill_all()` 收掉的」。被殺掉時 returncode != 0，呼叫端照原本的
    失敗路徑走（`check=True` 丟 CalledProcessError），所以包著它的 thread 會當場結束，
    不會繼續跑下一顆 seed。
    """
    if capture_output:
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
    kw.setdefault("start_new_session", True)
    proc = subprocess.Popen(cmd, **kw)
    with _LOCK:
        _LIVE.add(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException:  # timeout / Ctrl-C：不留孤兒在背景燒 GPU
        signal_group(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    finally:
        with _LOCK:
            _LIVE.discard(proc)
    done = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    if check:
        done.check_returncode()
    return done


def alive_pids() -> list[int]:
    with _LOCK:
        return [p.pid for p in _LIVE if p.poll() is None]


def kill_all(grace: float = TERM_GRACE_SEC) -> list[int]:
    """SIGTERM 整張表 → 等收乾 → 逾時 SIGKILL。回**真的殺掉**的 pid 清單。

    契約 §8.4 的「確認死了才回」就是這個迴圈；回來之後 `alive_pids()` 必須是空的。
    """
    with _LOCK:
        procs = [p for p in _LIVE if p.poll() is None]
    for p in procs:
        signal_group(p.pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(p.poll() is None for p in procs):
        time.sleep(0.1)
    for p in procs:
        if p.poll() is None:
            signal_group(p.pid, signal.SIGKILL)
            p.wait()
    return [p.pid for p in procs]


# ---------- 自檢：`uv run python -m src.app.procs`（不需要 torch / 不需要 GPU） ----------
# 驗兩件會靜默壞掉的事：run() 起的子行程自成一組（不然 killpg 會把自己打死）、
# kill_all() 回來之後那顆真的死了（假成功就是這條 assert 要抓的東西）。

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    import sys

    assert kill_all() == [], "表一開始應該是空的"

    out = run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "hi", out
    assert not _LIVE, "跑完沒有從表上移除"

    done: list[subprocess.CompletedProcess] = []
    holder: list[int] = []

    def _slow() -> None:
        try:
            run([sys.executable, "-c", "import os,time; print(os.getpid(), flush=True); time.sleep(120)"],
                capture_output=True, text=True)
        except BaseException as exc:  # noqa: BLE001
            done.append(exc)  # type: ignore[arg-type]

    th = threading.Thread(target=_slow, daemon=True)
    th.start()
    for _ in range(100):
        holder = alive_pids()
        if holder:
            break
        time.sleep(0.05)
    assert holder, "子行程沒登記進表 —— Stop 就殺不到它"
    pid = holder[0]
    assert os.getpgid(pid) == pid, "子行程沒有自成一組，killpg 會打到自己人"

    killed = kill_all()
    assert killed == [pid], (killed, pid)
    assert alive_pids() == [], "kill_all 回來了但子行程還活著（假成功）"
    th.join(timeout=5)
    try:
        os.kill(pid, 0)
        raise AssertionError(f"pid {pid} 還在")
    except ProcessLookupError:
        pass
    print(f"procs selfcheck PASS（殺掉 pid {pid}，表已清空）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
