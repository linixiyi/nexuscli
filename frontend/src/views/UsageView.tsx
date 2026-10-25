import type { JSX } from "react";
import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";

import { ApiError, fetchUsageSummary } from "../api/client";
import type { UsageStats } from "../api/types";

// 用量统计面板（2026-10-26_workbench-kitagent-ui task_0003 重绘：kitagent 无对应
// 面板，用同一设计 token 重绘为 data-panel + stat-card；统计口径与窗口切换不变）。

type Props = {
  active: boolean;
};

const WINDOWS = [1, 7, 30];

function costText(cost: { usd: number; cny: number }): string {
  return `$${cost.usd.toFixed(4)} / ¥${cost.cny.toFixed(4)}`;
}

export function UsageView({ active }: Props): JSX.Element {
  const [days, setDays] = useState(7);
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) {
      return;
    }
    let cancelled = false;
    fetchUsageSummary(days)
      .then((payload) => {
        if (!cancelled) {
          setStats(payload);
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
  }, [active, days]);

  return (
    <div className="data-panel">
      <h2>
        <BarChart3 size={17} /> 用量统计
      </h2>
      <p className="muted">
        GET /v1/usage/summary（= UsageStore.stats 原样）；未配置价格表时成本恒为 0，以供应商返回的实际 Token 为准
      </p>
      <div className="check-row">
        {WINDOWS.map((value) => (
          <button
            key={value}
            type="button"
            className={value === days ? "primary" : ""}
            onClick={() => setDays(value)}
          >
            {value} 天
          </button>
        ))}
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {stats === null ? (
        <p className="muted">加载中…</p>
      ) : (
        <>
          <div className="stat-cards">
            <div className="stat-card">
              <strong>{stats.turns}</strong>
              <span>回合</span>
            </div>
            <div className="stat-card">
              <strong>{stats.total_tokens}</strong>
              <span>总 Token</span>
            </div>
            <div className="stat-card">
              <strong>{costText(stats.total_cost)}</strong>
              <span>成本（USD / CNY）</span>
            </div>
            <div className="stat-card">
              <strong>
                {stats.input_tokens} / {stats.output_tokens}
              </strong>
              <span>输入 / 输出</span>
            </div>
          </div>
          <p className="detail-label">按模型</p>
          <table className="data-table">
            <thead>
              <tr>
                <th>模型</th>
                <th>回合</th>
                <th>Token</th>
                <th>成本</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_model.map((row) => (
                <tr key={row.model}>
                  <td>{row.model}</td>
                  <td>{row.turns}</td>
                  <td>{row.total_tokens}</td>
                  <td>{costText(row.total_cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="detail-label">按来源</p>
          <table className="data-table">
            <thead>
              <tr>
                <th>来源</th>
                <th>回合</th>
                <th>Token</th>
                <th>成本</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_source.map((row) => (
                <tr key={row.source}>
                  <td>{row.source}</td>
                  <td>{row.turns}</td>
                  <td>{row.total_tokens}</td>
                  <td>{costText(row.total_cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
