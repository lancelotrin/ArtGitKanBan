import { useEffect, useRef, useState } from "react";
import { ActionButton, adminPost, shortTime, useAdmin } from "./common";
import type { Bilibili, Items, Models, ModelStat, Person, Status } from "./types";

function cell(stat: ModelStat = {}) {
  return {
    count: <>{stat.ok || 0} / {stat.fail ? <span className="bad">{stat.fail}</span> : 0}</>,
    ok: stat.last_ok_at ? shortTime(stat.last_ok_at) : "—",
    fail: stat.last_fail_at ? (
      <>{shortTime(stat.last_fail_at)}{stat.last_error && <div className="meta" style={{ margin: 0 }}>{stat.last_error}</div>}</>
    ) : "—",
  };
}

function ModelsSection() {
  const { data } = useAdmin<Models>("models");
  const models = data?.models || {};
  const started = data?.started_at ? "大豆启动于 " + shortTime(data.started_at) + (data.provider ? "，智谱接口 " + data.provider : "") : "";
  const configured = (kind: "chat" | "vision", label: string) => (
    <div>
      <label>{label}（<code>{data?.env?.[kind] ?? (kind === "chat" ? "LLM_MODEL" : "LLM_VISION_MODEL")}</code>）</label>
      <input type="text" readOnly value={data ? data.current[kind] + (data.current[kind] === data.defaults[kind] ? "（默认）" : "") : ""} />
    </div>
  );
  return (
    <section id="sec-models">
      <h2>模型</h2>
      <p className="meta">大豆这次启动后实际在用的模型。次数是今天的，0 点清零；主模型失败时才会轮到备用模型。不显示 Key。</p>
      <div className="row">
        {configured("chat", "聊天回答")}
        {configured("vision", "看图")}
      </div>
      <p className="meta" style={{ marginTop: 6 }}>
        模型在项目根目录的 <code>.env</code> 里改，没填就用默认的智谱 flash 模型；改完点右上角「停止」再「启动」。评价用的 composer-2.5 是 Cursor 的，这里不改。
      </p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>用途</th><th>模型</th><th>备用</th><th>今天成功 / 失败</th><th>最近成功</th><th>最近失败</th></tr></thead>
          <tbody>
            {!(data?.uses || []).length && <tr><td colSpan={6} className="meta">大豆还没启动过，启动后这里会显示。</td></tr>}
            {(data?.uses || []).map((use) => {
              if (!use.on) return <tr key={use.use}><td>{use.use}</td><td colSpan={5} className="meta">没开：{use.off_reason}</td></tr>;
              const main = cell(models[use.model]);
              return (
                <tr key={use.use}>
                  <td>{use.use}</td>
                  <td><code>{use.model}</code><div className="meta" style={{ margin: 0 }}>{use.provider}</div></td>
                  <td>
                    {(use.fallbacks || []).length ? use.fallbacks!.map((name) => (
                      <div key={name}><code>{name}</code> <span className="meta">今天 {cell(models[name]).count}</span></div>
                    )) : <span className="meta">无</span>}
                  </td>
                  <td>{main.count}</td><td>{main.ok}</td><td>{main.fail}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="meta">{started}</p>
    </section>
  );
}

function BilibiliSection() {
  const { data } = useAdmin<Bilibili>("bilibili", false);
  const [cookie, setCookie] = useState("");
  const [qr, setQr] = useState("");
  const [message, setMessage] = useState("");
  const timer = useRef(0);
  useEffect(() => { if (data?.message) setMessage(data.message); }, [data?.message]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  // 每 2 秒问一次扫码结果，直到不再是“等待扫码/已扫码”。
  const poll = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        const result = await adminPost<{ state?: string; message?: string }>("bilibili", { action: "qr_poll" }).catch((error: Error) => ({ state: "error", message: error.message }));
        setMessage(result.message || "");
        if (result.state === "waiting" || result.state === "scanned") {
          poll();
          return;
        }
        setQr("");
      } catch (error) {
        setMessage("查扫码状态失败：" + (error as Error).message);
        poll();
      }
    }, 2000);
  };
  const saveCookie = (clearing: boolean) => async () => {
    const result = await adminPost<Bilibili>("bilibili", { cookie: clearing ? "" : cookie });
    setCookie("");
    setMessage(result.message || "");
    if (!clearing && !result.login) throw new Error(result.message || "没登录成功");
  };
  return (
    <section>
      <h2>B 站登录</h2>
      <p className="meta">读 AI 字幕用。点「扫码登录」，用 B 站 App 扫一下并在手机上确认，登录信息自动存到这台机器，页面不回显。扫不了再手动粘贴 Cookie。</p>
      <div className="row">
        <ActionButton className="primary" label="扫码登录" done="二维码已生成" run={async () => {
          const result = await adminPost<{ qr?: string; message?: string }>("bilibili", { action: "qr" });
          setMessage(result.message || "");
          setQr(result.qr || "");
          poll();
        }} />
        <input type="password" autoComplete="off" placeholder="或粘贴 Cookie / SESSDATA 后点保存" style={{ maxWidth: 360 }}
          value={cookie} onChange={(e) => setCookie(e.target.value)} />
        <ActionButton label="保存" done="已保存" run={saveCookie(false)} />
        <ActionButton label="清除" done="已清除" run={saveCookie(true)} />
      </div>
      {qr && <img src={qr} alt="B 站登录二维码" style={{ marginTop: 12, width: 200, height: 200, borderRadius: 8, background: "#fff" }} />}
      <p className="meta">{message}</p>
    </section>
  );
}

export default function SettingsTab({ status }: { status?: Status }) {
  const people = useAdmin<Items<Person>>("people", 8000).data?.items ?? [];
  const reminders = status?.reminders ?? [];
  const agents = status?.agents ?? [];
  return (
    <>
      <ModelsSection />
      <div className="grid2">
        <section>
          <h2>提醒</h2>
          <p className="meta">到点在原来的群里 @ 对方，只提醒一次。标了加急的，到点还会发应用内加急。</p>
          <table>
            <thead><tr><th>时间</th><th>提醒谁</th><th>内容</th><th></th></tr></thead>
            <tbody>
              {reminders.length ? reminders.map((item) => (
                <tr key={item.id}>
                  <td className="time">{item.when}</td><td>{item.who}</td><td>{item.task}</td>
                  <td><ActionButton label="取消" done="已取消" run={() => adminPost("reminders/delete", { id: item.id })} /></td>
                </tr>
              )) : <tr><td colSpan={4}>没有待提醒的</td></tr>}
            </tbody>
          </table>
        </section>
        <section>
          <h2>人名</h2>
          <p className="meta">只显示职务和姓名。</p>
          <table>
            <thead><tr><th>职务</th><th>姓名</th><th></th></tr></thead>
            <tbody>
              {people.length ? people.map((item) => (
                <tr key={item.role}>
                  <td>{item.role}</td><td>{item.name}</td>
                  <td><ActionButton label="删除" done="已删除" run={() => adminPost("people/delete", { role: item.role })} /></td>
                </tr>
              )) : <tr><td colSpan={3}>没有人名</td></tr>}
            </tbody>
          </table>
        </section>
      </div>
      <BilibiliSection />
      <section>
        <h2>本地程序</h2>
        <p className="meta">同事要改自己电脑上的文件时，在他电脑上运行 local_agent.py --root 他的工程目录。大豆不改这台机器的工程。下表是最近交过文件的人。</p>
        <table>
          <thead><tr><th>时间</th><th>目录</th><th>身份</th></tr></thead>
          <tbody>
            {agents.length ? agents.map((item, i) => (
              <tr key={i}><td className="time">{item.seen}</td><td>{item.root}</td><td>{item.who}</td></tr>
            )) : <tr><td colSpan={3}>还没有人从自己的电脑连过来</td></tr>}
          </tbody>
        </table>
      </section>
    </>
  );
}
