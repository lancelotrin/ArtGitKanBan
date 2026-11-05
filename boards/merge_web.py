"""合并确认：会话和本地工程的逻辑，以及 /merge/s/<会话>/ 评审页的转发。

- 会话列表、上传、本地工程（检查 / 采集 / 增删）：逻辑在这里，接口在 backend/routers/merge.py，页面在 web/。
- /merge/s/<会话>/：单个会话的评审页是 MergeReviewPortable 自带的页面和接口，console/server.py 把请求交给 MergeHandler。

只读 Git：capture 只读取冲突索引，不改仓库；apply / finish 仍用命令行在仓库那台机器上做。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from common.paths import DATA_DIR, ROOT_DIR

TOOL_DIR = os.path.join(ROOT_DIR, "MergeReviewPortable")
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)
import merge_review  # noqa: E402
import server as review_server  # noqa: E402

SESSIONS_DIR = Path(TOOL_DIR) / "sessions"
PROJECTS_PATH = os.path.join(DATA_DIR, "merge_projects.json")
OPEN_PATH = os.path.join(DATA_DIR, "merge_open.json")  # 管理员开放给普通用户的会话；没列进来的普通用户看不到
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,80}$")
MAX_UPLOAD = 300 * 1024 * 1024

_handlers = {}
_lock = threading.Lock()


# ---------- 工程列表 ----------

def load_projects() -> list:
    try:
        with open(PROJECTS_PATH, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []


def save_projects(projects: list) -> None:
    tmp = PROJECTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(projects, stream, ensure_ascii=False, indent=2)
    os.replace(tmp, PROJECTS_PATH)


# ---------- 开放给普通用户的会话 ----------

def load_open() -> list:
    try:
        with open(OPEN_PATH, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return []
    return [s for s in data if isinstance(s, str)] if isinstance(data, list) else []


def save_open(session_ids: list) -> None:
    tmp = OPEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(session_ids, stream, ensure_ascii=False, indent=2)
    os.replace(tmp, OPEN_PATH)


def set_open(session_id: str, is_open: bool) -> list:
    session_dir(session_id)  # 会话得存在
    ids = [s for s in load_open() if s != session_id]
    if is_open:
        ids.append(session_id)
    save_open(ids)
    return ids


def can_view(user, session_id: str) -> bool:
    """管理员看全部；普通用户只能看管理员开放的会话。"""
    if not user:
        return False
    return user.get("role") == "admin" or session_id in load_open()


def git(repo: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=30, text=True, encoding="utf-8", errors="replace")


def project_status(repo: str) -> dict:
    """看一眼工程现在是不是在 merge 冲突阶段，不改任何东西。"""
    info = {"repo": repo, "ok": False, "merging": False, "conflicts": 0, "branch": "", "incoming": "", "error": ""}
    if not os.path.isdir(repo):
        info["error"] = "目录不存在"
        return info
    try:
        top = git(repo, "rev-parse", "--show-toplevel")
        if top.returncode:
            info["error"] = "不是 Git 仓库"
            return info
        info["ok"] = True
        info["branch"] = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() or "（游离 HEAD）"
        merge_head = git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").stdout.strip()
        if merge_head:
            info["merging"] = True
            refs = git(repo, "for-each-ref", "--format=%(refname:short)", "--points-at", merge_head,
                       "refs/heads", "refs/remotes").stdout.split()
            info["incoming"] = refs[0] if refs else merge_head[:11]
        unmerged = git(repo, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
        info["conflicts"] = len([line for line in unmerged if line.strip()])
    except (OSError, subprocess.TimeoutExpired) as error:
        info["error"] = str(error)
    return info


# ---------- 会话 ----------

def session_dir(session_id: str) -> Path:
    if not SESSION_ID.match(session_id or ""):
        raise ValueError("会话名不对")
    path = SESSIONS_DIR / session_id
    if not (path / "snapshot.json").is_file():
        raise FileNotFoundError(session_id)
    return path


def list_sessions() -> list:
    rows = []
    if not SESSIONS_DIR.is_dir():
        return rows
    for path in SESSIONS_DIR.iterdir():
        snap_path = path / "snapshot.json"
        if not snap_path.is_file() or not SESSION_ID.match(path.name):
            continue
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        decided = 0
        db = path / "reviews.sqlite3"
        if db.is_file():
            try:
                conn = review_server.database(db)
                try:
                    decided = conn.execute("SELECT COUNT(*) FROM decisions WHERE snapshot=?", (snap.get("id"),)).fetchone()[0]
                finally:
                    conn.close()
            except Exception:  # noqa: BLE001 老库或者坏库都只影响计数
                decided = 0
        source = "上传" if (path / "uploaded.json").is_file() else "本机采集"
        rows.append({"id": path.name, "repo": snap.get("repo", ""), "date": snap.get("date", ""),
                     "ours": snap.get("ours_label", ""), "theirs": snap.get("theirs_label", ""),
                     "files": len(snap.get("files") or []), "decided": decided, "source": source,
                     "mtime": snap_path.stat().st_mtime})
    rows.sort(key=lambda row: row["mtime"], reverse=True)
    return rows


def session_handler(session_id: str):
    """每个会话一个评审 Handler 类，缓存起来。快照被改过会在 load_session 里报错。"""
    path = session_dir(session_id)
    key = str(path)
    with _lock:
        if key not in _handlers:
            session, snapshot, _ = merge_review.load_session(path)
            db_path = session / "reviews.sqlite3"
            review_server.initialize_database(db_path)
            _handlers[key] = review_server.make_handler(snapshot, db_path)
        return _handlers[key]


def capture_project(repo: str, replay_base: str = "") -> str:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session = merge_review.capture(repo, SESSIONS_DIR, replay_base=replay_base or None)
    return session.name


def import_zip(data: bytes) -> str:
    """接收会话目录打的 zip（里面有 snapshot.json），解压成一个新会话。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("不是有效的 zip")
    names = [info for info in archive.infolist() if not info.is_dir()]
    snaps = [info.filename for info in names if info.filename.replace("\\", "/").split("/")[-1] == "snapshot.json"]
    if len(snaps) != 1:
        raise ValueError("zip 里要正好有一个 snapshot.json（把整个会话目录打包上传）")
    prefix = snaps[0].replace("\\", "/")[:-len("snapshot.json")]
    if sum(info.file_size for info in names) > MAX_UPLOAD * 3:
        raise ValueError("解压后太大了")
    snapshot = json.loads(archive.read(snaps[0]).decode("utf-8"))
    return _store_session(snapshot, {info.filename.replace("\\", "/")[len(prefix):]: info for info in names
                                     if info.filename.replace("\\", "/").startswith(prefix)}, archive)


def import_snapshot(data: bytes) -> str:
    try:
        snapshot = json.loads(data.decode("utf-8-sig"))
    except ValueError:
        raise ValueError("只收会话 zip 或 snapshot.json")
    return _store_session(snapshot, {}, None, raw_snapshot=data)


def _store_session(snapshot: dict, members: dict, archive, raw_snapshot: bytes = b"") -> str:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), list) or not snapshot.get("id"):
        raise ValueError("snapshot.json 格式不对，不是 MergeReviewPortable 的会话")
    session_id = str(snapshot["id"])
    if not SESSION_ID.match(session_id):
        raise ValueError("会话 id 不合法")
    target = SESSIONS_DIR / session_id
    if target.exists():
        raise ValueError(f"会话 {session_id} 已经在了，直接在列表里打开")
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    temp = SESSIONS_DIR / f".upload-{uuid.uuid4().hex[:8]}"
    temp.mkdir()
    try:
        # 只要评审需要的东西：快照、校验、检查文档、已有选择、blobs；别的（日志、备份）不收
        allowed = re.compile(r"^(snapshot\.json|capture-manifest\.json|review\.md|reviews\.sqlite3|blobs/[0-9a-f]{40,64})$")
        for rel, info in members.items():
            if not allowed.match(rel):
                continue
            out = temp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        if raw_snapshot:
            (temp / "snapshot.json").write_bytes(raw_snapshot)
        if not (temp / "capture-manifest.json").is_file():
            merge_review.dump(temp / "capture-manifest.json",
                              {"snapshot_sha256": merge_review.sha((temp / "snapshot.json").read_bytes())})
        merge_review.load_session(temp)  # 校验快照没被改过
        if not (temp / "review.md").is_file():
            merge_review.write_review_document(temp, snapshot)
        merge_review.dump(temp / "uploaded.json", {"uploaded_at": datetime.now().astimezone().isoformat()})
        temp.rename(target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return session_id


# ---------- /merge/s/<会话>/ 转发 ----------

class MergeHandler(BaseHTTPRequestHandler):
    """console/server.py 换类进来时 self.path 已去掉 /merge 前缀，只处理 /s/<会话>/...。"""

    def log_message(self, fmt, *args) -> None:
        return

    def send_json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def delegate(self, method: str) -> None:
        path = urlparse(self.path).path
        match = re.match(r"^/s/([^/]+)(/.*)?$", path)
        if not match:
            self.send_json({"message": "没有这个页面。"}, 404)
            return
        if match.group(2) is None:
            self.send_response(301)
            self.send_header("Location", match.group(1) + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not can_view(getattr(self, "auth_user", None), match.group(1)):
            self.send_json({"message": "这个会话管理员没开放给你。"}, 403)
            return
        try:
            handler_class = session_handler(match.group(1))
        except FileNotFoundError:
            self.send_json({"message": "没有这个会话"}, 404)
            return
        except (ValueError, merge_review.ReviewError, OSError, KeyError) as error:
            self.send_json({"message": f"会话打不开：{error}"}, 400)
            return
        action = getattr(handler_class, "do_" + method, None)
        if action is None:
            self.send_error(405)
            return
        query = urlparse(self.path).query
        self.path = match.group(2) + ("?" + query if query else "")
        self.__class__ = handler_class
        action(self)

    def do_GET(self) -> None:
        self.delegate("GET")

    def do_POST(self) -> None:
        self.delegate("POST")
