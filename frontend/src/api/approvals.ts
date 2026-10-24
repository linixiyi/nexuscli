// 审批 API：GET /v1/approvals + POST /v1/approvals/{id}/decision（01 契约）。

import { request } from "./client";

export type PendingApproval = {
  id: string;
  tool_name: string | null;
  input: unknown;
  danger_level: string | null;
  description: string | null;
  scope_type: "thread" | "task";
  scope_id: string;
  created_at: string;
};

export function listApprovals(): Promise<{ approvals: PendingApproval[] }> {
  return request<{ approvals: PendingApproval[] }>("/v1/approvals");
}

export function postDecision(
  id: string,
  decision: "approve" | "deny",
): Promise<{ ok: boolean; id: string; resolved_by: string }> {
  return request<{ ok: boolean; id: string; resolved_by: string }>(
    `/v1/approvals/${encodeURIComponent(id)}/decision`,
    { method: "POST", body: { decision } },
  );
}

// F7：发起 turn（POST 同步阻塞到整个 turn 结束——02 定稿：前端当「后台提交」发出，
// 立即进入 SSE 观察，POST 的最终返回只作终态兜底）。
export function postTurn(
  threadId: string,
  message: string,
  mode: string,
): Promise<{ thread_id: string; text: string; mode: string; total_tokens: number }> {
  return request(`/v1/threads/${encodeURIComponent(threadId)}/turns`, {
    method: "POST",
    body: { message, mode },
  });
}
