"""離線 demo 的真點陣 wafer 合成器 + 免費精確 GT。

契約：`_Context/api-contract.md` §11（檔案佈局、256×256 PNG、id 規則、gt.json 形狀）。
擁有者：dataset-truth。CLI 入口是 `scripts/gen_demo_wafers.py`，邏輯放在這裡是為了讓
`src/app/routers/dataset.py` 能直接 import（scripts/ 不是套件，硬 import 會變成 sys.path 魔術）。

**為什麼要重寫**：既有前端那 48 張是 SVG data-URI —— SVG 沒有像素，
`scipy.ndimage.label` 在上面跑不出任何東西。這裡產的是真的 numpy 陣列存成 PNG。

**GT 為什麼是免費且精確的**：缺陷遮罩是我們自己畫的，所以「框在哪」不是猜的，
是同一份遮罩的連通分量外接框。合成資料的標註成本 = 0，這是離線 demo 唯一能做到
IoU 校準的原因（真資料要等 M3 的 Roboflow 人工 anchor，門檻不同，見 §11「兩把不同的尺」）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = PROJECT_ROOT / "01-raw-data" / "demo"
GT_PATH = DEMO_DIR / "gt.json"

GT_VERSION = 1
IMAGE_PX = 256          # 契約 §11 凍結：256×256 RGB
GRID = 64               # 64×64 die 網格
DIE_PX = IMAGE_PX // GRID   # 一顆 die = 4×4 px
WAFER_R_DIES = 30.0     # 晶圓半徑（die 為單位）
DEFAULT_COUNT = 120
DEFAULT_SEED = 42

# 4 種缺陷型態沿用既有 demo（契約 §11）。輪流發放，120 張剛好每種 30 張。
SHAPES = ("scratch", "particle", "ring", "edge-loss")

# 配色只取 design-system/tokens.css 的 --ds-* 變數（team-roles §4），不自己發明 hex。
BG = (0x14, 0x1B, 0x3D)      # --ds-navy-900 晶圓外
GOOD_DIE = (0x1E, 0x27, 0x61)  # --ds-navy-800 良品 die
DIE_LINE = (0x2E, 0x37, 0x68)  # --ds-line     die 邊界格線
DEFECT = (0xD9, 0x77, 0x57)    # --ds-coral    缺陷 die


def image_name(i: int) -> str:
    return f"wafer_{i:04d}.png"


def image_id(i: int) -> str:
    return f"demo{i:04d}"


def image_path(image_id_: str, demo_dir: Path = DEMO_DIR) -> Path | None:
    """`demo0007` → `01-raw-data/demo/wafer_0007.png`（契約 §11 凍結的解析規則）。

    嚴格照 §11 的 `demo{i:04d}`：長度必須是 8、後四碼必須是 ASCII 阿拉伯數字。
    `str.isdigit()` 對上標數字（`demo²`）回 True 而 `int()` 會丟 ValueError → 500，
    契約 §10 說「500 不在契約裡」；`demo7` / `demo0000007` 也會被擋，否則同一張圖
    會有好幾個 id 與好幾組 ETag。
    """
    suffix = image_id_[4:]
    if not image_id_.startswith("demo") or len(suffix) != 4 or not suffix.isascii() or not suffix.isdigit():
        return None
    path = demo_dir / image_name(int(image_id_[4:]))
    return path if path.exists() else None


# ---------- die 網格上的四種型態（純幾何，回傳 die 遮罩） ----------

def _grid() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    c = (GRID - 1) / 2
    yy, xx = np.mgrid[0:GRID, 0:GRID].astype(np.float64)
    rr = np.hypot(xx - c, yy - c)
    return xx - c, yy - c, rr


def _shape_mask(shape: str, rng: np.random.Generator) -> np.ndarray:
    dx, dy, rr = _grid()
    inside = rr <= WAFER_R_DIES
    R = WAFER_R_DIES

    if shape == "scratch":
        # 一條細長刮痕：對線段取距離場，厚度 ~1 die（所以 4 px 寬、60+ px 長）
        theta = rng.uniform(0, np.pi)
        length = rng.uniform(16, 28)
        half = np.array([np.cos(theta), np.sin(theta)]) * length / 2
        mid = rng.uniform(-0.45, 0.45, 2) * R
        p0, p1 = mid - half, mid + half
        seg = p1 - p0
        t = np.clip(((dx - p0[0]) * seg[0] + (dy - p0[1]) * seg[1]) / (seg @ seg), 0, 1)
        dist = np.hypot(dx - (p0[0] + t * seg[0]), dy - (p0[1] + t * seg[1]))
        mask = dist <= rng.uniform(0.6, 1.2)

    elif shape == "particle":
        # 1–3 顆團塊。重疊時會合成一個分量 —— GT 直接取合併後的外接框，不另外修
        mask = np.zeros_like(rr, dtype=bool)
        for _ in range(int(rng.integers(1, 4))):
            ang, rad = rng.uniform(0, 2 * np.pi), rng.uniform(0.15, 0.75) * R
            cx, cy = rad * np.cos(ang), rad * np.sin(ang)
            mask |= np.hypot(dx - cx, dy - cy) <= rng.uniform(1.8, 4.6)

    elif shape == "ring":
        # 甜甜圈：0.35–0.65R 的閉合環，一個連通分量，外接框等於整個環的方框
        mask = np.abs(rr - rng.uniform(0.35, 0.65) * R) <= rng.uniform(1.0, 1.6)

    elif shape == "edge-loss":
        # 外圈缺角：0.84–0.95R 上的一段弧（60°–150°）
        r0, span, phi0 = rng.uniform(0.84, 0.95) * R, np.radians(rng.uniform(60, 150)), rng.uniform(0, 2 * np.pi)
        dphi = np.abs(np.angle(np.exp(1j * (np.arctan2(dy, dx) - phi0))))
        mask = (np.abs(rr - r0) <= rng.uniform(1.0, 1.8)) & (dphi <= span / 2)

    else:
        raise ValueError(f"未知型態 {shape!r}")

    return mask & inside


def _noise_dies(shape_mask: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """散落的單顆壞 die。**刻意不進 GT** —— 單點不成型態（對應 WM-811K 的 Random/None 跳過），
    它存在的意義是逼 auto-label 真的要去雜點，不是讓遮罩乾乾淨淨地作弊。"""
    _, _, rr = _grid()
    # 離既有缺陷 2 die 以上才放，否則會黏成同一個分量、把 GT 撐大
    forbidden = ndimage.binary_dilation(shape_mask, np.ones((5, 5), bool))
    free = np.argwhere((rr <= WAFER_R_DIES - 1) & ~forbidden)
    picks = rng.choice(len(free), size=int(rng.integers(3, 11)), replace=False)
    noise = np.zeros_like(shape_mask)
    for y, x in free[picks]:
        noise[y, x] = True
    return noise


# ---------- 上色成真點陣 ----------

def _render(defect_dies: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    _, _, rr = _grid()
    inside = rr <= WAFER_R_DIES

    up = lambda m: np.kron(m, np.ones((DIE_PX, DIE_PX), bool))  # noqa: E731
    inside_px, defect_px = up(inside), up(defect_dies)

    img = np.zeros((IMAGE_PX, IMAGE_PX, 3), np.float64)
    img[:] = BG
    img[inside_px] = GOOD_DIE

    # die 格線只畫在良品區：畫過缺陷 die 會把一個分量切成好幾塊，連通分量就爛了
    grid_px = np.zeros((IMAGE_PX, IMAGE_PX), bool)
    grid_px[::DIE_PX, :] = True
    grid_px[:, ::DIE_PX] = True
    img[grid_px & inside_px & ~defect_px] = DIE_LINE

    # 缺陷強度抖動（0.75–1.0 混向良品色）：模擬檢測訊號強弱，仍遠高於偵測門檻
    blend = np.repeat(
        np.repeat(rng.uniform(0.75, 1.0, (GRID, GRID)), DIE_PX, 0), DIE_PX, 1
    )[..., None]
    img[defect_px] = (blend * np.array(DEFECT) + (1 - blend) * np.array(GOOD_DIE))[defect_px]

    img += rng.normal(0, 2.5, img.shape)  # 感測雜訊，讓門檻不是剛好貼邊
    return np.clip(img, 0, 255).astype(np.uint8)


def _boxes_from(defect_px: np.ndarray) -> list[dict[str, float]]:
    """乾淨缺陷遮罩 → 連通分量外接框（正規化 cx,cy,w,h，契約 §1 的 YOLO 慣例）。"""
    labels, n = ndimage.label(defect_px, structure=np.ones((3, 3), int))
    out = []
    for sy, sx in ndimage.find_objects(labels) if n else []:
        out.append(
            {
                "cx": round((sx.start + sx.stop) / 2 / IMAGE_PX, 6),
                "cy": round((sy.start + sy.stop) / 2 / IMAGE_PX, 6),
                "w": round((sx.stop - sx.start) / IMAGE_PX, 6),
                "h": round((sy.stop - sy.start) / IMAGE_PX, 6),
            }
        )
    return out


def generate(
    count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED, out_dir: Path = DEMO_DIR
) -> dict[str, Any]:
    """產 `count` 張 PNG + gt.json，回傳 gt 內容。同 seed 同輸出（逐位元組相同）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(1, count + 1):
        # 每張自己一組 rng：換 count 不會讓既有圖整批位移（第 7 張永遠是第 7 張）
        rng = np.random.default_rng([seed, i])
        shape = SHAPES[(i - 1) % len(SHAPES)]
        dies = _shape_mask(shape, rng)
        noise = _noise_dies(dies, rng)
        img = _render(dies | noise, rng)
        Image.fromarray(img).save(out_dir / image_name(i))
        images.append(
            {
                "id": image_id(i),
                "name": image_name(i),
                "shape": shape,  # 合成標籤，只准 selfcheck 與 M2 事後驗證讀（契約 §11）
                "boxes": _boxes_from(np.kron(dies, np.ones((DIE_PX, DIE_PX), bool))),
            }
        )

    gt = {
        "version": GT_VERSION,
        "seed": seed,
        "image_size": [IMAGE_PX, IMAGE_PX],
        "images": images,
    }
    (out_dir / "gt.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), "utf-8")
    return gt


def load_gt(demo_dir: Path = DEMO_DIR) -> dict[str, Any] | None:
    path = demo_dir / "gt.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def ensure(
    count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED, out_dir: Path = DEMO_DIR
) -> dict[str, Any]:
    """要有 `count` 張且 seed 對得上才沿用，否則重產。按鈕按下去不該因為沒先跑腳本就失敗。"""
    gt = load_gt(out_dir)
    # ponytail: 契約 §11 把 demo 影像的路徑凍成單一目錄（`GET /images/demo0007` →
    # `01-raw-data/demo/wafer_0007.png`），所以整個目錄是「一個 seed 的成品，最後寫的人贏」。
    # 換 seed 會整批重畫，連舊 run 的縮圖也跟著換。至少不讓它被截短：永遠產滿 DEFAULT_COUNT。
    count = max(count, DEFAULT_COUNT)
    if (
        gt
        and gt.get("seed") == seed
        and gt.get("version") == GT_VERSION
        and len(gt.get("images", [])) >= count
        and all((out_dir / rec["name"]).exists() for rec in gt["images"][:count])
    ):
        return gt
    return generate(count, seed, out_dir)
