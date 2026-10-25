import type { JSX } from "react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, cancelTask, listTasks } from "../api/client";
import type { TaskRecord } from "../api/types";

export function TasksView({ active }: { active: boolean }): JSX.Element {
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!active) {
      return;
    }
    listTasks()
      .then((payload) => {
        setTasks(payload.tasks);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : String(err));
      });
  }, [active]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => {
      clearInterval(timer);
    };
  }, [refresh]);

  const cancel = async (id: string): Promise<void> => {
    try {
      const payload = await cancelTask(id);
      window.alert(payload.canceled ? "已取消" : "任务已结束，取消无效");
      refresh();
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <div className="view">
      <p className="muted">每 2s 轮询 GET /v1/tasks；取消走 POST /v1/tasks/&#123;id&#125;/cancel</p>
      {error !== null && <p className="error">{error}</p>}
      {tasks.length === 0 ? (
        <p className="muted">没有后台任务</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>id</th>
              <th>mode</th>
              <th>状态</th>
              <th>attempts</th>
              <th>scope</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.id}>
                <td>
                  <button
                    type="button"
                    onClick={() => setExpanded(expanded === task.id ? null : task.id)}
                  >
                    {task.id}
                  </button>
                </td>
                <td>{task.mode}</td>
                <td className={`status-${task.status}`}>{task.status}</td>
                <td>{task.attempts}</td>
                <td>{task.scope}</td>
                <td>{task.updated_at}</td>
                <td>
                  {(task.status === "queued" || task.status === "running") && (
                    <button type="button" onClick={() => void cancel(task.id)}>
                      取消
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {expanded !== null &&
        (() => {
          const task = tasks.find((item) => item.id === expanded);
          if (task === undefined) {
            return null;
          }
          return (
            <div className="detail">
              <h3>{task.id}</h3>
              <pre className="content">{task.prompt}</pre>
              {task.result !== null && <pre className="content">{task.result}</pre>}
              {task.error !== null && <pre className="content error">{task.error}</pre>}
              <p className="muted">worker: {String(task.worker_id ?? "—")} · lease: {String(task.lease_expires_at ?? "—")}</p>
            </div>
          );
        })()}
    </div>
  );
}
