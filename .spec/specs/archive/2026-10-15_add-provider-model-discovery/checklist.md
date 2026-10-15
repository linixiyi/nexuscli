# 本地网关 provider 模型发现与 /model 切换 - 验收清单

## 假设与范围对齐
- [x] 无阻塞性待确认项；如有待确认项，已明确标记无需阻塞/适用外并给出理由
  - spec.md 2.3 待确认问题为「无阻塞性待确认项」，默认模型取记忆中已确认的 cbcn/glm-5.3-flash
- [x] 范围内/范围外与实现一致
  - 实现了模型发现 + Fetch tab + 编排 + 用户级网关配置 + README；未做 Anthropic 发现/能力过滤/缓存（3.3 范围外）
- [x] 本轮发现的可执行问题已回写任务包并完成，未甩给用户
  - 两项均已回写 tasks.md task-e2e 过程发现并修复：① 项目 .env 残留旧模型 id（env 优先级覆盖用户级 config）→ 更新 .env；② 既有测试未 patch USERPROFILE（win32 Path.home() 陷阱）泄漏真实用户配置 → 补 patch

## 简洁性
- [x] 没有未请求的扩展、抽象或配置化
  - 被拒绝的扩展点：模型能力（vision/tools）识别与过滤、模型列表缓存/自动刷新、新 LLM provider 子类（发现逻辑为 openai_compatible.py 模块级函数 + ModelListingProfile 轻量结构，未抽象 discovery 模块/配置节）、GUI 式输入框（base URL 输入用 rich Prompt 在应用外完成）
- [x] 当前方案保持最小可行
  - 零新依赖；复用 CustomModelStore 持久化、_activate_model 热切换、`!` 命令密钥解析既有链路

## 变更边界
- [x] 每项改动都能追溯到明确任务
  - openai_compatible.py→task-discovery；model_selector.py→task-selector；model_cmds.py→task-cmds；~/.nexuscli/{config.json,gateway.key}→task-config；README.md→task-docs；tests/test_model_discovery.py + 2 个隔离修复→task-e2e
- [x] 没有无关重构或顺手清理
  - chat 主链路、factory provider 分派、web_fetch SSRF 守卫、其他 slash commands 均未触碰（git diff 核对）

## 功能完整性
- [x] MVP 功能全部实现
  - fetch_available_models（/v1/models → ModelListingProfile 列表）；/model Fetch tab（输入地址→拉取→过滤→勾选→添加所选→激活）；用户级网关配置
- [x] 边界条件已处理
  - 空列表/非 list 响应/HTTP 4xx5xx/连接失败/非法 scheme 各自抛可读 ModelFetchError；模型 ID 含 `/`（cbcn/glm-5.3-flash）在 Fetch 路径不经过 `/model <provider> <model>` 参数解析；context_length 缺省回退 128K
- [x] 错误处理符合预期
  - 拉取失败在选择器内联显示原因，按 r 重试；配置 `!` 命令失败降级空 key 不阻塞启动（既有行为，验证通过）

## 测试与验证
- [x] 核心逻辑有测试或等价证据
  - tests/test_model_discovery.py：16 项（mock 8 + 状态机 3 + 编排 2 + 激活集成 1 + live 网关 1 + 空 tab 1）
- [x] 关键路径已验证
  - CLI 真实对话、脚本级 fetch→activate→chat 全链路（见验收证据）

## 文档同步
- [x] 受影响文档已更新，或记录适用外
  - README.md `/model` 小节补「API 拉取」说明（交互键位、错误重试、context_length 口径）
- [x] `spec.md` / `tasks.md` / `checklist.md` 已同步
  - tasks.md 全部勾选、过程发现已记录；本清单回填完成

## 部署验证（如适用）
- [x] 本地运行正常，或已记录适用外理由
  - `nexuscli -p "用一句话回复：连通正常" --plain` → `连通正常✅`（EXIT=0）
- [x] 构建成功，或已记录适用外理由
  - `uv run ruff check`（4 个改动文件 + 2 个测试文件）All checks passed；pytest 24 passed

## 边界回归
- [x] 越界负样本：boundary 外改动会被识别或回滚，未混入交付（本轮不适用时记 `N/A：<理由>` 后勾选）
  - `git status` 核对：改动仅 src 3 文件 + tests 3 文件 + README + .spec 包文档；.env 为 gitignored 本地文件（task-e2e 过程发现，属运行环境修复非交付）
- [x] 顺序交换负样本：交换无依赖任务或调换检查节顺序，验收结论不变（本轮不适用时记 `N/A：<理由>` 后勾选）
  - task-docs 与 task-cmds/task-selector 无依赖，交换后结论不变；阶段三依赖阶段二产物，不可交换
- [x] 旁路负样本：不存在绕过 `verify` 的完成入口，未运行的验证一律视为未通过（本轮不适用时记 `N/A：<理由>` 后勾选）
  - 每个勾选任务均先运行 verify 所述命令再勾选；发现的两处回归由 verify 命令暴露（未静默跳过）

## 验收证据
- 证据锚点：HEAD 564c84eb71dbe7ecc9beee6764b730336cd1949b @ 2026-10-15T20:30:03+08:00
- 裁决性检查：`uv run pytest tests/test_model_discovery.py tests/test_model_selector.py tests/test_config.py -q` → `........................ [100%]` 24 passed @ 2026-10-15 20:28
- 验证范围：package；实际执行命令属于该范围（变更模块局部测试 + 相邻 selector/config 回归）
- 外部对标：以 task package 状态机为真；包经 init_spec_package.py 创建于干净 main，分支 spec/2026-10-15_add-provider-model-discovery
- 脚本验证：fetch→profile→_activate_model→chat 全链路 → `1) fetched 28 models; 2) built profile; 3) activated ctx=1000000; 4) chat 120 events; FETCH->ACTIVATE->CHAT CHAIN OK` @ 2026-10-15 20:2x
- 行为成效：`env -u NEXUSCLI_* uv run nexuscli -p "用一句话回复：连通正常" --plain` → `连通正常✅`（EXIT=0，真实网关 cbcn/glm-5.3-flash）；用户级 config 无 env 时 load_config 输出 `openai-compatible / http://localhost:20128/v1 / cbcn/glm-5.3-flash / key_loaded: True`
- 密钥纪律：`git grep` 密钥前缀探针（经 shell 变量携带）→ worktree 与 tracked 均零匹配；密钥仅存 `~/.nexuscli/gateway.key`（0600），config.json 用 `!` 命令读取，仓库/证据无字面量
- 构建：`uv run ruff check src/nexuscli/llm/openai_compatible.py src/nexuscli/entrypoints/model_selector.py src/nexuscli/entrypoints/commands/model_cmds.py tests/test_model_discovery.py tests/test_config.py tests/test_model_selector.py` → All checks passed
- 测试：24 passed（16 discovery + 6 selector 原有 + 2 config 修复后回归）
- 手工验证：/model Fetch tab 状态机与编排由测试覆盖；交互式 prompt_toolkit 渲染经 render() 片段断言覆盖，真机键位留待用户 REPL 体验

---

**验收结果**：通过
