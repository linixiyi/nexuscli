import { useEffect, useState, type JSX } from "react";
import { History, ListTodo, MessageSquare, Plus, SquareTerminal, X } from "lucide-react";

import { listSessions } from "../api/client";
import type { SessionMeta } from "../api/types";
import { loadLocalThreads, type LocalThread } from "../state/localThreads";

// 侧栏（2026-10-26_workbench-kitagent-ui task_0003）。布局 Ported from kitagent
// (MIT, zed123214/kitagent) frontend/src/App.tsx 的 sidebar；会话数据源差异：
// 本仓没有「活动会话」概念，列表 = 本浏览器线程（localStorage 记忆）+ 服务端
// REPL 会话枚举（GET /v1/sessions），两组分栏展示；底部任务/用量入口是本仓特有
// 面板（gaps.md G13）。

export type SidebarRoute =
  | { kind: "empty" }
  | { kind: "thread"; id: string }
  | { kind: "replay"; id: string }
  | { kind: "tasks" }
  | { kind: "usage" };

type Props = {
  route: SidebarRoute;
  onNavigate: (route: SidebarRoute) => void;
  onClose: () => void;
};

// W2（2026-10-27_workbench-ux-round2）：REPL 会话 title 为空（或服务端兜底的 "(untitled)"）
// 时回退显示「会话 YYYY-MM-DD HH:MM」——SessionMeta.updated_at 是 unix 秒，按本地时区
// 格式化；多条无标题会话靠时间天然区分。不额外拉 transcript（零请求增量）。
function formatSessionStamp(unixSeconds: number): string {
  const date = new Date(unixSeconds * 1000);
  const pad = (value: number): string => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function sessionTitle(session: SessionMeta): string {
  const title = typeof session.title === "string" ? session.title.trim() : "";
  return title === "" || title === "(untitled)" ? `会话 ${formatSessionStamp(session.updated_at)}` : title;
}

export function Sidebar({ route, onNavigate, onClose }: Props): JSX.Element {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [localThreads, setLocalThreads] = useState<LocalThread[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listSessions(false)
      .then((payload) => {
        if (!cancelled) {
          setSessions(payload.sessions);
          setLocalThreads(loadLocalThreads());
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
  }, [route]);

  const navigate = (next: SidebarRoute): void => {
    onNavigate(next);
    onClose();
  };

  return (
    <nav className="sidebar" aria-label="会话列表">
      <div className="brand">
        <div className="brand-icon">
          <SquareTerminal size={22} />
        </div>
        <div>
          <strong>NexusCLI</strong>
          <small>RUNTIME WORKBENCH</small>
        </div>
        <button className="mobile-close" aria-label="关闭侧栏" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <button className="new-session" onClick={() => navigate({ kind: "empty" })}>
        <Plus size={17} /> 新建会话 <span>＋</span>
      </button>
      <div className="section-caption">
        工作会话 <span>{localThreads.length}</span>
      </div>
      <div className="session-list">
        {localThreads.map((thread) => (
          <button
            className={`session-item ${route.kind === "thread" && route.id === thread.id ? "active" : ""}`}
            key={thread.id}
            onClick={() => navigate({ kind: "thread", id: thread.id })}
          >
            <MessageSquare size={16} />
            <span>
              <strong>{thread.title || thread.id}</strong>
              <small>{thread.id.slice(-10)}</small>
            </span>
          </button>
        ))}
        {localThreads.length === 0 && (
          <p className="muted" style={{ margin: "4px 10px 10px" }}>
            暂无（发起任务后在此出现）
          </p>
        )}
        <div className="section-caption">
          会话回放 <span>{sessions.length}</span>
        </div>
        {error !== null && (
          <p className="muted" style={{ margin: "4px 10px 10px" }}>
            会话枚举失败：{error}
          </p>
        )}
        {sessions.map((session) => (
          <button
            className={`session-item ${route.kind === "replay" && route.id === session.id ? "active" : ""}`}
            key={session.id}
            title={`最近更新 ${formatSessionStamp(session.updated_at)}`}
            onClick={() => navigate({ kind: "replay", id: session.id })}
          >
            <History size={16} />
            <span>
              <strong>{sessionTitle(session)}</strong>
              <small>
                {session.message_count} 条消息 · {session.provider}/{session.model}
              </small>
            </span>
          </button>
        ))}
        {sessions.length === 0 && error === null && (
          <p className="muted" style={{ margin: "4px 10px 10px" }}>
            当前项目没有已保存会话
          </p>
        )}
      </div>
      <div className="sidebar-bottom">
        <div className="view-switch">
          <button
            type="button"
            className={route.kind === "tasks" ? "active" : ""}
            onClick={() => navigate({ kind: "tasks" })}
          >
            <ListTodo size={13} /> 任务
          </button>
          <button
            type="button"
            className={route.kind === "usage" ? "active" : ""}
            onClick={() => navigate({ kind: "usage" })}
          >
            用量
          </button>
        </div>
        <span className="local-badge">
          <span className="status-dot" />
          本机工作台
        </span>
        <p>执行过程实时可见，落盘动作需确认</p>
        <small>NexusCLI Runtime API · Web</small>
      </div>
    </nav>
  );
}
