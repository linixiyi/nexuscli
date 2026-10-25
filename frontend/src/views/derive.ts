import type { WorkbenchEvent } from "../state";

// 语义事件 → 视图模型的纯派生层（2026-10-26_workbench-kitagent-ui task_0004）。
// 独立成模块的原因：Inspector 与 report.ts（导出）都要同一套派生，避免循环依赖。

export type ToolInvocation = {
  seq: number;
  name: string;
  repr: string;
  result: { content: string; isError: boolean } | null;
};

/** 工具调用清单：tool_call 开行，tool_result 按名字就近配对（事件流线性，足够可靠）。 */
export function deriveTools(events: WorkbenchEvent[]): ToolInvocation[] {
  const tools: ToolInvocation[] = [];
  for (const event of events) {
    if (event.kind === "tool_call") {
      tools.push({ seq: event.seq, name: event.name, repr: event.repr, result: null });
    } else if (event.kind === "tool_result") {
      const match = [...tools].reverse().find((tool) => tool.name === event.name && tool.result === null);
      const result = { content: event.content, isError: event.isError };
      if (match !== undefined) {
        match.result = result;
      } else {
        tools.push({ seq: event.seq, name: event.name, repr: "", result });
      }
    }
  }
  return tools;
}

export type ThreadReport = {
  text: string;
  tokens: number;
  mode: string;
  turns: number;
  tools: number;
  approvals: number;
};

/** 运行报告派生：最后完成的 turn 为「本轮结论」，工具/审批计数全量统计。 */
export function deriveReport(events: WorkbenchEvent[]): ThreadReport | null {
  let report: ThreadReport | null = null;
  for (const event of events) {
    if (event.kind === "turn.started") {
      report = report ?? { text: "", tokens: 0, mode: event.mode, turns: 0, tools: 0, approvals: 0 };
      report.turns += 1;
      report.mode = event.mode;
    } else if (event.kind === "turn.completed") {
      report = report ?? { text: "", tokens: 0, mode: "react", turns: 1, tools: 0, approvals: 0 };
      report.text = event.text;
      report.tokens = event.totalTokens;
    } else if (event.kind === "approval_resolved") {
      report = report ?? { text: "", tokens: 0, mode: "react", turns: 1, tools: 0, approvals: 0 };
      report.approvals += 1;
    }
  }
  if (report !== null) {
    report.tools = deriveTools(events).length;
  }
  return report;
}
