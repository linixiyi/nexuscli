# 可选 OTLP 导出端点（Langfuse 等外挂观测） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：给 telemetry 增加可配置的 OTLP 导出端点与鉴权头（默认关闭）：用户可选把
  span 导到 Langfuse 这类原生接受 OTLP 的外部平台看 trace；导出失败静默降级 + 一次性
  警告，绝不影响 CLI 运行。
- **目标用户**：想把 NexusCLI 的 trace 事件接进自托管/云端 Langfuse、Jaeger 等 OTLP
  后端的用户。
- **核心价值**：可选外挂观测——不装依赖零感知，装了 extras 填配置即得外部 trace 视图。

## 2. 假设与待确认

### 2.1 已确认事实
- telemetry/tracing.py 自述 OTLP exporter「deliberately NOT ported（minimum slice）」，
  并留有「外部 provider 可先装自己的全局 TracerProvider」的说明；OTLP 端点/鉴权头配置
  目前不存在
- opentelemetry-api 与 opentelemetry-sdk 已是依赖（pyproject.toml:26-27、33-34，核心与
  extras 各一组）；仅缺 opentelemetry-exporter-otlp-proto-http
- config 的 telemetry 段已有 enabled 开关；docs/observability.md 与 docs/config.md 是
  telemetry 口径的文档面
- 仓库可选依赖四步套路（AGENTS.md）：extras 按能力分组、惰性导入 + exc.name 检查翻译
  成带安装指引的类型化错误、其他 import 失败原样抛、pytest marker 声明外部前提

### 2.2 关键假设
- exporter 缺包走「四步套路」：新 extras 组（或并入语义贴合的既有组）+ 惰性导入 +
  exc.name 检查——未装时配置了端点要得到带 `uv sync --extra <group>` 指引的类型化错误
  或一次性警告，而非裸 ImportError
- 导出是 fire-and-forget 旁路：BatchSpanProcessor 的失败只产生一次性警告，不阻塞命令
  退出（进程退出前 flush 尽力而为）
- 本地假 OTLP HTTP 接收端（测试内起线程 http.server）足以验证导出与降级两条路径

### 2.3 待确认问题
- [x] extras 组语义：新建独立组还是并入既有组？→ 结论：由实现者按 pyproject 现有
  extras 结构选贴合位置，结论回写 completion-summary（不阻塞开工）

### 2.4 可选解释与取舍
- 当前选择：只做 OTLP/HTTP（proto-http exporter）不做 gRPC -> 理由：Langfuse/多数
  SaaS 接 HTTP，依赖更轻（不引 grpcio）
- 当前选择：鉴权头用「头名: 值」的字符串映射配置 -> 理由：Langfuse 需要
  Authorization: Basic <base64>，通用头映射即可表达，不做专用 basic-auth 字段

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] telemetry.otlp 配置（endpoint、headers 映射、可选超时，默认关闭）：开启且依赖
  齐全时经 BatchSpanProcessor 向 OTLP/HTTP 端点导出既有 span；缺依赖给带安装指引的
  类型化错误或一次性警告
- [x] 降级保证：端点不可达/导出报错只产生一次性警告（进程生命周期内至多一次），CLI
  退出码与功能行为零影响；关闭（默认）时零感知零开销

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- gRPC exporter、指标/日志信号、采样策略配置、导出重试策略定制
- 任何 Langfuse 专属语义（就是标准 OTLP）

## 4. 最小实现路径
- 最简单可行方案：tracing.py 内新增 OTLP provider 装配分支（配置开启时惰性导入
  exporter 并挂到既有 TracerProvider），配置读 telemetry.otlp 三键
- 暂不引入：新核心依赖（exporter 进 extras）、连接池/重试定制、TLS 自定义
- 不做的抽象/配置化：不做通用「exporter 插件」接口；不做 per-exporter 采样

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest；新增 extras 依赖 opentelemetry-exporter-otlp-proto-http
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/telemetry/**、src/nexuscli/config.py 的 telemetry 配置
  面、pyproject.toml extras、tests 新增、docs/observability.md、docs/config.md、
  README 可观测性行
- 本轮不应触碰：tracing 既有 span 语义、LLM/agent/eval、其他 CLI 命令、frontend
- Git integration branch：`spec/2026-10-27_add-otlp-export`

### 5.1 验证策略
- 范围级别：package
- 变更对象：telemetry 模块、telemetry 配置面、extras 定义、telemetry 测试、两篇 docs
- 快速检查：`uv run --extra dev python -m pytest -q tests/test_telemetry.py`（或等价
  现有 telemetry 测试文件，以仓库实际为准）
- 集成检查：`uv run --extra dev python -m pytest -q`（telemetry 被 eval/agent 多处
  引用，全量兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷（telemetry 单模块
  + 可选 extras）；closeout 由发布门禁统一执行
- 升级触发：若实现发现必须动 llm/agent 才能拿到导出点，停下升级范围并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0，无强结构信号一律 local
- 执行说明：实现段由主会话委托动态工作流的子代理完成（用户显式要求），主会话保留
  路由、验收、done/push 门禁

## 6. 成功标准与验证方式
- [x] 本地假 OTLP HTTP 接收端能收到既有 span 的导出载荷（配置开启 + extras 安装）->
  verify: task_0001
- [x] 缺依赖/端点不可达两条降级路径行为正确（一次性警告、退出码与功能零影响），默认
  关闭零感知 -> verify: task_0001
- [x] extras 与安装指引就位，docs/observability.md、docs/config.md、README 与实现
  同步，全量测试与 Ruff 双检全绿 -> verify: task_0002

## 7. 风险与约束
- 风险点：BatchSpanProcessor 后台线程影响进程退出 -> 缓解：shutdown 尽力而为 +
  测试覆盖正常退出
- 风险点：用户把含敏感信息的 span 导出到外部端点 -> 缓解：文档明示「导出内容 = 既有
  span 面」与端点信任责任，默认关闭
