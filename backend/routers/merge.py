"""合并确认 API：会话列表、上传会话、本地工程（检查 / 采集 / 增删）。逻辑都在 merge_web.py。

单个会话的评审页 /merge/s/<会话>/ 仍是 MergeReviewPortable 自带的页面（它要能脱离大豆单独跑），不在这里。
本地工程、上传、删除、开放会话只有管理员能做；普通用户只看得到管理员开放的会话（merge_open.json），没开放就什么都看不到。
"""
import os
import shutil
import subprocess
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import merge_web
from backend.deps import admin_user, current_user, same_origin

merge_review = merge_web.merge_review  # merge_web 导入时才把 MergeReviewPortable 加进 sys.path

router = APIRouter(prefix="/merge", tags=["合并确认"])


class RepoBody(BaseModel):
    repo: str = Field(min_length=1, description="工程路径")


class OpenBody(BaseModel):
    open: bool = Field(description="是否开放给普通用户")


class CaptureBody(RepoBody):
    replay_base: str = Field("", description="（可选）squash/每日同步时的已同步基线提交号")


def failed(error: Exception) -> HTTPException:
    if isinstance(error, FileNotFoundError):
        return HTTPException(status_code=404, detail={"message": "没有这个会话。"})
    return HTTPException(status_code=400, detail={"message": str(error) or "没成功。"})


ERRORS = (ValueError, UnicodeError, KeyError, OSError, subprocess.SubprocessError, merge_review.ReviewError)


@router.get("/sessions", operation_id="list_merge_sessions", summary="所有合并确认会话和选择进度")
def list_sessions(user: dict = Depends(current_user)) -> dict:
    is_admin = user.get("role") == "admin"
    opened = set(merge_web.load_open())
    sessions = [dict(row, open=row["id"] in opened) for row in merge_web.list_sessions()]
    if not is_admin:
        sessions = [row for row in sessions if row["open"]]
    return {"sessions": sessions, "can_manage": is_admin}


@router.post("/sessions/{session_id}/open", operation_id="set_merge_session_open", dependencies=[Depends(same_origin)],
             summary="开放 / 收回会话给普通用户")
def set_session_open(session_id: str, body: OpenBody, _user: dict = Depends(admin_user)) -> dict:
    try:
        ids = merge_web.set_open(session_id, body.open)
    except ERRORS as error:
        raise failed(error)
    return {"open": ids}


@router.post("/sessions/upload", operation_id="upload_merge_session", dependencies=[Depends(same_origin)],
             summary="上传会话：请求体是会话目录打的 zip，或 snapshot.json")
async def upload_session(request: Request, _user: dict = Depends(admin_user)) -> dict:
    raw = await request.body()
    if not 0 < len(raw) <= merge_web.MAX_UPLOAD:
        raise HTTPException(status_code=400, detail={"message": "请求大小不对" if raw else "没有内容"})
    try:
        session_id = merge_web.import_zip(raw) if raw[:2] == b"PK" else merge_web.import_snapshot(raw)
    except ERRORS as error:
        raise failed(error)
    return {"session": session_id}


@router.delete("/sessions/{session_id}", operation_id="delete_merge_session", dependencies=[Depends(same_origin)],
               summary="删除会话（里面的评审选择一起删）")
def delete_session(session_id: str, _user: dict = Depends(admin_user)) -> dict:
    try:
        target = merge_web.session_dir(session_id)
        with merge_web._lock:
            merge_web._handlers.pop(str(target), None)
        shutil.rmtree(target)
        merge_web.save_open([s for s in merge_web.load_open() if s != session_id])
    except ERRORS as error:
        raise failed(error)
    return {"message": f"已删除 {session_id}。"}


@router.get("/projects", operation_id="list_merge_projects", summary="本机工程列表和各自的 merge 状态")
def list_projects(_user: dict = Depends(admin_user)) -> dict:
    return {"projects": [merge_web.project_status(repo) for repo in merge_web.load_projects()]}


@router.post("/projects", operation_id="add_merge_project", dependencies=[Depends(same_origin)], summary="把工程加入列表")
def add_project(body: RepoBody, _user: dict = Depends(admin_user)) -> dict:
    repo = os.path.abspath(os.path.expanduser(body.repo.strip()))
    if not os.path.isdir(repo):
        raise HTTPException(status_code=400, detail={"message": f"目录不存在：{repo}"})
    projects: List[str] = merge_web.load_projects()
    if repo not in projects:
        projects.append(repo)
        merge_web.save_projects(projects)
    return {"projects": projects}


@router.post("/projects/remove", operation_id="remove_merge_project", dependencies=[Depends(same_origin)],
             summary="从列表移除工程（不动仓库）")
def remove_project(body: RepoBody, _user: dict = Depends(admin_user)) -> dict:
    projects = [p for p in merge_web.load_projects() if p != body.repo]
    merge_web.save_projects(projects)
    return {"projects": projects}


@router.post("/projects/check", operation_id="check_merge_project", dependencies=[Depends(same_origin)],
             summary="看一眼工程是不是在 merge 冲突阶段（只读）")
def check_project(body: RepoBody, _user: dict = Depends(admin_user)) -> dict:
    return {"status": merge_web.project_status(body.repo.strip())}


@router.post("/projects/capture", operation_id="capture_merge_project", dependencies=[Depends(same_origin)],
             summary="采集工程当前的 merge 冲突，生成新会话（只读仓库）")
def capture_project(body: CaptureBody, _user: dict = Depends(admin_user)) -> dict:
    repo = body.repo.strip()
    if repo not in merge_web.load_projects():
        raise HTTPException(status_code=400, detail={"message": "先把工程加入列表"})
    try:
        session_id = merge_web.capture_project(repo, body.replay_base.strip())
    except ERRORS as error:
        raise failed(error)
    return {"session": session_id}
