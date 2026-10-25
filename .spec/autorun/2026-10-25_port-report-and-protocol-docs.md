# 轮次摘要：2026-10-25_port-report-and-protocol-docs（对齐 A13/A14，收官轮）

## 交付（/workflow 双 lane 并行）
- A13（frontend）：report.ts 纯函数（transcript+usage 组装 markdown，照搬 kitagent
  projection.py:346-380 report_markdown，末行声明「模型结论不代替检查证据」）+
  TranscriptView 导出按钮（Blob 下载，仅静态转录视图）
- A14（api.py + scripts + CI）：ROUTE_MANIFEST 常量元组（17 条路由单一事实源，纯插入
  零行为变化）+ scripts/generate_endpoint_docs.py（ast 解析生成 docs/runtime-api.md
  端点表节，Do-not-edit 围栏）+ scripts/check_endpoint_docs.py（不同步 exit 1）+
  ci.yml 加同步校验步骤

## 证据
- runs.jsonl：task_0001-0004 机器验证；全量回归 1224 passed；ruff/format 绿
- 失败路径实测：手改生成节 → check exit 1 → 重新生成 → exit 0

## 挂账
- ROUTE_MANIFEST 与 _handle 分流的一一对应靠注释+评审维护（无机械化测试，lane 边界外）
