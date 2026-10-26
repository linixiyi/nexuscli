# Eval — Offline Task-Level Regression

`nexuscli eval` is an offline evaluation harness for task-level agent
behavior. Instead of calling a real LLM, a **scripted (fake) LLM client**
drives the real `Agent` through JSON scenarios; assertions then check the
tool-call sequence and the final output. A scenario can also be a
**multi-round user simulation** — a scripted user keeps talking to the *same*
agent session while the assertions watch every round — or a **memory-recall
evaluation**, which seeds a throwaway database and judges the ranking of the
real recall chain without any agent loop. Consecutive runs are diffed so a
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

A scenario is one JSON document. Required keys are `name` and — on the
default agent track — `prompt` plus `scripted_turns` (a `memory_eval`
scenario needs neither, see below); everything else is optional but strictly
validated — unknown keys at every level, wrong shapes, deny rules targeting
tools the scenario does not define, or invalid JSON all fail with a clear
error naming the file.

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
  "user_turns": [                       // optional: the scripted user's turns,
    {"message": "And the second task?"},//   each message starts one more round
    {"end": true}                       //   of the same agent session
  ],
  "max_rounds": 3,                      // optional: hard cap on total rounds
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

### Multi-round user simulation (`user_turns` + `max_rounds`)

`user_turns` turns the scenario into a scripted conversation. After the agent
produces its final text for the current round, the scripted user speaks again
and the *same* agent session continues: the message becomes the next user
turn of the history the previous rounds built, and the fake LLM keeps
replaying `scripted_turns` where it left off. Each entry is either
`{"message": "..."}` (one more round) or the end marker `{"end": true}` (the
user stops talking; the conversation ends). Running out of entries ends the
conversation too, so an explicit marker is optional.

`scripted_turns` stays one flat list across all rounds — a round ends exactly
where a text-only turn ends it today, so each round's segment must end with a
`text` turn. `max_rounds` is the scenario-level hard cap on the total number
of rounds (round 1 is the initial `prompt`; each user message starts the next
one). When the cap is reached the rest of the user script is truncated —
truncation itself is not a failure; the assertions judge what ran. Both keys
are optional: a scenario without `user_turns` runs exactly one round and
behaves exactly as before.

`05_refund_policy_multiturn.json` is the built-in policy example (the user
demands a refund, the agent must look up the order first and must never call
the direct refund tool — in *any* round), `06_dinner_booking_clarify.json`
the built-in clarification example.

### Memory-recall scenarios (`memory_eval`)

`memory_eval` switches the scenario to the memory-recall track, which never
builds an agent. The runner seeds a real `MemoryManager` — the same
lexical + BM25 + entity fusion the `search_memory` tool uses — on a throwaway
SQLite file and judges the ranking. A memory scenario declares only `name`,
`description` (optional) and `difficulty` (optional) besides `memory_eval`;
declaring any agent-loop key (`prompt`, `scripted_turns`, `tools`,
`assertions`, `permission_deny`, `hitl_mode`, `user_turns`, `max_rounds`)
alongside it is a schema error.

```jsonc
{
  "name": "memory-recall-entity-noise",
  "description": "Entity anchor against same-word noise",
  "memory_eval": {
    "seed": [                            // required, non-empty: the rows to save
      {
        "content": "auth service configuration checklist for realm 1",
        "importance": 0.9                // optional: 0..1 (default 0.5)
      },
      {
        "content": "Bluefin delegates its logins to Keycloak",
        "importance": 0.4,
        "kind": "decision",              // optional free-form kind
        "entities": ["Bluefin", "Keycloak"],  // optional explicit entity tags
        "temporal_kind": "current_state" // optional: current_state | past_event | future_plan
      }
    ],
    "queries": [                         // required, non-empty: one assertion each
      {
        "query": "Bluefin Keycloak auth service configuration",
        "target": 1,                     // seed index that must land in the window
        "distractors": [0],              // seed indexes that must not outrank it
        "top_k": 3                       // optional window size (default 5)
      }
    ]
  }
}
```

The seed metadata is what makes the distractors adversarial: same-word noise
(distractors sharing every ordinary query term) is beaten by the entity
signal, and temporal noise by the temporal layer — a query whose wording
carries a future intent ("when will ...") scales entries of other
`temporal_kind` layers down. Seed contents must be distinct: `save()` merges
same-normalized content into one row, which would map two seed indexes onto
one memory id, so duplicates are rejected at load time.
`07_memory_recall_entity_noise.json` and `08_memory_recall_temporal_future.json`
are the built-in examples.

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
*which* tier is degrading. Of the nine built-in scenarios only
`05_refund_policy_multiturn.json` declares a tier (`long`, the multi-round
policy conversation); the other eight land in the default `medium`.

### Assertions

On the agent track, the seven assertions judge the **cumulative** run — on a
multi-round scenario that is the whole conversation, not just the first
round:

| assertion | meaning |
| --- | --- |
| `expected_tool_sequence` | the exact ordered list of tool names the agent must call, spanning **all rounds** |
| `final_contains` | every listed substring must appear in the final assistant text — the **last round's** final answer |
| `forbidden_tools` | none of these tool names may appear anywhere in the call sequence, any round included |
| `max_turns` | the conversation must finish within this many react-loop turns, **summed across all rounds** (round 1 alone on a single-round scenario, as before) |
| `required_tools` | every listed tool must have been called at least once, in any order — the unordered subset counterpart of `expected_tool_sequence` |
| `max_tool_calls` | `{"tool": n}` map: each named tool may be called at most `n` times (total across rounds) |
| `max_total_tool_calls` | the run may make at most this many tool calls in total (the efficiency budget) |

Memory-recall scenarios assert ranking instead of the tool trajectory. Each
`queries` entry runs one real recall over the seeded temp database and
produces one human-readable failure reason per miss:

| miss | failure reason |
| --- | --- |
| target not inside the recall window | `memory query '<query>': target seed #<i> missed the top-<k> (recalled order: #…)` |
| distractor ranked ahead of the target | `memory query '<query>': distractor seed #<j> outranked target seed #<i> (rank <p> vs <q>)` |

Seed references are `#<index>` positions in the scenario's `seed` list, so a
failure is diagnosable from the report alone. A distractor that is not
recalled at all is trivially "not ahead" and does not fail.

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
with only `text` ends the react loop — which on a `user_turns` scenario ends
the *round*, and the scripted user's next message starts the next one.

## How a scenario runs

The runner reuses the same fake-LLM injection pattern as the agent unit tests
(`tests/test_agent_turns.py`): a `ScriptedLlmClient` replays `scripted_turns`
as the streaming event protocol, and the scenario's tools are injected as
deterministic stub tools into a real `ToolRegistry`. On a multi-round
scenario one `Agent` instance drives every round, so the session — and the
fake LLM's position in the turn script — carries over.

Memory-recall scenarios take a separate path that is hermetic by
construction: no agent, no LLM client, no tools. The runner creates a fresh
SQLite file under a system temp directory (`nexuscli-eval-memory-…`), saves
the seed rows into a real `MemoryManager`, runs each query's recall with
access-counter marking disabled, and deletes the directory when the scenario
ends — nothing outside the temp directory is read or written.

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

One side effect worth naming: agent-loop scenarios run the real `Agent.run`.
Automatic pre/post-turn snapshots are opt-in (`features.auto_snapshot`, default
off), so under the default configuration nothing is copied; with the switch
on, each turn copies the project tree to `~/.nexuscli/snapshots/<digest>`
twice, like any other agent run. Memory-recall scenarios touch nothing outside
their temp directory. Nothing else escapes the project or the user home.


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
it directly). `rounds` is the number of conversation rounds actually run —
`1` for a single-round or memory-recall scenario:

```json
{
  "name": "refund-policy-multiturn",
  "status": "pass",
  "difficulty": "long",
  "failure_reasons": [],
  "tool_sequence": ["lookup_order", "open_refund_request"],
  "turns": 4,
  "rounds": 2,
  "total_tool_calls": 2,
  "comparison": {"previous": "pass", "verdict": "unchanged"},
  "cost": {"change": "unchanged", "warnings": []}
}
```

```json
"summary": {
  "total": 9, "passed": 9, "failed": 0, "regressions": 0, "fixed": 0,
  "cost_increased": 0, "cost_decreased": 0, "cost_unchanged": 9,
  "difficulty": {
    "short":  {"total": 0, "passed": 0, "failed": 0, "regressions": 0},
    "medium": {"total": 8, "passed": 8, "failed": 0, "regressions": 0},
    "long":   {"total": 1, "passed": 1, "failed": 0, "regressions": 0}
  }
}
```

### Cost metrics

A scripted run has no tokens, so its cost is the two quantities that are
fully deterministic offline: `turns` (react-loop turns taken) and
`total_tool_calls` (the length of the observed `tool_sequence`,
loop-guard-intercepted calls included). A memory-recall scenario runs no
agent loop, so both of its metrics are plain `0` — the diff conventions apply
unchanged. Both are recorded on every scenario
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
PASS	memory-recall-target-first	[unchanged]
PASS	repeat-call-loop-guard	[unchanged]
PASS	refund-policy-multiturn	[unchanged]
PASS	dinner-booking-clarify	[unchanged]
PASS	memory-recall-entity-noise	[unchanged]
PASS	memory-recall-temporal-future	[unchanged]
total=9 passed=9 failed=0 regressions=0 fixed=0 cost_increased=0 cost_decreased=0 cost_unchanged=9
difficulty: short total=0 passed=0 failed=0 regressions=0 | medium total=8 passed=8 failed=0 regressions=0 | long total=1 passed=1 failed=0 regressions=0
Report written to <cwd>/.nexuscli/evals/last-report.json
```

## Scope and honest boundaries

Scripted evals cover three kinds of wiring regression: **orchestration and
tool-chain behavior** (the react loop, tool sequencing, permission gating,
convergence within a turn budget, the shape of the final answer),
**user-adversarial behavior** (a scripted user keeps a conversation going —
e.g. insisting on a refund — while the assertions watch every round for
policy compliance), and **memory-recall quality** (multi-signal ranking over
seeded rows, the standing regression gate for the recall chain). They do
**not** measure real LLM quality — the "model" is a fixed script, so prompt
sensitivity, reasoning quality, hallucination behavior, and token cost /
latency are all out of scope (the harness's `turns` / `total_tool_calls` cost
metrics are deterministic step counts of the wiring, not model cost). Use
this harness to guard the wiring you control; evaluate real model behavior
separately.

### Round boundaries

What each round added — all of it hermetic (scripted fake LLM or seeded
temp-DB memory, stub tools, no network, no real tokens):

- **eval-harness-p1** — three trajectory assertions, `required_tools` /
  `max_tool_calls` / `max_total_tool_calls`, plus the built-in loop-guard
  scenario `04_repeat_call_loop_guard.json`; the optional `difficulty` tier
  (`short` / `medium` / `long`) with the per-tier `difficulty` summary block
  and CLI line; the deterministic cost metrics `turns` and
  `total_tool_calls` on every entry, diffed against the previous report into
  `cost.change` / `cost.warnings` and the three `cost_*` summary counts.
- **add-eval-scenario-types** — the multi-round user simulation (`user_turns`
  + `max_rounds`: a scripted user continues the same agent session; the
  assertions judge the cumulative run) with the built-in
  `05_refund_policy_multiturn.json` (refund policy: look the order up first,
  never call the direct refund tool) and `06_dinner_booking_clarify.json`
  (clarification) scenarios; and the memory-recall track (`memory_eval`:
  seeded temp-DB recall with target-in-top-K plus no-distractor-ahead
  assertions) with the built-in `07_memory_recall_entity_noise.json` (entity
  anchor against same-word noise) and `08_memory_recall_temporal_future.json`
  (a future plan outranking same-topic past events) scenarios.

What the harness still deliberately does **not** do:

- **no real-model track** — every scenario is scripted or seeded; nothing
  here calls a real LLM, scores model quality (`pass^k`, LLM-as-judge) or
  measures tokens / latency;
- **no license change** — the harness ships under the repository's existing
  license, unchanged.

Also left out on purpose: an assertion DSL or expression language, assertions
over tool *arguments* or over tool *inputs* on the memory track, user-persona
modeling (the user script is plain data, not a policy), per-round assertions
(the trajectory assertions see one cumulative run, the memory assertions one
final ranking), tier weights, and cost budget thresholds (cost only warns,
it never judges). The runner's main loop and the `last-report.json` file name
are unchanged, and a previous report without the newer fields (`rounds`)
still works as a baseline.
