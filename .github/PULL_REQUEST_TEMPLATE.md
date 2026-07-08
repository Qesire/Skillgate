## Change

Describe the behavioral change and why it belongs in SkillGate.

## Contract Impact

- Schemas or artifact compatibility:
- Safety, permissions, or side effects:

## Evidence

- [ ] Focused tests added or updated
- [ ] `uv run python -m unittest discover -s tests`
- [ ] `uv run python -m skillgate schemas --out exported-schemas`
- [ ] `uv build`
- [ ] `uv.lock` is unchanged or intentionally reviewed; CI uses `uv sync --frozen`

## Core Boundary

State whether the change affects `audit-skill`, `compile`, clarification, local context inspection, explanation, schema export, packaging, or documentation.
