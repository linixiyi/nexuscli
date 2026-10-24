import type { JSX } from "react";
import type { PendingApproval } from "../api/approvals";
import { postDecision } from "../api/approvals";

type Props = {
  approval: PendingApproval;
  onResolved: (id: string) => void;
};

// 审批卡片：description 必须渲染为 Reason 行（缺失显示占位并视为服务端契约缺陷）；
// 只有 approve/deny 两个动词（01 定稿：Web 不提供 approve_run/approve_auto）。
export function ApprovalCard({ approval, onResolved }: Props): JSX.Element {
  const resolve = (decision: "approve" | "deny"): void => {
    postDecision(approval.id, decision)
      .then(() => onResolved(approval.id))
      .catch(() => onResolved(approval.id)); // 404 = 已解决/过期，从本地列表移除
  };

  const inputSummary = (() => {
    const input = approval.input;
    if (input === null || input === undefined) {
      return "";
    }
    if (typeof input === "string") {
      return input.length > 160 ? `${input.slice(0, 160)}…` : input;
    }
    const text = JSON.stringify(input);
    return text.length > 160 ? `${text.slice(0, 160)}…` : text;
  })();

  return (
    <div className="approval-card">
      <div className="approval-head">
        <strong>需要审批</strong>
        <span className="muted">
          {approval.scope_type} · {approval.danger_level ?? "?"}
        </span>
      </div>
      <div>
        工具：<code>{approval.tool_name ?? "?"}</code>
      </div>
      {inputSummary !== "" && <pre className="content">{inputSummary}</pre>}
      <div className="reason">
        Reason: {approval.description === null || approval.description === ""
          ? "(no reason provided)"
          : approval.description}
      </div>
      <div className="row">
        <button type="button" onClick={() => resolve("approve")}>
          批准
        </button>
        <button type="button" onClick={() => resolve("deny")}>
          拒绝
        </button>
        <span className="muted">{approval.created_at}</span>
      </div>
    </div>
  );
}
