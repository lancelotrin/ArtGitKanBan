"""大豆后台。只监听本机 127.0.0.1:8765，不对外网开放。

启动：python D:\\FeishuBot\\console.py
浏览器打开 http://127.0.0.1:8765
"""
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
from urllib.parse import urlparse

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BOT_DIR, "bot_config.json")
LOG_PATH = os.path.join(BOT_DIR, "bot.log")
ERR_PATH = os.path.join(BOT_DIR, "bot.err.log")
MISSES_PATH = os.path.join(BOT_DIR, "misses.jsonl")
QUESTIONS_PATH = os.path.join(BOT_DIR, "questions.jsonl")
HANDLED_PATH = os.path.join(BOT_DIR, "misses_handled.json")
NOTES_PATH = os.path.join(BOT_DIR, "notes.json")
REVIEWS_PATH = os.path.join(BOT_DIR, "reviews.json")
PEOPLE_PATH = os.path.join(BOT_DIR, "people.json")
REF_INDEX_PATH = os.path.join(BOT_DIR, "ref_index.sqlite")
DEFAULT_ROOT = r"D:\Fork\onepiece_engine\Client"
PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
HOST = os.environ.get("CONSOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CONSOLE_PORT", "8765"))
# 子站都挂在后台同一个端口下，只留一个入口
MOUNTS = {"/review": "review_web", "/wish": "wish_web", "/bomb": "bomb_web", "/merge": "merge_web"}
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,60}$")
# 只放智谱免费的 flash 模型，第一个是默认。
MODEL_CHOICES = {
    "chat": ("glm-4.5-flash", "glm-4-flash-250414", "glm-4-flash"),
    "vision": ("glm-4.6v-flash", "glm-4v-flash"),
}
MODEL_ENV = {"chat": "LLM_MODEL", "vision": "LLM_VISION_MODEL"}
MODEL_HISTORY_KEEP = 20
MODEL_TEST_PROMPT = [{"role": "user", "content": "只回复 OK 两个字母。"}]

index_lock = threading.Lock()
index_state = {"running": False, "rows": 0, "error": "", "started": 0.0}

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>大豆后台</title>
<style>
  :root { color-scheme: light; --bg:#f4f6fb; --card:#fff; --line:#e6e9f2; --ink:#1b2333; --muted:#6b7386; --brand:#3d5afe; --brand-soft:#eef1ff;
    --ok:#14804a; --ok-soft:#e7f6ee; --bad:#c0262d; --bad-soft:#fdecec; --side:#141b34; --side-2:#1f2850; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.6 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--ink); }
  a { color: var(--brand); }
  .shell { display: flex; min-height: 100vh; }
  .side { width: 216px; flex: none; background: linear-gradient(180deg, var(--side), var(--side-2)); color: #cdd3ea; position: sticky; top: 0; height: 100vh;
    display: flex; flex-direction: column; padding: 18px 12px; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 4px 8px 18px; color: #fff; }
  .brand .logo { width: 34px; height: 34px; border-radius: 10px; background: linear-gradient(135deg, #5b74ff, #3d5afe); display: grid; place-items: center; font-weight: 700; }
  .brand b { display: block; font-size: 15px; } .brand small { color: #8f98bd; font-size: 12px; }
  .side nav { display: flex; flex-direction: column; gap: 2px; }
  .side nav a { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-radius: 10px; color: #cdd3ea; text-decoration: none; font-size: 14px; }
  .side nav a:hover { background: rgba(255,255,255,.06); color: #fff; }
  .side nav a.on { background: rgba(91,116,255,.22); color: #fff; box-shadow: inset 3px 0 0 #7b8fff; }
  .side nav a .ico { width: 18px; text-align: center; opacity: .85; }
  .side nav .group { margin: 14px 12px 4px; font-size: 11px; color: #7a83a8; letter-spacing: 1px; }
  .side-foot { margin-top: auto; padding: 12px; font-size: 12px; color: #8f98bd; border-top: 1px solid rgba(255,255,255,.08); }
  .side-foot a { color: #b9c3ff; word-break: break-all; }
  .content { flex: 1; min-width: 0; }
  .topbar { position: sticky; top: 0; z-index: 4; display: flex; gap: 14px; align-items: center; padding: 14px 28px; background: rgba(244,246,251,.92);
    backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
  .topbar h1 { margin: 0; font-size: 18px; font-weight: 650; }
  #topStatus { font-size: 13px; color: var(--muted); }
  #topStatus .ok, #topStatus .bad { display: inline-block; padding: 1px 10px; border-radius: 999px; }
  #topStatus .ok { background: var(--ok-soft); } #topStatus .bad { background: var(--bad-soft); }
  .spacer { flex: 1; }
  main { padding: 22px 28px 48px; max-width: 1240px; }
  .tab[hidden] { display: none; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  .grid2 > section { margin-bottom: 0; }
  .grid2 + section, section + .grid2 { margin-top: 18px; }
  section { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-bottom: 18px; box-shadow: 0 4px 18px rgba(20,27,52,.04);
    scroll-margin-top: 84px; }
  h2 { margin: 0 0 6px; font-size: 15px; font-weight: 650; }
  h2 + .meta, h2 + p.meta { margin-top: 0; margin-bottom: 12px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  button, input, textarea { font: inherit; }
  button { border: 1px solid var(--line); background: #fff; color: var(--ink); border-radius: 9px; padding: 6px 14px; cursor: pointer; transition: border-color .15s, background .15s; }
  button:hover:not(:disabled) { border-color: var(--brand); color: var(--brand); }
  button.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
  button.primary:hover:not(:disabled) { background: #2f4bef; color: #fff; }
  button:disabled { opacity: .45; cursor: default; }
  button:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
  button.text { border: 0; background: transparent; color: var(--brand); padding: 0; }
  button.text:hover:not(:disabled) { background: transparent; text-decoration: underline; }
  input[type=text], input[type=number], input[type=password], input[type=date], textarea { width: 100%; border: 1px solid var(--line); border-radius: 9px; padding: 8px 10px; background: #fff; color: var(--ink); }
  input:focus, textarea:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
  input.hours { width: 120px; }
  textarea { min-height: 88px; resize: vertical; }
  .meta { color: var(--muted); font-size: 13px; margin-top: 8px; }
  .ok { color: var(--ok); } .bad { color: var(--bad); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; vertical-align: top; border-top: 1px solid var(--line); padding: 9px 8px; }
  th { color: var(--muted); font-weight: 500; font-size: 12px; border-top: 0; }
  tbody tr:hover td { background: #fafbfe; }
  .handled { opacity: 0.45; }
  pre { white-space: pre-wrap; background: #0f172a; color: #e2e8f0; border-radius: 10px; padding: 12px 14px; margin: 8px 0 0; max-height: 360px; overflow: auto;
    font: 12px/1.55 Consolas, monospace; }
  label { display: block; margin: 12px 0 4px; color: var(--muted); font-size: 13px; }
  .pager { display: flex; gap: 8px; align-items: center; margin: 4px 0 10px; position: sticky; top: 62px; z-index: 2; background: var(--card); padding: 6px 0; }
  .pager .meta { margin: 0 6px 0 0; }
  .pager button:disabled { opacity: 1; color: #a3a3a3; border-color: #eceef4; background: #fafbfd; }
  .table-wrap { overflow: auto; }
  td.time { white-space: nowrap; color: var(--muted); }
  .clip { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .clip.open { display: block; -webkit-line-clamp: unset; }
  .toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); background: #1b2333; color: #fff; padding: 10px 16px; border-radius: 10px; z-index: 5; box-shadow: 0 8px 24px rgba(0,0,0,.18); }
  .toast.bad { background: var(--bad); }
  .sub { border-top: 1px dashed var(--line); margin-top: 16px; padding-top: 4px; }
  .sites { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
  .site { border: 1px solid var(--line); border-radius: 14px; padding: 16px; display: flex; flex-direction: column; gap: 8px; background: linear-gradient(180deg, #fff, #fafbff); }
  .site-top { display: flex; align-items: center; gap: 10px; }
  .site-ico { width: 38px; height: 38px; border-radius: 12px; display: grid; place-items: center; color: #fff; font-size: 18px; flex: none; }
  .site-name { font-weight: 650; font-size: 15px; } .site-port { color: var(--muted); font-size: 12px; }
  .dot { margin-left: auto; font-size: 12px; padding: 2px 10px; border-radius: 999px; }
  .dot.up { background: var(--ok-soft); color: var(--ok); } .dot.down { background: var(--bad-soft); color: var(--bad); }
  .site-desc { color: var(--muted); font-size: 13px; }
  .site-url { font: 12px Consolas, monospace; background: #f3f5fb; border-radius: 8px; padding: 4px 8px; word-break: break-all; }
  .site .row { margin-top: auto; }
  a.btnlink { display: inline-block; border: 1px solid var(--line); border-radius: 9px; padding: 6px 14px; text-decoration: none; color: var(--ink); background: #fff; }
  a.btnlink:hover { border-color: var(--brand); color: var(--brand); }
  a.btnlink.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
  .wbar select { border: 1px solid var(--line); border-radius: 9px; padding: 7px 10px; font: inherit; background: #fff; }
  .switch { display: inline-flex; align-items: center; gap: 8px; margin: 0; color: var(--ink); font-size: 13px; cursor: pointer; }
  .switch input { display: none; }
  .switch span { width: 36px; height: 20px; border-radius: 999px; background: #d5d9e6; position: relative; transition: background .15s; }
  .switch span::after { content: ""; position: absolute; left: 2px; top: 2px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: left .15s; }
  .switch input:checked + span { background: var(--ok); } .switch input:checked + span::after { left: 18px; }
  .wpill { display: inline-block; font-size: 12px; padding: 1px 9px; border-radius: 999px; white-space: nowrap; }
  .w-open { background: var(--brand-soft); color: var(--brand); } .w-doing { background: #fff4e5; color: #b4540b; } .w-done { background: var(--ok-soft); color: var(--ok); }
  tr.wedit td { background: #fafbff; }
  #sec-wish th, #sec-wish td:nth-child(3), #sec-wish td:nth-child(4), #sec-wish td:nth-child(6) { white-space: nowrap; }
  #sec-models code, #sec-models td:nth-child(4) { white-space: nowrap; }
  .wgrid { display: grid; grid-template-columns: 1fr 120px 140px 140px; gap: 10px; }
  .wgrid textarea { grid-column: 1 / -1; min-height: 70px; }
  .wgrid select { border: 1px solid var(--line); border-radius: 9px; padding: 8px 10px; font: inherit; background: #fff; }
  @media (max-width: 1100px) { .grid2 { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .side { width: 64px; } .brand div, .side nav a span, .side nav .group, .side-foot { display: none; } }
</style>
</head>
<body>
<div class="shell">
<aside class="side">
  <div class="brand"><span class="logo">豆</span><div><b>大豆后台</b><small>只在本机打开</small></div></div>
  <nav>
    <a href="#overview" data-tab="overview"><i class="ico">◎</i><span>概览</span></a>
    <div class="group">功能</div>
    <a href="#code" data-tab="code"><i class="ico">⌥</i><span>代码</span></a>
    <a href="#review" data-tab="review"><i class="ico">✓</i><span>复盘</span></a>
    <a href="#chat" data-tab="chat"><i class="ico">✎</i><span>对话</span></a>
    <a href="#sites" data-tab="sites"><i class="ico">◫</i><span>网站</span></a>
    <div class="group">其他</div>
    <a href="#settings" data-tab="settings"><i class="ico">⚙</i><span>设置</span></a>
  </nav>
  <div class="side-foot">记下的是单聊，以及群里 @ 了大豆的问题。</div>
</aside>
<div class="content">
<div class="topbar">
  <h1 id="pageTitle">概览</h1>
  <span id="topStatus">正在读取…</span>
  <div class="spacer"></div>
  <button id="start" class="primary">启动</button>
  <button id="stop">停止</button>
</div>
<main>
  <div class="tab" data-tab="overview">
    <div class="grid2">
      <section>
        <h2>运行状态</h2>
        <p class="meta">停止只结束大豆和它的飞书订阅，不会关掉飞书客户端。</p>
        <div id="status">正在读取…</div>
      </section>
      <section>
        <h2>引用索引</h2>
        <p class="meta">大豆每 6 小时自动更新一次。手动重建会扫整个 Assets，大约一分钟。进行中不要再点。</p>
        <div id="indexInfo">正在读取…</div>
        <div class="row" style="margin-top:10px"><button id="rebuild">重建索引</button></div>
      </section>
    </div>
    <section>
      <h2>最近日志</h2>
      <pre id="log"></pre>
    </section>
  </div>

  <div class="tab" data-tab="code" hidden>
    <section>
      <h2>工程</h2>
      <p class="meta">文件内容（面数、引用、Shader）读这里磁盘上的检出。</p>
      <label>Client 目录</label>
      <input id="root" type="text">
      <label>允许查询的 Git 分支，一行一个。提交记录只 fetch 这些分支，main 会保留，盯目录也只盯这里。</label>
      <textarea id="branches"></textarea>
      <div class="row" style="margin-top:10px">
        <button id="save" class="primary">保存</button>
        <button id="saveRestart">保存并重启</button>
      </div>
      <p id="configMsg" class="meta"></p>
      <div class="sub">
        <label>切工作区到这个分支（远端要有）。有本地修改会先清掉。和上面「查询分支」不是一回事。</label>
        <div class="row">
          <input id="checkoutBranch" type="text" placeholder="例如 CBT2_bugfix_shader" style="max-width:320px">
          <button id="checkoutNow" class="primary">切换分支</button>
        </div>
        <p id="checkoutMsg" class="meta"></p>
      </div>
    </section>
    <div class="grid2">
      <section id="sec-pull">
        <h2>拉代码</h2>
        <p class="meta">只快进当前分支。有已跟踪文件的本地修改会先清掉再拉。改完大约 20 秒内生效，不用重启。</p>
        <div id="pullInfo">正在读取…</div>
        <label>间隔（小时，1 到 168）</label>
        <input id="pullHours" class="hours" type="number" min="1" max="168" step="1">
        <div class="row" style="margin-top:10px">
          <button id="pullOn" class="primary">开启定时</button>
          <button id="pullOff">关闭定时</button>
          <button id="pullNow">现在拉一次</button>
        </div>
        <p id="pullMsg" class="meta"></p>
      </section>
      <section id="sec-watch">
        <h2>盯目录</h2>
        <p class="meta">只看 main 和 CBT2_bugfix，不拉工作区。有匹配文件的新提交就私聊朱辉。一行一个目录，后面写后缀。</p>
        <div id="watchInfo">正在读取…</div>
        <div class="row">
          <div><label>查最近几天（1 到 7）</label><input id="watchDays" class="hours" type="number" min="1" max="7" step="1"></div>
          <div><label>多久查一次（分钟，5 到 1440）</label><input id="watchMinutes" class="hours" type="number" min="5" max="1440" step="1"></div>
        </div>
        <label>目录和后缀</label>
        <textarea id="watchRules" placeholder="Assets/Bundle/Shader .shader .hlsl"></textarea>
        <div class="row" style="margin-top:10px">
          <button id="watchOn" class="primary">开启盯目录</button>
          <button id="watchOff">关闭</button>
          <button id="watchNow">现在查一次</button>
        </div>
        <p id="watchMsg" class="meta"></p>
      </section>
    </div>
    <section id="sec-bomb">
      <h2>炸线提醒</h2>
      <p class="meta">扫上面「查询分支」最近几天的合并：谁合并丢了谁的修改、现在补回没有，以及强推。新的会私聊朱辉并发到提醒群，报过的不重复报。结果是「疑似」，请当事人确认。</p>
      <div id="bombInfo">正在读取…</div>
      <div class="row">
        <div><label>查最近几天（1 到 7）</label><input id="bombDays" class="hours" type="number" min="1" max="7" step="1"></div>
        <div><label>多久查一次（分钟，5 到 1440）</label><input id="bombMinutes" class="hours" type="number" min="5" max="1440" step="1"></div>
      </div>
      <div class="row" style="margin-top:10px">
        <button id="bombOn" class="primary">开启 / 保存</button>
        <button id="bombOff">关闭</button>
        <button id="bombNow">现在查一次</button>
      </div>
      <p id="bombMsg" class="meta"></p>
      <div class="sub">
        <label>提醒发到这些群（群 ID，一行一个，oc_ 开头；留空只私聊朱辉）。大豆要在群里才发得出去。</label>
        <textarea id="bombGroups" placeholder="oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" style="min-height:64px"></textarea>
        <div class="row" style="margin-top:8px"><button id="bombGroupsSave">保存群</button></div>
      </div>
      <div class="sub">
        <label>提醒忽略这些路径（一行一个前缀；匹配到的文件不发提醒，整条只剩忽略路径时整条不发。炸线看板仍显示完整结果。）</label>
        <textarea id="bombIgnore" placeholder="BattleLibrary/output/&#10;Assets/DataTable/" style="min-height:64px"></textarea>
        <div class="row" style="margin-top:8px"><button id="bombIgnoreSave">保存忽略路径</button></div>
      </div>
      <div class="sub">
        <label>最近报出的（完整历史看 <a id="bombBoardLink" href="#" target="_blank">炸线看板</a>）</label>
        <div id="bombRecent"></div>
      </div>
    </section>
  </div>

  <div class="tab" data-tab="review" hidden>
    <section id="sec-review">
      <h2>自检复盘</h2>
      <p class="meta">每天 0 点复盘名单里每个人前一天在 main 和 CBT2_bugfix 上的提交，结果放在内网网页 <a id="reviewLink" href="#" target="_blank"></a>（只读，打开就能看所有人）。填了 open_id 的会收到私聊摘要，没填的只出网页。git 作者名要和提交里的一致。</p>
      <div id="reviewInfo">正在读取…</div>
      <table style="margin-top:10px"><thead><tr><th>姓名</th><th>git 作者</th><th>私聊</th><th></th></tr></thead><tbody id="reviewSubs"></tbody></table>
      <div class="sub">
        <label>登记新的人</label>
        <div class="row" id="reviewAddRow">
          <input id="reviewName" type="text" placeholder="姓名，如 朱辉" style="max-width:140px">
          <input id="reviewAuthor" type="text" placeholder="git 作者，如 zhuhui" style="max-width:180px">
          <input id="reviewOpenId" type="text" placeholder="open_id（可留空）" style="max-width:260px">
          <button id="reviewAdd" class="primary">登记</button>
        </div>
        <label>补跑某一天</label>
        <div class="row">
          <input id="reviewDate" type="date" style="max-width:180px">
          <button id="reviewRun">补跑这一天</button>
        </div>
        <p id="reviewMsg" class="meta"></p>
      </div>
      <div class="sub">
        <label>每天汇总发到这些群（群 ID，一行一个，oc_ 开头；留空不发群）。补跑也会发一次。大豆要在群里才发得出去。</label>
        <textarea id="reviewGroups" placeholder="oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" style="min-height:64px"></textarea>
        <div class="row" style="margin-top:8px"><button id="reviewGroupsSave">保存群</button></div>
        <p id="reviewGroupsMsg" class="meta"></p>
      </div>
    </section>
  </div>

  <div class="tab" data-tab="chat" hidden>
    <section>
      <h2>试跑</h2>
      <p class="meta">输入同事会问的话。按真实规则答，不发到群里，也不写入纠正笔记。</p>
      <textarea id="question" placeholder="例如：现在几点"></textarea>
      <div class="row" style="margin-top:10px"><button id="preview" class="primary">试跑</button></div>
      <pre id="previewOut">还没有试跑。</pre>
    </section>
    <section id="sec-questions">
      <h2>用户问题</h2>
      <p class="meta">新的在前。文件里保留最近 2000 条，这里每页看 10 条。试跑不记。长回复默认收起。</p>
      <div class="pager" id="questionsPager"></div>
      <div class="table-wrap">
        <table><thead><tr><th>时间</th><th>来源</th><th>路由</th><th>问题</th></tr></thead><tbody id="questions"></tbody></table>
      </div>
      <div class="pager" id="questionsPagerBottom"></div>
    </section>
    <section id="sec-reviews">
      <h2>评价</h2>
      <p class="meta">大豆答完后由 Cursor 评价。对的只留在这里，错的还会写成下次要遵守的纠正。每页 10 条，新的在前。</p>
      <div class="pager" id="reviewsPager"></div>
      <div class="table-wrap">
        <table><thead><tr><th>时间</th><th>结论</th><th>路由</th><th>问题</th><th>评价</th></tr></thead><tbody id="reviews"></tbody></table>
      </div>
      <div class="pager" id="reviewsPagerBottom"></div>
    </section>
    <section id="sec-misses">
      <h2>答错记录</h2>
      <p class="meta">撤回、只说「不对」、同一路径又问一遍。标成已处理不会改大豆的规则。每页 10 条。</p>
      <div class="pager" id="missesPager"></div>
      <div class="table-wrap">
        <table><thead><tr><th>时间</th><th>原因</th><th>路由</th><th>问题</th><th></th></tr></thead><tbody id="misses"></tbody></table>
      </div>
      <div class="pager" id="missesPagerBottom"></div>
    </section>
    <section>
      <h2>纠正</h2>
      <p class="meta">答错后写下的事实，下次回答会遵守。</p>
      <table><thead><tr><th>问题</th><th>事实</th><th></th></tr></thead><tbody id="notes"></tbody></table>
    </section>
  </div>

  <div class="tab" data-tab="sites" hidden>
    <section>
      <h2>网站</h2>
      <p class="meta">大豆在这台机器上开的内网网页。同事要能打开，Windows 防火墙要放行对应端口（只放 10.x 内网）。新加的网站也会出现在这里。</p>
      <div class="sites" id="sites">正在读取…</div>
    </section>
    <section id="sec-wish">
      <h2>心愿墙管理</h2>
      <p class="meta">这里能改任何心愿，不受「只有许愿人/接的人」限制。删除不能恢复。</p>
      <div class="row wbar">
        <label class="switch"><input type="checkbox" id="wishAllow"><span></span>允许发布新心愿</label>
        <span id="wishStats" class="meta" style="margin:0"></span>
        <div class="spacer"></div>
        <select id="wishFilter"><option value="">全部状态</option><option value="open">待接</option><option value="doing">进行中</option><option value="done">已实现</option></select>
        <input id="wishSearch" type="text" placeholder="搜标题、说明或名字" style="max-width:220px">
      </div>
      <div class="table-wrap">
        <table style="margin-top:10px"><thead><tr><th>#</th><th>心愿</th><th>许愿人</th><th>岗位</th><th>状态</th><th>接的人</th><th>♥</th><th>许愿时间</th><th></th></tr></thead>
          <tbody id="wishRows"></tbody></table>
      </div>
    </section>
  </div>

  <div class="tab" data-tab="settings" hidden>
    <section id="sec-models">
      <h2>模型</h2>
      <p class="meta">大豆这次启动后实际在用的模型。次数是今天的，0 点清零；主模型失败时才会轮到备用模型。不显示 Key。</p>
      <div class="row">
        <div><label>聊天回答</label><select id="modelChat"></select></div>
        <div><label>看图</label><select id="modelVision"></select></div>
        <button id="modelSave" class="primary">试调并保存</button>
        <button id="modelReset">恢复默认</button>
      </div>
      <p class="meta" style="margin-top:6px">只能选智谱免费的 flash 模型。保存前会先各问一句，答不上来就不保存；保存后自动重启大豆。评价用的 composer-2.5 是 Cursor 的，这里不改。</p>
      <p id="modelMsg" class="meta"></p>
      <div class="table-wrap">
        <table><thead><tr><th>用途</th><th>模型</th><th>备用</th><th>今天成功 / 失败</th><th>最近成功</th><th>最近失败</th></tr></thead>
          <tbody id="modelRows"></tbody></table>
      </div>
      <p id="modelInfo" class="meta"></p>
    </section>
    <div class="grid2">
      <section>
        <h2>提醒</h2>
        <p class="meta">到点在原来的群里 @ 对方，只提醒一次。标了加急的，到点还会发应用内加急。</p>
        <table><thead><tr><th>时间</th><th>提醒谁</th><th>内容</th><th></th></tr></thead><tbody id="reminders"></tbody></table>
      </section>
      <section>
        <h2>人名</h2>
        <p class="meta">只显示职务和姓名。</p>
        <table><thead><tr><th>职务</th><th>姓名</th><th></th></tr></thead><tbody id="people"></tbody></table>
      </section>
    </div>
    <section>
      <h2>B 站登录</h2>
      <p class="meta">读 AI 字幕用。点「扫码登录」，用 B 站 App 扫一下并在手机上确认，登录信息自动存到这台机器，页面不回显。扫不了再手动粘贴 Cookie。</p>
      <div class="row">
        <button id="biliQrStart" class="primary">扫码登录</button>
        <input id="biliCookie" type="password" autocomplete="off" placeholder="或粘贴 Cookie / SESSDATA 后点保存" style="max-width:360px">
        <button id="biliSave">保存</button>
        <button id="biliClear">清除</button>
      </div>
      <img id="biliQr" alt="B 站登录二维码" hidden style="margin-top:12px;width:200px;height:200px;border-radius:8px;background:#fff">
      <p id="biliMsg" class="meta"></p>
    </section>
    <section>
      <h2>本地程序</h2>
      <p class="meta">同事要改自己电脑上的文件时，在他电脑上运行 local_agent.py --root 他的工程目录。大豆不改这台机器的工程。下表是最近交过文件的人。</p>
      <table><thead><tr><th>时间</th><th>目录</th><th>身份</th></tr></thead><tbody id="agents"></tbody></table>
    </section>
  </div>
</main>
</div>
</div>
<div id="toast" class="toast" hidden></div>
<script>
const $ = (id) => document.getElementById(id);
const TAB_TITLES = { overview: "概览", code: "代码", review: "复盘", chat: "对话", sites: "网站", settings: "设置" };
const TAB_ALIAS = { "sec-pull": "code", "sec-watch": "code", "sec-bomb": "code", "sec-review": "review", "sec-questions": "chat", "sec-reviews": "chat", "sec-misses": "chat", "sec-wish": "sites", "sec-models": "settings" };
function showTab() {
  let key = decodeURIComponent(location.hash.slice(1));
  const anchor = TAB_ALIAS[key] ? key : "";
  key = TAB_ALIAS[key] || key;
  if (!TAB_TITLES[key]) key = "overview";
  document.querySelectorAll(".tab").forEach((node) => { node.hidden = node.dataset.tab !== key; });
  document.querySelectorAll(".side nav a").forEach((node) => node.classList.toggle("on", node.dataset.tab === key));
  $("pageTitle").textContent = TAB_TITLES[key];
  document.title = TAB_TITLES[key] + " · 大豆后台";
  if (anchor && $(anchor)) $(anchor).scrollIntoView({ block: "start" });
  else window.scrollTo(0, 0);
}
window.addEventListener("hashchange", showTab);
showTab();
const PAGE_SIZE = 10;
const pages = { questions: 0, reviews: 0, misses: 0 };
const cache = { questions: [], reviews: [], misses: [], notes: [], people: [], reminders: [], agents: [] };
let pullEnabled = false;
let watchEnabled = false;
let listStamp = "";
let toastTimer = 0;
let refreshing = false;
function toast(text, bad) {
  const node = $("toast");
  node.textContent = text;
  node.classList.toggle("bad", !!bad);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 2200);
}
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}
function esc(text) {
  return String(text ?? "").replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
}
function clip(text) {
  const value = String(text ?? "").trim();
  if (!value) return "";
  if (value.length <= 80 && !value.includes("\n")) return "<div class='meta'>" + esc(value) + "</div>";
  return "<div class='meta clip'>" + esc(value) + "</div><button type='button' class='text' data-clip>展开</button>";
}
function pageCount(name) {
  return Math.max(1, Math.ceil(cache[name].length / PAGE_SIZE));
}
function clampPage(name) {
  const last = pageCount(name) - 1;
  if (pages[name] > last) pages[name] = last;
  if (pages[name] < 0) pages[name] = 0;
}
function sliceOf(name) {
  clampPage(name);
  const start = pages[name] * PAGE_SIZE;
  return cache[name].slice(start, start + PAGE_SIZE);
}
function paintPager(node, name) {
  if (!node) return;
  const current = pages[name] + 1;
  const count = pageCount(name);
  node.innerHTML = "<span class='meta'>" + cache[name].length + " 条</span>"
    + "<button type='button' data-page='" + name + "' data-dir='-1'" + (current <= 1 ? " disabled" : "") + ">上一页</button>"
    + "<span>" + current + " / " + count + "</span>"
    + "<button type='button' data-page='" + name + "' data-dir='1'" + (current >= count ? " disabled" : "") + ">下一页</button>";
}
function pager(name) {
  clampPage(name);
  paintPager($(name + "Pager"), name);
  paintPager($(name + "PagerBottom"), name);
}
function renderLists() {
  pager("questions");
  $("questions").innerHTML = sliceOf("questions").map((item) =>
    "<tr><td class='time'>" + esc(item.time) + "</td><td>" + esc(item.where) + "</td><td>" + esc(item.route)
    + "</td><td>" + esc(item.question) + clip(item.answer) + "</td></tr>"
  ).join("") || "<tr><td colspan='4'>没有问题</td></tr>";
  pager("reviews");
  $("reviews").innerHTML = sliceOf("reviews").map((item) =>
    "<tr><td class='time'>" + esc(item.time) + "</td><td class='" + (item.verdict === "对" ? "ok" : "bad") + "'>" + esc(item.verdict)
    + "</td><td>" + esc(item.route) + "</td><td>" + esc(item.question) + clip(item.answer)
    + "</td><td>" + clip(item.detail) + "</td></tr>"
  ).join("") || "<tr><td colspan='5'>还没有评价</td></tr>";
  pager("misses");
  $("misses").innerHTML = sliceOf("misses").map((item) =>
    "<tr class='" + (item.handled ? "handled" : "") + "'><td class='time'>" + esc(item.time) + "</td><td>" + esc(item.reason)
    + "</td><td>" + esc(item.route) + "</td><td>" + esc(item.question) + clip(item.preview) + "</td><td>"
    + (item.handled ? "已处理" : "<button type='button' data-miss='" + esc(item.id) + "'>标为已处理</button>") + "</td></tr>"
  ).join("") || "<tr><td colspan='5'>没有记录</td></tr>";
  $("notes").innerHTML = cache.notes.map((item, index) =>
    "<tr><td>" + esc(item.question) + "</td><td>" + esc(item.fact) + "</td><td><button type='button' data-note='" + index + "'>删除</button></td></tr>"
  ).join("") || "<tr><td colspan='3'>没有纠正</td></tr>";
  $("people").innerHTML = cache.people.map((item) =>
    "<tr><td>" + esc(item.role) + "</td><td>" + esc(item.name) + "</td><td><button type='button' data-role='" + esc(item.role) + "'>删除</button></td></tr>"
  ).join("") || "<tr><td colspan='3'>没有人名</td></tr>";
  $("agents").innerHTML = cache.agents.map((item) =>
    "<tr><td class='time'>" + esc(item.seen) + "</td><td>" + esc(item.root) + "</td><td>" + esc(item.who) + "</td></tr>"
  ).join("") || "<tr><td colspan='3'>还没有人从自己的电脑连过来</td></tr>";
  $("reminders").innerHTML = cache.reminders.map((item) =>
    "<tr><td class='time'>" + esc(item.when) + "</td><td>" + esc(item.who) + "</td><td>" + esc(item.task)
    + "</td><td><button type='button' data-reminder='" + esc(item.id) + "'>取消</button></td></tr>"
  ).join("") || "<tr><td colspan='4'>没有待提醒的</td></tr>";
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const data = await api("/api/status");
    const alive = data.running ? "<span class='ok'>在运行</span>" : "<span class='bad'>没在运行</span>";
    const link = data.connected ? "<span class='ok'>飞书已连接</span>" : "<span class='bad'>飞书未连接</span>";
    $("topStatus").innerHTML = alive + " · " + link;
    $("status").innerHTML = alive + " · " + link
      + "<div class='meta'>进程读的工程：" + esc(data.running_root || "还没有启动记录") + "</div>"
      + "<div class='meta'>文件内容所在分支：" + esc(data.file_branch || "读不到")
      + (data.dirty ? "，工作区有未提交改动" : "") + "</div>"
      + "<div class='meta'>最后一次回复：" + esc(data.last_reply || "没有") + "</div>";
    if (!$("start").dataset.busy) $("start").disabled = data.running;
    if (!$("stop").dataset.busy) $("stop").disabled = !data.running;
    if (document.activeElement !== $("root")) $("root").value = data.project_root || "";
    if (document.activeElement !== $("branches")) $("branches").value = (data.git_branches || []).join("\n");
    if (document.activeElement !== $("checkoutBranch") && data.file_branch && !$("checkoutBranch").value) {
      $("checkoutBranch").value = data.file_branch;
    }
    const pull = data.pull || {};
    pullEnabled = !!pull.enabled;
    if (document.activeElement !== $("pullHours")) $("pullHours").value = pull.hours || 5;
    const pullState = pull.enabled ? "<span class='ok'>定时开着</span>" : "<span class='bad'>定时关着</span>";
    const pullWhen = pull.enabled ? "，每 " + esc(pull.hours) + " 小时，下次 " + esc(pull.next_at || "还没排") : "";
    const pullQueued = pull.run_requested ? "，已排队，马上拉" : "";
    const lastResult = esc(pull.last_result || "").replace(/\n/g, "<br>");
    $("pullInfo").innerHTML = pullState + pullWhen + pullQueued
      + (pull.last_at ? "<div class='meta'>上次 " + esc(pull.last_at) + "</div>" : "")
      + (lastResult ? "<div class='meta'>" + lastResult + "</div>" : "");
    const watch = data.watch || {};
    watchEnabled = !!watch.enabled;
    if (document.activeElement !== $("watchDays")) $("watchDays").value = watch.days || 1;
    if (document.activeElement !== $("watchMinutes")) $("watchMinutes").value = watch.minutes || 30;
    if (document.activeElement !== $("watchRules")) $("watchRules").value = watch.rules_text || "Assets/Bundle/Shader .shader .hlsl";
    const watchState = watch.enabled ? "<span class='ok'>开着</span>" : "<span class='bad'>关着</span>";
    const watchWhen = watch.enabled ? "，看最近 " + esc(watch.days || 1) + " 天，每 " + esc(watch.minutes) + " 分钟查一次，下次 " + esc(watch.next_at || "还没排") : "";
    const watchQueued = watch.run_requested ? "，已排队，马上查" : "";
    const watchResult = esc(watch.last_result || "").replace(/\n/g, "<br>");
    $("watchInfo").innerHTML = watchState + watchWhen + watchQueued
      + (watch.last_at ? "<div class='meta'>上次 " + esc(watch.last_at) + "</div>" : "")
      + (watchResult ? "<div class='meta'>" + watchResult + "</div>" : "");
    $("indexInfo").textContent = data.index_running
      ? "正在重建…"
      : "文件" + (data.index_exists ? "在，" + data.index_rows + " 条，更新于 " + data.index_time : "还没有")
        + (data.index_error ? "。上次失败：" + data.index_error : "");
    if (!$("rebuild").dataset.busy) $("rebuild").disabled = data.index_running;
    $("log").textContent = data.log || "";
    const [reviews, questions, misses, notes, people] = await Promise.all([
      api("/api/reviews"), api("/api/questions"), api("/api/misses"), api("/api/notes"), api("/api/people")
    ]);
    const next = {
      questions: questions.items || [],
      reviews: reviews.items || [],
      misses: misses.items || [],
      notes: notes.items || [],
      people: people.items || [],
      reminders: data.reminders || [],
      agents: data.agents || []
    };
    const stamp = JSON.stringify(next);
    if (stamp !== listStamp) {
      listStamp = stamp;
      Object.assign(cache, next);
      renderLists();
    }
  } finally {
    refreshing = false;
  }
}
async function press(button, message, work) {
  if (button.dataset.busy) return;
  button.dataset.busy = "1";
  const old = button.textContent;
  button.disabled = true;
  button.textContent = "处理中…";
  let failed = false;
  try {
    await work();
  } catch (error) {
    failed = true;
    toast(error.message || "没完成", true);
  } finally {
    button.dataset.busy = "";
    if (button.isConnected) {
      button.textContent = old;
      button.disabled = false;
    }
  }
  try {
    await refresh();
  } catch (error) {
    toast(error.message || "刷新失败", true);
    return;
  }
  if (!failed) toast(message);
}
document.body.addEventListener("click", async (event) => {
  const target = event.target instanceof Element ? event.target.closest("button") : null;
  if (!target) return;
  if ("clip" in target.dataset) {
    const body = target.previousElementSibling;
    const open = body.classList.toggle("open");
    target.textContent = open ? "收起" : "展开";
    return;
  }
  if (target.dataset.page) {
    if (target.disabled) return;
    const name = target.dataset.page;
    const dir = Number(target.dataset.dir);
    if (dir !== 1 && dir !== -1) return;
    pages[name] += dir;
    renderLists();
    const bar = document.getElementById(name + "Pager");
    if (bar) bar.scrollIntoView({ block: "nearest" });
    return;
  }
  if (target.id === "start") {
    await press(target, "已启动", () => api("/api/start", { method: "POST" }));
    return;
  }
  if (target.id === "stop") {
    await press(target, "已停止", () => api("/api/stop", { method: "POST" }));
    return;
  }
  if (target.id === "save" || target.id === "saveRestart") {
    const restart = target.id === "saveRestart";
    await press(target, restart ? "已保存并重启" : "已保存", async () => {
      await api("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        project_root: $("root").value, git_branches: $("branches").value.split(/\n+/), restart
      }) });
      $("configMsg").textContent = restart ? "已保存并重启。" : "已保存。重启大豆后，飞书那边才用新目录和新分支。";
    });
    return;
  }
  if (target.id === "reviewAdd") {
    const name = $("reviewName").value.trim();
    const author = $("reviewAuthor").value.trim();
    if (!name || !author) {
      $("reviewMsg").innerHTML = "<span class='bad'>姓名和 git 作者都要填。</span>";
      toast("姓名和 git 作者都要填。", true);
      return;
    }
    $("reviewMsg").textContent = "正在核对 git 作者…";
    await press(target, "已登记", async () => {
      const data = await api("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        action: "add", name, git_author: author, open_id: $("reviewOpenId").value
      }) });
      $("reviewMsg").innerHTML = data.ok ? esc(data.message || "") : "<span class='bad'>" + esc(data.message || "没登记上") + "</span>";
      if (!data.ok) throw new Error(data.message || "没登记上");
      $("reviewName").value = ""; $("reviewAuthor").value = ""; $("reviewOpenId").value = "";
      paintReview(data);
    });
    return;
  }
  if (target.dataset.reviewRemove) {
    await press(target, "已移除", async () => {
      const data = await api("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        action: "remove", git_author: target.dataset.reviewRemove
      }) });
      $("reviewMsg").textContent = data.message || "";
      paintReview(data);
    });
    return;
  }
  if (target.id === "reviewRun") {
    await press(target, "已排队", async () => {
      const data = await api("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        action: "run", date: $("reviewDate").value
      }) });
      $("reviewMsg").textContent = data.message || "";
      if (!data.ok) throw new Error(data.message || "没排上");
    });
    return;
  }
  if (target.id === "biliQrStart") {
    await press(target, "二维码已生成", async () => {
      const data = await api("/api/bilibili", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "qr" }) });
      $("biliMsg").textContent = data.message || "";
      if (!data.ok) throw new Error(data.message || "没拿到二维码");
      $("biliQr").src = data.qr;
      $("biliQr").hidden = false;
      pollBiliQr();
    });
    return;
  }
  if (target.id === "biliSave" || target.id === "biliClear") {
    const clearing = target.id === "biliClear";
    await press(target, clearing ? "已清除" : "已保存", async () => {
      const data = await api("/api/bilibili", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        cookie: clearing ? "" : $("biliCookie").value
      }) });
      $("biliCookie").value = "";
      $("biliMsg").textContent = data.message || "";
      if (!clearing && !data.login) throw new Error(data.message || "没登录成功");
    });
    return;
  }
  if (target.id === "checkoutNow") {
    await press(target, "分支已切换", async () => {
      const data = await api("/api/checkout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        branch: $("checkoutBranch").value
      }) });
      $("checkoutMsg").textContent = data.message || "已切换。";
      if (!data.ok) throw new Error(data.message || "没切成功");
    });
    return;
  }
  if (target.id === "pullOn" || target.id === "pullOff" || target.id === "pullNow") {
    await press(target, target.id === "pullNow" ? "已开始拉" : "定时已更新", async () => {
      const data = await api("/api/pull", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        enabled: target.id === "pullOff" ? false : (target.id === "pullOn" ? true : pullEnabled),
        hours: Number($("pullHours").value || 5),
        run_now: target.id === "pullNow"
      }) });
      $("pullMsg").textContent = data.message || "已保存。";
    });
    return;
  }
  if (target.id === "watchOn" || target.id === "watchOff" || target.id === "watchNow") {
    await press(target, target.id === "watchNow" ? "已开始查" : "盯目录已更新", async () => {
      const data = await api("/api/watch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        enabled: target.id === "watchOff" ? false : (target.id === "watchOn" ? true : watchEnabled),
        minutes: Number($("watchMinutes").value || 30),
        days: Number($("watchDays").value || 1),
        rules: $("watchRules").value,
        run_now: target.id === "watchNow"
      }) });
      $("watchMsg").textContent = data.message || "已保存。";
    });
    return;
  }
  if (target.id === "bombGroupsSave") {
    await press(target, "已保存", async () => {
      const data = await api("/api/bomb", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        action: "groups", groups: $("bombGroups").value
      }) });
      $("bombMsg").innerHTML = data.ok ? esc(data.message || "") : "<span class='bad'>" + esc(data.message || "没保存上") + "</span>";
      if (!data.ok) throw new Error(data.message || "没保存上");
      paintBomb(data, true);
    });
    return;
  }
  if (target.id === "bombIgnoreSave") {
    await press(target, "已保存", async () => {
      const data = await api("/api/bomb", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        action: "ignore_paths", ignore_paths: $("bombIgnore").value
      }) });
      $("bombMsg").innerHTML = data.ok ? esc(data.message || "") : "<span class='bad'>" + esc(data.message || "没保存上") + "</span>";
      if (!data.ok) throw new Error(data.message || "没保存上");
      paintBomb(data, true);
    });
    return;
  }
  if (target.id === "reviewGroupsSave") {
    await press(target, "已保存", async () => {
      const data = await api("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        action: "groups", groups: $("reviewGroups").value
      }) });
      $("reviewGroupsMsg").innerHTML = data.ok ? esc(data.message || "") : "<span class='bad'>" + esc(data.message || "没保存上") + "</span>";
      if (!data.ok) throw new Error(data.message || "没保存上");
      paintReview(data, true);
    });
    return;
  }
  if (target.id === "bombOn" || target.id === "bombOff" || target.id === "bombNow") {
    const action = target.id === "bombOn" ? "on" : (target.id === "bombOff" ? "off" : "now");
    await press(target, action === "now" ? "已排队" : "炸线提醒已更新", async () => {
      const data = await api("/api/bomb", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        action, minutes: Number($("bombMinutes").value || 30), days: Number($("bombDays").value || 2)
      }) });
      $("bombMsg").textContent = data.message || "";
      paintBomb(data);
    });
    return;
  }
  if (target.id === "preview") {
    await press(target, "试跑完成", async () => {
      $("previewOut").textContent = "正在试跑…";
      const data = await api("/api/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: $("question").value }) });
      $("previewOut").textContent = "路由：" + (data.route || "没有") + "\n\n" + (data.answer || "没有回复");
    });
    return;
  }
  if (target.id === "rebuild") {
    await press(target, "开始重建索引", () => api("/api/index/rebuild", { method: "POST" }));
    return;
  }
  if (target.dataset.miss) {
    await press(target, "已标为已处理", () => api("/api/misses/handled", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: target.dataset.miss }) }));
    return;
  }
  if (target.dataset.note) {
    await press(target, "已删除", () => api("/api/notes/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ index: Number(target.dataset.note) }) }));
    return;
  }
  if (target.dataset.role) {
    await press(target, "已删除", () => api("/api/people/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: target.dataset.role }) }));
    return;
  }
  if (target.dataset.reminder) {
    await press(target, "已取消", () => api("/api/reminders/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: target.dataset.reminder }) }));
  }
});
refresh().catch((error) => toast(error.message || "读取失败", true));
api("/api/bilibili").then((data) => { $("biliMsg").textContent = data.message || ""; }).catch(() => {});
let biliQrTimer = 0;
function pollBiliQr() {
  clearTimeout(biliQrTimer);
  biliQrTimer = setTimeout(async () => {
    let data;
    try {
      data = await api("/api/bilibili", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "qr_poll" }) });
    } catch (error) {
      $("biliMsg").textContent = "查扫码状态失败：" + error.message;
      pollBiliQr();
      return;
    }
    $("biliMsg").textContent = data.message || "";
    if (data.state === "waiting" || data.state === "scanned") {
      pollBiliQr();
      return;
    }
    $("biliQr").hidden = true;
    $("biliQr").removeAttribute("src");
  }, 2000);
}
function paintReview(data, force) {
  if (data.groups && (force || !reviewGroupsLoaded) && document.activeElement !== $("reviewGroups")) {
    $("reviewGroups").value = data.groups.join("\n");
    reviewGroupsLoaded = true;
  }
  const subs = data.subs || [];
  $("reviewSubs").innerHTML = subs.map((item) => "<tr><td>" + esc(item.name) + "</td><td>" + esc(item.git_author) + "</td><td>"
    + (item.open_id ? "会私聊" : "只出网页") + "</td><td><button data-review-remove=\"" + esc(item.git_author) + "\">移除</button></td></tr>").join("")
    || "<tr><td colspan='4' class='meta'>名单是空的</td></tr>";
  if (data.web) { $("reviewLink").textContent = data.web; $("reviewLink").href = data.web; }
  if (data.state) {
    const state = data.state;
    $("reviewInfo").innerHTML = "<div class='meta'>上次：" + esc(state.last_at || "还没跑过") + " " + esc(state.last_result || "")
      + (state.run_requested ? "；已排队补跑 " + esc(state.run_requested) : "") + "</div>";
  }
}
let bombGroupsLoaded = false;
let bombIgnoreLoaded = false;
let reviewGroupsLoaded = false;
function paintBomb(data, force) {
  if ((force || !bombGroupsLoaded) && document.activeElement !== $("bombGroups")) {
    $("bombGroups").value = (data.groups || []).join("\n");
    bombGroupsLoaded = true;
  }
  if ((force || !bombIgnoreLoaded) && document.activeElement !== $("bombIgnore")) {
    $("bombIgnore").value = (data.ignore_paths || []).join("\n");
    bombIgnoreLoaded = true;
  }
  const state = data.enabled ? "<span class='ok'>开着</span>" : "<span class='bad'>关着</span>";
  const when = data.enabled ? "，看最近 " + esc(data.days) + " 天，每 " + esc(data.minutes) + " 分钟查一次，下次 " + esc((data.next_at || "马上").replace("T", " ").slice(0, 16)) : "";
  const queued = data.run_requested ? "，已排队，马上查" : "";
  $("bombInfo").innerHTML = state + when + queued
    + "<div class='meta'>分支：" + esc((data.branches || []).join("、")) + "；发给朱辉私聊 + " + esc((data.groups || []).length) + " 个群；忽略 " + esc((data.ignore_paths || []).length) + " 条路径；已报过 " + esc(data.told) + " 条</div>"
    + (data.last_at ? "<div class='meta'>上次 " + esc(data.last_at.replace("T", " ").slice(0, 16)) + "：" + esc(data.last_result || "") + "</div>" : "");
  if (document.activeElement !== $("bombDays")) $("bombDays").value = data.days || 2;
  if (document.activeElement !== $("bombMinutes")) $("bombMinutes").value = data.minutes || 30;
  $("bombRecent").innerHTML = (data.recent || []).map((item) =>
    "<div class='meta'>" + esc((item.at || "").replace("T", " ").slice(0, 16)) + (item.sent ? "" : " <span class='bad'>没发出去</span>") + "</div>"
    + clip(item.text)
  ).join("") || "<div class='meta'>还没有报过。</div>";
}
function loadBomb() { api("/api/bomb").then(paintBomb).catch(() => {}); }
loadBomb();
setInterval(loadBomb, 30000);
function loadReview() { api("/api/review").then(paintReview).catch(() => {}); }
loadReview();
setInterval(loadReview, 30000);
$("reviewAddRow").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); $("reviewAdd").click(); }
});
if (!$("reviewDate").value) {
  const day = new Date(Date.now() - 86400000);
  $("reviewDate").value = day.getFullYear() + "-" + String(day.getMonth() + 1).padStart(2, "0") + "-" + String(day.getDate()).padStart(2, "0");
}
setInterval(() => { refresh().catch(() => {}); }, 8000);
const WISH_STATUS = { open: "待接", doing: "进行中", done: "已实现" };
let wishData = { wishes: [], settings: {}, counts: {}, total: 0 };
let wishEditing = 0;
function escAttr(text) { return esc(text).replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
function paintSites(data) {
  $("sites").innerHTML = (data.sites || []).map((site) =>
    "<div class='site'><div class='site-top'><span class='site-ico' style='background:" + escAttr(site.color) + "'>" + esc(site.icon) + "</span>"
    + "<div><div class='site-name'>" + esc(site.name) + "</div><div class='site-port'>路径 " + esc(site.port) + "</div></div>"
    + "<span class='dot " + (site.online ? "up'>● 在线" : "down'>● 没起来") + "</span></div>"
    + "<div class='site-desc'>" + esc(site.desc) + "</div>"
    + "<div class='site-url'>" + esc(site.url) + "</div>"
    + "<div class='row'><a class='btnlink primary' target='_blank' href='" + escAttr(site.url) + "'>打开</a>"
    + "<a class='btnlink' target='_blank' href='" + escAttr(site.local) + "'>本机打开</a>"
    + (site.manage ? "<a class='btnlink' href='" + escAttr(site.manage) + "'>管理</a>" : "") + "</div></div>").join("") || "还没有网站。";
  const board = (data.sites || []).find((site) => site.manage === "#sec-bomb");
  if (board) { $("bombBoardLink").href = board.local; }
}
function loadSites() { api("/api/sites").then(paintSites).catch(() => { $("sites").textContent = "读取失败。"; }); }
function modelCell(name, models) {
  const item = models[name] || {};
  const fail = item.fail ? "<span class='bad'>" + item.fail + "</span>" : "0";
  return { count: (item.ok || 0) + " / " + fail, ok: item.last_ok_at ? shortTime(item.last_ok_at) : "—",
    fail: item.last_fail_at ? shortTime(item.last_fail_at) + (item.last_error ? "<div class='meta' style='margin:0'>" + esc(item.last_error) + "</div>" : "") : "—" };
}
function paintModels(data) {
  const models = data.models || {};
  const rows = (data.uses || []).map((use) => {
    if (!use.on) return "<tr><td>" + esc(use.use) + "</td><td colspan='5' class='meta'>没开：" + esc(use.off_reason) + "</td></tr>";
    const main = modelCell(use.model, models);
    const backups = (use.fallbacks || []).map((name) => {
      const cell = modelCell(name, models);
      return "<div><code>" + esc(name) + "</code> <span class='meta'>今天 " + cell.count + "</span></div>";
    }).join("") || "<span class='meta'>无</span>";
    return "<tr><td>" + esc(use.use) + "</td><td><code>" + esc(use.model) + "</code><div class='meta' style='margin:0'>" + esc(use.provider) + "</div></td>"
      + "<td>" + backups + "</td><td>" + main.count + "</td><td>" + main.ok + "</td><td>" + main.fail + "</td></tr>";
  }).join("");
  $("modelRows").innerHTML = rows || "<tr><td colspan='6' class='meta'>大豆还没启动过，启动后这里会显示。</td></tr>";
  const started = data.started_at ? "大豆启动于 " + shortTime(data.started_at) + (data.provider ? "，智谱接口 " + data.provider : "") : "";
  const changes = (data.history || []).slice(0, 5).map((item) => shortTime(item.at) + " "
    + ["chat", "vision"].filter((kind) => item[kind]).map((kind) => (kind === "chat" ? "聊天 " : "看图 ") + item[kind][0] + " → " + item[kind][1]).join("，")).join("；");
  $("modelInfo").textContent = started + (changes ? "。最近改动：" + changes : "");
  if (!modelBusy && data.choices && data.current) {
    [["modelChat", "chat"], ["modelVision", "vision"]].forEach(([id, kind]) => {
      if (document.activeElement === $(id)) return;
      $(id).innerHTML = data.choices[kind].map((name) => "<option value='" + escAttr(name) + "'" + (name === data.current[kind] ? " selected" : "") + ">" + esc(name)
        + (name === data.defaults[kind] ? "（默认）" : "") + "</option>").join("");
    });
  }
}
let modelBusy = false;
async function saveModels(body) {
  modelBusy = true;
  $("modelSave").disabled = true; $("modelReset").disabled = true;
  $("modelMsg").textContent = "正在试调…";
  try {
    const data = await api("/api/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("modelMsg").innerHTML = "<span class='" + (data.ok ? "" : "bad") + "'>" + esc(data.message || "") + "</span>";
    modelBusy = false;
    paintModels(data);
    setTimeout(loadModels, 12000);
  } catch (error) {
    $("modelMsg").innerHTML = "<span class='bad'>" + esc(error.message || "保存失败。") + "</span>";
  } finally {
    modelBusy = false;
    $("modelSave").disabled = false; $("modelReset").disabled = false;
  }
}
$("modelSave").addEventListener("click", () => saveModels({ chat: $("modelChat").value, vision: $("modelVision").value }));
$("modelReset").addEventListener("click", () => saveModels({ action: "reset" }));
function loadModels() { api("/api/models").then(paintModels).catch(() => { $("modelRows").innerHTML = "<tr><td colspan='6'>读取失败。</td></tr>"; }); }
function shortTime(value) { return String(value || "").slice(5, 16).replace("T", " "); }
function paintWish() {
  const data = wishData;
  $("wishAllow").checked = data.settings.allow_create !== false;
  const counts = data.counts || {};
  $("wishStats").textContent = "共 " + (data.total || 0) + " 个：待接 " + (counts.open || 0) + "，进行中 " + (counts.doing || 0) + "，已实现 " + (counts.done || 0);
  const key = $("wishSearch").value.trim().toLowerCase();
  const status = $("wishFilter").value;
  const rows = (data.wishes || []).filter((wish) => (!status || wish.status === status)
    && (!key || [wish.title, wish.detail, wish.author, wish.taker, wish.role].join(" ").toLowerCase().includes(key)));
  if (!rows.length) { $("wishRows").innerHTML = "<tr><td colspan='9' class='meta'>" + (data.total ? "没有符合条件的心愿。" : "还没有人许愿。") + "</td></tr>"; return; }
  $("wishRows").innerHTML = rows.map((wish) => {
    const main = "<tr data-id='" + wish.id + "'><td class='time'>#" + wish.id + "</td>"
      + "<td><b>" + esc(wish.title) + "</b>" + (wish.detail ? "<div class='meta clip' style='margin-top:2px'>" + esc(wish.detail) + "</div>" : "") + "</td>"
      + "<td>" + esc(wish.author) + "</td><td>" + esc(wish.role || "其他") + "</td><td><span class='wpill w-" + escAttr(wish.status) + "'>" + esc(WISH_STATUS[wish.status] || wish.status) + "</span></td>"
      + "<td>" + esc(wish.taker || "—") + "</td><td title='" + escAttr((wish.votes || []).join("、")) + "'>" + (wish.votes || []).length + "</td>"
      + "<td class='time'>" + esc(shortTime(wish.created_at)) + "</td>"
      + "<td style='white-space:nowrap'><button class='text' data-wish='edit'>编辑</button>　<button class='text bad' data-wish='delete'>删除</button></td></tr>";
    if (wishEditing !== wish.id) return main;
    const options = Object.keys(WISH_STATUS).map((value) => "<option value='" + value + "'" + (wish.status === value ? " selected" : "") + ">" + WISH_STATUS[value] + "</option>").join("");
    const roles = (data.roles || ["美术", "程序", "QA", "策划", "其他"]).map((value) => "<option" + ((wish.role || "其他") === value ? " selected" : "") + ">" + esc(value) + "</option>").join("");
    return main + "<tr class='wedit' data-id='" + wish.id + "'><td></td><td colspan='8'><div class='wgrid'>"
      + "<input type='text' data-f='title' maxlength='60' value='" + escAttr(wish.title) + "'>"
      + "<select data-f='role'>" + roles + "</select>"
      + "<select data-f='status'>" + options + "</select>"
      + "<input type='text' data-f='taker' maxlength='20' placeholder='接的人' value='" + escAttr(wish.taker || "") + "'>"
      + "<textarea data-f='detail' maxlength='800' placeholder='补充说明'>" + esc(wish.detail || "") + "</textarea></div>"
      + "<div class='row' style='margin-top:8px'><button class='primary' data-wish='save'>保存</button><button data-wish='cancel'>取消</button>"
      + "<span class='meta' style='margin:0'>改成「待接」会清掉接的人；「进行中」要填接的人。</span></div></td></tr>";
  }).join("");
}
function loadWish() { return api("/api/wish").then((data) => { wishData = data; if (!wishEditing) paintWish(); }).catch(() => {}); }
async function wishAction(payload) {
  const data = await api("/api/wish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  toast(data.message || (data.ok ? "好了" : "没成功"), !data.ok);
  if (data.ok) { wishData = data; wishEditing = 0; paintWish(); }
  return data.ok;
}
$("wishRows").addEventListener("click", async (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-wish]") : null;
  if (!button) return;
  event.stopPropagation();
  const id = Number(button.closest("tr").dataset.id);
  const kind = button.dataset.wish;
  if (kind === "edit") { wishEditing = id; paintWish(); return; }
  if (kind === "cancel") { wishEditing = 0; paintWish(); return; }
  if (kind === "delete") {
    const wish = wishData.wishes.find((item) => item.id === id);
    if (!confirm("删除 #" + id + "「" + (wish ? wish.title : "") + "」？删了不能恢复。")) return;
    await wishAction({ action: "delete", id });
    return;
  }
  if (kind === "save") {
    const row = button.closest("tr");
    const field = (name) => row.querySelector("[data-f='" + name + "']").value;
    button.disabled = true;
    try { await wishAction({ action: "update", id, title: field("title"), role: field("role"), detail: field("detail"), status: field("status"), taker: field("taker") }); }
    finally { button.disabled = false; }
  }
});
$("wishAllow").addEventListener("change", async () => {
  const value = $("wishAllow").checked;
  if (!(await wishAction({ action: "allow_create", value }))) $("wishAllow").checked = !value;
});
$("wishSearch").addEventListener("input", paintWish);
$("wishFilter").addEventListener("change", paintWish);
loadSites();
loadModels();
setInterval(loadModels, 30000);
loadWish();
setInterval(loadSites, 30000);
setInterval(loadWish, 30000);
</script>
</body>
</html>
"""


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
    config = {"project_root": DEFAULT_ROOT, "git_branches": ["main", "CBT2_bugfix"]}
    saved = read_json(CONFIG_PATH, {})
    if isinstance(saved, dict):
        root = saved.get("project_root")
        if isinstance(root, str) and root.strip():
            config["project_root"] = root.strip()
        branches = saved.get("git_branches")
        if isinstance(branches, list) and branches:
            config["git_branches"] = [str(item) for item in branches]
    if "main" not in config["git_branches"]:
        config["git_branches"].insert(0, "main")
    return config


def update_config(changes: dict) -> None:
    """只改给出的键，其他设置（例如模型）原样保留。"""
    saved = read_json(CONFIG_PATH, {})
    saved = saved if isinstance(saved, dict) else {}
    saved.update(changes)
    write_json(CONFIG_PATH, saved)


def load_models() -> dict:
    saved = read_json(CONFIG_PATH, {})
    models = saved.get("models") if isinstance(saved, dict) and isinstance(saved.get("models"), dict) else {}
    return {kind: models.get(kind) if models.get(kind) in choices else choices[0] for kind, choices in MODEL_CHOICES.items()}


def model_history() -> list:
    saved = read_json(CONFIG_PATH, {})
    history = saved.get("model_history") if isinstance(saved, dict) else None
    return [item for item in history if isinstance(item, dict)] if isinstance(history, list) else []


def bot_models_env() -> dict:
    return {MODEL_ENV[kind]: name for kind, name in load_models().items()}


def apply_models_here() -> None:
    """后台进程自己的试跑、补跑也用同一对模型；只改本进程，不动 Windows 环境变量。"""
    os.environ.update(bot_models_env())
    loaded = sys.modules.get("echo_bot")
    if loaded is not None:
        current = load_models()
        loaded.LLM_MODEL = current["chat"]
        loaded.LLM_VISION_MODEL = current["vision"]


def models_view() -> dict:
    import model_stats
    return dict(model_stats.view(), choices={kind: list(choices) for kind, choices in MODEL_CHOICES.items()},
                current=load_models(), defaults={kind: choices[0] for kind, choices in MODEL_CHOICES.items()},
                history=model_history()[:10])


def change_models(wanted: dict) -> dict:
    """先各试调一次，都答得上来才保存并重启大豆。返回 {"ok", "message"}。"""
    import echo_bot
    current = load_models()
    picked = {}
    for kind, choices in MODEL_CHOICES.items():
        name = str(wanted.get(kind) or current[kind])
        if name not in choices:
            return {"ok": False, "message": f"{name} 不在可选范围里。"}
        picked[kind] = name
    if picked == current:
        return {"ok": True, "message": "和现在一样，没有改。"}
    if not echo_bot.LLM_ENABLED:
        return {"ok": False, "message": "没有配 LLM_API_KEY，试调不了，没有保存。"}
    for kind, name in picked.items():
        if name == current[kind]:
            continue
        try:
            echo_bot.call_chat_once(name, MODEL_TEST_PROMPT, max_tokens=16)
        except Exception as error:
            reason = echo_bot.redact(f"{type(error).__name__}: {error}")[:160]
            return {"ok": False, "message": f"{name} 试调失败，没有保存，还在用 {current[kind]}。{reason}"}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    entry = {"at": stamp}
    for kind in MODEL_CHOICES:
        if picked[kind] != current[kind]:
            entry[kind] = [current[kind], picked[kind]]
    update_config({"models": picked, "model_history": ([entry] + model_history())[:MODEL_HISTORY_KEEP]})
    apply_models_here()
    stop_bot()
    time.sleep(1)
    message = start_bot()
    if message and message != "已经在运行。":
        return {"ok": False, "message": f"已保存，但大豆没重启起来：{message}"}
    return {"ok": True, "message": "试调通过，已保存并重启大豆，大约 10 秒后生效。"}


def watch_view() -> dict:
    saved = read_json(os.path.join(BOT_DIR, "watch.json"), {})
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
    saved = read_json(os.path.join(BOT_DIR, "pull_schedule.json"), {})
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
    saved = read_json(os.path.join(BOT_DIR, "agents.json"), {})
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
    saved = read_json(os.path.join(BOT_DIR, "reminders.json"), {})
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


def bot_processes() -> list:
    if sys.platform != "win32":
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True, errors="replace", timeout=6)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return []
        return [int(line.split(None, 1)[0]) for line in output.splitlines()
                if ("echo_bot.py" in line or "event +subscribe" in line) and line.split(None, 1)[0].isdigit()]
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
        if "echo_bot.py" in line or "event +subscribe" in line:
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
    subprocess.Popen(
        [python, "-u", os.path.join(BOT_DIR, "echo_bot.py")],
        cwd=BOT_DIR, env=env, stdout=out, stderr=err, creationflags=NO_WINDOW,
    )
    return ""


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


def clean_branches(raw) -> list:
    names = []
    for item in raw or []:
        name = str(item).strip()
        if BRANCH_NAME.fullmatch(name) and name not in names:
            names.append(name)
    if "main" not in names:
        names.insert(0, "main")
    if len(names) > 8:
        raise ValueError("分支最多 8 个。")
    return names


def rebuild_index() -> None:
    with index_lock:
        if index_state["running"]:
            return
        index_state["running"] = True
        index_state["error"] = ""
        index_state["started"] = time.time()

    def work():
        try:
            import echo_bot
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

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def mounted(self, method: str) -> bool:
        """/review、/wish、/bomb 开头的请求交给对应子站的 Handler 处理。"""
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
            module = __import__(module_name)
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

    def do_GET(self) -> None:
        if self.mounted("GET"):
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/review":
            self.send_json(review_view())
            return
        if path == "/api/bomb":
            import echo_bot
            self.send_json(echo_bot.bomb_view())
            return
        if path == "/api/sites":
            self.send_json(sites_view())
            return
        if path == "/api/wish":
            import wish_board
            self.send_json(wish_board.admin_view())
            return
        if path == "/api/models":
            self.send_json(models_view())
            return
        if path == "/api/bilibili":
            import echo_bot
            view = echo_bot.bilibili_login_status()
            self.send_json({key: view.get(key) for key in ("set", "login", "uname", "message")})
            return
        if path == "/api/status":
            now = time.time()
            with _status_lock:
                cached = _status_cache["data"]
                if cached is not None and now - _status_cache["at"] < 15:
                    self.send_json(cached)
                    return
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
            self.send_json(data)
            return
        if path == "/api/reviews":
            self.send_json({"items": load_reviews()})
            return
        if path == "/api/questions":
            self.send_json({"items": load_questions()})
            return
        if path == "/api/misses":
            self.send_json({"items": load_misses()})
            return
        if path == "/api/notes":
            self.send_json({"items": load_notes()})
            return
        if path == "/api/people":
            self.send_json({"items": load_people()})
            return
        self.send_json({"error": "没有这个页面"}, 404)

    def do_POST(self) -> None:
        if self.mounted("POST"):
            return
        path = urlparse(self.path).path
        data = self.read_body()
        try:
            if path == "/api/start":
                message = start_bot()
                if message:
                    self.send_json({"error": message}, 400)
                else:
                    self.send_json({"ok": True})
                return
            if path == "/api/stop":
                self.send_json({"stopped": stop_bot()})
                return
            if path == "/api/models":
                if data.get("action") == "reset":
                    wanted = {kind: choices[0] for kind, choices in MODEL_CHOICES.items()}
                else:
                    wanted = {kind: data.get(kind) for kind in MODEL_CHOICES}
                result = change_models(wanted)
                self.send_json(dict(models_view(), ok=result["ok"], message=result["message"]))
                return
            if path == "/api/wish":
                import wish_board
                result = wish_board.admin_apply(str(data.get("action") or ""), data)
                self.send_json(dict(wish_board.admin_view(), ok=result["ok"], message=result["message"]))
                return
            if path == "/api/bomb":
                import echo_bot
                action = str(data.get("action") or "")
                if action == "groups":
                    self.send_json(echo_bot.control_bomb("groups", groups_text=str(data.get("groups") or "")))
                    return
                if action == "ignore_paths":
                    self.send_json(echo_bot.control_bomb(
                        "ignore_paths", ignore_text=str(data.get("ignore_paths") or "")))
                    return
                try:
                    minutes = int(data.get("minutes") or 30)
                    days = int(data.get("days") or 2)
                except (TypeError, ValueError):
                    raise ValueError("间隔和天数要填整数。")
                self.send_json(echo_bot.control_bomb(action, minutes, days))
                return
            if path == "/api/review":
                import echo_bot
                action = str(data.get("action") or "")
                if action == "groups":
                    result = echo_bot.control_review_groups(str(data.get("groups") or ""))
                    self.send_json(dict(review_view(), ok=result["ok"], message=result["message"]))
                    return
                if action == "run":
                    result = echo_bot.request_review_run(str(data.get("date") or ""))
                    self.send_json(dict(review_view(), ok=result["ok"], message=result["message"]))
                    return
                if action not in ("add", "remove"):
                    raise ValueError("不认识的操作。")
                result = echo_bot.control_review_sub(action, str(data.get("name") or ""), str(data.get("git_author") or ""),
                                                     str(data.get("open_id") or ""))
                self.send_json(dict(review_view(), ok=result["ok"], message=result["message"]))
                return
            if path == "/api/bilibili":
                import echo_bot
                action = str(data.get("action") or "")
                if action in ("qr", "qr_poll"):
                    try:
                        result = echo_bot.bilibili_qr_start() if action == "qr" else echo_bot.bilibili_qr_poll()
                    except Exception as error:
                        result = {"ok": False, "state": "error", "message": f"连 B 站失败：{type(error).__name__}"}
                    self.send_json(result)
                    return
                view = echo_bot.control_bilibili_cookie(str(data.get("cookie") or ""))
                self.send_json({key: view.get(key) for key in ("set", "login", "uname", "message")})
                return
            if path == "/api/checkout":
                import echo_bot
                branch = str(data.get("branch") or "").strip()
                if not branch:
                    raise ValueError("填要切到的分支名。")
                view = echo_bot.control_checkout(branch)
                with _status_lock:
                    _status_cache["at"] = 0.0
                    _status_cache["data"] = None
                _branch_cache["at"] = 0.0
                self.send_json({"ok": bool(view.get("ok")), "branch": view.get("branch"), "message": view.get("message"), "file_branch": view.get("branch")})
                return
            if path == "/api/pull":
                import echo_bot
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
                self.send_json({"ok": True, "pull": view, "message": message})
                return
            if path == "/api/watch":
                import echo_bot
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
                self.send_json({"ok": True, "watch": view, "message": message})
                return
            if path == "/api/reminders/delete":
                import echo_bot
                item_id = str(data.get("id") or "")
                if not echo_bot.delete_reminder(item_id):
                    self.send_json({"error": "这条提醒不存在。"}, 400)
                    return
                with _status_lock:
                    _status_cache["at"] = 0.0
                    _status_cache["data"] = None
                self.send_json({"ok": True})
                return
            if path == "/api/config":
                root = str(data.get("project_root") or "").strip()
                if not os.path.isdir(os.path.join(root, "Assets")):
                    self.send_json({"error": "目录不存在，或下面没有 Assets。"}, 400)
                    return
                branches = clean_branches(data.get("git_branches"))
                update_config({"project_root": os.path.abspath(root), "git_branches": branches})
                if data.get("restart"):
                    stop_bot()
                    time.sleep(1)
                    message = start_bot()
                    if message and message != "已经在运行。":
                        self.send_json({"error": message}, 400)
                        return
                self.send_json({"ok": True})
                return
            if path == "/api/preview":
                text = str(data.get("text") or "").strip()
                if not text:
                    self.send_json({"error": "先写一句要试的话。"}, 400)
                    return
                import echo_bot
                echo_bot.apply_bot_config()
                result = echo_bot.preview_question(text)
                self.send_json(result)
                return
            if path == "/api/index/rebuild":
                with index_lock:
                    if index_state["running"]:
                        self.send_json({"error": "正在重建。"}, 400)
                        return
                rebuild_index()
                self.send_json({"ok": True})
                return
            if path == "/api/misses/handled":
                item_id = str(data.get("id") or "")
                payload = read_json(HANDLED_PATH, {"ids": []})
                ids = [str(item) for item in payload.get("ids") or []]
                if item_id and item_id not in ids:
                    ids.append(item_id)
                write_json(HANDLED_PATH, {"ids": ids[-500:]})
                self.send_json({"ok": True})
                return
            if path == "/api/notes/delete":
                index = data.get("index")
                items = load_notes()
                if not isinstance(index, int) or not 0 <= index < len(items):
                    self.send_json({"error": "这条纠正不存在。"}, 400)
                    return
                del items[index]
                write_json(NOTES_PATH, {"items": items})
                self.send_json({"ok": True})
                return
            if path == "/api/people/delete":
                role = str(data.get("role") or "")
                people = read_json(PEOPLE_PATH, {})
                if not isinstance(people, dict) or role not in people:
                    self.send_json({"error": "没有这个职务。"}, 400)
                    return
                people.pop(role, None)
                write_json(PEOPLE_PATH, people)
                self.send_json({"ok": True})
                return
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
            return
        self.send_json({"error": "没有这个操作"}, 404)


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


def sites_view() -> dict:
    import echo_bot
    import bomb_web
    import review_web
    import wish_web
    host = echo_bot.review_web_base().rsplit(":", 1)[0]
    registry = (
        ("自检复盘", "✓", "#3d5afe", "/review", "每天 0 点复盘名单里每个人前一天的提交，只读，所有人都能看。", "#sec-review"),
        ("心愿墙", "✨", "#ff6f91", "/wish", "大家许愿想要的功能，+1 排优先级，有人接下来实现。", "#sec-wish"),
        ("炸线看板", "⚡", "#e8590c", "/bomb", "疑似炸线和强推的历史：谁的修改丢了、补回没有。只读，保留 90 天。", "#sec-bomb"),
        ("合并确认", "⇄", "#176c4e", "/merge", "merge 冲突逐文件确认选边：上传会话 zip，或在本机检查工程并采集冲突。", ""),
    )
    sites = [{"name": name, "icon": icon, "color": color, "port": port, "desc": desc, "manage": manage,
              "url": f"{host}:{PORT}{port}/", "local": f"http://127.0.0.1:{PORT}{port}/", "online": True}
             for name, icon, color, port, desc, manage in registry]
    return {"sites": sites}


def review_view() -> dict:
    import echo_bot
    return {
        "subs": echo_bot.load_review_subs(),
        "state": echo_bot.load_review_json(echo_bot.REVIEW_STATE_PATH, {}),
        "web": echo_bot.review_web_base(),
        "groups": echo_bot.review_groups(),
    }


def start_review_web() -> None:
    import review_web
    try:
        server = review_web.ThreadingHTTPServer((review_web.HOST, review_web.PORT), review_web.ReviewHandler)
    except OSError as error:
        print(f"复盘网页没起来：{error}", flush=True)
        return
    threading.Thread(target=server.serve_forever, name="review-web", daemon=True).start()
    print(f"复盘网页 http://{review_web.HOST}:{review_web.PORT}", flush=True)


def start_wish_web() -> None:
    import wish_web
    try:
        server = wish_web.ThreadingHTTPServer((wish_web.HOST, wish_web.PORT), wish_web.WishHandler)
    except OSError as error:
        print(f"心愿墙没起来：{error}", flush=True)
        return
    threading.Thread(target=server.serve_forever, name="wish-web", daemon=True).start()
    print(f"心愿墙 http://{wish_web.HOST}:{wish_web.PORT}", flush=True)


def start_bomb_web() -> None:
    import bomb_web
    try:
        server = bomb_web.ThreadingHTTPServer((bomb_web.HOST, bomb_web.PORT), bomb_web.BombHandler)
    except OSError as error:
        print(f"炸线看板没起来：{error}", flush=True)
        return
    threading.Thread(target=server.serve_forever, name="bomb-web", daemon=True).start()
    print(f"炸线看板 http://{bomb_web.HOST}:{bomb_web.PORT}", flush=True)


def main() -> None:
    apply_models_here()
    server = QuietHTTPServer((HOST, PORT), Handler)
    print(f"大豆后台 http://{HOST}:{PORT}  （复盘 /review/ · 心愿墙 /wish/ · 炸线看板 /bomb/ · 合并确认 /merge/）", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
