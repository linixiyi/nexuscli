import { useState, type JSX } from "react";
import { Check, ShieldCheck } from "lucide-react";

import { ApiError } from "../api/client";
import { postDecision, type PendingApproval } from "../api/approvals";

// 审批卡片（2026-10-26_workbench-kitagent-ui task_0004；视觉 Ported from kitagent
// (MIT, zed123214/kitagent) frontend/src/App.tsx ApprovalCard，语义保留本仓 01 定稿：
// description 必须渲染为 Reason 行（缺失显示占位并视为服务端契约缺陷）；只有
// approve/deny 两个动词，不提供 approve_run/approve_auto）。

type Props = {
  approval: PendingApproval;
  scopeLabel: string;
  onResolved: (id: string) => void;
};

export function ApprovalCard({ approval, scopeLabel, onResolved }: Props): JSX.Element {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const enabled = !sending;

  // 决策失败必须就地可见（复审 F3）：404（已解决/过期）才移除卡片；其余错误保留
  // 卡片并呈现原因——吞成「已解决」会让只读态下出现「卡片消失→轮询加回」的无限闪烁。
  const resolve = (decision: "approve" | "deny"): void => {
    setSending(true);
    setError(null);
    postDecision(approval.id, decision)
      .then(() => onResolved(approval.id))
      .catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 404) {
          onResolved(approval.id);
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => setSending(false));
  };

  const inputSummary = (() => {
    const input = approval.input;
    if (input === null || input === undefined) {
      return "";
    }
    if (typeof input === "string") {
      return input.length > 400 ? `${input.slice(0, 400)}…` : input;
    }
    const text = JSON.stringify(input);
    return text.length > 400 ? `${text.slice(0, 400)}…` : text;
  })();

  return (
    <div className="approval-card" aria-label="工具审批">
      <div className="approval-title">
        <ShieldCheck size={16} />
        <strong>需要你的确认</strong>
        <span>
          {scopeLabel} · {approval.danger_level ?? "?"}
        </span>
      </div>
      <p>
        将通过 <strong>{approval.tool_name ?? "?"}</strong> 执行本次调用。
      </p>
      {inputSummary !== "" && <pre>{inputSummary}</pre>}
      <div className="reason">
        Reason:{" "}
        {approval.description === null || approval.description === ""
          ? "(no reason provided)"
          : approval.description}
      </div>
      {error !== null && <p className="error-text">决策未送达：{error}（卡片保留，可重试）</p>}
      <div className="approval-actions">
        <button type="button" disabled={!enabled} onClick={() => resolve("deny")}>
          拒绝
        </button>
        <button type="button" className="primary" disabled={!enabled} onClick={() => resolve("approve")}>
          <Check size={13} />
          批准
        </button>
      </div>
      {/* U5：显式 zh-CN——无参 toLocaleString 跟随浏览器默认，en-US 环境泄漏
          「10/26/2026, 5:37:13 PM」格式（2026-10-27 实测 U5）。 */}
      <p className="detail-meta">挂起于 {new Date(approval.created_at).toLocaleString("zh-CN")}</p>
    </div>
  );
}
