# SkillGate

**Pre-activation metaskill and input compiler for agent skills.**

SkillGate is a metaskill/plugin-style layer to run before a task-specific skill. It audits a skill description, writes a reusable `SkillInputContract` reference file, and interactively compiles vague user requests into a `NormalizedSkillInput` that is ready for the target skill. It is a local CLI library plus a bundled metaskill prompt; it does not execute tasks or call coding agents.

## Core Flow

```
Target SKILL.md -> SkillGate -> SKILL.input.yaml   (canonical SkillInputContract v2)
User request + SKILL.input.yaml -> SkillGate -> NormalizedSkillInput
```

There is **one** canonical contract format. The LLM auditor, the rules-based
auditor, and the CLI all read/write the same `SkillInputContract` v2 shape.
On load, every contract is passed through `normalize_contract()` which
migrates v1/legacy/partial dicts to canonical v2, then validates — so the
`audit-skill --write` → `compile --skill-file` loop is a faithful roundtrip.

Every compilation emits a `skillgate_compilation_completed` event in
`trace.jsonl` carrying `contract_hash`, `request_hash`, `task_root_hash`,
and `decision`, so an experiment collector can verify SkillGate actually ran.

SkillGate separates:

- `human_askable`: intent, permission, acceptance criteria
- `agent_discoverable`: repository facts such as test commands and project structure
- `safe_assumption`: conservative defaults such as no file deletion
- `requires_authorization`: actions that need explicit permission
- `blocked`: unsafe requests that should not proceed

### Five constraint classes (v2 contract)

A `SkillInputContract` v2 distinguishes five constraint types with **distinct runtime semantics** — they are not all folded into one `block_if`:

| Class | Runtime semantic |
|---|---|
| `safety_blocks` | A dangerous *request* blocks immediately (credential access, production mutation). Evaluated by request-text match. |
| `authorization_requirements` | The action requires explicit user permission; routes to `ask_user` before proceeding. |
| `execution_constraints` | Always-active invariants propagated into the `NormalizedSkillInput` (e.g. "do not modify tests"). Never block by themselves. |
| `forbidden_actions` | Always propagated downstream. Block **only** if the user explicitly requests the forbidden action — not on a mere keyword mention. |
| `stop_conditions` | Evaluated against *slot state* (not keyword search): halt if a critical required slot is unanswerable and the request has no recoverable signal. |

Each slot also carries `missing_policy` (`ask_user` / `discover_then_ask` / `discover_only` / `assume_default` / `block`), which controls routing when a slot is unfilled, and an `evidence_status` (`verified` / `partially_verified` / `unverified`). Unverified safety/authorization slots are **fail-closed**: they are extracted to `low_confidence_slots` and never control execution.

## Install

```bash
uv sync --extra dev
```

## Quickstart

Audit a skill:

```bash
uv run python -m skillgate audit-skill examples/real_skills/bug_fix/SKILL.md --json
```

Write the discovered contract:

```bash
uv run python -m skillgate audit-skill examples/real_skills/bug_fix/SKILL.md --write /tmp/SKILL.input.yaml
```

Compile a request against a built-in skill:

```bash
uv run python -m skillgate compile --skill bug_fix "这个报错帮我修一下"
```

Compile using a skill file or generated input contract:

```bash
uv run python -m skillgate compile --skill-file examples/real_skills/bug_fix/SKILL.md "这个报错帮我修一下"
uv run python -m skillgate compile --skill-file /tmp/SKILL.input.yaml "这个报错帮我修一下"
```

Export core schemas:

```bash
uv run python -m skillgate schemas --out exported-schemas
```

Use the bundled metaskill instructions from:

```text
skills/skillgate-preactivation/SKILL.md
```

## Commands

| Command | Purpose |
|---|---|
| `audit-skill <path>` | Discover a reusable `SkillInputContract` from `SKILL.md` |
| `compile --skill <id>` | Compile a request against a built-in skill contract |
| `compile --skill-file <path>` | Compile with a `SKILL.md` or `SKILL.input.yaml` contract |
| `inspect` | Inspect allowlisted local context |
| `explain <decision.json>` | Explain a compilation decision |
| `compile -i <request>` | Write clarification questions when needed |
| `answer <run_dir>` | Record one clarification answer |
| `answer-batch <run_dir>` | Record multiple clarification answers |
| `recompile <run_dir>` | Recompile after clarification answers |
| `schemas` | Export or check core JSON Schemas |

## Public Artifacts

`audit-skill --write` creates a reusable reference file:

```
SKILL.input.yaml
```

`compile` writes a run directory:

```
.skillgate/runs/<run_id>/
├── request.md
├── context_manifest.json
├── skill_contract.json
├── normalized_skill_input.json
├── normalized_skill_input.md
├── decision.json
└── trace.jsonl
```

## Built-in Skills

| Skill ID | Task |
|---|---|
| `bug_fix` | Diagnose and repair a reported defect |
| `failing_test_repair` | Repair a failing test |
| `code_review` | Read-only evidence-backed review |
| `refactor` | Behavior-preserving structural improvements |
| `documentation_update` | Ground-truth documentation updates |
| `feature_impl` | Bounded feature implementation |

## Core Boundary

SkillGate does not:

- execute code changes
- run tests on behalf of an agent
- route, schedule, or activate skills
- call Codex, OpenCode, Claude Code, or other executors
- claim to improve agent success rates

The maintained core is the local input-contract compiler, clarification loop, context inspection, explanation command, schema export, and Python package/CLI surface.

## Verification

```bash
uv run python -m unittest discover -s tests
uv run python -m skillgate schemas --out exported-schemas
uv build
```

## License

MIT
# Skillgate
