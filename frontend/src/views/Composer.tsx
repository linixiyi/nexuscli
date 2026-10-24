import type { JSX } from "react";
import { useState } from "react";

import { createThreadId } from "../api/client";
import { postTurn } from "../api/approvals";
import { rememberThread } from "../state/localThreads";

type Props = {
  onOpenThread: (id: string) => void;
};

type SubmitState = "idle" | "sending" | "streaming" | "completed" | "failed";

// F7 提交状态机（02 定稿）：idle → sending（POST 已发出）→ SSE 观察（由
// TranscriptView 承担）→ POST 同步返回作终态兜底 completed/failed。
// POST 是同步阻塞请求，按「后台提交」发出，不阻塞 UI。
export function Composer({ onOpenThread }: Props): JSX.Element {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<"react" | "plan" | "team">("react");
  const [state, setState] = useState<SubmitState>("idle");
  const [error, setError] = useState<string | null>(null);

  const submit = (): void => {
    const trimmed = prompt.trim();
    if (trimmed === "" || state === "sending") {
      return;
    }
    setState("sending");
    setError(null);
    createThreadId()
      .then((thread) => {
        rememberThread(thread.id, trimmed.slice(0, 80));
        onOpenThread(thread.id);
        setState("streaming");
        return postTurn(thread.id, trimmed, mode)
          .then(() => setState("completed"))
          .catch((err: unknown) => {
            setError(err instanceof Error ? err.message : String(err));
            setState("failed");
          });
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setState("failed");
      });
  };

  return (
    <div className="composer">
      <textarea
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder="描述任务…（提交后经 SSE 实时观察流式输出）"
        rows={3}
        disabled={state === "sending"}
      />
      <div className="row">
        <select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
          <option value="react">react</option>
          <option value="plan">plan</option>
          <option value="team">team</option>
        </select>
        <button type="button" onClick={submit} disabled={state === "sending" || prompt.trim() === ""}>
          {state === "sending" ? "提交中…" : "发送"}
        </button>
        {state === "streaming" && <span className="muted">流式观察中（POST 结束后给终态）…</span>}
        {state === "completed" && <span className="muted">turn 完成</span>}
        {state === "failed" && <span className="error">{error}</span>}
      </div>
    </div>
  );
}
