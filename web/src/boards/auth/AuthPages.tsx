import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { usePageTitle } from "../../lib/usePageTitle";
import "./auth.css";

export interface Me { open_id: string; name: string; department?: string; avatar: string; role: string; first_login: string; last_login: string }
export interface Site { name: string; icon: string; color: string; port: string; desc: string; manage: string; url: string; local: string; online: boolean;
  private?: boolean; private_note?: string; open?: boolean }

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me"), staleTime: 60_000 });
export const useSites = () => useQuery({ queryKey: ["sites"], queryFn: () => api<{ sites: Site[]; closed?: string[] }>("/sites"), refetchInterval: 30_000 });

// 退出走 console/server.py 的 /api/logout（它管登录 cookie）。
export async function logout() {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
}

export function LoginPage() {
  usePageTitle("登录 · 大豆");
  const [params] = useSearchParams();
  const next = params.get("next") || "";
  const error = params.get("error") || "";
  const { data } = useQuery({
    queryKey: ["login-info", next, error],
    queryFn: () => api<{ href: string; configured: boolean; error: string }>(
      "/auth/login-info?" + new URLSearchParams({ next, error }).toString()),
  });
  return (
    <div className="auth-page">
      <div className="wrap">
        <div className="card login">
          <div className="logo">豆</div>
          <h1>大豆后台</h1>
          <p className="muted">用飞书账号登录，登录后这台浏览器会一直记住你。</p>
          {data?.error && <p className="err">{data.error}</p>}
          {data && !data.configured && (
            <p className="err">还没配置飞书应用：在项目根目录的 .env 里填 FEISHU_APP_ID / FEISHU_APP_SECRET（参考 .env.example）。</p>
          )}
          <a className="btn primary" href={data?.href || "/auth/feishu"}>用飞书登录</a>
        </div>
      </div>
    </div>
  );
}

export function Portal({ me }: { me: Me }) {
  usePageTitle("大豆");
  const { data } = useSites();
  return (
    <div className="auth-page">
      <header><b>你好，{me.name}</b><span className="spacer" /><span className="muted">点上面或下面的卡片切换</span></header>
      <main>
        {(data?.sites ?? []).map((site) => (
          <Link className="site card" to={site.port + "/"} key={site.port}>
            <span className="ico" style={{ background: site.color }}>{site.icon}</span>
            <span><b>{site.name}</b><span className="muted">{site.desc}</span></span>
          </Link>
        ))}
      </main>
    </div>
  );
}
