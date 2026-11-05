"""自检复盘网页：内网公开、只读。只读 reviews/ 下的 json，不碰 git、不调模型、没有任何写操作。"""
import datetime
import html
import json
import os
import re
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
REVIEW_DIR = os.path.join(BOT_DIR, "reviews")
HOST = "0.0.0.0"
PORT = 8770
# 挂在大豆后台下时的路径前缀（console.py 设成 /review）
BASE = os.environ.get("REVIEW_BASE", "/review")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{1,40}$")
FEISHU_LINK = re.compile(r"^https://project\.feishu\.cn/\w+/(story|issue)/detail/\d+$")
LABEL_LINE = re.compile(r"^(改动|影响|提升|隐患)[：:]\s*")
LABEL_CLASS = {"改动": "l-change", "影响": "l-impact", "提升": "l-gain", "隐患": "l-risk"}
WEEKDAYS = "一二三四五六日"
AVATAR_COLORS = ("#4f6bed", "#0ea5a4", "#e8794a", "#8b5cf6", "#d9467a", "#2f9e5b", "#c48a12", "#3b82c4")
FILTERS = (("all", "全部"), ("risk", "需要留意"), ("bomb", "疑似炸线"), ("clean", "没看出问题"), ("empty", "无提交"))

STYLE = """
:root { --bg:#f4f6fb; --card:#fff; --line:#e6e9f2; --text:#1b2333; --sub:#6b7386; --brand:#3d5afe; --brand-soft:#eef1ff;
  --ok:#14804a; --ok-soft:#e7f6ee; --warn:#b4540b; --warn-soft:#fff3e6; --bad:#c0262d; --bad-soft:#fdecec; --mute-soft:#f0f2f6; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.6 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
a { color:var(--brand); text-decoration:none; } a:hover { text-decoration:underline; }
header { background:linear-gradient(120deg,#141b34,#25307a 60%,#3d5afe); color:#fff; }
.hwrap { max-width:1180px; margin:0 auto; padding:22px 24px 64px; }
.brand { display:flex; align-items:center; gap:10px; font-size:13px; opacity:.85; color:#fff; }
.brand:hover { text-decoration:none; opacity:1; }
.logo { width:26px; height:26px; border-radius:8px; background:rgba(255,255,255,.18); display:grid; place-items:center; font-weight:700; }
h1 { margin:14px 0 4px; font-size:26px; font-weight:650; letter-spacing:.5px; }
.hsub { opacity:.75; font-size:13px; }
main { max-width:1180px; margin:-44px auto 40px; padding:0 24px; }
.panel { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:18px 20px; box-shadow:0 6px 24px rgba(20,27,52,.06); margin-bottom:18px; }
.toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
.spacer { flex:1; }
.btn { display:inline-grid; place-items:center; height:36px; min-width:36px; padding:0 12px; border:1px solid var(--line); border-radius:10px;
  background:#fff; color:var(--text); font-size:13px; }
.btn:hover { border-color:var(--brand); color:var(--brand); text-decoration:none; }
.btn.off { opacity:.35; pointer-events:none; }
input[type=date], input[type=search] { height:36px; border:1px solid var(--line); border-radius:10px; padding:0 12px; font:inherit; color:var(--text); background:#fff; }
input[type=search] { width:220px; }
input:focus { outline:none; border-color:var(--brand); box-shadow:0 0 0 3px var(--brand-soft); }
.weekday { color:var(--sub); font-size:13px; }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { height:32px; padding:0 12px; border-radius:999px; border:1px solid var(--line); background:#fff; color:var(--sub); font:inherit; font-size:13px; cursor:pointer; }
.chip.on { background:var(--text); border-color:var(--text); color:#fff; }
.chip b { font-weight:600; margin-left:4px; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-top:16px; }
.stat { border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
.stat .k { color:var(--sub); font-size:12px; } .stat .v { font-size:24px; font-weight:650; margin-top:2px; }
.stat.warn .v { color:var(--warn); } .stat.bad .v { color:var(--bad); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(270px,1fr)); gap:14px; }
.pcard { display:flex; flex-direction:column; gap:10px; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px;
  color:var(--text); transition:transform .15s, box-shadow .15s, border-color .15s; }
.pcard:hover { text-decoration:none; transform:translateY(-2px); box-shadow:0 10px 26px rgba(20,27,52,.10); border-color:#cfd5ea; }
.pcard.is-risk { border-top:3px solid #f0a14a; } .pcard.is-bomb { border-top:3px solid var(--bad); }
.ptop { display:flex; align-items:center; gap:10px; }
.avatar { width:38px; height:38px; border-radius:12px; color:#fff; display:grid; place-items:center; font-weight:650; flex:none; }
.avatar.big { width:52px; height:52px; border-radius:16px; font-size:20px; }
.pname { font-weight:650; font-size:15px; } .pauthor { color:var(--sub); font-size:12px; }
.ptop .pill { margin-left:auto; }
.pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; white-space:nowrap; }
.p-clean { background:var(--ok-soft); color:var(--ok); } .p-risk { background:var(--warn-soft); color:var(--warn); }
.p-bomb { background:var(--bad-soft); color:var(--bad); } .p-empty { background:var(--mute-soft); color:var(--sub); }
.p-failed { background:var(--bad-soft); color:var(--bad); }
.psum { color:#3a4357; font-size:13px; min-height:42px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.pfoot { display:flex; gap:12px; color:var(--sub); font-size:12px; border-top:1px dashed var(--line); padding-top:8px; }
.pfoot .w { color:var(--warn); } .pfoot .b { color:var(--bad); }
.none { text-align:center; color:var(--sub); padding:40px 0; }
.crumb { font-size:13px; color:rgba(255,255,255,.75); margin-top:14px; } .crumb a { color:#fff; }
.hero { display:flex; align-items:center; gap:14px; margin-top:14px; }
.hero h1 { margin:0; } .hero .pill { margin-left:6px; vertical-align:middle; }
section.panel h2 { font-size:15px; margin:0 0 12px; display:flex; align-items:center; gap:8px; }
section.panel h2 .n { color:var(--sub); font-weight:400; font-size:13px; }
.clist { list-style:none; margin:0; padding:0; }
.clist > li { padding:12px 0; border-bottom:1px solid var(--line); }
.clist > li:last-child { border-bottom:none; }
.cmain { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.hash { font-family:Consolas,monospace; font-size:12px; background:var(--mute-soft); padding:1px 7px; border-radius:6px; color:#384056; }
.br { font-size:11px; padding:1px 7px; border-radius:6px; background:var(--brand-soft); color:var(--brand); }
.time { color:var(--sub); font-size:12px; white-space:nowrap; }
.diffbox { margin-top:10px; border:1px solid var(--line); border-radius:10px; overflow:hidden; background:#fbfcfe; }
.diffbox > summary { cursor:pointer; list-style:none; padding:8px 12px; color:var(--sub); font-size:12px; user-select:none; }
.diffbox > summary::-webkit-details-marker { display:none; }
.diffbox > summary::before { content:"▸ "; color:var(--brand); }
.diffbox[open] > summary::before { content:"▾ "; }
.diffbox > summary:hover { color:var(--text); background:#f3f5fb; }
.diffpre { margin:0; padding:10px 12px 12px; overflow:auto; max-height:420px; font:12px/1.55 Consolas,"Sarasa Mono SC",monospace;
  background:#0f172a; color:#e2e8f0; border-top:1px solid var(--line); white-space:pre; }
.diffpre .d-add { color:#86efac; background:rgba(34,197,94,.12); display:block; }
.diffpre .d-del { color:#fca5a5; background:rgba(239,68,68,.12); display:block; }
.diffpre .d-hunk { color:#93c5fd; display:block; }
.diffpre .d-meta { color:#94a3b8; display:block; }
.diffnote { color:var(--sub); font-size:12px; margin-top:8px; }
.md .ln { margin:2px 0; } .md .gap { height:10px; }
.md b { font-weight:650; }
.lbl { display:inline-block; font-size:11px; padding:0 7px; border-radius:6px; margin-right:6px; vertical-align:1px; }
.l-change { background:var(--brand-soft); color:var(--brand); } .l-impact { background:var(--mute-soft); color:#4a5368; }
.l-gain { background:var(--ok-soft); color:var(--ok); } .l-risk { background:var(--warn-soft); color:var(--warn); }
code { font-family:Consolas,monospace; background:var(--mute-soft); padding:1px 5px; border-radius:5px; font-size:12.5px; }
.ext { font-size:12px; padding:0 6px; border:1px solid var(--line); border-radius:6px; color:var(--sub); }
.ext:hover { color:var(--brand); border-color:var(--brand); text-decoration:none; }
section.alert { border-left:4px solid var(--bad); }
table { border-collapse:collapse; width:100%; }
td, th { border-bottom:1px solid var(--line); padding:10px 8px; text-align:left; } th { color:var(--sub); font-weight:500; font-size:12px; }
tr:hover td { background:#fafbfe; }
@media (max-width:720px) { .stats { grid-template-columns:repeat(2,1fr); } input[type=search] { width:100%; } }
"""

INDEX_SCRIPT = """
(function(){
  var q=document.getElementById('q'), chips=document.querySelectorAll('.chip'), cards=document.querySelectorAll('.pcard'),
      none=document.getElementById('nomatch'), filter='all';
  function apply(){
    var key=(q.value||'').trim().toLowerCase(), shown=0;
    cards.forEach(function(c){
      var ok=(filter==='all'||(' '+c.dataset.tags+' ').indexOf(' '+filter+' ')>=0)&&c.dataset.key.indexOf(key)>=0;
      c.style.display=ok?'':'none'; if(ok){shown++;}
    });
    none.style.display=shown?'none':'';
  }
  chips.forEach(function(chip){ chip.addEventListener('click',function(){
    chips.forEach(function(x){x.classList.remove('on');}); chip.classList.add('on'); filter=chip.dataset.f; apply(); }); });
  q.addEventListener('input',apply);
})();
"""


def esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def weekday(date: str) -> str:
    try:
        return "周" + WEEKDAYS[datetime.date.fromisoformat(date).weekday()]
    except ValueError:
        return ""


def avatar(name: str, big: bool = False) -> str:
    color = AVATAR_COLORS[zlib.crc32(name.encode("utf-8")) % len(AVATAR_COLORS)]
    return f'<div class="avatar{" big" if big else ""}" style="background:{color}">{esc(name[:1])}</div>'


def link_html(match) -> str:
    url = match.group(1)
    if FEISHU_LINK.match(url):
        text = "缺陷单 ↗" if "/issue/" in url else "需求单 ↗"
        return f'<a class="ext" href="{url}" target="_blank" rel="noopener">{text}</a>'
    return f'<a href="{url}" target="_blank" rel="noopener">{url}</a>'


def render_inline(text: str) -> str:
    """只支持报告里用到的：**粗体**、`代码`、链接。先转义再替换，不放任何原始 HTML。"""
    escaped = html.escape(text or "")
    escaped = re.sub(r"(https?://[^\s<]+)", link_html, escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def render_md(text: str) -> str:
    rows = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            if rows and rows[-1] != '<div class="gap"></div>':
                rows.append('<div class="gap"></div>')
            continue
        label = LABEL_LINE.match(line)
        if label:
            name = label.group(1)
            rows.append(f'<div class="ln"><span class="lbl {LABEL_CLASS[name]}">{name}</span>{render_inline(line[label.end():])}</div>')
        else:
            rows.append(f'<div class="ln">{render_inline(line)}</div>')
    return f'<div class="md">{"".join(rows)}</div>'


def render_diff(text: str) -> str:
    """把裁剪后的 unified diff 转成带颜色的 <pre>，整行先转义。"""
    if not (text or "").strip() or text.startswith("（没有代码类"):
        return ""
    rows = []
    for line in text.splitlines():
        escaped = html.escape(line)
        if line.startswith("+++") or line.startswith("---") or line.startswith("diff ") or line.startswith("index "):
            klass = "d-meta"
        elif line.startswith("@@"):
            klass = "d-hunk"
        elif line.startswith("+"):
            klass = "d-add"
        elif line.startswith("-"):
            klass = "d-del"
        else:
            klass = ""
        rows.append(f'<span class="{klass}">{escaped}</span>' if klass else escaped)
    return f'<pre class="diffpre">{"".join(rows)}</pre>'


def diff_block(item: dict) -> str:
    body = render_diff(str(item.get("diff") or ""))
    if not body:
        if "diff" not in item:
            return '<div class="diffnote">旧报告没有存 diff，在后台补跑这一天即可。</div>'
        return '<div class="diffnote">这条没有代码类文本 diff（可能只改了二进制或非代码文件）。</div>'
    return f'<details class="diffbox"><summary>相关 diff（代码类，已裁剪）</summary>{body}</details>'


def list_dates() -> list:
    if not os.path.isdir(REVIEW_DIR):
        return []
    return sorted((name for name in os.listdir(REVIEW_DIR) if DATE_PATTERN.match(name)), reverse=True)


def load_record(date: str, name: str) -> dict:
    if not DATE_PATTERN.match(date) or not NAME_PATTERN.match(name):
        return {}
    path = os.path.join(REVIEW_DIR, date, name + ".json")
    try:
        with open(path, encoding="utf-8") as record_file:
            data = json.load(record_file)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def day_records(date: str) -> list:
    folder = os.path.join(REVIEW_DIR, date)
    if not DATE_PATTERN.match(date) or not os.path.isdir(folder):
        return []
    rows = []
    for file_name in sorted(os.listdir(folder)):
        if file_name.endswith(".json"):
            record = load_record(date, file_name[:-5])
            if record:
                record["_file"] = file_name[:-5]
                rows.append(record)
    return rows


def risk_count(report: str) -> int:
    if "需要留意" not in (report or ""):
        return 0
    count = 0
    for line in report.split("需要留意", 1)[1].split("\n")[1:]:
        line = line.strip()
        if line.startswith("**"):
            break
        if line:
            count += 1
    return max(count, 1)


def summary_of(record: dict) -> dict:
    status = record.get("status", "")
    commits = record.get("commits") or []
    report = record.get("report_md") or ""
    risks = risk_count(report) if status == "ok" else 0
    bomb = bool(record.get("bomb_out_md") or record.get("bomb_in_md"))
    tags = []
    if status == "failed":
        tags.append("failed")
        pill = ("p-failed", "生成失败")
    elif bomb:
        pill = ("p-bomb", "疑似炸线")
    elif risks:
        pill = ("p-risk", f"需要留意 {risks}")
    elif status == "empty" or not commits:
        pill = ("p-empty", "无提交")
    else:
        pill = ("p-clean", "没看出问题")
    if bomb:
        tags.append("bomb")
    if risks:
        tags.append("risk")
    if status == "empty" or not commits:
        tags.append("empty")
    elif status == "ok" and not risks and not bomb:
        tags.append("clean")
    overview = next((line.strip() for line in report.split("\n") if line.strip().startswith("共 ")), "")
    if status == "failed":
        text = "复盘没生成成功，可以找朱辉在后台补跑。"
    elif not commits:
        text = "这天在 main 和 CBT2_bugfix 上没有提交。"
    else:
        text = overview or commit_parts(commits[0])[1]
    return {"commits": len(commits), "risks": risks, "bomb": bomb, "tags": tags, "pill": pill, "text": text}


def commit_parts(item: dict) -> tuple:
    parts = str(item.get("line") or "").split("|", 3)
    if len(parts) < 4:
        return "", str(item.get("line") or "")
    return parts[2], parts[3]


def pill_html(pill: tuple) -> str:
    return f'<span class="pill {pill[0]}">{esc(pill[1])}</span>'


def page(title: str, hero: str, body: str, script: str = "") -> str:
    tail = f"<script>{script}</script>" if script else ""
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>{esc(title)}</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{STYLE}</style></head>'
            f'<body><header><div class="hwrap"><a class="brand" href="{BASE}/"><span class="logo">豆</span>大豆 · 自检复盘</a>{hero}</div></header>'
            f'<main>{body}</main>{tail}</body></html>')


def neighbors(dates: list, current: str) -> tuple:
    older = next((date for date in dates if date < current), "")
    newer = next((date for date in reversed(dates) if date > current), "")
    return older, newer


def nav_button(href: str, text: str, title: str) -> str:
    if not href:
        return f'<span class="btn off">{text}</span>'
    return f'<a class="btn" href="{esc(href)}" title="{esc(title)}">{text}</a>'


def index_page(query_date: str) -> str:
    dates = list_dates()
    if not dates:
        hero = '<h1>自检复盘</h1><div class="hsub">还没有复盘记录，每天 0 点自动生成。</div>'
        return page("自检复盘", hero, '<div class="panel none">还没有复盘记录。</div>')
    date = query_date if DATE_PATTERN.match(query_date or "") else dates[0]
    older, newer = neighbors(dates, date)
    records = day_records(date)
    rows = [(record, summary_of(record)) for record in records]
    rows.sort(key=lambda row: (not row[1]["bomb"], not row[1]["risks"], -row[1]["commits"], str(row[0].get("name"))))
    total_commits = sum(info["commits"] for _, info in rows)
    risk_people = sum(1 for _, info in rows if info["risks"])
    bomb_people = sum(1 for _, info in rows if info["bomb"])
    counts = {key: sum(1 for _, info in rows if key == "all" or key in info["tags"]) for key, _ in FILTERS}
    chips = "".join(f'<button class="chip{" on" if key == "all" else ""}" data-f="{key}">{text}<b>{counts[key]}</b></button>'
                    for key, text in FILTERS)
    toolbar = (f'<div class="toolbar">{nav_button(older and BASE + "/?d=" + older, "‹", "前一天")}'
               f'<form method="get" action="{BASE}/"><input type="date" name="d" value="{esc(date)}" min="{esc(dates[-1])}" '
               f'max="{esc(dates[0])}" onchange="this.form.submit()"></form>'
               f'{nav_button(newer and BASE + "/?d=" + newer, "›", "后一天")}'
               f'{nav_button(BASE + "/" if date != dates[0] else "", "最新", "最新一天")}'
               f'<span class="weekday">{weekday(date)}</span><div class="spacer"></div>'
               f'<input id="q" type="search" placeholder="搜名字或 git 作者"><div class="chips">{chips}</div></div>')
    stats = (f'<div class="stats"><div class="stat"><div class="k">复盘人数</div><div class="v">{len(rows)}</div></div>'
             f'<div class="stat"><div class="k">提交总数</div><div class="v">{total_commits}</div></div>'
             f'<div class="stat{" warn" if risk_people else ""}"><div class="k">需要留意的人</div><div class="v">{risk_people}</div></div>'
             f'<div class="stat{" bad" if bomb_people else ""}"><div class="k">疑似炸线的人</div><div class="v">{bomb_people}</div></div></div>')
    cards = []
    for record, info in rows:
        name = str(record.get("name") or record["_file"])
        author = str(record.get("git_author") or "")
        foot = [f'<span>{info["commits"]} 条提交</span>']
        if info["risks"]:
            foot.append(f'<span class="w">{info["risks"]} 处留意</span>')
        if info["bomb"]:
            foot.append('<span class="b">疑似炸线</span>')
        state = " is-bomb" if info["bomb"] else (" is-risk" if info["risks"] else "")
        cards.append(f'<a class="pcard{state}" href="{BASE}/r/{esc(date)}/{esc(record["_file"])}" '
                     f'data-key="{esc((name + " " + author).lower())}" data-tags="{" ".join(info["tags"])}">'
                     f'<div class="ptop">{avatar(name)}<div><div class="pname">{esc(name)}</div>'
                     f'<div class="pauthor">{esc(author)}</div></div>{pill_html(info["pill"])}</div>'
                     f'<div class="psum">{render_inline(info["text"])}</div><div class="pfoot">{"".join(foot)}</div></a>')
    empty_text = "这天没有复盘记录，换个日期看看。" if not rows else ""
    grid = f'<div class="grid">{"".join(cards)}</div>' if cards else ""
    body = (f'<div class="panel">{toolbar}{stats}</div>{grid}'
            f'<div id="nomatch" class="panel none" style="{"" if empty_text else "display:none"}">{empty_text or "没有符合条件的人。"}</div>')
    hero = (f'<h1>{esc(date)} 复盘</h1><div class="hsub">每天 0 点复盘前一天 main 和 CBT2_bugfix 上已推送的提交。'
            f'内容来自真实 diff，隐患只写有具体依据的；炸线是「疑似」，请当事人确认。</div>')
    return page(f"{date} 自检复盘", hero, body, INDEX_SCRIPT)


def person_page(name: str) -> str:
    if not NAME_PATTERN.match(name):
        return ""
    rows = []
    display = name
    for date in list_dates():
        record = load_record(date, name)
        if not record:
            continue
        display = str(record.get("name") or name)
        info = summary_of(record)
        rows.append(f'<tr><td><a href="{BASE}/r/{esc(date)}/{esc(name)}">{esc(date)}</a></td><td>{weekday(date)}</td>'
                    f'<td>{info["commits"]}</td><td>{pill_html(info["pill"])}</td><td>{render_inline(info["text"])}</td></tr>')
    if not rows:
        return ""
    hero = (f'<div class="crumb"><a href="{BASE}/">自检复盘</a> / {esc(display)}</div>'
            f'<div class="hero">{avatar(display, True)}<div><h1>{esc(display)} 的复盘历史</h1>'
            f'<div class="hsub">{esc(name)} · 共 {len(rows)} 天</div></div></div>')
    table = ('<table><tr><th>日期</th><th></th><th>提交</th><th>结论</th><th>概要</th></tr>' + "".join(rows) + "</table>")
    return page(f"{display} 的复盘", hero, f'<section class="panel">{table}</section>')


def report_page(date: str, name: str) -> str:
    record = load_record(date, name)
    if not record:
        return ""
    display = str(record.get("name") or name)
    info = summary_of(record)
    person_dates = [day for day in list_dates() if load_record(day, name)]
    older, newer = neighbors(person_dates, date)
    nav = (f'<div class="toolbar">{nav_button(older and f"{BASE}/r/{older}/{name}", "‹", "他的前一天")}'
           f'{nav_button(newer and f"{BASE}/r/{newer}/{name}", "›", "他的后一天")}'
           f'<a class="btn" href="{BASE}/?d={esc(date)}">当天所有人</a><a class="btn" href="{BASE}/p/{esc(name)}">他的历史</a>'
           f'<div class="spacer"></div><span class="weekday">生成于 {esc(str(record.get("generated_at") or "")[:16].replace("T", " "))}</span></div>')
    stats = (f'<div class="stats"><div class="stat"><div class="k">提交</div><div class="v">{info["commits"]}</div></div>'
             f'<div class="stat{" warn" if info["risks"] else ""}"><div class="k">需要留意</div><div class="v">{info["risks"]}</div></div>'
             f'<div class="stat{" bad" if record.get("bomb_out_md") else ""}"><div class="k">合并丢了别人的</div>'
             f'<div class="v">{"有" if record.get("bomb_out_md") else "无"}</div></div>'
             f'<div class="stat{" bad" if record.get("bomb_in_md") else ""}"><div class="k">被别人合并丢了</div>'
             f'<div class="v">{"有" if record.get("bomb_in_md") else "无"}</div></div></div>')
    blocks = [f'<div class="panel">{nav}{stats}</div>']
    if record.get("bomb_out_md"):
        blocks.append(f'<section class="panel alert"><h2>你的合并疑似丢了别人的修改</h2>{render_md(record["bomb_out_md"])}</section>')
    if record.get("bomb_in_md"):
        blocks.append(f'<section class="panel alert"><h2>你的修改疑似被别人合并丢了</h2>{render_md(record["bomb_in_md"])}</section>')
    if record.get("status") == "failed":
        blocks.append(f'<section class="panel">这天的复盘没生成成功：{esc(record.get("error") or "模型没返回内容")}。可以找朱辉在后台补跑。</section>')
    commits = record.get("commits") or []
    if commits:
        items = []
        for item in commits:
            time_text, subject = commit_parts(item)
            items.append(
                f'<li><div class="cmain"><span class="hash">{esc(item.get("hash"))}</span><span class="br">{esc(item.get("branch"))}</span>'
                f'<span class="time">{esc(time_text)}</span><span>{render_inline(subject)}</span></div>{diff_block(item)}</li>'
            )
        blocks.append(f'<section class="panel"><h2>当天提交<span class="n">{len(commits)} 条</span></h2><ul class="clist">{"".join(items)}</ul></section>')
    if record.get("report_md"):
        blocks.append(f'<section class="panel"><h2>复盘</h2>{render_md(record["report_md"])}</section>')
    if record.get("status") == "empty":
        blocks.append('<section class="panel none">这天在 main 和 CBT2_bugfix 上没有提交。</section>')
    hero = (f'<div class="crumb"><a href="{BASE}/">自检复盘</a> / <a href="{BASE}/?d={esc(date)}">{esc(date)}</a> / {esc(display)}</div>'
            f'<div class="hero">{avatar(display, True)}<div><h1>{esc(display)}{pill_html(info["pill"])}</h1>'
            f'<div class="hsub">{esc(record.get("git_author"))} · {esc(date)} {weekday(date)}</div></div></div>')
    return page(f"{display} {date} 复盘", hero, "".join(blocks))


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        return

    def send_html(self, body: str, code: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        parts = [unquote(part) for part in url.path.split("/") if part]
        body = ""
        if not parts:
            body = index_page((parse_qs(url.query).get("d") or [""])[0])
        elif parts[0] == "r" and len(parts) == 3:
            body = report_page(parts[1], parts[2])
        elif parts[0] == "p" and len(parts) == 2:
            body = person_page(parts[1])
        if body:
            self.send_html(body)
        else:
            self.send_html(page("没找到", "<h1>没有这页</h1>", f'<div class="panel none">没有这页。<a href="{BASE}/">回首页</a></div>'), 404)



def serve() -> None:
    ThreadingHTTPServer((HOST, PORT), ReviewHandler).serve_forever()


if __name__ == "__main__":
    serve()
