# SkillGate Roadmap

This roadmap tracks the maintained pre-activation metaskill, CLI, and package surface.

## Complete

- Built-in skill contracts for bug fixes, failing test repair, code review, refactoring, documentation updates, feature implementation, and unknown tasks.
- `audit-skill` for rule-based contracts and optional LLM-assisted discovery.
- `compile` for selected built-in skills and `SKILL.md` / `SKILL.input.yaml` inputs.
- Local context inspection with allowlists and secret redaction.
- Clarification answer recording and recompilation lineage.
- Core JSON Schema export and validation for `SkillInputContract`, `InputSlotState`, `NormalizedSkillInput`, decisions, clarification packets, clarification answers, and recompile metadata.
- Python package entry point and wheel smoke coverage.

## Next

1. Add deterministic schema validation for every CLI artifact written by `compile`.
2. Add more CLI fixture tests for custom non-built-in skill contracts.
3. Package the bundled `skillgate-preactivation` metaskill as a first-class installable skill/plugin asset.
4. Tighten LLM audit quality checks without making LLM access required for the default test suite.
5. Reduce remaining internal TaskBrief compatibility tests once downstream users have migrated to `NormalizedSkillInput`.

## Out Of Scope

- Running agents or tests on behalf of users.
- Executor adapters, campaign orchestration, paper evidence generation, or benchmark release tooling.
- Claims about downstream coding-agent success rate or token savings.
