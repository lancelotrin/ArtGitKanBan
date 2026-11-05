import { useEffect, useState } from "react";
import { ToastProvider } from "../../components/Toast";
import { logout, type Me } from "../auth/AuthPages";
import ChatTab from "./ChatTab";
import CodeTab from "./CodeTab";
import { ActionButton, adminPost, useAdmin } from "./common";
import OverviewTab, { Alive } from "./OverviewTab";
import ReviewTab from "./ReviewTab";
import SettingsTab from "./SettingsTab";
import SitesTab from "./SitesTab";
import type { Status } from "./types";
import UsersTab from "./UsersTab";
import "./admin.css";

// 页签用网址的 #hash 记，和旧后台一样；#sec-xxx 会跳到对应页签再滚到那一段（门户卡片的“管理”链接用它）。
const TABS: [string, string, string][] = [
  ["overview", "◎", "概览"], ["code", "⌥", "代码"], ["review", "✓", "复盘"], ["chat", "✎", "对话"],
  ["sites", "◫", "网站"], ["settings", "⚙", "设置"], ["users", "☺", "账号"],
];
const TITLES = Object.fromEntries(TABS.map(([key, , title]) => [key, title]));
const ALIAS: Record<string, string> = {
  "sec-pull": "code", "sec-watch": "code", "sec-bomb": "code", "sec-review": "review", "sec-questions": "chat",
  "sec-reviews": "chat", "sec-misses": "chat", "sec-wish": "sites", "sec-models": "settings",
};

function useHashTab(): [string, string] {
  const read = () => decodeURIComponent(location.hash.slice(1));
  const [hash, setHash] = useState(read);
  useEffect(() => {
    const onChange = () => setHash(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const tab = ALIAS[hash] || (TITLES[hash] ? hash : "overview");
  return [tab, ALIAS[hash] ? hash : ""];
}

export default function AdminConsole({ me }: { me: Me }) {
  return (
    <div className="admin-page">
      <ToastProvider>
        <Shell me={me} />
      </ToastProvider>
    </div>
  );
}

function Shell({ me }: { me: Me }) {
  const [tab, anchor] = useHashTab();
  const { data: status } = useAdmin<Status>("status", 8000);
  useEffect(() => {
    document.title = TITLES[tab] + " · 大豆后台";
    if (anchor) window.setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ block: "start" }), 50);
    else window.scrollTo(0, 0);
  }, [tab, anchor]);

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand"><span className="logo">豆</span><div><b>大豆后台</b><small>只在本机打开</small></div></div>
        <nav>
          {TABS.map(([key, icon, title], i) => (
            <span key={key} style={{ display: "contents" }}>
              {i === 1 && <div className="group">功能</div>}
              {i === 5 && <div className="group">其他</div>}
              <a href={"#" + key} className={tab === key ? "on" : ""}><i className="ico">{icon}</i><span>{title}</span></a>
            </span>
          ))}
        </nav>
        <div className="side-foot">记下的是单聊，以及群里 @ 了大豆的问题。</div>
      </aside>
      <div className="content">
        <div className="topbar">
          <h1>{TITLES[tab]}</h1>
          <span id="topStatus"><Alive status={status} /></span>
          <div className="spacer" />
          <ActionButton className="primary" label="启动" done="已启动" disabled={!status || status.running} run={() => adminPost("start")} />
          <ActionButton label="停止" done="已停止" disabled={!status || !status.running} run={() => adminPost("stop")} />
          <span className="meta" style={{ margin: 0 }}>{me.name} · 管理员</span>
          <button onClick={logout}>退出</button>
        </div>
        <main>
          {tab === "overview" && <OverviewTab status={status} />}
          {tab === "code" && <CodeTab status={status} />}
          {tab === "review" && <ReviewTab />}
          {tab === "chat" && <ChatTab />}
          {tab === "sites" && <SitesTab />}
          {tab === "settings" && <SettingsTab status={status} />}
          {tab === "users" && <UsersTab me={me} />}
        </main>
      </div>
    </div>
  );
}
