# Eval — Task-Level Regression: Hermetic + Real-Model Tracks

`nexuscli eval` is a two-track evaluation harness for task-level agent
behavior. The default **hermetic track** guards the wiring you control: a
**scripted (fake) LLM client** drives the real `Agent` through JSON
scenarios; assertions then check the tool-call sequence and the final
output. A scenario can also be a **multi-round user simulation** — a
scripted user keeps talking to the *same* agent session while the
assertions watch every round — or a **memory-recall evaluation**, which
seeds a throwaway database and judges the ranking of the real recall chain
without any agent loop. Consecutive runs are diffed so a change that breaks
an agent workflow shows up as a **regression** before it reaches
production. Every report also records deterministic **cost metrics** (turns
and tool calls, warned on growth) and groups results by **difficulty tier**,
so a regression report says *which* tier degraded and whether the run got
more expensive — without either ever changing a PASS/FAIL verdict.

The optional **real-model track** (`nexuscli eval run --real`) asks what the
script cannot: how the *real* model — the one your normal config chain
selects — behaves on the real read-only builtin tools, what it really costs,
and how stable it is across repeats (pass^k) on open-ended answers
(LLM-as-judge). It writes its own report and is nightly/manual evidence by
design: real billing and provider variance make it the wrong thing to gate
CI on, so the hermetic track stays the gate and this track stays honest
about quality. See [Real-model track](#real-model-track---real).

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

The real-model track adds two flags to `eval run` (see
[Real-model track](#real-model-track---real) for the full picture):

```bash
nexuscli eval run --real             # real-model track (examples/evals/real)
nexuscli eval run --real --repeat k  # every real scenario k times, serially
nexuscli eval run --real --file path # one real-track scenario file/directory
nexuscli eval run --real --baseline  # + the bare-model ablation, comparison file
```

`--repeat` defaults to `1` and only applies to `--real` — passing it on the
hermetic track is a usage error, not a silent no-op. `--baseline` likewise only
applies to `--real`, and it cannot combine with `--repeat` (the comparison is a
single-shot fair sample on both sides — see
[Baseline ablation](#baseline-ablation---real---baseline)).

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

## How a scenario runs (hermetic track)

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

## Real-model track (`--real`)

The hermetic track proves the wiring with a fixed script; the real-model
track measures the thing the script stands in for. `nexuscli eval run
--real` builds a real LLM client through the **normal config chain**
(`load_config` → `create_llm_client`, so `~/.nexuscli/config.json`, the
project's `.nexuscli/config.json`, `.env` and the `NEXUSCLI_*` environment
all apply), drives the real builtin tools with it, and reports real token
cost. Runs are serial with one fresh client per repetition; the scenario's
`max_turns` assertion caps the react loop as usual, and a run that hits the
runner's budget or the loop guard fails with `react loop terminated early:
…` — an unconverged real run must not look like a pass with truncated
output. The track is nightly/manual evidence, deliberately **not** a CI
gate: every repetition bills real tokens and a flaky provider would fail a
gate for the wrong reason. With `--baseline` the track can also run the same
scenarios against the bare model and report the harness's gain over it —
see [Baseline ablation](#baseline-ablation---real---baseline).

### Safety model: read-only surface, fail-closed writes

Unattended runs must never write. Three stacked layers enforce it:

- **Read-only tool subset.** The registry mounts the real builtin tools
  (no MCP, no plugins), but only a curated read-only subset may *execute*:
  `read_file`, `list_dir`, `glob`, `glob_files`, `grep`, `grep_code`,
  `directory_tree`, `get_file_info`, `web_search`, `web_fetch`,
  `search_code`, `task_output`, `todo_read`.
  Every other builtin gets a computed `permissions.deny` rule — deny beats
  ask/allow, so even a user config that allows `write_file` cannot punch
  through. The subset is curated **by name**, not derived from the tools'
  `is_read_only` flag: that flag is too loose for unattended runs, because
  two flagged-read-only builtins actually mutate user state in ways that
  change behavior — `todo_write` writes `.nexuscli/todo.json` (the default
  read-only flag), and `search_memory`'s recall runs with the default
  `mark_access=True` (an `access_count` UPDATE plus a DELETE of expired
  rows on the user's real memory DB per call). Both stay outside the
  allowlist and are denied+audited like any other write.
  `load_skill` was excluded under the same reasoning until the 2026-11-02
  layered-loading change re-classified it: its content surface is read-only
  by construction (name-keyed lookup confined to the skill roots), and its
  only write is accounting — usage counters at `~/.nexuscli/usage.json`
  that nothing reads to change behavior. It is IN the allowlist, and the
  track's write invariant is stated accordingly: zero workspace writes plus
  this one declared user-dir counter write; the audit log remains the
  track's only documented write.
- **Fail-closed approval, same as `-p` without `--yes`.** There is no
  approval callback, so anything that still reaches the approval gate
  (`requires_approval` tools, `ask` rules) is denied. `hitl_mode` is pinned
  to `auto` for the track so a user's `always`/`never` cannot accidentally
  close or widen the surface.
- **Audit pinned on.** Every denied call attempt goes through the real gate
  chain and is written to the audit log (approver `permission-rule`, or
  `hitl` for the approval-gate backstop) — a write attempt is a *known*
  tool whose model-visible result is
  `Tool "write_file" was denied by permission rule: write_file`, never an
  unaudited "tool not found".

### Real scenario schema

Real scenarios are their own set (`examples/evals/real/`; `--file` accepts
one file or a directory of `*.json` from anywhere) and load through a
separate, deliberately minimal schema. Allowed keys are exactly `name`,
`prompt`, `assertions` and an optional `judge`:

```jsonc
{
  "name": "real-repo-question-answer",   // required, non-empty
  "prompt": "Read pyproject.toml ...",   // required, non-empty
  "assertions": {                        // optional: the same seven checks as
    "max_turns": 6,                      // the hermetic track, judged against
    "forbidden_tools": ["write_file"]    // the real run's observed trajectory
  },
  "judge": {                             // optional: LLM-as-judge scoring
    "rubric": [                          // required, non-empty criteria the
      "The answer states that the project's name is nexuscli"
    ],
    "threshold": 0.6,                    // required: 0..1 minimum score
    "model": "glm-5.2"                   // optional: judge model override
  }
}
```

Validation is strict with the file named in every error. The hermetic-only
keys are rejected outright as **track conflicts** (`scripted_turns`,
`tools`, `memory_eval`, `permission_deny`, `hitl_mode`, `user_turns`,
`max_rounds`) — stub tools and scripted turns are meaningless against a real
model, and the two tracks use distinct scenario dataclasses so one cannot be
fed to the other runner by accident. Other unknown keys (including
`description` and `difficulty`) fail with the supported list in the message.
`expected_tool_sequence` is accepted but **discouraged** for real models:
any reordering the model chooses fails it, so prefer `required_tools` plus
`forbidden_tools` unless the order really is the contract.

### LLM-as-judge

When a scenario declares `judge`, the run's final answer is scored against
the rubric by a judge call on the **same client factory** as the run itself.
Without `judge.model` the judge follows the run's provider/model; with it,
the factory builds the judge a client with just the model swapped. The judge
is prompted for a strict two-line reply that parses deterministically:

```
SCORE: 0.8
REASON: one short sentence citing the rubric evidence
```

- `score >= threshold` passes the check; below it the run FAILS with
  `judge score 0.20 is below threshold 0.60: <explanation>` — score and
  evidence travel together in the failure reason.
- An unparsable reply (no `SCORE:` line) scores `0.0` **fail-closed**, with
  the raw excerpt quoted in the explanation, so a confused judge model can
  never silently pass a run.
- The judge call's own token usage is folded into the run's usage total, so
  the report's cost includes judging. (The cost figure is priced with the
  run's own model profile; token counts stay exact even when
  `judge.model` points elsewhere.)
- The judge evaluator is injectable — a plain callable seam, the same
  pattern as the fake LLM — which is how the track's judge behavior is
  tested with zero network.

### pass^k, real cost, and the report

`--repeat k` runs every scenario k times serially (default `1`, minimum 1).
The report keeps **every** repetition and adds the stability and cost
aggregation. It is written to
`<cwd>/.nexuscli/evals/last-real-report.json` — never to the hermetic
baseline `last-report.json` — and carries no comparison block on purpose:
nightly real-track comparisons are read by a human, not diffed by the tool.

```jsonc
// one scenario entry
{
  "name": "real-repo-question-answer",
  "source": "examples/evals/real/02_repo_question_answer.json",
  "pass_k": {                              // pass^k verdict for the scenario
    "repeat": 3, "passed_runs": 3, "failed_runs": 0, "all_pass": true
  },
  "cost": {
    "runs": 3,
    "total_usage": {                       // cache/reasoning splits preserved
      "input_tokens": 300, "output_tokens": 90, "cache_hit_tokens": 120,
      "cache_miss_tokens": 180, "reasoning_tokens": 24, "total_tokens": 390
    },
    "avg_usage": {"input_tokens": 100.0, "output_tokens": 30.0},
    "total_cost": {"usd": {"total_cost": 0.0214}}   // summed per currency
  },
  "runs": [
    {
      "index": 1,                          // the repetition number
      "status": "pass",
      "failure_reasons": [],
      "tool_sequence": ["read_file"],
      "turns": 2,
      "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130},
      "cost": {"usd": {"total_cost": 0.0071}},
      "judge": {"score": 0.9, "threshold": 0.6, "passed": true,
                 "explanation": "names nexuscli from pyproject.toml",
                 "model": null}            // null = judge followed the run model
    }
    // ... one entry per repetition, nothing dropped
  ]
}
```

```jsonc
"summary": {
  "total_runs": 6, "passed": 6, "failed": 0,
  "scenarios_total": 2, "scenarios_all_pass": 2, "all_pass_rate": 1.0,
  "total_usage": {"input_tokens": 600, "output_tokens": 180, "total_tokens": 780},
  "total_cost": {"usd": {"total_cost": 0.0428}}
}
```

`pass_k.all_pass` is the pass^k verdict: a scenario counts toward pass^k
only when **all** of its repeats passed — the metric exists to surface
`pass → fail` flakiness that a single run hides. The CLI prints one
`PASS/FAIL <name> [run <i>/<k>]` line per repetition (FAIL lines carry the
reasons), then a `total_runs=… passed=… failed=…` summary line, and exits
`1` on any failed run.

### Baseline ablation (`--real --baseline`)

`--baseline` turns the real track into a two-arm double run: after the agent
arm — unchanged, the report below is still exactly what a plain `--real` run
writes — the same scenario list runs against the **bare model**, and the
harness-gain comparison lands in its own file,
`<cwd>/.nexuscli/evals/last-real-comparison.json`:

```bash
nexuscli eval run --real --baseline                              # examples/evals/real
nexuscli eval run --real --baseline --file examples/evals/suite  # the 30-scenario suite
```

The ablation answers the question a single agent score cannot: *how much of
this score is the harness, and how much is the model?* The bare model is the
same-model lower bound, so `agent_pass − baseline_pass` is the harness's net
gain.

**The bare arm.** The same LLM through the same config chain (same
provider/model — the gain must be attributable to the harness, not to a model
swap), as **one direct chat completion**: an empty tool list, no skill
candidates, no agent system prompt — just
`You are a helpful assistant. Answer directly.` That minimal prompt is the
arm's entire system message, chosen to say nothing the model could leverage;
it is recorded verbatim in the comparison document
(`baseline.system_prompt`) so readers can see exactly what the bare arm got.
Scenarios whose questions need tools are expected to fail here — that is the
gap the ablation measures, not a defect.

**Assertion subset.** Only the checks that depend on the final text carry
over to the bare arm:

| check | bare arm |
| --- | --- |
| `final_contains` | judged, same failure wording as the agent arm |
| `judge` | reused verbatim from the scenario — same rubric, same threshold, same `judge.model` override; one ruler for both arms (the judge call's tokens land in that arm's total) |
| `expected_tool_sequence` / `forbidden_tools` / `required_tools` / `max_tool_calls` / `max_total_tool_calls` | **N/A** — the bare model gets no tools, so tool-trajectory assertions are meaningless |
| `max_turns` | **N/A** — one completion has no turn budget |

The document states the N/A set once, up front (`baseline.assertions` with
`evaluated` / `not_applicable`), instead of marking every scenario entry.

**Single-shot on purpose, and what it costs.** Model output varies between
calls, so caching an earlier agent side would compare against a different
sample; both arms run serially against the same client factory, one fresh
client per run. That is also why `--repeat` cannot combine with `--baseline`
— the combination is a usage error, not a silent reinterpretation. Cost
consequence: a `--baseline` double run bills roughly **2× a full-suite run**
(agent arm + bare arm, judge calls included in each arm's tokens). The
credential/provider check fires before any scenario starts, exactly like
`--real` (`No key, no run` below applies unchanged), and the exit code still
reflects the **agent arm only** — the bare model failing tool-required
questions is the ablation's expected signal, reported in the comparison, not
a command failure.

**Comparison file fields.** `last-real-report.json` keeps its exact schema
and stays agent-side only (its consumers — `/skill propose`, the post-landing
diff — never see baseline data); the comparison is a third file that a plain
`--real` run never writes. Per scenario:

| field | meaning |
| --- | --- |
| `id` | scenario name |
| `agent.status` / `baseline.status` | `pass` / `fail` of that arm |
| `agent.tokens` / `baseline.tokens` | that arm's `total_tokens` (judge usage included) |
| `delta.pass_delta` | agent − baseline: `+1` the harness passed where the bare model failed, `-1` the reverse |
| `delta.token_delta` | agent − baseline tokens: the harness's extra token cost for this scenario |

Summary:

| field | meaning |
| --- | --- |
| `scenarios_total` | paired scenarios (both arms run the same list) |
| `agent_pass` / `baseline_pass` | passed-count per arm |
| `pass_delta` | `agent_pass − baseline_pass` — the headline harness gain |
| `agent_pass_rate` / `baseline_pass_rate` | pass rates per arm, rounded to 4 decimals |
| `quadrants` | `both_pass` / `agent_only` / `baseline_only` / `both_fail` counts |
| `total_tokens` | `{agent, baseline}` run-wide token totals |

**Reading the quadrants.** `agent_only` is the harness's net wins —
questions the bare model could not answer (usually because they need tools)
but the agent did; together with `pass_delta` this is the number the
ablation exists for. `baseline_only` is the harness's net losses — rare, and
worth a look when non-zero (sampling variance or an agent-side failure
mode), not a defect by itself. `both_fail` makes the genuinely-hard tail
visible: bare-model misses on tool-required questions are expected to
concentrate here. `both_pass` is agreement — no differentiation. The CLI
prints one line per scenario plus a summary line (numbers illustrative):

```
cqa-sh-pyproject-name-license	agent=PASS baseline=FAIL tokens=11822/931 (pass_delta=+1, token_delta=+10891)
...
agent_pass=24/30 baseline_pass=17/30 pass_delta=+7 | quadrants: both_pass=16 agent_only=8 baseline_only=1 both_fail=5
Comparison written to <cwd>/.nexuscli/evals/last-real-comparison.json
```

### No key, no run

The credential and provider are validated **before** any scenario starts, so
a missing key can never produce a partial run or a silent skip. Without an
API key the command exits `1` with an actionable message naming the levers:
`NEXUSCLI_API_KEY` (or the provider's variable such as `DEEPSEEK_API_KEY` /
`GLM_API_KEY`), or `llm.api_key` in `~/.nexuscli/config.json` / the project's
`.nexuscli/config.json`. An unknown provider without a `base_url` fails the
same way. Two built-in real scenarios ship in `examples/evals/real/`:
`01_readonly_converge.json` (count the workspace root's entries within a
turn budget, write attempts forbidden) and `02_repo_question_answer.json`
(read `pyproject.toml`, name the project, judge-scored against a rubric) —
run them from a checkout with `uv run nexuscli eval run --real` and expect a
small, visible token bill in the report.

### Evaluation suite (`examples/evals/suite`)

The two scenarios above are wiring smoke. The evaluation suite is the real
track's actual measurement instrument: a **self-built vertical dataset** for
the workload this CLI exists for — repository operations and code
comprehension inside a terminal/CLI agent. It is a fixed grid of
**5 categories × 3 difficulties × 2 scenarios = 30 scenarios**
(`codebase-qa`, `repo-facts`, `diagnosis`, `skill-compliance`,
`extraction` × `short` / `medium` / `long`), and every prompt is written in
Chinese — the language the CLI's target users operate in.

Layout: the 30 scenario files sit **flat** in the suite directory (one JSON
per scenario; the file-name prefix encodes category and difficulty, e.g.
`cqa-sh-…`, `dgn-lg-…`), the frozen question data lives under `fixtures/`
(one subdirectory per category), and the dataset's catalog is
`meta/manifest.json`. `tests/test_eval_suite.py` mechanically pins this
shape (12 checks: manifest↔file alignment, grid fullness, difficulty→
`max_turns` mapping, answer-anchor leak guard, Chinese prompts, assertion
mirroring).

**Layout constraint — keep the manifest inside `meta/`.** Both directory
loaders glob `*.json` **non-recursively**, so the manifest is only safe in
a subdirectory. `examples/evals/` is the hermetic track's built-in scenario
directory (resolved by `builtin_eval_dir()` in
`src/nexuscli/eval/loader.py`), so `eval list` / `eval run` load every
top-level `*.json` as a hermetic scenario: a manifest at either top level
gets loaded as a scenario and fails loudly (observed with `eval list` on a
top-level manifest: `unknown scenario key(s) ['categories', 'language',
'scenarios']` — `EvalSchemaError`, exit 1), and the suite directory itself
goes through `load_real_eval_scenarios`, which would swallow a top-level
manifest the same way. `suite/meta/`, like `suite/fixtures/`, is outside
both globs.

The manifest records one entry per scenario — the dataset's paper trail:

| field | meaning |
| --- | --- |
| `id` | scenario name, 1:1 with the flat JSON file stems |
| `category` | one of the five categories, matching the file-name prefix |
| `difficulty` | `short` / `medium` / `long`; also the `max_turns` budget (3 / 5 / 8) |
| `summary` | one-sentence statement of what the scenario asks |
| `source_rationale` | why the scenario made the cut — the selection criteria kept on record |
| `anchor_type` | what the expected answer is anchored in: `fixture` (frozen data file), `repo-stable` (a fact read-verified in this repository) or `judge` (open-ended, rubric-scored) |
| `assertions` | the scenario's assertion summary (`forbidden`, `has_judge`, `max_turns`, `required_tools`, `final_contains`, `judge_threshold`), mirrored from the scenario file |
| `calibration` | backfilled after calibrated runs: `status`, `tokens`, `turns` (plus a `note` for observations deliberately kept) |

Run the whole suite as a directory target:

```bash
nexuscli eval run --real --file examples/evals/suite
```

Calibration convention: a **FAIL is a normal component of the difficulty
distribution** — a suite designed to probe difficulty must keep the misses
that are genuinely hard. Triage therefore only separates two cases: a
**scenario defect** (wrong anchor, or an answer that cannot be derived from
the declared anchor — fix the scenario and recalibrate) versus **genuine
difficulty** (keep the scenario as designed and record the observation via
`calibration.note`).

### Stability baseline (`--repeat k`)

The suite's stability baseline is one command:

```bash
nexuscli eval run --real --file examples/evals/suite --repeat 3
```

`--repeat k` reruns every scenario k times serially, and the pass^k verdict
credits a scenario only when **all** k runs passed (see
[pass^k, real cost, and the report](#passk-real-cost-and-the-report)) — the
aggregation exists precisely because single shots swing. Measured on the real
gateway (3 × 30 = 90 runs): single-shot passes of the same suite have landed
anywhere in **19–24 / 30** across three samples (calibration 24/30, ablation
agent arm 19/30), while pass^3 stabilizes at **20/30** (`all_pass_rate`
0.6667) — compare nightly runs against the 20, not against any single-shot
figure.

Per-scenario outcomes fall into three classes, and the suite's
`meta/manifest.json` records each scenario's outcome in its `stability`
field:

- **rock-pass (20 scenarios, 3/3)** — passes every repeat; deterministic
  under sampling.
- **rock-fail (5 scenarios, 0/3)** — `cqa-lg-memory-recall-signal-chain`,
  `cqa-lg-load-skill-layered-chain`, `rfa-md-01-todo-census`,
  `ext-lg-inventory-outdoor-threewh`,
  `ext-lg-inventory-kitchen-available`. All five are **guard-compliance
  signals, not quality regressions**: the model attempts a `bash` call, the
  track's read-only guard denies it, and the answer cannot be produced
  without the blocked surface — the deny chain working as designed is what
  these scenarios observe.
- **sway (5 scenarios, 1–2/3)** — `cqa-md-vision-capability-tristate`
  (1/3), `rfa-sh-02-license` (1/3), `skc-sh-02-rule-demo-conflict` (2/3),
  `skc-lg-01-incident-dispatch` (2/3), `skc-lg-02-freeze-abort` (2/3).
  These five are what a nightly run should watch first: a rock decaying
  into sway, or a sway scenario dropping to 0/3, is the earliest stability
  signal.

One known noise source is by design: an unparsable or empty judge reply
scores 0.0 **fail-closed** (see [LLM-as-judge](#llm-as-judge)) — in the
baseline measurement `skc-sh-02-rule-demo-conflict`'s third run failed
exactly this way (the judge returned an empty string on a gateway hiccup),
so that FAIL is the judging semantics working, not an agent regression.

Cost: `--repeat 3` bills roughly **3× a full-suite run** — on the order of
**2M tokens** per measurement (judge calls included).

## Scope and honest boundaries

The two tracks split the question "how is the agent doing?" by what can be
deterministic.

**Hermetic track — guard the wiring you control.** Scripted evals cover three
kinds of wiring regression: **orchestration and tool-chain behavior** (the
react loop, tool sequencing, permission gating, convergence within a turn
budget, the shape of the final answer), **user-adversarial behavior** (a
scripted user keeps a conversation going — e.g. insisting on a refund —
while the assertions watch every round for policy compliance), and
**memory-recall quality** (multi-signal ranking over seeded rows, the
standing regression gate for the recall chain). The "model" is a fixed
script, so this track never measures prompt sensitivity, reasoning quality,
hallucination behavior or latency — and its `turns` / `total_tool_calls`
cost metrics are deterministic step counts of the wiring, not model cost.
It stays the CI gate.

**Real-model track — look at quality, stability and cost.** The `--real`
track runs the configured provider/model on the real read-only tools and
reports what a script cannot stand in for: real token cost with
cache/reasoning splits, pass^k stability across repeats, and open-ended
answer quality via the rubric-scored judge. It is nightly/manual evidence,
not a gate: real billing and provider variance make a hard exit-code gate
wrong here, and the report is meant to be read, not diffed.

Still deliberately out of scope for both tracks: latency measurement, judge
inter-model consistency (one rubric + one threshold pins one judge's
behavior; agreement between different judge models is not asserted),
per-round assertions (trajectory assertions see one cumulative run, memory
assertions one final ranking), an assertion DSL or expression language,
tier weights, and cost budget circuit-breakers (hermetic cost only warns;
real cost is reported, never budgeted). The real track additionally does no
regression diffing (each report stands alone), runs repeats serially rather
than concurrently, and does not retry failed runs.

### Round boundaries

What each round added — the first two rounds all hermetic (scripted fake
LLM or seeded temp-DB memory, stub tools, no network, no real tokens); the
third is the deliberate exception:

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
- **add-real-model-eval-track** — the real-model track (`eval run --real`
  / `--repeat`): its own minimal scenario schema (hermetic-only keys
  rejected as track conflicts, file named in every error), the curated
  read-only builtin subset with computed deny rules + fail-closed approval
  (`-p` semantics) + pinned audit, real usage/cost collection from the
  stream (judge calls included), the LLM-as-judge (same client factory,
  per-scenario `judge.model` override, fail-closed `SCORE:` parsing,
  injectable fake judge), pass^k plus per-scenario cost aggregation in the
  independent `last-real-report.json`, and the built-in real scenarios
  `01_readonly_converge.json` / `02_repo_question_answer.json`.

What the harness still deliberately does **not** do:

- **no real LLM on the hermetic track** — every hermetic scenario is
  scripted or seeded; the hermetic track still never calls a real LLM, and
  its baseline (`last-report.json`) is never touched by a `--real` run.
  Real-model evaluation lives on the explicit `--real` track above, which
  is nightly/manual and never a CI gate;
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
