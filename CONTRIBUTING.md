# Contributing to SkillGate

SkillGate is a focused pre-activation metaskill and Python CLI for skill input contracts. Changes should preserve the core command behavior, artifact schemas, and local safety defaults.

## Development Setup

Use Python 3.11 or newer and `uv`:

```bash
uv sync --frozen --extra dev
uv run python -m unittest discover -s tests
```

Before submitting a change, run:

```bash
uv run python -m unittest discover -s tests
uv run python -m skillgate schemas --out exported-schemas
uv build
```

## Change Rules

- Keep user intent, authorization, and acceptance criteria separate from agent-discoverable repository facts.
- Add focused tests for behavior changes and negative tests for validators.
- Regenerate `schemas/` after changing a published core artifact contract.
- Introduce a new schema version for incompatible fields.
- Do not commit `.skillgate/`, build outputs, virtual environments, credentials, or raw private data.
- Do not claim downstream coding-agent success improvements from compiler-only changes.

## Pull Requests

Describe the user-facing failure or maintenance issue, the behavioral change, compatibility impact, and exact verification commands.

Security-sensitive reports should follow `SECURITY.md`, not a public issue.
