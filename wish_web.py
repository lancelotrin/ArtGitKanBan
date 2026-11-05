"""大豆心愿墙：独立的内网网页（和复盘分开）。数据和规则在 wish_board.py，这里只出页面和 JSON 接口。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import wish_board

HOST = "0.0.0.0"
PORT = 8771
BODY_MAX = 8192

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>心愿墙 · 大豆</title>
<style>
:root { --bg:#f5f3ff; --card:#fff; --line:#ebe7f7; --text:#1f1b2e; --sub:#7a7490; --brand:#7c5cff; --brand-soft:#f0ebff;
  --hot:#ff6b5b; --hot-soft:#fff0ee; --doing:#f08c1a; --doing-soft:#fff4e5; --done:#1f9d6b; --done-soft:#e6f7ef; --heart:#ff4d79; }
* { box-sizing:border-box; }
body { margin:0; color:var(--text); font:14px/1.6 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  background:radial-gradient(1200px 600px at 10% -10%,#ffe9df 0,transparent 60%),radial-gradient(900px 500px at 95% 0,#e7e0ff 0,transparent 60%),var(--bg); min-height:100vh; }
button { font:inherit; cursor:pointer; }
.wrap { max-width:1240px; margin:0 auto; padding:0 24px; }
/* hero */
.hero { position:relative; overflow:hidden; border-radius:0 0 32px 32px;
  background:linear-gradient(120deg,#ff9a6b 0%,#ff6f91 38%,#9a6bff 78%,#6c7bff 100%); color:#fff; }
.hero .wrap { position:relative; display:flex; align-items:center; gap:24px; padding-top:34px; padding-bottom:86px; }
.hero h1 { margin:0; font-size:34px; letter-spacing:2px; font-weight:750; text-shadow:0 4px 18px rgba(80,20,120,.25); }
.hero p { margin:8px 0 0; opacity:.92; max-width:560px; }
.brand { display:inline-flex; align-items:center; gap:8px; font-size:13px; opacity:.9; margin-bottom:10px; }
.brand i { width:24px; height:24px; border-radius:8px; background:rgba(255,255,255,.22); display:grid; place-items:center; font-style:normal; font-weight:700; }
.hstats { display:flex; gap:10px; margin-top:18px; flex-wrap:wrap; }
.hstat { background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.28); backdrop-filter:blur(6px); border-radius:14px; padding:8px 14px; }
.hstat b { font-size:20px; margin-right:4px; } .hstat span { opacity:.85; font-size:12px; }
.art { margin-left:auto; flex:none; width:300px; height:180px; }
.star { position:absolute; color:#fff; opacity:.55; animation:tw 3.6s ease-in-out infinite; pointer-events:none; }
@keyframes tw { 0%,100% { opacity:.25; transform:scale(.8); } 50% { opacity:.9; transform:scale(1.1); } }
/* granted strip */
.granted { margin-top:-62px; position:relative; z-index:2; }
.ghead { display:flex; align-items:center; gap:10px; color:#fff; font-weight:650; margin-bottom:10px; }
.ghead button { margin-left:auto; border:none; background:rgba(255,255,255,.22); color:#fff; border-radius:999px; padding:3px 12px; font-size:12px; }
.gbox { position:relative; }
.grow { display:flex; gap:14px; overflow-x:auto; scroll-snap-type:x mandatory; scroll-behavior:smooth; padding:2px 2px 8px; scrollbar-width:none; }
.grow::-webkit-scrollbar { display:none; }
.gcard { flex:0 0 232px; scroll-snap-align:start; border-radius:16px; padding:14px 16px; min-height:104px; color:#fff; cursor:pointer;
  background:linear-gradient(140deg,#8f7bff,#6a5af9 60%,#5b6cff); box-shadow:0 10px 24px rgba(90,70,220,.25); display:flex; flex-direction:column; }
.gcard:nth-child(3n+2) { background:linear-gradient(140deg,#ff8fa3,#f8677f 60%,#e85d9a); box-shadow:0 10px 24px rgba(230,80,120,.22); }
.gcard:nth-child(3n) { background:linear-gradient(140deg,#4fc3b0,#25a7a0 60%,#2b8fd6); box-shadow:0 10px 24px rgba(30,150,160,.22); }
.gcard .t { font-weight:650; font-size:15px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.gcard .m { margin-top:auto; font-size:12px; opacity:.9; }
.garrow { position:absolute; top:38px; width:34px; height:34px; border-radius:50%; border:none; background:#fff; color:var(--brand);
  box-shadow:0 4px 14px rgba(40,20,90,.18); font-size:18px; line-height:1; }
.garrow.l { left:-14px; } .garrow.r { right:-14px; }
.gempty { color:#fff; opacity:.9; background:rgba(255,255,255,.16); border:1px dashed rgba(255,255,255,.5); border-radius:16px; padding:22px; text-align:center; }
/* toolbar */
.bar { position:sticky; top:0; z-index:5; margin:22px 0 16px; background:rgba(255,255,255,.86); backdrop-filter:blur(10px);
  border:1px solid var(--line); border-radius:18px; padding:10px 12px; display:flex; flex-wrap:wrap; align-items:center; gap:10px; box-shadow:0 8px 24px rgba(60,40,140,.06); }
.seg { display:flex; background:#f3f1fa; border-radius:12px; padding:3px; }
.seg button { border:none; background:none; color:var(--sub); padding:5px 12px; border-radius:9px; font-size:13px; }
.seg button.on { background:#fff; color:var(--text); font-weight:600; box-shadow:0 2px 6px rgba(40,20,90,.08); }
.chips { display:flex; gap:6px; }
.chip { border:1px solid var(--line); background:#fff; color:var(--sub); border-radius:999px; padding:4px 12px; font-size:13px; }
.chip b { margin-left:4px; font-weight:600; color:var(--text); }
.chip.on { background:var(--text); border-color:var(--text); color:#fff; } .chip.on b { color:#fff; }
.spacer { flex:1; }
.cta.big { background:#fff; color:#ff5f7e; padding:10px 22px; font-size:15px; box-shadow:0 10px 26px rgba(120,30,90,.28); }
.cta[disabled] { opacity:.7; cursor:not-allowed; transform:none; }
.cta { border:none; color:#fff; border-radius:999px; padding:8px 20px; font-weight:650;
  background:linear-gradient(120deg,#ff8a5b,#ff5f7e); box-shadow:0 8px 18px rgba(255,100,110,.35); transition:transform .15s; }
.cta:hover { transform:translateY(-1px); }
.ghost { border:1px solid #ffc2b8; color:var(--hot); background:#fff; border-radius:999px; padding:7px 16px; }
.ghost.on { background:var(--hot-soft); border-color:var(--hot); font-weight:600; }
.search { position:relative; flex:1; min-width:150px; } .search input { width:100%; height:36px; border:1px solid var(--line); border-radius:12px; padding:0 12px 0 32px; font:inherit; background:#fff; }
.search::before { content:"⌕"; position:absolute; left:11px; top:6px; color:var(--sub); font-size:16px; }
input:focus, textarea:focus { outline:none; border-color:var(--brand) !important; box-shadow:0 0 0 3px var(--brand-soft); }
.me { position:absolute; right:28px; top:22px; z-index:3; font-size:13px; color:#fff; background:rgba(255,255,255,.18);
  border:1px solid rgba(255,255,255,.3); border-radius:999px; padding:4px 14px; backdrop-filter:blur(6px); }
.me a { color:#fff; cursor:pointer; text-decoration:underline; text-underline-offset:3px; }
/* cards */
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(228px,1fr)); gap:16px; padding-bottom:48px; }
.card { position:relative; overflow:hidden; background:var(--card); border:1px solid var(--line); border-radius:18px; padding:16px; display:flex; flex-direction:column; gap:10px;
  min-height:178px; cursor:pointer; transition:transform .15s, box-shadow .15s, border-color .15s; animation:pop .35s ease both; }
.card:hover { transform:translateY(-3px); box-shadow:0 16px 34px rgba(70,40,160,.12); border-color:#ddd5f7; }
@keyframes pop { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
.who { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--sub); min-width:0; }
.who .nm { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:90px; }
.av { width:26px; height:26px; border-radius:50%; color:#fff; display:grid; place-items:center; font-size:12px; font-weight:650; flex:none; }
.tag { font-size:11px; padding:1px 8px; border-radius:6px; white-space:nowrap; }
.t-open { background:var(--brand-soft); color:var(--brand); } .t-doing { background:var(--doing-soft); color:var(--doing); } .t-done { background:var(--done-soft); color:var(--done); }
.t-mine { background:var(--hot-soft); color:var(--hot); }
.title { font-size:16px; font-weight:700; line-height:1.45; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; word-break:break-word; }
.desc { color:var(--sub); font-size:13px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; word-break:break-word; }
.foot { margin-top:auto; display:flex; align-items:center; gap:8px; }
.doer { font-size:12px; color:var(--doing); background:var(--doing-soft); border-radius:8px; padding:2px 8px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:120px; }
.doer.d { color:var(--done); background:var(--done-soft); }
.take { border:1px solid #d9d0ff; color:var(--brand); background:#fff; border-radius:8px; padding:2px 10px; font-size:12px; }
.take:hover { background:var(--brand); color:#fff; border-color:var(--brand); }
.heart { margin-left:auto; border:none; background:none; color:var(--sub); font-size:13px; padding:2px 4px; display:inline-flex; align-items:center; gap:4px; border-radius:8px; }
.heart .h { color:#c9c3d9; font-size:15px; transition:transform .15s; }
.heart:hover .h { transform:scale(1.2); color:var(--heart); }
.heart.on { color:var(--heart); } .heart.on .h { color:var(--heart); }
.heart[disabled] { cursor:default; } .heart[disabled]:hover .h { transform:none; color:#c9c3d9; }
.stamp { position:absolute; right:-6px; top:-4px; width:78px; height:78px; border:3px double rgba(31,157,107,.35); border-radius:50%;
  color:rgba(31,157,107,.45); display:grid; place-items:center; font-weight:800; font-size:14px; letter-spacing:2px; transform:rotate(-18deg); pointer-events:none; }
.empty { grid-column:1/-1; text-align:center; color:var(--sub); padding:60px 0; }
.empty .big { font-size:46px; }
/* modal */
.mask { position:fixed; inset:0; background:rgba(25,15,50,.42); backdrop-filter:blur(3px); display:none; align-items:center; justify-content:center; z-index:20; padding:20px; }
.mask.show { display:flex; animation:fade .18s ease; }
@keyframes fade { from { opacity:0; } to { opacity:1; } }
.modal { width:min(560px,100%); max-height:90vh; overflow:auto; background:#fff; border-radius:22px; box-shadow:0 30px 80px rgba(30,10,80,.3); animation:rise .22s ease; }
@keyframes rise { from { transform:translateY(14px); opacity:.6; } to { transform:none; opacity:1; } }
.mtop { padding:20px 22px 0; display:flex; align-items:flex-start; gap:10px; }
.mtop h2 { margin:0; font-size:19px; line-height:1.4; word-break:break-word; }
.x { margin-left:auto; border:none; background:#f3f1fa; width:30px; height:30px; border-radius:50%; color:var(--sub); flex:none; }
.mbody { padding:14px 22px 22px; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label { font-size:13px; font-weight:600; } .field label small { color:var(--sub); font-weight:400; margin-left:6px; }
.field input, .field textarea { border:1px solid var(--line); border-radius:12px; padding:10px 12px; font:inherit; color:var(--text); background:#fcfbff; }
.field textarea { min-height:110px; resize:vertical; }
.roles { display:flex; gap:8px; flex-wrap:wrap; }
.roles button { border:1px solid var(--line); background:#fcfbff; border-radius:999px; padding:6px 14px; font-size:13px; color:var(--text); transition:all .15s; }
.roles button:hover { border-color:var(--brand); color:var(--brand); }
.roles button.on { background:var(--brand); border-color:var(--brand); color:#fff; box-shadow:0 6px 14px rgba(110,80,250,.28); }
.role { font-size:11px; padding:1px 8px; border-radius:6px; white-space:nowrap; background:#f3f1fa; color:#5b5570; }
.r-美术 { background:#fff0f6; color:#c2347a; } .r-程序 { background:#eaf2ff; color:#2f62d6; } .r-QA { background:#e9f8f3; color:#17885f; }
.r-策划 { background:#fff5e6; color:#b8650c; }
.rsel { height:36px; border:1px solid var(--line); border-radius:12px; padding:0 10px; font:inherit; background:#fff; color:var(--text); }
.count { align-self:flex-end; font-size:12px; color:var(--sub); margin-top:-4px; }
.tip { background:#faf8ff; border:1px dashed #e0d8fb; border-radius:12px; padding:10px 12px; color:var(--sub); font-size:12px; margin-bottom:14px; }
.mfoot { display:flex; gap:10px; justify-content:flex-end; align-items:center; }
.btn2 { border:1px solid var(--line); background:#fff; border-radius:12px; padding:8px 16px; }
.btn2:hover { border-color:var(--brand); color:var(--brand); }
.btn2.warn:hover { border-color:var(--hot); color:var(--hot); }
.btnp { border:none; color:#fff; background:linear-gradient(120deg,#8b6bff,#6a5af9); border-radius:12px; padding:8px 18px; font-weight:600; box-shadow:0 8px 18px rgba(110,80,250,.3); }
.btnp.hot { background:linear-gradient(120deg,#ff8a5b,#ff5f7e); box-shadow:0 8px 18px rgba(255,100,110,.3); }
.dmeta { display:flex; align-items:center; gap:8px; color:var(--sub); font-size:12px; margin-top:8px; flex-wrap:wrap; }
.ddesc { white-space:pre-wrap; word-break:break-word; background:#faf9fe; border-radius:14px; padding:12px 14px; margin:14px 0; }
.ddesc.none { color:var(--sub); }
.tl { list-style:none; margin:0 0 16px; padding:0 0 0 16px; border-left:2px solid #eee9fb; }
.tl li { position:relative; padding:2px 0 10px 8px; font-size:13px; }
.tl li::before { content:""; position:absolute; left:-23px; top:8px; width:10px; height:10px; border-radius:50%; background:#d6cdfa; border:2px solid #fff; }
.tl li.on::before { background:var(--brand); } .tl li.ok::before { background:var(--done); }
.tl small { color:var(--sub); margin-left:6px; }
.fans { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:16px; }
.fan { display:inline-flex; align-items:center; gap:6px; background:#faf8ff; border-radius:999px; padding:2px 10px 2px 2px; font-size:12px; }
.fan .av { width:22px; height:22px; font-size:11px; }
.sect { font-size:13px; font-weight:650; margin:0 0 8px; }
/* toast */
.toast { position:fixed; left:50%; bottom:28px; transform:translateX(-50%) translateY(20px); background:#1f1b2e; color:#fff; padding:10px 18px; border-radius:12px;
  font-size:13px; opacity:0; pointer-events:none; transition:all .22s; z-index:30; box-shadow:0 12px 30px rgba(0,0,0,.2); }
.toast.show { opacity:1; transform:translateX(-50%) translateY(0); } .toast.bad { background:#d93a4a; }
@media (max-width:860px) { .art { display:none; } .search input { width:100%; } .search { flex:1; } }
</style>
</head>
<body>
<section class="hero">
  <span class="star" style="left:6%;top:22%;font-size:14px">✦</span>
  <span class="star" style="left:42%;top:14%;font-size:10px;animation-delay:1.1s">✦</span>
  <span class="star" style="left:58%;top:62%;font-size:12px;animation-delay:.5s">✦</span>
  <span class="star" style="left:88%;top:18%;font-size:16px;animation-delay:2s">✦</span>
  <div class="me" id="me"></div>
  <div class="wrap">
    <div>
      <div class="brand"><i>豆</i>大豆</div>
      <h1>心愿墙</h1>
      <p>想要什么功能、工具、分享？许个愿挂到墙上。想要的人点 ♥，有能力的人就接下来实现它。</p>
      <div class="hstats"><button class="cta big" id="openNew">✨ 发布心愿</button><span id="hstats" style="display:contents"></span></div>
    </div>
    <svg class="art" viewBox="0 0 300 180" aria-hidden="true">
      <defs>
        <linearGradient id="jar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity=".18"/></linearGradient>
        <radialGradient id="glow"><stop offset="0" stop-color="#fff6c2"/><stop offset="1" stop-color="#ffd36b" stop-opacity="0"/></radialGradient>
      </defs>
      <ellipse cx="150" cy="168" rx="92" ry="9" fill="#3a1f7a" opacity=".18"/>
      <circle cx="150" cy="104" r="70" fill="url(#glow)" opacity=".7"/>
      <rect x="120" y="34" width="60" height="14" rx="5" fill="#fff" opacity=".75"/>
      <path d="M112 52 h76 q14 0 14 16 v74 q0 22 -22 22 h-60 q-22 0 -22 -22 v-74 q0 -16 14 -16z" fill="url(#jar)" stroke="#fff" stroke-opacity=".8" stroke-width="2"/>
      <g fill="#fff3a8"><path d="M140 96 l4 9 10 1 -8 6 3 10 -9 -6 -9 6 3 -10 -8 -6 10 -1z"/>
        <path d="M168 120 l3 6 7 1 -5 4 2 7 -7 -4 -6 4 2 -7 -5 -4 7 -1z" opacity=".9"/>
        <path d="M128 132 l2 5 5 0 -4 3 2 5 -5 -3 -4 3 1 -5 -4 -3 5 0z" opacity=".8"/></g>
      <g fill="#fff" opacity=".9"><circle cx="232" cy="46" r="3"/><circle cx="62" cy="60" r="2"/><circle cx="250" cy="120" r="2"/></g>
      <path d="M236 70 q20 -10 30 6" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" opacity=".7"/>
      <text x="216" y="104" font-size="26" fill="#fff">✨</text><text x="58" y="120" font-size="22" fill="#fff">💡</text>
    </svg>
  </div>
</section>

<div class="wrap">
  <section class="granted" id="granted">
    <div class="ghead">🎉 已实现的心愿 <span id="gcount" style="opacity:.85;font-weight:400"></span><button id="gtoggle">收起</button></div>
    <div class="gbox" id="gbox">
      <button class="garrow l" data-d="-1" aria-label="往左">‹</button>
      <div class="grow" id="grow"></div>
      <button class="garrow r" data-d="1" aria-label="往右">›</button>
    </div>
  </section>

  <div class="bar">
    <div class="seg" id="sort"><button data-s="hot" class="on">最热</button><button data-s="new">最新</button></div>
    <div class="chips" id="chips"></div>
    <select class="rsel" id="roleFilter"><option value="">全部岗位</option><option>美术</option><option>程序</option><option>QA</option><option>策划</option><option>其他</option></select>
    <button class="ghost" id="mine">我的心愿</button>
    <div class="spacer"></div>
    <div class="search"><input id="q" type="search" placeholder="搜心愿或名字"></div>
  </div>

  <div class="grid" id="grid"></div>
</div>

<div class="mask" id="newMask"><div class="modal">
  <div class="mtop"><h2>✨ 发布一个心愿</h2><button class="x" data-close>✕</button></div>
  <div class="mbody">
    <div class="field"><label>你的名字<small>浏览器会记住，不用登录</small></label><input id="fName" maxlength="20" placeholder="例如 朱辉"></div>
    <div class="field"><label>你的岗位</label><div class="roles" id="fRole">
      <button type="button" data-r="美术">🎨 美术</button><button type="button" data-r="程序">💻 程序</button><button type="button" data-r="QA">🧪 QA</button>
      <button type="button" data-r="策划">📋 策划</button><button type="button" data-r="其他" class="on">✳️ 其他</button></div></div>
    <div class="field"><label>想要什么？<small>一句话说清楚</small></label><input id="fTitle" maxlength="60" placeholder="例如：复盘能一键导出成 Excel"><span class="count" id="cTitle">0/60</span></div>
    <div class="field"><label>补充说明<small>可选</small></label><textarea id="fDetail" maxlength="800" placeholder="用在什么场景？现在哪里不方便？做成什么样算实现？"></textarea><span class="count" id="cDetail">0/800</span></div>
    <div class="tip">心愿会公开挂在墙上，所有人都能看到、点 ♥ 和接下。没人接之前你可以撤回。</div>
    <div class="mfoot"><button class="btn2" data-close>取消</button><button class="btnp hot" id="submitNew">挂上心愿墙</button></div>
  </div>
</div></div>

<div class="mask" id="nameMask"><div class="modal" style="width:min(420px,100%)">
  <div class="mtop"><h2>先告诉大家你是谁</h2><button class="x" data-close>✕</button></div>
  <div class="mbody">
    <div class="field"><label>你的名字<small>中文、字母或数字，20 字以内</small></label><input id="nName" maxlength="20" placeholder="例如 朱辉"></div>
    <div class="mfoot"><button class="btn2" data-close>取消</button><button class="btnp" id="saveName">好的</button></div>
  </div>
</div></div>

<div class="mask" id="viewMask"><div class="modal" id="view"></div></div>
<div class="toast" id="toast"></div>

<script>
(function(){
  var COLORS=['#7c5cff','#ff6f91','#22a99b','#f08c1a','#4f7cff','#d65db1','#2f9e5b','#c7832a'];
  var STATUS={open:'待接',doing:'进行中',done:'已实现'};
  var state={wishes:[],sort:'hot',filter:'all',mine:false,q:'',role:''}, pending=null;
  function roleTag(r){ r=r||'其他'; return '<span class="role r-'+esc(r)+'">'+esc(r)+'</span>'; }
  function $(id){ return document.getElementById(id); }
  function esc(v){ return String(v==null?'':v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];}); }
  function color(n){ var h=0; for(var i=0;i<n.length;i++){ h=(h*31+n.charCodeAt(i))>>>0; } return COLORS[h%COLORS.length]; }
  function av(n){ return '<span class="av" style="background:'+color(n||'?')+'">'+esc((n||'?').slice(0,1))+'</span>'; }
  function when(t){ if(!t){return '';} var d=new Date(t), s=(Date.now()-d)/1000;
    if(s<60){return '刚刚';} if(s<3600){return Math.floor(s/60)+' 分钟前';} if(s<86400){return Math.floor(s/3600)+' 小时前';}
    if(s<86400*7){return Math.floor(s/86400)+' 天前';} return (d.getMonth()+1)+'月'+d.getDate()+'日'; }
  function me(){ return (localStorage.getItem('wishName')||'').trim(); }
  function toast(t,bad){ var el=$('toast'); el.textContent=t; el.className='toast show'+(bad?' bad':''); clearTimeout(el._t); el._t=setTimeout(function(){el.className='toast';},2400); }
  function open(id){ $(id).classList.add('show'); } function close(el){ el.classList.remove('show'); }
  document.querySelectorAll('.mask').forEach(function(m){ m.addEventListener('click',function(e){ if(e.target===m||e.target.hasAttribute('data-close')){ close(m); } }); });
  document.addEventListener('keydown',function(e){ if(e.key==='Escape'){ document.querySelectorAll('.mask.show').forEach(close); } });

  function load(){ return fetch('/api/wishes').then(function(r){return r.json();}).then(function(d){
    state.wishes=d.wishes||[]; state.allow=!(d.settings&&d.settings.allow_create===false);
    var b=$('openNew'); b.disabled=!state.allow; b.textContent=state.allow?'✨ 发布心愿':'⏸ 暂停发布中'; b.title=state.allow?'':'管理员暂停了发布，可以先给已有的心愿点 ♥';
    render(); }); }
  function act(payload){
    if(!me()){ pending=payload; $('nName').value=''; open('nameMask'); setTimeout(function(){$('nName').focus();},50); return Promise.resolve(false); }
    payload.name=me();
    return fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
      .then(function(r){return r.json();}).then(function(d){ toast(d.message||(d.ok?'好了':'没成功'),!d.ok); return d.ok? load().then(function(){return d;}) : false; })
      .catch(function(){ toast('网络出错了，再试一次',true); return false; });
  }
  $('saveName').addEventListener('click',function(){
    var n=$('nName').value.trim(); if(!/^[A-Za-z0-9_.\-\u4e00-\u9fff]{1,20}$/.test(n)){ toast('名字只能是中文、字母或数字，20 字以内',true); return; }
    localStorage.setItem('wishName',n); close($('nameMask')); render(); if(pending){ var p=pending; pending=null; act(p).then(refreshView); }
  });

  function hot(w){ return (w.votes||[]).length; }
  function render(){
    var ws=state.wishes, name=me();
    var c={all:ws.length,open:0,doing:0,done:0}; ws.forEach(function(w){ c[w.status]=(c[w.status]||0)+1; });
    $('hstats').innerHTML='<div class="hstat"><b>'+c.all+'</b><span>个心愿</span></div><div class="hstat"><b>'+c.doing+'</b><span>正在实现</span></div><div class="hstat"><b>'+c.done+'</b><span>已经实现</span></div>';
    $('chips').innerHTML=[['all','全部'],['open','待接'],['doing','进行中'],['done','已实现']].map(function(x){
      return '<button class="chip'+(state.filter===x[0]?' on':'')+'" data-f="'+x[0]+'">'+x[1]+'<b>'+(c[x[0]]||0)+'</b></button>'; }).join('');
    $('me').innerHTML= name ? '我是 <b>'+esc(name)+'</b> · <a id="rename">改名</a>' : '<a id="rename">填名字</a>';
    var done=ws.filter(function(w){return w.status==='done';}).sort(function(a,b){return (b.done_at||'').localeCompare(a.done_at||'');});
    $('gcount').textContent=done.length?('· '+done.length+' 个'):'';
    $('grow').innerHTML= done.length ? done.map(function(w){ return '<div class="gcard" data-id="'+w.id+'"><div class="t">'+esc(w.title)+'</div><div class="m">✓ '+esc(w.taker||w.author)+' 帮忙实现 · '+when(w.done_at)+'</div></div>'; }).join('')
      : '<div class="gempty" style="flex:1">还没有实现的心愿。第一个被实现的会出现在这里 🌟</div>';
    var row=$('grow'), wide=row.scrollWidth>row.clientWidth+4;
    document.querySelectorAll('.garrow').forEach(function(b){ b.style.display=wide?'':'none'; });
    var q=state.q.toLowerCase();
    var list=ws.filter(function(w){
      if(state.filter!=='all'&&w.status!==state.filter){return false;}
      if(state.role&&(w.role||'其他')!==state.role){return false;}
      if(state.mine&&!(name&&(w.author===name||w.taker===name||(w.votes||[]).indexOf(name)>=0))){return false;}
      if(q&&[w.title,w.detail,w.author,w.taker,w.role].join(' ').toLowerCase().indexOf(q)<0){return false;}
      return true;
    });
    list.sort(function(a,b){
      if(state.sort==='hot'){ var o={open:0,doing:1,done:2}; var d=(o[a.status]-o[b.status])||(hot(b)-hot(a)); if(d){return d;} }
      return b.id-a.id;
    });
    if(!list.length){
      $('grid').innerHTML='<div class="empty"><div class="big">'+(ws.length?'🔍':'🌠')+'</div>'+(ws.length?'没有符合条件的心愿':'墙上还空着，来许第一个心愿吧')+'</div>';
      return;
    }
    $('grid').innerHTML=list.map(function(w,i){
      var voted=name&&(w.votes||[]).indexOf(name)>=0, own=name&&w.author===name, left='';
      if(w.status==='open'){ left='<button class="take" data-act="take">我来接</button>'; }
      if(w.status==='doing'){ left='<span class="doer">🛠 '+esc(w.taker)+' 在做</span>'; }
      if(w.status==='done'){ left='<span class="doer d">✓ '+esc(w.taker||w.author)+' 实现</span>'; }
      return '<div class="card" data-id="'+w.id+'" style="animation-delay:'+Math.min(i*25,400)+'ms">'+(w.status==='done'?'<div class="stamp">已实现</div>':'')+
        '<div class="who">'+av(w.author)+'<span class="nm">'+esc(w.author)+'</span>'+roleTag(w.role)+'<span class="tag t-'+w.status+'">'+STATUS[w.status]+'</span>'+(own?'<span class="tag t-mine">我的</span>':'')+'</div>'+
        '<div class="title">'+esc(w.title)+'</div>'+(w.detail?'<div class="desc">'+esc(w.detail)+'</div>':'<div class="desc" style="opacity:.6">'+when(w.created_at)+'许下</div>')+
        '<div class="foot">'+left+'<button class="heart'+(voted?' on':'')+'" data-act="vote"'+(own?' disabled title="自己的心愿"':'')+'><span class="h">'+(voted?'♥':'♡')+'</span>'+hot(w)+' 人想要</button></div></div>';
    }).join('');
  }

  var viewing=0;
  function refreshView(){ if(viewing&&$('viewMask').classList.contains('show')){ showView(viewing); } }
  function showView(id){
    var w=state.wishes.filter(function(x){return x.id===id;})[0]; if(!w){ close($('viewMask')); return; }
    viewing=id; var name=me(), voted=name&&(w.votes||[]).indexOf(name)>=0, own=name&&w.author===name, btns=[];
    if(w.status==='open'&&own){ btns.push('<button class="btn2 warn" data-act="delete">撤回心愿</button>'); }
    if(w.status==='doing'&&name===w.taker){ btns.push('<button class="btn2 warn" data-act="drop">放弃</button>'); }
    if(w.status==='doing'&&name&&(name===w.taker||own)){ btns.push('<button class="btnp" data-act="done">标成已实现</button>'); }
    if(w.status==='open'){ btns.push('<button class="btnp" data-act="take">我来接</button>'); }
    if(!own&&w.status!=='done'){ btns.unshift('<button class="btn2'+(voted?' on':'')+'" data-act="vote" style="color:var(--heart)">'+(voted?'♥ 取消想要':'♡ 我也想要')+'</button>'); }
    var tl='<li class="on">'+esc(w.author)+' 许下心愿<small>'+when(w.created_at)+'</small></li>';
    if(w.taker){ tl+='<li class="on">'+esc(w.taker)+' 接下<small>'+when(w.taken_at)+'</small></li>'; }
    if(w.status==='done'){ tl+='<li class="ok">心愿实现 🎉<small>'+when(w.done_at)+'</small></li>'; }
    else if(w.status==='doing'){ tl+='<li>等待实现…</li>'; } else { tl+='<li>等人来接…</li>'; }
    var fans=(w.votes||[]).length? (w.votes||[]).map(function(n){return '<span class="fan">'+av(n)+esc(n)+'</span>';}).join('') : '<span style="color:var(--sub);font-size:12px">还没有人点 ♥</span>';
    $('view').innerHTML='<div class="mtop"><div><span class="tag t-'+w.status+'">'+STATUS[w.status]+'</span><h2 style="margin-top:6px">'+esc(w.title)+'</h2>'+
      '<div class="dmeta">'+av(w.author)+esc(w.author)+roleTag(w.role)+' · #'+w.id+' · '+when(w.created_at)+'</div></div><button class="x" data-close>✕</button></div>'+
      '<div class="mbody"><div class="ddesc'+(w.detail?'':' none')+'">'+(w.detail?esc(w.detail):'没有补充说明。')+'</div>'+
      '<div class="sect">进度</div><ul class="tl">'+tl+'</ul><div class="sect">♥ '+hot(w)+' 人想要</div><div class="fans">'+fans+'</div>'+
      '<div class="mfoot">'+btns.join('')+'</div></div>';
    open('viewMask');
  }
  $('view').addEventListener('click',function(e){
    var b=e.target.closest('[data-act]'); if(!b){return;} var a=b.dataset.act;
    if(a==='delete'&&!confirm('撤回这个心愿？')){return;} if(a==='drop'&&!confirm('放弃后心愿回到待接，确定？')){return;}
    if(a==='take'&&!confirm('确定接下这个心愿？接了大家会看到是你在做。')){return;}
    act({action:a,id:viewing}).then(function(d){ if(d&&a==='delete'){ close($('viewMask')); } else { refreshView(); } });
  });
  $('grid').addEventListener('click',function(e){
    var card=e.target.closest('.card'); if(!card){return;} var id=+card.dataset.id, b=e.target.closest('[data-act]');
    if(b){ e.stopPropagation(); if(b.disabled){return;} var a=b.dataset.act;
      if(a==='take'&&!confirm('确定接下这个心愿？接了大家会看到是你在做。')){return;}
      act({action:a,id:id}); return; }
    showView(id);
  });
  $('grow').addEventListener('click',function(e){ var c=e.target.closest('.gcard'); if(c){ showView(+c.dataset.id); } });
  document.querySelectorAll('.garrow').forEach(function(b){ b.addEventListener('click',function(){ $('grow').scrollBy({left:+b.dataset.d*500}); }); });
  $('gtoggle').addEventListener('click',function(){ var box=$('gbox'), hide=box.style.display!=='none'; box.style.display=hide?'none':''; this.textContent=hide?'展开':'收起'; });
  $('sort').addEventListener('click',function(e){ var b=e.target.closest('button'); if(!b){return;} state.sort=b.dataset.s;
    this.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===b);}); render(); });
  $('chips').addEventListener('click',function(e){ var b=e.target.closest('.chip'); if(b){ state.filter=b.dataset.f; render(); } });
  $('mine').addEventListener('click',function(){ if(!me()){ open('nameMask'); return; } state.mine=!state.mine; this.classList.toggle('on',state.mine); render(); });
  $('q').addEventListener('input',function(){ state.q=this.value.trim(); render(); });
  $('roleFilter').addEventListener('change',function(){ state.role=this.value; render(); });
  function pickRole(r){ $('fRole').querySelectorAll('button').forEach(function(b){ b.classList.toggle('on',b.dataset.r===r); }); }
  function chosenRole(){ var b=$('fRole').querySelector('button.on'); return b?b.dataset.r:'其他'; }
  $('fRole').addEventListener('click',function(e){ var b=e.target.closest('button'); if(b){ pickRole(b.dataset.r); } });
  $('me').addEventListener('click',function(e){ if(e.target.id==='rename'){ $('nName').value=me(); open('nameMask'); } });
  function counter(input,label,max){ input.addEventListener('input',function(){ $(label).textContent=input.value.length+'/'+max; }); }
  counter($('fTitle'),'cTitle',60); counter($('fDetail'),'cDetail',800);
  $('openNew').addEventListener('click',function(){ $('fName').value=me(); open('newMask'); setTimeout(function(){ (me()?$('fTitle'):$('fName')).focus(); },50); });
  $('submitNew').addEventListener('click',function(){
    var n=$('fName').value.trim(); if(!/^[A-Za-z0-9_.\-\u4e00-\u9fff]{1,20}$/.test(n)){ toast('先填名字：中文、字母或数字，20 字以内',true); return; }
    if($('fTitle').value.trim().length<2){ toast('心愿至少写 2 个字',true); return; }
    localStorage.setItem('wishName',n);
    var btn=this; btn.disabled=true;
    act({action:'create',role:chosenRole(),title:$('fTitle').value,detail:$('fDetail').value}).then(function(d){
      btn.disabled=false; if(d){ $('fTitle').value=''; $('fDetail').value=''; pickRole('其他'); $('cTitle').textContent='0/60'; $('cDetail').textContent='0/800'; close($('newMask')); }
    });
  });
  load(); setInterval(function(){ if(!document.querySelector('.mask.show')){ load(); } },30000);
})();
</script>
</body>
</html>
"""


class WishHandler(BaseHTTPRequestHandler):
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
        elif path == "/api/wishes":
            self.send_json({"wishes": wish_board.list_wishes(), "settings": wish_board.settings()})
        else:
            self.send_body("没有这页。".encode("utf-8"), "text/plain; charset=utf-8", 404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/action":
            self.send_json({"ok": False, "message": "没有这个接口。"}, 404)
            return
        if not str(self.headers.get("Content-Type") or "").startswith("application/json"):
            self.send_json({"ok": False, "message": "要用 JSON 提交。"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > BODY_MAX:
            self.send_json({"ok": False, "message": "内容太长了。"}, 413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            self.send_json({"ok": False, "message": "提交的内容看不懂。"}, 400)
            return
        self.send_json(wish_board.apply(str(payload.get("action") or ""), payload, self.client_address[0]))


def serve() -> None:
    ThreadingHTTPServer((HOST, PORT), WishHandler).serve_forever()


if __name__ == "__main__":
    serve()
