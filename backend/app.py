"""FastAPI 入口：/api/v1/* 是 JSON 接口；页面路径都返回 React 构建出的 index.html。

console.py 仍是对外的门（80，飞书登录回调和退出在那里），它按下面的路由表把请求转发到这里（只听 127.0.0.1）。
单独调试：.venv/bin/python -m backend
"""
import os
import sys
import threading
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.deps import ROOT_DIR, auth

if "console" not in sys.modules and "__main__" in sys.modules and \
        getattr(sys.modules["__main__"], "__file__", "").endswith("console.py"):
    sys.modules["console"] = sys.modules["__main__"]  # 被 console.py 带起来时共用同一份状态

from backend.routers import admin, bomb, chat, core, help, merge, review, wish  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("BOARD_API_PORT", "8766"))
DIST_DIR = os.path.join(ROOT_DIR, "web", "dist")
INDEX_HTML = os.path.join(DIST_DIR, "index.html")

# 路由规则（console.board_route 解析）：以 = 开头是整条路径；以 / 结尾是前缀；其它是前缀且含自身。
SPA_ROUTES = ("=/", "=/index.html", "=/login", "/wish", "/bomb", "/review", "/merge", "/chat", "/help")
PUBLIC_PAGES = ("=/login",)
SPA_PREFIXES = tuple(rule for rule in SPA_ROUTES if not rule.startswith("="))
PROXY_ROUTES = SPA_ROUTES + ("/api/v1", "/static")
# 单个合并会话的评审页是 MergeReviewPortable 自带的页面，继续由 console.py 里的 merge_web 处理。
PROXY_EXCLUDE = ("/merge/s/",)
# 不用登录就能访问的：登录页、前端静态文件、登录页要读的接口。
PUBLIC_ROUTES = PUBLIC_PAGES + ("/static", "/api/v1/auth")

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


for _router in (core.router, wish.router, bomb.router, review.router, merge.router, chat.router, help.router, admin.router):
    app.include_router(_router, prefix="/api/v1")


@app.get("/api/v1/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True, "pages": list(SPA_ROUTES), "web_built": os.path.exists(INDEX_HTML)}


if os.path.isdir(DIST_DIR):
    app.mount("/static", StaticFiles(directory=DIST_DIR), name="static")


def spa_page(request: Request):
    path = request.url.path
    public = any(path == rule[1:] for rule in PUBLIC_PAGES)
    if not public and not auth.session_user(request.cookies.get(auth.COOKIE, "")):
        target = path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse("/login?" + urlencode({"next": target}), status_code=302)
    if not os.path.exists(INDEX_HTML):
        return JSONResponse({"message": "前端还没构建：在 web/ 下执行 npm run build。"}, status_code=503)
    return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


for _rule in SPA_ROUTES:
    if _rule.startswith("="):
        app.add_api_route(_rule[1:], spa_page, methods=["GET"], include_in_schema=False)
    else:
        app.add_api_route(_rule, spa_page, methods=["GET"], include_in_schema=False)
        app.add_api_route(_rule + "/{rest:path}", spa_page, methods=["GET"], include_in_schema=False)


def start_in_thread() -> None:
    """console.py 调用：在后台线程里起 uvicorn，只听本机。"""
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False,
                                           proxy_headers=False))
    threading.Thread(target=server.run, name="board-api", daemon=True).start()
