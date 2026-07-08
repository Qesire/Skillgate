# Skill: Code Review

Perform a read-only, evidence-backed code review with explicit focus and severity bar.

## Activation
Triggered when:
- A user asks to review code changes, a PR, or a diff
- A user requests a security audit or correctness check on specific files

## Execution
1. Identify the review target (files, diff, PR).
2. Read the changed or specified code.
3. Analyze for the requested focus: correctness, security, performance, maintainability, or API risk.
4. Report only findings that meet the severity bar.
5. Cite specific file paths and line ranges as evidence.

## Output
- A structured review report
- Each finding: severity, file, line range, description, evidence
- Optional: suggested fixes (reported separately, not applied)

## Constraints
- Review is read-only. Do not modify any files.
- Only report evidence-backed findings. Do not speculate.
- Redact secret-like values in evidence.
- Do not print full secret values.
- Do not push, deploy, or call external services.