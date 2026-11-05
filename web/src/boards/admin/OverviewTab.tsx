import { ActionButton, adminPost } from "./common";
import type { Status } from "./types";

export function Alive({ status }: { status?: Status }) {
  if (!status) return <>正在读取…</>;
  return (
    <>
      <span className={status.running ? "ok" : "bad"}>{status.running ? "在运行" : "没在运行"}</span> ·{" "}
      <span className={status.connected ? "ok" : "bad"}>{status.connected ? "飞书已连接" : "飞书未连接"}</span>
    </>
  );
}

export default function OverviewTab({ status }: { status?: Status }) {
  return (
    <>
      <div className="grid2">
        <section>
          <h2>运行状态</h2>
          <p className="meta">停止只结束大豆和它的飞书订阅，不会关掉飞书客户端。</p>
          <div>
            <Alive status={status} />
            {status && (
              <>
                <div className="meta">进程读的工程：{status.running_root || "还没有启动记录"}</div>
                <div className="meta">文件内容所在分支：{status.file_branch || "读不到"}{status.dirty ? "，工作区有未提交改动" : ""}</div>
                <div className="meta">最后一次回复：{status.last_reply || "没有"}</div>
              </>
            )}
          </div>
        </section>
        <section>
          <h2>引用索引</h2>
          <p className="meta">大豆每 6 小时自动更新一次。手动重建会扫整个 Assets，大约一分钟。进行中不要再点。</p>
          <div>
            {!status ? "正在读取…" : status.index_running ? "正在重建…"
              : "文件" + (status.index_exists ? `在，${status.index_rows} 条，更新于 ${status.index_time}` : "还没有")
                + (status.index_error ? "。上次失败：" + status.index_error : "")}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <ActionButton label="重建索引" done="开始重建索引" disabled={!!status?.index_running} run={() => adminPost("index/rebuild")} />
          </div>
        </section>
      </div>
      <section>
        <h2>最近日志</h2>
        <pre>{status?.log || ""}</pre>
      </section>
    </>
  );
}
