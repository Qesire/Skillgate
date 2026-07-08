"""Built-in SkillInputContract definitions with support classification.

Each slot carries:
- category: human_askable | agent_discoverable | safe_assumption | requires_authorization | blocked
- support: explicit | inferred | recommended
- answer_source: human | agent | human_or_agent | authorization | policy_default | blocked | known
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .schema import build_skill_input_contract


def _slot(entry_id: str, text: str, category: str, support: str = "explicit",
          answer_source: str | None = None,
          missing_policy: str | None = None) -> dict[str, Any]:
    """Build a slot entry with explicit support classification."""
    if answer_source is None:
        # Infer answer_source from category
        answer_source = {
            "human_askable": "human",
            "agent_discoverable": "agent",
            "safe_assumption": "policy_default",
            "requires_authorization": "authorization",
            "blocked": "blocked",
        }.get(category, "human")
    entry: dict[str, Any] = {
        "id": entry_id,
        "text": text,
        "category": category,
        "support": support,
        "answer_source": answer_source,
    }
    if missing_policy is not None:
        entry["missing_policy"] = missing_policy
    return entry


BUILTIN_CONTRACTS: dict[str, dict[str, Any]] = {
    "bug_fix": {
        "skill_id": "bug_fix",
        "skill_name": "Bug Fix",
        "skill_version": "1.0.0",
        "skill_description": "Diagnose and repair a reported defect with bounded source changes.",
        "required_slots": [
            _slot("failure_symptom", "What is the symptom: error message, traceback, blank page, timeout?",
                  "human_askable", support="recommended"),
            _slot("target_scope", "Which source files or module should be investigated?",
                  "agent_discoverable", support="recommended"),
            _slot("allowed_change_scope", "What kind of changes are allowed: source only, tests, public API?",
                  "human_askable", support="recommended"),
            _slot("verification_expectation", "How should completion be verified?",
                  "agent_discoverable", support="recommended"),
        ],
        "ask_if_missing": [
            _slot("may_modify_tests", "Is modifying test expectations allowed?",
                  "human_askable", support="recommended"),
            _slot("may_change_public_api", "Is changing public API allowed?",
                  "human_askable", support="recommended"),
            _slot("destructive_action_permission", "Are file deletion or external side effects allowed?",
                  "requires_authorization", support="recommended"),
            _slot("reproduction_evidence", "Can you provide reproduction steps or error details?",
                  "human_askable", support="recommended", answer_source="human_or_agent"),
        ],
        "discover_if_missing": [
            _slot("test_framework", "Discover test framework from project configuration",
                  "agent_discoverable", support="inferred"),
            _slot("package_manager", "Discover package manager from project files",
                  "agent_discoverable", support="inferred"),
            _slot("smallest_test_command", "Discover smallest relevant test command",
                  "agent_discoverable", support="inferred"),
            _slot("local_agent_instructions", "Read AGENTS.md or SKILL.md for local conventions",
                  "agent_discoverable", support="inferred"),
            _slot("codebase_structure", "Discover related source files and module structure",
                  "agent_discoverable", support="inferred"),
        ],
        "safe_defaults": [
            _slot("do_not_modify_tests", "Do not modify test expectations unless explicitly authorized",
                  "safe_assumption", support="recommended"),
            _slot("do_not_change_public_api", "Do not change public API unless explicitly authorized",
                  "safe_assumption", support="recommended"),
            _slot("prefer_minimal_changes", "Prefer minimal source changes over broad refactors",
                  "safe_assumption", support="recommended"),
            _slot("no_file_deletion", "Do not delete files without explicit authorization",
                  "safe_assumption", support="recommended"),
            _slot("no_external_side_effects", "Do not call external services or mutate remote data",
                  "safe_assumption", support="recommended"),
        ],
        "block_if": [
            _slot("credential_exposure", "Credential or secret exfiltration",
                  "blocked", support="recommended"),
            _slot("production_mutation", "Production data mutation without authorization",
                  "blocked", support="recommended"),
            _slot("destructive_operation", "Destructive operation (rm -rf, drop table, force push)",
                  "blocked", support="recommended"),
        ],
    },
    "failing_test_repair": {
        "skill_id": "failing_test_repair",
        "skill_name": "Failing Test Repair",
        "skill_version": "1.0.0",
        "skill_description": "Repair a failing test while preserving test intent and public behavior.",
        "required_slots": [
            _slot("failing_test_target", "Which test is failing?",
                  "human_askable", support="recommended", answer_source="human_or_agent"),
            _slot("reproduction_command", "How to reproduce the failure?",
                  "agent_discoverable", support="inferred"),
            _slot("expected_behavior", "What is the expected behavior?",
                  "agent_discoverable", support="inferred"),
            _slot("repair_scope", "Source fix or test expectation change?",
                  "human_askable", support="recommended"),
        ],
        "ask_if_missing": [
            _slot("may_update_snapshots", "Is updating snapshots allowed?",
                  "human_askable", support="recommended"),
            _slot("may_change_test_intent", "Can test expectations be modified?",
                  "human_askable", support="recommended"),
            _slot("source_vs_test_fix", "Should the source be fixed or should test expectations be adjusted?",
                  "human_askable", support="recommended"),
        ],
        "discover_if_missing": [
            _slot("test_framework", "Discover test framework from project config",
                  "agent_discoverable", support="inferred"),
            _slot("test_command", "Discover smallest relevant test command",
                  "agent_discoverable", support="inferred"),
            _slot("related_source", "Discover source files related to the failing test",
                  "agent_discoverable", support="inferred"),
            _slot("local_policies", "Read AGENTS.md for test modification policies",
                  "agent_discoverable", support="inferred"),
        ],
        "safe_defaults": [
            _slot("do_not_modify_tests", "Do not modify test expectations unless explicitly authorized",
                  "safe_assumption", support="recommended"),
            _slot("preserve_test_intent", "Preserve the original test intent",
                  "safe_assumption", support="recommended"),
            _slot("minimal_source_fix", "Prefer minimal source changes",
                  "safe_assumption", support="recommended"),
        ],
        "block_if": [
            _slot("credential_exposure", "Credential or secret exfiltration", "blocked", support="recommended"),
        ],
    },
    "code_review": {
        "skill_id": "code_review",
        "skill_name": "Code Review",
        "skill_version": "1.0.0",
        "skill_description": "Perform a read-only, evidence-backed code review with explicit focus and severity bar.",
        "required_slots": [
            _slot("review_target", "Which files, diff, or PR should be reviewed?",
                  "human_askable", support="recommended", answer_source="human_or_agent"),
            _slot("review_focus", "What focus: correctness, security, performance, maintainability, API risk?",
                  "human_askable", support="recommended"),
            _slot("severity_bar", "What severity of findings should be reported?",
                  "human_askable", support="recommended"),
        ],
        "ask_if_missing": [
            _slot("permission_to_modify", "Is modifying files allowed during review?",
                  "requires_authorization", support="recommended"),
        ],
        "discover_if_missing": [
            _slot("changed_files", "Discover changed files from git metadata",
                  "agent_discoverable", support="inferred"),
            _slot("repo_instructions", "Read review conventions from local docs",
                  "agent_discoverable", support="inferred"),
        ],
        "safe_defaults": [
            _slot("read_only", "Review is read-only; do not modify files",
                  "safe_assumption", support="recommended"),
            _slot("evidence_backed", "Only report evidence-backed findings",
                  "safe_assumption", support="recommended"),
            _slot("redact_secrets", "Redact secret-like values in evidence",
                  "safe_assumption", support="recommended"),
        ],
        "block_if": [
            _slot("credential_exposure", "Printing full secret values", "blocked", support="recommended"),
        ],
    },
    "refactor": {
        "skill_id": "refactor",
        "skill_name": "Refactor",
        "skill_version": "1.0.0",
        "skill_description": "Improve code structure while preserving behavior and public contracts.",
        "required_slots": [
            _slot("target_scope", "What should be refactored?",
                  "human_askable", support="recommended"),
            _slot("refactor_goal", "What improvement: naming, structure, duplication, performance?",
                  "human_askable", support="recommended"),
            _slot("behavior_preservation", "Must behavior be strictly preserved?",
                  "human_askable", support="recommended"),
        ],
        "ask_if_missing": [
            _slot("public_api_change", "Is public API change allowed?",
                  "human_askable", support="recommended"),
            _slot("batch_file_moves", "Are batch file moves/renames authorized?",
                  "requires_authorization", support="recommended"),
        ],
        "discover_if_missing": [
            _slot("related_tests", "Discover related tests", "agent_discoverable", support="inferred"),
            _slot("project_conventions", "Discover code style from project config",
                  "agent_discoverable", support="inferred"),
        ],
        "safe_defaults": [
            _slot("preserve_behavior", "Preserve public behavior", "safe_assumption", support="recommended"),
            _slot("no_public_api_change", "Do not change public API without authorization",
                  "safe_assumption", support="recommended"),
            _slot("localized_changes", "Keep changes localized", "safe_assumption", support="recommended"),
        ],
        "block_if": [
            _slot("credential_exposure", "Credential or secret exfiltration", "blocked", support="recommended"),
        ],
    },
    "documentation_update": {
        "skill_id": "documentation_update",
        "skill_name": "Documentation Update",
        "skill_version": "1.0.0",
        "skill_description": "Update documentation without fabricating unsupported project claims.",
        "required_slots": [
            _slot("target_document", "Which document should be updated?",
                  "human_askable", support="recommended"),
            _slot("audience", "Who is the target audience?",
                  "human_askable", support="recommended"),
            _slot("allowed_claims", "What claims are allowed: factual only, promotional?",
                  "human_askable", support="recommended"),
        ],
        "ask_if_missing": [
            _slot("audience_when_broad", "What audience and section should the documentation target?",
                  "human_askable", support="recommended"),
        ],
        "discover_if_missing": [
            _slot("project_commands", "Discover project commands from config",
                  "agent_discoverable", support="inferred"),
            _slot("package_manager", "Discover package manager from config",
                  "agent_discoverable", support="inferred"),
            _slot("existing_docs", "Discover existing documentation files",
                  "agent_discoverable", support="inferred"),
        ],
        "safe_defaults": [
            _slot("factual_only", "Only use facts grounded in repo files",
                  "safe_assumption", support="recommended"),
            _slot("no_fabrication", "Do not invent metrics, adoption, or benchmark claims",
                  "safe_assumption", support="recommended"),
        ],
        "block_if": [
            _slot("fabricated_claims", "Fabricating unsupported project claims",
                  "blocked", support="recommended"),
        ],
    },
    "feature_impl": {
        "skill_id": "feature_impl",
        "skill_name": "Feature Implementation",
        "skill_version": "1.0.0",
        "skill_description": "Implement a bounded user-visible feature with explicit behavior and tests.",
        "required_slots": [
            _slot("feature_behavior", "What user-visible behavior should be added?",
                  "human_askable", support="recommended"),
            _slot("target_surface", "Where should it be exposed: CLI, API, UI component?",
                  "human_askable", support="recommended"),
            _slot("data_contract", "What data format or API contract should be used?",
                  "human_askable", support="recommended"),
        ],
        "ask_if_missing": [
            _slot("external_service", "Are external service calls or payment APIs involved?",
                  "requires_authorization", support="recommended"),
            _slot("target_surface_when_missing", "What target surface: CLI, API endpoint, or UI screen?",
                  "human_askable", support="recommended"),
            _slot("dependency_policy", "Are new dependencies allowed?",
                  "human_askable", support="recommended"),
        ],
        "discover_if_missing": [
            _slot("ui_patterns", "Discover existing UI/API patterns",
                  "agent_discoverable", support="inferred"),
            _slot("test_commands", "Discover verification commands",
                  "agent_discoverable", support="inferred"),
        ],
        "safe_defaults": [
            _slot("no_large_deps", "Do not introduce large dependencies without authorization",
                  "safe_assumption", support="recommended"),
            _slot("follow_patterns", "Follow existing project patterns",
                  "safe_assumption", support="recommended"),
            _slot("no_payment_secrets", "Do not request or use payment secrets",
                  "safe_assumption", support="recommended"),
        ],
        "block_if": [
            _slot("payment_credentials", "Payment API without safe sandbox setup",
                  "blocked", support="recommended"),
        ],
    },
    "generic_unknown": {
        "skill_id": "generic_unknown",
        "skill_name": "Unknown Task",
        "skill_version": "1.0.0",
        "skill_description": "Clarify a request whose task direction is not actionable yet.",
        "required_slots": [
            _slot("task_direction", "What kind of task: fix, review, refactor, document, implement?",
                  "human_askable", support="recommended"),
            _slot("scope", "What is the scope of the request?",
                  "human_askable", support="recommended"),
            _slot("success_criteria", "How will completion be determined?",
                  "human_askable", support="recommended"),
        ],
        "ask_if_missing": [
            _slot("task_direction_primary", "What kind of task is this: fix, review, refactor, document, implement?",
                  "human_askable", support="recommended"),
        ],
        "discover_if_missing": [
            _slot("repo_context", "Discover project language, framework, and structure",
                  "agent_discoverable", support="inferred"),
        ],
        "safe_defaults": [
            _slot("no_modification", "Do not modify files until task direction is known",
                  "safe_assumption", support="recommended"),
        ],
        "block_if": [
            _slot("unclear_intent", "Task direction is fundamentally unclear",
                  "blocked", support="recommended"),
            _slot("destructive", "Destructive operation without authorization",
                  "blocked", support="recommended"),
        ],
    },
}

TASK_KIND_TO_SKILL_ID = {
    "bug_fix": "bug_fix",
    "failing_test": "failing_test_repair",
    "code_review": "code_review",
    "refactor": "refactor",
    "documentation": "documentation_update",
    "feature_impl": "feature_impl",
    "unknown": "generic_unknown",
}


def get_contract_for_skill(skill_id: str) -> dict[str, Any]:
    if skill_id not in BUILTIN_CONTRACTS:
        raise ValueError(f"unknown skill_id: {skill_id}")
    contract = deepcopy(BUILTIN_CONTRACTS[skill_id])
    if "schema_version" in contract:
        return contract
    return build_skill_input_contract(
        skill_id=contract["skill_id"],
        skill_name=contract["skill_name"],
        skill_version=contract["skill_version"],
        skill_description=contract["skill_description"],
        required_slots=contract["required_slots"],
        ask_if_missing=contract["ask_if_missing"],
        discover_if_missing=contract["discover_if_missing"],
        safe_defaults=contract["safe_defaults"],
        block_if=contract["block_if"],
    )


def get_contract_for_task_kind(task_kind: str) -> dict[str, Any]:
    skill_id = TASK_KIND_TO_SKILL_ID.get(task_kind)
    if skill_id is None:
        raise ValueError(f"unknown task_kind: {task_kind}")
    return get_contract_for_skill(skill_id)


# ── Legacy compatibility ─────────────────────────────────────


def get_capability_for_task(task_kind: str) -> dict[str, Any]:
    """Legacy adapter: returns old-format capability from new contract."""
    contract = get_contract_for_task_kind(task_kind)
    required = [s["id"] for s in contract["required_slots"]]
    discoverable = [s["id"] for s in contract["discover_if_missing"]]
    must_ask = [s["id"] for s in contract["ask_if_missing"] if s["category"] == "human_askable"]
    defaults = {s["id"]: s["text"] for s in contract["safe_defaults"]}
    forbidden = [s["text"] for s in contract["block_if"]] + [s["text"] for s in contract["safe_defaults"]]
    verification = [s["text"] for s in contract["discover_if_missing"]]
    return {
        "id": contract["skill_id"],
        "task_kind": task_kind,
        "name": contract["skill_name"],
        "description": contract["skill_description"],
        "triggers": [],
        "anti_triggers": [],
        "required_slots": required,
        "discoverable_slots": discoverable,
        "must_ask_slots": must_ask,
        "safe_defaults": defaults,
        "forbidden_actions": forbidden,
        "verification_hints": verification,
    }


CAPABILITIES: dict[str, dict[str, Any]] = {}
for tk in TASK_KIND_TO_SKILL_ID:
    CAPABILITIES[TASK_KIND_TO_SKILL_ID[tk]] = get_capability_for_task(tk)

TASK_KIND_TO_CAPABILITY = {k: v for k, v in TASK_KIND_TO_SKILL_ID.items()}
