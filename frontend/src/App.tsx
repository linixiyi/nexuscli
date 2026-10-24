import type { JSX } from "react";
import { useState } from "react";

import { getApiKey } from "./api/client";
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

export function App(): JSX.Element {
  const [apiKey, setApiKey] = useState<string | null>(() => {
    const stored = getApiKey();
    return stored === "" ? null : stored;
  });
  const [draftKey, setDraftKey] = useState("");
  const [tab, setTab] = useState<Tab>(() => readHash().tab);
  const [sessionId, setSessionId] = useState<string | null>(() => readHash().session);

  if (apiKey === null) {
    return (
      <div className="gate">
        <h1>NexusCLI Workbench</h1>
        <p>输入 Runtime API key（NEXUSCLI_RUNTIME_API_KEY；仅存本浏览器 localStorage）</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = draftKey.trim();
            if (trimmed !== "") {
              localStorage.setItem("nexuscli.workbench.apiKey", trimmed);
              setApiKey(trimmed);
            }
          }}
        >
          <input
            type="password"
            value={draftKey}
            onChange={(event) => setDraftKey(event.target.value)}
            placeholder="api key"
          />
          <button type="submit">进入</button>
        </form>
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
      <main>
        {tab === "sessions" && (
          <>
            <SessionsView activeKey={apiKey} onOpenSession={openSession} />
            {sessionId !== null && (
              <section>
                <h3>回放：{sessionId}</h3>
                <TranscriptView sessionId={sessionId} />
              </section>
            )}
          </>
        )}
        {tab === "tasks" && <TasksView activeKey={apiKey} />}
        {tab === "usage" && <UsageView activeKey={apiKey} />}
      </main>
    </div>
  );
}
