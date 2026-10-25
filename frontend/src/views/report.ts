// 运行报告导出：markdown 组装纯函数（无副作用，DOM 下载在 TranscriptView 侧）。
// Ported from kitagent src/agent_runtime/web/projection.py:346-380 report_markdown
// （模式照搬：标题/元数据行 → 分节正文 → 末尾声明行「模型结论不代替检查证据」；
// kitagent 按报告快照组装，此处按转录记录 + 用量台账行组装，输入全显式传参。）

import type { TranscriptRecord } from "../api/types";

// 末尾声明行：与 kitagent run 报告同口径——模型结论不代替检查证据。
const DECLARATION = "执行事实来自会话转录与用量台账；模型结论不代替检查证据。";

function contentText(content: TranscriptRecord["content"]): string {
  if (typeof content === "string") {
    return content;
  }
  if (content === undefined) {
    return "（无内容）";
  }
  // content 为数组（分块/结构化负载）时按 JSON 展示，保证确定性输出。
  return JSON.stringify(content, null, 2);
}

// 对话时间线一节：role 分节（### role · ts），usage 行不算对话，交给台账表。
function roleSection(record: TranscriptRecord): string {
  const role = String(record.role ?? "system");
  const ts = record.ts === undefined ? "" : ` · ${String(record.ts)}`;
  return [`### ${role}${ts}`, "", contentText(record.content)].join("\n");
}

// 台账单元格：竖线与换行会破坏 markdown 表格，统一转义/压平。
function tableCell(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

// usage 台账表：列对齐 01 契约的 usage 记录行字段（types.ts TranscriptRecord）。
function usageTable(usage: TranscriptRecord[]): string {
  if (usage.length === 0) {
    return "（本会话无用量记录）";
  }
  const header = [
    "时间",
    "模型",
    "输入",
    "输出",
    "缓存命中",
    "缓存未命中",
    "总计",
    "成本（USD）",
    "成本（CNY）",
  ];
  const lines = [
    `| ${header.join(" | ")} |`,
    `| ${header.map(() => "---").join(" | ")} |`,
  ];
  for (const row of usage) {
    const cells = [
      String(row.ts ?? "-"),
      String(row.model ?? "-"),
      String(Number(row.input_tokens ?? 0)),
      String(Number(row.output_tokens ?? 0)),
      String(Number(row.cache_hit_tokens ?? 0)),
      String(Number(row.cache_miss_tokens ?? 0)),
      String(Number(row.total_tokens ?? 0)),
      Number(row.cost_usd ?? 0).toFixed(4),
      Number(row.cost_cny ?? 0).toFixed(4),
    ].map(tableCell);
    lines.push(`| ${cells.join(" | ")} |`);
  }
  return lines.join("\n");
}

// 组装会话报告 markdown：标题（会话 id + 生成时间）、对话时间线（role 分节）、
// usage 台账表、末尾声明行。records 里的 type:"usage" 行不计入对话时间线。
export function exportReportMarkdown(
  sessionId: string,
  records: TranscriptRecord[],
  usage: TranscriptRecord[],
  generatedAt: string,
): string {
  const timeline = records.filter((record) => record.type !== "usage");
  const lines = [
    "# NexusCLI 会话报告",
    "",
    `会话：${sessionId}`,
    `生成时间：${generatedAt}`,
    "",
    "## 对话时间线",
    "",
  ];
  if (timeline.length === 0) {
    lines.push("（本会话无对话记录）", "");
  } else {
    for (const record of timeline) {
      lines.push(roleSection(record), "");
    }
  }
  lines.push("## 用量台账", "", usageTable(usage), "", "---", "", DECLARATION);
  return `${lines.join("\n")}\n`;
}
