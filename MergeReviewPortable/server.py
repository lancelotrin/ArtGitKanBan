"""LAN review board. Stores opinions only; never applies Git resolutions."""
import argparse
import csv
import io
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
CHOICES = {"ours", "theirs", "both"}
# 导出 CSV 的第一行（单个单元格，以 # 开头；merge_review.py plan 读 CSV 时会跳过）。
CSV_GUIDE = ("# 合并冲突处理说明（第二行起是表头和数据，每行一个冲突文件）：当前分支提交 {ours}，合入分支提交 {theirs}；"
             "ours=用当前分支版本：内容取 git show {ours}:<path>（一般等同 git checkout --ours -- <path>；涉及重命名时以同路径对象为准；base 里没有该文件则 git rm <path>），合入分支对它的修改全部丢弃；"
             "theirs=用合入分支版本：内容取 git show {theirs}:<path>（一般等同 git checkout --theirs -- <path>；合入分支没有该文件则 git rm <path>），当前分支对它的修改全部丢弃；"
             "both=以 base（当前分支）版本为基底，按顺序 git show <replay_commits 里的提交> -- <path> 查看合入分支的修改，"
             "只把有效修改重放到基底上：已包含的不重复，当前分支有意删除或重构的不复活，重叠内容/业务歧义列出差异问人；"
             "二进制或生成物不能拼接，需理解修改后人工整合或重新生成；replay_commits 为空时看 git diff <replay_from> <replay_to> -- <path>；"
             "pending=未选择，按 both 处理（合入分支无同路径历史的已在导出时写成 ours）；"
             "每个文件处理完确认无冲突标记后 git add <path>；comment 列是评审备注，处理时要遵守；只处理本 CSV 列出的路径，不提交、不推送。")


class ReviewServer(ThreadingHTTPServer):
    request_queue_size = 128



def login_name(handler) -> str:
    """挂在大豆后台下时由后台认过人（auth_user），以登录名为准；单独运行时返回空。"""
    user = getattr(handler, "auth_user", None) or {}
    return str(user.get("name") or "")[:60]

PREFS_LOCK = threading.Lock()


def prefs_key(handler) -> str:
    """个人设置按谁存：大豆后台登录了按登录名，单独运行时按访问机器的 IP。"""
    login = login_name(handler)
    return ("login:" + login) if login else ("ip:" + str(handler.client_address[0]))


def read_prefs(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_prefs(path, key, values) -> dict:
    """姓名 / 搜索词存在会话根目录的共享文件里，所有会话读同一份，改了全部会话同步。"""
    with PREFS_LOCK:
        data = read_prefs(path)
        entry = dict(data.get(key) or {})
        entry.update(values)
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        data[key] = entry
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return entry


def database(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database(path):
    conn = database(path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS decisions (
                snapshot TEXT, file_id TEXT, reviewer TEXT, choice TEXT, comment TEXT,
                updated_at TEXT, PRIMARY KEY(snapshot, file_id))""")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='votes'").fetchone():
                # Keep only the latest person's choice when upgrading the original board.
                conn.execute("""INSERT OR REPLACE INTO decisions
                    SELECT snapshot,file_id,reviewer,choice,comment,updated_at FROM votes
                    ORDER BY updated_at, rowid""")
                conn.execute("DROP TABLE votes")
            conn.execute("DROP TABLE IF EXISTS history")
    finally:
        conn.close()


def make_handler(snapshot, db_path):
    ids = {f["id"] for f in snapshot["files"]}
    prefs_path = Path(db_path).parent.parent / "reviewer-prefs.json"  # 会话根目录，所有会话共用

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body, mime="application/json; charset=utf-8", headers=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def votes(self):
            conn = database(db_path)
            try:
                return [dict(r) for r in conn.execute(
                    "SELECT file_id,reviewer,choice,comment,updated_at FROM decisions WHERE snapshot=? ORDER BY updated_at",
                    (snapshot["id"],))]
            finally:
                conn.close()

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/api/state":
                return self.reply(200, {"snapshot": snapshot, "votes": self.votes()})
            if route == "/api/votes":
                return self.reply(200, {"snapshot_id": snapshot["id"], "votes": self.votes()})
            if route == "/api/prefs":
                entry = read_prefs(prefs_path).get(prefs_key(self)) or {}
                return self.reply(200, {"name": login_name(self) or entry.get("name", ""),
                                        "search": entry.get("search", ""), "login": bool(login_name(self))})
            if route == "/api/review.md":
                report = Path(db_path).parent / 'review.md'
                if report.is_file():
                    return self.reply(200, report.read_bytes(), "text/markdown; charset=utf-8",
                                      {"Content-Disposition": 'attachment; filename="merge-conflicts.md"'})
                return self.reply(404, {"error": "本会话没有检查文档"})
            if route == "/api/export.csv":
                out = io.StringIO(newline="")
                writer = csv.writer(out)
                writer.writerow([CSV_GUIDE.format(ours=snapshot.get("ours") or "HEAD", theirs=snapshot.get("theirs") or "MERGE_HEAD")])  # 第一行：各选项怎么处理，新会话拿到 CSV 就知道怎么解冲突
                # both / pending（默认 both）只留新 AI 会话重放要用的：以 base（当前分支）为基底，
                # 重放 replay_from..replay_to 之间触及该路径的 replay_commits；分类、修改人、时间不导出。
                writer.writerow(["snapshot", "path", "choice", "category", "reviewer", "comment", "updated_at",
                                 "base", "replay_from", "replay_to", "replay_commits"])
                votes = self.votes()
                def safe(value):
                    value = str(value or "")
                    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
                for file in snapshot["files"]:
                    records = [v for v in votes if v["file_id"] == file["id"]] or [{}]
                    for v in records:
                        choice = v.get("choice", "pending")
                        if choice == "pending" and not file.get("theirs"):
                            choice = "ours"  # 合入分支无同路径历史：默认用当前分支
                        if choice in ("both", "pending"):
                            stage1 = ((file.get("stages") or {}).get("1") or {}).get("oid")
                            replay_from = file.get("replay_from") or snapshot.get("replay_base") or (f"stage1:{stage1}" if stage1 else "")
                            commits = file.get("replay_commits")
                            row = [snapshot["id"], file["path"], choice, "", "", v.get("comment", ""), "",
                                   snapshot.get("ours", ""), replay_from, snapshot.get("theirs", ""),
                                   " ".join(commits) if commits is not None else ""]
                        else:
                            row = [snapshot["id"], file["path"], choice, file.get("category", ""), v.get("reviewer", ""),
                                   v.get("comment", ""), v.get("updated_at", ""), "", "", "", ""]
                        writer.writerow([safe(x) for x in row])
                return self.reply(200, out.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="merge-review.csv"'})
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css")}
            if route in assets:
                name, mime = assets[route]
                raw = (ROOT / name).read_bytes()
                login = login_name(self)
                if route == "/" and login:  # 挂在大豆后台下已登录：姓名直接用飞书名
                    seed = "<script>try{localStorage.setItem('portable-merge-review-name'," + json.dumps(login) + ")}catch(e){}</script>"
                    raw = raw.replace(b"<body>", ("<body>" + seed).encode("utf-8"), 1)
                return self.reply(200, raw, mime + "; charset=utf-8")
            return self.reply(404, {"error": "页面不存在"})

        def do_POST(self):
            if urlsplit(self.path).path == "/api/prefs":
                return self.save_prefs()
            if urlsplit(self.path).path != "/api/vote":
                return self.reply(404, {"error": "接口不存在"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("请求大小无效")
                raw_body = self.rfile.read(length)
            except ValueError as exc:
                return self.reply(400, {"error": str(exc)})
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.reply(403, {"error": "不接受跨站提交"})
            if self.headers.get_content_type() != "application/json":
                return self.reply(415, {"error": "需要 JSON 请求"})
            try:
                body = json.loads(raw_body)
                if not isinstance(body, dict):
                    raise ValueError("请求格式无效")
                if body.get("snapshot") != snapshot["id"]:
                    return self.reply(409, {"error": "页面快照已变化，请刷新后选择"})
                file_id, choice = body.get("file_id"), body.get("choice")
                reviewer, comment = body.get("reviewer", ""), body.get("comment", "")
                if not isinstance(reviewer, str) or not isinstance(comment, str):
                    raise ValueError("姓名和备注必须是文本")
                reviewer, comment = (login_name(self) or reviewer).strip(), comment.strip()
                if file_id not in ids or choice not in CHOICES | {"clear"}:
                    raise ValueError("文件或选项无效")
                if not reviewer or len(reviewer) > 60 or len(comment) > 2000:
                    raise ValueError("请填写姓名（最多60字），备注最多2000字")
            except (ValueError, TypeError, UnicodeError) as exc:
                return self.reply(400, {"error": str(exc)})
            conn = database(db_path)
            try:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    stamp = datetime.now(timezone.utc).isoformat()
                    if choice == "clear":
                        conn.execute("DELETE FROM decisions WHERE snapshot=? AND file_id=?",
                                     (snapshot["id"], file_id))
                    else:
                        conn.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?)",
                                     (snapshot["id"], file_id, reviewer, choice, comment, stamp))
            except sqlite3.Error:
                return self.reply(503, {"error": "保存暂时失败，请重试；原记录未被覆盖"})
            finally:
                conn.close()
            self.reply(200, {"ok": True})

        def save_prefs(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("请求大小无效")
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    return self.reply(403, {"error": "不接受跨站提交"})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("请求格式无效")
                values = {}
                for field, limit in (("name", 60), ("search", 200)):
                    if field in body:
                        value = body[field]
                        if not isinstance(value, str) or len(value.strip()) > limit:
                            raise ValueError(f"{field} 必须是不超过 {limit} 字的文本")
                        values[field] = value.strip()
                if not values:
                    raise ValueError("没有要保存的内容")
            except (ValueError, TypeError, UnicodeError) as exc:
                return self.reply(400, {"error": str(exc)})
            try:
                entry = write_prefs(prefs_path, prefs_key(self), values)
            except OSError:
                return self.reply(503, {"error": "保存个人设置失败，请重试"})
            self.reply(200, {"ok": True, "name": entry.get("name", ""), "search": entry.get("search", "")})

    return Handler


def serve_session(session, snapshot, host="0.0.0.0", port=8767):
    db_path = Path(session) / "reviews.sqlite3"
    initialize_database(db_path)
    server = ReviewServer((host, port), make_handler(snapshot, db_path))
    print(f"Merge review: http://{host}:{port} | session: {session}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    from merge_review import main
    raise SystemExit(main(["serve", *(__import__('sys').argv[1:])]))
