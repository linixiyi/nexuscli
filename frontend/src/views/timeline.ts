import type { SseFrame } from "../api/types";

// 时间线归并：把 SSE 帧流归并为可渲染条目。tool_call 的 input 经后端 _jsonable
// 处理后是 Python repr 字符串而非 JSON 对象（01 契约），按等宽文本渲染，不得 JSON.parse。

export type TimelineEntry =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool_call"; name: string; repr: string }
  | { kind: "tool_result"; name: string; content: string; isError: boolean }
  | { kind: "error"; message: string }
  | { kind: "turn_completed"; tokens: number }
  | { kind: "raw"; event: string };

export function mergeTimeline(frames: SseFrame[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  let pendingCall: { name: string; repr: string } | null = null;
  for (const frame of frames) {
    const data = frame.data as Record<string, unknown>;
    switch (frame.event) {
      case "turn.started": {
        entries.push({ kind: "user", text: String(data.message ?? "") });
        break;
      }
      case "message.delta": {
        const text = String(data.text ?? "");
        const last = entries[entries.length - 1];
        if (last !== undefined && last.kind === "assistant") {
          last.text += text;
        } else {
          entries.push({ kind: "assistant", text });
        }
        break;
      }
      case "tool_call": {
        pendingCall = {
          name: String(data.tool_name ?? data.name ?? "tool"),
          repr: String(data.input ?? ""),
        };
        break;
      }
      case "tool_result": {
        entries.push({
          kind: "tool_result",
          name: pendingCall?.name ?? String(data.tool ?? "tool"),
          content: String(data.content ?? data.result ?? ""),
          isError: data.is_error === true,
        });
        pendingCall = null;
        break;
      }
      case "error": {
        entries.push({ kind: "error", message: String(data.message ?? data.error ?? "unknown") });
        break;
      }
      case "turn.completed": {
        entries.push({ kind: "turn_completed", tokens: Number(data.total_tokens ?? 0) });
        break;
      }
      default:
        break;
    }
  }
  if (pendingCall !== null) {
    entries.push({ kind: "tool_call", name: pendingCall.name, repr: pendingCall.repr });
  }
  return entries;
}
