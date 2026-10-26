import type { JSX } from "react";
import { useCallback, useEffect, useState } from "react";
import { ListTodo } from "lucide-react";

import { ApiError, cancelTask, listTasks } from "../api/client";
import type { TaskRecord } from "../api/types";
import { labels, StatusIcon } from "../components/StatusIcon";

// 后台任务面板（2026-10-26_workbench-kitagent-ui task_0003 重绘：kitagent 无对应
// 面板，用同一设计 token 重绘为 data-panel；轮询节奏与取消语义不变）。
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
    <div className="data-panel">
      <h2>
        <ListTodo size={17} /> 后台任务
      </h2>
      {/* U7（2026-10-27 实测）：副标题是用户文案，端点与轮询细节不进产品界面 */}
      <p className="muted">后台队列任务实时刷新，可在此取消</p>
      {error !== null && <p className="error-text">{error}</p>}
      {tasks.length === 0 ? (
        <div className="panel-empty">
          <ListTodo size={28} />
          <h3>没有后台任务</h3>
          <p>
            经 Runtime API 提交的队列任务
            <br />
            会按 lease/heartbeat 在这里显示。
          </p>
        </div>
      ) : (
        <table className="data-table">
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
                  <button type="button" onClick={() => setExpanded(expanded === task.id ? null : task.id)}>
                    {task.id}
                  </button>
                </td>
                <td>{task.mode}</td>
                <td>
                  <span className={`status-badge ${task.status}`}>
                    <StatusIcon status={task.status} size={12} />
                    {labels[task.status] ?? task.status}
                  </span>
                </td>
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
            <div className="tool-detail">
              <div className="detail-heading">
                <strong>{task.id}</strong>
                <span className={`badge ${task.status}`}>{task.status}</span>
              </div>
              <p className="detail-label">任务输入</p>
              <pre>{task.prompt}</pre>
              {task.result !== null && (
                <>
                  <p className="detail-label">结果</p>
                  <pre>{task.result}</pre>
                </>
              )}
              {task.error !== null && (
                <>
                  <p className="detail-label">错误</p>
                  <pre>{task.error}</pre>
                </>
              )}
              <p className="detail-meta">
                worker: {String(task.worker_id ?? "—")} · lease: {String(task.lease_expires_at ?? "—")}
              </p>
            </div>
          );
        })()}
    </div>
  );
}
