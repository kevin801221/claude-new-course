"""`source="roboflow"` 的真實資料來源 —— 下載 + 人工標註索引 + id → 檔案解析。

擁有者：**dataset-truth**。契約 `_Context/api-contract.md` §8.5（`source:"roboflow"`）、
§4（`ds.image` / `label.*`）、§11（兩把不同的尺）。決策紀錄在 `_Context/dataset-notes.md` §17。

**抄誰**：`agent_group_projects/computer-vision-wafer-detection/scripts/download_dataset.py`
（驗證過能跑）。沿用的四件事一個字都沒改：
  workspace `wm811k-paasr` / project `wm811k` / version 3 / format `yolov8`
以及「指定版本不存在 → 退回 `max(version_ids)`」的 fallback。

**偏離一處（刻意）**：那支用 `roboflow` 套件，這支用 stdlib `urllib` + `zipfile`。
理由是硬規矩「真實資料那條路只准有一個啟動指令」：`roboflow` 是新依賴，而
`pyproject.toml` 是 console-owner 的共同地基，加了就多一個 `--extra`（`--extra train`
那個坑已經害過講師一次）。實測套件在做的就是這三個 HTTP 呼叫（下面 `download()` 的註解
有實際回應），所以「抄做法」抄得到，只是沒抄那個 import。**零新依賴**：
`python-dotenv` 是 `uvicorn[standard]` 的既有相依（`uv.lock` 查得到），base `uv sync` 就有。

為什麼不用 Roboflow 的 `search` API 逐張抓（既有前端 `loadRoboflow()` 那條路）：
那條只拿得到縮圖 URL 與 `annotations.count`，**拿不到框的座標**。人工 bbox 只在
export（`yolov8` zip 的 `labels/*.txt`）裡，而「人工框當 GT」正是這條路的全部價值。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "01-raw-data" / "roboflow"
ENV_PATH = PROJECT_ROOT / ".env"
ENV_KEY = "ROBOFLOW_API_KEY"  # 沿用既有變數名（DESIGN 護欄 10：不改名）

# 預設就是驗證過的那一份（WM-811K 晶圓瑕疵，409 張，已有人工 bbox）。
# 三個值都能從 API 參數帶，預設只是「不帶參數時按鈕還是按得動」。
WORKSPACE, PROJECT, VERSION, FORMAT = "wm811k-paasr", "wm811k", 3, "yolov8"

API = "https://api.roboflow.com"
API_TIMEOUT_S = 30
ZIP_TIMEOUT_S = 300
ID_PREFIX = "rf"
KEY_RE = re.compile(r"[A-Za-z0-9]{10,64}")  # Roboflow Private API Key 實測是 20 碼英數
# `?api_key=<真 key>`（API）與簽名連結的 `?key=<真 key>`（zip）。訊息一律先過這一支。
SECRET_RE = re.compile(r"(?i)\b(api_key|key)=[^&\s'\"]*")


def _scrub(text: Any) -> str:
    """把任何 `api_key=…` / `key=…` 的值抹掉再放進錯誤訊息。

    ⛔ 這不是潔癖：uvicorn 的 stdout+stderr 被 `scripts/run_m2.py` 與 `scripts/selfcheck.py`
    導進 `runs/m2-uvicorn.log` / `runs/selfcheck-uvicorn.log`，而失敗時印的那句就是
    「看 runs/m2-uvicorn.log」—— 等於主動請學生去打開一個含 key 的檔，然後投影、截圖、貼 issue。
    例外的 `__str__` 是最容易漏的那條路（`http.client.InvalidURL` 的訊息就帶著整串 url）。
    """
    return SECRET_RE.sub(r"\1=***", str(text))


def _q(value: Any) -> str:
    """路徑片段一律 percent-encode。

    不做的話，workspace 打一個空白就走 `http.client.InvalidURL`、打中文就走
    `UnicodeEncodeError`（兩者都不是 OSError，從 `_get()` 逃出去變成 HTTP 500）。
    控制字元根本不該走到 http.client 那一層。
    """
    return urllib.parse.quote(str(value), safe="")


# 繁中指引只寫這一份：四個呼叫點（api_key / router / __main__ / 課堂講稿）共用同一段字，
# 不然 UI 說去 Settings、CLI 說去 .env，學生會以為是兩件事。
KEY_HOWTO = (
    "到 https://app.roboflow.com → 右上頭像 → Settings → 你的 Workspace → Roboflow API "
    "複製 **Private API Key**（不是 Publishable Key），"
    f"然後在專案根目錄 `cp .env.example .env`，把 `{ENV_KEY}=` 那一行填好"
    "（key 只在 server 端讀，前端永遠不持 key）"
)


class RoboflowError(RuntimeError):
    """帶機器碼與 HTTP 狀態的失敗 —— router 直接轉成契約 §1 的錯誤形狀。

    **不准靜默回空**：抓不到就是抓不到，回 0 張會讓前端顯示一個空的成功。
    """

    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.status = code, detail, status


# ---------- key ----------

def api_key(env_path: Path = ENV_PATH) -> str:
    """讀 server 端 `.env` 的 `ROBOFLOW_API_KEY`。沒有 / 格式不對一律丟 `RoboflowError`。

    `load_dotenv` **不覆寫**已存在的環境變數（`override=False` 是預設），所以
    `ROBOFLOW_API_KEY=... uv run ...` 這種臨時覆蓋照樣有效，`.env` 只是預設值。
    格式先在本機擋一次：`your-key-here` 這種佔位字串不該浪費一次 401 才知道，
    而且 401 的訊息會讓人去查網路（真因是 .env 還沒填）。
    """
    import os

    load_dotenv(env_path)
    key = (os.environ.get(ENV_KEY) or "").strip()
    if not key:
        where = f"{env_path} 不存在" if not env_path.exists() else f"{env_path} 裡的 {ENV_KEY} 是空的"
        raise RoboflowError("ROBOFLOW_KEY_MISSING", f"找不到 {ENV_KEY}（{where}）。{KEY_HOWTO}")
    if not KEY_RE.fullmatch(key):
        raise RoboflowError(
            "ROBOFLOW_KEY_INVALID",
            f"{ENV_KEY} 的格式不像 Roboflow key（10–64 碼英數，收到 {len(key)} 碼且含非英數字元）"
            "—— 本機格式檢查就擋下來了，**沒有打網路**，所以這不是網路或權限問題。" + KEY_HOWTO,
        )
    return key


# ---------- HTTP（stdlib） ----------

def _get(url: str, timeout: int) -> bytes:
    """GET → bytes。把三種失敗分開講清楚：權限 / 找不到 / 連不上。

    ⛔ **訊息裡永遠不准出現完整 url**（query 帶著真的 key）：只放 `url.split("?")[0]`，
    其餘一律過 `_scrub()`。
    """
    safe = url.split("?")[0]
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # 固定 https 常量，不吃使用者 URL
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = _scrub(exc.read()[:400].decode("utf-8", "replace"))
        if exc.code in (401, 403):
            raise RoboflowError(
                "ROBOFLOW_KEY_INVALID",
                f"api.roboflow.com 回 {exc.code}：這把 key 不存在、已被撤銷，或沒有這個 workspace 的權限"
                f"（**連得上，所以不是網路問題**）。原文：{body}。" + KEY_HOWTO,
            ) from exc
        if exc.code == 404:
            raise RoboflowError(
                "ROBOFLOW_NOT_FOUND",
                f"Roboflow 說找不到（404）：{safe} —— 檢查 workspace / project / version "
                f"三個值。原文：{body}",
                status=404,
            ) from exc
        raise RoboflowError(
            "ROBOFLOW_DOWNLOAD_FAILED",
            f"api.roboflow.com 回 HTTP {exc.code}（不是 key 的問題，key 已通過）：{body}",
            status=409,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        # `http.client.HTTPException` **不是 OSError 的子類**（`IncompleteRead` /
        # `InvalidURL` / `BadStatusLine` 都掛在它底下）。漏掉它的實際後果有兩個：
        # zip 下載到一半斷線 → 逃出去變成 HTTP 500 空訊息（使用者看不到「改用 source=demo」）；
        # workspace 帶空白 → `InvalidURL` 的訊息帶著整串含 key 的 url 進 server log。
        raise RoboflowError(
            "ROBOFLOW_UNREACHABLE",
            f"連不上 {safe}（{type(exc).__name__}: {_scrub(exc)}）—— 這是**網路或防火牆**問題，"
            "key 還沒被驗到。教室沒網路就改用 source=\"demo\"（合成 120 張，整條線一樣完整）",
            status=409,
        ) from exc


def _get_json(url: str) -> dict[str, Any]:
    payload = _get(url, API_TIMEOUT_S)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RoboflowError(
            "ROBOFLOW_DOWNLOAD_FAILED",
            f"Roboflow 回的不是 JSON（前 200 字）：{_scrub(repr(payload[:200]))}", status=409
        ) from exc


def resolve_version(workspace: str, project: str, key: str, want: int) -> tuple[int, list[int]]:
    """回 `(要下載的 version, 可用的 version 清單)`。

    抄 `download_dataset.py`：**指定版本不存在就退回 `max(version_ids)`**，而不是硬失敗 ——
    Roboflow 的版本會被作者重新生成，v3 哪天消失時課堂上要的是「退到最新版繼續跑」，
    不是一個 404。退版這件事要**看得見**（router 會把它寫進 `ds.total` 的 text）。
    """
    info = _get_json(f"{API}/{_q(workspace)}/{_q(project)}?api_key={key}")
    ids: list[int] = []
    for v in info.get("versions", []):
        raw = v.get("id") or v.get("version") or ""
        tail = str(raw).split("/")[-1]
        if tail.isdigit():
            ids.append(int(tail))
    if not ids:
        raise RoboflowError(
            "ROBOFLOW_NOT_FOUND",
            f"{workspace}/{project} 一個 version 都沒有（Roboflow 專案要先 Generate 出版本才能下載）",
            status=404,
        )
    return (want if want in ids else max(ids)), sorted(ids)


def _export_link(workspace: str, project: str, version: int, fmt: str, key: str) -> str:
    """拿 zip 的簽名連結。

    實測回應（`GET /wm811k-paasr/wm811k/3/yolov8?api_key=…`）：
    `{"workspace":…, "project":…, "version":…, "export":{"format":"yolov8",
      "link":"https://app.roboflow.com/ds/XXXX?key=YYYY","size":8.77}, "progress":1}`
    """
    d = _get_json(f"{API}/{_q(workspace)}/{_q(project)}/{_q(version)}/{_q(fmt)}?api_key={key}")
    link = (d.get("export") or {}).get("link")
    if not link:
        raise RoboflowError(
            "ROBOFLOW_DOWNLOAD_FAILED",
            f"{workspace}/{project} v{version} 的 {fmt} export 還沒生成好"
            f"（progress={d.get('progress')}）—— Roboflow 是非同步產生 export，等一下再打一次",
            status=409,
        )
    return link


def slug(workspace: str, project: str, version: int) -> str:
    return f"{workspace}__{project}__v{version}"


def dataset_dir(workspace: str, project: str, version: int) -> Path:
    return RAW_DIR / slug(workspace, project, version)


def download(workspace: str, project: str, version: int, fmt: str, key: str, out_dir: Path) -> None:
    """下載 + 解壓到 `out_dir`（不做索引）。"""
    link = _export_link(workspace, project, version, fmt, key)
    payload = _get(link, ZIP_TIMEOUT_S)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ".download.zip"
    tmp.write_bytes(payload)
    try:
        with zipfile.ZipFile(tmp) as zf:
            # extractall 會把絕對路徑與 `..` 成分剝掉（CPython 的 `ZipFile._extract_member`），
            # 所以第三方 zip 不會寫到 out_dir 外面 —— 但這是信任邊界，不要改成手動 join。
            zf.extractall(out_dir)
    except zipfile.BadZipFile as exc:
        raise RoboflowError(
            "ROBOFLOW_DOWNLOAD_FAILED",
            f"下載回來的不是合法 zip（{len(payload)} bytes）：{exc}。整批砍掉重抓："
            f"rm -rf {out_dir}",
            status=409,
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)


# ---------- 索引（id → 檔案 + 人工框） ----------

def _names_from_data_yaml(path: Path) -> list[str]:
    """只讀 `names:` 那一行 —— 不引 pyyaml（不是 base 相依），也不想為一行 list 引。

    yolov8 export 的 `data.yaml` 實測長這樣：`names: ['Donut']`。
    """
    if not path.exists():
        return []
    for line in path.read_text("utf-8").splitlines():
        if not line.startswith("names:"):
            continue
        raw = line.split(":", 1)[1].strip()
        quoted = [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", raw)]
        return quoted or [s.strip() for s in raw.strip("[]").split(",") if s.strip()]
    return []


def _image_id(slug_: str, rel: str) -> str:
    """`rf` + sha1(slug/相對路徑) 前 12 碼。

    **不用序號**（`rf0001`）：序號在「同一台機器抓了第二個 Roboflow 資料集」時會撞，
    而 `GET /api/v1/images/{id}` 只有 id、沒有 ds，撞了就回錯的圖。雜湊帶 slug 進去，
    同一個 zip 重抓 → 同一組 id（重跑不換考卷），不同資料集 → 永遠不同 id。
    """
    return ID_PREFIX + hashlib.sha1(f"{slug_}/{rel}".encode()).hexdigest()[:12]


def _parse_label(path: Path) -> list[dict[str, Any]]:
    """YOLO `.txt` → `[{cls,cx,cy,w,h}]`（人工框，正規化座標）。

    格式驗證沿用既有 `bbox-labeler` 的那一條：`cls_id` 非負整數、`cx/cy/w/h` 在 [0,1]。
    壞行**不靜默丟掉** —— 丟掉就變成「這張沒有人工框」，而 GT 少一框會讓 IoU 天花板
    無聲降低。直接 raise，讓錯誤停在資料層。
    """
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for lineno, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 5:
            raise RoboflowError(
                "ROBOFLOW_DOWNLOAD_FAILED",
                f"{path.name}:{lineno} 不是 5 欄的 YOLO 行（`cls cx cy w h`）：{line!r}", status=409,
            )
        cls = int(parts[0])
        cx, cy, w, h = (float(x) for x in parts[1:])
        if cls < 0 or not all(0.0 <= v <= 1.0 for v in (cx, cy, w, h)):
            raise RoboflowError(
                "ROBOFLOW_DOWNLOAD_FAILED",
                f"{path.name}:{lineno} 的值超出 YOLO 範圍（cls≥0、cx/cy/w/h∈[0,1]）：{line!r}", status=409,
            )
        out.append({"cls": cls, "cx": round(cx, 6), "cy": round(cy, 6),
                    "w": round(w, 6), "h": round(h, 6)})
    return out


def build_index(out_dir: Path, workspace: str, project: str, version: int) -> dict[str, Any]:
    """掃解壓後的目錄 → `index.json`（這個 ds 的唯一真相）。

    順序是**檔名排序**，不是 `iterdir()` 的 inode 順序：`freeze.gt_partition()` 照 id 順序
    切 anchor / sealed-test，順序不穩就等於每次重跑偷偷換一批考卷。
    """
    slug_ = slug(workspace, project, version)
    names = _names_from_data_yaml(out_dir / "data.yaml")
    images: list[dict[str, Any]] = []
    for split_dir in sorted(p for p in out_dir.iterdir() if (p / "images").is_dir()):
        for img in sorted((split_dir / "images").iterdir()):
            if img.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):
                continue
            rel = img.relative_to(out_dir).as_posix()
            boxes = _parse_label(split_dir / "labels" / f"{img.stem}.txt")
            images.append({
                "id": _image_id(slug_, rel),
                # Roboflow 的檔名是 `105569_png.rf.<32 碼雜湊>.jpg`，整串塞進縮圖牆會爆版；
                # 留原始那一段（`105569.jpg`），完整路徑在 `file`。
                "name": f"{img.stem.split('_png.rf.')[0].split('.rf.')[0]}{img.suffix}",
                "file": rel,
                "rf_split": split_dir.name,   # Roboflow 自己的分割；我們的 split 由 s04 指派
                "boxes": boxes,
            })
    if not images:
        raise RoboflowError(
            "ROBOFLOW_DOWNLOAD_FAILED",
            f"{out_dir} 解壓後找不到任何 `<split>/images/` 影像 —— "
            f"format 不是 {FORMAT} 的話目錄佈局會不一樣。整批砍掉重抓：rm -rf {out_dir}",
            status=409,
        )
    if not names:
        raise RoboflowError(
            "ROBOFLOW_DOWNLOAD_FAILED",
            f"{out_dir}/data.yaml 讀不到 `names:` —— 類別名是人工標註的一半，沒有它不准往下走",
            status=409,
        )
    index = {
        "slug": slug_, "workspace": workspace, "project": project, "version": version,
        "format": FORMAT, "names": names, "nc": len(names), "total": len(images),
        "labels": "human",
        "source_url": f"https://universe.roboflow.com/{workspace}/{project}/dataset/{version}",
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "box_total": sum(len(r["boxes"]) for r in images),
        "images": images,
    }
    _write_index(out_dir, index)
    return index


def _write_index(out_dir: Path, index: dict[str, Any]) -> None:
    """原子寫（tmp + replace），比照 `dataset.py::_write_json` 與 `registry.write_state`。

    裸 `write_text` 有兩個中斷窗口（`build_index` 一次、`ensure` 補 `version_fallback` 又一次）；
    中斷在那裡就會留下半份 index.json，而它會**永久**擋住每一次重試（見 `_read_index`）。
    """
    tmp = out_dir / "index.json.tmp"
    tmp.write_text(json.dumps(index, ensure_ascii=False), "utf-8")
    tmp.replace(out_dir / "index.json")


def _read_index(path: Path) -> dict[str, Any] | None:
    """讀 index.json；**壞掉就當沒下載過**（回 None），不要讓例外逃出去。

    下載到一半被 Ctrl-C → index.json 只寫了半份 → `json.loads` 直接炸。那個例外在
    `ensure()` 走到 `api_key()` **之前**就發生，所以磁碟上那份壞檔會擋住每一次重試，
    而且錯誤是純文字 500（不是契約 §1 的 `{code, detail}`），也沒人告訴使用者要 rm -rf。
    回 None 就自動接回既有的自癒路徑：`ensure()` 看到沒索引 → 重抓。
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def load_index(out_dir: Path) -> dict[str, Any] | None:
    index = _read_index(out_dir / "index.json")
    return index if index and index.get("images") else None


def ensure(
    workspace: str = WORKSPACE, project: str = PROJECT, version: int = VERSION,
) -> dict[str, Any]:
    """要有這份資料集才回 index；**已下載過就沿用不重抓**（教室重跑不該每次下載 8.8 MB）。

    沿用的判準是 `index.json` 在且有圖 —— 索引是下載成功的最後一步，在就代表 zip 解完、
    每一行人工框都驗過。要重抓就 `rm -rf 01-raw-data/roboflow/<slug>/`。
    注意：沿用這條路**不讀 key**，所以抓過一次之後沒 key 也能重跑整條線。
    """
    out_dir = dataset_dir(workspace, project, version)
    if (index := load_index(out_dir)) is not None:
        return index
    key = api_key()
    target, available = resolve_version(workspace, project, key, version)
    out_dir = dataset_dir(workspace, project, target)
    if (index := load_index(out_dir)) is not None:
        return index
    download(workspace, project, target, FORMAT, key, out_dir)
    index = build_index(out_dir, workspace, project, target)
    index["version_fallback"] = None if target == version else {"want": version, "used": target,
                                                                "available": available}
    _write_index(out_dir, index)
    return index


# ---------- id → 檔案（`GET /api/v1/images/{id}` 的解析規則） ----------

_ID_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def _id_map(out_dir: Path) -> dict[str, str]:
    """`index.json` 的 id → 相對路徑，用 mtime 當快取鍵（409 張每次重讀 JSON 是 3 ms × 每張圖）。"""
    path = out_dir / "index.json"
    mtime = path.stat().st_mtime
    hit = _ID_CACHE.get(str(out_dir))
    if hit is None or hit[0] != mtime:
        index = _read_index(path)   # 半份 index.json 一樣不准炸：縮圖 proxy 會整面掛掉
        _ID_CACHE[str(out_dir)] = (mtime, {r["id"]: r["file"] for r in (index or {}).get("images", [])})
    return _ID_CACHE[str(out_dir)][1]


def id_of_filename(name: str) -> str | None:
    """**反方向**：影像檔名 → `rf<12 碼>` id。

    為什麼需要它：`02-dataset/` 落檔後只剩 basename，而 eval/推論那側是從那裡讀回來的。
    少了這支，推論圖牆會拿 Roboflow 的原始檔名（`279288_png.rf.95ce….jpg`）去打
    `/api/v1/images/{id}`，而 proxy 只認 sha1 id —— 實測 58 張 valid 有 48 張 404、
    畫面上是破圖。合成資料時兩邊剛好都是 `demo0001` 才沒暴露。

    basename 在一個資料集內唯一（Roboflow export 的 `.rf.<hash>` 後綴保證），
    跨資料集則靠 id 自己帶 slug 雜湊區分，所以這裡回第一個命中的即可。
    """
    if not RAW_DIR.exists():
        return None
    # ⚠️ 比 stem 不比完整檔名：同一張圖的影像是 `.jpg`、標註是 `.txt`，
    # 而 eval 兩邊都要推出同一個 id（predictions 讀 .jpg、load_gt 讀 .txt）。
    # 拿完整檔名比會讓 .txt 一律查不到 → 退回 stem → GT 與預測 key 不同 →
    # 實測 mAP 掉成 0.0、bootstrap 1000 次全被丟掉。
    stem = Path(name).stem
    for d in sorted(RAW_DIR.iterdir()):
        if not (d / "index.json").exists():
            continue
        for image_id, rel in _id_map(d).items():
            if Path(rel).stem == stem:
                return image_id
    return None


def image_path(image_id: str) -> Path | None:
    """`rf<12 碼>` → 實際檔案。掃 `01-raw-data/roboflow/*/index.json`（id 帶 slug 雜湊，不會撞）。"""
    if not image_id.startswith(ID_PREFIX) or not RAW_DIR.exists():
        return None
    for d in sorted(RAW_DIR.iterdir()):
        if not (d / "index.json").exists():
            continue
        rel = _id_map(d).get(image_id)
        if rel is not None:
            path = d / rel
            return path if path.exists() else None
    return None


def path_of(image_id: str) -> Path | None:
    """**全系統唯一的 id → 影像檔解析**（demo 走契約 §11 規則、roboflow 走 index.json）。

    放在這裡而不是各自 import：`dataset.py` 的圖片 proxy、s02 描述子、s03 標註、
    `freeze.py` 的落檔一共五個呼叫點，少改一處就是「有些圖找不到而且沒有錯誤訊息」。
    """
    from . import demo  # 延後 import：demo 不認識 roboflow，方向固定成單向

    return demo.image_path(image_id) or image_path(image_id)


def boxes_of(index: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """index → `{image_id: [人工框]}`（框已含 `cls`，是真實類別 id）。"""
    return {r["id"]: r["boxes"] for r in index["images"]}


def counts_of(boxes: list[dict[str, Any]], names: list[str]) -> dict[str, int]:
    """人工框 → `{類別名: 框數}`（`ds.image.counts` 與 §8.7 的 `annotations.classes`）。"""
    out: dict[str, int] = {}
    for b in boxes:
        name = names[b["cls"]] if 0 <= b["cls"] < len(names) else f"cls{b['cls']}"
        out[name] = out.get(name, 0) + 1
    return out


# ---------- 會失敗的檢查 ----------

def _selfcheck() -> None:  # pragma: no cover - 手動跑的自檢
    """`uv run python -m src.autolabel.roboflow_src`

    沒 key → 明確失敗並印出正確指引；key 格式錯 → 本機就擋（不打網路）；
    key 格式對但是假的 → 打得到網路就回權限錯、打不到就回網路錯。三種訊息**不准混**。
    """
    import os

    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
        if not ok:
            fails.append(name)

    print("1) 沒有 key 的錯誤路徑（.env 指到不存在的檔案 + 清空環境變數）")
    saved = os.environ.pop(ENV_KEY, None)
    try:
        api_key(PROJECT_ROOT / ".env.does-not-exist")
        check("沒 key 要丟 RoboflowError", False, "居然沒丟")
    except RoboflowError as exc:
        check("code == ROBOFLOW_KEY_MISSING", exc.code == "ROBOFLOW_KEY_MISSING", exc.code)
        check("status 是 4xx", 400 <= exc.status < 500, str(exc.status))
        check("指引有講去哪拿 key", "app.roboflow.com" in exc.detail)
        check("指引有講放哪個檔", ".env" in exc.detail and ENV_KEY in exc.detail)
        print(f"      → {exc.detail[:120]}…")

    print("2) key 格式錯 vs 網路/權限問題要分得開")
    os.environ[ENV_KEY] = "your-key-here"
    try:
        api_key(PROJECT_ROOT / ".env.does-not-exist")
        check("格式錯要丟 RoboflowError", False, "居然沒丟")
    except RoboflowError as exc:
        check("code == ROBOFLOW_KEY_INVALID", exc.code == "ROBOFLOW_KEY_INVALID", exc.code)
        check("訊息明講沒打網路", "沒有打網路" in exc.detail)
    os.environ[ENV_KEY] = "ffffffffffffffffffff"  # 格式合法、但不存在的 key
    try:
        resolve_version(WORKSPACE, PROJECT, api_key(PROJECT_ROOT / ".env.does-not-exist"), VERSION)
        check("假 key 不該拿到版本清單", False, "居然成功了")
    except RoboflowError as exc:
        check(
            "假 key → 權限(KEY_INVALID) 或 沒網路(UNREACHABLE)，不是 KEY_MISSING",
            exc.code in ("ROBOFLOW_KEY_INVALID", "ROBOFLOW_UNREACHABLE"), exc.code,
        )
        print(f"      → {exc.detail[:120]}…")
    if saved is not None:
        os.environ[ENV_KEY] = saved
    else:
        os.environ.pop(ENV_KEY, None)

    print("3) 已下載過就沿用不重抓（沒抓過就跳過，不在這裡打網路）")
    out_dir = dataset_dir(WORKSPACE, PROJECT, VERSION)
    index = load_index(out_dir)
    if index is None:
        print(f"  [SKIP] {out_dir.name} 還沒下載過 —— 先跑 "
              f"`POST /api/v1/datasets/ingest {{\"source\":\"roboflow\"}}`")
    else:
        check("index 有 names", bool(index["names"]), str(index["names"]))
        check("total 與 images 對得上", index["total"] == len(index["images"]), str(index["total"]))
        ids = [r["id"] for r in index["images"]]
        check("id 不重複", len(set(ids)) == len(ids), f"{len(set(ids))}/{len(ids)}")
        check("id 帶 slug 雜湊（換資料集不會撞）",
              ids[0] == _image_id(index["slug"], index["images"][0]["file"]))
        check("每張圖都解析得到檔案", all(path_of(i) is not None for i in ids[:20]))
        check("人工框數 > 0", index["box_total"] > 0, f"{index['box_total']} 框")
        nb = sum(1 for r in index["images"] if not r["boxes"])
        print(f"      → {index['total']} 張 / {index['box_total']} 人工框 / 無框 {nb} 張 / "
              f"nc={index['nc']} {index['names']}")

    print("4) 壞掉的標註行不准靜默丟掉（少一個 GT 框 = IoU 天花板無聲降低）")
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="rf-selfcheck-")) / "x.txt"
    for text, why in (("0 0.5 0.5 0.2\n", "只有 4 欄"), ("0 0.5 0.5 1.4 0.2\n", "w 超出 [0,1]")):
        tmp.write_text(text, "utf-8")
        try:
            rows = _parse_label(tmp)
            check(f"{why} 要 raise", False, f"居然回了 {rows}")
        except RoboflowError as exc:
            check(f"{why} → {exc.code}", exc.code == "ROBOFLOW_DOWNLOAD_FAILED", exc.code)

    print("5) 例外不准逃出 _get()，訊息不准帶 key")
    fake = "K" * 20
    # (a) workspace 帶空白 → http.client.InvalidURL（不是 OSError）。訊息本來帶整串含 key 的 url。
    for why, url in (
        ("空白 → InvalidURL", f"{API}/a b/p?api_key={fake}"),
        ("中文 → 先被 quote 掉", f"{API}/{_q('中文')}/p?api_key={fake}"),
    ):
        try:
            _get(url, 5)
            check(f"{why} 要丟 RoboflowError", False, "居然沒丟")
        except RoboflowError as exc:
            check(f"{why} → RoboflowError", True, exc.code)
            check(f"{why} 的訊息不含 key", fake not in exc.detail and fake not in str(exc))
        except Exception as exc:  # noqa: BLE001 —— 逃出去的就是這一條要抓的 bug
            check(f"{why} 逃出 _get()（{type(exc).__name__}）", False, _scrub(exc))
    check("_q() 會把空白與中文編碼掉", _q("a b") == "a%20b" and _q("中文") == "%E4%B8%AD%E6%96%87")
    check("_scrub() 抹得掉兩種 query", "api_key=***" in _scrub(f"x?api_key={fake}")
          and "key=***" in _scrub(f"https://app.roboflow.com/ds/ZZ?key={fake}"))

    # (b) 下載到一半斷線：宣告 Content-Length 100000 只送 500 bytes（http.client.IncompleteRead）
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def _half() -> None:
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n" + b"x" * 500)
        conn.close()

    threading.Thread(target=_half, daemon=True).start()
    try:
        _get(f"http://127.0.0.1:{srv.getsockname()[1]}/ds/ZZ?key={fake}", 5)
        check("zip 斷線要丟 RoboflowError", False, "居然沒丟")
    except RoboflowError as exc:
        check("zip 下載斷線 → ROBOFLOW_UNREACHABLE", exc.code == "ROBOFLOW_UNREACHABLE", exc.code)
        check("斷線訊息有講改用 demo", 'source="demo"' in exc.detail)
        check("斷線訊息不含 key", fake not in exc.detail)
    except Exception as exc:  # noqa: BLE001
        check(f"zip 斷線逃出 _get()（{type(exc).__name__}）", False, _scrub(exc))
    finally:
        srv.close()

    print("6) 半份 index.json 要當作沒下載過（壞檔不准永久擋住重抓）")
    broken = Path(tempfile.mkdtemp(prefix="rf-broken-"))
    (broken / "index.json").write_text('{"images": [{"id": "rfaaa', "utf-8")
    try:
        check("load_index 回 None 而不是丟 JSONDecodeError", load_index(broken) is None)
    except Exception as exc:  # noqa: BLE001
        check(f"load_index 炸了（{type(exc).__name__}）", False, str(exc)[:80])
    try:
        check("_id_map 也不准炸（縮圖 proxy 走這條）", _id_map(broken) == {})
    except Exception as exc:  # noqa: BLE001
        check(f"_id_map 炸了（{type(exc).__name__}）", False, str(exc)[:80])

    print(f"\n{'FAIL' if fails else 'PASS'}  失敗 {len(fails)} 條" + (f"：{fails}" if fails else ""))
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    _selfcheck()
