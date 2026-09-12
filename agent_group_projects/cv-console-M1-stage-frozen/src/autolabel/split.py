"""前端 `stratifiedSplit` 的 Python 移植 —— 同 seed 同輸入必須**逐筆相同**。

DESIGN「資料真相」規格 (5)：把前端的 `mulberry32` + 最大餘數法移植到 Python，留 assert。
契約：`_Context/api-contract.md` §11 驗收第 2 條（`scripts/selfcheck.py` 會逐筆比對）。
擁有者：dataset-truth。

為什麼要移植而不是兩邊各寫一套：分層抽樣決定哪張圖進 train、哪張進 valid。
前端算一套、後端算一套，只要有一處不同，前端顯示的 split 統計與實際落檔的 dataset
就是兩件事，而且**不會報錯**，只會在 mAP 上變成一個永遠查不出來的偏差。

移植時逐行對照前端的四個細節（錯任何一個都會不一樣，但不會壞）：
  1. 主類別取 `cls[0]`，沒有類別歸 `__none__`
  2. 分層鍵**字串排序**後才處理（Map 的插入序不可靠）
  3. 每層用 `mulberry32(seed >>> 0)` 的 Fisher–Yates 洗牌，**同一個 rng 跨層連續用下去**
  4. 最大餘數法補足，餘數大的先拿；同餘數時保持 `train, valid, test` 原順序（JS sort 是穩定排序）
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

SPLITS = ("train", "valid", "test")
NO_CLASS = "__none__"

_U32 = 0xFFFFFFFF


def _i32(x: int) -> int:
    """JS 的 `x | 0`：截成有號 32 位元。"""
    x &= _U32
    return x - 0x100000000 if x >= 0x80000000 else x


def _u32(x: int) -> int:
    """JS 的 `x >>> 0`：截成無號 32 位元。"""
    return x & _U32


def _imul(a: int, b: int) -> int:
    """JS 的 `Math.imul(a, b)`：32 位元乘法取低位再當有號數。"""
    return _i32(_u32(a) * _u32(b))


def mulberry32(a: int) -> Callable[[], float]:
    """前端同名函式的逐行移植。回傳 [0,1) 的 float，與 JS 位元級相同。"""
    state = _i32(a)

    def rnd() -> float:
        nonlocal state
        state = _i32(state + 0x6D2B79F5)
        t = _imul(state ^ (_u32(state) >> 15), 1 | state)
        t = _i32(t + _imul(t ^ (_u32(t) >> 7), 61 | t)) ^ t
        return _u32(t ^ (_u32(t) >> 14)) / 4294967296

    return rnd


def stratified_split(
    items: Iterable[dict[str, Any]], ratios: dict[str, float], seed: int
) -> dict[str, str]:
    """`[{id, cls:[...]}]` → `{image_id: "train"|"valid"|"test"}`。

    `ratios` 不必加起來等於 1（會自動正規化）；三個全 0 丟 `ValueError`
    （契約 §10 的 `BAD_RANGE`，前端是 `throw new Error('比例不能全為 0')`）。
    """
    total = sum(ratios.get(s, 0) for s in SPLITS)
    if total <= 0:
        raise ValueError("比例不能全為 0")
    p = {s: ratios.get(s, 0) / total for s in SPLITS}

    rnd = mulberry32(_u32(seed))  # 前端是 mulberry32(seed >>> 0)
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        cls = it.get("cls") or []
        groups.setdefault(cls[0] if cls else NO_CLASS, []).append(it)

    out: dict[str, str] = {}
    for key in sorted(groups):  # 排序 → 同 seed 同結果
        arr = list(groups[key])
        for i in range(len(arr) - 1, 0, -1):  # Fisher–Yates，與前端同方向
            j = int(rnd() * (i + 1))
            arr[i], arr[j] = arr[j], arr[i]
        raw = {s: len(arr) * p[s] for s in SPLITS}
        n = {s: int(raw[s] // 1) for s in SPLITS}
        left = len(arr) - sum(n.values())
        # 最大餘數法：餘數大的先拿；Python 的 sorted 與 JS 的 sort 都是穩定排序，同餘數保持原順序
        order = sorted(SPLITS, key=lambda s: -(raw[s] - n[s]))
        for i in range(left):
            n[order[i % 3]] += 1
        c = 0
        for s in SPLITS:
            for _ in range(n[s]):
                out[arr[c]["id"]] = s
                c += 1
    return out


def split_counts(assignment: dict[str, str]) -> dict[str, int]:
    return {s: sum(1 for v in assignment.values() if v == s) for s in SPLITS}


# ---------- 自檢：`uv run python -m src.autolabel.split` ----------
# 黃金值不是我自己算的 —— 是把 claude-design_claude-code/prototype/index.html 裡那段
# stratifiedSplit 原封不動丟給 node 跑出來的（產生指令見 _Context/dataset-notes.md）。
# selfcheck.py 的 assert 2 比對的就是這組 fixture。

FIXTURE = [
    {"id": f"demo{i:04d}", "cls": ([] if i % 7 == 0 else [["scratch", "particle", "ring", "edge-loss"][i % 4]])}
    for i in range(1, 41)
]
FIXTURE_SEED = 42
FIXTURE_RATIOS = {"train": 70, "valid": 20, "test": 10}
# node 跑前端原始碼的輸出（40 筆，逐筆相同才算過）
FIXTURE_EXPECTED = {
    "demo0001": "train", "demo0002": "valid", "demo0003": "train", "demo0004": "valid",
    "demo0005": "train", "demo0006": "train", "demo0007": "train", "demo0008": "train",
    "demo0009": "valid", "demo0010": "train", "demo0011": "test", "demo0012": "train",
    "demo0013": "train", "demo0014": "train", "demo0015": "train", "demo0016": "train",
    "demo0017": "train", "demo0018": "train", "demo0019": "valid", "demo0020": "valid",
    "demo0021": "train", "demo0022": "test", "demo0023": "train", "demo0024": "train",
    "demo0025": "valid", "demo0026": "train", "demo0027": "train", "demo0028": "valid",
    "demo0029": "train", "demo0030": "valid", "demo0031": "train", "demo0032": "train",
    "demo0033": "test", "demo0034": "train", "demo0035": "train", "demo0036": "test",
    "demo0037": "train", "demo0038": "train", "demo0039": "valid", "demo0040": "train",
}


def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    # 1. mulberry32 的前 5 個值與 JS 位元級相同（浮點也要一模一樣，用 repr 比）
    js_first5 = [
        0.6011037519201636, 0.44829055899754167, 0.8524657934904099,
        0.6697340414393693, 0.17481389874592423,
    ]
    rnd = mulberry32(42)
    got = [rnd() for _ in range(5)]
    assert got == js_first5, f"mulberry32 與 JS 不同：{got}"

    # 2. 分層抽樣逐筆等於前端（這條就是 selfcheck.py assert 2 的內容）
    out = stratified_split(FIXTURE, FIXTURE_RATIOS, FIXTURE_SEED)
    diff = {k: (v, FIXTURE_EXPECTED[k]) for k, v in out.items() if v != FIXTURE_EXPECTED[k]}
    assert not diff, f"與前端 stratifiedSplit 不一致：{diff}"
    assert len(out) == len(FIXTURE), "有圖沒有被指派到任何 split"

    # 3. 同 seed 完全相同、不同 seed 不同（前端 ?selftest=1 也有這兩條）
    assert stratified_split(FIXTURE, FIXTURE_RATIOS, 42) == out
    assert stratified_split(FIXTURE, FIXTURE_RATIOS, 43) != out

    # 4. 最大餘數法：三份加起來剛好等於總數，不會少一張也不會多一張
    assert sum(split_counts(out).values()) == len(FIXTURE)
    one = stratified_split(FIXTURE, {"train": 1, "valid": 0, "test": 0}, 1)
    assert set(one.values()) == {"train"}
    try:
        stratified_split(FIXTURE, {"train": 0, "valid": 0, "test": 0}, 1)
        raise AssertionError("比例全 0 沒有丟例外")
    except ValueError:
        pass

    # 5. 無類別的那一層走 `__none__`（M1 所有圖都還沒有類別，退化路徑要能跑）
    none_only = stratified_split([{"id": "a"}, {"id": "b", "cls": []}], FIXTURE_RATIOS, 42)
    assert set(none_only) == {"a", "b"}

    print(f"split selfcheck PASS（fixture {len(FIXTURE)} 筆，{split_counts(out)}）")


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
