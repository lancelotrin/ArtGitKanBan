"""自检复盘 API（只读）。读 reviews/ 下的 json；规则和渲染在 review_board.py。

*_html 字段由 review_board 的渲染函数生成：先整段转义，只放 <b>/<code>/链接等几个固定标签，前端可以直接插入。
同时给出 *_md 原文，方便 agent 直接读。
"""
import zlib

from fastapi import APIRouter, Depends, HTTPException

from typing import Optional

from boards import board_access
from boards import review_board as rw
from backend.deps import current_user

router = APIRouter(prefix="/reviews", tags=["自检复盘"])


def color(name: str) -> str:
    return rw.AVATAR_COLORS[zlib.crc32(name.encode("utf-8")) % len(rw.AVATAR_COLORS)]


def pill(info: dict) -> dict:
    return {"cls": info["pill"][0], "text": info["pill"][1]}


def allowed(user: dict) -> Optional[set]:
    """管理员开了「只看自己的」时，普通用户能看的文件名；None 表示不限制。不在名单里的人直接 403。"""
    if not board_access.restricted("/review", user):
        return None
    files = rw.own_files(user)
    if not files:
        raise HTTPException(status_code=403, detail={"message": "你不在复盘名单里，看不到复盘。要加人找管理员在后台登记。"})
    return files


def check(user: dict, name: str) -> None:
    files = allowed(user)
    if files is not None and name not in files:
        raise HTTPException(status_code=403, detail={"message": "只能看自己的复盘。"})


@router.get("/days", operation_id="review_day", summary="某一天所有人的复盘概要；不传 date 就是最新一天")
def review_day(date: str = "", user: dict = Depends(current_user)) -> dict:
    files = allowed(user)
    dates = rw.list_dates()
    if files is not None:  # 只留自己有记录的日期，前后翻页不会翻到空的一天
        dates = [day for day in dates if any(rw.load_record(day, name) for name in files)]
    if not dates:
        return {"dates": [], "date": "", "people": [], "mine_only": files is not None}
    day = date if rw.DATE_PATTERN.match(date or "") else dates[0]
    older, newer = rw.neighbors(dates, day)
    rows = [(record, rw.summary_of(record)) for record in rw.day_records(day)
            if files is None or record["_file"] in files]
    rows.sort(key=lambda row: (not row[1]["bomb"], not row[1]["risks"], -row[1]["commits"], str(row[0].get("name"))))
    people = []
    for record, info in rows:
        name = str(record.get("name") or record["_file"])
        people.append({"file": record["_file"], "name": name, "author": str(record.get("git_author") or ""),
                       "color": color(name), "commits": info["commits"], "risks": info["risks"], "bomb": info["bomb"],
                       "tags": info["tags"], "pill": pill(info), "summary_html": rw.render_inline(info["text"])})
    return {
        "dates": dates, "date": day, "weekday": rw.weekday(day), "older": older, "newer": newer, "latest": dates[0],
        "people": people, "mine_only": files is not None,
        "filters": [{"key": key, "text": text, "count": sum(1 for p in people if key == "all" or key in p["tags"])}
                    for key, text in rw.FILTERS],
        "stats": {"people": len(people), "commits": sum(p["commits"] for p in people),
                  "risk_people": sum(1 for p in people if p["risks"]), "bomb_people": sum(1 for p in people if p["bomb"])},
    }


@router.get("/people/{name}", operation_id="review_person", summary="某个人每天的复盘历史")
def review_person(name: str, user: dict = Depends(current_user)) -> dict:
    if not rw.NAME_PATTERN.match(name):
        raise HTTPException(status_code=404, detail={"message": "没有这个人。"})
    check(user, name)
    rows, display = [], name
    for day in rw.list_dates():
        record = rw.load_record(day, name)
        if not record:
            continue
        display = str(record.get("name") or name)
        info = rw.summary_of(record)
        rows.append({"date": day, "weekday": rw.weekday(day), "commits": info["commits"], "pill": pill(info),
                     "summary_html": rw.render_inline(info["text"])})
    if not rows:
        raise HTTPException(status_code=404, detail={"message": "这个人还没有复盘记录。"})
    return {"name": name, "display": display, "color": color(display), "rows": rows}


@router.get("/days/{date}/{name}", operation_id="review_report", summary="某人某天的完整复盘：提交、diff、隐患、炸线")
def review_report(date: str, name: str, user: dict = Depends(current_user)) -> dict:
    check(user, name)
    record = rw.load_record(date, name)
    if not record:
        raise HTTPException(status_code=404, detail={"message": "没有这份复盘。"})
    display = str(record.get("name") or name)
    info = rw.summary_of(record)
    person_dates = [day for day in rw.list_dates() if rw.load_record(day, name)]
    older, newer = rw.neighbors(person_dates, date)
    commits = []
    for item in record.get("commits") or []:
        time_text, subject = rw.commit_parts(item)
        diff_html = rw.render_diff(str(item.get("diff") or ""))
        note = ""
        if not diff_html:
            note = "旧报告没有存 diff，在后台补跑这一天即可。" if "diff" not in item else "这条没有代码类文本 diff（可能只改了二进制或非代码文件）。"
        commits.append({"hash": str(item.get("hash") or ""), "branch": str(item.get("branch") or ""), "time": time_text,
                        "subject": subject, "subject_html": rw.render_inline(subject), "diff_html": diff_html, "diff_note": note})
    return {
        "date": date, "weekday": rw.weekday(date), "name": name, "display": display, "color": color(display),
        "git_author": str(record.get("git_author") or ""),
        "generated_at": str(record.get("generated_at") or "")[:16].replace("T", " "),
        "status": str(record.get("status") or ""), "error": str(record.get("error") or ""),
        "pill": pill(info), "commit_count": info["commits"], "risks": info["risks"],
        "older": older, "newer": newer, "commits": commits,
        "report_md": record.get("report_md") or "", "report_html": rw.render_md(record["report_md"]) if record.get("report_md") else "",
        "bomb_out_md": record.get("bomb_out_md") or "",
        "bomb_out_html": rw.render_md(record["bomb_out_md"]) if record.get("bomb_out_md") else "",
        "bomb_in_md": record.get("bomb_in_md") or "",
        "bomb_in_html": rw.render_md(record["bomb_in_md"]) if record.get("bomb_in_md") else "",
    }
