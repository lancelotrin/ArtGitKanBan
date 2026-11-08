import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../../api/client";
import { usePageTitle } from "../../lib/usePageTitle";
import "./docwatch.css";

// 和 backend/routers/docwatch.py → doc_watch_board.board_view() 对应。
interface Person {
  name: string;
  role?: string;
  reason?: string;
}
interface Item {
  kind: "added" | "changed" | "removed";
  text: string;
}
interface Section {
  section: string;
  items: Item[];
  points: string[];
  features: string[];
  people: Person[];
}
interface Batch {
  id: string;
  time: string;
  doc: string;
  token: string;
  url: string;
  counts: { added: number; changed: number; removed: number };
  sections: Section[];
  summary: string;
  people: Person[];
  features: string[];
  source: "ai" | "rule";
  analyzed_at: string;
  recipients: Record<string, string>;
  retrying: boolean;
}
interface Pending {
  section: string;
  doc: string;
  text: string;
  url: string;
  asked_at: string;
}
interface View {
  enabled: boolean;
  docs: { name: string; url: string }[];
  batches: Batch[];
  pending: Pending[];
  asker: string;
  check_minutes: number[];
  digest: string;
}

const MARK: Record<Item["kind"], string> = { added: "＋", changed: "～", removed: "－" };
const KIND: Record<Item["kind"], string> = { added: "新增", changed: "修改", removed: "删除" };
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

const parse = (t: string) => Date.parse(String(t || "").replace(" ", "T"));
const ageDays = (t: string) => {
  const ms = parse(t);
  return isNaN(ms) ? 0 : (Date.now() - ms) / 86400000;
};
const hm = (t: string) => String(t || "").slice(11, 16);
const short = (t: string) => String(t || "").slice(5, 16);

function dayLabel(t: string): string {
  const d = new Date(parse(t));
  if (isNaN(d.getTime())) return "更早";
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  const tag = same(d, new Date()) ? " · 今天" : same(d, new Date(Date.now() - 86400000)) ? " · 昨天" : "";
  return d.getMonth() + 1 + " 月 " + d.getDate() + " 日 " + WEEK[d.getDay()] + tag;
}

const total = (b: Batch) => b.counts.added + b.counts.changed + b.counts.removed;
const involves = (b: Batch, name: string) => b.people.some((p) => p.name === name) || name in (b.recipients || {});

function haystack(b: Batch): string {
  const bits = [b.doc, b.summary, ...b.features, ...b.people.map((p) => p.name + " " + (p.reason || ""))];
  b.sections.forEach((s) => {
    bits.push(s.section, ...s.points, ...s.features);
    s.items.forEach((i) => bits.push(i.text));
  });
  return bits.join(" ").toLowerCase();
}

function PersonChip({ p, notified, active, onPick }: { p: Person; notified?: string; active?: boolean; onPick?: (name: string) => void }) {
  const tip = [p.role, p.reason, notified === "OK" ? "已私聊通知" : notified ? "没发出：" + notified : ""].filter(Boolean).join("\n");
  return (
    <button type="button" className={"person" + (active ? " on" : "") + (notified === "OK" ? " told" : "")} title={tip}
      onClick={() => onPick?.(p.name)}>
      {notified === "OK" && <i>✓</i>}
      <b>{p.name}</b>
      {p.role && <span>{p.role}</span>}
    </button>
  );
}

function SectionBlock({ s, recipients, pick, person }: { s: Section; recipients: Record<string, string>; pick: (n: string) => void; person: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sec">
      <div className="sec-title">▸ {s.section}</div>
      {s.points.length > 0 && (
        <ul className="points">
          {s.points.map((point, i) => <li key={i}>{point}</li>)}
        </ul>
      )}
      {(s.features.length > 0 || s.people.length > 0) && (
        <div className="sec-meta">
          {s.features.map((f) => <span className="feature" key={f}>{f}</span>)}
          {s.people.map((p) => (
            <span className={"mini" + (p.name === person ? " on" : "")} key={p.name} title={[p.role, p.reason].filter(Boolean).join("：")}
              onClick={() => pick(p.name)}>
              {recipients[p.name] === "OK" ? "✓ " : ""}{p.name}{p.reason ? "：" + p.reason : ""}
            </span>
          ))}
        </div>
      )}
      <button type="button" className="raw-toggle" onClick={() => setOpen(!open)}>
        {open ? "收起原文" : `看原文改动 ${s.items.length} 条`}
      </button>
      {open && (
        <div className="raw">
          {s.items.map((item, i) => (
            <div className={"raw-line k-" + item.kind} key={i} title={KIND[item.kind]}>
              <span>{MARK[item.kind]}</span>
              <p>{item.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BatchCard({ b, person, pick }: { b: Batch; person: string; pick: (n: string) => void }) {
  const recipients = b.recipients || {};
  const sent = Object.entries(recipients).filter(([, r]) => r === "OK").map(([n]) => n);
  const missed = Object.entries(recipients).filter(([, r]) => r !== "OK");
  return (
    <article className={"card" + (b.retrying ? " retrying" : "")}>
      <div className="top">
        <span className="doc">{b.doc}</span>
        <span className="time">{hm(b.time)}</span>
        <span className="counts">
          {b.counts.added > 0 && <em className="k-added">＋{b.counts.added}</em>}
          {b.counts.changed > 0 && <em className="k-changed">～{b.counts.changed}</em>}
          {b.counts.removed > 0 && <em className="k-removed">－{b.counts.removed}</em>}
        </span>
        <span className={"src " + b.source} title={b.source === "ai" ? "大豆用 AI 读过这批改动，" + short(b.analyzed_at) + " 分析" : "AI 还没分析到这批，先按归因规则（关键词 / 功能对照表）推算"}>
          {b.source === "ai" ? "AI 分析" : "规则推算"}
        </span>
        {b.retrying && <span className="retry">还有人没发出，大豆在重试</span>}
        <a className="open" href={b.url} target="_blank" rel="noreferrer">打开文档 ↗</a>
      </div>
      {b.summary && <div className="summary">{b.summary}</div>}
      {b.people.length > 0 && (
        <div className="row">
          <span className="label">涉及的人</span>
          <div className="people">
            {b.people.map((p) => <PersonChip key={p.name} p={p} notified={recipients[p.name]} active={p.name === person} onPick={pick} />)}
          </div>
        </div>
      )}
      {b.features.length > 0 && (
        <div className="row">
          <span className="label">涉及功能</span>
          <div className="features">{b.features.map((f) => <span className="feature" key={f}>{f}</span>)}</div>
        </div>
      )}
      <div className="secs">
        {b.sections.map((s) => <SectionBlock key={s.section} s={s} recipients={recipients} pick={pick} person={person} />)}
      </div>
      <div className="foot">
        {sent.length ? <>已私聊通知：{sent.join("、")}</> : "这批没有私聊记录"}
        {missed.map(([n, r]) => <span className="miss" key={n}> · {n}没发出（{r}）</span>)}
      </div>
    </article>
  );
}

export default function DocWatchBoard() {
  usePageTitle("监修改动 · 大豆");
  const { data, error } = useQuery({ queryKey: ["docwatch"], queryFn: () => api<View>("/docwatch"), refetchInterval: 60_000 });
  const [params, setParams] = useSearchParams();
  const setParam = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: true });
  const range = Number(params.get("range")) || 30;
  const docParam = params.get("doc") || "";
  const person = params.get("who") || "";
  const q = params.get("q") || "";
  const [showPending, setShowPending] = useState(false);

  const batches = data?.batches ?? [];
  const docs = data?.docs ?? [];
  const doc = docs.some((d) => d.name === docParam) ? docParam : "";
  const people = useMemo(() => {
    const count = new Map<string, number>();
    batches.forEach((b) => {
      const names = new Set([...b.people.map((p) => p.name), ...Object.keys(b.recipients || {})]);
      names.forEach((n) => count.set(n, (count.get(n) || 0) + 1));
    });
    return [...count.entries()].sort((a, b) => b[1] - a[1]);
  }, [batches]);

  const needle = q.trim().toLowerCase();
  const rows = batches.filter((b) =>
    (range >= 9999 || ageDays(b.time) <= range) && (!doc || b.doc === doc) && (!person || involves(b, person)) &&
    (!needle || haystack(b).includes(needle)));
  const week = batches.filter((b) => ageDays(b.time) <= 7);
  const month = batches.filter((b) => ageDays(b.time) <= 30);
  const monthPeople = new Set(month.flatMap((b) => b.people.map((p) => p.name)));
  const stats: [string, number, string][] = [
    ["", week.length, "最近 7 天改动批次"],
    ["", week.reduce((n, b) => n + total(b), 0), "最近 7 天改动段落"],
    ["", monthPeople.size, "30 天内涉及的人"],
    [data?.pending.length ? "warn" : "", data?.pending.length ?? 0, "等确认归属"],
  ];
  const pick = (name: string) => setParam("who", name === person ? "" : name);

  let lastDay = "";
  return (
    <div className="docwatch-page">
      <header className="hero">
        <div className="wrap">
          <div>
            <div className="brand"><i>豆</i>大豆</div>
            <h1>监修改动</h1>
            <p>版权方监修文档的每一次改动：改了什么、涉及哪些功能、该谁跟进。大豆自动检查文档，按负责人私聊通知，并用 AI 读一遍改动标出相关的人和功能。</p>
            <div className="scan">
              {data && !data.enabled ? "文档监测没开启" : data && (
                <>每小时 {data.check_minutes.map((m) => ":" + String(m).padStart(2, "0")).join(" / ")} 检查
                  {data.digest && <> · 每天 {data.digest} 发日报</>}
                  {docs.map((d) => <span key={d.name}> · <a href={d.url} target="_blank" rel="noreferrer">{d.name}</a></span>)}
                </>
              )}
            </div>
          </div>
          <svg className="art" viewBox="0 0 120 120" aria-hidden="true">
            <circle cx="60" cy="60" r="52" fill="rgba(255,255,255,.08)" />
            <rect x="34" y="22" width="52" height="70" rx="8" fill="#fff" opacity=".95" />
            <rect x="42" y="36" width="36" height="5" rx="2.5" fill="#99e9f2" />
            <rect x="42" y="48" width="28" height="5" rx="2.5" fill="#c5f6fa" />
            <rect x="42" y="60" width="32" height="5" rx="2.5" fill="#ffd43b" />
            <rect x="42" y="72" width="22" height="5" rx="2.5" fill="#c5f6fa" />
            <circle cx="84" cy="84" r="16" fill="#ffd43b" />
            <path d="m77 84 5 5 9-10" stroke="#0b7285" strokeWidth="4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </header>
      <main className="wrap">
        <div className="stats">
          {stats.map(([cls, n, label], i) => (
            <div className={"stat " + cls + (i === 3 && n ? " click" : "")} key={label} onClick={() => i === 3 && n && setShowPending(!showPending)}>
              <div className="n">{n}</div><div className="l">{label}</div>
            </div>
          ))}
        </div>
        {showPending && data && data.pending.length > 0 && (
          <section className="pending">
            <div className="pending-head">
              这些改动规则归不上，大豆私聊问了{data.asker || "维护者"}，回复数字后会补发给对应的人：
              <button type="button" onClick={() => setShowPending(false)}>收起</button>
            </div>
            {data.pending.map((p) => (
              <div className="pending-row" key={p.section + p.asked_at}>
                <span className="time">{short(p.asked_at)}</span>
                <a href={p.url} target="_blank" rel="noreferrer">{p.doc}</a>
                <b>{p.section}</b>
                <span className="txt">{p.text}</span>
              </div>
            ))}
          </section>
        )}
        <div className="bar">
          <div className="seg">
            {[{ name: "", label: "全部文档" }, ...docs.map((d) => ({ name: d.name, label: d.name }))].map((d) => (
              <button key={d.name || "all"} className={doc === d.name ? "on" : ""} onClick={() => setParam("doc", d.name)}>{d.label}</button>
            ))}
          </div>
          <select className="sel" value={person} onChange={(e) => setParam("who", e.target.value)}>
            <option value="">所有人</option>
            {people.map(([name, n]) => <option key={name} value={name}>{name}（{n}）</option>)}
          </select>
          <select className="sel" value={range} onChange={(e) => setParam("range", e.target.value === "30" ? "" : e.target.value)}>
            <option value="7">最近 7 天</option>
            <option value="30">最近 30 天</option>
            <option value="90">最近 90 天</option>
            <option value="9999">全部</option>
          </select>
          <label className="search">
            <input type="search" placeholder="搜角色、功能、人名或原文" value={q} onChange={(e) => setParam("q", e.target.value)} />
          </label>
        </div>
        {person && (
          <div className="filter-note">
            只看和 <b>{person}</b> 有关的改动（AI 认为涉及他，或大豆私聊通知过他）
            <button type="button" onClick={() => setParam("who", "")}>看全部</button>
          </div>
        )}
        <div>
          {error ? (
            <div className="empty">读不到数据，稍后刷新试试。</div>
          ) : !data ? (
            <div className="empty">正在读取…</div>
          ) : !rows.length ? (
            <div className="empty">
              <div className="big">{batches.length ? "🔍" : "📭"}</div>
              {batches.length ? "没有符合条件的改动，换个筛选试试。" : "还没有检测到监修文档的改动。"}
            </div>
          ) : (
            rows.map((b) => {
              const label = dayLabel(b.time);
              const head = label !== lastDay ? <div className="day">{label}</div> : null;
              lastDay = label;
              return (
                <div key={b.id}>
                  {head}
                  <BatchCard b={b} person={person} pick={pick} />
                </div>
              );
            })
          )}
        </div>
        <p className="note">
          「AI 分析」是大豆用 AI 读完这批改动后给出的要点、涉及功能和该跟进的人（人名和功能只从负责人名单、功能对照表里选）；
          还没分析到的批次显示「规则推算」，按关键词和功能对照表算。实际私聊给谁以通知记录为准（人名前带 ✓）。
          日文原文已在要点里译成中文，原文以文档为准。
        </p>
      </main>
    </div>
  );
}
