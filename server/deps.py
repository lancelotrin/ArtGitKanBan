"""所有看板共用的依赖：认人、管理员判断、跨站提交拦截、取客户端地址。"""
import os
import sys
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import auth  # noqa: E402  根目录的 auth.py，和 console.py 共用同一套登录和会话

TRUSTED_PROXIES = {"127.0.0.1", "::1"}


def current_user(request: Request) -> dict:
    """按 cookie 认人。console.py 在前面已经拦过未登录的请求，这里再认一次，不信转发头。"""
    user = auth.session_user(request.cookies.get(auth.COOKIE, ""))
    if not user:
        raise HTTPException(status_code=401, detail={"message": "请先登录。", "login": "/login"})
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"message": "只有管理员能做这个操作。"})
    return user


def same_origin(request: Request) -> None:
    """改数据的接口只接受同站提交：带了 Origin 就必须和 Host 一致。"""
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail={"message": "不接受跨站提交。"})


def client_address(request: Request) -> str:
    """真实客户端地址。只有请求来自本机的 console.py 转发时才信 X-Forwarded-For。"""
    peer: Optional[str] = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if peer in TRUSTED_PROXIES and forwarded:
        return forwarded.split(",")[0].strip()
    return peer or ""
