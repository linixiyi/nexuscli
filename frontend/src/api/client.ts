// fetch 封装：附 x-api-key 头；错误归一为 ApiError{status, message}。
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

export function getApiKey(): string {
  const raw = localStorage.getItem("nexuscli.workbench.apiKey");
  return raw === null ? "" : raw;
}

async function request<T>(
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<T> {
  const hasBody = init?.body !== undefined;
  const response = await fetch(path, {
    method: init?.method ?? "GET",
    headers: {
      "x-api-key": getApiKey(),
      ...(hasBody ? { "content-type": "application/json" } : {}),
    },
    body: hasBody ? JSON.stringify(init?.body) : undefined,
  });
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
