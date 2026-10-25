import { useEffect, useState, type JSX } from "react";
import { History } from "lucide-react";

import { fetchTranscript } from "../api/client";
import type { TranscriptRecord } from "../api/types";
import { exportReportMarkdown } from "./report";
import { Markdown } from "../components/Markdown";

// REPL 会话回放（2026-10-26_workbench-kitagent-ui task_0004，替代旧
// TranscriptView 的静态半边）：REPL 会话 id → GET /v1/sessions/{id}/transcript
// 静态渲染。视觉语言与实时聊天一致（user/assistant 气泡 + markdown），usage 记录
// 进检查器口径（这里简化为页脚台账摘要行）。旧线程 SSE 半边由 ChatPanel +
// useWorkbench 承担，本组件不再处理 thread_*。

function isThreadLike(id: string): boolean {
  return id.startsWith("thread_");
}

function downloadReport(sessionId: string, text: string): void {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `nexuscli-report-${sessionId.replace(/[^A-Za-z0-9._-]/g, "_")}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

type Props = {
  sessionId: string;
};

export function ReplayView({ sessionId }: Props): JSX.Element {
  const [records, setRecords] = useState<TranscriptRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRecords(null);
    fetchTranscript(sessionId)
      .then((payload) => {
        if (!cancelled) {
          setRecords(payload.messages);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const usageRows = (records ?? []).filter((record) => record.type === "usage");
  const totalTokens = usageRows.reduce((sum, row) => sum + Number(row.total_tokens ?? 0), 0);

  if (isThreadLike(sessionId)) {
    // 走到这里的 thread id 一律经 App 路由进 ChatPanel；此分支只是兜底提示。
    return (
      <div className="data-panel">
        <p className="muted">线程请从「工作会话」打开（实时视图）。</p>
      </div>
    );
  }

  return (
    <section className="chat-panel">
      <div className="conversation-heading">
        <span>
          <History size={16} /> 会话回放
        </span>
        <span className="muted">
          {records === null ? "加载中…" : `${records.filter((r) => r.type !== "usage").length} 条消息 · ${totalTokens} tokens`}
        </span>
      </div>
      <div className="messages">
        {error !== null && <p className="error-text">{error}</p>}
        {records !== null &&
          records.map((record, index) => {
            if (record.type === "usage") {
              return (
                <p key={index} className="detail-meta" style={{ margin: "10px 0" }}>
                  usage · {String(record.model ?? "")} · {String(record.total_tokens ?? 0)} tokens
                </p>
              );
            }
            const text =
              typeof record.content === "string"
                ? record.content
                : JSON.stringify(record.content ?? null, null, 2);
            return record.role === "user" ? (
              <div className="user-message" key={index}>
                <div className="message-label">你</div>
                <div>{text}</div>
              </div>
            ) : (
              <div className="assistant-message" key={index}>
                <div className="message-label">{String(record.role ?? "system")}</div>
                <Markdown text={text} />
              </div>
            );
          })}
        {records !== null && records.length === 0 && (
          <div className="panel-empty">
            <History size={28} />
            <h3>空会话</h3>
            <p>该会话没有可回放的消息记录。</p>
          </div>
        )}
      </div>
      {records !== null && (
        <div className="replay-note" style={{ padding: "0 28px 16px" }}>
          <button type="button" onClick={() => downloadReport(sessionId, exportReportMarkdown(sessionId, records, usageRows, new Date().toISOString()))}>
            导出报告（markdown）
          </button>
          <span className="muted" style={{ marginLeft: 10 }}>
            静态回放 · usage 台账随报告导出
          </span>
        </div>
      )}
    </section>
  );
}
