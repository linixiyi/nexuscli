# Eval — Offline Task-Level Regression

`nexuscli eval` is an offline evaluation harness for task-level agent
behavior. Instead of calling a real LLM, a **scripted (fake) LLM client**
drives the real `Agent` through JSON scenarios; assertions then check the
tool-call sequence and the final output. Consecutive runs are diffed so a
change that breaks an agent workflow shows up as a **regression** before it
reaches production.

## Commands

```bash
nexuscli eval list              # list built-in scenarios (examples/evals)
nexuscli eval run               # run every built-in scenario (same as --all)
nexuscli eval run --all         # explicit form of the default
nexuscli eval run --file path   # one scenario file, or a directory of *.json
```

`eval run` prints one `PASS/FAIL` line per scenario plus a summary, writes the
report to `<cwd>/.nexuscli/evals/last-report.json`, and exits `1` when any
scenario failed (CI-friendly).

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
  "assertions": {                       // optional; four supported checks
    "expected_tool_sequence": ["list_tasks"],
    "final_contains": ["fix login"],
    "forbidden_tools": ["bash"],
    "max_turns": 3
  }
}
```

### Assertions

| assertion | meaning |
| --- | --- |
| `expected_tool_sequence` | the exact ordered list of tool names the agent must call |
| `final_contains` | every listed substring must appear in the final assistant text |
| `forbidden_tools` | none of these tool names may appear anywhere in the call sequence |
| `max_turns` | the react loop must finish within this many turns |

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

One known side effect, disclosed rather than hidden: scenarios run the real
`Agent.run`, whose pre/post-turn snapshot step still executes — each turn
copies the project tree to `~/.nexuscli/snapshots/<digest>` (twice per turn,
like any other agent run). Nothing else escapes the project or the user home.


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

## Scope and honest boundaries

Scripted evals cover **orchestration and tool-chain regression**: the react
loop, tool sequencing, permission gating, convergence within a turn budget,
and the shape of the final answer. They do **not** measure real LLM quality —
the "model" is a fixed script, so prompt sensitivity, reasoning quality,
hallucination behavior, and cost/latency are all out of scope. Use this
harness to guard the wiring you control; evaluate real model behavior
separately.
