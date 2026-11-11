import { useState } from "react";
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
}
interface Scan { root?: string; items: Item[]; seconds?: number; timing?: Record<string, number> }
interface ApplyResult { ok?: boolean; message?: string; commit?: string; scan?: Scan }

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

export default function MetaBoard() {
  usePageTitle("meta 检查");
  const client = useQueryClient();
  const scanQuery = useQuery({
    queryKey: ["meta-scan"], queryFn: () => api<Scan>("/meta"),
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false,
  });
  const logQuery = useQuery({ queryKey: ["meta-log"], queryFn: () => api<{ log: string }>("/meta/log"), refetchOnWindowFocus: false });
  const [message, setMessage] = useState<{ text: string; bad?: boolean }>({ text: "" });
  const [applying, setApplying] = useState(false);
  const scan = scanQuery.data;
  const busy = applying || scanQuery.isFetching;

  const items = scan?.items || [];
  const imports = items.filter((item) => item.kind === "import");
  const guids = items.filter((item) => item.kind === "guid");
  const fixable = guids.filter(fixableOf);
  const unknown = items.filter((item) => item.kind === "unknown");
  const files = fixable.reduce((sum, item) => sum + (item.refs || []).filter((ref) => ref.replace).length, 0);

  // 不弹窗：结果写进页面上的提示和下面的日志。
  const recheck = async () => {
    setMessage({ text: "" });
    await scanQuery.refetch();
    await logQuery.refetch();
  };
  const applyAll = async () => {
    setApplying(true);
    try {
      const result = await api<ApplyResult>("/meta/apply", { method: "POST", body: JSON.stringify({}) });
      setMessage({ text: result.message || "", bad: result.ok === false });
      if (result.scan) client.setQueryData(["meta-scan"], (old: Scan | undefined) => ({ ...old, ...result.scan! }));
    } catch (error) {
      setMessage({ text: (error as Error).message || "没完成", bad: true });
    } finally {
      setApplying(false);
      await logQuery.refetch();
    }
  };

  const stats: [string, number | string, string][] = [
    ["", scan ? imports.length : "…", "只改导入设置"],
    ["warn", scan ? guids.length : "…", "guid 变了"],
    ["ok", scan ? files : "…", "要换引用的文件"],
    ["bad", scan ? unknown.length + guids.length - fixable.length : "…", "不能自动处理"],
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
              部署机工程里本地改过的 .meta。只改导入设置的直接提交；guid 变了的，找工作区里也用这个旧 guid 的别的 meta（和路径无关），
              两边里后拿到这个 guid 的提交是重复提交，之后开始的引用换成本地新 guid，之前的是之前那个资源的，不动。
            </p>
            <div className="scan">
              {scan?.root && <span>工程 {scan.root} · </span>}
              {scanQuery.isFetching ? "检查中…" : scan?.seconds !== undefined ? `检查用时 ${scan.seconds} 秒${timing ? `（${timing}）` : ""}` : ""}
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
          <button disabled={busy} onClick={recheck}>{scanQuery.isFetching ? "检查中…" : "重新检查"}</button>
          <button className="primary" disabled={busy || !scan || (!imports.length && !fixable.length)} onClick={applyAll}>
            {applying ? "处理中…" : `全部处理并提交（导入设置 ${imports.length} 个，guid ${fixable.length} 个，换 ${files} 个文件）`}
          </button>
          <span className="sub">只 commit 不 push，跳过 pre-commit。「拉代码」会清掉本地修改，先处理完再拉。</span>
        </div>
        {message.text && <div className={"msg" + (message.bad ? " bad" : "")}>{message.text}</div>}
        {scanQuery.error && <div className="msg bad">{(scanQuery.error as Error).message}</div>}

        {scan && !items.length && <div className="empty"><div className="big">✓</div>没有本地改过的 meta。</div>}
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

        <div className="day">日志</div>
        <pre className="log">{logQuery.data?.log || "还没有记录。"}</pre>
      </main>
    </div>
  );
}
