import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../../api/client";
import { usePageTitle } from "../../lib/usePageTitle";
import "./bomb.css";

// 和 backend/routers/bomb.py → bomb_board.board_view() 对应。
interface BombFile {
  path: string;
  state: string;
  commits: string[];
  lost_branch?: string;
  judge?: string;
}
interface BombRecord {
  key: string;
  kind: "merge" | "force";
  status: string;
  branches?: string[];
  at?: string;
  first_seen?: string;
  checked_at?: string;
  alerted?: boolean;
  merge?: string;
  author?: string;
  date?: string;
  subject?: string;
  count?: number;
  files?: BombFile[];
  old?: string;
  new?: string;
  when?: string;
  lost?: string[];
  resolved?: boolean;
  resolved_by?: string;
  resolved_at?: string;
}
interface BombView {
  records: BombRecord[];
  scan: { enabled: boolean; minutes: number; last_at: string; next_at: string };
  note: string;
}

const STATUS: [string, string][] = [
  ["all", "全部"], ["open", "没解决"], ["resolved", "已解决"], ["还丢着", "还丢着"], ["要人工看", "要人工看"], ["已补回", "已补回"], ["强推", "强推"],
];
// 和 bomb_board.is_open 一致：没人标记解决、也不是全部已补回的，大豆会定时再提醒。
const isOpen = (r: BombRecord) => !r.resolved && r.status !== "已补回";
const matchStatus = (r: BombRecord, status: string) =>
  status === "all" || (status === "open" ? isOpen(r) : status === "resolved" ? !!r.resolved : r.status === status);
const CLS: Record<string, string> = { 还丢着: "bad", 要人工看: "warn", 已补回: "ok", 强推: "force" };
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

const clean = (subject?: string) => String(subject || "").replace(/\s*https?:\/\/\S+/g, "").trim();
const short = (iso?: string) => {
  const t = String(iso || "");
  return t.length >= 16 ? t.slice(5, 10) + " " + t.slice(11, 16) : t;
};
const whenOf = (r: BombRecord) => String(r.at || r.first_seen || "");
const ageDays = (iso: string) => {
  const t = Date.parse(iso);
  return isNaN(t) ? 0 : (Date.now() - t) / 86400000;
};

function haystack(r: BombRecord): string {
  const bits: string[] = [r.author || "", r.subject || "", r.merge || "", r.old || "", r.new || "", (r.branches || []).join(" ")];
  (r.files || []).forEach((f) => {
    bits.push(f.path);
    (f.commits || []).forEach((line) => bits.push(line));
  });
  (r.lost || []).forEach((line) => bits.push(line));
  return bits.join(" ").toLowerCase();
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "更早";
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  const tag = same(d, new Date()) ? " · 今天" : same(d, new Date(Date.now() - 86400000)) ? " · 昨天" : "";
  return d.getMonth() + 1 + " 月 " + d.getDate() + " 日 " + WEEK[d.getDay()] + tag;
}

function CommitLine({ line }: { line: string }) {
  const parts = String(line).split("|");
  if (parts.length < 4) return <>{line}</>;
  const subject = clean(parts.slice(3).join("|"));
  return (
    <>
      <b>{parts[1]}</b> {parts[2]}「{subject.slice(0, 60)}」 <code>{parts[0]}</code>
    </>
  );
}

function Told({ alerted }: { alerted?: boolean }) {
  return <span className="told">{alerted ? "已提醒" : "补录，没单独提醒"}</span>;
}

function Resolve({ r }: { r: BombRecord }) {
  const client = useQueryClient();
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: (resolved: boolean) =>
      api<BombView>("/bombs/resolve", { method: "POST", body: JSON.stringify({ key: r.key, resolved }) }),
    onSuccess: (view) => {
      setError("");
      client.setQueryData(["bombs"], (old: BombView | undefined) => (old ? { ...old, records: view.records } : view));
    },
    onError: (e: Error) => setError(e.message),
  });
  return (
    <div className="resolve">
      {r.resolved ? (
        <>
          <span className="done">✓ {r.resolved_by || "有人"} {short(r.resolved_at)} 标记已解决，不再提醒</span>
          <button className="ghost" disabled={mutation.isPending} onClick={() => mutation.mutate(false)}>撤销</button>
        </>
      ) : (
        <>
          {isOpen(r) && <span className="again">没解决，大豆会定时再提醒</span>}
          <button className="primary" disabled={mutation.isPending} onClick={() => mutation.mutate(true)}>
            {mutation.isPending ? "提交中…" : "标记已解决"}
          </button>
        </>
      )}
      {error && <span className="err">{error}</span>}
    </div>
  );
}

function MergeCard({ r }: { r: BombRecord }) {
  const cls = CLS[r.status] || "warn";
  const files = r.files || [];
  const extra = (r.count || 0) - files.length;
  return (
    <article className={"card s-" + cls + (r.resolved ? " resolved" : "")}>
      <div className="top">
        <span className={"pill p-" + cls}>{r.status}</span>
        {(r.branches || []).map((b) => <span className="br" key={b}>{b}</span>)}
        <code>{r.merge}</code>
        <span className="who"><b>{r.author}</b> 合并 · {r.date || short(r.at)}</span>
        <Told alerted={r.alerted} />
      </div>
      <div className="subj">{clean(r.subject)}</div>
      <div className="who">丢了 {r.count || 0} 个文件的修改</div>
      <div className="files">
        {files.map((f, i) => (
          <div className="file" key={f.path + i}>
            <div className="fp">
              <span className={"fs " + (CLS[f.state] || "warn")}>{f.state || "要人工看"}</span>
              <code title={f.path}>{String(f.path || "").split("/").slice(-3).join("/")}</code>
              {f.lost_branch && <span className="lb">丢的分支：{f.lost_branch}</span>}
            </div>
            <div className="lost">
              {f.commits?.length ? (
                f.commits.map((line, j) => <span key={j}>丢的是：<CommitLine line={line} /></span>)
              ) : (
                <span>丢的是对面那侧的修改</span>
              )}
              {f.judge && <span className="judge">Codex 判断：{f.judge}</span>}
            </div>
          </div>
        ))}
        {extra > 0 && <div className="more">另外还有 {extra} 个文件没逐个列出</div>}
      </div>
      <div className="foot">首次发现 {short(r.first_seen)} · 补回状态截至 {short(r.checked_at)}</div>
      <Resolve r={r} />
    </article>
  );
}

function ForceCard({ r }: { r: BombRecord }) {
  const lost = r.lost || [];
  return (
    <article className={"card s-force" + (r.resolved ? " resolved" : "")}>
      <div className="top">
        <span className="pill p-force">强推</span>
        {(r.branches || []).map((b) => <span className="br" key={b}>{b}</span>)}
        <code>{r.old}</code> → <code>{r.new}</code>
        <span className="who">本机拉取时发现 · {r.when}</span>
        <Told alerted={r.alerted} />
      </div>
      <div className="subj">远端分支被强推，冲掉了 {lost.length} 个提交</div>
      <div className="files">
        <div className="file">
          <div className="lost">
            {lost.length ? lost.map((line, i) => <span key={i}><CommitLine line={line} /></span>) : <span>没列出被冲掉的提交</span>}
          </div>
        </div>
      </div>
      <div className="foot">首次发现 {short(r.first_seen)}</div>
      <Resolve r={r} />
    </article>
  );
}

export default function BombBoard() {
  usePageTitle("炸线看板 · 大豆");
  const { data, error } = useQuery({ queryKey: ["bombs"], queryFn: () => api<BombView>("/bombs"), refetchInterval: 60_000 });
  const [params, setParams] = useSearchParams();
  const range = Number(params.get("range")) || 30;
  const status = params.get("status") || "all";
  const q = params.get("q") || "";
  const setParam = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: true });

  const records = data?.records ?? [];
  const branches = useMemo(() => {
    const list: string[] = [];
    records.forEach((r) => (r.branches || []).forEach((b) => !list.includes(b) && list.push(b)));
    return list;
  }, [records]);
  const branchParam = params.get("branch") || "all";
  const branch = branchParam !== "all" && !branches.includes(branchParam) ? "all" : branchParam;

  const needle = q.trim().toLowerCase();
  const inScope = (r: BombRecord) =>
    ageDays(whenOf(r)) <= range && (branch === "all" || (r.branches || []).includes(branch)) && (!needle || haystack(r).includes(needle));
  const scoped = records.filter(inScope);
  const rows = scoped.filter((r) => matchStatus(r, status));
  const within = (days: number) => records.filter((r) => ageDays(whenOf(r)) <= days);
  const count30 = (s: string) => within(30).filter((r) => r.status === s).length;
  const stats: [string, number, string][] = [
    ["", within(7).length, "最近 7 天"], ["bad", records.filter(isOpen).length, "没解决（会再提醒）"], ["bad", count30("还丢着"), "30 天内还丢着"],
    ["warn", count30("要人工看"), "30 天内要人工看"], ["force", count30("强推"), "30 天内强推"],
  ];

  const scan = data?.scan;
  const scanParts: React.ReactNode[] = [];
  if (scan) {
    scanParts.push(scan.enabled ? `每 ${scan.minutes} 分钟自动查一次` : "自动检查已关闭");
    if (scan.last_at) scanParts.push(<>上次检查 <b>{short(scan.last_at)}</b></>);
    if (scan.next_at) scanParts.push(<>下次 <b>{short(scan.next_at)}</b></>);
  }

  let lastDay = "";
  return (
    <div className="bomb-page">
      <header className="hero">
        <div className="wrap">
          <div>
            <div className="brand"><i>豆</i>大豆</div>
            <h1>炸线看板</h1>
            <p>合并时谁的修改被整份丢掉、现在补回没有，以及远端分支被强推。大豆定时自动查，保留最近 90 天。处理完点「标记已解决」，大豆就不再提醒。</p>
            <div className="scan">{scanParts.map((part, i) => <span key={i}>{i > 0 && " · "}{part}</span>)}</div>
          </div>
          <svg className="bolt" viewBox="0 0 120 120" aria-hidden="true">
            <circle cx="60" cy="60" r="52" fill="rgba(255,255,255,.08)" />
            <circle cx="60" cy="60" r="36" fill="rgba(255,255,255,.08)" />
            <path d="M66 14 30 66h24l-8 40 40-56H62l8-36z" fill="#ffd43b" />
          </svg>
        </div>
      </header>
      <main className="wrap">
        <div className="stats">
          {stats.map(([cls, n, label]) => (
            <div className={"stat " + cls} key={label}><div className="n">{n}</div><div className="l">{label}</div></div>
          ))}
        </div>
        <div className="bar">
          <div className="seg">
            {[["all", "全部分支"], ...branches.map((b) => [b, b])].map(([key, label]) => (
              <button key={key} className={branch === key ? "on" : ""} onClick={() => setParam("branch", key === "all" ? "" : key)}>{label}</button>
            ))}
          </div>
          <div className="chips">
            {STATUS.map(([key, label]) => (
              <button key={key} className={"chip" + (status === key ? " on" : "")} onClick={() => setParam("status", key === "all" ? "" : key)}>
                {label} <b>{scoped.filter((r) => matchStatus(r, key)).length}</b>
              </button>
            ))}
          </div>
          <select className="sel" value={range} onChange={(e) => setParam("range", e.target.value === "30" ? "" : e.target.value)}>
            <option value="7">最近 7 天</option>
            <option value="30">最近 30 天</option>
            <option value="90">最近 90 天</option>
          </select>
          <label className="search">
            <input type="search" placeholder="搜人名、文件、提交号或说明" value={q} onChange={(e) => setParam("q", e.target.value)} />
          </label>
        </div>
        <div>
          {error ? (
            <div className="empty">读不到数据，稍后刷新试试。</div>
          ) : !data ? (
            <div className="empty">正在读取…</div>
          ) : !rows.length ? (
            <div className="empty">
              <div className="big">{records.length ? "🔍" : "🎉"}</div>
              {records.length ? "没有符合条件的记录，换个筛选试试。" : "最近没查到炸线。"}
            </div>
          ) : (
            rows.map((r) => {
              const label = dayLabel(whenOf(r));
              const head = label !== lastDay ? <div className="day">{label}</div> : null;
              lastDay = label;
              return (
                <div key={r.key}>
                  {head}
                  {r.kind === "force" ? <ForceCard r={r} /> : <MergeCard r={r} />}
                </div>
              );
            })
          )}
        </div>
        <p className="note">{data?.note}</p>
      </main>
    </div>
  );
}
