import type { MouseEvent } from "react";
import { when } from "../../lib/format";
import { STATUS_TEXT, type Wish, type WishAction } from "./api";
import { Avatar, RoleTag, TAKE_CONFIRM } from "./bits";

interface Props {
  wish: Wish;
  me: string;
  index: number;
  onOpen: () => void;
  onAction: (action: WishAction) => void;
}

export default function WishCard({ wish, me, index, onOpen, onAction }: Props) {
  const voted = !!me && wish.votes.includes(me);
  const own = !!me && wish.author === me;
  const act = (action: WishAction) => (event: MouseEvent) => {
    event.stopPropagation();
    if (action === "take" && !confirm(TAKE_CONFIRM)) return;
    onAction(action);
  };
  return (
    <div className="card" style={{ animationDelay: Math.min(index * 25, 400) + "ms" }} onClick={onOpen}>
      {wish.status === "done" && <div className="stamp">已实现</div>}
      <div className="who">
        <Avatar name={wish.author} />
        <span className="nm">{wish.author}</span>
        <RoleTag role={wish.role} />
        <span className={"tag t-" + wish.status}>{STATUS_TEXT[wish.status]}</span>
        {own && <span className="tag t-mine">我的</span>}
      </div>
      <div className="title">{wish.title}</div>
      {wish.detail ? (
        <div className="desc">{wish.detail}</div>
      ) : (
        <div className="desc" style={{ opacity: 0.6 }}>{when(wish.created_at)}许下</div>
      )}
      <div className="foot">
        {wish.status === "open" && <button className="take" onClick={act("take")}>我来接</button>}
        {wish.status === "doing" && <span className="doer">🛠 {wish.taker} 在做</span>}
        {wish.status === "done" && <span className="doer d">✓ {wish.taker || wish.author} 实现</span>}
        <button className={"heart" + (voted ? " on" : "")} disabled={own} title={own ? "自己的心愿" : undefined}
          onClick={act("vote")}>
          <span className="h">{voted ? "♥" : "♡"}</span>
          {wish.votes.length} 人想要
        </button>
      </div>
    </div>
  );
}
