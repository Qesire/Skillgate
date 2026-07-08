# Skill: Refactor

Improve code structure while strictly preserving behavior and public contracts.

## Activation
Triggered when:
- A user asks to restructure, rename, or simplify code
- A user requests deduplication or code reorganization
- A user asks to improve naming or modularity

## Execution
1. Identify the refactoring target and goal.
2. Read the current code and its test coverage.
3. Identify related tests.
4. Apply structural changes.
5. Run the smallest relevant test command.
6. Verify that behavior is preserved.

## Output
- Summary of changes made
- Before/after structure comparison
- Test results confirming behavior preservation

## Constraints
- Behavior must be strictly preserved.
- Do not change public API without explicit authorization.
- Do not batch move or rename files without authorization.
- Keep changes localized. Do not perform broad refactors unless explicitly requested.
- Do not delete files.
- Run tests to verify behavior preservation before declaring completion.