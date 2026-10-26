# Eval — Offline Task-Level Regression

`nexuscli eval` is an offline evaluation harness for task-level agent
behavior. Instead of calling a real LLM, a **scripted (fake) LLM client**
drives the real `Agent` through JSON scenarios; assertions then check the
tool-call sequence and the final output. Consecutive runs are diffed so a
change that breaks an agent workflow shows up as a **regression** before it
reaches production. Every report also records deterministic **cost metrics**
(turns and tool calls, warned on growth) and groups results by **difficulty
tier**, so a regression report says *which* tier degraded and whether the run
got more expensive — without either ever changing a PASS/FAIL verdict.

## Commands

```bash
nexuscli eval list              # list built-in scenarios (examples/evals)
nexuscli eval run               # run every built-in scenario (same as --all)
nexuscli eval run --all         # explicit form of the default
nexuscli eval run --file path   # one scenario file, or a directory of *.json
```

`eval run` prints one `PASS/FAIL` line per scenario, a `COST` warning line
under any scenario whose cost grew since the previous report, an overall
summary line and a per-tier `difficulty:` line; it writes the report to
`<cwd>/.nexuscli/evals/last-report.json` and exits `1` when any scenario
failed (CI-friendly). Cost warnings and tier counts are informational and
never affect the exit code.

## Scenario schema

A scenario is one JSON document. Required keys are `name`, `prompt`,
`scripted_turns`; everything else is optional but strictly validated — unknown
keys, wrong shapes, deny rules targeting tools the scenario does not define,
or invalid JSON all fail with a clear error naming the file.

```jsonc
{
  "name": "read-task-details",          // required, non-empty
  "description": "Happy path",          // optional, shown by `eval list`
  "prompt": "List the tasks ...",       // required: the user message fed to the agent
  "tools": [                            // optional: deterministic stub tools
    {
      "name": "list_tasks",
      "description": "List tracked tasks.",
      "result": "1. fix login (urgent)", // canned tool output
      "is_error": false,                 // optional (default false)
      "read_only": true,                 // optional (default true)
      "requires_approval": false,        // optional (default false)
      "danger_level": "safe"             // optional: safe | medium | high
    }
  ],
  "scripted_turns": [                   // required, non-empty: the fake LLM's
    {                                   // responses, replayed in order
      "tool_calls": [                   // each call: {"name", "arguments"}
        {"name": "list_tasks", "arguments": {}}
      ]
    },
    {
      "text": "The most urgent task is 'fix login'."  // final assistant text
    }
  ],
  "permission_deny": ["deploy_app"],    // optional: injected as deny rules
  "hitl_mode": "auto",                  // optional: auto | never | always
  "difficulty": "short",                // optional: short | medium | long (default medium)
  "assertions": {                       // optional; seven supported checks
    "expected_tool_sequence": ["list_tasks"],
    "final_contains": ["fix login"],
    "forbidden_tools": ["bash"],
    "max_turns": 3,
    "required_tools": ["list_tasks"],
    "max_tool_calls": {"list_tasks": 2},
    "max_total_tool_calls": 4
  }
}
```

### Difficulty tiers

`difficulty` is the author's declared tier for the scenario, one of `short` /
`medium` / `long` — roughly a one-or-two-step task, a multi-step task that has
to converge, and an extended trajectory. The key is optional and defaults to
`medium`; when present it must be exactly one of the three names
(case-sensitive: `"Short"`, `""`, `null` and non-strings are all rejected with
`'difficulty' must be one of ['short', 'medium', 'long'], got ...`).

The tier never changes how a scenario runs or is judged. Its only consumer is
the reporter, which aggregates PASS/FAIL and regression counts per tier (see
[Difficulty breakdown](#difficulty-breakdown)) so a regression report can show
*which* tier is degrading. None of the four built-in scenarios declares a tier,
so they all land in `medium`.

### Assertions

| assertion | meaning |
| --- | --- |
| `expected_tool_sequence` | the exact ordered list of tool names the agent must call |
| `final_contains` | every listed substring must appear in the final assistant text |
| `forbidden_tools` | none of these tool names may appear anywhere in the call sequence |
| `max_turns` | the react loop must finish within this many turns |
| `required_tools` | every listed tool must have been called at least once, in any order — the unordered subset counterpart of `expected_tool_sequence` |
| `max_tool_calls` | `{"tool": n}` map: each named tool may be called at most `n` times |
| `max_total_tool_calls` | the run may make at most this many tool calls in total (the efficiency budget) |

The two trajectory flavours are deliberate. `expected_tool_sequence` is
precise but brittle against real regressions (any reordering fails it);
`required_tools` only asks that every listed tool was reached. Use the exact
sequence when the order *is* the contract, the subset form when it is not.

The three call-count assertions share one counting rule with the sequence
checks: they read every `tool_call` event the agent emitted, **including calls
the loop guard intercepted** instead of executing (the agent still surfaces
them as `tool_call` events), so they measure what the run *attempted*. That is
what lets `max_tool_calls` pair with the agent's loop guard: the guard blocks
the third consecutive identical call (default `agent.loop_detection_threshold`
= 3) and asks the model to change strategy, so `"max_tool_calls": {"poll_job":
3}` says "the same call repeated three times must have been intercepted — a
fourth attempt is a regression". `04_repeat_call_loop_guard.json` is the
built-in scenario for exactly this.

Validation is as strict as the rest of the schema: `max_tool_calls` must be a
non-empty object mapping tool names to positive integers (an empty object
asserts nothing; a cap of `0` is rejected — "never call this tool" is what
`forbidden_tools` is for), and `max_total_tool_calls` must be a positive
integer (`true` is not a count). Every miss produces one human-readable
failure reason in the report and on the `FAIL` line:

| assertion | failure reason |
| --- | --- |
| `required_tools` | `required tool was never called: <tool>` |
| `max_tool_calls` | `tool <tool> was called <n> times, limit is <limit>` |
| `max_total_tool_calls` | `made <n> tool calls in total, limit is <limit>` |

A turn is one scripted LLM response. A turn with `tool_calls` gets
`stop_reason=tool_use` (so the loop continues after the tools run); a turn
with only `text` ends the run.

## How a scenario runs

The runner reuses the same fake-LLM injection pattern as the agent unit tests
(`tests/test_agent_turns.py`): a `ScriptedLlmClient` replays `scripted_turns`
as the streaming event protocol, and the scenario's tools are injected as
deterministic stub tools into a real `ToolRegistry`.

**Stub tools, deliberately chosen over a real builtin read-only subset.** The
whole execution path — tool validation, permission rules, plan/HITL gates,
the react loop — is the real one; only each tool's *effect* is canned. Real
builtin tools would read the actual workspace, making scenarios depend on
repo contents and machine layout; stubs keep every scenario hermetic and
byte-identical across machines, which is what a regression baseline needs.
(Scenario `02_denied_privileged_call.json` proves the gate chain is real: its
canned result `"deployed"` never appears — the deny rule intercepts first.)

Each run is isolated: no network call, no real LLM, telemetry disabled
(no usage recording), audit log disabled, no context compression or skill
prefixes. The report itself is written under the project's own
`.nexuscli/evals/`.

One side effect worth naming: scenarios run the real `Agent.run`. Automatic
pre/post-turn snapshots are opt-in (`features.auto_snapshot`, default off), so
under the default configuration nothing is copied; with the switch on, each
turn copies the project tree to `~/.nexuscli/snapshots/<digest>` twice, like
any other agent run. Nothing else escapes the project or the user home.


## Regression comparison

Every run writes `.nexuscli/evals/last-report.json`. On the next run the
previous report is read back and each scenario is marked:

| previous → current | verdict |
| --- | --- |
| pass → fail | `regression` |
| fail → pass | `fixed` |
| pass → pass | `unchanged` |
| fail → fail | `still_failing` |
| no previous entry | `new` |

The summary counts `regressions` and `fixed` separately, so
`nexuscli eval run` doubles as a CI gate: nonzero exit on any failure, and
the `regressions` count tells you which failures are new. A corrupt previous
report is an error, not a silent reset — silently dropping the baseline would
turn real regressions into `new` verdicts.

### Report shape

One scenario entry and the summary block, exactly as written to
`last-report.json` (plain JSON, no dataclass dump, so CI can diff or publish
it directly):

```json
{
  "name": "repeat-call-loop-guard",
  "status": "pass",
  "difficulty": "medium",
  "failure_reasons": [],
  "tool_sequence": ["poll_job", "poll_job", "poll_job"],
  "turns": 4,
  "total_tool_calls": 3,
  "comparison": {"previous": "pass", "verdict": "unchanged"},
  "cost": {"change": "unchanged", "warnings": []}
}
```

```json
"summary": {
  "total": 4, "passed": 4, "failed": 0, "regressions": 0, "fixed": 0,
  "cost_increased": 0, "cost_decreased": 0, "cost_unchanged": 4,
  "difficulty": {
    "short":  {"total": 0, "passed": 0, "failed": 0, "regressions": 0},
    "medium": {"total": 4, "passed": 4, "failed": 0, "regressions": 0},
    "long":   {"total": 0, "passed": 0, "failed": 0, "regressions": 0}
  }
}
```

### Cost metrics

A scripted run has no tokens, so its cost is the two quantities that are
fully deterministic offline: `turns` (react-loop turns taken) and
`total_tool_calls` (the length of the observed `tool_sequence`,
loop-guard-intercepted calls included). Both are recorded on every scenario
entry and diffed against the same scenario's entry in the previous report:

| field | meaning |
| --- | --- |
| `cost.change` | `increased` when any metric went up; `decreased` when some metric went down and nothing went up; `unchanged` otherwise; `null` when there is no comparable baseline (new scenario, or a previous entry without cost fields) |
| `cost.warnings` | one line per metric that grew, in the form `4 turns → 7 turns` / `2 tool calls → 5 tool calls` |

The summary counts `cost_increased` / `cost_decreased` / `cost_unchanged`
scenarios separately; scenarios with `change: null` land in no bucket, so the
three counts need not add up to `total`. On the CLI, every scenario whose cost
grew gets a `COST\t<name>\t<warnings>` line right under its `PASS/FAIL` line.

Cost is **advisory only**: an increase never changes a PASS/FAIL verdict, the
`regressions` count or the exit code. The CI gate is still "any failed
scenario → exit 1"; the cost lines exist to make an efficiency regression
*visible*, and the judgement stays with you. A previous report written before
the cost fields existed is handled by skipping the missing metrics instead of
inventing a baseline, so upgrading keeps the old `last-report.json` usable.

### Difficulty breakdown

Every entry carries its scenario's `difficulty` tier, and the summary's
`difficulty` block aggregates per tier — all three tiers always present, in
`short` / `medium` / `long` order, each with `total` / `passed` / `failed` /
`regressions`. Empty tiers are emitted as zeros so the block has the same
shape on every run (stable for CI diffs, and "no coverage at this tier" stays
visible). A tier's `regressions` counts its `pass → fail` verdicts: when the
top-level `regressions` is non-zero, this block says *which* tier is
degrading. Like cost, the tier never changes a verdict.

The CLI renders the block as one `difficulty:` line after the summary line.
A full second run of the built-ins against its own baseline looks like this:

```
PASS	read-task-details	[unchanged]
PASS	denied-privileged-call	[unchanged]
PASS	multi-turn-search-converge	[unchanged]
PASS	repeat-call-loop-guard	[unchanged]
total=4 passed=4 failed=0 regressions=0 fixed=0 cost_increased=0 cost_decreased=0 cost_unchanged=4
difficulty: short total=0 passed=0 failed=0 regressions=0 | medium total=4 passed=4 failed=0 regressions=0 | long total=0 passed=0 failed=0 regressions=0
Report written to <cwd>/.nexuscli/evals/last-report.json
```

## Scope and honest boundaries

Scripted evals cover **orchestration and tool-chain regression**: the react
loop, tool sequencing, permission gating, convergence within a turn budget,
and the shape of the final answer. They do **not** measure real LLM quality —
the "model" is a fixed script, so prompt sensitivity, reasoning quality,
hallucination behavior, and token cost / latency are all out of scope (the
harness's `turns` / `total_tool_calls` cost metrics are deterministic step
counts of the wiring, not model cost). Use this harness to guard the wiring
you control; evaluate real model behavior separately.

### This round's boundary (eval-harness-p1)

What this round added — all of it hermetic (scripted fake LLM, stub tools, no
network, no real tokens):

- three trajectory assertions, `required_tools` / `max_tool_calls` /
  `max_total_tool_calls`, plus the built-in loop-guard scenario
  `04_repeat_call_loop_guard.json`;
- the optional `difficulty` tier (`short` / `medium` / `long`) with the
  per-tier `difficulty` summary block and CLI line;
- the deterministic cost metrics `turns` and `total_tool_calls` on every
  entry, diffed against the previous report into `cost.change` /
  `cost.warnings` and the three `cost_*` summary counts.

What it deliberately does **not** do:

- **no user-simulation scenarios** — the "user" is still one fixed `prompt`;
  there is no simulated multi-message user, follow-up or clarification loop;
- **no memory-recall evaluation** — scenarios do not exercise project memory
  or long-term recall, and there is no assertion over what was remembered;
- **no real-model track** — every scenario is scripted; nothing here calls a
  real LLM, scores model quality or measures tokens / latency;
- **no license change** — the harness ships under the repository's existing
  license, unchanged.

Also left out on purpose: an assertion DSL or expression language, assertions
over tool *arguments*, tier weights, and cost budget thresholds (cost only
warns, it never judges). The existing four assertions, the runner's main loop
and the `last-report.json` file name are unchanged, and a previous report
without the new fields still works as a baseline.
