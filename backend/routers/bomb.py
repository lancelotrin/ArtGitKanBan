"""炸线看板 API。数据在 bomb_board.py，由大豆的炸线扫描写入；网页只能标记已解决 / 撤销。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from bot import bomb
from boards import bomb_board
from backend.deps import current_user, same_origin

router = APIRouter(tags=["炸线看板"])


@router.get("/bombs", operation_id="list_bombs",
            summary="最近 90 天的疑似炸线（合并丢了别人的修改）和强推记录，以及自动检查的状态")
def list_bombs(_user: dict = Depends(current_user)) -> dict:
    return dict(bomb_board.board_view(), scan=bomb_board.scan_info(), note=bomb.BOMB_NOTE)


class ResolveBody(BaseModel):
    key: str = Field(min_length=1, max_length=80, description="记录的 key，例如 merge:cb81e9adec1")
    resolved: bool = Field(True, description="true 标记已解决（不再提醒），false 撤销")


@router.post("/bombs/resolve", operation_id="resolve_bomb", dependencies=[Depends(same_origin)],
             summary="把一条炸线标记为已解决（大豆不再提醒），或撤销标记")
def resolve_bomb(body: ResolveBody, user: dict = Depends(current_user)) -> dict:
    try:
        bomb_board.set_resolved(body.key, body.resolved, str(user.get("name") or user.get("open_id") or ""))
    except KeyError as error:
        raise HTTPException(status_code=404, detail={"message": str(error.args[0] if error.args else error)})
    return dict(bomb_board.board_view(), scan=bomb_board.scan_info(), note=bomb.BOMB_NOTE)
