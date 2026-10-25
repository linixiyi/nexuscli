import { CheckCheck, Circle, LoaderCircle, ShieldCheck, X } from "lucide-react";
import type { JSX } from "react";

// 状态图标映射（Ported from kitagent (MIT, zed123214/kitagent)
// frontend/src/App.tsx:123-137 的 StatusIcon，状态名按本仓语义增删：
// 本仓工具/审批状态来自 state.ts 事件与 /v1/approvals，无 kitagent 的
// syncing/closing/lost 等 WS 态）。

export function StatusIcon({ status, size = 16 }: { status: string; size?: number }): JSX.Element {
  if (status === "success" || status === "ready" || status === "resolved" || status === "completed") {
    return <CheckCheck size={size} />;
  }
  if (["failed", "denied", "lost", "sync_error", "unknown", "canceled", "error"].includes(status)) {
    return <X size={size} />;
  }
  if (status === "approval" || status === "pending" || status === "queued") {
    return <ShieldCheck size={size} />;
  }
  if (["running", "processing", "submitting"].includes(status)) {
    return <LoaderCircle size={size} className="spin" />;
  }
  return <Circle size={size} />;
}

/** 事件/状态 → 中文标签（本仓口径，含 kitagent 移植面共有的键）。 */
export const labels: Record<string, string> = {
  completed: "已完成",
  success: "已完成",
  resolved: "已解决",
  ready: "就绪",
  approval: "等待审批",
  pending: "等待中",
  queued: "排队中",
  running: "执行中",
  processing: "执行中",
  submitting: "提交中",
  failed: "失败",
  denied: "已拒绝",
  canceled: "已取消",
  error: "错误",
};
