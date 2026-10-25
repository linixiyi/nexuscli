import type { JSX } from "react";
import { useEffect, useState } from "react";

import { bootstrapWithToken } from "./api/client";
import { listApprovals, type PendingApproval } from "./api/approvals";
import { ApprovalCard } from "./views/ApprovalCard";
import { Composer } from "./views/Composer";
import { SessionsView } from "./views/SessionsView";
import { TasksView } from "./views/TasksView";
import { TranscriptView } from "./views/TranscriptView";
import { UsageView } from "./views/UsageView";

type Tab = "sessions" | "tasks" | "usage";

function readHash(): { tab: Tab; session: string | null } {
  const hash = window.location.hash.replace(/^#/, "");
  if (hash.startsWith("/threads/")) {
    return { tab: "sessions", session: decodeURIComponent(hash.slice("/threads/".length)) };
  }
  if (hash === "/tasks" || hash === "/usage") {
    return { tab: hash.slice(1) as Tab, session: null };
  }
  return { tab: "sessions", session: null };
}

type BootstrapState =
  | { phase: "bootstrapping" }
  | { phase: "ready" }
  | { phase: "waiting" }
  | { phase: "error"; message: string };

export function App(): JSX.Element {
  // F15：凭证 = 一次性口令换发的 HttpOnly cookie。页面加载时读 #bootstrap= 片段
  // 自动换发（换发后从地址栏抹掉片段）；无片段则提示打开服务日志打印的一键链接。
  const [auth, setAuth] = useState<BootstrapState>(() => {
    const match = window.location.hash.match(/^#bootstrap=(.+)$/);
    if (match !== null) {
      return { phase: "bootstrapping" };
    }
    return { phase: "waiting" };
  });
  const [tab, setTab] = useState<Tab>(() => readHash().tab);
  const [sessionId, setSessionId] = useState<string | null>(() => readHash().session);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);

  // bootstrap：一次性口令换发 cookie + csrf（碎片抹除，token 不留地址栏）
  useEffect(() => {
    const match = window.location.hash.match(/^#bootstrap=(.+)$/);
    if (match === null) {
      return;
    }
    const token = decodeURIComponent(match[1]);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    bootstrapWithToken(token)
      .then(() => setAuth({ phase: "ready" }))
      .catch((err: unknown) => {
        setAuth({ phase: "error", message: err instanceof Error ? err.message : String(err) });
      });
  }, []);

  // 常驻审批轮询（2s，01 定稿节奏）：审批挂起/解决的 SSE 推送为挂账②，MVP 用轮询
  useEffect(() => {
    if (auth.phase !== "ready") {
      return;
    }
    let cancelled = false;
    const poll = (): void => {
      listApprovals()
        .then((payload) => {
          if (!cancelled) {
            setApprovals(payload.approvals);
          }
        })
        .catch(() => {
          // 轮询失败静默重试
        });
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [auth]);

  if (auth.phase !== "ready") {
    return (
      <div className="gate">
        <h1>NexusCLI Workbench</h1>
        {auth.phase === "bootstrapping" && <p>正在完成一次性认证…</p>}
        {auth.phase === "waiting" && (
          <p>
            请从服务启动日志打印的一键链接进入（形如
            <code> http://127.0.0.1:&lt;port&gt;/#bootstrap=… </code>）。
            <br />
            认证凭证由一次性口令换发的 HttpOnly cookie 承载，无需输入任何 key。
          </p>
        )}
        {auth.phase === "error" && <p className="error">{auth.message}</p>}
      </div>
    );
  }

  const openSession = (id: string): void => {
    setSessionId(id);
    setTab("sessions");
    window.location.hash = `/threads/${encodeURIComponent(id)}`;
  };

  const switchTab = (next: Tab): void => {
    setTab(next);
    window.location.hash = next === "sessions" ? "" : `/${next}`;
  };

  return (
    <div className="app">
      <header>
        <h1>NexusCLI Workbench</h1>
        <nav>
          <button type="button" className={tab === "sessions" ? "active" : ""} onClick={() => switchTab("sessions")}>
            会话
          </button>
          <button type="button" className={tab === "tasks" ? "active" : ""} onClick={() => switchTab("tasks")}>
            任务
          </button>
          <button type="button" className={tab === "usage" ? "active" : ""} onClick={() => switchTab("usage")}>
            用量
          </button>
        </nav>
      </header>
      {approvals.length > 0 && (
        <section className="approvals">
          {approvals.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              onResolved={(id) => setApprovals((list) => list.filter((item) => item.id !== id))}
            />
          ))}
        </section>
      )}
      <main>
        {tab === "sessions" && (
          <>
            <Composer onOpenThread={openSession} />
            <SessionsView active onOpenSession={openSession} />
            {sessionId !== null && (
              <section>
                <h3>回放：{sessionId}</h3>
                <TranscriptView sessionId={sessionId} />
              </section>
            )}
          </>
        )}
        {tab === "tasks" && <TasksView active />}
        {tab === "usage" && <UsageView active />}
      </main>
    </div>
  );
}
