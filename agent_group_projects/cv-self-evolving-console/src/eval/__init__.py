"""metric-auditor 的模組 —— noise floor / bootstrap CI / per-class / verdict / 圖表 series。

⚠️ 除了 `infer.py`（訓練子行程那一側）之外，本套件**一行 torch / ultralytics 都不准出現** ——
它會被 API process import（`src/app/routers/eval.py`），而 base 的 `uv sync` 沒有那兩個。
"""
