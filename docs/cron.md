# 定时任务（cron）

> 本文是 [README](../README.md)「⏰ 定时任务（cron）」章节的完整版。

`nexuscli cron` 是**离线形态**的定时提示词注册表（实现：`src/nexuscli/runtime/cron.py` + `entrypoints/cli.py` 的 `cron_app`）：任务表就是一个 JSON 文件，CLI 只做解析与读取面，**没有常驻守护进程、没有调度循环、模块内也不做任何 LLM/网络调用**。真正到点触发由你或外部调度器驱动。

## 任务表

任务表位于 `~/.nexuscli/cron.json`，**手工维护**（当前 CLI 不提供 create/update/delete 写操作）：

```json
{
  "tasks": [
    { "id": "daily-standup",  "cron": "30 9 * * 1-5", "prompt": "汇总本项目的近期改动，生成一份晨会纪要", "enabled": true },
    { "id": "weekly-report",  "cron": "0 18 * * 5",   "prompt": "读取本周会话记录并生成周报草稿", "enabled": false }
  ]
}
```

字段：`id`（字符串）、`cron`（5 字段表达式）、`prompt`（触发时要交给模型的提示词）、`enabled`（缺省 `true`）。容错读取：文件缺失、JSON 损坏、结构不对都按空表处理；缺 `id`/`cron`/`prompt` 的条目被跳过，坏一条不会拖垮整个表。

## 表达式语法

标准 vixie cron 的 5 字段：`分 时 日 月 周`（本地时区，全程 naive local time）。

- 每字段支持：`*`、数字、逗号列表 `a,b`、区间 `a-b`、步进 `*/n` 与 `a-b/n`
- 值域：分 0-59、时 0-23、日 1-31、月 1-12、周 0-7（0=周日，**7 规范化为 0**）
- 日/周两字段都被显式限制时按 vixie cron 的 **OR** 语义匹配（任一字段命中即可）；只限制其中一个时两字段都要满足
- `next` 按分钟粒度向前扫描，最多一年（跨年不饱和的表达式报错）；`now` 本身命中的时刻不会被返回（严格「之后」）

## CLI 命令

```bash
uv run nexuscli cron list          # 每行：id  cron  enabled/disabled  prompt
uv run nexuscli cron next          # 每行：id  YYYY-MM-DD HH:MM（本地时间）；解析失败的任务标 [warn] 不中断整表
uv run nexuscli cron run <id>      # 原样打印该任务的 prompt 后退出——不执行任何东西
```

`cron run` 的行为：任务不存在或已 `enabled: false` 时报错退出码 1；否则把 prompt 原样输出到 stdout。这是刻意的离线设计：执行交给外部调度器串联。

## 外部调度器触发模式

让系统 cron / Windows 任务计划程序在计划时刻运行：

```bash
# bash / Git Bash 示例：把打印出的 prompt 喂给单次执行
uv run nexuscli -p "$(uv run nexuscli cron run daily-standup)"
```

```powershell
# PowerShell 示例
$p = uv run nexuscli cron run daily-standup; uv run nexuscli -p $p
```

## 如实边界（未从 ZCode 移植的部分）

- 无常驻守护进程与进程内调度循环——错过触发不补跑
- 无 `delayMinutes` 相对调度、无 `intervalUnit+interval` 间隔调度、无会话边界约束
- 无桌面通知；CLI 无任务的增删改命令，写表即手工编辑 JSON
