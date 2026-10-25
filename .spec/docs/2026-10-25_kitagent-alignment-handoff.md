# 交接文档：kitagent 对齐之后的改进接续（2026-10-25）

> 写给下一个会话：本文件是跨会话交接快照。开场先读 AGENTS.md（自动加载）与本文件，
> 即可获得全部上下文。spec 工作流命令（/spec、init_spec_package.py、chain_session.py）
> 与 /workflow（子代理并行）的用法见各归档包的实际用例。

## 状态快照

- 规划面 29/29 特性完成：F1-F13（七轮）+ F14/F15（鉴权重构）+ A1-A14（kitagent 对齐五轮）
- 全部已本地合并 main 并推送远端至 9dc1243；spec 分支已清理；工作树干净
- 测试基线：1224 passed + 2 skipped；ruff/format 双绿；mypy 渐进门禁在 CI（排除清单只减不增）
- kitagent 参照源：/tmp/kitagent-src（MIT；若被清理从 GitHub 重拉 zed123214/kitagent）
- 对齐依据：43 项差距矩阵（30 项「不对齐/不适用」有逐条证据理由，不要为对而对重开）

## 挂账清单（按价值排序，均为设计内延后，非缺陷）

1. **Message.thinking_blocks 字段落地**（F12）：anthropic.py 客户端回传机制已备并有测试，
   字段进 types.py/store.py 编解码/turn_checkpoint/react 累计四处后端到端打通
2. **A9 审批异步化的作用域扩展**：plan/team 任务仍是 F6 阻塞桥（react 专属挂起恢复）；
   并行 batch 兄弟调用挂起时 journal 行停在 started（fail-closed 已兜底，可优化）
3. **审计词表**：会话 auto 放行记 approver="hitl"（应加 session_auto，需改 executor 合同）；
   超时 deny 同理（approval-timeout）
4. **thread_id 改 uuid**：微秒时间戳格式有同微秒撞号风险（runtime/api.py _create_thread）
5. **journal 扩展**：MCP/task/save_memory 等其余副作用工具接入（声明式 requires_journal）；
   prune 命令；worker 等待改 run_in_executor
6. **mypy 收尾**：版本固定（--with 取最新有漂移风险）、dev extras + uv lock、
   executor.py/agent.py 摘除排除清单（修这两个文件时顺手）
7. **CI Actions 首次运行人工确认**：frontend job / mypy / 端点文档同步三个新步骤
   （本地无法验证 CI 语义，F8/A14 挂账）
8. **skill allowed_tools 收窄**（kitagent session/manager.py:226-248 语义，矩阵判 S）：
   load_skill 激活期收窄工具白名单，先核对 $ARGUMENTS 渲染现状
9. **run 收尾统一编排核对**（kitagent runner.py:729-800 shield 语义）：后台子代理收尾
   cancel_all 是否统一触发，确认缺失则补
10. **ROUTE_MANIFEST 机械化测试**：清单与 _handle 分流一一对应目前靠注释+评审

## 候选改进方向（下一轮规划原料）

- 工作台多会话化时整体参照 kitagent web/gateway.py（会话所有权/容量护栏/reap）
- 守护进程化时回看 kitagent core/app.py（断连清理顺序/resume 协调/回放限流）
- 前端 useWorkbench 待接入 App.tsx（pending 幂等恢复已实现未接线）；App cookie 探针
- 六段式压缩摘要模板（compactor.py:18-45）为可选内容级改进

## 硬约束（新会话必须遵守）

- AGENTS.md 全部守则（HOME/USERPROFILE 双重定向、进程组纪律、审批 allow 语义、
  压缩摘要 user 角色等）
- 依赖边界：不引入 LangChain/LangGraph/FastAPI 等 LLM/Web 框架（差距矩阵 30 项不适用判定）
- 工作流纪律：spec 包 + /workflow 子代理并行，主会话只做路由/验收/收口
- push 由用户逐次授权

## 值得加什么（可选依赖模式分析沉淀，2026-10-25 第二次对话）

来源：kitagent 可选依赖机制的源码级分析（engine/router.py:111-124 惰性导入 +
exc.name 检查、checkpoint.py:56-60 二级关卡、pyproject extras 二级粒度、
ensure_graph_available 启动早期快速失败、pytest marker 声明外部前提）。

### 1. 可选依赖「四步套路」固化为仓库约定（✅ 已落地 AGENTS.md「可选依赖」节，2026-10-25）

NexusCLI 已有两处同款雏形（read_file 的 pypdf 缺失返回安装指引、telemetry 未装全链路
no-op），但未成文。任何未来的重可选能力（图编排、本地沙箱后端、新 provider SDK、
重二进制工具）按此四步进：

- extras 按能力分组、粒度到二级（「能用」与「能恢复/持久化」分开——kitagent 的
  graph 与 graph-sqlite 之别）
- 框架 import 只出现在专属模块边界，核心模块顶层零引用
- 惰性导入 + `exc.name` 检查：缺依赖翻译成带安装指引的类型化错误，其他 import
  失败原样抛（内部 bug 不被友好提示掩盖）
- 启动早期 ensure（provider/外设构造之前快速失败）+ pytest marker 声明外部前提

### 2. 触发点（什么时候真的加）

- 图编排类需求出现时：先复用 turn_checkpoint/journal/EngineResult 自研等价物评估，
  不够再按四步套路引入（kitagent engine/base.py 的 Protocol 形态是合同参照）
- 本地沙箱/重二进制工具需求出现时：extras 二级粒度同样适用
- 配置面扩大时：kitagent config.py 的「组合校验（引擎×沙箱互斥）」思想值得随配置项增长引入

### 3. 顺带值得核对的三个小件（矩阵判 S，实施时先核对现状）

- runner 合同校验层：引擎结果与 canonical 状态双重比对（kitagent runner.py:561-596）——
  引擎数量增多（>2 个实现者）时引入，当前 ask/plan/team 三入口暂不需要
- run 收尾 shield 编排核对（runner.py:729-800 finish_run 语义，交接文档挂账 #9 重复引用）
- stdio stderr drain：核对 MCP SDK 是否已处理管道满（kitagent client.py 手写 drain）

### 引入时机判定（防止为加而加）

第二个实现者出现才值得付抽象成本（kitagent 的 Protocol+Router 是两个引擎倒逼出来的）；
单一实现者阶段保持 query_engine 三入口直连，只把 sum type 返回合同（F11 已做）守住。
