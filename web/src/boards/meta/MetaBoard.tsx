import { Component, type ReactNode, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { usePageTitle } from "../../lib/usePageTitle";
import "./meta.css";

// 和 backend/routers/meta.py → bot/meta_fix.py 的 scan / apply_all 对应。
interface Commit {
  hash: string; short: string; author: string; date: string; subject?: string; dup_paths?: string[];
  mine_later?: boolean; mine?: Commit | Record<string, never>; other?: Commit;
}
interface Ref { path: string; count: number; dirty: boolean; replace: boolean; reason: string; since?: Commit | Record<string, never> }
interface Item {
  path: string;
  kind: "import" | "guid" | "unknown";
  old_guid: string;
  new_guid: string;
  note?: string;
  commit?: Commit | Record<string, never>;
  refs?: Ref[];
  grep_done?: boolean;
  notified?: { ok: boolean; name: string; message: string; at?: string };
}
interface Note { ok: boolean; message: string }
interface Scan {
  root?: string; items: Item[]; seconds?: number; timing?: Record<string, number>;
  checked_at?: string; who?: string; pull?: Note; unity?: Note;
}
interface Job {
  running: boolean; action: string; title: string; step: string; who: string;
  started_at: string; finished_at: string; ok: boolean | null; message: string;
}
interface View { job: Job; scan: Scan | null }
interface Push {
  hash?: string; short?: string; author?: string; date?: string; message?: string; files?: string[];
  at: string; who: string; branch: string; remote: string; pushed: boolean; output: string;
}

const commitOf = (item: Item) => item.commit as Commit | undefined;
const fixableOf = (item: Item) => item.kind === "guid" && !!commitOf(item)?.hash && !!item.grep_done;

function RefList({ refs }: { refs: Ref[] }) {
  return (
    <div className="refs">
      {refs.map((ref) => {
        const since = ref.since as Commit | undefined;
        return (
          <div className="ref" key={ref.path}>
            <code>{ref.path}</code>
            <span className="sub">
              引用 {ref.count} 处{since?.hash ? ` · ${since.short} ${since.author} ${since.date} 开始引用` : ""}
              {!ref.replace && ` · ${ref.reason}`}
            </span>
            {ref.replace && ref.dirty && <span className="warn">本地也有别的修改，会一起提交</span>}
          </div>
        );
      })}
    </div>
  );
}

function GuidCard({ item }: { item: Item }) {
  const commit = commitOf(item);
  const refs = item.refs || [];
  const change = refs.filter((ref) => ref.replace);
  const keep = refs.filter((ref) => !ref.replace);
  const ok = fixableOf(item);
  return (
    <div className={"card " + (ok ? (commit?.mine_later ? "s-ok" : "s-warn") : "s-bad")}>
      <div className="fp"><code>{item.path}</code></div>
      <div className="sub">提交里是 <code>{item.old_guid}</code>，本地是 <code>{item.new_guid}</code></div>
      {!commit?.hash ? <div className="bad">{item.note || "找不到重复 guid 的提交，不处理。"}</div> : (
        <>
          <div className="sub">旧 guid 和这些 meta 重复：{(commit.dup_paths || []).map((dup) => <code key={dup}>{dup}</code>)}</div>
          <div className="sub">重复提交（两边里后拿到这个 guid 的）：<b>{commit.short}</b> {commit.author} {commit.date} {commit.subject}</div>
          {item.notified && (
            <div className={item.notified.ok ? "sub" : "bad"}>
              {item.notified.message}{item.notified.at ? `（${item.notified.at}）` : ""}
            </div>
          )}
          {commit.mine_later ? (
            <div className="sub">
              本地这个 meta 是后来的；另一个在 {commit.other?.short} {commit.other?.author} {commit.other?.date} 就有这个 guid。
              重复提交及之后开始的引用换成本地 guid，之前的不动。
            </div>
          ) : (
            <div className="warn">
              本地这个 meta 是之前的（{(commit.mine as Commit | undefined)?.short || "?"}），另一个是后来的。重复提交之前的引用换成本地 guid，之后的不动。
            </div>
          )}
          {item.grep_done === false && <div className="bad">没查完哪些文件引用旧 guid，这个先不处理。</div>}
          <div className="group">换成本地 guid · {change.length} 个文件</div>
          {change.length > 0 && <RefList refs={change} />}
          {keep.length > 0 && (
            <>
              <div className="group">不动 · {keep.length} 个文件</div>
              <RefList refs={keep} />
            </>
          )}
        </>
      )}
    </div>
  );
}

function PushRow({ push, canRetry, onRetry, busy }: { push: Push; canRetry: boolean; onRetry: () => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const files = push.files || [];
  const [title, ...body] = String(push.message || "").split("\n");
  return (
    <div className={"card " + (push.pushed ? "s-ok" : "s-bad")}>
      <div className="push-top">
        <span className={"pill " + (push.pushed ? "p-ok" : "p-bad")}>{push.pushed ? "已推送" : "推送失败"}</span>
        <b>{push.short || "?"}</b>
        <span className="sub">{push.branch} → {push.remote}/{push.branch}</span>
        <span className="sub">{push.at}{push.who ? ` · ${push.who} 点的` : ""}</span>
        {canRetry && <button className="ghost" disabled={busy} onClick={onRetry}>再推一次</button>}
      </div>
      <div className="subj">{title}</div>
      {body.filter((line) => line.trim()).length > 0 && <div className="sub pre">{body.join("\n").trim()}</div>}
      <div className="sub">提交号 <code>{push.hash}</code> · 作者 {push.author} · {push.date}</div>
      <button className="text" onClick={() => setOpen(!open)}>{open ? "收起" : `改了 ${files.length} 个文件，展开`}</button>
      {open && <div className="refs">{files.map((file) => <div className="ref" key={file}><code>{file}</code></div>)}</div>}
      {!push.pushed && push.output && <pre className="out">{push.output}</pre>}
    </div>
  );
}

/** 页面里哪里出错都只显示错误，不整页白掉。 */
class Guard extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="meta-page"><main className="wrap" style={{ paddingTop: 24 }}>
        <div className="msg bad">页面出错了：{this.state.error.message}。如果刚更新过，重启大豆后台（./restart.sh）后再刷新页面。</div>
      </main></div>
    );
  }
}

export default function MetaBoard() {
  return <Guard><MetaPage /></Guard>;
}

function MetaPage() {
  usePageTitle("meta 检查");
  const client = useQueryClient();
  // 打开页面只读上次结果和进度，不自动检查；有任务在跑就每 2 秒刷新一次。
  const viewQuery = useQuery({
    queryKey: ["meta-view"], queryFn: () => api<View>("/meta"),
    refetchInterval: (query) => (query.state.data?.job?.running ? 2000 : 30_000),
  });
  // 后台没重启时 /meta 还是旧格式（没有 job），当成没有数据，别让页面崩掉。
  const viewOk = !!viewQuery.data && typeof viewQuery.data === "object" && "job" in viewQuery.data;
  const running = viewOk && !!viewQuery.data?.job?.running;
  const logQuery = useQuery({ queryKey: ["meta-log"], queryFn: () => api<{ log: string }>("/meta/log"), refetchInterval: running ? 2000 : 30_000 });
  const pushQuery = useQuery({ queryKey: ["meta-pushes"], queryFn: () => api<{ items: Push[] }>("/meta/pushes"), refetchInterval: running ? 4000 : 30_000 });
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const job = viewOk ? viewQuery.data?.job : undefined;
  const scan = (viewOk && viewQuery.data?.scan) || undefined;
  const busy = sending || running;

  const items = Array.isArray(scan?.items) ? scan!.items : [];
  const imports = items.filter((item) => item.kind === "import");
  const guids = items.filter((item) => item.kind === "guid");
  const fixable = guids.filter(fixableOf);
  const unknown = items.filter((item) => item.kind === "unknown");
  const files = fixable.reduce((sum, item) => sum + (item.refs || []).filter((ref) => ref.replace).length, 0);
  const pushes = Array.isArray(pushQuery.data?.items) ? pushQuery.data!.items : [];

  // 不弹窗：开始后看上面的进度、下面的日志。
  const start = async (action: "check" | "apply" | "push") => {
    setSending(true);
    setError("");
    try {
      const result = await api<{ ok: boolean; message: string }>("/meta/" + action, { method: "POST", body: JSON.stringify({}) });
      if (!result.ok) setError(result.message);
    } catch (e) {
      setError((e as Error).message || "没开始");
    } finally {
      setSending(false);
      await client.invalidateQueries({ queryKey: ["meta-view"] });
      await client.invalidateQueries({ queryKey: ["meta-log"] });
    }
  };

  const stats: [string, number | string, string][] = [
    ["", scan ? imports.length : "–", "只改导入设置"],
    ["warn", scan ? guids.length : "–", "guid 变了"],
    ["ok", scan ? files : "–", "要换引用的文件"],
    ["bad", scan ? unknown.length + guids.length - fixable.length : "–", "不能自动处理"],
  ];
  const timing = scan?.timing ? Object.entries(scan.timing).map(([name, sec]) => `${name} ${sec} 秒`).join("，") : "";

  return (
    <div className="meta-page">
      <header className="hero">
        <div className="wrap">
          <div>
            <div className="brand"><i>豆</i>大豆</div>
            <h1>meta 检查</h1>
            <p>
              检查前先把 .env 配的工程当前分支拉到最新（只快进），再让 Unity 刷新资源重新生成 meta，然后查本地改过的 .meta。
              只改导入设置的直接提交；guid 和别的 meta 重复的，两边里后拿到这个 guid 的提交是重复提交，之后开始的引用换成本地新 guid，之前的不动。提交后自动推送。
            </p>
            <div className="scan">
              {scan?.root && <span>工程 {scan.root}</span>}
              {scan?.checked_at && <span> · 上次检查 {scan.checked_at}{scan.who ? `（${scan.who}）` : ""}，用时 {scan.seconds} 秒{timing ? `（${timing}）` : ""}</span>}
            </div>
          </div>
          <svg className="art" viewBox="0 0 120 120" aria-hidden="true">
            <circle cx="60" cy="60" r="52" fill="rgba(255,255,255,.08)" />
            <rect x="34" y="24" width="52" height="68" rx="8" fill="rgba(255,255,255,.9)" />
            <path d="M44 42h32M44 54h32M44 66h20" stroke="#1971c2" strokeWidth="5" strokeLinecap="round" />
            <circle cx="82" cy="84" r="14" fill="#ffd43b" />
            <path d="M76 84l4 4 8-8" stroke="#1d3557" strokeWidth="4" fill="none" strokeLinecap="round" />
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
          <button disabled={busy} onClick={() => start("check")}>拉最新、刷新 Unity 并检查</button>
          <button className="primary" disabled={busy || !scan || (!imports.length && !fixable.length)} onClick={() => start("apply")}>
            {`全部处理、提交并推送（导入设置 ${imports.length} 个，guid ${fixable.length} 个，换 ${files} 个文件）`}
          </button>
          <span className="sub">提交和推送都跳过工程的 git 钩子。</span>
        </div>
        {running && job && (
          <div className="msg run">
            <span className="spin" />{job.who ? `${job.who} 点了` : ""}「{job.title}」，{String(job.started_at || "").slice(11)} 开始，现在：{job.step || "排队"}
          </div>
        )}
        {!running && job?.finished_at && (
          <div className={"msg" + (job.ok ? "" : " bad")}>{String(job.finished_at).slice(5)} {job.title}：{job.message}</div>
        )}
        {error && <div className="msg bad">{error}</div>}
        {viewQuery.error && <div className="msg bad">{(viewQuery.error as Error).message}</div>}
        {viewQuery.data && !viewOk && <div className="msg bad">接口还是旧版本，重启大豆后台（./restart.sh）后再刷新页面。</div>}
        {scan && (scan.pull?.ok === false || scan.unity?.ok === false) && (
          <div className="msg warn">
            上次检查时：{[scan.pull, scan.unity].filter((note) => note && !note.ok).map((note) => note!.message).join(" ")}
          </div>
        )}

        {!scan && !running && <div className="empty"><div className="big">◈</div>还没检查过，点「拉最新、刷新 Unity 并检查」。</div>}
        {scan && !items.length && <div className="empty"><div className="big">✓</div>上次检查没有本地改过的 meta。</div>}
        {imports.length > 0 && (
          <>
            <div className="day">只改导入设置 · {imports.length} 个</div>
            <div className="card s-ok">
              {imports.map((item) => <div className="fp" key={item.path}><code>{item.path}</code></div>)}
            </div>
          </>
        )}
        {guids.length > 0 && <div className="day">guid 变了 · {guids.length} 个</div>}
        {guids.map((item) => <GuidCard key={item.path} item={item} />)}
        {unknown.length > 0 && (
          <>
            <div className="day">读不到 guid 行，没处理 · {unknown.length} 个</div>
            <div className="card s-bad">
              {unknown.map((item) => <div className="fp" key={item.path}><code>{item.path}</code></div>)}
            </div>
          </>
        )}

        <div className="day">推送记录 · {pushes.length} 条</div>
        {pushes.length ? pushes.map((push, index) => (
          <PushRow key={push.at + (push.hash || "")} push={push} busy={busy}
            canRetry={index === 0 && !push.pushed} onRetry={() => start("push")} />
        )) : <div className="sub">还没有推送过。</div>}

        <div className="day">日志</div>
        <pre className="log">{logQuery.data?.log || "还没有记录。"}</pre>
      </main>
    </div>
  );
}
