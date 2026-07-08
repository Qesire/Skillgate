# Normalized Skill Input

## Selected Skill

- **Skill:** `refactor` — Refactor
- **Decision:** `assume_and_continue`
- **Reason:** Only low-risk gaps remain; conservative assumptions are recorded.

## User Request

Refactor src/parser.py to split the monolithic parse() function into smaller, named helpers for readability. Preserve behavior exactly; do not change the public API or move files. Run `pytest tests/test_parser.py` to confirm behavior is preserved.

## Safe Defaults

- Do not delete files without explicit authorization
- Keep changes localized; do not perform broad refactors unless requested

## Activation Instruction

Activate the `refactor` skill with the inputs above.

Before editing, perform read-only discovery for agent-discoverable slots.

## Expected Target Skill Output

- refactored targets with before/after summary
- behavior preservation evidence (test pass)
- changed files list
- any remaining structural concerns
