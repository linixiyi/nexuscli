import { useMemo, useState, type JSX } from "react";
import { ArrowRight, ChevronRight, Clock3, FileText, GitBranch, Radio, ShieldCheck, SquareTerminal } from "lucide-react";

import type { PendingApproval } from "../api/approvals";
import type { WorkbenchEvent, WorkbenchState } from "../state";
import { StatusIcon } from "../components/StatusIcon";
import { Markdown } from "../components/Markdown";
import { exportThreadReportMarkdown } from "./report";
import { deriveReport, deriveTools, type ThreadReport, type ToolInvocation } from "./derive";
import { ApprovalCard } from "./ApprovalCard";

// 执行检查器（2026-10-26_workbench-kitagent-ui task_0004）。布局与 tab 结构
// Ported from kitagent (MIT, zed123214/kitagent) frontend/src/App.tsx Inspector/
// ToolDetail/ReportPanel；数据源差异：本仓无 run 树（单线程线性事件流），按
// turn 分组；无 GET /status，环境卡改显线程标识（gaps.md A8 适配）。派生逻辑
// 在 views/derive.ts（与报告导出共享）。

export type { ThreadReport, ToolInvocation } from "./derive";
export { deriveReport, deriveTools } from "./derive";

function ToolDetail({ tool }: { tool: ToolInvocation }): JSX.Element {
  return (
    <div className="tool-detail">
      <div className="detail-heading">
        <SquareTerminal size={15} />
        <strong>{tool.name}</strong>
        <span className={`badge ${tool.result === null ? "approval" : tool.result.isError ? "failed" : "success"}`}>
          {tool.result === null ? "执行中" : tool.result.isError ? "失败" : "已完成"}
        </span>
      </div>
      <p className="detail-label">调用参数</p>
      <pre>{tool.repr || "未提供"}</pre>
      <p className="detail-label">工具输出</p>
      <pre>
        {tool.result?.content ??
          (tool.result === null ? "调用尚未返回输出。" : "调用已完成，没有文本输出。")}
      </pre>
    </div>
  );
}

function ReportPanel({
  report,
  tools,
  threadId,
  events,
  onTool,
}: {
  report: ThreadReport;
  tools: ToolInvocation[];
  threadId: string;
  events: WorkbenchEvent[];
  /** 点击某条证据 → 跳执行 tab 并选中该工具（复用 Inspector.choose 通路）。 */
  onTool: (seq: number) => void;
}): JSX.Element {
  const [copied, setCopied] = useState(false);
  return (
    <div className="report">
      <div className="report-heading">
        <div className="report-mark">
          <FileText size={24} />
        </div>
        <div>
          <p className="eyebrow">RUN REPORT</p>
          <h2>本轮运行报告</h2>
        </div>
        <span className="badge success">
          <StatusIcon status="completed" size={12} />
          已完成
        </span>
      </div>
      <div className="report-stats">
        <div>
          <strong>{report.tools}</strong>
          <span>工具调用</span>
        </div>
        <div>
          <strong>{report.approvals}</strong>
          <span>人工决定</span>
        </div>
        <div>
          <strong>{report.mode || "react"}</strong>
          <span>执行模式</span>
        </div>
      </div>
      <div className="report-actions">
        <button
          className="primary"
          onClick={() => {
            const blob = new Blob([exportThreadReportMarkdown(threadId, events, new Date().toISOString())], {
              type: "text/markdown;charset=utf-8",
            });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = `nexuscli-thread-${threadId.replace(/[^A-Za-z0-9._-]/g, "_")}.md`;
            anchor.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}
        >
          导出运行报告
        </button>
        <button
          aria-label="复制本轮结论"
          onClick={() => void navigator.clipboard.writeText(report.text).then(() => setCopied(true))}
        >
          {copied ? "已复制" : "复制结论"}
        </button>
      </div>
      <p className="detail-label">本轮模型结论</p>
      <Markdown text={report.text || "本轮没有已提交的最终答复。"} />
      <p className="detail-label">执行事实与来源</p>
      {/* U6（2026-10-27_workbench-ux-round2）：逐工具列出证据（名称 + 状态字），替换原来
          的单行聚合按钮；每行点击跳执行 tab 并选中该工具。视觉复用 tool-row 结构 +
          evidence-list 容器。 */}
      <div className="evidence-list">
        {tools.length === 0 && <p className="muted">本轮没有工具调用，执行事实仅来自模型文本。</p>}
        {tools.map((tool) => {
          const status = tool.result === null ? "approval" : tool.result.isError ? "failed" : "success";
          return (
            <button
              type="button"
              className="tool-row"
              key={tool.seq}
              data-testid="evidence-tool-row"
              onClick={() => onTool(tool.seq)}
            >
              <span className={`tool-state ${status}`}>
                <StatusIcon status={status} size={14} />
              </span>
              <span className="tool-row-content">
                <strong>{tool.name}</strong>
                <small>{tool.result === null ? "等待输出" : tool.result.isError ? "失败" : "已完成"}</small>
              </span>
              <ChevronRight size={14} />
            </button>
          );
        })}
      </div>
      <p className="report-note">
        累计 {report.tokens} tokens · 执行事实来自会话转录与用量台账；模型结论不代替检查证据。
      </p>
    </div>
  );
}

type Props = {
  workbench: WorkbenchState | null;
  approvals: PendingApproval[];
  connected: boolean;
  onDecision: (id: string) => void;
};

export function Inspector({ workbench, approvals, connected, onDecision }: Props): JSX.Element {
  const [tab, setTab] = useState<"execution" | "result">("execution");
  const [selected, setSelected] = useState<number | null>(null);
  const threadId = workbench?.threadId ?? "";
  const tools = useMemo(() => (workbench === null ? [] : deriveTools(workbench.events)), [workbench]);
  const report = useMemo(() => (workbench === null ? null : deriveReport(workbench.events)), [workbench]);
  const pending = approvals;

  // 审批解决后结果 tab 出现新报告 → 结果点提示（简化：report 存在即亮点）。
  function choose(seq: number): void {
    setSelected(seq);
    setTab("execution");
  }

  return (
    <aside className="inspector">
      <div className="inspector-tabs" role="tablist" aria-label="运行检查">
        <button role="tab" aria-selected={tab === "execution"} onClick={() => setTab("execution")}>
          执行过程 <span>{tools.length}</span>
        </button>
        <button role="tab" aria-selected={tab === "result"} onClick={() => setTab("result")}>
          结果 {report !== null && <span className="result-dot" />}
        </button>
      </div>
      <div className="inspector-scroll">
        {tab === "result" ? (
          report !== null ? (
            <ReportPanel
              report={report}
              tools={tools}
              threadId={threadId}
              events={workbench?.events ?? []}
              onTool={choose}
            />
          ) : (
            <div className="panel-empty">
              <FileText size={28} />
              <h3>结果将在完成后出现</h3>
              <p>
                本轮结论和执行事实完成同步后，
                <br />
                可以查看并导出运行报告。
              </p>
            </div>
          )
        ) : (
          <>
            <div className="environment-card">
              <span className="detail-label">执行环境</span>
              <span className="environment-kind">
                <SquareTerminal size={15} />
                本机 Runtime API
              </span>
              <code title={threadId}>{threadId || "未选择线程"}</code>
              <small>SSE {connected ? "已连接" : "未连接"} · 线程内事件按 seq 折叠</small>
            </div>
            {pending.map((approval) => (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                scopeLabel={approval.scope_id === threadId ? "本线程" : approval.scope_id.slice(-8)}
                onResolved={onDecision}
              />
            ))}
            {tools.length === 0 && pending.length === 0 && (
              <div className="panel-empty">
                <Radio size={28} />
                <h3>等待任务开始</h3>
                <p>
                  工具调用、审批与结果
                  <br />
                  将按实际事件显示在这里。
                </p>
              </div>
            )}
            <div className="timeline">
              {report !== null && report.turns > 0 && (
                <div className="run-heading">
                  <GitBranch size={14} />
                  <span>主任务</span>
                  <small>第 {report.turns} 轮</small>
                </div>
              )}
              {tools.map((tool) => (
                <button
                  className={`tool-row ${tool.seq === selected ? "selected" : ""}`}
                  key={tool.seq}
                  onClick={() => choose(tool.seq)}
                >
                  <span
                    className={`tool-state ${
                      tool.result === null ? "approval" : tool.result.isError ? "failed" : "success"
                    }`}
                  >
                    <StatusIcon
                      status={tool.result === null ? "approval" : tool.result.isError ? "failed" : "success"}
                    />
                  </span>
                  <span className="tool-row-content">
                    <strong>{tool.name}</strong>
                    <small>
                      {tool.result === null
                        ? "等待输出"
                        : tool.result.isError
                          ? "失败 · 详见详情"
                          : "已完成"}
                    </small>
                  </span>
                  <ChevronRight size={14} />
                </button>
              ))}
            </div>
            {selected !== null && tools.some((tool) => tool.seq === selected) && (
              <ToolDetail tool={tools.find((tool) => tool.seq === selected) as ToolInvocation} />
            )}
            {report !== null && (
              <button className="view-report" onClick={() => setTab("result")}>
                <FileText size={17} />
                <span>本轮运行报告已就绪</span>
                <ArrowRight size={16} />
              </button>
            )}
          </>
        )}
        {report !== null && report.tokens > 0 && tab === "execution" && (
          <div className="detail-meta">
            <Clock3 size={13} /> 累计 {report.tokens} tokens
          </div>
        )}
      </div>
      <div className="inspector-foot">
        <ShieldCheck size={13} />
        执行与审批状态由 Runtime API 确认
      </div>
    </aside>
  );
}
