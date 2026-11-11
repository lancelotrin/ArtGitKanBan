"""meta 检查看板 API。任务、日志、推送记录在 boards/meta_board.py，git 处理在 bot/meta_fix.py。

登录了都能用；任务在后台跑，同一时间只跑一个，网页轮询 /meta 看进度。日志和推送记录记下是谁点的。
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.deps import current_user, same_origin
from boards import meta_board

router = APIRouter(tags=["meta 检查"])


def who(user: dict) -> str:
    return str(user.get("name") or user.get("open_id") or "")


def start(action: str, user: dict) -> dict:
    try:
        return meta_board.start(action, who(user))
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"message": str(error)})


@router.get("/meta", operation_id="meta_view",
            summary="当前任务进度，和上次检查结果：只改导入设置的、guid 和别的 meta 重复的（含重复提交和要换 / 不动的引用）")
def meta_view(_user: dict = Depends(current_user)) -> dict:
    return meta_board.view()


@router.get("/meta/log", operation_id="meta_log", summary="meta 检查和处理日志（最近 200 行）")
def meta_log(_user: dict = Depends(current_user)) -> dict:
    return {"log": meta_board.log_text()}


@router.get("/meta/pushes", operation_id="meta_pushes",
            summary="meta 处理的推送记录：提交号、作者、说明、改了哪些文件、推到哪、成功没有（新的在前）")
def meta_pushes(_user: dict = Depends(current_user)) -> dict:
    return {"items": meta_board.pushes()}


@router.post("/meta/check", operation_id="check_meta", dependencies=[Depends(same_origin)],
             summary="后台开始：拉 .env 配的工程当前分支到最新（只快进）→ 让 Unity（MCP for Unity）刷新资源 → 检查 meta")
def check_meta(user: dict = Depends(current_user)) -> dict:
    return start("check", user)


@router.post("/meta/apply", operation_id="apply_meta", dependencies=[Depends(same_origin)],
             summary="后台开始：全部处理并提交（跳过 pre-commit），再推送当前分支（跳过 pre-push）")
def apply_meta(user: dict = Depends(current_user)) -> dict:
    return start("apply", user)


@router.post("/meta/push", operation_id="push_meta", dependencies=[Depends(same_origin)],
             summary="后台开始：再推一次当前分支（上次推送失败时用）")
def push_meta(user: dict = Depends(current_user)) -> dict:
    return start("push", user)
