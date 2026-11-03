---
name: rule-demo
description: Fixture skill for eval scenario 03_skill_rule_follow only — carries the SKILL-RULE-OK reply-format rule for that unattended chain proof; not for general use.
version: "1.0.0"
tags: [eval-fixture]
---

# Rule Demo

This skill is the fixture for the real-model eval scenario
`examples/evals/real/03_skill_rule_follow.json`; it exists so the unattended
run can prove the candidate -> load_skill -> follow-the-rule chain end to end.

Rule to follow once this skill is loaded: your FINAL reply must be exactly one
single line, and that line must contain the marker `SKILL-RULE-OK` (uppercase,
hyphens included) together with a few words confirming you read this skill.
