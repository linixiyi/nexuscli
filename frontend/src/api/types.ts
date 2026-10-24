// 全部 TS 类型对齐 01 契约（.spec/plans/2026-10-25_workbench-and-readme/01-workbench-server.md）
// 与后端形状；后端形状变化的唯一来源是 01 文档与实现，本文不发明端点。

export type TaskStatus = "queued" | "running" | "completed" | "failed" | "canceled";

// tasks.py:13-26 的 asdict 形状（GET /v1/tasks 原样返回）
export type TaskRecord = {
  id: string;
  prompt: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  result: string | null;
  error: string | null;
  mode: string;
  attempts: number;
  worker_id: string | null;
  lease_expires_at: string | null;
  scope: string;
};

// store.py:31-41，REPL 会话元信息（01 契约 GET /v1/sessions 的条目形状）
export type SessionMeta = {
  id: string;
  cwd: string;
  created_at: number;
  updated_at: number;
  model: string;
  provider: string;
  title: string;
  message_count: number;
  forked_from: string;
};

// transcript 记录行：role 消息行或 type:"usage" 记录行（meta 行被服务端剔除）
export type TranscriptRecord = {
  role?: string;
  content?: string | unknown[];
  type?: string;
  ts?: string;
  model?: string;
  provider?: string;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cache_hit_tokens?: number;
  cache_miss_tokens?: number;
  cost_usd?: number;
  cost_cny?: number;
  [key: string]: unknown;
};

export type TranscriptResponse = {
  messages: TranscriptRecord[];
  total: number;
};

// 01 契约 GET /v1/usage/summary 的响应形状（= UsageStore.stats 原样，usage_store.py:215-246）
export type UsageStats = {
  days: number;
  turns: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  total_cost: { usd: number; cny: number };
  by_model: {
    model: string;
    turns: number;
    total_tokens: number;
    total_cost: { usd: number; cny: number };
  }[];
  by_source: {
    source: string;
    turns: number;
    total_tokens: number;
    total_cost: { usd: number; cny: number };
  }[];
};

// SSE 事件帧（01 契约：id:/event:/data: 三段；data 是单行 JSON）
export type SseFrame = {
  id: number;
  event: string;
  data: Record<string, unknown>;
};
