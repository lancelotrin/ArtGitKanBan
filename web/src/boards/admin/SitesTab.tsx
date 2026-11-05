import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { useToast } from "../../components/Toast";
import { useSites } from "../auth/AuthPages";
import { adminPost, shortTime, useAdmin } from "./common";
import type { AdminWish, WishAdmin } from "./types";

const WISH_STATUS: Record<string, string> = { open: "待接", doing: "进行中", done: "已实现" };

function Sites() {
  const { data, error } = useSites();
  return (
    <section>
      <h2>网站</h2>
      <p className="meta">大豆在这台机器上开的内网网页。同事要能打开，Windows 防火墙要放行对应端口（只放 10.x 内网）。新加的网站也会出现在这里。</p>
      <div className="sites">
        {error ? "读取失败。" : !data ? "正在读取…" : !data.sites.length ? "还没有网站。" : data.sites.map((site) => (
          <div className="site" key={site.port}>
            <div className="site-top">
              <span className="site-ico" style={{ background: site.color }}>{site.icon}</span>
              <div><div className="site-name">{site.name}</div><div className="site-port">路径 {site.port}</div></div>
              <span className={"dot " + (site.online ? "up" : "down")}>● {site.online ? "在线" : "没起来"}</span>
            </div>
            <div className="site-desc">{site.desc}</div>
            <div className="site-url">{site.url}</div>
            <div className="row">
              <Link className="btnlink primary" to={site.port + "/"}>打开</Link>
              <button type="button" className="btnlink" onClick={() => navigator.clipboard?.writeText(site.url)}>复制地址</button>
              {site.manage && <a className="btnlink" href={site.manage}>管理</a>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function WishEditor({ wish, roles, onSave, onCancel }: {
  wish: AdminWish; roles: string[]; onSave: (fields: Partial<AdminWish>) => Promise<void>; onCancel: () => void;
}) {
  const [fields, setFields] = useState({ title: wish.title, role: wish.role || "其他", status: wish.status, taker: wish.taker || "", detail: wish.detail || "" });
  const [saving, setSaving] = useState(false);
  const set = (key: keyof typeof fields) => (e: { target: { value: string } }) => setFields({ ...fields, [key]: e.target.value });
  return (
    <tr className="wedit">
      <td></td>
      <td colSpan={8}>
        <div className="wgrid">
          <input type="text" maxLength={60} value={fields.title} onChange={set("title")} />
          <select value={fields.role} onChange={set("role")}>{roles.map((r) => <option key={r}>{r}</option>)}</select>
          <select value={fields.status} onChange={set("status")}>
            {Object.entries(WISH_STATUS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
          </select>
          <input type="text" maxLength={20} placeholder="接的人" value={fields.taker} onChange={set("taker")} />
          <textarea maxLength={800} placeholder="补充说明" value={fields.detail} onChange={set("detail")} />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="primary" disabled={saving} onClick={async () => {
            setSaving(true);
            try { await onSave(fields as Partial<AdminWish>); } finally { setSaving(false); }
          }}>保存</button>
          <button onClick={onCancel}>取消</button>
          <span className="meta" style={{ margin: 0 }}>改成「待接」会清掉接的人；「进行中」要填接的人。</span>
        </div>
      </td>
    </tr>
  );
}

function WishManager() {
  const { data } = useAdmin<WishAdmin>("wish");
  const client = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(0);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  // 成功就关掉编辑行；失败弹出原因，编辑行留着。
  const act = async (payload: object) => {
    try {
      const result = await adminPost<WishAdmin>("wish", payload);
      toast(result.message || "好了");
      client.setQueryData(["admin", "wish"], result);
      client.invalidateQueries({ queryKey: ["wishes"] });
      setEditing(0);
      return true;
    } catch (error) {
      toast((error as Error).message, true);
      return false;
    }
  };
  const counts = data?.counts || {};
  const key = search.trim().toLowerCase();
  const rows = (data?.wishes || []).filter((w) => (!status || w.status === status)
    && (!key || [w.title, w.detail, w.author, w.taker, w.role].join(" ").toLowerCase().includes(key)));
  const roles = data?.roles || ["美术", "程序", "QA", "策划", "其他"];
  return (
    <section id="sec-wish">
      <h2>心愿墙管理</h2>
      <p className="meta">这里能改任何心愿，不受「只有许愿人/接的人」限制。删除不能恢复。</p>
      <div className="row wbar">
        <label className="switch">
          <input type="checkbox" checked={data?.settings.allow_create !== false}
            onChange={(e) => act({ action: "allow_create", value: e.target.checked })} />
          <span></span>允许发布新心愿
        </label>
        <span className="meta" style={{ margin: 0 }}>
          共 {data?.total || 0} 个：待接 {counts.open || 0}，进行中 {counts.doing || 0}，已实现 {counts.done || 0}
        </span>
        <div className="spacer" />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(WISH_STATUS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
        </select>
        <input type="text" placeholder="搜标题、说明或名字" style={{ maxWidth: 220 }} value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      <div className="table-wrap">
        <table style={{ marginTop: 10 }}>
          <thead><tr><th>#</th><th>心愿</th><th>许愿人</th><th>岗位</th><th>状态</th><th>接的人</th><th>♥</th><th>许愿时间</th><th></th></tr></thead>
          <tbody>
            {!rows.length && <tr><td colSpan={9} className="meta">{data?.total ? "没有符合条件的心愿。" : "还没有人许愿。"}</td></tr>}
            {rows.map((w) => [
              <tr key={w.id}>
                <td className="time">#{w.id}</td>
                <td><b>{w.title}</b>{w.detail && <div className="meta clip" style={{ marginTop: 2 }}>{w.detail}</div>}</td>
                <td>{w.author}</td><td>{w.role || "其他"}</td>
                <td><span className={"wpill w-" + w.status}>{WISH_STATUS[w.status] || w.status}</span></td>
                <td>{w.taker || "—"}</td>
                <td title={(w.votes || []).join("、")}>{(w.votes || []).length}</td>
                <td className="time">{shortTime(w.created_at)}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="text" onClick={() => setEditing(w.id)}>编辑</button>{"　"}
                  <button className="text bad" onClick={() => {
                    if (confirm(`删除 #${w.id}「${w.title}」？删了不能恢复。`)) act({ action: "delete", id: w.id });
                  }}>删除</button>
                </td>
              </tr>,
              editing === w.id && (
                <WishEditor key={"edit" + w.id} wish={w} roles={roles} onCancel={() => setEditing(0)}
                  onSave={async (fields) => { await act({ action: "update", id: w.id, ...fields }); }} />
              ),
            ])}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function SitesTab() {
  return (
    <>
      <Sites />
      <WishManager />
    </>
  );
}
