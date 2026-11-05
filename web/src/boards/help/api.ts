import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToast } from "../../components/Toast";

// 和 backend/routers/help.py 的 Pydantic 模型一一对应。
export interface Person {
  id: string;
  name: string;
  department: string;
  avatar: string;
  open_id: string;
  skills: string;
  tags: string[];
  helper: boolean;
  updated_at: string;
  manual: boolean;
}

export interface Question {
  id: number;
  title: string;
  keywords: string[];
  answer: string;
  department: string;
  helpers: string[];
  author: string;
  author_id: string;
  created_at: string;
  updated_at: string;
  hits: number;
  score?: number | null;
  matched: string[];
}

export interface PeopleList {
  people: Person[];
  departments: string[];
  me: string;
  admin: boolean;
}

export interface Analysis {
  model: string;
  summary: string;
  department: string;
  tips: string;
  picks: { person: Person; reason: string }[];
  questions: Question[];
}

export type QuestionDraft = Pick<Question, "title" | "keywords" | "answer" | "department" | "helpers">;
export type ProfileDraft = { name?: string; department?: string; skills: string; tags: string[] };

export const NO_DEPARTMENT = "未填部门";

/** 飞书里直接打开和这个人的单聊。open_id 是登录用的同一个应用发的，所以能用。 */
export const feishuChat = (openId: string) =>
  "https://applink.feishu.cn/client/chat/open?openId=" + encodeURIComponent(openId);

export function usePeople() {
  return useQuery({ queryKey: ["help", "people"], queryFn: () => api<PeopleList>("/help/people") });
}

export function useQuestions(q = "", enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["help", "questions", q],
    queryFn: () => api<{ questions: Question[] }>("/help/questions?q=" + encodeURIComponent(q)),
    placeholderData: (prev) => prev,
  });
}

function useHelpMutation<V, R>(send: (vars: V) => Promise<R>, done = "好了") {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: send,
    onSuccess: () => toast(done),
    onError: (error: Error) => toast(error.message, true),
    onSettled: () => client.invalidateQueries({ queryKey: ["help"] }),
  });
}

export function useSaveProfile() {
  return useHelpMutation(({ id, body }: { id: string; body: ProfileDraft }) =>
    id === "me"
      ? api<Person>("/help/people/me", { method: "PUT", body: JSON.stringify(body) })
      : id
        ? api<Person>(`/help/people/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) })
        : api<Person>("/help/people", { method: "POST", body: JSON.stringify(body) }), "资料已保存");
}

export function useRemoveProfile() {
  return useHelpMutation((id: string) => api(`/help/people/${encodeURIComponent(id)}`, { method: "DELETE" }), "已删除");
}

export function useSaveQuestion() {
  return useHelpMutation(({ id, body }: { id: number; body: QuestionDraft }) =>
    id
      ? api<Question>(`/help/questions/${id}`, { method: "PUT", body: JSON.stringify(body) })
      : api<Question>("/help/questions", { method: "POST", body: JSON.stringify(body) }), "问题库已更新");
}

export function useDeleteQuestion() {
  return useHelpMutation((id: number) => api(`/help/questions/${id}`, { method: "DELETE" }), "已删除");
}

export function useHit() {
  return useHelpMutation((id: number) => api(`/help/questions/${id}/hit`, { method: "POST" }), "收到，谢谢反馈");
}

export function useAnalyze() {
  const toast = useToast();
  return useMutation({
    mutationFn: (problem: string) => api<Analysis>("/help/analyze", { method: "POST", body: JSON.stringify({ problem }) }),
    onError: (error: Error) => toast(error.message, true),
  });
}
