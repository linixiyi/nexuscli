// state.ts：SSE 帧应用器纯函数（A12，Ported from kitagent (MIT, zed123214/kitagent)
// frontend/src/state.ts:5-28 的 applyPatch / SequenceGap，适配 NexusCLI 的事件语境）。
//
// 与 kitagent 的模型差异（有意）：
// - kitagent 是 View+Patch（快照 + 增量操作），seq 在 patch 顶；NexusCLI（A7 定稿）
//   没有快照端点——「快照」= SSE since=0 全量事件回放，每个事件的 payload 内带
//   per-thread 单调 seq（自 1 起严格 +1，turn 边界不重置；src/nexuscli/runtime/
//   api.py:932-958 _append_event）。因此应用器按「帧流折叠」建模：applyEvent 消费
//   一帧返回新状态，断档抛 SequenceGap，调用方（useWorkbench）重拉快照 = 重建
//   初始状态并从 since=0 全量回放。
// - seq 连续性校验必须覆盖所有带 seq 的帧（thread.started/turn.started/error/done
//   等未映射事件也推进游标），否则中间帧丢失会被误判为连续——kitagent 对每个
//   patch 都校验 seq，同款收口。
// - SSE 帧的 id: 行是 events 表全局行 id（重放游标），与 per-thread seq 无关，
//   本应用器不读 id。

import type { SseFrame } from "./api/types";

/** 断档信号：调用方必须重建状态并从 since=0 全量回放（kitagent state.ts:3 同名异常）。 */
export class SequenceGap extends Error {}

/** NexusCLI 语义事件名（规范名，与运行时事件类型的映射见 mapEventName）。 */
export type WorkbenchEventKind =
  | "turn.started"
  | "text_delta"
  | "tool_call"
  | "tool_result"
  | "turn.completed"
  | "approval_suspended"
  | "approval_resolved";

/**
 * SSE 线上事件名 → NexusCLI 语义事件名。
 *
 * - 运行时把引擎的 text_delta 落库为 message.delta（api.py:532），此处归一为
 *   text_delta；同时直接接受 text_delta（引擎内部名，防后续改名双写期漏帧）。
 * - tool_call / tool_result / turn.completed / approval_suspended 与线上名一致
 *   （api.py:533-545、approvals.py:155-162）；approval_resolved 为 A10 审批事件
 *   面的解决侧（本文件按同名接受，payload 形状宽松解析）。
 * - turn.started（2026-10-26_workbench-kitagent-ui G16）映射为用户消息事件，
 *   seq 游标语义不变；其余事件（thread.created / error / done / 心跳注释行）
 *   返回 null：不进事件流，但仍推进 seq 游标（见 applyEvent）。
 */
export function mapEventName(event: string): WorkbenchEventKind | null {
  switch (event) {
    case "turn.started":
      return "turn.started";
    case "message.delta":
    case "text_delta":
      return "text_delta";
    case "tool_call":
      return "tool_call";
    case "tool_result":
      return "tool_result";
    case "turn.completed":
      return "turn.completed";
    case "approval_suspended":
      return "approval_suspended";
    case "approval_resolved":
      return "approval_resolved";
    default:
      return null;
  }
}

export type WorkbenchEvent =
  | { kind: "turn.started"; seq: number; message: string; mode: string }
  | { kind: "text_delta"; seq: number; text: string }
  | { kind: "tool_call"; seq: number; name: string; repr: string; callId: string }
  | {
      kind: "tool_result";
      seq: number;
      name: string;
      content: string;
      isError: boolean;
    }
  | {
      kind: "turn.completed";
      seq: number;
      text: string;
      mode: string;
      totalTokens: number;
    }
  | {
      kind: "approval_suspended";
      seq: number;
      approvalId: string;
      scopeType: string;
      scopeId: string;
      toolName: string | null;
      reason: string;
    }
  | {
      kind: "approval_resolved";
      seq: number;
      approvalId: string;
      decision: string | null;
      resolvedBy: string | null;
    };

/** 折叠中的工作台状态：seq 是「最后应用的 per-thread seq」（0 = 尚未应用任何帧）。 */
export type WorkbenchState = {
  threadId: string;
  seq: number;
  events: WorkbenchEvent[];
};

export function initialWorkbenchState(threadId: string): WorkbenchState {
  return { threadId, seq: 0, events: [] };
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function frameToEvent(
  kind: WorkbenchEventKind,
  seq: number,
  data: Record<string, unknown>,
): WorkbenchEvent {
  switch (kind) {
    case "turn.started":
      // 2026-10-26_workbench-kitagent-ui G16：聊天面板需要用户消息（回放/刷新场景），
      // turn.started 的 payload 是 {message, mode}（api.py _run_turn 落库形状）。
      return {
        kind,
        seq,
        message: asString(data.message),
        mode: asString(data.mode),
      };
    case "text_delta":
      return { kind, seq, text: asString(data.text) };
    case "tool_call":
      // input 经后端 _jsonable 是 Python repr 字符串（01 契约），按原文保留，不 JSON.parse。
      return {
        kind,
        seq,
        name: asString(data.tool_name ?? data.name) || "tool",
        repr: asString(data.input ?? data.arguments),
        callId: asString(data.tool_call_id ?? data.id),
      };
    case "tool_result":
      return {
        kind,
        seq,
        name: asString(data.tool_name ?? data.tool ?? data.name) || "tool",
        content: asString(data.content ?? data.result),
        isError: data.is_error === true,
      };
    case "turn.completed":
      return {
        kind,
        seq,
        text: asString(data.text),
        mode: asString(data.mode),
        totalTokens: asNumber(data.total_tokens),
      };
    case "approval_suspended":
      // 形状来源 approvals.py:155-162 as_event()；字段缺失时宽松归空，不炸 UI。
      return {
        kind,
        seq,
        approvalId: asString(data.approval_id),
        scopeType: asString(data.scope_type),
        scopeId: asString(data.scope_id),
        toolName: typeof data.tool_name === "string" ? data.tool_name : null,
        reason: asString(data.reason) || "approval_pending",
      };
    case "approval_resolved":
      return {
        kind,
        seq,
        approvalId: asString(data.approval_id),
        decision: typeof data.decision === "string" ? data.decision : null,
        resolvedBy: typeof data.resolved_by === "string" ? data.resolved_by : null,
      };
  }
}

function payloadSeq(data: Record<string, unknown>): number | null {
  const value = data.seq;
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : null;
}

/**
 * 应用一帧 SSE：seq 严格连续（new.seq === state.seq + 1）才前进。
 *
 * - 帧不带合法 seq（心跳注释行、data 非 JSON 的兜底帧）→ 原样返回（kitagent
 *   applyPatch 对无关帧原样返回的同位）。
 * - seq <= state.seq（重放/重复帧）→ 原样返回，不重复入库（kitagent state.ts:11 同款）。
 * - seq > state.seq + 1 → 抛 SequenceGap：调用方重建初始状态并从 since=0 全量回放。
 * - seq 恰好 +1 → 推进游标；事件名可映射则追加语义事件，未映射事件只推进游标。
 *
 * 纯函数：不修改入参。
 */
export function applyEvent(state: WorkbenchState, frame: SseFrame): WorkbenchState {
  const seq = payloadSeq(frame.data);
  if (seq === null) {
    // A10 合帧旁路（跨 lane 契约）：text_delta 合并帧不含 per-thread seq（data.text
    // 已含该段全部文本）；帧 id 是 events 表**全局行 id**（api.py:1114-1117——重放
    // 游标，与 per-thread seq 是两套计数）。合帧只追加文本事件，**不得把全局 id 写进
    // seq 游标**：真实库中全局行 id 普遍大于 per-thread seq（跨线程、跨重启累积），
    // 一旦污染游标，其后所有正常 seq 帧都会走 `seq <= state.seq` 被静默吞掉
    // （2026-10-26 复审 F1；demo/e2e 全新 HOME 首线程 id==seq 掩蔽了它）。
    if (frame.event === "message.delta" || frame.event === "text_delta") {
      const merged = frameToEvent("text_delta", 0, frame.data);
      return { ...state, events: [...state.events, merged] };
    }
    return state;
  }
  if (seq <= state.seq) return state;
  if (seq !== state.seq + 1) {
    throw new SequenceGap(
      `线程 ${state.threadId} 事件序号不连续（期望 ${state.seq + 1}，收到 ${seq}），需要重拉快照。`,
    );
  }
  const kind = mapEventName(frame.event);
  if (kind === null) {
    return { ...state, seq };
  }
  return { ...state, seq, events: [...state.events, frameToEvent(kind, seq, frame.data)] };
}

/**
 * 快照重建：从初始状态折叠整段帧流（SSE since=0 回放或 GET /v1/threads/{id}/events）。
 * 服务端历史自身断档（不应发生）时抛 SequenceGap，由调用方如实呈报。
 */
export function replayEvents(threadId: string, frames: SseFrame[]): WorkbenchState {
  let state = initialWorkbenchState(threadId);
  for (const frame of frames) {
    state = applyEvent(state, frame);
  }
  return state;
}
