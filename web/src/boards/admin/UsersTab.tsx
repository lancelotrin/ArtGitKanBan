import { useState } from "react";
import { useToast } from "../../components/Toast";
import type { Me } from "../auth/AuthPages";
import { adminPost, useAdmin, useRefreshAdmin } from "./common";
import type { Account, Items } from "./types";

function Row({ user, self }: { user: Account; self: boolean }) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const refresh = useRefreshAdmin();
  const admin = user.role === "admin";
  const act = async (action: string) => {
    if (action === "remove" && !confirm(`移除 ${user.name}？对方下次用飞书登录会重新变成普通用户。`)) return;
    setBusy(true);
    try {
      await adminPost("users", { action, open_id: user.open_id });
      toast("已更新。");
      await refresh();
    } catch (error) {
      toast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <tr>
      <td>{user.name}{self && <span className="meta">（我）</span>}</td>
      <td>{user.department || <span className="meta">—</span>}</td>
      <td>{admin ? <b>管理员</b> : "普通用户"}</td>
      <td>{user.first_login}</td>
      <td>{user.last_login}</td>
      <td>
        {!self && (
          <>
            <button disabled={busy} onClick={() => act(admin ? "user" : "admin")}>{admin ? "取消管理员" : "设为管理员"}</button>{" "}
            <button disabled={busy} onClick={() => act("remove")}>移除</button>
          </>
        )}
      </td>
    </tr>
  );
}

export default function UsersTab({ me }: { me: Me }) {
  const users = useAdmin<Items<Account>>("users", false).data?.items ?? [];
  return (
    <section>
      <h2>账号</h2>
      <p className="meta">用飞书登录过的人都在这里。普通用户只能用子站（许愿、+1、合并选边）；管理员还能进后台改设置、管心愿墙、在本机检查工程，并能代别人处理心愿。</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>姓名</th><th>部门</th><th>角色</th><th>第一次登录</th><th>最近登录</th><th></th></tr></thead>
          <tbody>
            {users.length ? users.map((u) => <Row key={u.open_id} user={u} self={u.open_id === me.open_id} />)
              : <tr><td colSpan={6} className="meta">还没有人登录过。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
