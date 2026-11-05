"""FastAPI 入口：/api/v1/* 是 JSON 接口；已迁移看板的页面路径返回 React 构建出的 index.html。

console.py 仍是对外的门（8765，登录在那里），它把迁移过的前缀转发到这里（只听 127.0.0.1）。
单独调试：.venv/bin/python -m server
"""
import os
import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlencode

from server.deps import ROOT_DIR, auth, current_user
from server.routers import wish

HOST = "127.0.0.1"
PORT = int(os.environ.get("BOARD_API_PORT", "8766"))
DIST_DIR = os.path.join(ROOT_DIR, "web", "dist")
INDEX_HTML = os.path.join(DIST_DIR, "index.html")
# 已经迁到新前端的看板。console.py 按这个表决定哪些前缀转发过来。
SPA_PREFIXES = ("/wish",)
PROXY_PREFIXES = SPA_PREFIXES + ("/api/v1", "/static")

app = FastAPI(title="大豆看板 API", version="1", docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json",
              redoc_url=None)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
    """错误统一成 {"message": ..., 其他字段}，前端和以后的 agent 都只看 message。"""
    body = dict(error.detail) if isinstance(error.detail, dict) else {"message": str(error.detail)}
    return JSONResponse(body, status_code=error.status_code, headers=getattr(error, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    first = (error.errors() or [{}])[0]
    field = ".".join(str(part) for part in first.get("loc", ())[1:])
    return JSONResponse({"message": f"参数不对：{field} {first.get('msg', '')}".strip(), "errors": error.errors()},
                        status_code=422)


app.include_router(wish.router, prefix="/api/v1")


@app.get("/api/v1/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True, "boards": list(SPA_PREFIXES), "web_built": os.path.exists(INDEX_HTML)}


@app.get("/api/v1/me", operation_id="who_am_i", summary="当前登录的人")
def me(user: dict = Depends(current_user)) -> dict:
    return auth.public_user(user)


if os.path.isdir(DIST_DIR):
    app.mount("/static", StaticFiles(directory=DIST_DIR), name="static")


def spa_page(request: Request):
    if not auth.session_user(request.cookies.get(auth.COOKIE, "")):
        return RedirectResponse("/login?" + urlencode({"next": str(request.url.path)}), status_code=302)
    if not os.path.exists(INDEX_HTML):
        return JSONResponse({"message": "前端还没构建：在 web/ 下执行 npm run build。"}, status_code=503)
    return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


for _prefix in SPA_PREFIXES:
    app.add_api_route(_prefix, spa_page, methods=["GET"], include_in_schema=False)
    app.add_api_route(_prefix + "/{rest:path}", spa_page, methods=["GET"], include_in_schema=False)


def start_in_thread() -> None:
    """console.py 调用：在后台线程里起 uvicorn，只听本机。"""
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False,
                                           proxy_headers=False))
    threading.Thread(target=server.run, name="board-api", daemon=True).start()
