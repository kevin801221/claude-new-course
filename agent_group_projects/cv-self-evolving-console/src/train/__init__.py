"""training-engineer 的模組：選型（select）、子行程管理（runner）、子行程內的事件回呼（callbacks）。

三個檔案刻意分在 process 的兩側：
  - `select.py` / `runner.py` 在 **API process** 內跑，**一行 torch 都不准 import**
    （base 的 `uv sync` 沒有 torch，import 到就整個 app 起不來）。
  - `callbacks.py` 只在**訓練子行程**內被 import（`scripts/train_yolo.py`），那裡才有 torch。
"""
