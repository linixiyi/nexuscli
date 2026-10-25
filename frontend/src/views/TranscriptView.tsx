import type { JSX } from "react";
import { useEffect, useState } from "react";

import { fetchTranscript } from "../api/client";
import type { TranscriptRecord } from "../api/types";
import { useEventStream } from "../sse/useEventStream";
import { exportReportMarkdown } from "./report";
import { mergeTimeline } from "./timeline";

type Props = {
  sessionId: string;
};

// 转录回放：thread id → SSE since=0 全量回放 + live；REPL 会话 id → /v1/sessions/{id}/transcript 静态渲染。
// 刷新后重新回放（since=0），这是特性而不是成本（02 定稿口径）。

function isThreadLike(id: string): boolean {
  return id.startsWith("thread_");
}

// 导出报告：Blob + 临时 objectURL + a[download] 触发浏览器下载，不落服务端。
// 文件名只保留安全字符，防会话 id 里的分隔符搅乱下载名。
function downloadReport(sessionId: string, text: string): void {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `nexuscli-report-${sessionId.replace(/[^A-Za-z0-9._-]/g, "_")}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

// 静态转录 → 报告导出：usage 行单独抽出台账表，其余记录进对话时间线。
function exportStaticReport(sessionId: string, records: TranscriptRecord[] | null): void {
  if (records === null) {
    return;
  }
  const usage = records.filter((record) => record.type === "usage");
  downloadReport(
    sessionId,
    exportReportMarkdown(sessionId, records, usage, new Date().toISOString()),
  );
}

function TranscriptBody({ records }: { records: TranscriptRecord[] }): JSX.Element {
  return (
    <div className="timeline">
      {records.map((record, index) => {
        if (record.type === "usage") {
          return (
            <div key={index} className="entry usage">
              usage · {String(record.model ?? "")} · {String(record.total_tokens ?? 0)} tokens
            </div>
          );
        }
        return (
          <div key={index} className={`entry ${record.role ?? "system"}`}>
            <pre className="content">{String(record.content ?? "")}</pre>
          </div>
        );
      })}
    </div>
  );
}

export function TranscriptView({ sessionId }: Props): JSX.Element {
  const isThread = isThreadLike(sessionId);
  const { frames, state, lastError } = useEventStream(isThread ? sessionId : null);
  const [records, setRecords] = useState<TranscriptRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isThread) {
      return;
    }
    let cancelled = false;
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
  }, [sessionId, isThread]);

  if (!isThread) {
    return (
      <div className="view">
        <p className="muted">REPL 会话转录（静态回放）</p>
        {error !== null && <p className="error">{error}</p>}
        {records !== null && (
          <button type="button" onClick={() => exportStaticReport(sessionId, records)}>
            导出报告（markdown）
          </button>
        )}
        {records === null ? <p className="muted">加载中…</p> : <TranscriptBody records={records} />}
      </div>
    );
  }

  const entries = mergeTimeline(frames);
  return (
    <div className="view">
      <p className="muted">
        SSE {state}
        {lastError !== null ? ` · ${lastError}` : ""}
      </p>
      {state === "error" && <p className="error">{lastError}</p>}
      <div className="timeline">
        {entries.map((entry, index) => {
          switch (entry.kind) {
            case "user":
              return (
                <div key={index} className="entry user">
                  <pre className="content">{entry.text}</pre>
                </div>
              );
            case "assistant":
              return (
                <div key={index} className="entry assistant">
                  <pre className="content">{entry.text}</pre>
                </div>
              );
            case "tool_call":
              return (
                <div key={index} className="entry tool">
                  tool_call · {entry.name}
                  <pre className="content">{entry.repr}</pre>
                </div>
              );
            case "tool_result":
              return (
                <div key={index} className={`entry tool ${entry.isError ? "error" : ""}`}>
                  tool_result · {entry.name}
                  <pre className="content">{entry.content}</pre>
                </div>
              );
            case "error":
              return (
                <div key={index} className="entry error">
                  {entry.message}
                </div>
              );
            case "turn_completed":
              return (
                <div key={index} className="entry usage">
                  turn 完成 · {entry.tokens} tokens
                </div>
              );
            default:
              return (
                <div key={index} className="entry raw">
                  {entry.event}
                </div>
              );
          }
        })}
      </div>
    </div>
  );
}
