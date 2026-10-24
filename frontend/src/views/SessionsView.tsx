import type { JSX } from "react";
import { useEffect, useState } from "react";

import { ApiError, listSessions } from "../api/client";
import type { SessionMeta } from "../api/types";
import { loadLocalThreads, type LocalThread } from "../state/localThreads";

type Props = {
  activeKey: string | null;
  onOpenSession: (id: string) => void;
};

export function SessionsView({ activeKey, onOpenSession }: Props): JSX.Element {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [localThreads, setLocalThreads] = useState<LocalThread[]>([]);
  const [allProjects, setAllProjects] = useState(false);
  const [manualId, setManualId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (activeKey === null) {
      return;
    }
    let cancelled = false;
    listSessions(allProjects)
      .then((payload) => {
        if (!cancelled) {
          setSessions(payload.sessions);
          setLocalThreads(loadLocalThreads());
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activeKey, allProjects]);

  return (
    <div className="view">
      <label className="row">
        <input
          type="checkbox"
          checked={allProjects}
          onChange={(event) => setAllProjects(event.target.checked)}
        />
        显示全部项目（all=1）
      </label>

      <h3>本浏览器创建的线程</h3>
      {localThreads.length === 0 ? (
        <p className="muted">暂无（F7 上线后在此出现）</p>
      ) : (
        <ul className="list">
          {localThreads.map((thread) => (
            <li key={thread.id}>
              <button type="button" onClick={() => onOpenSession(thread.id)}>
                {thread.title || thread.id}
              </button>
            </li>
          ))}
        </ul>
      )}

      <h3>REPL 会话（服务端枚举）</h3>
      {error !== null && <p className="error">{error}</p>}
      {sessions.length === 0 && error === null ? (
        <p className="muted">当前项目没有已保存会话</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>会话 id</th>
              <th>标题</th>
              <th>消息数</th>
              <th>模型</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr key={session.id}>
                <td>
                  <button type="button" onClick={() => onOpenSession(session.id)}>
                    {session.id}
                  </button>
                </td>
                <td>{session.title || "(untitled)"}</td>
                <td>{session.message_count}</td>
                <td>
                  {session.provider}/{session.model}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>手动打开线程 / 会话</h3>
      <form
        className="row"
        onSubmit={(event) => {
          event.preventDefault();
          if (manualId.trim() !== "") {
            onOpenSession(manualId.trim());
          }
        }}
      >
        <input
          value={manualId}
          onChange={(event) => setManualId(event.target.value)}
          placeholder="thread id 或会话 id"
        />
        <button type="submit">打开</button>
      </form>
    </div>
  );
}
