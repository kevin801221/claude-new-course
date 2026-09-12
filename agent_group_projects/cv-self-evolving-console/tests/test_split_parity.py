"""Python 分層抽樣 == 前端 `stratifiedSplit`（DESIGN「資料真相」規格 5 的 assert）。

    uv run --with pytest pytest tests/          # pytest 版
    uv run python tests/test_split_parity.py    # 不裝 pytest 也能跑

pyproject 刻意不裝 pytest（M1 的 `uv sync` 要快），所以兩種都留著。
`scripts/selfcheck.py` 的 assert B 不依賴這個檔，也不依賴 pytest。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.autolabel.split import (  # noqa: E402
    FIXTURE,
    FIXTURE_EXPECTED,
    FIXTURE_RATIOS,
    FIXTURE_SEED,
    mulberry32,
    split_counts,
    stratified_split,
)

PROTOTYPE = PROJECT_ROOT / "prototype" / "index.html"

# node 跑前端 mulberry32(42) 的前 5 個值。浮點要逐位相同 —— 憑感覺寫的 32 位元移植
# 第一個數就會錯（0.6011037724465132 vs 真值），只比 split 結果抓不到這種偏差。
JS_FIRST5 = [
    0.6011037519201636, 0.44829055899754167, 0.8524657934904099,
    0.6697340414393693, 0.17481389874592423,
]


def js_split(items: list[dict], ratios: dict[str, float], seed: int) -> dict[str, str] | None:
    """從 prototype/index.html 原地抽出那兩個函式丟給 node。沒有 node 回 None。"""
    src = PROTOTYPE.read_text(encoding="utf-8")
    start = src.index("function mulberry32")
    end = src.index("\n}\n", src.index("function stratifiedSplit", start)) + 3
    js = src[start:end] + (
        f"\nconsole.log(JSON.stringify(stratifiedSplit("
        f"{json.dumps(items)},{json.dumps(ratios)},{seed})));\n"
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
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_mulberry32_matches_js_bitwise() -> None:
    rnd = mulberry32(42)
    assert [rnd() for _ in range(5)] == JS_FIRST5


def test_fixture_matches_frozen_golden() -> None:
    out = stratified_split(FIXTURE, FIXTURE_RATIOS, FIXTURE_SEED)
    assert out == FIXTURE_EXPECTED
    assert len(out) == len(FIXTURE)
    assert sum(split_counts(out).values()) == len(FIXTURE)  # 最大餘數法不多不少


def test_fixture_matches_live_frontend() -> None:
    js = js_split(FIXTURE, FIXTURE_RATIOS, FIXTURE_SEED)
    if js is None:  # 沒裝 node：上面那條已經蓋住黃金值，這條跳過
        return
    assert js == FIXTURE_EXPECTED, "前端被改過，黃金值過期"
    assert stratified_split(FIXTURE, FIXTURE_RATIOS, FIXTURE_SEED) == js


def test_other_seeds_and_ratios_still_match_frontend() -> None:
    """只比 seed 42 會漏掉「剛好那組對」的巧合，換 seed 與換比例各比一次。"""
    for ratios, seed in (
        (FIXTURE_RATIOS, 7),
        ({"train": 50, "valid": 30, "test": 20}, 42),
        ({"train": 1, "valid": 0, "test": 0}, 3),
    ):
        js = js_split(FIXTURE, ratios, seed)
        if js is None:
            return
        assert stratified_split(FIXTURE, ratios, seed) == js, f"ratios={ratios} seed={seed}"


def test_all_zero_ratio_raises() -> None:
    try:
        stratified_split(FIXTURE, {"train": 0, "valid": 0, "test": 0}, 1)
    except ValueError:
        return
    raise AssertionError("比例全 0 沒有丟 ValueError")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
