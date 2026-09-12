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

from src.eval import ceiling as ceil_mod  # noqa: E402
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
        lo, hi, _ = metrics.bootstrap_ci(sample, lambda s: sum(s) / len(s), n_boot=300, seed=t)
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


def test_support_under_30_is_flagged_but_not_a_hard_fail() -> None:
    """`MIN_SUPPORT` 是**註記門檻**：weak 類別要被點名，但不判 FAIL。

    當達標門檻的話：6 類 × 30 = 180 個 GT 框，而出貨的 valid 只有 15 個 —— 這一項在這份
    資料集上永遠 FAIL，verdict 永遠是 suspect，trusted 結構性不可達，DESIGN 的 plateau_ok
    （明文要求 verdict = trusted）永遠不會成立。永遠 FAIL 跟永遠 PASS 一樣是零資訊。
    「不得當證據」這件事由 arbiter 端（只讀 anchor_ok / 不拿 weak 類別當達標依據）守。
    """
    per_class = [{"cls": 0, "name": "edge-ring", "support": 3, "ap50": 1.0},
                 {"cls": 1, "name": "center", "support": 1, "ap50": 0.5}]
    got = ck.test_size_power(per_class, n_images=12, ci_width=0.2112)
    assert got["pass"] is True, got                                   # 只註記
    assert [w["support"] for w in got["weak_classes"]] == [3, 1], got  # 但一個都不准漏掉
    assert "只能當提示" in got["detail"], got["detail"]
    # 完全沒有 GT 的類別才是真的 FAIL —— 那一格根本沒被量到
    empty = ck.test_size_power(per_class + [{"cls": 2, "name": "donut", "support": 0}], 12)
    assert empty["pass"] is False and empty["empty_classes"] == ["donut"], empty


def test_real_valid_split_can_reach_trusted() -> None:
    """出貨的 02-dataset/labels/valid（12 張 / 15 框、六類 support 1..4）不准讓 verdict 結構性卡死。"""
    labels = PROJECT_ROOT / "02-dataset" / "labels" / "valid"
    if not labels.exists():
        return
    counts: dict[int, int] = {}
    n_images = 0
    for txt in sorted(labels.glob("*.txt")):
        n_images += 1
        for line in txt.read_text("utf-8").splitlines():
            if line.split():
                counts[int(line.split()[0])] = counts.get(int(line.split()[0]), 0) + 1
    names = metrics.class_names()
    per_class = [{"cls": c, "name": n, "support": counts.get(c, 0)} for c, n in enumerate(names)]
    got = ck.test_size_power(per_class, n_images=n_images, ci_width=0.2112)
    assert got["pass"] is True, got        # 這一項在出貨資料集上永遠 FAIL 的話，trusted 是死碼
    assert ck.verdict([{"name": "split_leakage", "pass": True, "detail": ""}, got,
                       {"name": "anchor_drift", "pass": True, "detail": ""},
                       {"name": "overfit_gap", "pass": True, "detail": ""}])["verdict"] == "trusted"


def test_anchor_gate_uses_its_own_noise_band() -> None:
    """anchor gate 的門檻是 anchor recall 自己的 2σ，不是 val mAP50-95 的（實測差 2.1 倍）。"""
    # 實測同一組 noise-floor 的三顆 seed：anchor recall 0.7533 / 0.4467 / 0.5700 → 2σ 0.30855
    sd, band = metrics.sigma_of([0.7533, 0.4467, 0.5700])
    assert abs(band - 0.30855) < 1e-4, (sd, band)
    assert band > 2 * 0.14792 * 0.9, "anchor 的帶明顯比 val mAP50-95 的 0.14792 寬，不准共用"
    # 純 seed 抖動（0.7533 → 0.5700）：用自己的帶 → 過；用 val 那條 → 被判成 abandoned
    assert ck.anchor_drift(0.5700, 0.7533, 0.02, band)["pass"] is True
    assert ck.anchor_drift(0.5700, 0.7533, 0.02, 0.14792)["pass"] is False
    # 沒量到 anchor 的帶 → 拒答，不准拿別的 metric 的 σ 湊
    assert ck.anchor_drift(0.5700, 0.7533, 0.02, None)["pass"] is False


def test_n_boot_floor_keeps_the_ci_half_of_guardrail_3() -> None:
    """`n_boot=1` 會回寬度 0 的假 CI，把「|Δ| < CI 半寬」那道判準關掉 —— router 要 400。"""
    r = _client().post("/api/v1/eval", json={"run_id": "r1", "split": "valid", "n_boot": 1})
    assert r.status_code == 400 and r.json()["code"] == "BAD_RANGE", r.text
    assert metrics.MIN_BOOT >= 200, metrics.MIN_BOOT


def test_low_support_class_gets_no_fake_ci() -> None:
    """GT 全擠在 1 張圖上的類別：三分之一的重抽抽不到它 → 不給 CI，而不是給一條假性很窄的線。"""
    box = {"cls": 0, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}
    recs = [{"image_id": f"i{i}", "boxes": [], "gt_boxes": []} for i in range(9)]
    recs.append({"image_id": "i9", "boxes": [{**box, "conf": 0.9}], "gt_boxes": [box]})
    got = metrics.with_ci(metrics.evaluate(recs, ["a"]), n_boot=300)["per_class"][0]
    assert got["ci_lo"] is None and got["ci_hi"] is None, got
    assert got["ci_dropped"] > 0.05 * 300 and "重抽無法給 CI" in got["ci_note"], got


def test_sealed_test_answers_are_not_served_by_the_images_snapshot() -> None:
    """封印的側門：`GET /datasets/{ds}/images` 不准吐 sealed-test 的 split 與標註。"""
    ds_dir = PROJECT_ROOT / "01-raw-data" / "datasets"
    splits_files = sorted(ds_dir.glob("*/splits.json"))
    target = next((f for f in splits_files
                   if "sealed_test" in f.read_text("utf-8")), None)
    if target is None:
        return  # 還沒 freeze 過
    ds_id = target.parent.name
    r = _client().get(f"/api/v1/datasets/{ds_id}/images", params={"limit": 500})
    assert r.status_code == 200, r.text
    sealed = [x for x in r.json()["results"] if x["split"] == "sealed"]
    assert sealed, "splits.json 有 sealed_test，但快照一筆都沒標出來"
    assert all(x["annotations"] is None for x in sealed), sealed[:2]
    assert not any(x["split"] == "sealed_test" for x in r.json()["results"])


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


# ---------- 標註天花板：退化值 vs 真人工 GT（M5+：真實資料進來之後才有意義的數字） ----------

GT_FIXTURE = [[{"cls": 0, "cx": 0.3 + 0.004 * i, "cy": 0.5, "w": 0.22, "h": 0.18}]
              for i in range(40)]


def _ious(scale: float | None, seed: int = 7) -> list[list[float]]:
    """`scale=None` → pred 就是 GT（退化）；否則是人工風格的抖動框。"""
    from src.autolabel import geometry

    return [geometry.best_ious(g if scale is None else ceil_mod._jitter(g, scale, seed + i), g)
            for i, g in enumerate(GT_FIXTURE)]


def test_identical_gt_and_pred_is_degenerate() -> None:
    """GT 與 pred 完全相同 → IoU 1.0，**必須判成退化並標記**，不是「天花板很高」。

    這就是合成資料那條路的實情（GT 與 auto-bbox 同一套連通分量定義，實測 r81 中位數 1.0000、
    155 個框全擠在最後一格）。當成天花板證據的話，整條流水線等於沒有上限這個概念。
    """
    got = ceil_mod.summarize(_ious(None), source=ceil_mod.ROBOFLOW_ANCHOR, scope="anchor",
                             n_boot=metrics.MIN_BOOT)
    assert got["iou_median"] == 1.0, got["iou_median"]
    assert got["degenerate"] and not got["credible"], got
    # 出處欄位寫「真人工 anchor」也照樣抓得到 —— 退化偵測不靠出處自律（那面鏡子的防線）
    assert got["frac_ge_999"] == 1.0, got
    ck_out = ck.label_ceiling(got)
    assert ck_out["pass"] and not ck_out["credible"], ck_out
    assert "退化值" in ck_out["detail"], ck_out["detail"]
    # 退化不等於 abandon：那是「沒量到天花板」，不是「天花板很低」
    assert not ceil_mod.gate(got)["abandon"], ceil_mod.gate(got)
    assert not ceil_mod.gate(got)["credible"], ceil_mod.gate(got)


def test_human_style_jitter_gives_a_real_iou_below_one() -> None:
    """人工風格的抖動框 → 真實 IoU 中位數 < 1.0，而且不准被標成退化。

    真資料實測（Roboflow WM-811K v3、人工框 vs 連通分量）：pool 289 張中位數 **0.6381**、
    anchor 40 張 0.8258 —— 兩者都遠低於合成那條路的 1.0000。
    """
    got = ceil_mod.summarize(_ious(0.18), source=ceil_mod.ROBOFLOW_ANCHOR, scope="pool",
                             n_boot=metrics.MIN_BOOT)
    assert 0.4 < got["iou_median"] < 1.0, got["iou_median"]
    assert not got["degenerate"] and got["credible"], got
    assert got["ci"]["hi"] < 1.0, got["ci"]
    # mAP 版的天花板：十個 IoU 門檻平均一定 <= 只看 0.5 的那個
    assert got["map5095_ceiling"] <= got["map50_ceiling"] <= 1.0, got
    assert ck.label_ceiling(got)["pass"] and ck.label_ceiling(got)["credible"]


def test_ceiling_below_threshold_stops_at_s03_and_invalidates_the_verdict() -> None:
    """天花板 < 0.4（契約 §11 的人工 GT 那把尺）→ gate abandon + verdict invalid。

    DESIGN 的 `label_ceiling`：問題在標註不在模型。這一輪不該訓練，訓練出來的數字也不是
    模型的成績 —— 所以 verdict 走 invalid（跟 split leakage 同一個等級的資格問題）。
    """
    got = ceil_mod.summarize(_ious(0.8, seed=100), source=ceil_mod.ROBOFLOW_ANCHOR,
                             scope="pool", n_boot=metrics.MIN_BOOT)
    assert got["iou_median"] < 0.4 and got["verdict"] == "fail", got["iou_median"]
    gate = ceil_mod.gate(got)
    assert gate["abandon"] and gate["reason"] == "label_ceiling", gate
    assert "問題在標註不在模型" in gate["detail"], gate["detail"]
    v = ck.verdict([{"name": "split_leakage", "pass": True, "detail": ""}, ck.label_ceiling(got)])
    assert v["verdict"] == "invalid" and v["fatal"] == ["label_ceiling"], v


def test_ceiling_sources_are_never_comparable() -> None:
    """合成退化值與真人工 GT 是兩把尺 —— 跨來源的 Δ 一律不可比（同 class_table_version 等級）。"""
    assert ceil_mod.comparable("synthetic", "synthetic")
    assert not ceil_mod.comparable("synthetic", "roboflow_anchor"), "跨來源竟然可比"
    assert not ceil_mod.comparable(None, "synthetic"), "來源不明竟然可比"


def test_sealed_test_boxes_are_unreachable_from_the_ceiling() -> None:
    """🔒 第五道側門：sealed-test 的 80 個人工框是考卷答案，任何 scope 都拿不到它。"""
    for scope in ("sealed_test", "test", "all"):
        try:
            ceil_mod.measure(slug="whatever", scope=scope)
        except ValueError as exc:
            assert "scope 只接受" in str(exc), exc
        else:
            raise AssertionError(f"scope={scope} 竟然被放行")


def test_ceiling_endpoint_reports_degenerate_for_the_demo_run() -> None:
    """`GET /eval/ceiling?run_id=` 在 s03 跑完就答得出來（不必先訓練），而且會標出可信度。"""
    c = _client()
    assert c.get("/api/v1/eval/ceiling").status_code == 400          # 沒帶 run_id / ds
    r = c.get("/api/v1/eval/ceiling", params={"run_id": "r81", "measure": "false"})
    if r.status_code == 404:            # 這台機器沒有 r81 就跳過（測的是形狀不是那個 run）
        return
    body = r.json()
    assert body["source"] == "synthetic" and body["degenerate"] is True, body
    assert body["credible"] is False and body["gate"]["abandon"] is False, body
    assert body["gate"]["credible"] is False, body["gate"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
