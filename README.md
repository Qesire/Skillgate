# SkillGate

**Pre-activation metaskill and input compiler for agent skills.**

SkillGate is a metaskill/plugin-style layer to run before a task-specific skill. It audits a skill description, writes a reusable `SkillInputContract` reference file, and interactively compiles vague user requests into a `NormalizedSkillInput` that is ready for the target skill. It is a local CLI library plus a bundled metaskill prompt; it does not execute tasks or call coding agents.

## Core Flow

```
Target SKILL.md -> SkillGate -> SKILL.input.yaml
User request + SKILL.input.yaml -> SkillGate -> NormalizedSkillInput
```

SkillGate separates:

- `human_askable`: intent, permission, acceptance criteria
- `agent_discoverable`: repository facts such as test commands and project structure
- `safe_assumption`: conservative defaults such as no file deletion
- `requires_authorization`: actions that need explicit permission
- `blocked`: unsafe requests that should not proceed

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
