"""大豆炸线看板：独立的只读内网网页。数据在 bomb_board.py（由大豆的炸线扫描写入），这里只出页面和 JSON。"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import bomb
import bomb_board

HOST = "0.0.0.0"
PORT = 8772
STATE_PATH = os.path.join(bomb_board.BOT_DIR, "bomb.json")


def scan_info() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    enabled = state.get("enabled", True) is not False
    return {"enabled": enabled, "minutes": state.get("minutes") or 30, "last_at": state.get("last_at", ""),
            "next_at": state.get("next_at", "") if enabled else ""}


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>炸线看板 · 大豆</title>
<style>
:root { --bg:#f6f4f2; --card:#fff; --line:#ece6e1; --text:#231c18; --sub:#857a72; --brand:#e8590c;
  --bad:#e03131; --bad-soft:#fff0f0; --warn:#e67700; --warn-soft:#fff6e6; --ok:#2b8a3e; --ok-soft:#ebf8ee; --force:#7048e8; --force-soft:#f1edff; }
* { box-sizing:border-box; }
body { margin:0; color:var(--text); font:14px/1.6 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); min-height:100vh; }
button, select, input { font:inherit; }
.wrap { max-width:1120px; margin:0 auto; padding:0 24px; }
code { font:12px/1.4 Consolas,"Cascadia Mono",monospace; background:#f4efeb; border-radius:6px; padding:1px 6px; color:#5c4a3f; }
/* hero */
.hero { position:relative; overflow:hidden; border-radius:0 0 30px 30px; color:#fff;
  background:radial-gradient(700px 300px at 85% 10%,rgba(255,214,102,.35),transparent 60%),linear-gradient(120deg,#3b1d12 0%,#8a2a0e 45%,#e8590c 85%,#f59f00 100%); }
.hero .wrap { padding-top:30px; padding-bottom:84px; display:flex; gap:24px; align-items:center; }
.brand { display:inline-flex; align-items:center; gap:8px; font-size:13px; opacity:.9; margin-bottom:8px; }
.brand i { width:24px; height:24px; border-radius:8px; background:rgba(255,255,255,.2); display:grid; place-items:center; font-style:normal; font-weight:700; }
.hero h1 { margin:0; font-size:32px; letter-spacing:2px; font-weight:750; }
.hero p { margin:6px 0 0; opacity:.9; max-width:600px; }
.scan { margin-top:14px; font-size:12px; opacity:.85; }
.scan b { font-weight:600; }
.bolt { margin-left:auto; flex:none; width:180px; height:150px; }
.bolt path { filter:drop-shadow(0 6px 22px rgba(255,200,60,.55)); animation:flick 4s ease-in-out infinite; }
@keyframes flick { 0%,92%,100% { opacity:1; } 94% { opacity:.4; } 96% { opacity:1; } 97% { opacity:.55; } }
/* stats */
.stats { margin-top:-56px; position:relative; z-index:2; display:grid; grid-template-columns:repeat(5,1fr); gap:12px; }
.stat { background:var(--card); border-radius:16px; padding:14px 16px; box-shadow:0 10px 28px rgba(60,30,10,.08); border:1px solid var(--line); }
.stat .n { font-size:26px; font-weight:750; line-height:1.2; }
.stat .l { color:var(--sub); font-size:12px; }
.stat.bad .n { color:var(--bad); } .stat.warn .n { color:var(--warn); } .stat.force .n { color:var(--force); }
/* toolbar */
.bar { position:sticky; top:0; z-index:5; margin:18px 0 14px; background:rgba(255,255,255,.9); backdrop-filter:blur(10px); border:1px solid var(--line);
  border-radius:16px; padding:10px 12px; display:flex; flex-wrap:wrap; align-items:center; gap:10px; box-shadow:0 6px 20px rgba(60,30,10,.05); }
.seg { display:flex; background:#f4f0ec; border-radius:11px; padding:3px; }
.seg button { border:none; background:none; padding:5px 12px; border-radius:9px; color:var(--sub); cursor:pointer; }
.seg button.on { background:#fff; color:var(--text); font-weight:600; box-shadow:0 1px 4px rgba(0,0,0,.08); }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { border:1px solid var(--line); background:#fff; border-radius:999px; padding:4px 12px; cursor:pointer; color:var(--sub); }
.chip b { color:var(--text); margin-left:2px; }
.chip.on { background:var(--text); border-color:var(--text); color:#fff; } .chip.on b { color:#fff; }
.sel { height:34px; border:1px solid var(--line); border-radius:11px; padding:0 10px; background:#fff; color:var(--text); }
.search { position:relative; flex:1; min-width:180px; }
.search input { width:100%; height:34px; border:1px solid var(--line); border-radius:11px; padding:0 12px 0 30px; background:#fff; }
.search::before { content:"⌕"; position:absolute; left:10px; top:5px; color:var(--sub); font-size:16px; }
/* list */
.day { display:flex; align-items:center; gap:10px; margin:22px 0 10px; color:var(--sub); font-size:13px; font-weight:600; }
.day::after { content:""; flex:1; height:1px; background:var(--line); }
.card { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--warn); border-radius:14px; padding:14px 18px; margin-bottom:12px;
  box-shadow:0 4px 14px rgba(60,30,10,.04); }
.card.s-bad { border-left-color:var(--bad); } .card.s-ok { border-left-color:var(--ok); } .card.s-force { border-left-color:var(--force); }
.top { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.pill { border-radius:999px; padding:1px 10px; font-size:12px; font-weight:600; }
.p-bad { background:var(--bad-soft); color:var(--bad); } .p-warn { background:var(--warn-soft); color:var(--warn); }
.p-ok { background:var(--ok-soft); color:var(--ok); } .p-force { background:var(--force-soft); color:var(--force); }
.br { font-size:12px; color:#5c4a3f; background:#f4efeb; border-radius:6px; padding:1px 8px; }
.who { color:var(--sub); } .who b { color:var(--text); }
.told { margin-left:auto; font-size:12px; color:var(--sub); }
.subj { margin:6px 0 4px; font-weight:600; word-break:break-all; }
.files { margin-top:8px; display:flex; flex-direction:column; gap:6px; }
.file { background:#faf8f6; border-radius:10px; padding:8px 12px; }
.file .fp { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.file .fp code { background:none; padding:0; color:var(--text); font-size:12.5px; word-break:break-all; }
.fs { font-size:12px; font-weight:600; } .fs.bad { color:var(--bad); } .fs.ok { color:var(--ok); } .fs.warn { color:var(--warn); }
.lost { margin-top:3px; color:var(--sub); font-size:12.5px; }
.lost span { display:block; }
.more { color:var(--sub); font-size:12.5px; padding-left:4px; }
.foot { margin-top:10px; color:var(--sub); font-size:12px; }
.empty { text-align:center; color:var(--sub); padding:70px 0; }
.empty .big { font-size:40px; }
.note { margin:26px 0 40px; color:var(--sub); font-size:12px; line-height:1.7; }
@media (max-width:820px) { .stats { grid-template-columns:repeat(2,1fr); } .bolt { display:none; } .told { margin-left:0; } }
</style>
</head>
<body>
<header class="hero">
  <div class="wrap">
    <div>
      <div class="brand"><i>豆</i>大豆</div>
      <h1>炸线看板</h1>
      <p>合并时谁的修改被整份丢掉、现在补回没有，以及远端分支被强推。大豆定时自动查，这里只读，保留最近 90 天。</p>
      <div class="scan" id="scan"></div>
    </div>
    <svg class="bolt" viewBox="0 0 120 120" aria-hidden="true">
      <circle cx="60" cy="60" r="52" fill="rgba(255,255,255,.08)"/><circle cx="60" cy="60" r="36" fill="rgba(255,255,255,.08)"/>
      <path d="M66 14 30 66h24l-8 40 40-56H62l8-36z" fill="#ffd43b"/>
    </svg>
  </div>
</header>
<main class="wrap">
  <div class="stats" id="stats"></div>
  <div class="bar">
    <div class="seg" id="branchSeg"></div>
    <div class="chips" id="chips"></div>
    <select class="sel" id="range"><option value="7">最近 7 天</option><option value="30" selected>最近 30 天</option><option value="90">最近 90 天</option></select>
    <label class="search"><input id="q" type="search" placeholder="搜人名、文件、提交号或说明"></label>
  </div>
  <div id="list"></div>
  <p class="note" id="note"></p>
</main>
<script>
const state = { data: null, branch: "all", status: "all", range: 30, q: "" };
const STATUS = [["all", "全部"], ["还丢着", "还丢着"], ["要人工看", "要人工看"], ["已补回", "已补回"], ["强推", "强推"]];
const CLS = { "还丢着": "bad", "要人工看": "warn", "已补回": "ok", "强推": "force" };
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const $ = (id) => document.getElementById(id);
function esc(text) { return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function clean(subject) { return String(subject || "").replace(/\s*https?:\/\/\S+/g, "").trim(); }
function short(iso) { const t = String(iso || ""); return t.length >= 16 ? t.slice(5, 10) + " " + t.slice(11, 16) : t; }
function when(record) { return String(record.at || record.first_seen || ""); }
function ageDays(iso) { const t = Date.parse(iso); return isNaN(t) ? 0 : (Date.now() - t) / 86400000; }
function commitLine(line) {
  const parts = String(line).split("|");
  if (parts.length < 4) return esc(line);
  const subject = clean(parts.slice(3).join("|"));
  return "<b>" + esc(parts[1]) + "</b> " + esc(parts[2]) + "「" + esc(subject.slice(0, 60)) + "」 <code>" + esc(parts[0]) + "</code>";
}
function haystack(record) {
  const bits = [record.author, record.subject, record.merge, record.old, record.new, (record.branches || []).join(" ")];
  (record.files || []).forEach((file) => { bits.push(file.path); (file.commits || []).forEach((line) => bits.push(line)); });
  (record.lost || []).forEach((line) => bits.push(line));
  return bits.join(" ").toLowerCase();
}
function inScope(record) {
  if (ageDays(when(record)) > state.range) return false;
  if (state.branch !== "all" && !(record.branches || []).includes(state.branch)) return false;
  return !state.q || haystack(record).includes(state.q);
}
function paintStats(records) {
  const week = records.filter((r) => ageDays(when(r)) <= 7).length;
  const month = records.filter((r) => ageDays(when(r)) <= 30).length;
  const count = (status) => records.filter((r) => r.status === status && ageDays(when(r)) <= 30).length;
  $("stats").innerHTML = [["", week, "最近 7 天"], ["", month, "最近 30 天"], ["bad", count("还丢着"), "30 天内还丢着"],
    ["warn", count("要人工看"), "30 天内要人工看"], ["force", count("强推"), "30 天内强推"]]
    .map(([cls, n, label]) => "<div class='stat " + cls + "'><div class='n'>" + n + "</div><div class='l'>" + label + "</div></div>").join("");
}
function paintBar(records) {
  const branches = [];
  records.forEach((r) => (r.branches || []).forEach((b) => { if (!branches.includes(b)) branches.push(b); }));
  if (state.branch !== "all" && !branches.includes(state.branch)) state.branch = "all";
  $("branchSeg").innerHTML = [["all", "全部分支"]].concat(branches.map((b) => [b, b]))
    .map(([key, label]) => "<button data-b='" + esc(key) + "' class='" + (state.branch === key ? "on" : "") + "'>" + esc(label) + "</button>").join("");
  const scoped = records.filter(inScope);
  $("chips").innerHTML = STATUS.map(([key, label]) => {
    const n = key === "all" ? scoped.length : scoped.filter((r) => r.status === key).length;
    return "<button class='chip" + (state.status === key ? " on" : "") + "' data-s='" + key + "'>" + label + " <b>" + n + "</b></button>";
  }).join("");
}
function mergeCard(r) {
  const cls = CLS[r.status] || "warn";
  const files = (r.files || []).map((f) => {
    const name = String(f.path || "").split("/").slice(-3).join("/");
    const lost = (f.commits || []).map((line) => "<span>丢的是：" + commitLine(line) + "</span>").join("") || "<span>丢的是对面那侧的修改</span>";
    return "<div class='file'><div class='fp'><span class='fs " + (CLS[f.state] || "warn") + "'>" + esc(f.state || "要人工看") + "</span>"
      + "<code title='" + esc(f.path) + "'>" + esc(name) + "</code></div><div class='lost'>" + lost + "</div></div>";
  }).join("");
  const extra = (r.count || 0) - (r.files || []).length;
  return "<article class='card s-" + cls + "'><div class='top'><span class='pill p-" + cls + "'>" + esc(r.status) + "</span>"
    + (r.branches || []).map((b) => "<span class='br'>" + esc(b) + "</span>").join("")
    + "<code>" + esc(r.merge) + "</code><span class='who'><b>" + esc(r.author) + "</b> 合并 · " + esc(r.date || short(r.at)) + "</span>"
    + "<span class='told'>" + (r.alerted ? "已提醒" : "补录，没单独提醒") + "</span></div>"
    + "<div class='subj'>" + esc(clean(r.subject)) + "</div>"
    + "<div class='who'>丢了 " + (r.count || 0) + " 个文件的修改</div>"
    + "<div class='files'>" + files + (extra > 0 ? "<div class='more'>另外还有 " + extra + " 个文件没逐个列出</div>" : "") + "</div>"
    + "<div class='foot'>首次发现 " + esc(short(r.first_seen)) + " · 补回状态截至 " + esc(short(r.checked_at)) + "</div></article>";
}
function forceCard(r) {
  const lost = (r.lost || []).map((line) => "<span>" + commitLine(line) + "</span>").join("") || "<span>没列出被冲掉的提交</span>";
  return "<article class='card s-force'><div class='top'><span class='pill p-force'>强推</span>"
    + (r.branches || []).map((b) => "<span class='br'>" + esc(b) + "</span>").join("")
    + "<code>" + esc(r.old) + "</code> → <code>" + esc(r.new) + "</code><span class='who'>本机拉取时发现 · " + esc(r.when) + "</span>"
    + "<span class='told'>" + (r.alerted ? "已提醒" : "补录，没单独提醒") + "</span></div>"
    + "<div class='subj'>远端分支被强推，冲掉了 " + (r.lost || []).length + " 个提交</div>"
    + "<div class='files'><div class='file'><div class='lost'>" + lost + "</div></div></div>"
    + "<div class='foot'>首次发现 " + esc(short(r.first_seen)) + "</div></article>";
}
function dayLabel(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "更早";
  const today = new Date(); const yesterday = new Date(Date.now() - 86400000);
  const same = (a, b) => a.toDateString() === b.toDateString();
  const tag = same(d, today) ? " · 今天" : same(d, yesterday) ? " · 昨天" : "";
  return (d.getMonth() + 1) + " 月 " + d.getDate() + " 日 " + WEEK[d.getDay()] + tag;
}
function render() {
  const data = state.data;
  if (!data) return;
  const records = data.records || [];
  paintStats(records);
  paintBar(records);
  const rows = records.filter((r) => inScope(r) && (state.status === "all" || r.status === state.status));
  if (!rows.length) {
    const none = !records.length;
    $("list").innerHTML = "<div class='empty'><div class='big'>" + (none ? "🎉" : "🔍") + "</div>"
      + (none ? "最近没查到炸线。" : "没有符合条件的记录，换个筛选试试。") + "</div>";
    return;
  }
  let html = ""; let last = "";
  rows.forEach((r) => {
    const label = dayLabel(when(r));
    if (label !== last) { html += "<div class='day'>" + esc(label) + "</div>"; last = label; }
    html += r.kind === "force" ? forceCard(r) : mergeCard(r);
  });
  $("list").innerHTML = html;
}
function paintScan(scan) {
  const parts = [];
  parts.push(scan.enabled ? "每 " + scan.minutes + " 分钟自动查一次" : "自动检查已关闭");
  if (scan.last_at) parts.push("上次检查 <b>" + esc(short(scan.last_at)) + "</b>");
  if (scan.next_at) parts.push("下次 <b>" + esc(short(scan.next_at)) + "</b>");
  $("scan").innerHTML = parts.join(" · ");
}
async function load() {
  try {
    const response = await fetch("api/bombs", { cache: "no-store" });
    const data = await response.json();
    state.data = data;
    paintScan(data.scan || {});
    $("note").textContent = data.note || "";
    render();
  } catch (error) {
    $("list").innerHTML = "<div class='empty'>读不到数据，稍后刷新试试。</div>";
  }
}
document.addEventListener("click", (event) => {
  const branch = event.target.closest("[data-b]");
  if (branch) { state.branch = branch.dataset.b; render(); return; }
  const chip = event.target.closest("[data-s]");
  if (chip) { state.status = chip.dataset.s; render(); }
});
$("range").addEventListener("change", (event) => { state.range = Number(event.target.value) || 30; render(); });
$("q").addEventListener("input", (event) => { state.q = event.target.value.trim().toLowerCase(); render(); });
load();
setInterval(load, 60000);
</script>
</body>
</html>
"""


class BombHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        return

    def send_body(self, raw: bytes, kind: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, data: dict, code: int = 200) -> None:
        self.send_body(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.send_body(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/bombs":
            self.send_json(dict(bomb_board.board_view(), scan=scan_info(), note=bomb.BOMB_NOTE))
        else:
            self.send_body("没有这页。".encode("utf-8"), "text/plain; charset=utf-8", 404)


def serve() -> None:
    ThreadingHTTPServer((HOST, PORT), BombHandler).serve_forever()


if __name__ == "__main__":
    serve()
