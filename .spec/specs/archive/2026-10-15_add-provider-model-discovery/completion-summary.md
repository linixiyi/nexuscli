# 本地网关 provider 模型发现与 /model 切换 - 完成总结

## 交付结论
- 结果：本地网关模型发现与 /model 切换落地：fetch_available_models + /model API 拉取 tab + 用户级网关配置，CLI 真实对话与全链路验证通过
- 完成时间：2026-10-15 20:39

## 假设回顾
### 已验证假设
- "OpenAI Chat Completions" 协议即项目现有 `openai-compatible` provider 语义，无需新增 provider 类型。
- API key 允许写入用户级 `~/.nexuscli/config.json`（用户机器上的私有配置，非仓库文件）；仓库源码/测试/文档一律不含真实密钥字面量（Mimosa 约束）。
- `/v1/models` 探测需要（可选）携带 Authorization 头；网关无鉴权时也能 200，两种都兼容。

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- openai_compatible.py 新增 fetch_available_models/ModelListingProfile/ModelFetchError；/model 选择器第三 tab（过滤/勾选/全选/重拉）；model_cmds 编排（写入 CustomModelStore+激活）；用户级 config.json+gateway.key(0600,!命令读取)；README API 拉取说明；tests/test_model_discovery.py 16 项

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 发现逻辑为模块级函数而非新模块；Fetch tab 输入用应用外 rich Prompt；勾选结果复用 CustomModelStore 持久化

### 本轮明确不做的内容
- Anthropic 协议模型发现；模型能力识别与过滤；模型列表缓存/自动刷新；GUI 输入框

## 变更边界
### 本轮主要改动模块
- src/nexuscli/llm/openai_compatible.py, src/nexuscli/entrypoints/model_selector.py, src/nexuscli/entrypoints/commands/model_cmds.py, tests/, README.md

### 明确未触碰的区域
- chat 主链路, factory provider 分派, web_fetch SSRF 守卫, 其他 slash commands, config 加载主链路

## 验证证据
### 备注
- 测试：uv run pytest tests/test_model_discovery.py tests/test_model_selector.py tests/test_config.py -q → 24 passed @ 2026-10-15
- 手工：nexuscli -p 用一句话回复：连通正常 --plain → 连通正常✅（EXIT=0）；fetch→activate→chat 全链路 28 模型 120 事件

## 验证范围
- 级别：package
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-15 20:39
- 范围：本地网关 provider 模型发现与 /model 切换
- 功能：openai_compatible.py 新增 fetch_available_models/ModelListingProfile/ModelFetchError；/model 选择器第三 tab（过滤/勾选/全选/重拉）；model_cmds 编排（写入 CustomModelStore+激活）；用户级 config.json+gateway.key(0600,!命令读取)；README API 拉取说明；tests/test_model_discovery.py 16 项
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：564c84eb71dbe7ecc9beee6764b730336cd1949b..564c84eb71dbe7ecc9beee6764b730336cd1949b
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
无

## 被拒绝的扩展提议
- 模型能力过滤, 列表缓存/自动刷新, 新 provider 子类, discovery 配置节, GUI 输入框

## 门禁证据
- check_spec_package → 验收结果：通过/已收敛；证据锚点 HEAD 564c84e @ 2026-10-15T20:30+08:00

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": ".env 已更新为 cbcn/glm-5.3-flash；nexuscli -p 真实对话 EXIT=0",
      "id": "issue-env-stale-model",
      "summary": "项目 .env 残留旧模型 id 触发网关 openrouter 404",
      "taskId": "task-e2e"
    },
    {
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "两测试补 USERPROFILE patch 后 24 passed",
      "id": "issue-win32-userprofile-leak",
      "summary": "既有测试未 patch USERPROFILE 泄漏真实用户配置",
      "taskId": "task-e2e"
    }
  ],
  "version": 1
}
```
