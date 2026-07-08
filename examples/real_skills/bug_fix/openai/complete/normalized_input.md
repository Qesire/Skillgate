# Normalized Skill Input

## Selected Skill

- **Skill:** `bug_fix` — Bug Fix
- **Decision:** `ask_user`
- **Reason:** Authorization is required before proceeding.

## User Request

Fix the KeyError crash in auth.py around line 42 that happens when a user logs in with an expired token. Reproduce with `pytest tests/test_auth.py::test_expired_token`. Keep changes to source only; do not modify tests, the public API, or add dependencies.

## Safe Defaults

- allowed_change_scope

## Authorization Required

> These must be explicitly authorized before proceeding.

- 

## Questions for User

1. 

## Activation Instruction

**Do not activate the skill yet.** Answer the questions above, then recompile.
