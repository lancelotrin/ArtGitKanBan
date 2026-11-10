import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { ActionButton, adminPost, Clip, Lines, OnOff, stamp, useAdmin, useSynced } from "./common";
import type { BombAdmin, MetaCommit, MetaItem, MetaRef, MetaResult, MetaScan, PullState, Status, WatchState } from "./types";

const BOMB_BOARD = "/bomb/";

function ProjectConfig({ status }: { status?: Status }) {
  const [checkout, setCheckout] = useState("");
  const [checkoutMsg, setCheckoutMsg] = useState("");
  useEffect(() => {
    if (status?.file_branch) setCheckout((current) => current || status.file_branch);
  }, [status?.file_branch]);
  return (
    <section>
      <h2>工程</h2>
      <p className="meta">
        文件内容（面数、引用、Shader）读部署这台电脑上的检出。目录和分支在项目根目录的 <code>.env</code> 里改，改完点右上角「停止」再「启动」。
      </p>
      <label>Client 目录（<code>PROJECT_ROOT</code>，下面要有 Assets）</label>
      <input type="text" value={status?.project_root ?? ""} readOnly />
      <label>允许查询的 Git 分支（<code>GIT_BRANCHES</code>，逗号分隔）。提交记录只 fetch 这些分支，main 总会带上，盯目录也只盯这里。</label>
      <input type="text" value={(status?.git_branches ?? []).join(", ")} readOnly />
      <div className="sub">
        <label>切工作区到这个分支（远端要有）。有本地修改会先清掉。和上面「查询分支」不是一回事。</label>
        <div className="row">
          <input type="text" placeholder="例如 CBT2_bugfix_shader" style={{ maxWidth: 320 }} value={checkout} onChange={(e) => setCheckout(e.target.value)} />
          <ActionButton className="primary" label="切换分支" done="分支已切换" run={async () => {
            try {
              const data = await adminPost<{ message?: string }>("checkout", { branch: checkout });
              setCheckoutMsg(data.message || "已切换。");
            } catch (error) {
              setCheckoutMsg((error as Error).message);
              throw error;
            }
          }} />
        </div>
        <p className="meta">{checkoutMsg}</p>
      </div>
    </section>
  );
}

function Schedule({ state, queuedText, extra }: { state?: PullState | WatchState; queuedText: string; extra: string }) {
  if (!state) return <div>正在读取…</div>;
  return (
    <div>
      <OnOff on={state.enabled} yes={"hours" in state ? "定时开着" : "开着"} no={"hours" in state ? "定时关着" : "关着"} />
      {state.enabled ? extra : ""}
      {state.run_requested ? queuedText : ""}
      {state.last_at && <div className="meta">上次 {state.last_at}</div>}
      <Lines text={state.last_result} />
    </div>
  );
}

function PullSection({ pull }: { pull?: PullState }) {
  const [hours, setHours] = useSynced(pull?.hours ? String(pull.hours) : undefined, "5");
  const [message, setMessage] = useState("");
  const send = (enabled: boolean, runNow: boolean) => async () => {
    const data = await adminPost<{ message?: string }>("pull", { enabled, hours: Number(hours || 5), run_now: runNow });
    setMessage(data.message || "已保存。");
  };
  return (
    <section id="sec-pull">
      <h2>拉代码</h2>
      <p className="meta">只快进当前分支。有已跟踪文件的本地修改会先清掉再拉。改完大约 20 秒内生效，不用重启。</p>
      <Schedule state={pull} queuedText="，已排队，马上拉" extra={pull ? `，每 ${pull.hours} 小时，下次 ${pull.next_at || "还没排"}` : ""} />
      <label>间隔（小时，1 到 168）</label>
      <input className="hours" type="number" min={1} max={168} step={1} value={hours} onChange={(e) => setHours(e.target.value)} />
      <div className="row" style={{ marginTop: 10 }}>
        <ActionButton className="primary" label="开启定时" done="定时已更新" run={send(true, false)} />
        <ActionButton label="关闭定时" done="定时已更新" run={send(false, false)} />
        <ActionButton label="现在拉一次" done="已开始拉" run={send(!!pull?.enabled, true)} />
      </div>
      <p className="meta">{message}</p>
    </section>
  );
}

function WatchSection({ watch }: { watch?: WatchState }) {
  const [days, setDays] = useSynced(watch ? String(watch.days || 1) : undefined, "1");
  const [minutes, setMinutes] = useSynced(watch ? String(watch.minutes || 30) : undefined, "30");
  const [rules, setRules] = useSynced(watch ? watch.rules_text || "Assets/Bundle/Shader .shader .hlsl" : undefined, "");
  const [message, setMessage] = useState("");
  const send = (enabled: boolean, runNow: boolean) => async () => {
    const data = await adminPost<{ message?: string }>("watch", {
      enabled, minutes: Number(minutes || 30), days: Number(days || 1), rules, run_now: runNow,
    });
    setMessage(data.message || "已保存。");
  };
  return (
    <section id="sec-watch">
      <h2>盯目录</h2>
      <p className="meta">只看 main 和 CBT2_bugfix，不拉工作区。有匹配文件的新提交就私聊朱辉。一行一个目录，后面写后缀。</p>
      <Schedule state={watch} queuedText="，已排队，马上查"
        extra={watch ? `，看最近 ${watch.days || 1} 天，每 ${watch.minutes} 分钟查一次，下次 ${watch.next_at || "还没排"}` : ""} />
      <div className="row">
        <div><label>查最近几天（1 到 7）</label><input className="hours" type="number" min={1} max={7} step={1} value={days} onChange={(e) => setDays(e.target.value)} /></div>
        <div><label>多久查一次（分钟，5 到 1440）</label><input className="hours" type="number" min={5} max={1440} step={1} value={minutes} onChange={(e) => setMinutes(e.target.value)} /></div>
      </div>
      <label>目录和后缀</label>
      <textarea placeholder="Assets/Bundle/Shader .shader .hlsl" value={rules} onChange={(e) => setRules(e.target.value)} />
      <div className="row" style={{ marginTop: 10 }}>
        <ActionButton className="primary" label="开启盯目录" done="盯目录已更新" run={send(true, false)} />
        <ActionButton label="关闭" done="盯目录已更新" run={send(false, false)} />
        <ActionButton label="现在查一次" done="已开始查" run={send(!!watch?.enabled, true)} />
      </div>
      <p className="meta">{message}</p>
    </section>
  );
}

function BombSection() {
  const { data } = useAdmin<BombAdmin>("bomb");
  const [days, setDays] = useSynced(data ? String(data.days || 2) : undefined, "2");
  const [minutes, setMinutes] = useSynced(data ? String(data.minutes || 30) : undefined, "30");
  const [groups, setGroups, resetGroups] = useSynced(data?.groups?.join("\n"), "");
  const [ignore, setIgnore, resetIgnore] = useSynced(data?.ignore_paths?.join("\n"), "");
  const [message, setMessage] = useState<{ text: string; bad?: boolean }>({ text: "" });
  const act = (action: string) => async () => {
    const data = await adminPost<BombAdmin>("bomb", { action, minutes: Number(minutes || 30), days: Number(days || 2) });
    setMessage({ text: data.message || "" });
  };
  const saveList = (action: "groups" | "ignore_paths", reset: () => void) => async () => {
    try {
      const body = action === "groups" ? { action, groups } : { action, ignore_paths: ignore };
      const result = await adminPost<BombAdmin>("bomb", body);
      setMessage({ text: result.message || "" });
      reset();
    } catch (error) {
      setMessage({ text: (error as Error).message, bad: true });
      throw error;
    }
  };
  return (
    <section id="sec-bomb">
      <h2>炸线提醒</h2>
      <p className="meta">扫上面「查询分支」最近几天的合并：谁合并丢了谁的修改、现在补回没有，以及强推。新的会私聊朱辉并发到提醒群，报过的不重复报。结果是「疑似」，请当事人确认。</p>
      {!data ? <div>正在读取…</div> : (
        <div>
          <OnOff on={data.enabled} />
          {data.enabled ? `，看最近 ${data.days} 天，每 ${data.minutes} 分钟查一次，下次 ${stamp(data.next_at) || "马上"}` : ""}
          {data.run_requested ? "，已排队，马上查" : ""}
          <div className="meta">
            分支：{(data.branches || []).join("、")}；发给朱辉私聊 + {(data.groups || []).length} 个群；忽略 {(data.ignore_paths || []).length} 条路径；已报过 {data.told} 条
          </div>
          {data.last_at && <div className="meta">上次 {stamp(data.last_at)}：{data.last_result}</div>}
        </div>
      )}
      <div className="row">
        <div><label>查最近几天（1 到 7）</label><input className="hours" type="number" min={1} max={7} step={1} value={days} onChange={(e) => setDays(e.target.value)} /></div>
        <div><label>多久查一次（分钟，5 到 1440）</label><input className="hours" type="number" min={5} max={1440} step={1} value={minutes} onChange={(e) => setMinutes(e.target.value)} /></div>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <ActionButton className="primary" label="开启 / 保存" done="炸线提醒已更新" run={act("on")} />
        <ActionButton label="关闭" done="炸线提醒已更新" run={act("off")} />
        <ActionButton label="现在查一次" done="已排队" run={act("now")} />
      </div>
      <p className="meta">{message.bad ? <span className="bad">{message.text}</span> : message.text}</p>
      <div className="sub">
        <label>提醒发到这些群（群 ID，一行一个，oc_ 开头；留空只私聊朱辉）。大豆要在群里才发得出去。</label>
        <textarea placeholder="oc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" style={{ minHeight: 64 }} value={groups} onChange={(e) => setGroups(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}><ActionButton label="保存群" done="已保存" run={saveList("groups", resetGroups)} /></div>
      </div>
      <div className="sub">
        <label>提醒忽略这些路径（一行一个目录前缀，可以只写路径中间的一段，如 StreamingAssets/Battle/；匹配到的文件不发提醒，整条只剩忽略路径时整条不发。炸线看板仍显示完整结果。）</label>
        <textarea placeholder={"BattleLibrary/output/\nAssets/DataTable/"} style={{ minHeight: 64 }} value={ignore} onChange={(e) => setIgnore(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}><ActionButton label="保存忽略路径" done="已保存" run={saveList("ignore_paths", resetIgnore)} /></div>
      </div>
      <div className="sub">
        <label>最近报出的（完整历史看 <Link to={BOMB_BOARD}>炸线看板</Link>）</label>
        <div>
          {(data?.recent || []).length ? data!.recent.map((item, i) => (
            <div key={i}>
              <div className="meta">{stamp(item.at)}{!item.sent && <> <span className="bad">没发出去</span></>}</div>
              <Clip text={item.text} />
            </div>
          )) : <div className="meta">还没有报过。</div>}
        </div>
      </div>
    </section>
  );
}

function RefList({ refs }: { refs: MetaRef[] }) {
  return (
    <ul className="meta">
      {refs.map((ref) => {
        const since = ref.since as MetaCommit | undefined;
        return (
          <li key={ref.path}>
            <code>{ref.path}</code> 引用 {ref.count} 处
            {since?.hash ? `，${since.short} ${since.author} ${since.date} 开始引用` : ""}
            {!ref.replace && `（${ref.reason}）`}
            {ref.replace && ref.dirty && <span className="bad">（本地也有别的修改，会一起提交）</span>}
          </li>
        );
      })}
    </ul>
  );
}

function GuidItem({ item }: { item: MetaItem }) {
  const commit = item.commit as MetaCommit | undefined;
  const refs = item.refs || [];
  const change = refs.filter((ref) => ref.replace);
  const keep = refs.filter((ref) => !ref.replace);
  return (
    <div className="sub">
      <div><code>{item.path}</code></div>
      <div className="meta">提交里是 <code>{item.old_guid}</code>，本地是 <code>{item.new_guid}</code></div>
      {!commit?.hash ? <div className="bad">{item.note || "找不到重复 guid 的提交，不处理。"}</div> : (
        <>
          <div className="meta">旧 guid 和这些 meta 重复：{(commit.dup_paths || []).map((dup) => <code key={dup}>{dup} </code>)}</div>
          <div className="meta">重复提交：{commit.short} {commit.author} {commit.date} {commit.subject}</div>
          {item.grep_done === false && <div className="bad">没查完哪些文件引用旧 guid，这个先不处理。</div>}
          <div className="meta">换成本地 guid（{change.length} 个文件）{change.length ? "：" : ""}</div>
          {change.length > 0 && <RefList refs={change} />}
          {keep.length > 0 && (
            <>
              <div className="meta">不动（{keep.length} 个文件）：</div>
              <RefList refs={keep} />
            </>
          )}
        </>
      )}
    </div>
  );
}

function MetaSection() {
  const [scan, setScan] = useState<MetaScan>();
  const [message, setMessage] = useState("");
  const imports = scan?.items.filter((item) => item.kind === "import") || [];
  const guids = scan?.items.filter((item) => item.kind === "guid") || [];
  const fixable = guids.filter((item) => (item.commit as MetaCommit | undefined)?.hash && item.grep_done);
  const unknown = scan?.items.filter((item) => item.kind === "unknown") || [];
  const files = fixable.reduce((sum, item) => sum + (item.refs || []).filter((ref) => ref.replace).length, 0);
  return (
    <section id="sec-meta">
      <h2>meta 改动</h2>
      <p className="meta">
        查工作区里本地改过的 <code>.meta</code>。guid 没变（只改了导入设置）的直接提交；guid 变了的，按旧 guid 找最近一次提交了「别的 meta 也用这个 guid」的提交（重复提交，和路径无关）。
        引用旧 guid 的文件，在重复提交及之后开始引用的换成本地新 guid；重复提交之前就引用的是之前那个资源的，不动。全部一次提交，只 commit 不 push。「拉代码」会清掉本地修改，先处理完再拉。
      </p>
      <div className="row">
        <ActionButton label="检查" done="已检查" run={async () => {
          setScan(await api<MetaScan>("/admin/meta"));
          setMessage("");
        }} />
        {scan && (imports.length > 0 || fixable.length > 0) && (
          <ActionButton className="primary" done="已提交"
            label={`全部处理并提交（导入设置 ${imports.length} 个，guid ${fixable.length} 个，换 ${files} 个文件）`}
            run={async () => {
              const result = await adminPost<MetaResult>("meta", { action: "apply_all" });
              setMessage(result.message || "");
              if (result.scan) setScan(result.scan);
            }} />
        )}
      </div>
      <p className="meta">{message}</p>
      {scan?.seconds !== undefined && (
        <div className="meta">
          检查用时 {scan.seconds} 秒{scan.timing && Object.keys(scan.timing).length
            ? "（" + Object.entries(scan.timing).map(([name, sec]) => `${name} ${sec} 秒`).join("，") + "）" : ""}
        </div>
      )}
      {scan && !scan.items.length && <div className="meta">没有本地改过的 meta。</div>}
      {imports.length > 0 && (
        <div className="sub">
          <label>只改导入设置（{imports.length} 个）</label>
          <Clip text={imports.map((item) => item.path).join("\n")} />
        </div>
      )}
      {guids.length > 0 && <label style={{ marginTop: 16 }}>guid 变了（{guids.length} 个）</label>}
      {guids.map((item) => <GuidItem key={item.path} item={item} />)}
      {unknown.length > 0 && (
        <div className="sub">
          <label>读不到 guid 行，没处理</label>
          <Clip text={unknown.map((item) => item.path).join("\n")} />
        </div>
      )}
    </section>
  );
}

export default function CodeTab({ status }: { status?: Status }) {
  return (
    <>
      <ProjectConfig status={status} />
      <div className="grid2">
        <PullSection pull={status?.pull} />
        <WatchSection watch={status?.watch} />
      </div>
      <MetaSection />
      <BombSection />
    </>
  );
}
