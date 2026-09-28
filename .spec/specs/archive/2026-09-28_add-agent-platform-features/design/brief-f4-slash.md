# F4 slash 命令增强（解析层）— 实现简报（第一波）

必读：`design/nexuscli-context.md` slash_commands 节；`src/nexuscli/entrypoints/slash_commands.py` 全文（96 行）；`src/nexuscli/tools/commands.py:75 classify_command`；`design/refs-notes.md` §4。

## 边界（本波只做解析层，不动 repl.py）
只改 `src/nexuscli/entrypoints/slash_commands.py` 与 `tests/test_slash_commands.py`（新建或并入既有命令测试文件，若无既有文件则新建）。mode/allowed-tools 的**会话态应用**是第四波集成车道的事，本波只负责解析并随扩展结果返回。

## 设计规格

### 1. CustomCommand 扩展（保持 frozen dataclass，加带默认值字段）
```python
@dataclass(frozen=True, slots=True)
class CustomCommand:
    name: str
    description: str = ""
    body: str = ""
    source: str = "user"
    path: Path | None = None          # 类型放宽需兼容既有构造调用
    mode: str = ""                     # "react" | "plan" | "team"，空=不改
    allowed_tools: tuple[str, ...] = ()  # 空=不过滤
    argument_hint: str = ""
```
`_parse_command_file` 的 frontmatter 逐行解析扩展：`description`、`mode`（白名单校验，非法值忽略并视为空）、`allowed-tools`（逗号分隔，去空白，保序去重）、`argument-hint`。保持与现解析同款"简单 key: value"，不引 YAML 依赖。

### 2. 位置参数 `$1..$9`
新函数 `split_positional_args(args: str) -> list[str]`：`shlex.split(args, posix=True)`（Windows 路径反斜杠问题：posix=False 会保留引号——选择 posix=True 并在 docstring 注明局限，Claude Code 同样按空白切）。`expand_custom_command(command, args)` 扩展逻辑（保持函数签名不变，返回值改为见第 4 点的 ExpansionResult 会导致 repl 调用点破坏——**所以保留 `expand_custom_command` 返回 str 的旧语义**，新增下面的 `expand_command`）：
```python
@dataclass(frozen=True, slots=True)
class CommandExpansion:
    prompt: str            # 最终提示词
    mode: str              # 来自 frontmatter，空=不改
    allowed_tools: tuple[str, ...]
    rejected_injections: tuple[str, ...]  # 被拒绝的 !`cmd` 命令及原因
def expand_command(command: CustomCommand, args: str) -> CommandExpansion
```
替换顺序：先 `` !`cmd` `` 注入（第 3 点），再 `$1..$9`（`split_positional_args(args)` 按 1-based 填充，越界替换为空串），最后 `$ARGUMENTS`（整段 args；保持旧兼容——无任何占位符时尾接 args 的旧行为保留在 `expand_custom_command` 里不动，`expand_command` 中当 body 不含任何占位符时同样尾接）。

### 3. `` !`cmd` `` shell 注入（安全红线；2026-09-28 所有人裁定：复用 CommandExecutor）
正则 ``r"!`([^`]+)`"`` 扫 body。对每条命令：用 `nexuscli.tools.commands` 现成的 `CommandExecutor`（模块级实例 `_INJECTION_EXECUTOR = CommandExecutor()`；底层 `asyncio.create_subprocess_shell`，Mimosa 认可形态，**禁止** `subprocess.run(cmd, shell=True)`）执行 `await _INJECTION_EXECUTOR.execute(cmd, timeout=30)`：
- CommandGuard 拦截危险命令时抛 `CommandPolicyError` → 捕获即拒绝路径：原片段替换为 `[refused: <cmd> — blocked by command guard]` 并记入 `rejected_injections`（替代手动 classify_command 预检，不要两套都上）；
- 非零退出/超时 → 读 `CommandResult` 字段，替换为 `[failed: <cmd> — <一行原因>]` 并记入 rejected_injections；任何异常都必须吞掉（不能让一条坏命令炸掉整个展开）；
- 正常 → stdout 去尾换行替换进 body。
**契约**：`expand_command` 为 `async def`（返回 CommandExpansion 不变）；旧 `expand_custom_command` 保持同步不动。测试用同步 `def test_*` + `asyncio.run(...)`（仓库习语，参照 tests/test_permissions.py:111）。

### 4. 测试 `tests/test_slash_commands.py`
覆盖：`mode`/`allowed-tools`/`argument-hint` 解析（合法值、mode 非法值忽略、allowed-tools 去重）；`$1..$3` 替换与越界置空；`$ARGUMENTS` 兼容（含旧"无占位符尾接"回归）；`` !`echo hi` `` 注入 stdout（Windows 下 `echo hi` 在 shell=True 可用）；危险命令（如 `` !`rm -rf /` `` 或 classify_command 判高危的形态）被拒且 prompt 含 refused 标记、rejected_injections 非空；执行失败命令（如 `` !`exit 1` `` 类）产生 failed 标记不抛异常；旧格式命令（仅 description）行为不变（回归）；project 覆盖 user 同名仍生效（回归）。
