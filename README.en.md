# NexusCLI

**A terminal-native AI Agent CLI, built for real project work**

Read/write files · search code · run commands · web search · MCP tools · memory · snapshots · Runtime API

[中文 README](README.md) · [English quickstart](docs/en/quickstart.md) · [Docs site](https://linixiyi.github.io/nexuscli/) · [Tutorial (Chinese)](TUTORIAL.md)

> **About this file**: this is the English *entry point*, not a full translation.
> The canonical, complete documentation of this project is written in Chinese.
> Start with the [English quickstart](docs/en/quickstart.md) for the shortest
> path to a first run, then follow the linked Chinese docs for full details.

## What you get

- **Agent loop in your terminal** — ReAct-style tool loop over OpenAI-compatible
  streaming providers (DeepSeek / GLM / Kimi / StepFun / Anthropic native),
  thinking & usage events, loop-call detection.
- **Plan-and-Execute** — planner drafts a dependency DAG, workers run it in
  parallel batches, graph-level checkpoints support cross-process resume.
- **Multi-Agent & workflows** — planner / worker / reviewer collaboration,
  parallel workers, JSON step-graph workflows and expert flows.
- **Tools & MCP** — file, shell, search, web and memory built-ins; MCP client
  and server; BM25 code index with scoped search.
- **Safety rails** — human-in-the-loop approvals, allow/deny/ask permission
  rules, lifecycle hooks, JSONL audit log. Guardrails are approval gates,
  **not a sandbox** (see SECURITY.md).
- **Memory & sessions** — static project memory plus SQLite long-term memory;
  JSONL session persistence with cross-process resume and snapshots.
- **Runtime API & workbench** — HTTP API for threads / turns / event streams
  and a browser workbench for replay, approvals and usage stats.

## Install

PyPI packages ship from 0.2.1. Pick one:

```bash
uv tool install nexuscli    # recommended, isolated tool environment
# or
pip install nexuscli
```

From source (development — use `uv run nexuscli` in place of `nexuscli` below):

```bash
git clone https://github.com/linixiyi/nexuscli.git
cd nexuscli
uv sync --extra dev --frozen
uv run nexuscli --help
```

## First run

The [English quickstart](docs/en/quickstart.md) covers the 60-second path:
write a provider key into `.env`, run a single query with
`nexuscli -p "..."` (no approval prompts in `-p` mode — disk-writing actions
fail closed unless you pass `--yes`), then enter the interactive REPL with
`nexuscli`.

## Documentation

| Link | Language | Content |
|---|---|---|
| [README.md](README.md) | Chinese | canonical README: features, config, commands, boundaries |
| [docs/en/quickstart.md](docs/en/quickstart.md) | English | quickstart: install, model key, first query, REPL |
| [TUTORIAL.md](TUTORIAL.md) | Chinese | end-to-end tutorial from install to a first agent task |
| [docs/config.md](docs/config.md) | Chinese | provider keys, config precedence, model profiles |
| [docs/permissions.md](docs/permissions.md) | Chinese | permission rules and approval semantics |

Everything else — per-topic deep docs, the rendered site and the interactive
architecture map — is reachable from the [Chinese README](README.md) and the
[docs site](https://linixiyi.github.io/nexuscli/).
