# Normalized Skill Input

## Selected Skill

- **Skill:** `bug_fix` — Bug Fix
- **Decision:** `ask_user`
- **Reason:** Some required inputs need to be provided by the user.

## User Request

Fix the KeyError crash in auth.py around line 42 that happens when a user logs in with an expired token. Reproduce with `pytest tests/test_auth.py::test_expired_token`. Keep changes to source only; do not modify tests, the public API, or add dependencies.

## Safe Defaults

- Do not delete files without explicit authorization
- Do not call external services

## Questions for User

1. What changes are permitted: source-only, tests allowed, public API changes

## Activation Instruction

**Do not activate the skill yet.** Answer the questions above, then recompile.
