import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToast } from "../../components/Toast";

// 和 server/routers/wish.py 的 Pydantic 模型一一对应。
export type WishStatus = "open" | "doing" | "done";

export interface Wish {
  id: number;
  title: string;
  detail: string;
  role: string;
  author: string;
  created_at: string;
  votes: string[];
  status: WishStatus;
  taker: string;
  taken_at: string;
  done_at: string;
}

export interface WishList {
  wishes: Wish[];
  settings: { allow_create: boolean };
  roles: string[];
  me: string;
}

interface ActionResult {
  message: string;
  wish: Wish | null;
}

export type WishAction = "vote" | "take" | "drop" | "done" | "withdraw";

export const STATUS_TEXT: Record<WishStatus, string> = { open: "待接", doing: "进行中", done: "已实现" };

const KEY = ["wishes"];

export function useWishes() {
  return useQuery({ queryKey: KEY, queryFn: () => api<WishList>("/wishes"), refetchInterval: 30_000 });
}

function useWishMutation<V>(send: (vars: V) => Promise<ActionResult>) {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: send,
    onSuccess: (result) => toast(result.message || "好了"),
    onError: (error: Error) => toast(error.message, true),
    onSettled: () => client.invalidateQueries({ queryKey: KEY }),
  });
}

export function useWishAction() {
  return useWishMutation(({ action, id }: { action: WishAction; id: number }) =>
    action === "withdraw"
      ? api<ActionResult>(`/wishes/${id}`, { method: "DELETE" })
      : api<ActionResult>(`/wishes/${id}/${action}`, { method: "POST" }),
  );
}

export function useCreateWish() {
  return useWishMutation((body: { title: string; detail: string; role: string }) =>
    api<ActionResult>("/wishes", { method: "POST", body: JSON.stringify(body) }),
  );
}
