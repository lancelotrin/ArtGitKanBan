import { useState } from "react";
import { Link } from "react-router";
import { ActionButton, adminPost, useAdmin, useSynced } from "./common";
import type { ReviewAdmin } from "./types";

function yesterday(): string {
  const day = new Date(Date.now() - 86400000);
  return day.getFullYear() + "-" + String(day.getMonth() + 1).padStart(2, "0") + "-" + String(day.getDate()).padStart(2, "0");
}

export default function ReviewTab() {
  const { data } = useAdmin<ReviewAdmin>("review");
  const [name, setName] = useState("");
  const [author, setAuthor] = useState("");
  const [date, setDate] = useState(yesterday);
  const [message, setMessage] = useState<{ text: string; bad?: boolean }>({ text: "" });
  const [groups, setGroups, resetGroups] = useSynced(data?.groups?.join("\n"), "");
  const [groupsMsg, setGroupsMsg] = useState<{ text: string; bad?: boolean }>({ text: "" });

  // 失败时把消息写到对应的提示行，再抛出去让按钮弹错误。
  const withMessage = (set: typeof setMessage, work: () => Promise<ReviewAdmin>) => async () => {
    try {
      const result = await work();
      set({ text: result.message || "" });
      return result;
    } catch (error) {
      set({ text: (error as Error).message, bad: true });
      throw error;
    }
  };
  const add = withMessage(setMessage, async () => {
    if (!name.trim() || !author.trim()) throw new Error("姓名和 git 作者都要填。");
    setMessage({ text: "正在核对 git 作者、按姓名找飞书账号…" });
    const result = await adminPost<ReviewAdmin>("review", { action: "add", name: name.trim(), git_author: author.trim() });
    setName(""); setAuthor("");
    return result;
  });

  const state = data?.state;
  return (
    <section id="sec-review">
      <h2>自检复盘</h2>
      <p className="meta">
        每天 0 点复盘名单里每个人前一天在 main 和 CBT2_bugfix 上的提交，结果放在内网网页{" "}
        <Link to="/review/">{data?.web || "/review/"}</Link>
        （只读，打开就能看所有人）。姓名填飞书里的名字，大豆会在它所在的群里按姓名找到这个人并私聊摘要；找不到的只出网页，之后进了群会自动补上。git 作者名要和提交里的一致。
      </p>
      <div>
        {!data ? "正在读取…" : (
          <div className="meta">
            上次：{state?.last_at || "还没跑过"} {state?.last_result || ""}{state?.run_requested ? "；已排队补跑 " + state.run_requested : ""}
          </div>
        )}
      </div>
      <table style={{ marginTop: 10 }}>
        <thead><tr><th>姓名</th><th>git 作者</th><th>私聊</th><th></th></tr></thead>
        <tbody>
          {(data?.subs || []).length ? data!.subs.map((item) => (
            <tr key={item.git_author}>
              <td>{item.name}</td><td>{item.git_author}</td><td>{item.open_id ? "会私聊" : "只出网页"}</td>
              <td><ActionButton label="移除" done="已移除" run={withMessage(setMessage, () => adminPost<ReviewAdmin>("review", { action: "remove", git_author: item.git_author }))} /></td>
            </tr>
          )) : <tr><td colSpan={4} className="meta">名单是空的</td></tr>}
        </tbody>
      </table>
      <div className="sub">
        <label>登记新的人</label>
        <form className="row" onSubmit={(e) => e.preventDefault()}>
          <input type="text" placeholder="飞书姓名，如 朱辉" style={{ maxWidth: 140 }} value={name} onChange={(e) => setName(e.target.value)} />
          <input type="text" placeholder="git 作者，如 zhuhui" style={{ maxWidth: 180 }} value={author} onChange={(e) => setAuthor(e.target.value)} />
          <ActionButton className="primary" label="登记" done="已登记" run={add} />
        </form>
        <label>补跑某一天</label>
        <div className="row">
          <input type="date" style={{ maxWidth: 180 }} value={date} onChange={(e) => setDate(e.target.value)} />
          <ActionButton label="补跑这一天" done="已排队" run={withMessage(setMessage, () => adminPost<ReviewAdmin>("review", { action: "run", date }))} />
        </div>
        <p className="meta">{message.bad ? <span className="bad">{message.text}</span> : message.text}</p>
      </div>
      <div className="sub">
        <label>每天汇总发到这些群（群 ID，一行一个，oc_ 开头；留空不发群）。补跑也会发一次。大豆要在群里才发得出去。</label>
        <textarea placeholder="oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" style={{ minHeight: 64 }} value={groups} onChange={(e) => setGroups(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <ActionButton label="保存群" done="已保存" run={withMessage(setGroupsMsg, async () => {
            const result = await adminPost<ReviewAdmin>("review", { action: "groups", groups });
            resetGroups();
            return result;
          })} />
        </div>
        <p className="meta">{groupsMsg.bad ? <span className="bad">{groupsMsg.text}</span> : groupsMsg.text}</p>
      </div>
    </section>
  );
}
