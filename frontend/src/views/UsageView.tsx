import type { JSX } from "react";
import { useEffect, useState } from "react";

import { ApiError, fetchUsageSummary } from "../api/client";
import type { UsageStats } from "../api/types";

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
    <div className="view">
      <p className="muted">
        GET /v1/usage/summary（= UsageStore.stats 原样）；未配置价格表时成本恒为 0，以供应商返回的实际 Token 为准
      </p>
      <div className="row">
        {WINDOWS.map((value) => (
          <button
            key={value}
            type="button"
            className={value === days ? "active" : ""}
            onClick={() => setDays(value)}
          >
            {value} 天
          </button>
        ))}
      </div>
      {error !== null && <p className="error">{error}</p>}
      {stats === null ? (
        <p className="muted">加载中…</p>
      ) : (
        <>
          <div className="cards">
            <div className="card">
              <div className="card-value">{stats.turns}</div>
              <div className="card-label">回合</div>
            </div>
            <div className="card">
              <div className="card-value">{stats.total_tokens}</div>
              <div className="card-label">总 Token</div>
            </div>
            <div className="card">
              <div className="card-value">{costText(stats.total_cost)}</div>
              <div className="card-label">成本（USD/CNY）</div>
            </div>
            <div className="card">
              <div className="card-value">
                {stats.input_tokens} / {stats.output_tokens}
              </div>
              <div className="card-label">输入 / 输出</div>
            </div>
          </div>
          <h3>按模型</h3>
          <table className="table">
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
          <h3>按来源</h3>
          <table className="table">
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
