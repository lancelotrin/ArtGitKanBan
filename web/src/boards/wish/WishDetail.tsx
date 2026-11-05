import { Modal } from "../../components/Modal";
import { when } from "../../lib/format";
import { STATUS_TEXT, type Wish, type WishAction } from "./api";
import { Avatar, RoleTag, TAKE_CONFIRM } from "./bits";

const CONFIRMS: Partial<Record<WishAction, string>> = {
  withdraw: "撤回这个心愿？",
  drop: "放弃后心愿回到待接，确定？",
  take: TAKE_CONFIRM,
};

interface Props {
  wish: Wish;
  me: string;
  busy: boolean;
  onClose: () => void;
  onAction: (action: WishAction) => void;
}

export default function WishDetail({ wish, me, busy, onClose, onAction }: Props) {
  const voted = !!me && wish.votes.includes(me);
  const own = !!me && wish.author === me;
  const run = (action: WishAction) => {
    const question = CONFIRMS[action];
    if (question && !confirm(question)) return;
    onAction(action);
  };
  const button = (action: WishAction, label: string, className: string, style?: object) => (
    <button key={action} className={className} style={style} disabled={busy} onClick={() => run(action)}>
      {label}
    </button>
  );
  const buttons = [];
  if (!own && wish.status !== "done")
    buttons.push(button("vote", voted ? "♥ 取消想要" : "♡ 我也想要", "btn2" + (voted ? " on" : ""), { color: "var(--heart)" }));
  if (wish.status === "open" && own) buttons.push(button("withdraw", "撤回心愿", "btn2 warn"));
  if (wish.status === "doing" && me === wish.taker) buttons.push(button("drop", "放弃", "btn2 warn"));
  if (wish.status === "doing" && me && (me === wish.taker || own)) buttons.push(button("done", "标成已实现", "btnp"));
  if (wish.status === "open") buttons.push(button("take", "我来接", "btnp"));

  return (
    <Modal onClose={onClose}>
      <div className="mtop">
        <div>
          <span className={"tag t-" + wish.status}>{STATUS_TEXT[wish.status]}</span>
          <h2 style={{ marginTop: 6 }}>{wish.title}</h2>
          <div className="dmeta">
            <Avatar name={wish.author} />
            {wish.author}
            <RoleTag role={wish.role} /> · #{wish.id} · {when(wish.created_at)}
          </div>
        </div>
        <button className="x" onClick={onClose} aria-label="关闭">✕</button>
      </div>
      <div className="mbody">
        <div className={"ddesc" + (wish.detail ? "" : " none")}>{wish.detail || "没有补充说明。"}</div>
        <div className="sect">进度</div>
        <ul className="tl">
          <li className="on">{wish.author} 许下心愿<small>{when(wish.created_at)}</small></li>
          {wish.taker && <li className="on">{wish.taker} 接下<small>{when(wish.taken_at)}</small></li>}
          {wish.status === "done" ? (
            <li className="ok">心愿实现 🎉<small>{when(wish.done_at)}</small></li>
          ) : (
            <li>{wish.status === "doing" ? "等待实现…" : "等人来接…"}</li>
          )}
        </ul>
        <div className="sect">♥ {wish.votes.length} 人想要</div>
        <div className="fans">
          {wish.votes.length ? (
            wish.votes.map((name) => (
              <span className="fan" key={name}><Avatar name={name} />{name}</span>
            ))
          ) : (
            <span style={{ color: "var(--sub)", fontSize: 12 }}>还没有人点 ♥</span>
          )}
        </div>
        <div className="mfoot">{buttons}</div>
      </div>
    </Modal>
  );
}
