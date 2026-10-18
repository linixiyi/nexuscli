# 主线程补测:/history(两轮 lane 分工遗漏项,2026-10-19 补)

- 机器验证:`tests/test_history_search.py`(已有,未重写)
- 真实运行:pty 隔离 RUNHOME + 干净 WORKDIR,REPL 内先发一句「紫罗兰海豚是今天的暗号」,再敲 `/history 紫罗兰 --all`
- 关键输出:

```
┃ ID                     ┃ Title                  ┃ Match                  ┃ Updated          ┃
│ 20261019-000612-94cde0 │ 紫罗兰海豚是今天的暗号 │ 紫罗兰海豚是今天的暗号 │ 2026-10-19 00:07 │
Searched all projects. Resume with /resume <id>
```

- 结论:4 列表格正确、命中行与刚问的问题一致、`--all` 跨项目搜索提示与 `/resume <id>` 衔接可读;无查询词时 `Usage: /history <query> [--all]` 可读
- 矩阵建议:/history 🟡 → ✅
