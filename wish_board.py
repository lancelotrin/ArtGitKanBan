"""许愿板数据：wishes.json 里存愿望；网页填名字许愿、+1、接、放弃、实现。没有登录，名字靠自觉。"""
import datetime
import json
import os
import re
import threading
import time

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
WISH_PATH = os.environ.get("WISH_PATH") or os.path.join(BOT_DIR, "wishes.json")  # 测试时可指到别的文件
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{1,20}$")
TITLE_MIN = 2
TITLE_MAX = 60
DETAIL_MAX = 800
CREATE_LIMIT_PER_HOUR = 10
STATUS_TEXT = {"open": "待接", "doing": "进行中", "done": "已实现"}
ROLES = ("美术", "程序", "QA", "策划", "其他")
DEFAULT_ROLE = "其他"
_lock = threading.Lock()
_create_log = {}


def now_text() -> str:
    return datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def load() -> dict:
    try:
        with open(WISH_PATH, encoding="utf-8") as wish_file:
            data = json.load(wish_file)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("next_id", 1)
    data.setdefault("wishes", [])
    if not isinstance(data.get("settings"), dict):
        data["settings"] = {}
    data["settings"].setdefault("allow_create", True)
    for wish in data["wishes"]:
        if wish.get("role") not in ROLES:
            wish["role"] = DEFAULT_ROLE
    return data


def _clean_role(value) -> str:
    role = str(value or "").strip()
    return role if role in ROLES else DEFAULT_ROLE


def save(data: dict) -> None:
    temporary = WISH_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as wish_file:
        json.dump(data, wish_file, ensure_ascii=False, indent=2)
    os.replace(temporary, WISH_PATH)


def list_wishes() -> list:
    with _lock:
        return list(load()["wishes"])


def settings() -> dict:
    with _lock:
        return dict(load()["settings"])


def admin_view() -> dict:
    with _lock:
        data = load()
    wishes = sorted(data["wishes"], key=lambda wish: -int(wish.get("id") or 0))
    counts = {key: sum(1 for wish in wishes if wish.get("status") == key) for key in STATUS_TEXT}
    return {"wishes": wishes, "settings": data["settings"], "counts": counts, "total": len(wishes), "roles": list(ROLES)}


def admin_apply(action: str, payload: dict) -> dict:
    """后台管理：不看名字权限。返回 {ok, message}。"""
    with _lock:
        data = load()
        if action == "allow_create":
            data["settings"]["allow_create"] = bool(payload.get("value"))
            save(data)
            return {"ok": True, "message": "已打开发布。" if data["settings"]["allow_create"] else "已暂停发布，心愿墙只能看、+1 和接。"}
        wish = _find(data, payload.get("id"))
        if not wish:
            return {"ok": False, "message": "这个心愿不在了，刷新看看。"}
        if action == "delete":
            data["wishes"] = [item for item in data["wishes"] if item is not wish]
            save(data)
            return {"ok": True, "message": f"已删除 #{wish.get('id')}。"}
        if action != "update":
            return {"ok": False, "message": "不认识的操作。"}
        title = _clean_text(payload.get("title", wish.get("title")), TITLE_MAX)
        detail = _clean_text(payload.get("detail", wish.get("detail")), DETAIL_MAX)
        status = str(payload.get("status") or wish.get("status") or "open")
        taker = str(payload.get("taker", wish.get("taker")) or "").strip()
        if len(title) < TITLE_MIN:
            return {"ok": False, "message": "标题至少 2 个字。"}
        if status not in STATUS_TEXT:
            return {"ok": False, "message": "状态不对。"}
        if taker and not NAME_PATTERN.match(taker):
            return {"ok": False, "message": "接的人名字只能是中文、字母或数字，20 字以内。"}
        if status == "doing" and not taker:
            return {"ok": False, "message": "进行中要填接的人。"}
        if status == "open":
            taker = ""
        if taker != wish.get("taker") or (taker and not wish.get("taken_at")):
            wish["taken_at"] = now_text() if taker else ""
        if status == "done" and wish.get("status") != "done":
            wish["done_at"] = now_text()
        if status != "done":
            wish["done_at"] = ""
        wish.update(title=title, detail=detail, status=status, taker=taker, role=_clean_role(payload.get("role", wish.get("role"))))
        save(data)
        return {"ok": True, "message": f"已保存 #{wish.get('id')}。"}


def _clean_name(value) -> str:
    name = str(value or "").strip()
    return name if NAME_PATTERN.match(name) else ""


def _clean_text(value, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value or "")).strip()
    return text[:limit]


def _find(data: dict, wish_id) -> dict:
    try:
        target = int(wish_id)
    except (TypeError, ValueError):
        return {}
    return next((wish for wish in data["wishes"] if wish.get("id") == target), {})


def _allow_create(client: str) -> bool:
    cutoff = time.time() - 3600
    stamps = [stamp for stamp in _create_log.get(client, []) if stamp > cutoff]
    if len(stamps) >= CREATE_LIMIT_PER_HOUR:
        _create_log[client] = stamps
        return False
    _create_log[client] = stamps + [time.time()]
    return True


def apply(action: str, payload: dict, client: str = "", admin: bool = False) -> dict:
    """返回 {ok, message}。所有改动都在锁里读-改-写。admin=True 时可以替别人放弃、标完成、撤回。"""
    name = _clean_name(payload.get("name"))
    if not name:
        return {"ok": False, "message": "先填名字（中文、字母、数字，20 字以内）。"}
    with _lock:
        data = load()
        if action == "create":
            title = _clean_text(payload.get("title"), TITLE_MAX)
            detail = _clean_text(payload.get("detail"), DETAIL_MAX)
            if not data["settings"].get("allow_create", True):
                return {"ok": False, "message": "心愿墙暂停发布了，可以先给已有的心愿点 ♥。"}
            if len(title) < TITLE_MIN:
                return {"ok": False, "message": "愿望标题至少 2 个字。"}
            if not _allow_create(client):
                return {"ok": False, "message": "许愿太频繁了，过一会儿再来。"}
            wish = {"id": int(data["next_id"]), "title": title, "detail": detail, "role": _clean_role(payload.get("role")),
                    "author": name, "created_at": now_text(),
                    "votes": [], "status": "open", "taker": "", "taken_at": "", "done_at": ""}
            data["next_id"] = wish["id"] + 1
            data["wishes"].append(wish)
            save(data)
            return {"ok": True, "message": "许好了，大家都能在板上看到。", "id": wish["id"]}
        wish = _find(data, payload.get("id"))
        if not wish:
            return {"ok": False, "message": "这个愿望不在了，刷新看看。"}
        if action == "vote":
            votes = list(wish.get("votes") or [])
            if name == wish.get("author"):
                return {"ok": False, "message": "自己的愿望不用 +1。"}
            if name in votes:
                votes.remove(name)
                message = "已取消 +1。"
            else:
                votes.append(name)
                message = "+1 成功。"
            wish["votes"] = votes
        elif action == "take":
            if wish.get("status") != "open":
                return {"ok": False, "message": f"已经被 {wish.get('taker')} 接了。" if wish.get("taker") else "这个愿望现在接不了。"}
            wish.update(status="doing", taker=name, taken_at=now_text())
            message = "接下了，加油。"
        elif action == "drop":
            if wish.get("status") != "doing" or (wish.get("taker") != name and not admin):
                return {"ok": False, "message": "只有接的人能放弃。"}
            wish.update(status="open", taker="", taken_at="")
            message = "已放弃，愿望回到待接。"
        elif action == "done":
            if wish.get("status") != "doing" or (name not in (wish.get("taker"), wish.get("author")) and not admin):
                return {"ok": False, "message": "只有接的人或许愿的人能标成已实现。"}
            wish.update(status="done", done_at=now_text())
            message = "愿望实现了。"
        elif action == "delete":
            if not admin and (wish.get("author") != name or wish.get("status") != "open"):
                return {"ok": False, "message": "只有许愿的人能撤回，而且要在没人接之前。"}
            data["wishes"] = [item for item in data["wishes"] if item is not wish]
            message = "已撤回。"
        else:
            return {"ok": False, "message": "不认识的操作。"}
        save(data)
        return {"ok": True, "message": message}
