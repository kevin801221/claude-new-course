"""單一 FastAPI app —— 五位專家的五個 router 掛在同一個 process 裡。

啟動：`uv run uvicorn src.app.main:app --reload --reload-dir src`
`--reload-dir src` 不是可選的：`runs/` 在專案樹內且被逐筆寫入，預設 `--reload` 會每寫一筆
事件就重啟一次並殺掉 driver（契約 §1、DESIGN 護欄 6）。

擁有者：console-owner（共同地基）。掛 router 的那一行也只有它能改 —— 五個人搶著改同一個
include 就是整合當天的第一個 conflict（team-roles §1）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import bus, registry
from .routers import console, dataset, eval as eval_router, round as round_router, train

API_PREFIX = "/api/v1"

# 契約 §1：只 bind 127.0.0.1，且 Host header 非本機一律 403。
# 威脅模型是「不小心 --host 0.0.0.0 把 key 和 GPU 一起送出去」，最便宜的解法是不給這個選項。
# 白名單只放**真的是本機**的四個字串。曾經多放的 `0.0.0.0` 與 `testserver` 已移除：
# 前者正是威脅模型本身（`--host 0.0.0.0` 之後，區網任何人多帶一個 Host header 就進得來），
# 後者是 Starlette TestClient 的預設值 —— 為了測試方便在生產邊界開常設的洞，
# 測試要用 TestClient 就給它 `base_url="http://127.0.0.1"`。
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 重啟認領：pid 死掉的 running run 標 crashed 並推 stage.failed，
    # 否則前端會把「子行程死了」顯示成「進度條卡住」（DESIGN 護欄 6）。
    crashed = registry.claim_or_crash()
    if crashed:
        print(f"[registry] 重啟認領：{', '.join(crashed)} 標記為 crashed")
    yield


app = FastAPI(
    title="CV 自我進化主控台",
    version="1",  # = 事件信封與 API 契約的 v
    lifespan=lifespan,
)


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.url.path.startswith(API_PREFIX):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "NON_LOCAL_HOST",
                    "detail": "只接受來自 localhost 的請求；本服務不可部署到公開網址。",
                },
            )
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """統一成契約 §1 的錯誤形狀 `{code, detail}`（前端只有一套錯誤渲染器）。"""
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": f"HTTP_{exc.status_code}", "detail": str(detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """契約 §10：pydantic 的 422 一律轉 400，不准漏出 FastAPI 預設形狀。"""
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ())[1:]) or "body"
    return JSONResponse(
        status_code=400,
        content={
            "code": "VALIDATION_ERROR",
            "detail": f"body 驗證失敗：{where} — {first.get('msg', '格式不合契約')}",
        },
    )


@app.exception_handler(NotImplementedError)
async def not_implemented(request: Request, exc: NotImplementedError):
    """尚未接上的內部實作（例如 M1 的 dataset-truth seam）一律回 501，不回 404、不回假資料。"""
    return JSONResponse(
        status_code=501,
        content={"code": "NOT_IMPLEMENTED", "detail": str(exc) or "此端點尚未實作"},
    )


# 五個 router 掛進同一個 app（DESIGN「刻意不做的事」第 10 條：不做 per-expert 微服務）。
app.include_router(console.router, prefix=API_PREFIX)  # console-owner
app.include_router(dataset.router, prefix=API_PREFIX)  # dataset-truth
app.include_router(train.router, prefix=API_PREFIX)  # training-engineer
app.include_router(eval_router.router, prefix=API_PREFIX)  # metric-auditor
app.include_router(round_router.router, prefix=API_PREFIX)  # experiment-arbiter


@app.get("/healthz")
def healthz() -> dict[str, object]:
    """給 selfcheck 與 run_all 確認 server 起來了（不在 /api/v1 底下，不受 Host 檢查）。"""
    return {"ok": True, "contract_v": 1, "milestone": "M1"}


# 前端：單檔零依賴，由同一個 process 從 `/` 送出。
# 一定要同源 —— 前端要打 `/api/v1/*` 與 `<img src="/api/v1/images/{id}">`，
# 跨源就得開 CORS（等於把這個無人看管的 server 對外開一扇門，違反護欄 10）。
# 掛在檔案最後：Starlette 依註冊順序比對，/api/v1 與 /healthz 都已經先註冊，不會被這個 mount 蓋掉。
app.mount(
    "/",
    StaticFiles(directory=bus.PROJECT_ROOT / "prototype", html=True),
    name="ui",
)
