"""看板的「只看自己的」开关：管理员在后台「网站」里按看板开关，记在 data/board_private.json。

开了以后普通用户只看得到自己的那份；自己不在名单里，门户和导航里就不显示这个看板。管理员不受影响。
目前支持的看板列在 SUPPORTED 里，新看板要支持时在这里加一条，并在它的接口里按 visible_* 过滤。
"""
import json
import os
import threading

from common.paths import DATA_DIR

PRIVATE_PATH = os.path.join(DATA_DIR, "board_private.json")
SUPPORTED = {
    "/review": "开启后普通用户只看得到自己的复盘；不在复盘名单里的人看不到这个看板。",
}

_lock = threading.Lock()


def load() -> dict:
    try:
        with open(PRIVATE_PATH, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    boards = data.get("boards") if isinstance(data, dict) else None
    return {port: bool(on) for port, on in (boards or {}).items() if port in SUPPORTED}


def is_private(port: str) -> bool:
    return load().get(port, False)


def set_private(port: str, on: bool) -> dict:
    if port not in SUPPORTED:
        raise ValueError("这个看板不支持「只看自己的」。")
    with _lock:
        boards = load()
        boards[port] = bool(on)
        os.makedirs(os.path.dirname(PRIVATE_PATH), exist_ok=True)
        temp = PRIVATE_PATH + ".tmp"
        with open(temp, "w", encoding="utf-8") as stream:
            json.dump({"boards": boards}, stream, ensure_ascii=False, indent=2)
        os.replace(temp, PRIVATE_PATH)
    return boards


def restricted(port: str, user: dict) -> bool:
    """这个人在这个看板上是不是只能看自己的。"""
    return user.get("role") != "admin" and is_private(port)
