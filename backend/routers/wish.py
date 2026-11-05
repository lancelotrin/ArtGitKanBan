"""心愿墙 API。规则全在 wish_board.py，这里只做参数校验和 HTTP 状态码。"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from boards import wish_board
from backend.deps import current_user, same_origin

router = APIRouter(prefix="/wishes", tags=["心愿墙"])

Status = Literal["open", "doing", "done"]


class Wish(BaseModel):
    id: int
    title: str
    detail: str = ""
    role: str = wish_board.DEFAULT_ROLE
    author: str = ""
    created_at: str = ""
    votes: List[str] = []
    status: Status = "open"
    taker: str = ""
    taken_at: str = ""
    done_at: str = ""


class WishSettings(BaseModel):
    allow_create: bool = True


class WishList(BaseModel):
    wishes: List[Wish]
    settings: WishSettings
    roles: List[str]
    me: str = Field(description="当前登录的人在心愿墙上的名字，用来判断“我的”“我 +1 过”")


class WishCreate(BaseModel):
    title: str = Field(min_length=wish_board.TITLE_MIN, max_length=wish_board.TITLE_MAX, description="一句话说清想要什么")
    detail: str = Field("", max_length=wish_board.DETAIL_MAX, description="补充说明：场景、痛点、怎样算实现")
    role: str = Field(wish_board.DEFAULT_ROLE, description="许愿人的岗位：" + "、".join(wish_board.ROLES))


class ActionResult(BaseModel):
    message: str
    wish: Optional[Wish] = None


def login_name(user: dict) -> str:
    """飞书名就是心愿墙上的名字；有空格的去掉空格再试一次。"""
    raw = str(user.get("name") or "")
    name = wish_board._clean_name(raw) or wish_board._clean_name(raw.replace(" ", ""))
    if not name:
        raise HTTPException(status_code=400, detail={"message": "飞书名字里有心愿墙不支持的字符，联系管理员处理。"})
    return name


def find_wish(wish_id: int) -> Optional[dict]:
    return next((wish for wish in wish_board.list_wishes() if wish.get("id") == wish_id), None)


def run(action: str, user: dict, payload: dict) -> ActionResult:
    payload = dict(payload, name=login_name(user))
    result = wish_board.apply(action, payload, client=user.get("open_id") or "", admin=user.get("role") == "admin")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail={"message": result.get("message") or "没成功。"})
    wish_id = result.get("id") or payload.get("id")
    wish = find_wish(int(wish_id)) if wish_id else None
    return ActionResult(message=result.get("message") or "好了", wish=Wish(**wish) if wish else None)


def existing(wish_id: int) -> int:
    if not find_wish(wish_id):
        raise HTTPException(status_code=404, detail={"message": "这个心愿不在了，刷新看看。"})
    return wish_id


@router.get("", response_model=WishList, operation_id="list_wishes", summary="列出所有心愿")
def list_wishes(user: dict = Depends(current_user)) -> WishList:
    return WishList(wishes=[Wish(**wish) for wish in wish_board.list_wishes()],
                    settings=WishSettings(**wish_board.settings()), roles=list(wish_board.ROLES),
                    me=login_name(user))


@router.post("", response_model=ActionResult, status_code=201, operation_id="create_wish", summary="许一个新心愿",
             dependencies=[Depends(same_origin)])
def create_wish(body: WishCreate, user: dict = Depends(current_user)) -> ActionResult:
    return run("create", user, body.model_dump())


@router.post("/{wish_id}/vote", response_model=ActionResult, operation_id="toggle_vote",
             summary="给心愿 +1；已经 +1 过就取消", dependencies=[Depends(same_origin)])
def vote(wish_id: int, user: dict = Depends(current_user)) -> ActionResult:
    return run("vote", user, {"id": existing(wish_id)})


@router.post("/{wish_id}/take", response_model=ActionResult, operation_id="take_wish",
             summary="接下一个待接的心愿", dependencies=[Depends(same_origin)])
def take(wish_id: int, user: dict = Depends(current_user)) -> ActionResult:
    return run("take", user, {"id": existing(wish_id)})


@router.post("/{wish_id}/drop", response_model=ActionResult, operation_id="drop_wish",
             summary="放弃自己接下的心愿，回到待接", dependencies=[Depends(same_origin)])
def drop(wish_id: int, user: dict = Depends(current_user)) -> ActionResult:
    return run("drop", user, {"id": existing(wish_id)})


@router.post("/{wish_id}/done", response_model=ActionResult, operation_id="finish_wish",
             summary="把进行中的心愿标成已实现（接的人或许愿的人）", dependencies=[Depends(same_origin)])
def done(wish_id: int, user: dict = Depends(current_user)) -> ActionResult:
    return run("done", user, {"id": existing(wish_id)})


@router.delete("/{wish_id}", response_model=ActionResult, operation_id="withdraw_wish",
               summary="撤回自己的、还没人接的心愿", dependencies=[Depends(same_origin)])
def withdraw(wish_id: int, user: dict = Depends(current_user)) -> ActionResult:
    return run("delete", user, {"id": existing(wish_id)})
