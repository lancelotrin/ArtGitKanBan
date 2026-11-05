import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ToastProvider } from "../../components/Toast";
import { when } from "../../lib/format";
import { useWishAction, useWishes, type Wish, type WishStatus } from "./api";
import NewWish from "./NewWish";
import WishCard from "./WishCard";
import WishDetail from "./WishDetail";
import "./wish.css";

type Filter = "all" | WishStatus;
const FILTERS: [Filter, string][] = [["all", "全部"], ["open", "待接"], ["doing", "进行中"], ["done", "已实现"]];
const STATUS_ORDER: Record<WishStatus, number> = { open: 0, doing: 1, done: 2 };

export default function WishBoard() {
  useEffect(() => {
    document.title = "心愿墙 · 大豆";
  }, []);
  return (
    <div className="wish-page">
      <ToastProvider className="toast">
        <Board />
      </ToastProvider>
    </div>
  );
}

function Board() {
  const { data, isPending, error } = useWishes();
  const action = useWishAction();
  // 筛选条件放在网址里，复制链接给别人看到的是同一个视图。
  const [params, setParams] = useSearchParams();
  const sort = params.get("sort") === "new" ? "new" : "hot";
  const filter = (params.get("status") || "all") as Filter;
  const role = params.get("role") || "";
  const mine = params.get("mine") === "1";
  const q = params.get("q") || "";
  const setParam = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: true });

  const [viewing, setViewing] = useState(0);
  const [creating, setCreating] = useState(false);
  const wishes = data?.wishes ?? [];
  const me = data?.me ?? "";
  const allow = data?.settings.allow_create !== false;

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: wishes.length, open: 0, doing: 0, done: 0 };
    wishes.forEach((w) => (c[w.status] += 1));
    return c;
  }, [wishes]);

  const granted = useMemo(
    () => wishes.filter((w) => w.status === "done").sort((a, b) => (b.done_at || "").localeCompare(a.done_at || "")),
    [wishes],
  );

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return wishes
      .filter((w) => filter === "all" || w.status === filter)
      .filter((w) => !role || (w.role || "其他") === role)
      .filter((w) => !mine || (!!me && (w.author === me || w.taker === me || w.votes.includes(me))))
      .filter((w) => !needle || [w.title, w.detail, w.author, w.taker, w.role].join(" ").toLowerCase().includes(needle))
      .sort((a, b) => {
        if (sort === "hot") {
          const d = STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || b.votes.length - a.votes.length;
          if (d) return d;
        }
        return b.id - a.id;
      });
  }, [wishes, filter, role, mine, me, q, sort]);

  const current = wishes.find((w) => w.id === viewing);
  const run = (id: number) => (name: Parameters<typeof action.mutate>[0]["action"]) => action.mutate({ action: name, id });

  return (
    <>
      <Hero me={me} counts={counts} allow={allow} onNew={() => setCreating(true)} />
      <div className="wrap">
        <Granted wishes={granted} onOpen={setViewing} />
        <div className="bar">
          <div className="seg">
            <button className={sort === "hot" ? "on" : ""} onClick={() => setParam("sort", "")}>最热</button>
            <button className={sort === "new" ? "on" : ""} onClick={() => setParam("sort", "new")}>最新</button>
          </div>
          <div className="chips">
            {FILTERS.map(([key, label]) => (
              <button key={key} className={"chip" + (filter === key ? " on" : "")}
                onClick={() => setParam("status", key === "all" ? "" : key)}>
                {label}<b>{counts[key]}</b>
              </button>
            ))}
          </div>
          <select className="rsel" value={role} onChange={(e) => setParam("role", e.target.value)}>
            <option value="">全部岗位</option>
            {(data?.roles ?? []).map((r) => <option key={r}>{r}</option>)}
          </select>
          <button className={"ghost" + (mine ? " on" : "")} onClick={() => setParam("mine", mine ? "" : "1")}>我的心愿</button>
          <div className="spacer" />
          <div className="search">
            <input type="search" placeholder="搜心愿或名字" value={q} onChange={(e) => setParam("q", e.target.value)} />
          </div>
        </div>
        <div className="grid">
          {isPending ? (
            <div className="empty">加载中…</div>
          ) : error ? (
            <div className="empty"><div className="big">⚠️</div>{error.message}</div>
          ) : list.length ? (
            list.map((w, i) => (
              <WishCard key={w.id} wish={w} me={me} index={i} onOpen={() => setViewing(w.id)} onAction={run(w.id)} />
            ))
          ) : (
            <div className="empty">
              <div className="big">{wishes.length ? "🔍" : "🌠"}</div>
              {wishes.length ? "没有符合条件的心愿" : "墙上还空着，来许第一个心愿吧"}
            </div>
          )}
        </div>
      </div>
      {current && (
        <WishDetail wish={current} me={me} busy={action.isPending} onClose={() => setViewing(0)} onAction={run(current.id)} />
      )}
      {creating && <NewWish me={me} roles={data?.roles ?? []} onClose={() => setCreating(false)} />}
    </>
  );
}

function Hero({ me, counts, allow, onNew }: { me: string; counts: Record<Filter, number>; allow: boolean; onNew: () => void }) {
  return (
    <section className="hero">
      <span className="star" style={{ left: "6%", top: "22%", fontSize: 14 }}>✦</span>
      <span className="star" style={{ left: "42%", top: "14%", fontSize: 10, animationDelay: "1.1s" }}>✦</span>
      <span className="star" style={{ left: "58%", top: "62%", fontSize: 12, animationDelay: ".5s" }}>✦</span>
      <span className="star" style={{ left: "88%", top: "18%", fontSize: 16, animationDelay: "2s" }}>✦</span>
      {me && <div className="me">我是 <b>{me}</b> · <Link to="/">大豆后台</Link></div>}
      <div className="wrap">
        <div>
          <div className="brand"><i>豆</i>大豆</div>
          <h1>心愿墙</h1>
          <p>想要什么功能、工具、分享？许个愿挂到墙上。想要的人点 ♥，有能力的人就接下来实现它。</p>
          <div className="hstats">
            <button className="cta big" disabled={!allow} onClick={onNew}
              title={allow ? "" : "管理员暂停了发布，可以先给已有的心愿点 ♥"}>
              {allow ? "✨ 发布心愿" : "⏸ 暂停发布中"}
            </button>
            <div className="hstat"><b>{counts.all}</b><span>个心愿</span></div>
            <div className="hstat"><b>{counts.doing}</b><span>正在实现</span></div>
            <div className="hstat"><b>{counts.done}</b><span>已经实现</span></div>
          </div>
        </div>
        <JarArt />
      </div>
    </section>
  );
}

function Granted({ wishes, onOpen }: { wishes: Wish[]; onOpen: (id: number) => void }) {
  const [open, setOpen] = useState(true);
  const [wide, setWide] = useState(false);
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const measure = () => row.current && setWide(row.current.scrollWidth > row.current.clientWidth + 4);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [wishes, open]);
  const scroll = (direction: number) => row.current?.scrollBy({ left: direction * 500 });
  return (
    <section className="granted">
      <div className="ghead">
        🎉 已实现的心愿 <span style={{ opacity: 0.85, fontWeight: 400 }}>{wishes.length ? `· ${wishes.length} 个` : ""}</span>
        <button onClick={() => setOpen(!open)}>{open ? "收起" : "展开"}</button>
      </div>
      {open && (
        <div className="gbox">
          {wide && <button className="garrow l" aria-label="往左" onClick={() => scroll(-1)}>‹</button>}
          <div className="grow" ref={row}>
            {wishes.length ? (
              wishes.map((w) => (
                <div className="gcard" key={w.id} onClick={() => onOpen(w.id)}>
                  <div className="t">{w.title}</div>
                  <div className="m">✓ {w.taker || w.author} 帮忙实现 · {when(w.done_at)}</div>
                </div>
              ))
            ) : (
              <div className="gempty" style={{ flex: 1 }}>还没有实现的心愿。第一个被实现的会出现在这里 🌟</div>
            )}
          </div>
          {wide && <button className="garrow r" aria-label="往右" onClick={() => scroll(1)}>›</button>}
        </div>
      )}
    </section>
  );
}

function JarArt() {
  return (
    <svg className="art" viewBox="0 0 300 180" aria-hidden="true">
      <defs>
        <linearGradient id="jar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity=".55" />
          <stop offset="1" stopColor="#fff" stopOpacity=".18" />
        </linearGradient>
        <radialGradient id="glow">
          <stop offset="0" stopColor="#fff6c2" />
          <stop offset="1" stopColor="#ffd36b" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="150" cy="168" rx="92" ry="9" fill="#3a1f7a" opacity=".18" />
      <circle cx="150" cy="104" r="70" fill="url(#glow)" opacity=".7" />
      <rect x="120" y="34" width="60" height="14" rx="5" fill="#fff" opacity=".75" />
      <path d="M112 52 h76 q14 0 14 16 v74 q0 22 -22 22 h-60 q-22 0 -22 -22 v-74 q0 -16 14 -16z" fill="url(#jar)"
        stroke="#fff" strokeOpacity=".8" strokeWidth="2" />
      <g fill="#fff3a8">
        <path d="M140 96 l4 9 10 1 -8 6 3 10 -9 -6 -9 6 3 -10 -8 -6 10 -1z" />
        <path d="M168 120 l3 6 7 1 -5 4 2 7 -7 -4 -6 4 2 -7 -5 -4 7 -1z" opacity=".9" />
        <path d="M128 132 l2 5 5 0 -4 3 2 5 -5 -3 -4 3 1 -5 -4 -3 5 0z" opacity=".8" />
      </g>
      <g fill="#fff" opacity=".9">
        <circle cx="232" cy="46" r="3" />
        <circle cx="62" cy="60" r="2" />
        <circle cx="250" cy="120" r="2" />
      </g>
      <path d="M236 70 q20 -10 30 6" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" opacity=".7" />
      <text x="216" y="104" fontSize="26" fill="#fff">✨</text>
      <text x="58" y="120" fontSize="22" fill="#fff">💡</text>
    </svg>
  );
}
