import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../../api/client";
import { ToastProvider, useToast } from "../../components/Toast";
import { usePageTitle } from "../../lib/usePageTitle";
import "./merge.css";

// 和 backend/routers/merge.py 对应。单个会话的评审页 /merge/s/<id>/ 是 MergeReviewPortable 自带的页面，整页跳过去。
interface Session { id: string; repo: string; date: string; ours: string; theirs: string; files: number; decided: number; source: string; open: boolean }
interface Project { repo: string; ok: boolean; merging: boolean; conflicts: number; branch: string; incoming: string; error: string }

const sessionUrl = (id: string) => `/merge/s/${encodeURIComponent(id)}/`;

export default function MergeBoard() {
  usePageTitle("合并确认 · 大豆");
  return (
    <div className="merge-page">
      <ToastProvider>
        <Board />
      </ToastProvider>
    </div>
  );
}

function Board() {
  const toast = useToast();
  const client = useQueryClient();
  const sessions = useQuery({ queryKey: ["merge-sessions"], queryFn: () => api<{ sessions: Session[]; can_manage: boolean }>("/merge/sessions") });
  const canManage = !!sessions.data?.can_manage;
  const projects = useQuery({
    queryKey: ["merge-projects"], enabled: canManage,
    queryFn: () => api<{ projects: Project[] }>("/merge/projects"),
  });
  const [busy, setBusy] = useState("");
  const [addPath, setAddPath] = useState("");
  const [replayBase, setReplayBase] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const projectsRef = useRef<HTMLElement>(null);

  // 同一时间只跑一个操作；key 标出是哪个按钮在忙。
  const run = async (key: string, work: () => Promise<void>) => {
    setBusy(key);
    try {
      await work();
    } catch (error) {
      toast((error as Error).message, true);
    } finally {
      setBusy("");
    }
  };
  const refresh = () => client.invalidateQueries({ queryKey: ["merge-projects"] });
  const post = <T,>(path: string, body: object) => api<T>(path, { method: "POST", body: JSON.stringify(body) });

  const upload = (file: File) =>
    run("upload", async () => {
      toast("正在上传 " + file.name + " …");
      const data = await api<{ session: string }>("/merge/sessions/upload", {
        method: "POST", body: file,
        headers: { "Content-Type": file.name.endsWith(".json") ? "application/json" : "application/zip" },
      });
      location.href = sessionUrl(data.session);
    });

  return (
    <>
      <header><Link className="brand" to="/merge/">MERGE REVIEW <span>大豆 · 合并确认</span></Link></header>
      <main>
        <h1>合并确认</h1>
        <p className="muted">
          每次 merge 冲突一个会话：大家逐个文件选“当前分支 / 合入分支 / 两边都要”，最后由负责人在仓库那台机器上 <code>merge_review.py plan / apply</code>。
        </p>
        {canManage && <div className="actions">
          <button className="primary" disabled={!!busy} onClick={() => fileInput.current?.click()}>⬆ 上传会话</button>
          <input ref={fileInput} type="file" accept=".zip,.json" hidden
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
          {canManage && (
            <button onClick={() => { projectsRef.current?.scrollIntoView({ behavior: "smooth" }); document.getElementById("addPath")?.focus(); }}>
              🔍 检查本地工程 merge
            </button>
          )}
        </div>}
        {canManage && <p className="muted" style={{ marginTop: -14, fontSize: 12 }}>上传：把 sessions 下的会话目录整个打成 zip（或只传 snapshot.json）。勾“开放”的会话普通用户才看得到。</p>}

        {canManage && (
          <section className="panel" id="projects" ref={projectsRef}>
            <h2>本地工程</h2>
            <p className="muted">先在工程里自己 <code>git merge</code>，保留冲突别解决，然后点“采集冲突”。采集只读，不改仓库。</p>
            {(projects.data?.projects ?? []).map((p) => {
              const can = p.ok && p.merging && p.conflicts > 0;
              return (
                <div className="proj" key={p.repo}>
                  <div className="path">{p.repo}</div>
                  {!p.ok ? <span className="tag bad">{p.error}</span>
                    : p.merging && p.conflicts ? <span className="tag warn">合并中 · {p.branch} ← {p.incoming} · {p.conflicts} 个冲突</span>
                      : p.merging ? <span className="tag good">合并中 · 冲突已全部解决</span>
                        : <span className="tag">{p.branch} · 没有进行中的 merge</span>}
                  <button disabled={!!busy} onClick={() => run("check" + p.repo, async () => { await post("/merge/projects/check", { repo: p.repo }); await refresh(); })}>
                    {busy === "check" + p.repo ? "检查中…" : "重新检查"}
                  </button>
                  <button className="primary" disabled={!can || !!busy}
                    onClick={() => run("capture" + p.repo, async () => {
                      const data = await post<{ session: string }>("/merge/projects/capture", { repo: p.repo, replay_base: replayBase });
                      location.href = sessionUrl(data.session);
                    })}>
                    {busy === "capture" + p.repo ? "采集中…" : "采集冲突并开评审"}
                  </button>
                  <button disabled={!!busy} onClick={() => {
                    if (confirm("从列表移除 " + p.repo + "？（不动仓库）"))
                      run("remove" + p.repo, async () => { await post("/merge/projects/remove", { repo: p.repo }); await refresh(); });
                  }}>移除</button>
                </div>
              );
            })}
            {projects.data && !projects.data.projects.length && <div className="empty">还没有工程，在下面加一个。</div>}
            <div className="addrow">
              <input id="addPath" value={addPath} onChange={(e) => setAddPath(e.target.value)}
                placeholder="工程路径，例如 D:\OnePiece\onepiece 或 /Users/me/work/client" />
              <button disabled={!!busy} onClick={() => {
                if (!addPath.trim()) { toast("先填工程路径", true); return; }
                run("add", async () => { await post("/merge/projects", { repo: addPath }); setAddPath(""); await refresh(); });
              }}>加入列表</button>
            </div>
            <div className="addrow">
              <input value={replayBase} onChange={(e) => setReplayBase(e.target.value)}
                placeholder="（可选）--replay-base 已同步基线提交号；squash/每日同步时才需要" />
            </div>
          </section>
        )}

        <section className="panel">
          <h2>会话</h2>
          {sessions.error ? <div className="empty">{sessions.error.message}</div>
            : !sessions.data ? <div className="empty">正在读取…</div>
              : !sessions.data.sessions.length ? <div className="empty">{canManage ? "还没有会话。上传一个，或者检查本地工程采集。" : "管理员还没开放会话。"}</div>
                : (
                  <table>
                    <tbody>
                      <tr><th>分支</th><th className="hide-sm">仓库</th><th>采集日期</th><th>已选择</th>{canManage && <th>开放</th>}<th></th></tr>
                      {sessions.data.sessions.map((s) => {
                        const pct = s.files ? Math.floor((100 * s.decided) / s.files) : 0;
                        return (
                          <tr key={s.id}>
                            <td>
                              <a href={sessionUrl(s.id)}><b>{s.ours} ← {s.theirs}</b></a>
                              <div className="muted" style={{ fontSize: 12 }}>{s.id} · {s.source}</div>
                            </td>
                            <td className="mono hide-sm">{s.repo}</td>
                            <td>{s.date}</td>
                            <td>{s.decided} / {s.files}<div className="bar"><i style={{ width: pct + "%" }} /></div></td>
                            {canManage && (
                              <td>
                                <input type="checkbox" checked={s.open} disabled={!!busy} title="开放给普通用户"
                                  onChange={(e) => {
                                    const open = e.target.checked;
                                    run("open" + s.id, async () => {
                                      await post(`/merge/sessions/${encodeURIComponent(s.id)}/open`, { open });
                                      await client.invalidateQueries({ queryKey: ["merge-sessions"] });
                                    });
                                  }} />
                              </td>
                            )}
                            <td>
                              <a className="btn" href={sessionUrl(s.id)}>打开</a>{" "}
                              {canManage && (
                                <button disabled={!!busy} onClick={() => {
                                  if (confirm("删除会话 " + s.id + "？里面的评审选择会一起删掉。"))
                                    run("delete" + s.id, async () => {
                                      await api(`/merge/sessions/${encodeURIComponent(s.id)}`, { method: "DELETE" });
                                      await client.invalidateQueries({ queryKey: ["merge-sessions"] });
                                    });
                                }}>删除</button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
        </section>
      </main>
    </>
  );
}
