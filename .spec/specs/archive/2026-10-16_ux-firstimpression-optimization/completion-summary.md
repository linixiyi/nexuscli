# 首帧体验优化：快照/流式/错误路径/非TTY - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-16 21:13

## 假设回顾
### 已验证假设
- 假设 A：自动快照改为"默认关闭 + 显式开启"或"仅增量拷贝"后，`/snapshot` `/restore` 手动路径保留，不破坏现有恢复语义
- 假设 B：`-p` 模式实时 echo delta 不会破坏 `--json` 消费者（json 模式保持缓冲一次性输出）

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- features.auto_snapshot 默认关，门控 react/plan/team 三处自动快照（含构造）
- 增量快照：manifest(mtime_ns+size) 指纹 + 同卷硬链接复用，失败降级真拷贝；_copy_tree 重写 os.walk 逐文件
- SKIP_DIRS 增补 .mimosa/.ruff_cache/.pytest_cache（根因：.mimosa 26540 文件占全仓 98%）
- -p 单发流式：text_delta 边到边写 stdout；--json 保持一次性契约
- 非 TTY stdin 优雅降级：一行引导 + 退出码 1，不再 traceback

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 增量用 manifest 即时比对+硬链接，纯标准库；不引入 git checkpoint/文件监听/快照策略插件

### 本轮明确不做的内容
- spec §3.3/§4 排除项维持拒绝：REPL 流式 UI 打磨、-p spinner/计时、headless 管道 REPL、git checkpoint、异步快照线程、快照策略插件化（均为计划内排除，候选下一轮）

## 变更边界
### 本轮主要改动模块
- snapshot/service.py、config.py、agent/{agent,plan_execute,orchestrator}.py、entrypoints/{cli,repl}.py 及对应测试

### 明确未触碰的区域
- llm/* 协议层、react/team 循环逻辑、--json 契约、MCP、权限/HITL

## 验证证据
### 备注
- 测试：主会话复跑 uv run pytest：799 passed, 1 skipped；ruff check 与 ruff format --check 全绿（CI 同款）；runs.jsonl 5/5 任务 exit=0（fp=c44f0ba9b0ee）
- 手工：真网关 127.0.0.1:7864/v1 端到端（key 仅环境注入）：-p 同场景 2m53s→11.279s；--plain 流式 ~10.9s 首行上屏；echo /help 管道一行引导退出码 1 无 traceback

## 验证范围
- 级别：package
- 机器验证率：5/5
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- 单发端到端 15x 提速（171s→11.3s）；真实项目快照 56-183s→0.4s；全量测试 102.5s→52.3s

### 一致性门禁回顾
- check_spec_package.py 已收敛（验收通过，runs.jsonl 证据满足）

## Git 记录
- 日期时间：2026-10-16 21:13
- 范围：首帧体验优化：快照/流式/错误路径/非TTY
- 功能：features.auto_snapshot 默认关，门控 react/plan/team 三处自动快照（含构造）；增量快照：manifest(mtime_ns+size) 指纹 + 同卷硬链接复用，失败降级真拷贝；_copy_tree 重写 os.walk 逐文件；SKIP_DIRS 增补 .mimosa/.ruff_cache/.pytest_cache（根因：.mimosa 26540 文件占全仓 98%）；-p 单发流式：text_delta 边到边写 stdout；--json 保持一次性契约；非 TTY stdin 优雅降级：一行引导 + 退出码 1，不再 traceback
- 操作：提交 / 推送
- 成效：单发端到端 15x 提速（171s→11.3s）；真实项目快照 56-183s→0.4s；全量测试 102.5s→52.3s
- 提交：pending local commit
- 提交区间：697eb0d9ca5193819eb59e4d986ea5f7d5c44eb2..697eb0d9ca5193819eb59e4d986ea5f7d5c44eb2
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
无

## 被拒绝的扩展提议
- 无

## 门禁证据
- spec_verify.py run --all 5/5 exit=0；check 门禁通过

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "SKIP_DIRS 增补后同项目 full=0.4s/incremental=0.37s，未变源文件硬链接复用；tests/test_snapshot.py::test_tool_cache_dirs_are_skipped 固化",
      "id": "snapshot-crawl-tool-caches",
      "summary": "增量快照首轮实测无效，.mimosa 扫描器状态目录 26540 文件被全量爬取拷贝",
      "taskId": "task_0002"
    },
    {
      "actionable": true,
      "dependency": "用户数据，是否清理需用户本人决定",
      "disposition": "external_blocked",
      "id": "legacy-snapshot-disk-bloat",
      "owner": "用户",
      "retryTrigger": "用户运行 nexuscli /snapshot clean",
      "summary": "~/.nexuscli/snapshots 历史快照膨胀（旧逻辑每轮 2 次全量拷贝 2.7 万文件所致）"
    }
  ],
  "version": 1
}
```
