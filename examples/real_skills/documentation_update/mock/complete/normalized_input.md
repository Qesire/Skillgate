# Normalized Skill Input

## Selected Skill

- **Skill:** `documentation_update` — Documentation Update
- **Decision:** `assume_and_continue`
- **Reason:** Only low-risk gaps remain; conservative assumptions are recorded.

## User Request

Update README.md to document the existing `skillgate audit-skill` and `skillgate compile` commands for a developer audience. Only include facts grounded in the repo (README, pyproject.toml, source). Do not invent metrics or adoption numbers.

## Safe Defaults

- Only use facts grounded in repo files
- Do not invent metrics, adoption numbers, or benchmark results

## Activation Instruction

Activate the `documentation_update` skill with the inputs above.

Before editing, perform read-only discovery for agent-discoverable slots.

## Expected Target Skill Output

- updated documentation sections
- facts grounded in repo files or user-provided context
- no fabricated metrics or claims
