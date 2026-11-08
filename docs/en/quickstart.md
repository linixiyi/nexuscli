# Quickstart (English)

NexusCLI is a terminal-native AI Agent CLI: it reads and writes files, runs
commands, searches the web and your code, and keeps memory across sessions —
all with human-in-the-loop approvals. This page is the shortest path from zero
to a first run. The complete, canonical documentation is written in Chinese;
links at the bottom point you there.

## 1. Install

Three paths, pick one (PyPI packages ship from 0.2.1):

```bash
# ① uv tool (recommended) — isolated tool environment
uv tool install nexuscli

# ② pip / pipx
pip install nexuscli
# or
pipx install nexuscli

# ③ From source — development or unreleased features
#    (use `uv run nexuscli` instead of `nexuscli` in the examples below)
git clone https://github.com/linixiyi/nexuscli.git
cd nexuscli
uv sync --extra dev --frozen
uv run nexuscli --help
```

## 2. Configure a model key

Write a `.env` at your project root. DeepSeek shown; the provider/model key
table lives in [docs/config.md](../config.md) (Chinese):

```bash
# ① write .env with a provider config (DeepSeek example)
cat > .env <<'EOF'
NEXUSCLI_PROVIDER=deepseek
NEXUSCLI_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=your_key_here
EOF
```

Keys can also live in `~/.nexuscli/config.json`; the full precedence chain
and every supported provider are documented in [docs/config.md](../config.md).

## 3. First query: `-p` mode

`-p` runs a single scripted query. There is **no approval prompt channel** in
`-p` mode: calls that would write to disk (write files, run commands,
`save_memory`, …) are **rejected by default (fail-closed, audit-logged)**.
Pass `--yes` explicitly to allow them:

```bash
# ② single query (fail-closed: no disk writes without --yes)
nexuscli -p "Summarize this project"

# ③ allow side effects explicitly
nexuscli -p "Remember this project manages deps with uv" --yes

# ④ environment diagnostics
nexuscli doctor --cwd .
```

The full `-p` semantics (flag order, custom slash commands in `-p`) are in
[docs/permissions.md](../permissions.md#单次--p-模式下没有审批弹窗) (Chinese).

## 4. Interactive mode

```bash
nexuscli
```

`/help` lists built-in slash commands; Shift+Tab switches permission modes.
Type a task in natural language — the agent plans, uses tools with your
approval, and reports back.

## Where to go next

- [中文 README](../../README.md) — capabilities overview and full doc index
  (Chinese)
- [TUTORIAL.md](../../TUTORIAL.md) — end-to-end tutorial from install to the
  first agent task (Chinese)
- [docs/config.md](../config.md) — providers, keys, price overrides, loop and
  turn budgets (Chinese)
- [docs/permissions.md](../permissions.md) — permission rules, approval
  modes, audit log (Chinese)
- [Docs site](https://linixiyi.github.io/nexuscli/) — rendered documentation
  with search
