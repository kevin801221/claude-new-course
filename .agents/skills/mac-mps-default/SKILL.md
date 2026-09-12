---
name: mac-mps-default
description: Use whenever the task involves training, inference, fine-tuning, or running PyTorch / ML models on this Mac. Automatically default to MPS (Metal Performance Shaders) instead of CUDA, set up the standard fallback env vars, and warn about MPS-incompatible ops. Triggers on phrases like "train", "inference", "fine-tune", "GPU", "cuda", "model.to()", "torch.device", or whenever ML/DL code is being written.
---

# mac-mps-default

Kevin 用 **Mac (Apple Silicon)**。任何牽涉 PyTorch / 訓練 / 推論的程式碼**預設用 MPS**，不是 CUDA、不是 CPU。

## 黃金規則

1. **`device='mps'`，不是 `'cuda'`**
2. **load checkpoint 要 `map_location='mps'`**
3. **`torch.backends.mps.is_available()` 檢查可用性**
4. **設 `PYTORCH_ENABLE_MPS_FALLBACK=1`**：MPS 不支援的 op 自動 fallback 到 CPU，避免崩潰
5. **batch size 比 CUDA 小**：Apple Silicon 統一記憶體跟 RAM 共用，太大會 swap

## 標準起手式

```python
import os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch

def get_device():
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")

device = get_device()
print(f"Using device: {device}")
```

## Ultralytics YOLO

```python
from ultralytics import YOLO

model = YOLO("yolov8n.pt")
model.train(
    data="data.yaml",
    device="mps",          # ← 不是 0 / 'cuda'
    batch=8,               # 不要塞太大
    epochs=50,
)
```

## 翻譯對照表

| 看到 | 改成 |
|---|---|
| `device='cuda'` | `device='mps'` |
| `.cuda()` | `.to('mps')` |
| `torch.cuda.is_available()` | `torch.backends.mps.is_available()` |
| `nvidia-smi` | `system_profiler SPDisplaysDataType` 或 `mactop` |
| `CUDA_VISIBLE_DEVICES` | 沒對應，MPS 只有一張 |
| `pin_memory=True` | MPS 不需要，可拿掉或設 False |
| `num_workers=8` | 改 `num_workers=4` 或更低（Mac 通常更少 core） |

## MPS 的雷區

| 雷 | 處理 |
|---|---|
| 某 op 報錯 `not implemented for MPS` | `export PYTORCH_ENABLE_MPS_FALLBACK=1` |
| `bfloat16` 在舊 PyTorch 不穩 | 用 `float32` 或升到最新 PyTorch |
| 訓練到一半 RAM 爆 | batch size 砍半（Mac 統一記憶體會吃光） |
| `torch.compile()` 在 MPS 還沒 100% 穩 | 教學/MVP 階段不開 compile |
| 多 GPU `DataParallel` | MPS 不支援多卡，**只有一張**，移除 DataParallel |
| `pin_memory` 警告 | 拿掉這個參數 |

## 環境檢查腳本（一鍵跑）

寫到 `scripts/check_mps.py`：

```python
import torch
print(f"PyTorch: {torch.__version__}")
print(f"MPS available: {torch.backends.mps.is_available()}")
print(f"MPS built: {torch.backends.mps.is_built()}")

if torch.backends.mps.is_available():
    x = torch.randn(1000, 1000, device='mps')
    y = x @ x
    print(f"MPS works ✅, result shape: {y.shape}")
```

用 `uv run python scripts/check_mps.py`。

## 何時例外

- **使用者明說要 CPU**：例如 debug、單元測試 → 用 CPU 沒問題
- **遠端訓練（雲端 GPU）**：CUDA 沒問題，但要明確問清楚是否要切到雲
- **跨平台 lib**：寫成 `device='auto'` 由 lib 自選

## 一句話總結

> **「Mac = MPS」是反射動作。寫 `'cuda'` 之前停一下。**
