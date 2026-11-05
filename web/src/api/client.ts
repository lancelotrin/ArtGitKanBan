// 所有看板共用的请求函数。后端错误统一是 {"message": ...}；401 跳去登录页。

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "网络出错了，再试一次");
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    throw new ApiError(401, "请先登录。");
  }
  if (!response.ok) throw new ApiError(response.status, (data && data.message) || "没成功");
  return data as T;
}
