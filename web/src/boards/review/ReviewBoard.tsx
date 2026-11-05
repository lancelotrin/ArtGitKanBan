import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { usePageTitle } from "../../lib/usePageTitle";
import "./review.css";

// 和 backend/routers/review.py 对应。*_html 字段是后端转义过再加了固定标签的片段，可以直接插入。
interface Pill { cls: string; text: string }
interface Person {
  file: string; name: string; author: string; color: string; commits: number; risks: number; bomb: boolean;
  tags: string[]; pill: Pill; summary_html: string;
}
interface DayView {
  dates: string[]; date: string; weekday?: string; older?: string; newer?: string; latest?: string; people: Person[];
  filters?: { key: string; text: string; count: number }[];
  stats?: { people: number; commits: number; risk_people: number; bomb_people: number };
}
interface PersonView { name: string; display: string; color: string; rows: { date: string; weekday: string; commits: number; pill: Pill; summary_html: string }[] }
interface Commit { hash: string; branch: string; time: string; subject_html: string; diff_html: string; diff_note: string }
interface Report {
  date: string; weekday: string; name: string; display: string; color: string; git_author: string; generated_at: string;
  status: string; error: string; pill: Pill; commit_count: number; risks: number; older: string; newer: string;
  commits: Commit[]; report_html: string; bomb_out_html: string; bomb_in_html: string;
}

const BASE = "/review";
const Html = ({ html, className }: { html: string; className?: string }) => (
  <div className={className} dangerouslySetInnerHTML={{ __html: html }} />
);
const PillTag = ({ pill }: { pill: Pill }) => <span className={"pill " + pill.cls}>{pill.text}</span>;
const Avatar = ({ name, color, big }: { name: string; color: string; big?: boolean }) => (
  <div className={"avatar" + (big ? " big" : "")} style={{ background: color }}>{name.slice(0, 1)}</div>
);
function NavButton({ to, text, title }: { to?: string; text: string; title: string }) {
  return to ? <Link className="btn" to={to} title={title}>{text}</Link> : <span className="btn off">{text}</span>;
}

function Layout({ hero, children }: { hero: ReactNode; children: ReactNode }) {
  return (
    <div className="review-page">
      <header>
        <div className="hwrap">
          <Link className="brand" to={BASE + "/"}><span className="logo">豆</span>大豆 · 自检复盘</Link>
          {hero}
        </div>
      </header>
      <main>{children}</main>
    </div>
  );
}

function Problem({ error }: { error: Error }) {
  return (
    <Layout hero={<h1>没有这页</h1>}>
      <div className="panel none">{error.message} <Link to={BASE + "/"}>回首页</Link></div>
    </Layout>
  );
}

function DayPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const date = params.get("d") || "";
  const { data, error } = useQuery({ queryKey: ["review-day", date], queryFn: () => api<DayView>("/reviews/days?date=" + encodeURIComponent(date)) });
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  usePageTitle(data?.date ? `${data.date} 自检复盘` : "自检复盘");
  const shown = useMemo(() => {
    const key = q.trim().toLowerCase();
    return (data?.people ?? []).filter((p) => (filter === "all" || p.tags.includes(filter)) && (p.name + " " + p.author).toLowerCase().includes(key));
  }, [data, filter, q]);
  if (error) return <Problem error={error} />;
  if (!data) return <Layout hero={<h1>自检复盘</h1>}><div className="panel none">正在读取…</div></Layout>;
  if (!data.dates.length) {
    return (
      <Layout hero={<><h1>自检复盘</h1><div className="hsub">还没有复盘记录，每天 0 点自动生成。</div></>}>
        <div className="panel none">还没有复盘记录。</div>
      </Layout>
    );
  }
  const stats = data.stats!;
  const day = (d?: string) => (d ? `${BASE}/?d=${d}` : undefined);
  const hero = (
    <>
      <h1>{data.date} 复盘</h1>
      <div className="hsub">每天 0 点复盘前一天 main 和 CBT2_bugfix 上已推送的提交。内容来自真实 diff，隐患只写有具体依据的；炸线是「疑似」，请当事人确认。</div>
    </>
  );
  return (
    <Layout hero={hero}>
      <div className="panel">
        <div className="toolbar">
          <NavButton to={day(data.older)} text="‹" title="前一天" />
          <input type="date" value={data.date} min={data.dates[data.dates.length - 1]} max={data.dates[0]}
            onChange={(e) => e.target.value && navigate(day(e.target.value)!)} />
          <NavButton to={day(data.newer)} text="›" title="后一天" />
          <NavButton to={data.date !== data.latest ? BASE + "/" : undefined} text="最新" title="最新一天" />
          <span className="weekday">{data.weekday}</span>
          <div className="spacer" />
          <input type="search" placeholder="搜名字或 git 作者" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="chips">
            {(data.filters ?? []).map((f) => (
              <button key={f.key} className={"chip" + (filter === f.key ? " on" : "")} onClick={() => setFilter(f.key)}>
                {f.text}<b>{f.count}</b>
              </button>
            ))}
          </div>
        </div>
        <div className="stats">
          <div className="stat"><div className="k">复盘人数</div><div className="v">{stats.people}</div></div>
          <div className="stat"><div className="k">提交总数</div><div className="v">{stats.commits}</div></div>
          <div className={"stat" + (stats.risk_people ? " warn" : "")}><div className="k">需要留意的人</div><div className="v">{stats.risk_people}</div></div>
          <div className={"stat" + (stats.bomb_people ? " bad" : "")}><div className="k">疑似炸线的人</div><div className="v">{stats.bomb_people}</div></div>
        </div>
      </div>
      {shown.length > 0 && (
        <div className="grid">
          {shown.map((p) => (
            <Link key={p.file} className={"pcard" + (p.bomb ? " is-bomb" : p.risks ? " is-risk" : "")} to={`${BASE}/r/${data.date}/${p.file}`}>
              <div className="ptop">
                <Avatar name={p.name} color={p.color} />
                <div><div className="pname">{p.name}</div><div className="pauthor">{p.author}</div></div>
                <PillTag pill={p.pill} />
              </div>
              <Html className="psum" html={p.summary_html} />
              <div className="pfoot">
                <span>{p.commits} 条提交</span>
                {p.risks > 0 && <span className="w">{p.risks} 处留意</span>}
                {p.bomb && <span className="b">疑似炸线</span>}
              </div>
            </Link>
          ))}
        </div>
      )}
      {!shown.length && <div className="panel none">{data.people.length ? "没有符合条件的人。" : "这天没有复盘记录，换个日期看看。"}</div>}
    </Layout>
  );
}

function PersonPage() {
  const { name = "" } = useParams();
  const { data, error } = useQuery({ queryKey: ["review-person", name], queryFn: () => api<PersonView>("/reviews/people/" + encodeURIComponent(name)) });
  usePageTitle(data ? `${data.display} 的复盘` : "自检复盘");
  if (error) return <Problem error={error} />;
  if (!data) return <Layout hero={<h1>{name}</h1>}><div className="panel none">正在读取…</div></Layout>;
  const hero = (
    <>
      <div className="crumb"><Link to={BASE + "/"}>自检复盘</Link> / {data.display}</div>
      <div className="hero">
        <Avatar name={data.display} color={data.color} big />
        <div><h1>{data.display} 的复盘历史</h1><div className="hsub">{data.name} · 共 {data.rows.length} 天</div></div>
      </div>
    </>
  );
  return (
    <Layout hero={hero}>
      <section className="panel">
        <table>
          <tbody>
            <tr><th>日期</th><th></th><th>提交</th><th>结论</th><th>概要</th></tr>
            {data.rows.map((row) => (
              <tr key={row.date}>
                <td><Link to={`${BASE}/r/${row.date}/${data.name}`}>{row.date}</Link></td>
                <td>{row.weekday}</td>
                <td>{row.commits}</td>
                <td><PillTag pill={row.pill} /></td>
                <td dangerouslySetInnerHTML={{ __html: row.summary_html }} />
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </Layout>
  );
}

function ReportPage() {
  const { date = "", name = "" } = useParams();
  const { data: r, error } = useQuery({
    queryKey: ["review-report", date, name],
    queryFn: () => api<Report>(`/reviews/days/${encodeURIComponent(date)}/${encodeURIComponent(name)}`),
  });
  usePageTitle(r ? `${r.display} ${r.date} 复盘` : "自检复盘");
  if (error) return <Problem error={error} />;
  if (!r) return <Layout hero={<h1>{name}</h1>}><div className="panel none">正在读取…</div></Layout>;
  const hero = (
    <>
      <div className="crumb">
        <Link to={BASE + "/"}>自检复盘</Link> / <Link to={`${BASE}/?d=${r.date}`}>{r.date}</Link> / {r.display}
      </div>
      <div className="hero">
        <Avatar name={r.display} color={r.color} big />
        <div><h1>{r.display}<PillTag pill={r.pill} /></h1><div className="hsub">{r.git_author} · {r.date} {r.weekday}</div></div>
      </div>
    </>
  );
  return (
    <Layout hero={hero}>
      <div className="panel">
        <div className="toolbar">
          <NavButton to={r.older ? `${BASE}/r/${r.older}/${r.name}` : undefined} text="‹" title="他的前一天" />
          <NavButton to={r.newer ? `${BASE}/r/${r.newer}/${r.name}` : undefined} text="›" title="他的后一天" />
          <Link className="btn" to={`${BASE}/?d=${r.date}`}>当天所有人</Link>
          <Link className="btn" to={`${BASE}/p/${r.name}`}>他的历史</Link>
          <div className="spacer" />
          <span className="weekday">生成于 {r.generated_at}</span>
        </div>
        <div className="stats">
          <div className="stat"><div className="k">提交</div><div className="v">{r.commit_count}</div></div>
          <div className={"stat" + (r.risks ? " warn" : "")}><div className="k">需要留意</div><div className="v">{r.risks}</div></div>
          <div className={"stat" + (r.bomb_out_html ? " bad" : "")}><div className="k">合并丢了别人的</div><div className="v">{r.bomb_out_html ? "有" : "无"}</div></div>
          <div className={"stat" + (r.bomb_in_html ? " bad" : "")}><div className="k">被别人合并丢了</div><div className="v">{r.bomb_in_html ? "有" : "无"}</div></div>
        </div>
      </div>
      {r.bomb_out_html && <section className="panel alert"><h2>你的合并疑似丢了别人的修改</h2><Html html={r.bomb_out_html} /></section>}
      {r.bomb_in_html && <section className="panel alert"><h2>你的修改疑似被别人合并丢了</h2><Html html={r.bomb_in_html} /></section>}
      {r.status === "failed" && (
        <section className="panel">这天的复盘没生成成功：{r.error || "模型没返回内容"}。可以找朱辉在后台补跑。</section>
      )}
      {r.commits.length > 0 && (
        <section className="panel">
          <h2>当天提交<span className="n">{r.commits.length} 条</span></h2>
          <ul className="clist">
            {r.commits.map((c, i) => (
              <li key={c.hash + i}>
                <div className="cmain">
                  <span className="hash">{c.hash}</span><span className="br">{c.branch}</span><span className="time">{c.time}</span>
                  <span dangerouslySetInnerHTML={{ __html: c.subject_html }} />
                </div>
                {c.diff_html ? (
                  <details className="diffbox"><summary>相关 diff（代码类，已裁剪）</summary><Html html={c.diff_html} /></details>
                ) : (
                  <div className="diffnote">{c.diff_note}</div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {r.report_html && <section className="panel"><h2>复盘</h2><Html html={r.report_html} /></section>}
      {r.status === "empty" && <section className="panel none">这天在 main 和 CBT2_bugfix 上没有提交。</section>}
    </Layout>
  );
}

export default function ReviewBoard() {
  return (
    <Routes>
      <Route index element={<DayPage />} />
      <Route path="r/:date/:name" element={<ReportPage />} />
      <Route path="p/:name" element={<PersonPage />} />
      <Route path="*" element={<Problem error={new Error("没有这页。")} />} />
    </Routes>
  );
}
