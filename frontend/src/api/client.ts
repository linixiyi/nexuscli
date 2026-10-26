// fetch 封装：凭证 = HttpOnly cookie（经 /v1/workbench/bootstrap 换发，同源自动携带）；
// 变更请求（POST）另附 x-csrf-token（bootstrap 返回后存内存，不落 localStorage）。
// 端点契约唯一来源是 01 文档；本文不定义、不改名任何端点。

import type {
  SessionMeta,
  TaskRecord,
  TranscriptResponse,
  UsageStats,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// csrf 存内存（模块态）：HttpOnly cookie 挡 XSS 偷取，内存态挡持久泄露。
let csrfToken = "";
export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export async function bootstrapWithToken(token: string): Promise<void> {
  const response = await fetch("/v1/workbench/bootstrap", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, `bootstrap 失败（HTTP ${response.status}）`);
  }
  const payload = (await response.json()) as { csrf: string };
  setCsrfToken(payload.csrf);
}

let reissueInFlight: Promise<boolean> | null = null;

/**
 * 403 csrf 自愈（ux-iteration 复审 R1）：凭会话 cookie 重签 csrf 并更新内存态。
 * 覆盖两类丢失：本页刷新（initialize 已处理，这里是兜底）与多标签页轮换——
 * 单值 csrf 下后重签的标签页会使先打开标签页的令牌失效，后者的下一个变更请求
 * 403 后经此重签重试一次即收敛（last-writer-wins + 自愈）。
 */
async function reissueCsrf(): Promise<boolean> {
  if (reissueInFlight === null) {
    reissueInFlight = fetch("/v1/workbench/reissue-csrf", {
      method: "POST",
      headers: { "content-type": "application/json" },
    })
      .then(async (response) => {
        if (!response.ok) {
          return false;
        }
        const payload = (await response.json()) as { csrf?: unknown };
        if (typeof payload.csrf !== "string" || payload.csrf === "") {
          return false;
        }
        csrfToken = payload.csrf;
        return true;
      })
      .catch(() => false)
      .finally(() => {
        reissueInFlight = null;
      });
  }
  return reissueInFlight;
}

export async function request<T>(
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<T> {
  const hasBody = init?.body !== undefined;
  const method = init?.method ?? "GET";
  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (hasBody) {
      headers["content-type"] = "application/json";
    }
    // F15 修正：POST 一律附 csrf（cookie 通道变更请求必检；无体 POST 也不例外——
    // createThreadId 即无体 POST，漏头会被 api.py CSRF 门 403）。
    if (method === "POST") {
      headers["x-csrf-token"] = csrfToken;
    }
    return fetch(path, {
      method,
      headers,
      body: hasBody ? JSON.stringify(init?.body) : undefined,
    });
  };
  let response = await send();
  // CSRF 门 403 → 重签一次并重试一次（bootstrap 403 的文案不含 csrf，不受影响）。
  if (response.status === 403 && method === "POST") {
    let cause = "";
    try {
      const payload = (await response.clone().json()) as { error?: string };
      if (typeof payload.error === "string") {
        cause = payload.error;
      }
    } catch {
      // 非 JSON 错误体
    }
    if (cause.includes("csrf") && (await reissueCsrf())) {
      response = await send();
    }
  }
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { error?: string };
      if (typeof payload.error === "string") {
        message = payload.error;
      }
    } catch {
      // 非 JSON 错误体，保留 HTTP 状态码文案
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

export function listTasks(): Promise<{ tasks: TaskRecord[] }> {
  return request<{ tasks: TaskRecord[] }>("/v1/tasks");
}

export function cancelTask(id: string): Promise<{ canceled: boolean }> {
  return request<{ canceled: boolean }>(`/v1/tasks/${encodeURIComponent(id)}/cancel`);
}

export function listSessions(all: boolean): Promise<{ sessions: SessionMeta[] }> {
  return request<{ sessions: SessionMeta[] }>(
    `/v1/sessions?limit=50${all ? "&all=1" : ""}`,
  );
}

export function fetchTranscript(sessionId: string): Promise<TranscriptResponse> {
  return request<TranscriptResponse>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/transcript`,
  );
}

export function fetchUsageSummary(days: number): Promise<UsageStats> {
  return request<UsageStats>(`/v1/usage/summary?days=${days}`);
}

export function createThreadId(): Promise<{ id: string }> {
  return request<{ id: string }>("/v1/threads", { method: "POST" });
}
