"""监修改动看板 API：只读。数据由大豆的文档监测写在 data/doc_watch/，见 boards/doc_watch_board.py。"""
from fastapi import APIRouter, Depends

from backend.deps import current_user
from boards import doc_watch_board

router = APIRouter(tags=["监修改动"])


@router.get("/docwatch", operation_id="list_doc_changes",
            summary="监修文档最近的改动批次：每个标题改了什么、AI 分析涉及的功能和该跟进的人、实际通知了谁；以及等确认归属的改动")
def list_doc_changes(_user: dict = Depends(current_user)) -> dict:
    return doc_watch_board.board_view()
