import { useEffect, useRef, useState } from "react";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { useCreateWish } from "./api";

const ROLE_ICONS: Record<string, string> = { 美术: "🎨", 程序: "💻", QA: "🧪", 策划: "📋", 其他: "✳️" };
const TITLE_MAX = 60;
const DETAIL_MAX = 800;

export default function NewWish({ me, roles, onClose }: { me: string; roles: string[]; onClose: () => void }) {
  const [role, setRole] = useState("其他");
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const create = useCreateWish();
  const toast = useToast();
  const titleRef = useRef<HTMLInputElement>(null);
  useEffect(() => titleRef.current?.focus(), []);

  const submit = () => {
    if (title.trim().length < 2) {
      toast("心愿至少写 2 个字", true);
      return;
    }
    create.mutate({ title, detail, role }, { onSuccess: onClose });
  };

  return (
    <Modal onClose={onClose}>
      <div className="mtop">
        <h2>✨ 发布一个心愿</h2>
        <button className="x" onClick={onClose} aria-label="关闭">✕</button>
      </div>
      <div className="mbody">
        <div className="field">
          <label>许愿人<small>用你的飞书名字</small></label>
          <input value={me} disabled />
        </div>
        <div className="field">
          <label>你的岗位</label>
          <div className="roles">
            {roles.map((r) => (
              <button type="button" key={r} className={r === role ? "on" : ""} onClick={() => setRole(r)}>
                {ROLE_ICONS[r] || "✳️"} {r}
              </button>
            ))}
          </div>
        </div>
        <div className="field">
          <label>想要什么？<small>一句话说清楚</small></label>
          <input ref={titleRef} maxLength={TITLE_MAX} value={title} onChange={(e) => setTitle(e.target.value)}
            placeholder="例如：复盘能一键导出成 Excel" />
          <span className="count">{title.length}/{TITLE_MAX}</span>
        </div>
        <div className="field">
          <label>补充说明<small>可选</small></label>
          <textarea maxLength={DETAIL_MAX} value={detail} onChange={(e) => setDetail(e.target.value)}
            placeholder="用在什么场景？现在哪里不方便？做成什么样算实现？" />
          <span className="count">{detail.length}/{DETAIL_MAX}</span>
        </div>
        <div className="tip">心愿会公开挂在墙上，所有人都能看到、点 ♥ 和接下。没人接之前你可以撤回。</div>
        <div className="mfoot">
          <button className="btn2" onClick={onClose}>取消</button>
          <button className="btnp hot" disabled={create.isPending} onClick={submit}>挂上心愿墙</button>
        </div>
      </div>
    </Modal>
  );
}
