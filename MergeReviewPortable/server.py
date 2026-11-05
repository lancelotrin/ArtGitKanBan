"""LAN review board. Stores opinions only; never applies Git resolutions."""
import argparse
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
CHOICES = {"ours", "theirs", "both"}


class ReviewServer(ThreadingHTTPServer):
    request_queue_size = 128


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
            if route == "/api/review.md":
                report = Path(db_path).parent / 'review.md'
                if report.is_file():
                    return self.reply(200, report.read_bytes(), "text/markdown; charset=utf-8",
                                      {"Content-Disposition": 'attachment; filename="merge-conflicts.md"'})
                return self.reply(404, {"error": "本会话没有检查文档"})
            if route == "/api/export.csv":
                out = io.StringIO(newline="")
                writer = csv.writer(out)
                writer.writerow(["snapshot", "path", "category", "reviewer", "choice", "comment", "updated_at"])
                votes = self.votes()
                def safe(value):
                    value = str(value)
                    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
                for file in snapshot["files"]:
                    records = [v for v in votes if v["file_id"] == file["id"]] or [{}]
                    for v in records:
                        writer.writerow([safe(x) for x in [snapshot["id"], file["path"], file["category"],
                            v.get("reviewer", ""), v.get("choice", "pending"), v.get("comment", ""), v.get("updated_at", "")]])
                return self.reply(200, out.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="merge-review.csv"'})
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css")}
            if route in assets:
                name, mime = assets[route]
                return self.reply(200, (ROOT / name).read_bytes(), mime + "; charset=utf-8")
            return self.reply(404, {"error": "页面不存在"})

        def do_POST(self):
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
                reviewer, comment = reviewer.strip(), comment.strip()
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
