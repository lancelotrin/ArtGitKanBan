"""看板的两个开关，管理员在后台「网站」里按看板开关，都记在 data/board_private.json：

- 「只看自己的」（boards）：开了以后普通用户只看得到自己的那份；自己不在名单里，门户和导航里就不显示这个看板。
  目前支持的看板列在 SUPPORTED 里，新看板要支持时在这里加一条，并在它的接口里按 visible_* 过滤。
- 「开放给大家」（hidden）：每个看板都有。关掉后普通用户在门户和导航里看不到，打开页面或调接口也会被拦下。
  看板和接口路径的对应在 API_PREFIXES 里，新看板要在这里加一条。

管理员两个开关都不受影响。
"""
import json
import os
import threading

from common.paths import DATA_DIR

PRIVATE_PATH = os.path.join(DATA_DIR, "board_private.json")
SUPPORTED = {
    "/review": "开启后普通用户只看得到自己的复盘；不在复盘名单里的人看不到这个看板。",
}
# 看板路径 → 它用到的 /api/v1 接口前缀。关掉的看板，普通用户访问这些接口一律 403。
API_PREFIXES = {
    "/review": ("/api/v1/reviews",),
    "/wish": ("/api/v1/wishes",),
    "/bomb": ("/api/v1/bombs",),
    "/merge": ("/api/v1/merge",),
    "/chat": ("/api/v1/chat",),
    "/help": ("/api/v1/help",),
}
CLOSED_MESSAGE = "这个看板管理员暂时关闭了。"

_lock = threading.Lock()


def _read() -> dict:
    try:
        with open(PRIVATE_PATH, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    os.makedirs(os.path.dirname(PRIVATE_PATH), exist_ok=True)
    temp = PRIVATE_PATH + ".tmp"
    with open(temp, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    os.replace(temp, PRIVATE_PATH)


def load() -> dict:
    boards = _read().get("boards")
    return {port: bool(on) for port, on in (boards if isinstance(boards, dict) else {}).items() if port in SUPPORTED}


def load_hidden() -> set:
    hidden = _read().get("hidden")
    return {port for port, on in (hidden if isinstance(hidden, dict) else {}).items() if on and port in API_PREFIXES}


def is_private(port: str) -> bool:
    return load().get(port, False)


def set_private(port: str, on: bool) -> dict:
    if port not in SUPPORTED:
        raise ValueError("这个看板不支持「只看自己的」。")
    with _lock:
        data = _read()
        data["boards"] = dict(load(), **{port: bool(on)})
        _write(data)
    return data["boards"]


def set_hidden(port: str, hidden: bool) -> set:
    if port not in API_PREFIXES:
        raise ValueError("没有这个看板。")
    with _lock:
        data = _read()
        ports = load_hidden()
        if hidden:
            ports.add(port)
        else:
            ports.discard(port)
        data["hidden"] = {p: True for p in sorted(ports)}
        _write(data)
    return ports


def restricted(port: str, user: dict) -> bool:
    """这个人在这个看板上是不是只能看自己的。"""
    return user.get("role") != "admin" and is_private(port)


def board_of(path: str) -> str:
    """页面或接口路径属于哪个看板；不属于任何看板返回空串。"""
    for port, prefixes in API_PREFIXES.items():
        for prefix in (port,) + prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                return port
    return ""


def closed_for(path: str, user: dict) -> bool:
    """这个人打开这个路径时，所属看板是不是被管理员关掉了。没登录的交给登录检查，这里不拦。"""
    if not user or user.get("role") == "admin":
        return False
    port = board_of(path)
    return bool(port) and port in load_hidden()
