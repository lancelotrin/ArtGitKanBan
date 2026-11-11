"""meta 检查看板 API。逻辑在 bot/meta_fix.py，日志和锁在 console.meta_action。

检查（只读 git）和全部处理（在部署机工程上 commit，不 push）登录了都能用，日志记下是谁点的。
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.deps import current_user, same_origin
from console import server as console

router = APIRouter(tags=["meta 检查"])


def unwrap(result: tuple) -> dict:
    status, payload = result
    if status >= 400:
        raise HTTPException(status_code=status, detail={"message": payload.get("error") or "没成功。",
                                                        "log": payload.get("log", "")})
    return payload


@router.get("/meta", operation_id="scan_meta",
            summary="检查部署机工程里本地改过的 .meta：只改导入设置的、guid 和别的 meta 重复的（含重复提交和要换 / 不动的引用）")
def scan_meta(_user: dict = Depends(current_user)) -> dict:
    return unwrap(console.meta_action("scan"))


@router.get("/meta/log", operation_id="meta_log", summary="meta 检查和处理日志（最近 200 行）")
def meta_log(_user: dict = Depends(current_user)) -> dict:
    return {"log": console.meta_log_text()}


@router.post("/meta/apply", operation_id="apply_meta", dependencies=[Depends(same_origin)],
             summary="全部处理并提交（不推送，跳过 pre-commit）。导入设置直接提交；guid 重复的把该换的引用换成本地新 guid")
def apply_meta(user: dict = Depends(current_user)) -> dict:
    return unwrap(console.meta_action("apply_all", who=str(user.get("name") or user.get("open_id") or "")))
