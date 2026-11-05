import { useState } from "react";
import { ActionButton, adminPost, Clip, PagedTable, useAdmin } from "./common";
import type { Evaluation, Items, Miss, Note, Question } from "./types";

function Preview() {
  const [text, setText] = useState("");
  const [out, setOut] = useState("还没有试跑。");
  return (
    <section>
      <h2>试跑</h2>
      <p className="meta">输入同事会问的话。按真实规则答，不发到群里，也不写入纠正笔记。</p>
      <textarea placeholder="例如：现在几点" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row" style={{ marginTop: 10 }}>
        <ActionButton className="primary" label="试跑" done="试跑完成" run={async () => {
          setOut("正在试跑…");
          try {
            const data = await adminPost<{ route?: string; answer?: string; ok?: boolean }>("preview", { text });
            setOut("路由：" + (data.route || "没有") + "\n\n" + (data.answer || "没有回复"));
          } catch (error) {
            setOut((error as Error).message);
            throw error;
          }
        }} />
      </div>
      <pre>{out}</pre>
    </section>
  );
}

export default function ChatTab() {
  const questions = useAdmin<Items<Question>>("questions", 8000).data?.items ?? [];
  const reviews = useAdmin<Items<Evaluation>>("reviews", 8000).data?.items ?? [];
  const misses = useAdmin<Items<Miss>>("misses", 8000).data?.items ?? [];
  const notes = useAdmin<Items<Note>>("notes", 8000).data?.items ?? [];
  return (
    <>
      <Preview />
      <section id="sec-questions">
        <h2>用户问题</h2>
        <p className="meta">新的在前。文件里保留最近 2000 条，这里每页看 10 条。试跑不记。长回复默认收起。</p>
        <PagedTable items={questions} head={<tr><th>时间</th><th>来源</th><th>路由</th><th>问题</th></tr>}
          empty={<tr><td colSpan={4}>没有问题</td></tr>}
          row={(item, i) => (
            <tr key={i}>
              <td className="time">{item.time}</td><td>{item.where}</td><td>{item.route}</td>
              <td>{item.question}<Clip text={item.answer} /></td>
            </tr>
          )} />
      </section>
      <section id="sec-reviews">
        <h2>评价</h2>
        <p className="meta">大豆答完后由 Cursor 评价。对的只留在这里，错的还会写成下次要遵守的纠正。每页 10 条，新的在前。</p>
        <PagedTable items={reviews} head={<tr><th>时间</th><th>结论</th><th>路由</th><th>问题</th><th>评价</th></tr>}
          empty={<tr><td colSpan={5}>还没有评价</td></tr>}
          row={(item, i) => (
            <tr key={i}>
              <td className="time">{item.time}</td><td className={item.verdict === "对" ? "ok" : "bad"}>{item.verdict}</td>
              <td>{item.route}</td><td>{item.question}<Clip text={item.answer} /></td><td><Clip text={item.detail} /></td>
            </tr>
          )} />
      </section>
      <section id="sec-misses">
        <h2>答错记录</h2>
        <p className="meta">撤回、只说「不对」、同一路径又问一遍。标成已处理不会改大豆的规则。每页 10 条。</p>
        <PagedTable items={misses} head={<tr><th>时间</th><th>原因</th><th>路由</th><th>问题</th><th></th></tr>}
          empty={<tr><td colSpan={5}>没有记录</td></tr>}
          row={(item) => (
            <tr key={item.id} className={item.handled ? "handled" : ""}>
              <td className="time">{item.time}</td><td>{item.reason}</td><td>{item.route}</td>
              <td>{item.question}<Clip text={item.preview} /></td>
              <td>{item.handled ? "已处理" : <ActionButton label="标为已处理" done="已标为已处理" run={() => adminPost("misses/handled", { id: item.id })} />}</td>
            </tr>
          )} />
      </section>
      <section>
        <h2>纠正</h2>
        <p className="meta">答错后写下的事实，下次回答会遵守。</p>
        <table>
          <thead><tr><th>问题</th><th>事实</th><th></th></tr></thead>
          <tbody>
            {notes.length ? notes.map((item, index) => (
              <tr key={index + item.question}>
                <td>{item.question}</td><td>{item.fact}</td>
                <td><ActionButton label="删除" done="已删除" run={() => adminPost("notes/delete", { index })} /></td>
              </tr>
            )) : <tr><td colSpan={3}>没有纠正</td></tr>}
          </tbody>
        </table>
      </section>
    </>
  );
}
