"""后台管理接口：逻辑都在 console.admin_get / admin_post，这里只做管理员校验和路由。

每个接口有自己的 operation_id 和中文说明，以后聊天 agent 可以按名字挑工具。
"""
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

from console import server as console
from backend.deps import admin_user, same_origin

router = APIRouter(prefix="/admin", tags=["后台管理"])

READS = {
    "status": "运行状态、工程（.env 里配）、拉代码、盯目录、索引、日志、提醒、本地程序",
    "reviews": "Cursor 对大豆回答的评价",
    "questions": "用户问过的问题",
    "misses": "答错记录",
    "notes": "纠正笔记",
    "people": "职务和人名",
    "users": "登录过的账号",
    "review": "自检复盘名单、群和上次运行情况",
    "bomb": "炸线提醒设置和最近报出的",
    "wish": "心愿墙管理视图",
    "models": "模型（在 .env 里配）和今天的调用次数",
    "bilibili": "B 站登录状态",
    "meta": "工作区里本地改过的 .meta：只改导入设置的、guid 变了的（含重复 guid 的提交，引用分成要换的和不动的）",
}
WRITES = {
    "users": "改账号角色或移除账号：action=user/admin/remove，open_id",
    "start": "启动大豆",
    "stop": "停止大豆",
    "wish": "心愿墙管理：action=allow_create/update/delete",
    "bomb": "炸线提醒：action=on/off/now（minutes、days）、groups、ignore_paths",
    "review": "自检复盘：action=add/remove（name、git_author）、dm（git_author、on：勾选私聊，按姓名自动找 open_id）、run（date）、groups",
    "bilibili": "B 站登录：action=qr/qr_poll，或 cookie",
    "sites": "看板开关：action=open（开放给普通用户）或 private（只看自己的），port（如 /review），on",
    "checkout": "把工作区切到某个分支：branch",
    "meta": "处理本地 .meta 改动（只提交不推送）：action=apply_all 一次全部处理：只改导入设置的直接提交；guid 和别的 meta 重复的，重复提交及之后引用旧 guid 的换成本地新 guid，之前就引用的不动",
    "pull": "定时拉代码：enabled、hours、run_now",
    "watch": "盯目录：enabled、minutes、days、rules、run_now",
    "reminders/delete": "取消一条提醒：id",
    "preview": "试跑一句话：text",
    "index/rebuild": "重建引用索引",
    "misses/handled": "答错记录标成已处理：id",
    "notes/delete": "删一条纠正：index",
    "people/delete": "删一个职务：role",
}


def unwrap(result: tuple) -> dict:
    status, payload = result
    if status >= 400:
        message = payload.get("error") if isinstance(payload, dict) else ""
        raise HTTPException(status_code=status, detail={"message": message or "没成功。"})
    return payload


def make_read(name: str):
    def read(user: dict = Depends(admin_user)) -> Dict[str, Any]:
        return unwrap(console.admin_get("/api/" + name, user))
    return read


def make_write(name: str):
    def write(body: Dict[str, Any] = Body(default={}), user: dict = Depends(admin_user)) -> Dict[str, Any]:
        return unwrap(console.admin_post("/api/" + name, body, user))
    return write


for _name, _summary in READS.items():
    router.add_api_route("/" + _name, make_read(_name), methods=["GET"], summary=_summary,
                         operation_id="admin_get_" + _name.replace("/", "_"))
for _name, _summary in WRITES.items():
    router.add_api_route("/" + _name, make_write(_name), methods=["POST"], summary=_summary,
                         operation_id="admin_" + _name.replace("/", "_"), dependencies=[Depends(same_origin)])
