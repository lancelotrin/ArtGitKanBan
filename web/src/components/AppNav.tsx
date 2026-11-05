import { NavLink } from "react-router";
import { logout, useMe, useSites } from "../boards/auth/AuthPages";

/** 每个页面顶上的导航条：在各个看板之间站内切换（不开新标签、不整页刷新）。子站列表来自 /api/v1/sites。 */
export default function AppNav() {
  const me = useMe().data;
  const sites = useSites().data?.sites ?? [];
  return (
    <nav className="app-nav">
      <NavLink to="/" end className="app-nav-brand"><span>豆</span>大豆</NavLink>
      <div className="app-nav-links">
        <NavLink to="/" end>{me?.role === "admin" ? "后台" : "首页"}</NavLink>
        {sites.map((site) => (
          <NavLink key={site.port} to={site.port + "/"}>
            <i style={{ color: site.color }}>{site.icon}</i>{site.name}
          </NavLink>
        ))}
      </div>
      {me && (
        <div className="app-nav-me">
          <span>{me.name}{me.department && <span className="app-nav-dept">{me.department}</span>}</span>
          <button onClick={logout}>退出</button>
        </div>
      )}
    </nav>
  );
}
