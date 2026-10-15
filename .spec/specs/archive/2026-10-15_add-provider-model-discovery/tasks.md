# 本地网关 provider 模型发现与 /model 切换 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`
- 如果一个任务没有验证方式，就不能开始
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`

## 阶段一：澄清与初始化
- [x] 固化问题定义、关键假设与非目标
  - boundary: 只更新 `.spec/specs/2026-10-15_add-provider-model-discovery/spec.md`
  - verify: `spec.md` 已写明已确认事实（网关探针结果）、关键假设、非目标与当前 integration branch
- [x] 从主分支创建独立工作分支
  - boundary: 只执行 Git 分支检查/创建，不改业务文件
  - verify: 当前分支 `spec/2026-10-15_add-provider-model-discovery`，由干净的 main 创建（init_spec_package.py 输出）

## 阶段二：核心实现
- [x] 实现 fetch_available_models 模型发现函数
  - id: task-discovery
  - boundary: 只改 `src/nexuscli/llm/openai_compatible.py`（新增模块级函数，不改 chat 主链路）
  - verify: `uv run pytest tests/test_model_discovery.py -q` 通过（mock httpx 响应：正常列表/context_length 缺省/HTTP 401/连接拒绝/响应非 list 五类）→ 16 passed，含 live 网关 fetch
- [x] /model 选择器新增 Fetch tab 状态与渲染
  - depends-on: task-discovery
  - id: task-selector
  - boundary: 只改 `src/nexuscli/entrypoints/model_selector.py`
  - verify: selector 状态机测试通过（tab 切换/勾选/全选/取消全选/过滤/add 动作）→ test_selector_fetch_tab_roundtrip 等通过；`uv run ruff check` 通过
- [x] /model 命令串起 输入→拉取→勾选→写入→激活
  - depends-on: task-selector
  - id: task-cmds
  - boundary: 只改 `src/nexuscli/entrypoints/commands/model_cmds.py`
  - verify: 编排函数测试通过（注入假 fetch 返回，断言 CustomModelStore 落盘、去重与激活调用）→ test_store_fetched_profiles_* 通过
- [x] 配置本地网关（用户级 config.json + 外置密钥）
  - depends-on: task-cmds
  - id: task-config
  - boundary: 只写用户级 `~/.nexuscli/config.json`（api_key 用 `!` 命令形式）与 `~/.nexuscli/gateway.key`（0600，用户目录非仓库）
  - verify: `NEXUSCLI_*` env 不设置时 `load_config()` 输出 openai-compatible / http://localhost:20128/v1 / cbcn/glm-5.3-flash / key_loaded: True，且密钥字面量不出现在输出 → 已通过（`!` 命令经 PowerShell 读外置文件成功解析）
- [x] README 配置节补充 /model 拉取说明
  - depends-on: task-cmds
  - id: task-docs
  - boundary: 只改 `README.md` 的配置/模型相关小节
  - verify: 文档包含 Fetch tab 用法说明，且全文无真实密钥字面量 → README 已含「API 拉取」段；`git grep` 密钥探针零匹配

## 阶段三：集成与验收
- [x] 端到端验证：真实网关一轮对话 + /model 拉取链路
  - depends-on: task-config, task-docs
  - id: task-e2e
  - boundary: 只运行命令取证，不改业务文件
  - verify: ① `nexuscli -p "用一句话回复：连通正常" --plain` 输出 `连通正常✅`（EXIT=0；注意网关 reasoning 模型冷启动可达 140s，shell timeout 需放宽）② 脚本级 fetch→profile→_activate_model→chat 单轮成功（28 模型，chat 120 事件）③ `uv run pytest tests/test_model_discovery.py tests/test_model_selector.py tests/test_config.py -q` 24 passed
  - 过程发现（已回写并本轮修复）：项目级 `.env`（gitignored 的本仓库旧开发配置）残留 `NEXUSCLI_MODEL=deepseek-v4-flash`（无 `cbcn/` 前缀），env 优先级高于用户级 config.json，导致 CLI 路径把模型路由到 openrouter 报 404——已更新 `.env` 为 `cbcn/glm-5.3-flash`；既有 2 个测试只 patch `HOME` 未 patch `USERPROFILE`（win32 Path.home() 陷阱），新增用户级 config.json 后把真实配置泄漏进测试——已补 USERPROFILE patch
- [x] 最小化复核点：确认未引入未请求抽象/配置化
  - depends-on: task-e2e
  - id: task-review
  - boundary: 只审查当前任务包相关改动
  - verify: 在验收记录中列出被拒绝的扩展点 → 已在 checklist 简洁性节列出
- [x] 完成验收检查并补齐文档
  - depends-on: task-review
  - id: task-check
  - boundary: 只更新任务包文档、检查项与验收证据
  - verify: `checklist.md` 达到可判定状态（全部勾选 + 验收结果通过 + 至少一条真实命令证据）→ 24 passed 裁决性检查已录，验收结果：通过

---

**当前进度**：由脚本计算，无需手动维护
