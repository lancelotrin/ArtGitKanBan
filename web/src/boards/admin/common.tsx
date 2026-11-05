import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToast } from "../../components/Toast";

// 后台接口都在 /api/v1/admin/<名字>，逻辑在 console.admin_get / admin_post。
// 有些写接口失败时仍回 200 + {ok:false, message}，这里统一当成出错。
export interface Result { ok?: boolean; message?: string }

export function useAdmin<T>(name: string, refetchInterval: number | false = 30_000) {
  return useQuery({ queryKey: ["admin", name], queryFn: () => api<T>("/admin/" + name), refetchInterval });
}

export async function adminPost<T extends Result>(name: string, body: object = {}): Promise<T> {
  const data = await api<T>("/admin/" + name, { method: "POST", body: JSON.stringify(body) });
  if (data && data.ok === false) throw new Error(data.message || "没成功");
  return data;
}

export function useRefreshAdmin() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: ["admin"] });
}

/** 按下后显示“处理中…”，做完刷新后台数据，成功弹 done，失败弹错误。 */
export function ActionButton({ label, done, run, className, disabled, style }: {
  label: string; done: string; run: () => Promise<unknown>; className?: string; disabled?: boolean; style?: CSSProperties;
}) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const refresh = useRefreshAdmin();
  const press = async () => {
    setBusy(true);
    let failed = false;
    try {
      await run();
    } catch (error) {
      failed = true;
      toast((error as Error).message || "没完成", true);
    } finally {
      setBusy(false);
    }
    await refresh();
    if (!failed) toast(done);
  };
  return (
    <button type="button" className={className} style={style} disabled={busy || disabled} onClick={press}>
      {busy ? "处理中…" : label}
    </button>
  );
}

/** 输入框跟着服务器的值走，用户改过之后就不再被覆盖，直到 reset()。 */
export function useSynced<T>(server: T | undefined, fallback: T): [T, (value: T) => void, () => void] {
  const [value, setValue] = useState<T>(server ?? fallback);
  const dirty = useRef(false);
  const key = JSON.stringify(server);
  useEffect(() => {
    if (!dirty.current && server !== undefined) setValue(server);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const change = (next: T) => {
    dirty.current = true;
    setValue(next);
  };
  const reset = () => {
    dirty.current = false;
    if (server !== undefined) setValue(server);
  };
  return [value, change, reset];
}

/** 长文本默认收起两行，旁边一个“展开”。 */
export function Clip({ text }: { text?: string }) {
  const [open, setOpen] = useState(false);
  const value = String(text ?? "").trim();
  if (!value) return null;
  if (value.length <= 80 && !value.includes("\n")) return <div className="meta">{value}</div>;
  return (
    <>
      <div className={"meta clip" + (open ? " open" : "")}>{value}</div>
      <button type="button" className="text" onClick={() => setOpen(!open)}>{open ? "收起" : "展开"}</button>
    </>
  );
}

const PAGE_SIZE = 10;

/** 每页 10 条的表格，上下各一个翻页条。 */
export function PagedTable<T>({ items, head, empty, row }: {
  items: T[]; head: ReactNode; empty: ReactNode; row: (item: T, index: number) => ReactNode;
}) {
  const [page, setPage] = useState(0);
  const count = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const current = Math.min(page, count - 1);
  const top = useRef<HTMLDivElement>(null);
  const go = (dir: number) => {
    setPage(Math.max(0, Math.min(count - 1, current + dir)));
    top.current?.scrollIntoView({ block: "nearest" });
  };
  const pager = (ref?: React.Ref<HTMLDivElement>) => (
    <div className="pager" ref={ref}>
      <span className="meta">{items.length} 条</span>
      <button type="button" disabled={current <= 0} onClick={() => go(-1)}>上一页</button>
      <span>{current + 1} / {count}</span>
      <button type="button" disabled={current >= count - 1} onClick={() => go(1)}>下一页</button>
    </div>
  );
  const slice = items.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE);
  return (
    <>
      {pager(top)}
      <div className="table-wrap">
        <table>
          <thead>{head}</thead>
          <tbody>{slice.length ? slice.map((item, i) => row(item, current * PAGE_SIZE + i)) : empty}</tbody>
        </table>
      </div>
      {pager()}
    </>
  );
}

export const shortTime = (value?: string) => String(value || "").slice(5, 16).replace("T", " ");
export const stamp = (value?: string) => String(value || "").replace("T", " ").slice(0, 16);

export function OnOff({ on, yes = "开着", no = "关着" }: { on: boolean; yes?: string; no?: string }) {
  return <span className={on ? "ok" : "bad"}>{on ? yes : no}</span>;
}

/** 多行结果：换行显示。 */
export function Lines({ text }: { text?: string }) {
  if (!text) return null;
  return <div className="meta" style={{ whiteSpace: "pre-wrap" }}>{text}</div>;
}
