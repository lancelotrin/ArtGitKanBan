"""大豆后台。只监听本机 127.0.0.1:80，不对外网开放。

启动：在项目根目录 python -m console（或 ./restart.sh）
浏览器打开 http://127.0.0.1
"""
import http.client
import importlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
try:
    import winreg
except ImportError:  # 非 Windows
    winreg = None
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from common.paths import DATA_DIR, ROOT_DIR, migrate_legacy_data

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG_PATH = os.path.join(DATA_DIR, "bot.log")
ERR_PATH = os.path.join(DATA_DIR, "bot.err.log")
MISSES_PATH = os.path.join(DATA_DIR, "misses.jsonl")
QUESTIONS_PATH = os.path.join(DATA_DIR, "questions.jsonl")
HANDLED_PATH = os.path.join(DATA_DIR, "misses_handled.json")
NOTES_PATH = os.path.join(DATA_DIR, "notes.json")
REVIEWS_PATH = os.path.join(DATA_DIR, "reviews.json")
PEOPLE_PATH = os.path.join(DATA_DIR, "people.json")
REF_INDEX_PATH = os.path.join(DATA_DIR, "ref_index.sqlite")
PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
HOST = os.environ.get("CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CONSOLE_PORT", "80"))
# 页面和接口都在新后端（backend/ FastAPI + web/ React），console/server.py 只管飞书登录、退出，再按路由表转发。
# 唯一的例外：单个合并会话的评审页 /merge/s/<会话>/ 是 MergeReviewPortable 自带的页面，由 merge_web 处理。
MOUNTS = {"/merge": "boards.merge_web"}
BOARD_API = None  # start_board_api() 起好新后端后填上端口和路由表
def board_route(path: str, rules: tuple) -> bool:
    """rules 里以 / 结尾的是前缀（"/merge/s/"），"=/" 这种是整条路径，其它是前缀且含自身（"/wish" 管 /wish 和 /wish/...）。"""
    for rule in rules:
        if rule.startswith("="):
            if path == rule[1:]:
                return True
        elif rule.endswith("/"):
            if path.startswith(rule):
                return True
        elif path == rule or path.startswith(rule + "/"):
            return True
    return False


HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade", "content-length", "x-forwarded-for", "server", "date"}
# 工程目录、分支、模型都在 .env 里（见 settings.py），网页上只显示不修改。
ENV_ONLY = "工程目录、分支和模型改在项目根目录的 .env 里（PROJECT_ROOT、GIT_BRANCHES、LLM_MODEL、LLM_VISION_MODEL），改完重启大豆。"

index_lock = threading.Lock()
index_state = {"running": False, "rows": 0, "error": "", "started": 0.0}

def read_json(path: str, fallback):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return fallback


def write_json(path: str, payload) -> None:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def load_config() -> dict:
    from common import settings
    return {"project_root": settings.project_root(), "git_branches": settings.git_branches()}


def load_models() -> dict:
    from common import settings
    return settings.models()


def bot_models_env() -> dict:
    from common import settings
    return {settings.MODEL_ENV[kind]: name for kind, name in load_models().items()}


def apply_models_here() -> None:
    """后台进程自己的试跑、补跑也用同一对模型；只改本进程。"""
    os.environ.update(bot_models_env())
    loaded = sys.modules.get("bot.echo_bot")
    if loaded is not None:
        current = load_models()
        loaded.LLM_MODEL = current["chat"]
        loaded.LLM_VISION_MODEL = current["vision"]


def models_view() -> dict:
    from common import model_stats
    from common import settings
    return dict(model_stats.view(), current=load_models(),
                defaults={kind: choices[0] for kind, choices in settings.MODEL_CHOICES.items()},
                env=settings.MODEL_ENV, note=ENV_ONLY)


def watch_view() -> dict:
    saved = read_json(os.path.join(DATA_DIR, "watch.json"), {})
    if not isinstance(saved, dict) or not saved:
        return {
            "enabled": False,
            "minutes": 30,
            "days": 1,
            "rules_text": "Assets/Bundle/Shader .shader .hlsl",
            "next_at": "",
            "run_requested": False,
            "last_at": "",
            "last_result": "大豆启动后会从当前提交开始盯。",
        }
    interval = int(saved.get("interval_sec") or 1800)
    rules = saved.get("rules") if isinstance(saved.get("rules"), list) else []
    lines = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        path = str(rule.get("path") or "")
        exts = " ".join(str(ext) for ext in rule.get("exts") or [])
        if path and exts:
            lines.append(path + " " + exts)
    return {
        "enabled": bool(saved.get("enabled")),
        "minutes": max(interval // 60, 1),
        "days": saved.get("lookback_days") if isinstance(saved.get("lookback_days"), int) else 1,
        "rules_text": "\n".join(lines),
        "next_at": str(saved.get("next_at") or "").replace("T", " ")[:16],
        "run_requested": bool(saved.get("run_requested")),
        "last_at": str(saved.get("last_at") or "").replace("T", " ")[:16],
        "last_result": str(saved.get("last_result") or "")[:500],
    }


def pull_view() -> dict:
    saved = read_json(os.path.join(DATA_DIR, "pull_schedule.json"), {})
    if not isinstance(saved, dict):
        saved = {}
    interval = int(saved.get("interval_sec") or 0)
    hours = (interval // 3600) if interval and interval % 3600 == 0 else (interval / 3600 if interval else 5)
    return {
        "enabled": bool(saved.get("enabled")),
        "hours": hours,
        "next_at": str(saved.get("next_at") or "").replace("T", " ")[:16],
        "run_requested": bool(saved.get("run_requested")),
        "last_at": str(saved.get("last_at") or "").replace("T", " ")[:16],
        "last_result": str(saved.get("last_result") or "")[:400],
    }


def agent_rows() -> list:
    saved = read_json(os.path.join(DATA_DIR, "agents.json"), {})
    items = saved.get("items") if isinstance(saved, dict) else []
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        identity = str(item.get("open_id") or "")
        rows.append({
            "seen": str(item.get("seen") or "").replace("T", " ")[:16],
            "root": str(item.get("root") or ""),
            "who": ("…" + identity[-6:]) if len(identity) > 6 else identity,
        })
    rows.reverse()
    return rows[:20]


def reminder_rows() -> list:
    saved = read_json(os.path.join(DATA_DIR, "reminders.json"), {})
    items = saved.get("items") if isinstance(saved, dict) else []
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        names = [str(target.get("name") or "你") for target in item.get("targets") or [] if isinstance(target, dict)]
        rows.append({
            "id": str(item.get("id") or ""),
            "when": str(item.get("when") or "").replace("T", " ")[:16],
            "who": "、".join(names) or "这个会话",
            "task": ("加急 · " if item.get("urgent") == "app" else "") + str(item.get("task") or ""),
        })
    return rows


BOT_COMMAND = re.compile(r"echo_bot\.py|-m bot(\s|$)|event \+subscribe")  # 认旧的 echo_bot.py 启动方式，方便停掉


def is_bot_command(line: str) -> bool:
    return bool(BOT_COMMAND.search(line))


def bot_processes() -> list:
    if sys.platform != "win32":
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True, errors="replace", timeout=6)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return []
        return [int(line.split(None, 1)[0]) for line in output.splitlines()
                if is_bot_command(line) and line.split(None, 1)[0].isdigit()]
    script = (
        "Get-Process python,node -ErrorAction SilentlyContinue | ForEach-Object { "
        "$cmd = (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine; "
        "if ($cmd) { $_.Id.ToString() + '|' + $cmd } }"
    )
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True, errors="replace", timeout=6, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return []
    found = []
    for line in output.splitlines():
        if is_bot_command(line):
            pid_text = line.split("|", 1)[0].strip()
            if pid_text.isdigit():
                found.append(int(pid_text))
    return found


def stop_bot() -> int:
    pids = bot_processes()
    for pid in pids:
        if sys.platform != "win32":
            try:
                os.kill(pid, 15)
            except OSError:
                pass
            continue
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True, creationflags=NO_WINDOW)
    return len(pids)


def start_bot() -> str:
    if bot_processes():
        return "已经在运行。"
    python = PYTHON if os.path.isfile(PYTHON) else sys.executable
    key = ""
    try:
        if winreg is None:
            raise OSError("no winreg")
        reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        key = winreg.QueryValueEx(reg, "LLM_API_KEY")[0]
    except OSError:
        key = os.environ.get("LLM_API_KEY", "")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["LLM_API_KEY"] = key
    env.update(bot_models_env())
    env["LLM_BASE_URL"] = env.get("LLM_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4"
    profile = os.environ.get("USERPROFILE") or r"C:\Users\Administrator"
    lock = os.path.join(profile, r".lark-cli\locks\subscribe_cli_a964587c1d781bd4.lock")
    try:
        if os.path.isfile(lock):
            os.remove(lock)
    except OSError:
        pass
    out = open(LOG_PATH, "w", encoding="utf-8")
    err = open(ERR_PATH, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [python, "-u", "-m", "bot"],
        cwd=ROOT_DIR, env=env, stdout=out, stderr=err, creationflags=NO_WINDOW,
    )
    out.close()
    err.close()
    # 拉起来不等于活着：缺 lark-cli、缺 key 之类会马上退出，等一会儿再看
    for _ in range(30):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        return ""
    try:
        lines = open(ERR_PATH, encoding="utf-8", errors="replace").read().strip().splitlines()
    except OSError:
        lines = []
    if not lines:
        # 订阅失败时 echo_bot 正常退出，错误只在 bot.log 里，挑 lark-cli 的 message / hint 出来
        lines = [re.sub(r'^\[\w+\]\s*"(message|hint)":\s*', "", line).strip().rstrip(",").strip('"')
                 for line in read_log().splitlines() if re.search(r'"(message|hint)":', line)]
    reason = "；".join(lines[-2:]) if lines else f"退出码 {proc.returncode}"
    return f"大豆启动后马上退出了：{reason}"


def read_log() -> str:
    try:
        data = open(LOG_PATH, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    return re.sub(r"\x1b\[[0-9;]*m", "", data)


def log_text() -> str:
    return "\n".join(read_log().splitlines()[-30:])


def running_root(log: str) -> str:
    found = ""
    for line in log.splitlines():
        if line.startswith("[start] project="):
            found = line.split("=", 1)[1].strip()
    return found


def connected(log: str) -> bool:
    start_at = log.rfind("[start] project=")
    chunk = log[start_at:] if start_at >= 0 else log
    return "Connected." in chunk


def last_reply(log: str) -> str:
    found = ""
    for line in log.splitlines():
        if line.startswith("[reply]"):
            found = line
    return found


_branch_cache = {"root": "", "at": 0.0, "branch": "", "dirty": False}
_status_lock = threading.Lock()
_status_cache = {"at": 0.0, "data": None}


def file_branch(root: str) -> tuple:
    if not root or not os.path.isdir(root):
        return "", False
    now = time.time()
    if _branch_cache["root"] == root and now - _branch_cache["at"] < 30:
        return _branch_cache["branch"], _branch_cache["dirty"]
    name = ""
    dirty = False
    try:
        branch = subprocess.run(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
            creationflags=NO_WINDOW,
        )
        if branch.returncode == 0:
            name = branch.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        name = ""
    try:
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
            creationflags=NO_WINDOW,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
    except (OSError, subprocess.TimeoutExpired):
        dirty = False
    _branch_cache["root"] = root
    _branch_cache["at"] = time.time()
    _branch_cache["branch"] = name
    _branch_cache["dirty"] = dirty
    return name, dirty


def index_info() -> dict:
    exists = os.path.isfile(REF_INDEX_PATH)
    rows = 0
    when = ""
    if exists:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(REF_INDEX_PATH)))
        try:
            connection = sqlite3.connect(f"file:{REF_INDEX_PATH}?mode=ro", uri=True)
            rows = connection.execute("select count(*) from refs").fetchone()[0]
            connection.close()
        except sqlite3.Error:
            rows = 0
    return {"exists": exists, "rows": rows, "time": when}


def load_questions() -> list:
    items = []
    try:
        lines = open(QUESTIONS_PATH, encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    for line in lines[-100:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        chat_id = str(row.get("chat_id") or "")
        kind = str(row.get("kind") or "")
        where = kind
        if len(chat_id) >= 6:
            where = (kind + " · …" + chat_id[-6:]).strip(" ·")
        items.append({
            "time": row.get("time") or "",
            "where": where,
            "route": row.get("route") or "",
            "question": row.get("question") or "",
            "answer": (row.get("answer") or "")[:500],
        })
    items.reverse()
    return items


def load_misses() -> list:
    handled = set(read_json(HANDLED_PATH, {}).get("ids") or [])
    items = []
    try:
        lines = open(MISSES_PATH, encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    for line in lines[-80:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("message_id") or row.get("time") or "")
        items.append({
            "id": item_id,
            "time": row.get("time") or "",
            "reason": row.get("reason") or "",
            "route": row.get("route") or "",
            "question": row.get("question") or "",
            "preview": (row.get("preview") or "")[:180],
            "handled": item_id in handled,
        })
    items.reverse()
    return items


def load_reviews() -> list:
    items = read_json(REVIEWS_PATH, {}).get("items") or []
    rows = [item for item in items if isinstance(item, dict)]
    rows.reverse()
    return rows[:100]


def load_notes() -> list:
    items = read_json(NOTES_PATH, {}).get("items") or []
    return [item for item in items if isinstance(item, dict)]


def load_people() -> list:
    data = read_json(PEOPLE_PATH, {})
    rows = []
    if isinstance(data, dict):
        for role, record in data.items():
            name = record.get("name") if isinstance(record, dict) else ""
            if role and name:
                rows.append({"role": role, "name": name})
    return rows


def rebuild_index() -> None:
    with index_lock:
        if index_state["running"]:
            return
        index_state["running"] = True
        index_state["error"] = ""
        index_state["started"] = time.time()

    def work():
        try:
            from bot import echo_bot
            echo_bot.apply_bot_config()
            rows = echo_bot.build_ref_index()
            with index_lock:
                index_state["rows"] = rows
                if rows == 0:
                    index_state["error"] = "没有建成索引。确认 rg 在 PATH 里，并且工程目录下有 Assets。"
        except Exception as error:
            with index_lock:
                index_state["error"] = str(error)[:200]
        finally:
            with index_lock:
                index_state["running"] = False

    threading.Thread(target=work, name="rebuild-index", daemon=True).start()


def _reply(payload, status: int = 200) -> tuple:
    return status, payload


def meta_action(action: str, data: dict | None = None) -> tuple:
    """本地 .meta 改动：scan 只读；apply_all 全部处理并提交（不推送），和拉代码、切分支共用锁。"""
    from bot import echo_bot, meta_fix
    data = data or {}
    root = echo_bot.get_git_root()
    if action == "scan":
        try:
            view = meta_fix.scan(root)
            echo_bot.log(f"[meta] scan {len(view['items'])} 个 meta，{view['seconds']}s {view['timing']}")
            return _reply(dict(view, root=root))
        except RuntimeError as error:
            return _reply({"error": str(error)}, 400)
    if action != "apply_all":
        return _reply({"error": "不认识的操作。"}, 400)
    if not echo_bot._pull_lock.acquire(blocking=False):
        return _reply({"error": "正在拉代码或切分支，等它做完再提交。"}, 400)
    try:
        result = meta_fix.apply_all(root)
    except (ValueError, RuntimeError, OSError) as error:
        return _reply({"error": str(error)}, 400)
    finally:
        echo_bot._pull_lock.release()
    _branch_cache["at"] = 0.0
    with _status_lock:
        _status_cache["at"] = 0.0
        _status_cache["data"] = None
    echo_bot.log(f"[meta] {result.get('commit')} {result.get('message')}")
    return _reply(result)


def admin_get(path: str, user: dict) -> tuple:
    """管理员的读接口，返回 (状态码, JSON)。新后端 /api/v1/admin/* 调它。"""
    if path == "/api/users":
        return _reply({"items": importlib.import_module("common.auth").list_users()})
    if path == "/api/review":
        return _reply(review_view())
    if path == "/api/bomb":
        from bot import echo_bot
        return _reply(echo_bot.bomb_view())
    if path == "/api/wish":
        from boards import wish_board
        return _reply(wish_board.admin_view())
    if path == "/api/models":
        return _reply(models_view())
    if path == "/api/meta":
        return meta_action("scan")
    if path == "/api/bilibili":
        from bot import echo_bot
        view = echo_bot.bilibili_login_status()
        return _reply({key: view.get(key) for key in ("set", "login", "uname", "message")})
    if path == "/api/status":
        now = time.time()
        with _status_lock:
            cached = _status_cache["data"]
            if cached is not None and now - _status_cache["at"] < 15:
                return _reply(cached)
            config = load_config()
            full_log = read_log()
            log = "\n".join(full_log.splitlines()[-30:])
            info = index_info()
            branch, dirty = file_branch(config["project_root"])
            with index_lock:
                rebuilding = index_state["running"]
                index_error = index_state["error"]
            data = {
                "running": bool(bot_processes()),
                "connected": connected(full_log),
                "running_root": running_root(full_log),
                "project_root": config["project_root"],
                "git_branches": config["git_branches"],
                "file_branch": branch,
                "dirty": dirty,
                "last_reply": last_reply(full_log),
                "index_exists": info["exists"],
                "index_rows": info["rows"],
                "index_time": info["time"],
                "index_running": rebuilding,
                "index_error": index_error,
                "log": log,
                "pull": pull_view(),
                "watch": watch_view(),
                "agents": agent_rows(),
                "reminders": reminder_rows(),
            }
            _status_cache["data"] = data
            _status_cache["at"] = time.time()
        return _reply(data)
    if path == "/api/reviews":
        return _reply({"items": load_reviews()})
    if path == "/api/questions":
        return _reply({"items": load_questions()})
    if path == "/api/misses":
        return _reply({"items": load_misses()})
    if path == "/api/notes":
        return _reply({"items": load_notes()})
    if path == "/api/people":
        return _reply({"items": load_people()})
    return _reply({"error": "没有这个页面"}, 404)


def admin_post(path: str, data: dict, user: dict) -> tuple:
    """管理员的写接口，返回 (状态码, JSON)。调用方负责登录、管理员和跨站检查。"""
    try:
        if path == "/api/users":
            from common import auth
            action = str(data.get("action") or "")
            key = str(data.get("open_id") or "")
            try:
                if action in auth.ROLES:
                    auth.set_role(key, action, by=user["open_id"])
                elif action == "remove":
                    auth.remove_user(key, by=user["open_id"])
                else:
                    raise ValueError("不认识的操作。")
            except auth.AuthError as error:
                raise ValueError(str(error))
            return _reply({"ok": True, "items": auth.list_users()})
        if path == "/api/start":
            message = start_bot()
            if message:
                return _reply({"error": message}, 400)
            else:
                return _reply({"ok": True})
        if path == "/api/stop":
            return _reply({"stopped": stop_bot()})
        if path == "/api/sites":
            from boards import board_access
            action = str(data.get("action") or "")
            if action == "private":
                board_access.set_private(str(data.get("port") or ""), bool(data.get("on")))
            elif action == "open":
                board_access.set_hidden(str(data.get("port") or ""), not data.get("on"))
            else:
                raise ValueError("不认识的操作。")
            return _reply(dict(sites_view(), ok=True))
        if path in ("/api/models", "/api/config"):
            return _reply({"error": ENV_ONLY}, 400)
        if path == "/api/wish":
            from boards import wish_board
            result = wish_board.admin_apply(str(data.get("action") or ""), data)
            return _reply(dict(wish_board.admin_view(), ok=result["ok"], message=result["message"]))
        if path == "/api/bomb":
            from bot import echo_bot
            action = str(data.get("action") or "")
            if action == "groups":
                return _reply(echo_bot.control_bomb("groups", groups_text=str(data.get("groups") or "")))
            if action == "ignore_paths":
                return _reply(echo_bot.control_bomb(
                    "ignore_paths", ignore_text=str(data.get("ignore_paths") or "")))
            try:
                minutes = int(data.get("minutes") or 30)
                days = int(data.get("days") or 2)
            except (TypeError, ValueError):
                raise ValueError("间隔和天数要填整数。")
            return _reply(echo_bot.control_bomb(action, minutes, days))
        if path == "/api/review":
            from bot import echo_bot
            action = str(data.get("action") or "")
            if action == "groups":
                result = echo_bot.control_review_groups(str(data.get("groups") or ""))
                return _reply(dict(review_view(), ok=result["ok"], message=result["message"]))
            if action == "run":
                result = echo_bot.request_review_run(str(data.get("date") or ""))
                return _reply(dict(review_view(), ok=result["ok"], message=result["message"]))
            if action == "dm":
                result = echo_bot.control_review_dm(str(data.get("git_author") or ""), bool(data.get("on")))
                return _reply(dict(review_view(), ok=result["ok"], message=result["message"]))
            if action not in ("add", "remove"):
                raise ValueError("不认识的操作。")
            result = echo_bot.control_review_sub(action, str(data.get("name") or ""), str(data.get("git_author") or ""),
                                                 str(data.get("open_id") or ""))
            return _reply(dict(review_view(), ok=result["ok"], message=result["message"]))
        if path == "/api/bilibili":
            from bot import echo_bot
            action = str(data.get("action") or "")
            if action in ("qr", "qr_poll"):
                try:
                    result = echo_bot.bilibili_qr_start() if action == "qr" else echo_bot.bilibili_qr_poll()
                except Exception as error:
                    result = {"ok": False, "state": "error", "message": f"连 B 站失败：{type(error).__name__}"}
                return _reply(result)
            view = echo_bot.control_bilibili_cookie(str(data.get("cookie") or ""))
            return _reply({key: view.get(key) for key in ("set", "login", "uname", "message")})
        if path == "/api/meta":
            return meta_action(str(data.get("action") or ""), data)
        if path == "/api/checkout":
            from bot import echo_bot
            branch = str(data.get("branch") or "").strip()
            if not branch:
                raise ValueError("填要切到的分支名。")
            view = echo_bot.control_checkout(branch)
            with _status_lock:
                _status_cache["at"] = 0.0
                _status_cache["data"] = None
            _branch_cache["at"] = 0.0
            return _reply({"ok": bool(view.get("ok")), "branch": view.get("branch"), "message": view.get("message"), "file_branch": view.get("branch")})
        if path == "/api/pull":
            from bot import echo_bot
            hours = data.get("hours")
            if isinstance(hours, bool) or not isinstance(hours, (int, float)) or (isinstance(hours, float) and not hours.is_integer()):
                raise ValueError("间隔要填整数小时。")
            view = echo_bot.control_pull_schedule(bool(data.get("enabled")), int(hours), bool(data.get("run_now")))
            with _status_lock:
                _status_cache["at"] = 0.0
                _status_cache["data"] = None
            if view.get("run_requested"):
                message = "已交给大豆，马上拉。拉完会私聊朱辉，结果也显示在这里。"
            elif view.get("enabled"):
                message = f"已开启，每 {view.get('hours')} 小时拉一次。下次 {view.get('next_at')}。"
            else:
                message = "已关闭定时。"
            if not bot_processes():
                message += "大豆没在运行，启动后才会拉。"
            return _reply({"ok": True, "pull": view, "message": message})
        if path == "/api/watch":
            from bot import echo_bot
            minutes = data.get("minutes")
            if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or (isinstance(minutes, float) and not minutes.is_integer()):
                raise ValueError("间隔要填整数分钟。")
            days = data.get("days")
            if isinstance(days, bool) or not isinstance(days, (int, float)) or (isinstance(days, float) and not days.is_integer()):
                raise ValueError("天数要填整数。")
            view = echo_bot.control_watch(bool(data.get("enabled")), int(minutes), str(data.get("rules") or ""), bool(data.get("run_now")), int(days))
            with _status_lock:
                _status_cache["at"] = 0.0
                _status_cache["data"] = None
            if view.get("run_requested"):
                message = f"已交给大豆，马上查最近 {view.get('days')} 天。有改动会私聊朱辉，结果也显示在这里。"
            elif view.get("enabled"):
                message = f"已开启，看最近 {view.get('days')} 天，每 {view.get('minutes')} 分钟查一次。下次 {view.get('next_at')}。"
            else:
                message = "已关闭盯目录。"
            if not bot_processes():
                message += "大豆没在运行，启动后才会查。"
            return _reply({"ok": True, "watch": view, "message": message})
        if path == "/api/reminders/delete":
            from bot import echo_bot
            item_id = str(data.get("id") or "")
            if not echo_bot.delete_reminder(item_id):
                return _reply({"error": "这条提醒不存在。"}, 400)
            with _status_lock:
                _status_cache["at"] = 0.0
                _status_cache["data"] = None
            return _reply({"ok": True})
        if path == "/api/preview":
            text = str(data.get("text") or "").strip()
            if not text:
                return _reply({"error": "先写一句要试的话。"}, 400)
            from bot import echo_bot
            echo_bot.apply_bot_config()
            result = echo_bot.preview_question(text)
            return _reply(result)
        if path == "/api/index/rebuild":
            with index_lock:
                if index_state["running"]:
                    return _reply({"error": "正在重建。"}, 400)
            rebuild_index()
            return _reply({"ok": True})
        if path == "/api/misses/handled":
            item_id = str(data.get("id") or "")
            payload = read_json(HANDLED_PATH, {"ids": []})
            ids = [str(item) for item in payload.get("ids") or []]
            if item_id and item_id not in ids:
                ids.append(item_id)
            write_json(HANDLED_PATH, {"ids": ids[-500:]})
            return _reply({"ok": True})
        if path == "/api/notes/delete":
            index = data.get("index")
            items = load_notes()
            if not isinstance(index, int) or not 0 <= index < len(items):
                return _reply({"error": "这条纠正不存在。"}, 400)
            del items[index]
            write_json(NOTES_PATH, {"items": items})
            return _reply({"ok": True})
        if path == "/api/people/delete":
            role = str(data.get("role") or "")
            people = read_json(PEOPLE_PATH, {})
            if not isinstance(people, dict) or role not in people:
                return _reply({"error": "没有这个职务。"}, 400)
            people.pop(role, None)
            write_json(PEOPLE_PATH, people)
            return _reply({"ok": True})
    except ValueError as error:
        return _reply({"error": str(error)}, 400)
    return _reply({"error": "没有这个操作"}, 404)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str, cookies: tuple = ()) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def auth_routes(self, method: str) -> bool:
        """登录相关的几个地址，不用登录就能访问。"""
        from common import auth
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        arg = lambda key: (query.get(key) or [""])[0]
        host = self.headers.get("Host") or f"127.0.0.1:{PORT}"
        if method == "GET" and parsed.path == "/login":
            if self.user:
                self.redirect(auth.safe_next(arg("next")))
                return True
            return False  # 新前端的登录页，由 proxied 转发
        if method == "GET" and parsed.path == "/auth/feishu":
            try:
                url, state = auth.begin_login(host, arg("next"))
            except auth.AuthError as error:
                self.redirect("/login?" + urlencode({"next": arg("next"), "error": str(error)}))
                return True
            self.redirect(url, (auth.cookie_header(auth.STATE_COOKIE, state, auth.STATE_SECONDS),))
            return True
        if method == "GET" and parsed.path == "/auth/feishu/callback":
            clear_state = auth.cookie_header(auth.STATE_COOKIE, "", 0)
            try:
                _user, token, next_path = auth.finish_login(
                    host, arg("code"), arg("state"), auth.read_cookie(self.headers.get("Cookie"), auth.STATE_COOKIE))
            except auth.AuthError as error:
                self.redirect("/login?" + urlencode({"error": str(error)}), (clear_state,))
                return True
            self.redirect(next_path, (clear_state, auth.cookie_header(auth.COOKIE, token, auth.SESSION_SECONDS)))
            return True
        if method == "POST" and parsed.path == "/api/logout":
            auth.end_session(auth.read_cookie(self.headers.get("Cookie"), auth.COOKIE))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", auth.cookie_header(auth.COOKIE, "", 0))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        return False

    def gate(self, method: str) -> bool:
        """先认人：没登录的跳去登录页（接口返回 401）。处理完了返回 True。"""
        from common import auth
        self.user = auth.session_user(auth.read_cookie(self.headers.get("Cookie"), auth.COOKIE))
        self.auth_user = self.user  # 子站 Handler 换类后靠这个知道是谁
        if self.auth_routes(method):
            return True
        if self.user:
            return False
        path = urlparse(self.path).path
        if board_route(path, BOARD_API["public"]):
            return False  # 登录页、前端静态文件、登录页要读的接口，不用登录
        if method == "GET" and "/api/" not in path:
            self.redirect("/login?" + urlencode({"next": self.path}))
        else:
            self.send_json({"error": "请先登录。", "login": "/login"}, 401)
        return True

    def proxied(self, method: str) -> bool:
        """请求原样转给新后端（cookie 一起带过去，新后端自己再认一次人）。"""
        path = urlparse(self.path).path
        if not board_route(path, BOARD_API["routes"]) or board_route(path, BOARD_API["exclude"]):
            return False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
        headers["X-Forwarded-For"] = self.client_address[0]
        headers["Connection"] = "close"
        connection = http.client.HTTPConnection("127.0.0.1", BOARD_API["port"], timeout=180)
        try:
            try:
                connection.request(method, self.path, body=body, headers=headers)
                response = connection.getresponse()
                streaming = response.getheader("Content-Type", "").startswith("text/event-stream")
                payload = b"" if streaming else response.read()
            except OSError:
                self.send_json({"message": "新看板后端没响应，稍后再试。"}, 502)
                return True
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            if streaming:
                # 流式回答（AI 聊天）：不带长度，边收边转，结束时关连接（HTTP/1.0）
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return True
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(payload)
            return True
        finally:
            connection.close()

    def mounted(self, method: str) -> bool:
        """/merge/s/<会话>/ 交给 merge_web 的 Handler（它再交给 MergeReviewPortable）。"""
        path = urlparse(self.path).path
        for prefix, module_name in MOUNTS.items():
            if path != prefix and not path.startswith(prefix + "/"):
                continue
            if path == prefix:
                query = urlparse(self.path).query
                self.send_response(301)
                self.send_header("Location", prefix + "/" + ("?" + query if query else ""))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True
            module = importlib.import_module(module_name)
            handler_class = next(v for v in vars(module).values()
                                 if isinstance(v, type) and issubclass(v, BaseHTTPRequestHandler)
                                 and v is not BaseHTTPRequestHandler and v.__module__ == module_name)
            action = getattr(handler_class, "do_" + method, None)
            if action is None:
                self.send_error(405)
                return True
            self.path = self.path[len(prefix):]
            self.__class__ = handler_class  # 同是 BaseHTTPRequestHandler，换类后直接复用子站逻辑
            action(self)
            return True
        return False

    def closed_board(self) -> bool:
        """管理员关掉的看板，普通用户打开 /merge/s/... 这种不走新后端的页面也拦下（新后端的接口它自己拦）。"""
        from boards import board_access
        path = urlparse(self.path).path
        if not board_route(path, BOARD_API["exclude"]) or not board_access.closed_for(path, self.user):
            return False
        self.send_json({"message": board_access.CLOSED_MESSAGE, "closed": True}, 403)
        return True

    def handle_any(self, method: str) -> None:
        if self.gate(method) or self.closed_board() or self.proxied(method) or self.mounted(method):
            return
        self.send_json({"message": "没有这个页面。"}, 404)

    def do_GET(self) -> None:
        self.handle_any("GET")

    def do_POST(self) -> None:
        self.handle_any("POST")

    def do_DELETE(self) -> None:
        self.handle_any("DELETE")

    def do_PUT(self) -> None:
        self.handle_any("PUT")

    def do_PATCH(self) -> None:
        self.handle_any("PATCH")


class QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def site_online(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def sites_view(user: dict = None) -> dict:
    """子站表。给了 user 时按开关过滤：普通用户看不到管理员关掉的看板，「只看自己的」看板不在名单里也不显示。
    closed 列出对这个人关掉的看板路径，前端拿它在页面上显示「已关闭」。"""
    from bot import echo_bot
    from boards import board_access, review_board
    # review_web_base() 形如 http://10.234.30.83/review（80 端口不带端口号）或 http://10.234.30.83:8765/review，
    # 去掉 /review 就是对外的根地址；不能按冒号切，80 端口时会切成 "http"
    web_root = echo_bot.review_web_base().rsplit("/review", 1)[0]
    local_root = "http://127.0.0.1" + ("" if PORT == 80 else f":{PORT}")
    registry = (
        ("自检复盘", "✓", "#3d5afe", "/review", "每天 0 点复盘名单里每个人前一天的提交，只读，所有人都能看。", "#sec-review"),
        ("心愿墙", "✨", "#ff6f91", "/wish", "大家许愿想要的功能，+1 排优先级，有人接下来实现。", "#sec-wish"),
        ("炸线看板", "⚡", "#e8590c", "/bomb", "疑似炸线和强推的历史：谁的修改丢了、补回没有。只读，保留 90 天。", "#sec-bomb"),
        ("合并确认", "⇄", "#176c4e", "/merge", "merge 冲突逐文件确认选边：上传会话 zip，或在本机检查工程并采集冲突。", ""),
        ("AI 聊天", "✦", "#7c5cff", "/chat", "直接和大豆配好的大模型聊天。对话存在服务器上，每个人只看得到自己的。", "#sec-models"),
        ("监修改动", "📝", "#0b7285", "/docwatch", "版权方监修文档的每次改动：改了什么、AI 分析涉及哪些功能、该谁跟进、通知了谁。只读。", ""),
        ("热心市民", "🙋", "#f03e3e", "/help", "遇到问题不知道找谁：先查常见问题库，再让 AI 分析该找哪个部门的谁；也能按部门翻通讯录。", ""),
    )
    sites = [{"name": name, "icon": icon, "color": color, "port": port, "desc": desc, "manage": manage,
              "url": f"{web_root}{port}/", "local": f"{local_root}{port}/", "online": True}
             for name, icon, color, port, desc, manage in registry]
    private = board_access.load()
    for site in sites:
        if site["port"] in board_access.SUPPORTED:
            site["private"] = private.get(site["port"], False)
            site["private_note"] = board_access.SUPPORTED[site["port"]]
            if site["private"]:
                site["desc"] = site["desc"].replace("所有人都能看", "每个人只看得到自己的")
    closed = board_access.load_hidden()
    for site in sites:
        site["open"] = site["port"] not in closed
    if user is not None and user.get("role") != "admin":
        hidden = {"/review"} if private.get("/review") and not review_board.own_files(user) else set()
        sites = [site for site in sites if site["port"] not in hidden | closed]
        return {"sites": sites, "closed": sorted(closed)}
    return {"sites": sites, "closed": []}


def review_view() -> dict:
    from bot import echo_bot
    return {
        "subs": echo_bot.load_review_subs(),
        "state": echo_bot.load_review_json(echo_bot.REVIEW_STATE_PATH, {}),
        "web": echo_bot.review_web_base(),
        "groups": echo_bot.review_groups(),
    }


def start_board_api() -> None:
    """在后台线程里起新后端（FastAPI，127.0.0.1:8766）。缺依赖或前端没构建就直接退出，说明怎么补。"""
    global BOARD_API
    try:
        import backend.app as board_app
    except ImportError as error:
        raise SystemExit(f"新看板后端起不来，缺依赖（{error}）：python -m pip install -r requirements.txt")
    if not os.path.exists(board_app.INDEX_HTML):
        raise SystemExit("新看板前端没构建（web/dist 不存在）：cd web && npm install && npm run build")
    board_app.start_in_thread()
    BOARD_API = {"port": board_app.PORT, "routes": board_app.PROXY_ROUTES, "exclude": board_app.PROXY_EXCLUDE,
                 "public": board_app.PUBLIC_ROUTES}
    print(f"新看板后端 http://127.0.0.1:{board_app.PORT}（后台、门户、登录和 {'、'.join(board_app.SPA_PREFIXES)} 都走新前端）", flush=True)


def main() -> None:
    migrate_legacy_data(lambda text: print(text, flush=True))
    apply_models_here()
    start_board_api()
    from common import wiki_kb
    if wiki_kb.start_sync_loop(lambda text: print(text, flush=True)):
        print(f"知识库每 {wiki_kb.setting('WIKI_SYNC_MINUTES', '30')} 分钟从 {wiki_kb.setting('WIKI_SOURCE_DIR')} 同步到 wiki/", flush=True)
    server = QuietHTTPServer((HOST, PORT), Handler)
    print(f"大豆后台 http://{HOST}:{PORT}  （复盘 /review/ · 心愿墙 /wish/ · 炸线看板 /bomb/ · 合并确认 /merge/）", flush=True)
    server.serve_forever()


