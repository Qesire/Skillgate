# Skill: Documentation Update

Update documentation without fabricating unsupported project claims.

## Activation
Triggered when:
- A user asks to update README, installation docs, contribution guides, or API docs
- A user requests new documentation sections with specific content descriptions

## Execution
1. Identify the target document and the intended audience.
2. Read the existing document and related project files.
3. Discover project commands, package manager, and conventions from local config.
4. Write or update the documentation section.
5. Ground every factual claim in repo evidence.

## Output
- Updated documentation file
- A list of claims with evidence sources

## Constraints
- Only use facts grounded in repo files (README, pyproject, config, source).
- Do not invent metrics, adoption numbers, or benchmark results.
- Do not fabricate features or capabilities the project does not have.
- Do not write promotional language without explicit user request.