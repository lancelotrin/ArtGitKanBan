const $ = (s) => document.querySelector(s);
let labels = {ours: '当前分支', theirs: '合入分支', both: '两边都要'};
let state, busy = false, needsRender = false;
const drafts = new Map(), expanded = new Set();
try { $('#reviewer').value = localStorage.getItem('portable-merge-review-name') || ''; } catch {}
const escape = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function reviewer() { return $('#reviewer').value.trim(); }
function votesFor(id) { return state.votes.filter(v => v.file_id === id); }
function notify(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(notify.timer); notify.timer = setTimeout(() => $('#toast').hidden = true, 3500); }
function branch(c, name) { return `<div class="branch"><div class="branch-name">${name}</div>${c ? `<div class="commit"><span class="sha">${escape(c.sha.slice(0,11))}</span> · ${escape(c.author)}</div><div class="commit">${escape(c.subject)}</div><div class="meta">${escape(c.at)}${c.committer !== c.author ? ' · 提交人 '+escape(c.committer) : ''}</div>` : '<div class="meta">该分支无同路径历史，需核对删除或重命名</div>'}</div>`; }
function render() {
  if (!state) return;
  const files = state.snapshot.files, reviewed = files.filter(f => votesFor(f.id).length);
  const mine = state.votes.filter(v => v.reviewer === reviewer());
  $('#stats').innerHTML = [[files.length,'冲突文件'],[reviewed.length,'已选择'],[files.length-reviewed.length,'尚未选择'],[state.votes.filter(v => v.choice === 'both').length,'两边都要'],[mine.length,'我最后修改']].map(([n,t]) => `<div class="stat"><strong>${n}</strong><span>${t}</span></div>`).join('');
  const query = $('#search').value.toLowerCase(), category = $('#category').value, status = $('#status').value;
  const visible = files.filter(f => {
    const votes = votesFor(f.id), own = votes.some(v => v.reviewer === reviewer());
    if (category && f.category !== category) return false;
    if (status === 'pending' && votes.length || status === 'reviewed' && !votes.length || status === 'mine' && !own) return false;
    return !query || `${f.path} ${JSON.stringify(f.ours_history)} ${JSON.stringify(f.theirs_history)}`.toLowerCase().includes(query);
  }).sort((a,b) => {
    const pending = Number(!!votesFor(a.id).length) - Number(!!votesFor(b.id).length);
    const folder = p => p.includes('/') ? p.slice(0,p.lastIndexOf('/')) : '';
    return pending || folder(a.path).localeCompare(folder(b.path),'zh-CN',{numeric:true}) || a.path.localeCompare(b.path,'zh-CN',{numeric:true});
  });
  $('#count').textContent = `显示 ${visible.length} / ${files.length} 个文件`;
  $('#files').innerHTML = visible.map(f => {
    const votes = votesFor(f.id), own = votes[0];
    const badge = own ? labels[own.choice] : '尚未选择';
    return `<article class="card" data-id="${f.id}"><div class="card-top"><span class="number">${String(f.number).padStart(3,'0')}</span><div class="heading"><div class="filename">${escape(f.path.split('/').pop())}</div><div class="path">${escape(f.path)}</div><span class="badge">${escape(f.category)}</span></div><span class="badge ${votes.length?'good':''}">${escape(badge)}</span></div><div class="columns">${branch(f.ours,escape(labels.ours))}${branch(f.theirs,escape(labels.theirs))}</div><p class="advice">${escape(f.advice)}</p><div class="decision"><div class="vote-row">${Object.entries(labels).map(([key,label])=>`<button class="choice ${own?.choice===key?'active':''}" data-choice="${key}" aria-pressed="${own?.choice===key}">${escape(label)}</button>`).join('')}<input class="note" maxlength="2000" aria-label="选择备注" placeholder="备注（选填），输入后点击分支按钮保存" value="${escape(drafts.has(f.id)?drafts.get(f.id):own?.comment||'')}"><button class="clear" data-choice="clear">清除当前选择</button></div><div class="votes">${votes.length?votes.map(v=>`<span class="vote" title="${escape(new Date(v.updated_at).toLocaleString())}">最后修改：${escape(v.reviewer)} → ${escape(labels[v.choice])}${v.comment?' · '+escape(v.comment):''} · ${escape(new Date(v.updated_at).toLocaleString())}</span>`).join(''):'尚未选择，等待相关同事确认。'}</div></div><details ${expanded.has(f.id)?'open':''}><summary>查看双方文件差异${f.binary?'（二进制）':''}</summary><pre>${escape(f.diff || '无可显示的文本差异，请结合对应资源和提交确认。')}</pre></details></article>`;
  }).join('') || '<div class="empty">没有符合筛选条件的文件</div>';
  document.querySelectorAll('details').forEach(d=>d.addEventListener('toggle',()=>{const id=d.closest('.card').dataset.id;d.open?expanded.add(id):expanded.delete(id);}));
}
async function load() {
  if (busy) return;
  try {
    const response = await fetch(state ? 'api/votes' : 'api/state'); if(!response.ok) throw Error('服务返回 '+response.status);
    const payload = await response.json();
    if (state && payload.snapshot_id !== state.snapshot.id) { location.reload(); return; }
    const next = state ? {snapshot:state.snapshot,votes:payload.votes} : payload;
    const first = !state, changed = first || JSON.stringify(state.votes) !== JSON.stringify(next.votes); state = next;
    if(first){ labels = {ours:state.snapshot.ours_label, theirs:state.snapshot.theirs_label,both:'两边都要'}; $('#date').textContent = '分支合并 · '+state.snapshot.date; $('#category').innerHTML += [...new Set(state.snapshot.files.map(f=>f.category))].map(c=>`<option>${escape(c)}</option>`).join(''); document.title='合并确认 · '+labels.ours+' ← '+labels.theirs; $('#snapshot').textContent=`${labels.ours} ${state.snapshot.ours.slice(0,11)} ↔ ${labels.theirs} ${state.snapshot.theirs.slice(0,11)} · 固定评审快照`; }
    $('#connection').textContent='● 已连接 · '+new Date().toLocaleTimeString(); $('#error').hidden=true;
    needsRender ||= changed;
    if (needsRender && !document.activeElement?.classList.contains('note')) { render(); needsRender = false; }
  } catch(e) { $('#connection').textContent='● 连接中断'; $('#error').textContent='同步失败：'+e.message+'。请确认主机服务正在运行；当前未保存的选择不会自动提交。';$('#error').hidden=false; }
}
$('#files').addEventListener('input',e=>{if(e.target.classList.contains('note'))drafts.set(e.target.closest('.card').dataset.id,e.target.value);});
$('#files').addEventListener('click',async e=>{
  const button=e.target.closest('button[data-choice]'); if(!button||busy)return;
  if(!reviewer()){notify('请先填写姓名 / Git 账号');$('#reviewer').focus();return;}
  const card=button.closest('.card'),id=card.dataset.id;
  busy=true;card.querySelectorAll('button').forEach(b=>b.disabled=true);
  try {
    const response=await fetch('api/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshot:state.snapshot.id,file_id:id,reviewer:reviewer(),choice:button.dataset.choice,comment:card.querySelector('.note').value})});
    const result=await response.json();if(!response.ok)throw Error(result.error||'保存失败');
    drafts.delete(id);notify(button.dataset.choice==='clear'?'已清除当前选择':'已保存为最新选择，所有人可见');
  } catch(e){notify(e.message);} finally{busy=false;await load();card.querySelectorAll('button').forEach(b=>b.disabled=false);}
});
$('#reviewer').addEventListener('change',()=>{try{localStorage.setItem('portable-merge-review-name',reviewer());}catch{}drafts.clear();render();});
['search','category','status'].forEach(id=>$('#'+id).addEventListener(id==='search'?'input':'change',render));
$('#refresh').addEventListener('click',load);load();setInterval(load,10000);
if (location.pathname.includes('/s/')) $('#back').hidden = false;
