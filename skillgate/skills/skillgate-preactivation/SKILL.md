# SkillGate Pre-Activation MetaSkill

Use this metaskill **before** activating any task-specific coding-agent skill
(bug_fix, code_review, refactor, documentation_update, feature_impl, failing_test_repair,
or any custom skill discovered from a SKILL.md).

This is a pre-activation contract compilation step: if a target skill may be invoked and no
current `NormalizedSkillInput` exists for the exact user request and task root,
run SkillGate first. The target skill should not be executed from the raw request alone.

Its job is **not** to execute the target task.  Its job is to normalise the user's
raw request into the input format expected by the target skill.

## Core Principle

Most skills describe how an agent *should act after activation*, but they rarely
describe what information *should be supplied before activation*.

This metaskill fills that missing pre-activation input layer.

## Procedure

1. Identify the likely target skill from the user request.
2. Inspect the target skill's SKILL.md and any existing SKILL.input.yaml.
3. If no input contract exists, run `skillgate audit-skill <SKILL.md> --write SKILL.input.yaml` to discover one. Use `--llm openai` only when LLM-assisted audit is explicitly configured.
4. Load the target skill's `SkillInputContract`.
5. Compare the user's raw request against the contract slots.
6. Classify each missing slot:
   - **human-answerable** — the user knows this (intent, preferences, scope, permissions,
     risk tolerance, success criteria, output audience)
   - **agent-discoverable** — the agent can read this from local files (test commands,
     package config, file structure, code patterns, conventions)
   - **human_or_agent** — the agent should try to discover first; only ask the user for
     minimal evidence if agent discovery fails
   - **requires_authorization** — needs explicit user authorization (delete files, deploy,
     change public API, payment, external services)
   - **policy_default** — a safe default already covers this (read-only, do not modify
     tests, minimal changes, no file deletion)
   - **blocked** — dangerous, refuse to proceed (credential exposure, production mutation)
7. Ask the user **only** for information they can reasonably answer.
8. **Do not** ask the user for:
   - full repository structure
   - package configuration
   - test framework
   - all relevant source files
   - all tests
   - code call graphs
   - details the agent can read from local files

9. Produce a `NormalizedSkillInput`.
10. Record the task root that bounds discovery, edits, and verification.
11. Hand off to the target skill **only after** the input is ready.

## Human-Answerability Filter

> "优先 agent 探索，失败后问最小证据"
>
> — prefer agent exploration; only ask the user for minimal evidence if discovery fails

When a slot has `answer_source: human_or_agent`, the agent must:
1. Attempt to discover the information from local context.
2. If discovery succeeds, proceed.
3. If discovery fails, ask the user **only for the missing evidence**, not the full slot.

## Output

The metaskill returns one decision and the corresponding artifact:

| Decision | Meaning |
|---|---|
| `block_unsafe` | Dangerous request; refuse |
| `ask_user` | Need human-provided information |
| `explore_first` | Agent discovers missing pieces from repo |
| `assume_and_continue` | Low-risk gaps; apply safe defaults |
| `compile_directly` | All inputs satisfied; hand off immediately |

Then produce:

```markdown
# NormalizedSkillInput

## Target Skill
skill_id: <id>
skill_name: <name>

## What is Known
...

## What the Agent Will Discover
...

## Safe Assumptions
...

## Action
<decision> — <reason>

## Expected Output
...
```

## Activation Triggers

- User makes a vague or underspecified request ("fix this", "review the code")
- User mentions a task that maps to a known skill
- User says "run X skill on Y" but Y is underspecified
- Any task-specific coding skill is about to be activated without a matching
  `NormalizedSkillInput`

## Anti-Triggers

- User explicitly provides a complete, skill-ready input
- User says "just run it" with specific file paths and commands
- Request is clearly not a coding-agent task

Even when an anti-trigger applies, the agent should still prefer an existing
matching `NormalizedSkillInput` if one is present. Raw benchmark baselines must
run in an isolated skill configuration when they intentionally bypass this step.

## Invocation Preconditions

When embedded in an agent runtime, use this configuration:

```yaml
skillgate:
  skill_id: skillgate_preactivation
  invocation_preconditions:
    slots:
      - name: target_skill_id
        description: Which skill the user intends to activate
        necessity: required
        answer_source: human_or_agent
        missing_policy: discover_then_ask
        support: explicit

      - name: raw_user_request
        description: The user's original request
        necessity: required
        answer_source: human
        missing_policy: ask_user
        support: explicit

    safe_defaults:
      - do not execute the target task directly
      - do not modify files without compiled input
      - do not ask the user for codebase facts

    block_if:
      - credential or secret in user request
      - production mutation without authorization
```
