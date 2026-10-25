# 轮次摘要：2026-10-25_port-alignment-quick-wins（对齐 A1-A4）

## 交付（/workflow 三 lane 并行照搬，出处注释保留）
- A1+A2（api.py）：安全壳（CSP/nosniff/no-referrer/no-store 统一出口 + 64KiB 413 + 非 JSON 415）
  + 写端点幂等门（x-request-id + payload 摘要，重放回填/异摘要 409/容量满 429，在飞重放 Condition 等待）
  ——Ported from kitagent web/app.py:106-128、web/gateway.py:72-93；11 新用例
- A3：docs/workbench.md 与 runtime-api.md 鉴权节重写为三通道表述（bootstrap/cookie/csrf/Origin +
  外部 api_key）+ test_docs_integrity 第 5 用例（鉴权关键词机械防烂）
- A4：repair_tail 移植（半行 JSON 尾截回 + 中部坏行跳过 + 多字节切断安全）+ SessionWriter
  flush 纪律（fsync 可选默认关）——Ported from kitagent core/graph/event_log.py:29-50；10 新用例

## 证据
- runs.jsonl：task_0001-0005 机器验证；全量回归 1160 passed；ruff/format 绿

## 有意偏离与挂账（lane 如实披露，主会话确认）
- 413 门对无 content-length 的 POST 放行（按 spec 字面「>64KiB」，偏离 kitagent 的 not isdigit→413，
  保住既有无体 POST 零适配）
- repair_tail 为视图级截断（reads.py 只读定位，不物理改写用户文件）；物理修复挂账（落点应在
  SessionWriter 打开时，kitagent EventWriter read_event_log(repair_tail=True) 模式）
- bootstrap 不参与幂等门（一次性口令语义本身即幂等）；fsync 默认关（性能特征不变）
