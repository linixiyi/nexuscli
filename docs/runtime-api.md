# Runtime API

> 本文是 [README](../README.md)「📡 Runtime API」章节的完整版。

NexusCLI 内置轻量 Runtime API，适合外部系统接入线程、turn、事件和后台任务。

启动服务：

```bash
NEXUSCLI_RUNTIME_API_KEY=dev-key \
uv run nexuscli serve --http --port 8080
```

创建线程：

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/threads \
  -H 'x-api-key: dev-key'
```

发送 turn：

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/threads/<thread_id>/turns \
  -H 'content-type: application/json' \
  -H 'x-api-key: dev-key' \
  -d '{"message":"总结这个项目"}'
```

读取事件：

```bash
curl -sS http://127.0.0.1:8080/v1/threads/<thread_id>/events \
  -H 'x-api-key: dev-key'
```

创建并查看后台任务：

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/tasks \
  -H 'content-type: application/json' \
  -H 'x-api-key: dev-key' \
  -d '{"message":"后台总结这个仓库","mode":"plan"}'

curl -sS http://127.0.0.1:8080/v1/tasks \
  -H 'x-api-key: dev-key'
```

也可以只启动队列消费者，不暴露 HTTP：

```bash
uv run nexuscli worker --workers 2 --cwd .
```

任务队列按项目目录隔离；worker 使用 SQLite 原子事务领取任务，并通过 lease/heartbeat 恢复崩溃任务。运行中取消会阻止 worker 把迟到结果重新覆盖为 completed。
