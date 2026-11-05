import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { Modal } from "../../components/Modal";
import { ToastProvider, useToast } from "../../components/Toast";
import {
  feishuChat, NO_DEPARTMENT, useAnalyze, useDeleteQuestion, useHit, usePeople, useQuestions, useRemoveProfile,
  useSaveProfile, useSaveQuestion, type Person, type PeopleList, type Question, type QuestionDraft,
} from "./api";
import "./help.css";

export default function HelpBoard() {
  useEffect(() => {
    document.title = "热心市民 · 大豆";
  }, []);
  return (
    <div className="help-page">
      <ToastProvider>
        <Board />
      </ToastProvider>
    </div>
  );
}

/** 输入停一会儿再查问题库，免得每个字都发请求。 */
function useDebounced(value: string, ms = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

const splitWords = (text: string) => text.split(/[,，、\s]+/).map((w) => w.trim()).filter(Boolean);

type Tab = "people" | "questions";
type EditingQuestion = { id: number; draft: QuestionDraft } | null;

function Board() {
  const peopleQuery = usePeople();
  const directory = peopleQuery.data;
  const byId = useMemo(() => new Map((directory?.people ?? []).map((p) => [p.id, p])), [directory]);
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "questions" ? "questions" : "people";
  const setTab = (next: Tab) => setParams((prev) => {
    const p = new URLSearchParams(prev);
    if (next === "people") p.delete("tab"); else p.set("tab", next);
    return p;
  }, { replace: true });

  const [problem, setProblem] = useState("");
  const typed = useDebounced(problem.trim());
  const matches = useQuestions(typed, typed.length >= 2);
  const analyze = useAnalyze();
  const [editingProfile, setEditingProfile] = useState<Person | "new" | null>(null);
  const [editingQuestion, setEditingQuestion] = useState<EditingQuestion>(null);

  const me = directory ? byId.get(directory.me) : undefined;
  const helperCount = directory?.people.filter((p) => p.helper).length ?? 0;
  const bankMatches = typed.length >= 2 ? matches.data?.questions ?? [] : [];

  const ask = () => {
    if (problem.trim().length < 2) return;
    analyze.mutate(problem.trim());
  };
  const saveToBank = () => {
    const result = analyze.data;
    setEditingQuestion({
      id: 0,
      draft: {
        title: problem.trim().slice(0, 100), keywords: [], answer: result?.tips ?? "",
        department: result?.department ?? "", helpers: result?.picks.map((p) => p.person.id) ?? [],
      },
    });
  };

  return (
    <>
      <header className="hero">
        <div className="wrap">
          <div className="hero-text">
            <h1>🙋 热心市民</h1>
            <p>遇到问题别干着急：说说怎么了，先翻常见问题库，再让 AI 帮你判断该找哪个部门的谁。</p>
            <div className="hstats">
              <span><b>{helperCount}</b>位热心市民</span>
              <span><b>{directory?.departments.length ?? 0}</b>个部门</span>
              <button className="hbtn" onClick={() => me && setEditingProfile(me)} disabled={!me}>
                {me?.helper ? "✏️ 改我能帮什么" : "🙌 我也来当热心市民"}
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="wrap">
        <section className="ask card">
          <label htmlFor="problem">遇到什么问题了？</label>
          <textarea id="problem" value={problem} maxLength={2000} rows={3}
            onChange={(e) => setProblem(e.target.value)}
            onKeyDown={(e) => (e.metaKey || e.ctrlKey) && e.key === "Enter" && ask()}
            placeholder="例如：Unity 打包到一半报 Gradle 错误；SVN 提交提示被锁；新电脑装不上 Spine……说得越具体越好" />
          <div className="ask-bar">
            <span className="sub">{bankMatches.length ? `问题库里有 ${bankMatches.length} 条可能对得上 ↓` : "边打字边查问题库；⌘/Ctrl + Enter 直接问 AI"}</span>
            <button className="cta" disabled={problem.trim().length < 2 || analyze.isPending} onClick={ask}>
              {analyze.isPending ? "AI 正在分析…" : "✦ AI 帮我找人"}
            </button>
          </div>

          {bankMatches.length > 0 && (
            <div className="matches">
              <h3>📚 问题库里可能对得上</h3>
              {bankMatches.map((q) => <QuestionCard key={q.id} q={q} byId={byId} compact />)}
            </div>
          )}

          {analyze.isPending && <div className="ai-card thinking">AI 在看通讯录、问题库和知识库，可能要十几秒……</div>}
          {analyze.data && !analyze.isPending && (
            <div className="ai-card">
              <div className="ai-head">
                <h3>✦ AI 的建议</h3>
                {analyze.data.model && <span className="sub">{analyze.data.model}</span>}
              </div>
              {analyze.data.summary && <p className="ai-summary">{analyze.data.summary}</p>}
              {analyze.data.department && <p>建议找：<span className="dept-tag">{analyze.data.department}</span></p>}
              {analyze.data.picks.length > 0 ? (
                <div className="people-grid">
                  {analyze.data.picks.map((pick) => (
                    <PersonCard key={pick.person.id} p={pick.person} reason={pick.reason} />
                  ))}
                </div>
              ) : <p className="sub">通讯录里没挑到合适的人，按上面的部门去问问，或在下面按部门翻翻。</p>}
              {analyze.data.tips && (<><h4>找人之前可以先试试</h4><p className="pre">{analyze.data.tips}</p></>)}
              <div className="ai-foot">
                <span className="sub">解决了？把它记进问题库，下次别人就能直接找到人。</span>
                <button className="btn2" onClick={saveToBank}>＋ 加进问题库</button>
              </div>
            </div>
          )}
        </section>

        <div className="tabs">
          <button className={tab === "people" ? "on" : ""} onClick={() => setTab("people")}>👥 按部门找人</button>
          <button className={tab === "questions" ? "on" : ""} onClick={() => setTab("questions")}>📚 常见问题库</button>
        </div>

        {peopleQuery.error && <p className="error">{peopleQuery.error.message}</p>}
        {directory && tab === "people" && (
          <PeopleTab directory={directory} onEdit={setEditingProfile} />
        )}
        {directory && tab === "questions" && (
          <QuestionsTab directory={directory} byId={byId}
            onEdit={(q) => setEditingQuestion(q ? { id: q.id, draft: q } : { id: 0, draft: emptyQuestion })} />
        )}
      </main>

      {editingProfile && directory && (
        <ProfileModal target={editingProfile} self={editingProfile !== "new" && editingProfile.id === directory.me}
          admin={directory.admin} departments={directory.departments} onClose={() => setEditingProfile(null)} />
      )}
      {editingQuestion && directory && (
        <QuestionModal editing={editingQuestion} directory={directory} onClose={() => setEditingQuestion(null)} />
      )}
    </>
  );
}

const emptyQuestion: QuestionDraft = { title: "", keywords: [], answer: "", department: "", helpers: [] };

function Avatar({ p }: { p: Person }) {
  return p.avatar
    ? <img className="avatar" src={p.avatar} alt="" referrerPolicy="no-referrer" />
    : <span className="avatar">{p.name.slice(-2)}</span>;
}

function PersonCard({ p, reason, onEdit }: { p: Person; reason?: string; onEdit?: () => void }) {
  return (
    <div className={"person" + (p.helper ? " helper" : "")}>
      <div className="person-top">
        <Avatar p={p} />
        <div className="person-name">
          <b>{p.name}</b>
          <span className="sub">{p.department || NO_DEPARTMENT}</span>
        </div>
        {p.helper && <span className="badge" title="写了能帮什么">热心市民</span>}
      </div>
      {reason && <p className="reason">💡 {reason}</p>}
      {p.skills && <p className="skills">{p.skills}</p>}
      {p.tags.length > 0 && <div className="tags">{p.tags.map((t) => <span key={t}>{t}</span>)}</div>}
      <div className="person-foot">
        {p.open_id
          ? <a className="chat-link" href={feishuChat(p.open_id)} target="_blank" rel="noreferrer">飞书找 TA →</a>
          : <span className="sub">还没登录过大豆，线下找 TA</span>}
        {onEdit && <button className="link" onClick={onEdit}>编辑</button>}
      </div>
    </div>
  );
}

function PeopleTab({ directory, onEdit }: { directory: PeopleList; onEdit: (p: Person | "new") => void }) {
  const [department, setDepartment] = useState("");
  const [q, setQ] = useState("");
  const [helpersOnly, setHelpersOnly] = useState(false);
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return directory.people
      .filter((p) => !department || (p.department || NO_DEPARTMENT) === department)
      .filter((p) => !helpersOnly || p.helper)
      .filter((p) => !needle || [p.name, p.department, p.skills, ...p.tags].join(" ").toLowerCase().includes(needle));
  }, [directory, department, q, helpersOnly]);
  const count = (d: string) => directory.people.filter((p) => (p.department || NO_DEPARTMENT) === d).length;

  return (
    <section>
      <div className="bar">
        <div className="chips">
          <button className={!department ? "chip on" : "chip"} onClick={() => setDepartment("")}>
            全部<b>{directory.people.length}</b>
          </button>
          {directory.departments.map((d) => (
            <button key={d} className={d === department ? "chip on" : "chip"} onClick={() => setDepartment(d)}>
              {d}<b>{count(d)}</b>
            </button>
          ))}
        </div>
        <div className="bar-row">
          <input className="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜名字、擅长什么…" />
          <label className="check"><input type="checkbox" checked={helpersOnly} onChange={(e) => setHelpersOnly(e.target.checked)} />只看热心市民</label>
          {directory.admin && <button className="btn2" onClick={() => onEdit("new")}>＋ 手动加人</button>}
        </div>
      </div>
      {list.length === 0 ? <p className="empty">这里还没有人。</p> : (
        <div className="people-grid">
          {list.map((p) => (
            <PersonCard key={p.id} p={p}
              onEdit={directory.admin || p.id === directory.me ? () => onEdit(p) : undefined} />
          ))}
        </div>
      )}
    </section>
  );
}

function QuestionCard({ q, byId, compact, onEdit }: {
  q: Question; byId: Map<string, Person>; compact?: boolean; onEdit?: () => void;
}) {
  const hit = useHit();
  const [open, setOpen] = useState(!compact);
  const helpers = q.helpers.map((id) => byId.get(id)).filter((p): p is Person => !!p);
  return (
    <div className="question">
      <div className="q-head" onClick={() => setOpen(!open)}>
        <b>{q.title}</b>
        {q.department && <span className="dept-tag">{q.department}</span>}
        <span className="sub q-meta">{q.hits > 0 && `👍 ${q.hits}`}</span>
      </div>
      {helpers.length > 0 && (
        <div className="q-helpers">
          找：{helpers.map((p) => p.open_id
            ? <a key={p.id} href={feishuChat(p.open_id)} target="_blank" rel="noreferrer">{p.name}</a>
            : <span key={p.id}>{p.name}</span>)}
        </div>
      )}
      {open && (
        <>
          {q.answer && <p className="pre">{q.answer}</p>}
          {q.keywords.length > 0 && <div className="tags">{q.keywords.map((k) => <span key={k} className={q.matched.includes(k) ? "hit" : ""}>{k}</span>)}</div>}
          <div className="q-foot">
            <span className="sub">{q.author && `${q.author} 记于 ${q.created_at}`}</span>
            <button className="link" disabled={hit.isPending} onClick={() => hit.mutate(q.id)}>👍 有用</button>
            {onEdit && <button className="link" onClick={onEdit}>编辑</button>}
          </div>
        </>
      )}
      {!open && q.answer && <button className="link more" onClick={() => setOpen(true)}>看处理办法</button>}
    </div>
  );
}

function QuestionsTab({ directory, byId, onEdit }: {
  directory: PeopleList; byId: Map<string, Person>; onEdit: (q: Question | null) => void;
}) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const { data, isPending } = useQuestions(query);
  const questions = data?.questions ?? [];
  return (
    <section>
      <div className="bar">
        <div className="bar-row">
          <input className="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜问题库…" />
          <button className="cta small" onClick={() => onEdit(null)}>＋ 加一条</button>
        </div>
      </div>
      {isPending ? null : questions.length === 0
        ? <p className="empty">{query ? "没找到对得上的，试试上面的 AI 找人。" : "问题库还是空的。遇到过、解决过的问题记一条，后来的人就不用到处问了。"}</p>
        : questions.map((item) => (
          <QuestionCard key={item.id} q={item} byId={byId}
            onEdit={directory.admin || item.author_id === directory.me ? () => onEdit(item) : undefined} />
        ))}
    </section>
  );
}

function ProfileModal({ target, self, admin, departments, onClose }: {
  target: Person | "new"; self: boolean; admin: boolean; departments: string[]; onClose: () => void;
}) {
  const isNew = target === "new";
  const person = isNew ? null : target;
  const [name, setName] = useState(person?.name ?? "");
  const [department, setDepartment] = useState(person?.department ?? "");
  const [skills, setSkills] = useState(person?.skills ?? "");
  const [tags, setTags] = useState(person?.tags.join("、") ?? "");
  const save = useSaveProfile();
  const remove = useRemoveProfile();
  const toast = useToast();
  const manual = isNew || !!person?.manual;

  const submit = () => {
    if (manual && !name.trim()) return toast("写个名字", true);
    const body = { name: name.trim() || person?.name || "", department, skills, tags: splitWords(tags) };
    save.mutate({ id: self ? "me" : person?.id ?? "", body }, { onSuccess: onClose });
  };

  return (
    <Modal onClose={onClose} width={520}>
      <div className="mtop">
        <h2>{isNew ? "手动加一个人" : self ? "我能帮大家什么" : `编辑 ${person?.name}`}</h2>
        <button className="x" onClick={onClose} aria-label="关闭">✕</button>
      </div>
      <div className="mbody">
        {manual && (
          <>
            <div className="field"><label>名字</label><input value={name} maxLength={20} onChange={(e) => setName(e.target.value)} /></div>
            <div className="field">
              <label>部门</label>
              <input value={department} maxLength={60} list="help-departments" onChange={(e) => setDepartment(e.target.value)} />
              <datalist id="help-departments">{departments.map((d) => <option key={d} value={d} />)}</datalist>
            </div>
          </>
        )}
        <div className="field">
          <label>能帮大家解决什么？<small>写清楚擅长的事，AI 会按这个推荐你</small></label>
          <textarea value={skills} maxLength={500} rows={4} onChange={(e) => setSkills(e.target.value)}
            placeholder="例如：Unity 打包和 Gradle 报错、Spine 动画导出、SVN 锁文件……" />
        </div>
        <div className="field">
          <label>关键词<small>用逗号或顿号隔开</small></label>
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="打包、Spine、SVN" />
        </div>
        {self && <div className="tip">写了之后你会出现在「热心市民」里，同事遇到相关问题时 AI 会推荐来找你。两项都清空就退出。</div>}
        <div className="mfoot">
          {!isNew && person?.helper && (self || admin) && (
            <button className="btn2 danger" disabled={remove.isPending}
              onClick={() => confirm("删掉这份资料？") && remove.mutate(person.id, { onSuccess: onClose })}>删除资料</button>
          )}
          <span style={{ flex: 1 }} />
          <button className="btn2" onClick={onClose}>取消</button>
          <button className="cta" disabled={save.isPending} onClick={submit}>保存</button>
        </div>
      </div>
    </Modal>
  );
}

function QuestionModal({ editing, directory, onClose }: {
  editing: { id: number; draft: QuestionDraft }; directory: PeopleList; onClose: () => void;
}) {
  const [title, setTitle] = useState(editing.draft.title);
  const [keywords, setKeywords] = useState(editing.draft.keywords.join("、"));
  const [department, setDepartment] = useState(editing.draft.department);
  const [answer, setAnswer] = useState(editing.draft.answer);
  const [helpers, setHelpers] = useState<string[]>(editing.draft.helpers);
  const [pickDept, setPickDept] = useState("");
  const save = useSaveQuestion();
  const remove = useDeleteQuestion();
  const toast = useToast();
  const candidates = directory.people.filter((p) => !pickDept || (p.department || NO_DEPARTMENT) === pickDept);
  const toggle = (id: string) => setHelpers((list) => list.includes(id) ? list.filter((x) => x !== id) : [...list, id].slice(0, 8));

  const submit = () => {
    if (title.trim().length < 2) return toast("问题至少写 2 个字", true);
    save.mutate({ id: editing.id, body: { title, keywords: splitWords(keywords), department, answer, helpers } },
      { onSuccess: onClose });
  };

  return (
    <Modal onClose={onClose} width={620}>
      <div className="mtop">
        <h2>{editing.id ? "编辑问题" : "加进问题库"}</h2>
        <button className="x" onClick={onClose} aria-label="关闭">✕</button>
      </div>
      <div className="mbody">
        <div className="field"><label>问题<small>一句话</small></label>
          <input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} placeholder="例如：打包报 Gradle 错误" /></div>
        <div className="field"><label>关键词<small>描述里出现这些词就会匹配上，用逗号或顿号隔开</small></label>
          <input value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="打包、Gradle、build" /></div>
        <div className="field"><label>归哪个部门</label>
          <input value={department} maxLength={60} list="help-departments-q" onChange={(e) => setDepartment(e.target.value)} />
          <datalist id="help-departments-q">{directory.departments.map((d) => <option key={d} value={d} />)}</datalist>
        </div>
        <div className="field">
          <label>该找谁<small>已选 {helpers.length} 人</small></label>
          <select value={pickDept} onChange={(e) => setPickDept(e.target.value)}>
            <option value="">所有部门</option>
            {directory.departments.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <div className="pick-list">
            {candidates.map((p) => (
              <button type="button" key={p.id} className={helpers.includes(p.id) ? "on" : ""} onClick={() => toggle(p.id)}>
                {p.name}<small>{p.department || NO_DEPARTMENT}</small>
              </button>
            ))}
          </div>
        </div>
        <div className="field"><label>处理办法<small>自己可以先试什么、怎么解决</small></label>
          <textarea value={answer} maxLength={2000} rows={4} onChange={(e) => setAnswer(e.target.value)} /></div>
        <div className="mfoot">
          {editing.id > 0 && (
            <button className="btn2 danger" disabled={remove.isPending}
              onClick={() => confirm("删掉这条？") && remove.mutate(editing.id, { onSuccess: onClose })}>删除</button>
          )}
          <span style={{ flex: 1 }} />
          <button className="btn2" onClick={onClose}>取消</button>
          <button className="cta" disabled={save.isPending} onClick={submit}>保存</button>
        </div>
      </div>
    </Modal>
  );
}
