// 后台接口返回的数据形状（只列页面用到的字段），对应 console/server.py 里各 *_view / load_* 函数。

export interface PullState { enabled: boolean; hours: number; next_at: string; run_requested: boolean; last_at: string; last_result: string }
export interface WatchState extends Omit<PullState, "hours"> { days: number; minutes: number; rules_text: string }

export interface Status {
  running: boolean;
  connected: boolean;
  running_root: string;
  project_root: string;
  git_branches: string[];
  file_branch: string;
  dirty: boolean;
  last_reply: string;
  index_exists: boolean;
  index_rows: number;
  index_time: string;
  index_running: boolean;
  index_error: string;
  log: string;
  pull: PullState;
  watch: WatchState;
  agents: { seen: string; root: string; who: string }[];
  reminders: { id: string; when: string; who: string; task: string }[];
}

export interface Items<T> { items: T[] }
export interface Question { time: string; where: string; route: string; question: string; answer: string }
export interface Evaluation { time: string; verdict: string; route: string; question: string; answer: string; detail: string }
export interface Miss { id: string; time: string; reason: string; route: string; question: string; preview: string; handled: boolean }
export interface Note { question: string; fact: string }
export interface Person { role: string; name: string }

export interface ReviewAdmin {
  subs: { name: string; git_author: string; open_id: string; dm?: boolean }[];
  state: { last_at?: string; last_result?: string; run_requested?: string };
  web: string;
  groups: string[];
  ok?: boolean;
  message?: string;
}

export interface BombAdmin {
  enabled: boolean;
  days: number;
  minutes: number;
  next_at: string;
  run_requested: boolean;
  branches: string[];
  groups: string[];
  ignore_paths: string[];
  told: number;
  last_at: string;
  last_result: string;
  recent: { at: string; sent: boolean; text: string }[];
  ok?: boolean;
  message?: string;
}

export interface ModelStat { ok?: number; fail?: number; last_ok_at?: string; last_fail_at?: string; last_error?: string }
export interface Models {
  models: Record<string, ModelStat>;
  uses: { use: string; on: boolean; off_reason?: string; model: string; provider: string; fallbacks?: string[] }[];
  started_at?: string;
  provider?: string;
  current: { chat: string; vision: string };
  defaults: { chat: string; vision: string };
  env?: { chat: string; vision: string };
}

export interface AdminWish {
  id: number; title: string; detail: string; role: string; author: string; status: "open" | "doing" | "done";
  taker: string; votes: string[]; created_at: string;
}
export interface WishAdmin {
  wishes: AdminWish[];
  settings: { allow_create?: boolean };
  counts: Record<string, number>;
  total: number;
  roles: string[];
  ok?: boolean;
  message?: string;
}

export interface Account { open_id: string; name: string; department?: string; role: string; first_login: string; last_login: string }
export interface Bilibili { set?: boolean; login?: boolean; uname?: string; message?: string }

export interface MetaCommit {
  hash: string; short: string; author: string; date: string; subject?: string; dup_paths?: string[];
  mine_later?: boolean; mine?: MetaCommit | Record<string, never>; other?: MetaCommit;
}
export interface MetaRef { path: string; count: number; dirty: boolean; replace: boolean; reason: string; since?: MetaCommit | Record<string, never> }
export interface MetaItem {
  path: string;
  kind: "import" | "guid" | "unknown";
  old_guid: string;
  new_guid: string;
  note?: string;
  commit?: MetaCommit | Record<string, never>;
  refs?: MetaRef[];
  grep_done?: boolean;
}
export interface MetaScan { root?: string; items: MetaItem[]; seconds?: number; timing?: Record<string, number> }
export interface MetaResult { ok?: boolean; message?: string; commit?: string; scan?: MetaScan }
