# Examples

This directory contains tiny deterministic fixtures for the core SkillGate workflows.

Fixtures:

- `python_pytest_minimal`: README + `pyproject.toml` + pytest-shaped parser target.
- `docs_only`: README + CONTRIBUTING.
- `real_skills`: sample `SKILL.md` files and request fixtures for `audit-skill`, `compile`, and `answer`.

These fixtures do not test downstream coding-agent execution. They keep context discovery, skill auditing, and normalized skill input compilation reproducible.
