"""门户和登录页用的接口：我是谁、有哪些子站、登录页信息（这个不用登录）。"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends

import console
from backend.deps import auth, current_user

router = APIRouter(tags=["门户"])


@router.get("/me", operation_id="who_am_i", summary="当前登录的人")
def me(user: dict = Depends(current_user)) -> dict:
    return auth.public_user(user)


@router.get("/sites", operation_id="list_sites", summary="大豆开的内网子站")
def sites(_user: dict = Depends(current_user)) -> dict:
    return console.sites_view()


@router.get("/auth/login-info", operation_id="login_info", summary="登录页要显示的东西（不用登录）")
def login_info(next: str = "", error: str = "") -> dict:
    return {"href": "/auth/feishu?" + urlencode({"next": auth.safe_next(next)}),
            "configured": bool(auth.feishu_config()["app_id"]), "error": error[:200]}
