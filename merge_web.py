"""合并确认：把 MergeReviewPortable 挂到大豆后台 /merge 下。

- /merge/            会话列表：上传别人采集好的会话 zip，或者检查本机某个工程的 merge 冲突并采集
- /merge/s/<会话>/   单个会话的评审页（复用 MergeReviewPortable 的页面和接口）

只读 Git：capture 只读取冲突索引，不改仓库；apply / finish 仍用命令行在仓库那台机器上做。
检查本地工程、增删工程只允许本机访问；上传会话和评审选择所有人都能用。
"""
import html
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.join(BOT_DIR, "MergeReviewPortable")
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)
import merge_review  # noqa: E402
import server as review_server  # noqa: E402

HOST = "0.0.0.0"
PORT = 8773
SESSIONS_DIR = Path(TOOL_DIR) / "sessions"
PROJECTS_PATH = os.path.join(BOT_DIR, "merge_projects.json")
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,80}$")
MAX_UPLOAD = 300 * 1024 * 1024
LOCAL_CLIENTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

_handlers = {}
_lock = threading.Lock()


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


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


# ---------- 页面 ----------

STYLE = """
:root{--ink:#1c2926;--muted:#66736e;--line:#dfe6df;--green:#176c4e;--bg:#f4f6f2;--warn:#986212;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
header{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 4vw;background:#fff;border-bottom:1px solid var(--line)}
.brand{font-weight:850;letter-spacing:2px;color:var(--ink);text-decoration:none}.brand span{font-size:11px;font-weight:500;letter-spacing:1px;margin-left:16px;color:var(--muted)}
main{max-width:1200px;margin:auto;padding:30px 4vw 60px}h1{font-size:28px;margin:0 0 6px}h2{font-size:17px;margin:0 0 12px}
.muted{color:var(--muted)}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 26px}
button,.btn,input{font:inherit;border:1px solid var(--line);border-radius:7px;padding:8px 14px;background:#fff;color:var(--ink);text-decoration:none;cursor:pointer}
button.primary,.btn.primary{background:var(--green);border-color:var(--green);color:#fff}button:disabled{opacity:.5;cursor:wait}
button:hover{filter:brightness(.97)}input{cursor:text}
.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}
.proj{display:flex;gap:12px;align-items:center;padding:12px 0;border-top:1px solid var(--line);flex-wrap:wrap}.proj:first-of-type{border-top:0}
.proj .path{font:12px/1.5 Consolas,Menlo,monospace;flex:1;min-width:220px;overflow-wrap:anywhere}
.tag{font-size:11px;padding:2px 9px;border-radius:20px;background:#f0f3ed;white-space:nowrap}.tag.warn{background:#fff0d8;color:var(--warn)}.tag.good{background:#e3f1e8;color:var(--green)}.tag.bad{background:#fde8e6;color:var(--bad)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 8px;border-top:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600;border-top:0}
td.mono{font:12px/1.5 Consolas,Menlo,monospace;overflow-wrap:anywhere}a{color:var(--green)}
.bar{height:6px;background:#eef2ec;border-radius:4px;overflow:hidden;min-width:80px;margin-top:4px}.bar i{display:block;height:100%;background:var(--green)}
.addrow{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}.addrow input{flex:1;min-width:240px}
#toast{position:fixed;right:20px;bottom:20px;background:#1c2926;color:#fff;padding:10px 16px;border-radius:8px;max-width:420px;white-space:pre-wrap}
#toast.err{background:var(--bad)}.empty{color:var(--muted);padding:12px 0}
@media (max-width:700px){.hide-sm{display:none}}
"""

SCRIPT = r"""
const $ = (id) => document.getElementById(id);
function toast(text, bad){ const t=$('toast'); t.textContent=text; t.className=bad?'err':''; t.hidden=false; clearTimeout(toast.t); toast.t=setTimeout(()=>t.hidden=true, bad?7000:3500); }
async function post(url, body, headers){
  const r = await fetch(url, {method:'POST', headers: headers || {'Content-Type':'application/json'}, body: headers ? body : JSON.stringify(body)});
  let d = {}; try { d = await r.json(); } catch {}
  if (!r.ok || !d.ok) throw Error(d.error || ('服务返回 ' + r.status));
  return d;
}
async function busy(btn, fn){ btn.disabled = true; try { await fn(); } catch(e){ toast(e.message, true); } finally { btn.disabled = false; } }
$('uploadBtn').onclick = () => $('uploadFile').click();
$('uploadFile').onchange = () => {
  const f = $('uploadFile').files[0]; if (!f) return;
  busy($('uploadBtn'), async () => {
    toast('正在上传 ' + f.name + ' …');
    const d = await post('api/upload', f, {'Content-Type': f.name.endsWith('.json') ? 'application/json' : 'application/zip', 'X-Filename': encodeURIComponent(f.name)});
    location.href = 's/' + encodeURIComponent(d.session) + '/';
  });
  $('uploadFile').value = '';
};
document.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-act]'); if (!b) return;
  const repo = b.dataset.repo, act = b.dataset.act;
  if (act === 'check') busy(b, async () => { await post('api/check', {repo}); location.reload(); });
  if (act === 'capture') busy(b, async () => {
    const d = await post('api/capture', {repo, replay_base: ($('replayBase')||{}).value || ''});
    location.href = 's/' + encodeURIComponent(d.session) + '/';
  });
  if (act === 'remove') { if (confirm('从列表移除 ' + repo + '？（不动仓库）')) busy(b, async () => { await post('api/projects', {action:'remove', repo}); location.reload(); }); }
  if (act === 'delete') { if (confirm('删除会话 ' + b.dataset.id + '？里面的评审选择会一起删掉。')) busy(b, async () => { await post('api/delete', {session: b.dataset.id}); location.reload(); }); }
});
const add = $('addBtn');
if (add) add.onclick = () => busy(add, async () => {
  const repo = $('addPath').value.trim(); if (!repo) { toast('先填工程路径', true); return; }
  await post('api/projects', {action:'add', repo}); location.reload();
});
const localCheck = $('checkBtn');
if (localCheck) localCheck.onclick = () => { $('projects').scrollIntoView({behavior:'smooth'}); $('addPath') && $('addPath').focus(); };
"""


def index_page(is_local: bool) -> str:
    sessions = list_sessions()
    if is_local:
        projects = [project_status(repo) for repo in load_projects()]
        rows = []
        for p in projects:
            if not p["ok"]:
                state = f'<span class="tag bad">{esc(p["error"])}</span>'
            elif p["merging"] and p["conflicts"]:
                state = f'<span class="tag warn">合并中 · {esc(p["branch"])} ← {esc(p["incoming"])} · {p["conflicts"]} 个冲突</span>'
            elif p["merging"]:
                state = f'<span class="tag good">合并中 · 冲突已全部解决</span>'
            else:
                state = f'<span class="tag">{esc(p["branch"])} · 没有进行中的 merge</span>'
            can = p["ok"] and p["merging"] and p["conflicts"]
            rows.append(f'<div class="proj"><div class="path">{esc(p["repo"])}</div>{state}'
                        f'<button data-act="check" data-repo="{esc(p["repo"])}">重新检查</button>'
                        f'<button class="primary" data-act="capture" data-repo="{esc(p["repo"])}"{"" if can else " disabled"}>采集冲突并开评审</button>'
                        f'<button data-act="remove" data-repo="{esc(p["repo"])}">移除</button></div>')
        project_block = (
            '<section class="panel" id="projects"><h2>本地工程</h2>'
            '<p class="muted">先在工程里自己 <code>git merge</code>，保留冲突别解决，然后点“采集冲突”。采集只读，不改仓库。</p>'
            + ("".join(rows) or '<div class="empty">还没有工程，在下面加一个。</div>')
            + '<div class="addrow"><input id="addPath" placeholder="工程路径，例如 D:\\OnePiece\\onepiece 或 /Users/me/work/client">'
            '<button id="addBtn">加入列表</button></div>'
            '<div class="addrow"><input id="replayBase" placeholder="（可选）--replay-base 已同步基线提交号；squash/每日同步时才需要"></div>'
            '</section>')
        check_button = '<button id="checkBtn">🔍 检查本地工程 merge</button>'
    else:
        project_block = ""
        check_button = ""
    trs = []
    for s in sessions:
        pct = int(100 * s["decided"] / s["files"]) if s["files"] else 0
        delete = (f'<button data-act="delete" data-id="{esc(s["id"])}">删除</button>' if is_local else "")
        trs.append(f'<tr><td><a href="s/{esc(s["id"])}/"><b>{esc(s["ours"])} ← {esc(s["theirs"])}</b></a>'
                   f'<div class="muted" style="font-size:12px">{esc(s["id"])} · {esc(s["source"])}</div></td>'
                   f'<td class="mono hide-sm">{esc(s["repo"])}</td><td>{esc(s["date"])}</td>'
                   f'<td>{s["decided"]} / {s["files"]}<div class="bar"><i style="width:{pct}%"></i></div></td>'
                   f'<td><a class="btn" href="s/{esc(s["id"])}/">打开</a> {delete}</td></tr>')
    table = ('<table><tr><th>分支</th><th class="hide-sm">仓库</th><th>采集日期</th><th>已选择</th><th></th></tr>'
             + "".join(trs) + "</table>") if trs else '<div class="empty">还没有会话。上传一个，或者检查本地工程采集。</div>'
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>合并确认 · 大豆</title><style>{STYLE}</style></head><body>'
        '<header><a class="brand" href="./">MERGE REVIEW <span>大豆 · 合并确认</span></a></header><main>'
        '<h1>合并确认</h1><p class="muted">每次 merge 冲突一个会话：大家逐个文件选“当前分支 / 合入分支 / 两边都要”，'
        '最后由负责人在仓库那台机器上 <code>merge_review.py plan / apply</code>。</p>'
        '<div class="actions"><button class="primary" id="uploadBtn">⬆ 上传会话</button>'
        f'<input type="file" id="uploadFile" accept=".zip,.json" hidden>{check_button}</div>'
        '<p class="muted" style="margin-top:-14px;font-size:12px">上传：把 sessions 下的会话目录整个打成 zip（或只传 snapshot.json）。</p>'
        f'{project_block}<section class="panel"><h2>会话</h2>{table}</section>'
        f'</main><div id="toast" hidden></div><script>{SCRIPT}</script></body></html>')


class MergeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        return

    def send_body(self, body, code: int = 200, mime: str = "text/html; charset=utf-8") -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: dict, code: int = 200) -> None:
        self.send_body(json.dumps(data, ensure_ascii=False), code, "application/json; charset=utf-8")

    def is_local(self) -> bool:
        return self.client_address[0] in LOCAL_CLIENTS

    def delegate(self, method: str) -> bool:
        """/s/<会话>/... 交给 MergeReviewPortable 的 Handler。"""
        path = urlparse(self.path).path
        match = re.match(r"^/s/([^/]+)(/.*)?$", path)
        if not match:
            return False
        if match.group(2) is None:
            self.send_response(301)
            self.send_header("Location", match.group(1) + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        try:
            handler_class = session_handler(match.group(1))
        except FileNotFoundError:
            self.send_json({"error": "没有这个会话"}, 404)
            return True
        except (ValueError, merge_review.ReviewError, OSError, KeyError) as error:
            self.send_json({"error": f"会话打不开：{error}"}, 400)
            return True
        query = urlparse(self.path).query
        self.path = match.group(2) + ("?" + query if query else "")
        self.__class__ = handler_class
        getattr(handler_class, "do_" + method)(self)
        return True

    def do_GET(self) -> None:
        if self.delegate("GET"):
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.send_body(index_page(self.is_local()))
            return
        if path == "/api/sessions":
            self.send_json({"sessions": list_sessions()})
            return
        self.send_json({"error": "没有这个页面"}, 404)

    def read_raw(self, limit: int) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= limit:
            raise ValueError("请求大小不对" if length else "没有内容")
        return self.rfile.read(length)

    def do_POST(self) -> None:
        if self.delegate("POST"):
            return
        path = urlparse(self.path).path
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self.send_json({"error": "不接受跨站提交"}, 403)
            return
        try:
            if path == "/api/upload":
                raw = self.read_raw(MAX_UPLOAD)
                if raw[:2] == b"PK":
                    session_id = import_zip(raw)
                else:
                    session_id = import_snapshot(raw)
                self.send_json({"ok": True, "session": session_id})
                return
            if path not in ("/api/check", "/api/capture", "/api/projects", "/api/delete"):
                self.send_json({"error": "没有这个接口"}, 404)
                return
            if not self.is_local():
                self.send_json({"error": "只能在大豆所在的机器上操作"}, 403)
                return
            data = json.loads(self.read_raw(65536).decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("请求格式不对")
            repo = str(data.get("repo") or "").strip()
            if path == "/api/check":
                self.send_json({"ok": True, "status": project_status(repo)})
            elif path == "/api/capture":
                if repo not in load_projects():
                    raise ValueError("先把工程加入列表")
                session_id = capture_project(repo, str(data.get("replay_base") or "").strip())
                self.send_json({"ok": True, "session": session_id})
            elif path == "/api/projects":
                projects = load_projects()
                if data.get("action") == "add":
                    repo = os.path.abspath(os.path.expanduser(repo))
                    if not os.path.isdir(repo):
                        raise ValueError(f"目录不存在：{repo}")
                    if repo not in projects:
                        projects.append(repo)
                elif data.get("action") == "remove":
                    projects = [p for p in projects if p != repo]
                else:
                    raise ValueError("不认识的操作")
                save_projects(projects)
                self.send_json({"ok": True, "projects": projects})
            elif path == "/api/delete":
                target = session_dir(str(data.get("session") or ""))
                with _lock:
                    _handlers.pop(str(target), None)
                shutil.rmtree(target)
                self.send_json({"ok": True})
        except FileNotFoundError:
            self.send_json({"error": "没有这个会话"}, 404)
        except merge_review.ReviewError as error:
            self.send_json({"error": str(error)}, 400)
        except (ValueError, UnicodeError, KeyError, OSError, subprocess.SubprocessError) as error:
            self.send_json({"error": str(error)}, 400)


def serve() -> None:
    print(f"合并确认 http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), MergeHandler).serve_forever()


if __name__ == "__main__":
    serve()
