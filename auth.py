"""大豆后台的登录：用飞书账号登录（OAuth 网页授权），登录状态存在浏览器 cookie 里，长期有效。

配置写在项目根目录的 .env（照 .env.example 抄一份；系统环境变量优先于 .env）：
  FEISHU_APP_ID         飞书自建应用的 App ID（开放平台 → 凭证与基础信息）
  FEISHU_APP_SECRET     App Secret
  FEISHU_REDIRECT_URI   可选，默认 http://<访问的域名>/auth/feishu/callback
需要在飞书开放平台 → 应用 → 安全设置 → 重定向 URL 里加上这个回调地址。

部门：登录时用应用身份（tenant_access_token）查通讯录拿到所在部门，显示在名字边上，聊天时也告诉模型。
  要在开放平台 → 权限管理开通「获取用户组织架构信息」contact:user.department:readonly 和
  「获取部门基础信息」contact:department.base:readonly，并把通讯录权限范围设成全员（或包含要登录的人）。
  查不到（没权限、不在范围里）不影响登录，部门留空，原因写进日志。

角色：user（普通）/ admin（管理员）。还没有管理员时，第一个登录的人自动成为管理员。
命令行：
  python auth.py list               列出登录过的人
  python auth.py admin <名字或open_id>   设为管理员
  python auth.py user <名字或open_id>    取消管理员
  python auth.py departments        给登录过的人补查一遍部门
"""
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BOT_DIR, ".env")
USERS_PATH = os.path.join(BOT_DIR, "users.json")
SESSIONS_PATH = os.path.join(BOT_DIR, "sessions.json")
COOKIE = "dadou_sid"
STATE_COOKIE = "dadou_oauth"
SESSION_DAYS = 180  # 用着就自动续期，半年不用才要重新登录
SESSION_SECONDS = SESSION_DAYS * 86400
STATE_SECONDS = 600
AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
CONTACT_USER_URL = "https://open.feishu.cn/open-apis/contact/v3/users/{}?user_id_type=open_id&department_id_type=open_department_id"
DEPARTMENT_URL = "https://open.feishu.cn/open-apis/contact/v3/departments/{}?department_id_type=open_department_id"
ROLES = ("user", "admin")

_lock = threading.Lock()
_states: dict = {}  # state -> {"next": str, "at": float}


class AuthError(Exception):
    pass


def _read(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write(path: str, data) -> None:
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def read_env_file(path: str = None) -> dict:
    """读 .env：每行 KEY=VALUE，# 开头是注释，值两边的引号会去掉。"""
    values = {}
    try:
        with open(path or ENV_PATH, "r", encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def env_value(name: str, file_values: dict) -> str:
    value = (os.environ.get(name) or file_values.get(name) or "").strip()
    return "" if "replace_me" in value else value  # .env.example 里的占位符当没填


def feishu_config() -> dict:
    """每次现读，改了 .env 不用重启。"""
    values = read_env_file()
    return {
        "app_id": env_value("FEISHU_APP_ID", values),
        "app_secret": env_value("FEISHU_APP_SECRET", values),
        "redirect_uri": env_value("FEISHU_REDIRECT_URI", values),
    }


def redirect_uri(host: str) -> str:
    return feishu_config()["redirect_uri"] or f"http://{host}/auth/feishu/callback"


def safe_next(value: str) -> str:
    """只允许跳回本站的相对路径，防止被拿去跳到别的网站。"""
    value = str(value or "")
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


# ---------- 飞书授权 ----------

def begin_login(host: str, next_path: str) -> tuple:
    """返回 (跳转到飞书的地址, state)。state 同时写进 cookie，回调时核对，防止登录 CSRF。"""
    config = feishu_config()
    if not config["app_id"] or not config["app_secret"]:
        raise AuthError("还没配置飞书应用：在项目根目录的 .env 里填 FEISHU_APP_ID 和 FEISHU_APP_SECRET（参考 .env.example）。")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for key in [k for k, v in _states.items() if now - v["at"] > STATE_SECONDS]:
            _states.pop(key, None)
        _states[state] = {"next": safe_next(next_path), "at": now}
    query = urllib.parse.urlencode({"client_id": config["app_id"], "redirect_uri": redirect_uri(host), "state": state})
    return f"{AUTHORIZE_URL}?{query}", state


def _post_json(url: str, body: dict, headers: dict = None) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers=dict({"Content-Type": "application/json; charset=utf-8"}, **(headers or {})))
    return _open(request)


def _get_json(url: str, headers: dict) -> dict:
    return _open(urllib.request.Request(url, headers=headers))


def _open(request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read().decode("utf-8"))
        except ValueError:
            raise AuthError(f"飞书返回 HTTP {error.code}")
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise AuthError(f"连不上飞书：{type(error).__name__}")


def finish_login(host: str, code: str, state: str, cookie_state: str) -> tuple:
    """飞书回调。返回 (用户, 会话 token, 要跳回的路径)。"""
    with _lock:
        entry = _states.pop(state or "", None)
    if not entry or not cookie_state or not secrets.compare_digest(state, cookie_state) \
            or time.time() - entry["at"] > STATE_SECONDS:
        raise AuthError("登录链接过期了，请重新登录。")
    if not code:
        raise AuthError("飞书没有给授权码，可能是取消了授权。")
    config = feishu_config()
    token = _post_json(TOKEN_URL, {"grant_type": "authorization_code", "client_id": config["app_id"],
                                   "client_secret": config["app_secret"], "code": code,
                                   "redirect_uri": redirect_uri(host)})
    access = token.get("access_token")
    if token.get("code") not in (0, None) or not access:
        raise AuthError(f"飞书换 token 失败：{token.get('error_description') or token.get('msg') or token.get('code')}")
    info = _get_json(USER_INFO_URL, {"Authorization": f"Bearer {access}"})
    data = info.get("data") or {}
    if info.get("code") != 0 or not data.get("open_id"):
        raise AuthError(f"读不到飞书用户信息：{info.get('msg') or info.get('code')}")
    data["department"] = lookup_department(data["open_id"])
    user = remember_user(data)
    return user, create_session(user["open_id"]), entry["next"]


# ---------- 部门 ----------

_tenant = {"token": "", "until": 0.0}
_department_names: dict = {}  # open_department_id -> 名称，进程内缓存


def tenant_token() -> str:
    """应用身份的 token，有效期 2 小时，提前 5 分钟换新的。"""
    if _tenant["token"] and time.time() < _tenant["until"]:
        return _tenant["token"]
    config = feishu_config()
    if not config["app_id"] or not config["app_secret"]:
        raise AuthError("还没配置飞书应用")
    body = _post_json(TENANT_TOKEN_URL, {"app_id": config["app_id"], "app_secret": config["app_secret"]})
    if body.get("code") != 0 or not body.get("tenant_access_token"):
        raise AuthError(f"拿不到应用 token：{body.get('msg') or body.get('code')}")
    _tenant.update(token=body["tenant_access_token"], until=time.time() + int(body.get("expire") or 7200) - 300)
    return _tenant["token"]


def department_name(department_id: str, headers: dict) -> str:
    if department_id in _department_names:
        return _department_names[department_id]
    body = _get_json(DEPARTMENT_URL.format(urllib.parse.quote(department_id)), headers)
    name = str(((body.get("data") or {}).get("department") or {}).get("name") or "") if body.get("code") == 0 else ""
    if name:
        _department_names[department_id] = name
    return name


def lookup_department(open_id: str) -> str:
    """返回部门名称，多个部门用「、」连起来；查不到返回空字符串（不影响登录）。"""
    try:
        headers = {"Authorization": f"Bearer {tenant_token()}"}
        body = _get_json(CONTACT_USER_URL.format(urllib.parse.quote(open_id)), headers)
        if body.get("code") != 0:
            raise AuthError(f"查通讯录失败：{body.get('msg') or body.get('code')}")
        user = (body.get("data") or {}).get("user") or {}
        if "department_ids" not in user:  # 没开权限时飞书不报错，只是不返回这个字段
            raise AuthError("飞书没返回 department_ids，应用还没开通读部门的权限")
        ids = user["department_ids"] or []
        names = [name for name in (department_name(str(item), headers) for item in ids[:3]) if name]
        return "、".join(names)[:60]
    except AuthError as error:
        print(f"[auth] 部门没查到 {open_id}：{error}（要开通 contact:user.department:readonly、"
              f"contact:department.base:readonly，通讯录权限范围要包含这个人）", flush=True)
        return ""


def refresh_departments() -> list:
    """给登录过的人补查部门，返回 [(名字, 部门)]。查不到的保留原来的。"""
    done = []
    for open_id in list(load_users()):
        department = lookup_department(open_id)
        with _lock:
            users = load_users()
            if open_id in users and department:
                users[open_id]["department"] = department
                _write(USERS_PATH, users)
            user = users.get(open_id) or {}
        done.append((user.get("name", open_id), user.get("department", "")))
    return done


# ---------- 用户 ----------

def load_users() -> dict:
    return _read(USERS_PATH, {})


def remember_user(info: dict) -> dict:
    open_id = str(info["open_id"])
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        users = load_users()
        user = users.get(open_id) or {"open_id": open_id, "role": "user", "first_login": now}
        if not any(item.get("role") == "admin" for item in users.values()):
            user["role"] = "admin"  # 还没有管理员：第一个登录的人当管理员
        user.update(name=str(info.get("name") or info.get("en_name") or open_id)[:40],
                    avatar=str(info.get("avatar_url") or info.get("avatar_thumb") or ""),
                    union_id=str(info.get("union_id") or ""), last_login=now)
        if info.get("department"):  # 这次没查到就保留上次的
            user["department"] = str(info["department"])[:60]
        users[open_id] = user
        _write(USERS_PATH, users)
    return dict(user)


def public_user(user: dict) -> dict:
    return {key: user.get(key, "") for key in ("open_id", "name", "department", "avatar", "role", "first_login",
                                                "last_login")}


def list_users() -> list:
    users = sorted(load_users().values(), key=lambda u: (u.get("role") != "admin", u.get("last_login") or ""), reverse=False)
    return [public_user(user) for user in users]


def find_user(key: str) -> dict:
    users = load_users()
    if key in users:
        return users[key]
    matches = [user for user in users.values() if user.get("name") == key]
    if len(matches) != 1:
        raise AuthError("没找到这个人（要先用飞书登录过一次）。" if not matches else "重名了，请用 open_id。")
    return matches[0]


def set_role(key: str, role: str, by: str = "") -> dict:
    if role not in ROLES:
        raise AuthError("不认识的角色。")
    with _lock:
        users = load_users()
        target = find_user(key)
        if role != "admin" and target.get("role") == "admin" \
                and sum(1 for u in users.values() if u.get("role") == "admin") <= 1:
            raise AuthError("至少要留一个管理员。")
        if by and target["open_id"] == by and role != "admin":
            raise AuthError("不能取消自己的管理员。")
        users[target["open_id"]]["role"] = role
        _write(USERS_PATH, users)
        return public_user(users[target["open_id"]])


def remove_user(key: str, by: str = "") -> None:
    with _lock:
        users = load_users()
        target = find_user(key)
        if target["open_id"] == by:
            raise AuthError("不能删除自己。")
        if target.get("role") == "admin" and sum(1 for u in users.values() if u.get("role") == "admin") <= 1:
            raise AuthError("至少要留一个管理员。")
        users.pop(target["open_id"], None)
        _write(USERS_PATH, users)
        sessions = _read(SESSIONS_PATH, {})
        _write(SESSIONS_PATH, {k: v for k, v in sessions.items() if v.get("open_id") != target["open_id"]})


# ---------- 会话 ----------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()  # 文件里只存哈希，泄露了也拿不到 cookie


def create_session(open_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        sessions = {k: v for k, v in _read(SESSIONS_PATH, {}).items() if v.get("expires", 0) > now}
        sessions[_digest(token)] = {"open_id": open_id, "created": now, "expires": now + SESSION_SECONDS}
        _write(SESSIONS_PATH, sessions)
    return token


def session_user(token: str):
    """cookie 里的 token 对应的用户；没登录或过期返回 None。剩不到一半有效期时自动续期。"""
    if not token:
        return None
    key = _digest(token)
    now = time.time()
    sessions = _read(SESSIONS_PATH, {})
    entry = sessions.get(key)
    if not entry or entry.get("expires", 0) <= now:
        return None
    user = load_users().get(entry.get("open_id"))
    if not user:
        return None
    if entry["expires"] - now < SESSION_SECONDS / 2:
        with _lock:
            sessions = _read(SESSIONS_PATH, {})
            if key in sessions:
                sessions[key]["expires"] = now + SESSION_SECONDS
                _write(SESSIONS_PATH, sessions)
    return dict(user)


def end_session(token: str) -> None:
    if not token:
        return
    with _lock:
        sessions = _read(SESSIONS_PATH, {})
        if sessions.pop(_digest(token), None) is not None:
            _write(SESSIONS_PATH, sessions)


def cookie_header(name: str, value: str, max_age: int) -> str:
    return f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"


def read_cookie(header: str, name: str) -> str:
    for part in str(header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def main(argv: list) -> int:
    if len(argv) == 2 and argv[1] == "list":
        for user in list_users():
            print(f"{user['role']:5}  {user['name']}  {user['department'] or '-'}  {user['open_id']}  "
                  f"最近登录 {user['last_login']}")
        return 0
    if len(argv) == 2 and argv[1] == "departments":
        for name, department in refresh_departments():
            print(f"{name}  {department or '（没查到）'}")
        return 0
    if len(argv) == 3 and argv[1] in ROLES:
        try:
            user = set_role(argv[2], argv[1])
        except AuthError as error:
            print(error)
            return 1
        print(f"{user['name']} 现在是 {'管理员' if user['role'] == 'admin' else '普通用户'}。")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
