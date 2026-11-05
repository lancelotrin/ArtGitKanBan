import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../../api/client";
import { ToastProvider, useToast } from "../../components/Toast";
import { usePageTitle } from "../../lib/usePageTitle";
import { useMe } from "../auth/AuthPages";
import { sendMessage, useChatInfo, useConversation, useConversations, type Message } from "./api";
import "./chat.css";

export default function ChatPage() {
  usePageTitle("AI 聊天 · 大豆");
  return (
    <div className="chat-page">
      <ToastProvider>
        <Chat />
      </ToastProvider>
    </div>
  );
}

/** 正在进行的一问一答（还没写进服务器返回的对话里）。 */
// base：发送时已有几条消息。流式过程中服务器那边已经存了这条问题，只显示 base 之前的，免得问题出现两次。
interface Pending { question: string; answer: string; model: string; error: string; done: boolean; base: number }

function Bubble({ role, content, model, status }: { role: string; content: string; model?: string; status?: string }) {
  return (
    <div className={"msg " + role + (status === "error" ? " failed" : "")}>
      <div className="who">{role === "user" ? "我" : "豆"}</div>
      <div className="body">
        {role === "user" ? (
          <div className="text">{content}</div>
        ) : (
          <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content || "…"}</ReactMarkdown></div>
        )}
        {role === "assistant" && (model || status === "stopped") && (
          <div className="meta">{model}{status === "stopped" ? "（中途停止）" : ""}</div>
        )}
      </div>
    </div>
  );
}

function Chat() {
  const { id } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const toast = useToast();
  const me = useMe().data;
  const info = useChatInfo().data;
  const list = useConversations().data?.conversations ?? [];
  const detail = useConversation(id);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [sideOpen, setSideOpen] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = !!pending && !pending.done;

  const messages: Message[] = detail.data?.messages ?? [];
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.length, pending?.answer, pending?.question]);
  useEffect(() => {
    if (detail.error) navigate("/chat", { replace: true });
  }, [detail.error, navigate]);

  const refresh = async (conversationId?: string) => {
    await client.invalidateQueries({ queryKey: ["chat", "list"] });
    if (conversationId) await client.invalidateQueries({ queryKey: ["chat", "detail", conversationId] });
  };

  const send = async () => {
    const content = draft.trim();
    if (!content || busy) return;
    setDraft("");
    const controller = new AbortController();
    abort.current = controller;
    let conversationId = id;
    setPending({ question: content, answer: "", model: "", error: "", done: false, base: id ? messages.length : 0 });
    try {
      await sendMessage(content, id, (event) => {
        if ("conversation" in event) {
          conversationId = event.conversation.id;
          if (!id) navigate("/chat/" + conversationId, { replace: true });
        } else if ("model" in event) {
          setPending((p) => p && { ...p, model: event.model });
        } else if ("delta" in event) {
          setPending((p) => p && { ...p, answer: p.answer + event.delta });
        } else if ("error" in event) {
          setPending((p) => p && { ...p, error: event.error });
        }
      }, controller.signal);
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        toast((error as Error).message, true);
        setDraft(content); // 没发出去，把话放回输入框
      }
    } finally {
      abort.current = null;
      await refresh(conversationId);
      setPending(null);
    }
  };

  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法选词时的回车不算发送
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  };

  const remove = async (conversationId: string, title: string) => {
    if (!confirm(`删除对话「${title}」？删了不能恢复。`)) return;
    try {
      await api("/chat/conversations/" + conversationId, { method: "DELETE" });
      if (conversationId === id) navigate("/chat", { replace: true });
      await refresh();
    } catch (error) {
      toast((error as Error).message, true);
    }
  };

  const rename = async (conversationId: string, title: string) => {
    const next = prompt("对话标题", title)?.trim();
    if (!next || next === title) return;
    try {
      await api("/chat/conversations/" + conversationId, { method: "PATCH", body: JSON.stringify({ title: next }) });
      await refresh(conversationId);
    } catch (error) {
      toast((error as Error).message, true);
    }
  };

  const startNew = () => {
    if (busy) return;
    navigate("/chat");
    setSideOpen(false);
  };

  const empty = !id && !pending;
  return (
    <div className={"layout" + (sideOpen ? " side-open" : "")}>
      <aside className="side">
        <div className="brand"><Link to="/" className="logo">豆</Link><b>AI 聊天</b></div>
        <button className="new" onClick={startNew} disabled={busy}>＋ 新对话</button>
        <div className="convs">
          {list.map((c) => (
            <div key={c.id} className={"conv" + (c.id === id ? " on" : "")}>
              <button className="title" disabled={busy} onClick={() => { navigate("/chat/" + c.id); setSideOpen(false); }}
                onDoubleClick={() => rename(c.id, c.title)} title="双击改名">{c.title}</button>
              <button className="del" disabled={busy} onClick={() => remove(c.id, c.title)} aria-label="删除">✕</button>
            </div>
          ))}
          {!list.length && <div className="hint">还没有对话</div>}
        </div>
        <div className="foot">对话只有你自己看得到。{me ? `当前：${me.name}${me.department ? `（${me.department}）` : ""}` : ""}</div>
      </aside>
      <main className="main">
        <header>
          <button className="menu" onClick={() => setSideOpen(!sideOpen)} aria-label="对话列表">☰</button>
          <h1>{detail.data?.title || "新对话"}</h1>
          <span className="model">{info ? (info.enabled ? info.model : "没有配置模型") : ""}</span>
        </header>
        <div className="thread">
          {empty ? (
            <div className="welcome">
              <div className="big">✦</div>
              <h2>问点什么吧</h2>
              <p>直接调用大豆配好的大模型{info?.enabled ? `（${info.model}）` : ""}。回答仅供参考，要紧的事自己再核对。</p>
              {info && !info.enabled && <p className="warn">管理员还没配置 LLM_API_KEY，暂时用不了。</p>}
            </div>
          ) : (
            <>
              {detail.isPending && id && !pending && <div className="hint center">正在读取…</div>}
              {(pending ? messages.slice(0, pending.base) : messages).map((m) => <Bubble key={m.id} role={m.role} content={m.content} model={m.model} status={m.status} />)}
              {pending && (
                <>
                  <Bubble role="user" content={pending.question} />
                  {(pending.answer || !pending.error) && (
                    <Bubble role="assistant" content={pending.answer} model={pending.model} />
                  )}
                  {pending.error && <Bubble role="assistant" content={pending.error} status="error" />}
                </>
              )}
            </>
          )}
          <div ref={bottom} />
        </div>
        <div className="composer">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} maxLength={8000}
            placeholder={info && !info.enabled ? "暂时用不了" : "输入问题，Enter 发送，Shift+Enter 换行"}
            disabled={!!info && !info.enabled} rows={3} />
          {busy ? (
            <button className="stop" onClick={() => abort.current?.abort()}>■ 停止</button>
          ) : (
            <button className="send" onClick={send} disabled={!draft.trim() || (!!info && !info.enabled)}>发送</button>
          )}
        </div>
      </main>
    </div>
  );
}
