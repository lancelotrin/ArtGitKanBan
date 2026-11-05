"""炸线看板 API（只读）。数据在 bomb_board.py，由大豆的炸线扫描写入。"""
from fastapi import APIRouter, Depends

import bomb
import bomb_board
import bomb_web
from backend.deps import current_user

router = APIRouter(tags=["炸线看板"])


@router.get("/bombs", operation_id="list_bombs",
            summary="最近 90 天的疑似炸线（合并丢了别人的修改）和强推记录，以及自动检查的状态")
def list_bombs(_user: dict = Depends(current_user)) -> dict:
    return dict(bomb_board.board_view(), scan=bomb_web.scan_info(), note=bomb.BOMB_NOTE)
