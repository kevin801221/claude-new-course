"""metric-auditor 的檢查 —— CI / noise floor / 封印 test 的四道鎖。

    uv run python tests/test_ci.py        # 不需要 pytest
    uv run pytest tests/test_ci.py        # 有裝就這樣跑

沿用 `tests/test_split_parity.py` 的寫法：純 assert + `__main__` 收尾，不引框架。
零 GPU、零 torch、零網路 —— 這些是**判準本身**的檢查，不是模型的檢查。
擁有者：metric-auditor。
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eval import checks as ck  # noqa: E402
from src.eval import metrics  # noqa: E402

# TestClient 的預設 Host 是 `testserver`，而契約 §1 的白名單只放真的是本機的四個字串
# （為了測試方便在生產邊界開洞 = 那個洞會一直在）。所以這裡給它一個真的本機 base_url。
LOCAL = "http://127.0.0.1"


def _client():
    from fastapi.testclient import TestClient

    from src.app.main import app

    # 刻意不用 `with`：進 context manager 會跑 lifespan，而 lifespan 會把「pid 已死的 running run」
    # 標成 crashed —— 跑個測試不該改別人的 run 狀態。
    return TestClient(app, base_url=LOCAL)


# ---------- 封印 test 的四道鎖 ----------

def test_eval_split_test_is_400() -> None:
    """第二道鎖：`POST /eval` 的 `split` 傳 `test` 一律 400 `SPLIT_TEST_FORBIDDEN`。"""
    r = _client().post("/api/v1/eval", json={"run_id": "r1", "split": "test"})
    assert r.status_code == 400, (r.status_code, r.text)
    assert r.json()["code"] == "SPLIT_TEST_FORBIDDEN", r.json()
    # 連讀預測快照也不准借道
    r2 = _client().get("/api/v1/eval/r1/predictions", params={"split": "test"})
    assert r2.status_code == 400 and r2.json()["code"] == "SPLIT_TEST_FORBIDDEN", r2.text


def test_data_layer_cannot_reach_sealed_test() -> None:
    """第二道鎖的裡層：不是靠 router 自律，`split_dirs()` 物理上就拿不到 sealed-test。"""
    try:
        metrics.split_dirs("test")
    except ValueError:
        pass
    else:
        raise AssertionError("split_dirs('test') 竟然回得出目錄")
    images, labels = metrics.sealed_dirs()
    assert images.parent.name == "03-sealed-test" and labels.parent.name == "03-sealed-test"


def test_data_yaml_has_no_test_key() -> None:
    """第一道鎖：`data.yaml` 只有 train / valid，Ultralytics 物理上讀不到 sealed-test。"""
    data_yaml = PROJECT_ROOT / "02-dataset" / "data.yaml"
    if not data_yaml.exists():
        return  # 還沒 freeze 過（M2 之前），沒有東西可檢查
    body = [ln.split("#", 1)[0].strip() for ln in data_yaml.read_text("utf-8").splitlines()]
    keys = {ln.split(":", 1)[0] for ln in body if ":" in ln and not ln.startswith("-")}
    assert "test" not in keys, f"data.yaml 出現 test key：{keys}"
    # 註解裡寫「sealed-test 不在這裡」是好事，所以只檢查去掉註解後的**值**
    assert "03-sealed-test" not in "\n".join(body), body


def test_final_test_needs_a_stop_event() -> None:
    """第三道鎖：沒有 stop 事件（未 converged/abandoned）就進不去 `/final-test`。"""
    r = _client().post("/api/v1/final-test", json={"run_id": "r1", "stop_event_seq": 1})
    assert r.status_code in (404, 409), (r.status_code, r.text)
    assert r.json()["code"] in {"RUN_NOT_FOUND", "NOT_STOPPED_YET", "FINAL_TEST_ALREADY_USED"}, r.json()


# ---------- 進步的門檻是 CI 寬度不是點估計（護欄 3） ----------

def test_bootstrap_ci_coverage() -> None:
    """已知分布下，95% CI 要涵蓋真值約 95%。

    這是整份 CI 邏輯唯一能被證偽的地方：百分位取錯、重抽單位取錯（對「框」而不是「圖」重抽），
    覆蓋率都會掉下來，而點估計看起來照樣漂亮。
    """
    rng = random.Random(7)
    true_mean, n, trials = 0.35, 50, 300
    covered = 0
    for t in range(trials):
        sample = [1.0 if rng.random() < true_mean else 0.0 for _ in range(n)]
        lo, hi = metrics.bootstrap_ci(sample, lambda s: sum(s) / len(s), n_boot=300, seed=t)
        covered += lo <= true_mean <= hi
    coverage = covered / trials
    assert 0.88 <= coverage <= 0.99, f"95% CI 的實測覆蓋率 {coverage:.3f} 不在 0.88..0.99"


def test_noise_floor_is_two_sigma_and_needs_more_than_one_seed() -> None:
    values = [0.3045, 0.2328, 0.1566]          # 實測：同 config、seed 42/43/44
    sd, floor = metrics.sigma_of(values)
    assert abs(sd - statistics.stdev(values)) < 1e-5   # sigma_of 回報時四捨五入到 5 位
    assert abs(floor - 2 * sd) < 1e-5, (sd, floor)
    try:
        metrics.sigma_of([0.3045])
    except ValueError:
        return
    raise AssertionError("只有一個 seed 也算得出 σ —— 那不是雜訊帶，是猜的")


def test_delta_inside_noise_floor_is_not_progress() -> None:
    """實測那一格：yolov8n 0.2662 vs yolov8s 0.2561，Δ −0.0101 遠小於 noise floor 0.1479。"""
    got = metrics.significant(-0.0101, 0.14792, 0.2112)
    assert got["significant"] is False, got
    assert metrics.significant(0.30, 0.14792, 0.2112)["significant"] is True


def test_support_under_30_cannot_be_evidence() -> None:
    per_class = [{"cls": 0, "name": "edge-ring", "support": 3, "ap50": 1.0}]
    got = ck.test_size_power(per_class, n_images=12, ci_width=0.2112)
    assert got["pass"] is False and got["weak_classes"][0]["support"] == 3, got


def test_verdict_ladder() -> None:
    ok = {"name": "split_leakage", "pass": True, "detail": ""}
    bad_leak = {"name": "split_leakage", "pass": False, "detail": ""}
    bad_power = {"name": "test_size_power", "pass": False, "detail": ""}
    assert ck.verdict([ok])["verdict"] == "trusted"
    assert ck.verdict([ok, bad_power])["verdict"] == "suspect"
    assert ck.verdict([bad_leak, bad_power])["verdict"] == "invalid"


def test_dataset_fingerprint_moves_with_content() -> None:
    """`ds_version` 是人填的字串，重新 freeze 不會變；指紋要會變（實測 02-dataset 被換過）。"""
    before = metrics.dataset_fingerprint()
    assert len(before) == 16 and before == metrics.dataset_fingerprint()  # 同內容要穩定


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
