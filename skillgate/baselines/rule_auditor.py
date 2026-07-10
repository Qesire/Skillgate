"""Phase A: Skill Contract Discovery.

Audit a SKILL.md (or similar skill description file) and discover its pre-activation
input contract. This module answers: "what does this skill need before it can be activated?"

The auditor reads the skill's execution/output rules and infers:
- required_slots: inputs the skill explicitly or implicitly needs
- ask_if_missing: which missing info should be asked of the user
- discover_if_missing: which missing info the agent should discover from codebase
- safe_defaults: conservative defaults that can be assumed
- block_if: conditions that should block activation

For MVP, the auditor can work with built-in contracts (from capabilities.py)
or parse YAML front matter in SKILL.md files that declare explicit invocation_preconditions.
Future: use LLM to infer contracts from natural-language skill descriptions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from ..capabilities import BUILTIN_CONTRACTS, get_contract_for_skill
from ..schema import (
    SKILL_INPUT_CONTRACT_VERSION,
    build_skill_input_contract,
    validate_skill_input_contract,
)


def audit_skill(
    skill_path: str | Path,
    *,
    use_builtin_fallback: bool = True,
) -> dict[str, Any]:
    """Audit a SKILL.md file and return a SkillInputContract.

    Resolution order:
    1. If SKILL.md has YAML front matter with `skillgate.invocation_preconditions`, parse it.
    2. If `skillgate.skill_id` in front matter references a known builtin, use that.
    3. If use_builtin_fallback, try to match by skill name or path to a builtin.
    4. Otherwise return a minimal contract with inferred slots.

    Args:
        skill_path: Path to a SKILL.md or equivalent skill description file.
        use_builtin_fallback: When true, fall back to built-in contracts.

    Returns:
        A SkillInputContract dict.
    """
    skill_path = Path(skill_path).resolve()
    if not skill_path.is_file():
        raise FileNotFoundError(f"skill file not found: {skill_path}")

    text = skill_path.read_text(encoding="utf-8")
    sha = hashlib.sha256(skill_path.read_bytes()).hexdigest()

    front_matter = None

    # Try YAML front matter
    if text.startswith("---\n"):
        front_matter = _extract_front_matter(text)
        parsed = _try_parse_front_matter(front_matter)
        if parsed:
            return parsed
        if _has_external_skill_identity(front_matter):
            return _infer_minimal_contract(text, skill_path, sha, front_matter=front_matter)

    # Try built-in fallback by name matching
    if use_builtin_fallback:
        builtin = _match_builtin_by_content(text, skill_path)
        if builtin:
            return _annotate_builtin(builtin, skill_path, sha)

    # Minimal contract: infer from content
    return _infer_minimal_contract(text, skill_path, sha, front_matter=front_matter)


def audit_skill_to_yaml(skill_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Audit a skill and write the discovered contract as YAML."""
    contract = audit_skill(skill_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    yaml_text = _contract_to_yaml(contract)
    output_path.write_text(yaml_text, encoding="utf-8")
    return contract


# ── front matter parsing ─────────────────────────────────────


def _extract_front_matter(text: str) -> dict[str, Any] | None:
    """Extract YAML front matter if present."""
    try:
        if "\n---\n" not in text[4:]:
            return None
        front = text[4:].split("\n---\n", 1)[0]
        data = yaml.safe_load(front)
        if not isinstance(data, dict):
            return None
        return data
    except yaml.YAMLError:
        return None


def _try_parse_front_matter(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Try to extract a SkillInputContract from parsed YAML front matter."""
    if not isinstance(data, dict):
        return None

    skillgate = data.get("skillgate")
    if not isinstance(skillgate, dict):
        return None

    # Option A: explicit skill_id referencing a builtin
    skill_id = skillgate.get("skill_id")
    if isinstance(skill_id, str) and skill_id in BUILTIN_CONTRACTS:
        return get_contract_for_skill(skill_id)

    # Option B: explicit invocation_preconditions → convert to SkillInputContract
    preconditions = skillgate.get("invocation_preconditions")
    if isinstance(preconditions, list) and preconditions:
        return _preconditions_to_contract(data, skillgate, preconditions)

    return None


def _has_external_skill_identity(data: dict[str, Any] | None) -> bool:
    """Return true when ordinary SKILL.md front matter names an external skill.

    Built-in matching is intentionally broad and content-based. For third-party
    skills such as SkillsBench task skills, ordinary front matter like
    `name: rl-post-training` is a stronger identity signal than words such as
    "bug" or "test" in the body.
    """
    if not isinstance(data, dict):
        return False
    return any(isinstance(data.get(key), str) and data[key].strip() for key in ("name", "id", "title"))


def _preconditions_to_contract(
    data: dict,
    skillgate: dict,
    preconditions: list,
) -> dict[str, Any]:
    """Convert explicit preconditions list into a SkillInputContract."""
    # Classify each precondition by its id pattern
    required_slots = []
    ask_if_missing = []
    discover_if_missing = []
    safe_defaults = []
    block_if = []

    for item in preconditions:
        if not isinstance(item, dict):
            continue
        slot_id = str(item.get("id", ""))
        text = str(item.get("text", ""))
        required = item.get("required", False)

        category = _classify_slot(slot_id, text, required)
        support = _infer_support(item, text)
        answer_source = _infer_answer_source(category, text)

        entry = {"id": slot_id, "text": text, "category": category,
                 "support": support, "answer_source": answer_source}
        if category == "blocked":
            block_if.append(entry)
        elif category == "human_askable":
            ask_if_missing.append(entry)
        elif category == "agent_discoverable":
            discover_if_missing.append(entry)
        elif category == "safe_assumption":
            safe_defaults.append(entry)
        elif required:
            required_slots.append(entry)
        else:
            ask_if_missing.append(entry)

    return build_skill_input_contract(
        skill_id=data.get("name", "unknown_skill"),
        skill_name=str(data.get("name", "Unknown Skill")).strip(),
        skill_version=str(data.get("version", "0.0.0")).strip(),
        skill_description=str(data.get("description", "")).strip(),
        required_slots=required_slots,
        ask_if_missing=ask_if_missing,
        discover_if_missing=discover_if_missing,
        safe_defaults=safe_defaults,
        block_if=block_if,
    )


def _classify_slot(slot_id: str, text: str, required: bool) -> str:
    """Classify a slot based on its id and text content."""
    text_lower = text.lower()

    # Block conditions
    block_patterns = ["credential", "secret", "production", "destructive", "malware", "exfiltration"]
    if any(p in text_lower for p in block_patterns):
        return "blocked"

    # Authorization-required
    auth_patterns = ["delete", "push", "deploy", "release", "payment", "stripe", "send email", "submit", "force"]
    if any(p in text_lower for p in auth_patterns):
        return "requires_authorization"

    # Agent-discoverable
    discover_patterns = [
        "test framework", "test command", "package manager", "project", "config",
        "ci", "lint", "format", "discover", "build", "dependency", "structure",
        "source file", "module", "convention", "codebase", "repo",
        "readme", "agents", "contributing", "workflow", "docker",
    ]
    if any(p in text_lower for p in discover_patterns):
        return "agent_discoverable"

    # Safe assumptions
    default_patterns = [
        "do not", "should not", "default", "prefer", "minimal",
        "unless", "without", "read-only", "report only",
    ]
    if any(p in text_lower for p in default_patterns):
        return "safe_assumption"

    return "human_askable"


def _infer_support(item: dict, text: str) -> str:
    """Infer the support level of a precondition."""
    if item.get("support"):
        return item["support"]
    # If it came from front matter, it's explicit
    return "explicit"


def _infer_answer_source(category: str, text: str) -> str:
    """Infer answer source from category and text."""
    mapping = {
        "human_askable": "human",
        "agent_discoverable": "agent",
        "safe_assumption": "policy_default",
        "requires_authorization": "authorization",
        "blocked": "blocked",
    }
    ans = mapping.get(category, "human")
    # Check for human_or_agent patterns
    text_lower = text.lower()
    if any(w in text_lower for w in ["error message", "reproduction", "traceback", "log", "failing"]):
        ans = "human_or_agent"
    return ans


# ── built-in matching ────────────────────────────────────────


def _match_builtin_by_content(text: str, path: Path) -> dict[str, Any] | None:
    """Try to match the skill file to a built-in contract by name or content."""
    text_lower = text.lower()
    path_name = path.stem.lower()

    if "bug" in path_name or "bug_fix" in path_name or "bug" in text_lower:
        return get_contract_for_skill("bug_fix")
    if "test" in path_name or "failing_test" in path_name or "test_repair" in path_name:
        return get_contract_for_skill("failing_test_repair")
    if "review" in path_name or "code_review" in path_name or "审查" in text:
        return get_contract_for_skill("code_review")
    if "refactor" in path_name or "重构" in text:
        return get_contract_for_skill("refactor")
    if "document" in path_name or "doc" in path_name or "文档" in text:
        return get_contract_for_skill("documentation_update")
    if "feature" in path_name or "feature_impl" in path_name or "实现" in text:
        return get_contract_for_skill("feature_impl")

    return None


def _annotate_builtin(
    contract: dict[str, Any],
    skill_path: Path,
    sha: str,
) -> dict[str, Any]:
    """Annotate a built-in contract with source information."""
    contract["source_path"] = str(skill_path)
    contract["source_sha256"] = sha
    contract["contract_evidence"] = [{
        "source": "builtin",
        "skill_id": contract["skill_id"],
        "method": "content_matching",
    }]
    validate_skill_input_contract(contract)
    return contract


# ── minimal contract inference ───────────────────────────────


def _infer_minimal_contract(
    text: str,
    skill_path: Path,
    sha: str,
    *,
    front_matter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal SkillInputContract from natural-language skill description."""
    skill_id = _infer_skill_id(skill_path, front_matter)
    name = _infer_skill_name(skill_id, skill_path, front_matter)
    version = str((front_matter or {}).get("version", "0.0.0")).strip() or "0.0.0"
    description = str((front_matter or {}).get("description", f"Skill described in {skill_path.name}")).strip()

    required_slots = [
        {"id": "task_direction", "text": "What kind of task needs to be performed?",
         "category": "human_askable", "support": "inferred", "answer_source": "human"},
        {"id": "scope", "text": "What is the scope of the request?",
         "category": "human_askable", "support": "inferred", "answer_source": "human"},
        {"id": "success_criteria", "text": "How will completion be determined?",
         "category": "human_askable", "support": "inferred", "answer_source": "human"},
    ]

    text_lower = text.lower()
    discover_if_missing = []
    if "test" in text_lower:
        discover_if_missing.append(
            {"id": "test_framework", "text": "Discover test framework and commands from project config",
             "category": "agent_discoverable", "support": "inferred", "answer_source": "agent"}
        )
    if "build" in text_lower:
        discover_if_missing.append(
            {"id": "build_system", "text": "Discover build system and package manager",
             "category": "agent_discoverable", "support": "inferred", "answer_source": "agent"}
        )
    discover_if_missing.append(
        {"id": "codebase_structure", "text": "Discover relevant source files and module structure",
         "category": "agent_discoverable", "support": "inferred", "answer_source": "agent"}
    )

    safe_defaults = [
        {"id": "no_destructive", "text": "Do not perform destructive operations without authorization",
         "category": "safe_assumption", "support": "recommended", "answer_source": "policy_default"},
        {"id": "read_only_first", "text": "Start with read-only inspection before modifying",
         "category": "safe_assumption", "support": "recommended", "answer_source": "policy_default"},
    ]

    block_if = [
        {"id": "credential_exposure", "text": "Credential or secret exfiltration",
         "category": "blocked", "support": "recommended", "answer_source": "blocked"},
        {"id": "production_mutation", "text": "Production data mutation without authorization",
         "category": "blocked", "support": "recommended", "answer_source": "blocked"},
    ]

    return build_skill_input_contract(
        skill_id=skill_id,
        skill_name=name,
        skill_version=version,
        skill_description=description,
        source_path=str(skill_path),
        source_sha256=sha,
        required_slots=required_slots,
        ask_if_missing=required_slots,
        discover_if_missing=discover_if_missing,
        safe_defaults=safe_defaults,
        block_if=block_if,
        contract_evidence=[{
            "source": "inferred",
            "method": "content_scan",
            "file": str(skill_path),
        }],
    )


def _infer_skill_id(skill_path: Path, front_matter: dict[str, Any] | None) -> str:
    """Infer a stable skill id from ordinary skill front matter or path."""
    if front_matter:
        for key in ("name", "id"):
            value = front_matter.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if skill_path.stem == "SKILL" and skill_path.parent.name:
        return skill_path.parent.name.strip()
    return skill_path.stem


def _infer_skill_name(skill_id: str, skill_path: Path, front_matter: dict[str, Any] | None) -> str:
    """Infer display name without losing a frontmatter-provided id."""
    if front_matter:
        title = front_matter.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    if skill_id:
        return skill_id.replace("_", " ").replace("-", " ").title()
    return skill_path.stem.replace("_", " ").title()


# ── YAML export ──────────────────────────────────────────────


def _contract_to_yaml(contract: dict[str, Any]) -> str:
    """Serialize a SkillInputContract to canonical human-readable YAML.

    Normalizes the contract through :func:`build_skill_input_contract` so the
    output always matches the canonical schema, even when fed a from-scratch
    dict (e.g., a ``get_contract_for_skill()`` return value that was not
    already built through the builder).
    """
    normalized = build_skill_input_contract(
        skill_id=contract.get("skill_id", "unknown"),
        skill_name=contract.get("skill_name", "Unknown"),
        skill_version=contract.get("skill_version", "0.0.0"),
        skill_description=contract.get("skill_description", ""),
        source_path=contract.get("source_path"),
        source_sha256=contract.get("source_sha256"),
        required_slots=contract.get("required_slots", []),
        ask_if_missing=contract.get("ask_if_missing", []),
        discover_if_missing=contract.get("discover_if_missing", []),
        safe_defaults=contract.get("safe_defaults", []),
        safety_blocks=contract.get("safety_blocks", []),
        authorization_requirements=contract.get("authorization_requirements", []),
        execution_constraints=contract.get("execution_constraints", []),
        forbidden_actions=contract.get("forbidden_actions", []),
        stop_conditions=contract.get("stop_conditions", []),
        block_if=contract.get("block_if", []),
        contract_evidence=contract.get("contract_evidence", []),
    )
    header = [
        "# SkillGate Input Contract",
        f"# Generated from audit of: {contract.get('source_path', 'unknown')}",
    ]
    body = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False)
    return "\n".join(header) + "\n" + body
