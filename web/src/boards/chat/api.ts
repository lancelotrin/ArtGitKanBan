import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";

// 和 backend/routers/chat.py 对应。对话存在服务器上，接口只返回当前登录的人自己的。
export interface Conversation { id: string; title: string; created_at: number; updated_at: number }
export interface Message { id: number; role: "user" | "assistant"; content: string; model: string; status: "ok" | "stopped" | "error"; created_at: number }
export interface ConversationDetail extends Conversation { messages: Message[] }

export type StreamEvent =
  | { conversation: { id: string; title: string } }
  | { model: string }
  | { delta: string }
  | { done: true }
  | { error: string };

export const useChatInfo = () => useQuery({ queryKey: ["chat-info"], queryFn: () => api<{ enabled: boolean; model: string }>("/chat/info") });
export const useConversations = () =>
  useQuery({ queryKey: ["chat", "list"], queryFn: () => api<{ conversations: Conversation[] }>("/chat/conversations") });
export const useConversation = (id?: string) =>
  useQuery({ queryKey: ["chat", "detail", id], enabled: !!id, queryFn: () => api<ConversationDetail>("/chat/conversations/" + id) });

/** 发一句话，边收边回调。fetch 不能用 EventSource（要 POST），所以自己按行解析 `data: {...}`。 */
export async function sendMessage(content: string, conversationId: string | undefined, onEvent: (event: StreamEvent) => void,
  signal: AbortSignal): Promise<void> {
  const response = await fetch("/api/v1/chat/messages", {
    method: "POST", credentials: "same-origin", signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(conversationId ? { content, conversation_id: conversationId } : { content }),
  });
  if (response.status === 401) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname);
    throw new ApiError(401, "请先登录。");
  }
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(response.status, data.message || "没发出去");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 2);
      if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      cut = buffer.indexOf("\n\n");
    }
  }
}
