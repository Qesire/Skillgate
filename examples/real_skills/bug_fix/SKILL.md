# Skill: Bug Fix

Repair a reported defect while preserving existing behavior and public contracts.

## Activation
Triggered when:
- A user reports a runtime error, crash, unexpected output, or broken behavior
- A test fails unexpectedly because the source code is incorrect

## Execution
1. Read and understand the reported symptom.
2. Reproduce the failure using the smallest relevant command.
3. Isolate the root cause.
4. Propose a minimal source fix.
5. Do not modify test expectations unless explicitly authorized.
6. Report the fix and verification result.

## Output
- Root cause analysis with evidence from source files
- A minimal source change that fixes the reported behavior
- The relevant test command and its result

## Constraints
- Do not modify tests unless explicitly authorized.
- Do not change public API surface.
- Prefer minimal source changes over broad refactors.
- Do not delete files.
- Do not introduce new dependencies.
- Do not call external services.