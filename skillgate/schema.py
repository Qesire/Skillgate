from __future__ import annotations

import hashlib
from typing import Any

# ── schema versions ──────────────────────────────────────────
LEGACY_TASKBRIEF_VERSION = "taskbrief.v2.p0"

# Backward compat alias
SCHEMA_VERSION = "taskbrief.v2.p0"

SKILL_INPUT_CONTRACT_VERSION = "skillgate.skill_input_contract.v2"
INPUT_SLOT_STATE_VERSION = "skillgate.input_slot_state.v1"
NORMALIZED_SKILL_INPUT_VERSION = "skillgate.normalized_skill_input.v1"

# ── enums ────────────────────────────────────────────────────

SOURCE_KINDS = {
    "user",
    "conversation",
    "repo_file",
    "repo_config",
    "skill",
    "policy",
    "inferred",
    "answer",
    "runtime",
}

TASK_KINDS = {
    "failing_test",
    "bug_fix",
    "code_review",
    "refactor",
    "documentation",
    "feature_impl",
    "unknown",
}

DECISION_KINDS = {
    "block_unsafe",
    "explore_first",
    "ask_user",
    "assume_and_continue",
    "compile_directly",
}

CAPABILITY_IDS = {
    "failing_test_repair",
    "bug_fix",
    "code_review",
    "refactor",
    "documentation_update",
    "feature_impl",
    "generic_unknown",
}

# ── new: InputSlotStatus ─────────────────────────────────────

SLOT_STATUSES = {
    "known",
    "human_askable",
    "agent_discoverable",
    "safe_assumption",
    "requires_authorization",
    "blocked",
    "irrelevant",
}

# ── support classification (how was this slot discovered) ────

SUPPORT_KINDS = {
    "explicit",     # 文档明确写出
    "inferred",     # 从执行步骤合理推断
    "recommended",  # 安全最佳实践建议
    "guessed",      # LLM 低置信度猜测
}

# ── answer_source taxonomy ───────────────────────────────────

ANSWER_SOURCES = {
    "human",             # 用户意图、范围、验收标准、输出偏好
    "agent",             # 仓库结构、测试命令、依赖配置、代码调用链
    "human_or_agent",    # 失败日志、复现命令，优先 agent，失败后问最小证据
    "authorization",     # 删文件、联网、改测试、改 API、部署
    "policy_default",    # 不删文件、不改 public API、不改测试、不联网
    "blocked",           # 不可继续
    "known",             # 已知/已提供
}

# ── helpers ──────────────────────────────────────────────────


def short_hash(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence(
    evidence_id: str,
    source_kind: str,
    source_id: str,
    *,
    path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    quote: str | None = None,
    quote_hash: str | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source_kind: {source_kind}")
    if quote is not None and quote_hash is not None:
        raise ValueError("quote and quote_hash are mutually exclusive")
    return {
        "id": evidence_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "path": path,
        "line_start": line_start,
        "line_end": line_end,
        "quote": quote,
        "quote_hash": quote_hash,
        "confidence": confidence,
    }


def statement(text: str, evidence_ids: list[str], *, confidence: float = 1.0) -> dict[str, Any]:
    return {
        "text": text,
        "evidence_ids": list(evidence_ids),
        "confidence": confidence,
    }


# ═══════════════════════════════════════════════════════════════
#  NEW: SkillInputContract
# ═══════════════════════════════════════════════════════════════


def build_skill_input_contract(
    *,
    skill_id: str,
    skill_name: str,
    skill_version: str,
    skill_description: str,
    source_path: str | None = None,
    source_sha256: str | None = None,
    required_slots: list[dict[str, Any]] | None = None,
    ask_if_missing: list[dict[str, Any]] | None = None,
    discover_if_missing: list[dict[str, Any]] | None = None,
    safe_defaults: list[dict[str, Any]] | None = None,
    safety_blocks: list[dict[str, Any]] | None = None,
    authorization_requirements: list[dict[str, Any]] | None = None,
    execution_constraints: list[dict[str, Any]] | None = None,
    forbidden_actions: list[dict[str, Any]] | None = None,
    stop_conditions: list[dict[str, Any]] | None = None,
    block_if: list[dict[str, Any]] | None = None,
    contract_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a SkillInputContract dict.

    Each slot entry is {id, text, category} where category is one of:
    'human_askable', 'agent_discoverable', 'safe_assumption', 'requires_authorization', 'blocked'.

    The five specialized block categories:
    - safety_blocks: request safety blocks (dangerous task)
    - authorization_requirements: actions needing user permission
    - execution_constraints: don't modify X, don't change Y
    - forbidden_actions: never introduce dependencies, never do X
    - stop_conditions: block if X is missing

    ``block_if`` is kept for backward compatibility; if provided without
    ``safety_blocks``, it is mapped to ``safety_blocks``.
    """
    # Backward compat: block_if → safety_blocks
    if block_if is not None and safety_blocks is None:
        safety_blocks = block_if

    return {
        "schema_version": SKILL_INPUT_CONTRACT_VERSION,
        "skill_id": skill_id,
        "skill_name": skill_name,
        "skill_version": skill_version,
        "skill_description": skill_description,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "required_slots": required_slots or [],
        "ask_if_missing": ask_if_missing or [],
        "discover_if_missing": discover_if_missing or [],
        "safe_defaults": safe_defaults or [],
        "safety_blocks": safety_blocks or [],
        "authorization_requirements": authorization_requirements or [],
        "execution_constraints": execution_constraints or [],
        "forbidden_actions": forbidden_actions or [],
        "stop_conditions": stop_conditions or [],
        "contract_evidence": contract_evidence or [],
    }


V1_VERSION = "skillgate.skill_input_contract.v1"


def migrate_contract_v1_to_v2(contract: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v1 SkillInputContract to v2.

    v2 added five new section fields (safety_blocks, authorization_requirements,
    execution_constraints, forbidden_actions, stop_conditions) replacing the
    monolithic ``block_if``.  This migrator copies existing ``block_if`` entries
    into ``safety_blocks`` (the closest semantic match) and fills the remaining
    sections with empty lists so the contract is valid under the v2 schema.
    """
    migrated = dict(contract)
    migrated["schema_version"] = SKILL_INPUT_CONTRACT_VERSION
    for field in (
        "safety_blocks",
        "authorization_requirements",
        "execution_constraints",
        "forbidden_actions",
        "stop_conditions",
    ):
        if field not in migrated:
            migrated[field] = []
    if "safety_blocks" in migrated and not migrated["safety_blocks"]:
        migrated["safety_blocks"] = list(migrated.get("block_if", []))
    if "block_if" not in migrated:
        migrated["block_if"] = []
    return migrated


def validate_skill_input_contract(contract: dict[str, Any]) -> None:
    req = ["schema_version", "skill_id", "skill_name", "skill_version", "skill_description"]
    for key in req:
        if key not in contract:
            raise ValueError(f"SkillInputContract missing required key: {key}")
        # Required metadata must be non-empty strings, not just present.
        if key in ("skill_id", "skill_name", "skill_version", "skill_description"):
            val = contract[key]
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"SkillInputContract.{key} must be a non-empty string")
    if contract["schema_version"] not in (SKILL_INPUT_CONTRACT_VERSION, V1_VERSION):
        raise ValueError(f"unexpected schema_version: {contract['schema_version']}")
    for section in [
        "required_slots", "ask_if_missing", "discover_if_missing",
        "safe_defaults", "block_if",
        "safety_blocks", "authorization_requirements",
        "execution_constraints", "forbidden_actions", "stop_conditions",
    ]:
        items = contract.get(section, [])
        if not isinstance(items, list):
            raise ValueError(f"SkillInputContract.{section} must be a list")
        for idx, item in enumerate(items):
            _validate_slot_entry(item, f"{section}[{idx}]")


# Evidence-status taxonomy for fail-closed evidence verification.
# A slot's evidence is only considered actionable when verified.
EVIDENCE_STATUSES = {
    "verified",            # every evidence quote confirmed against source
    "partially_verified",  # some quotes verified, others not
    "unverified",          # no quotes verified (or no evidence at all)
}

MISSING_POLICIES = {
    "ask_user",
    "discover_then_ask",
    "discover_only",
    "assume_default",
    "block",
}


def _validate_slot_entry(item: dict[str, Any], field: str) -> None:
    for key in ["id", "text", "category"]:
        if key not in item:
            raise ValueError(f"{field} missing {key}")
    if item["category"] not in SLOT_STATUSES:
        raise ValueError(f"{field}.category invalid: {item['category']}")
    support = item.get("support")
    if support and support not in SUPPORT_KINDS:
        raise ValueError(f"{field}.support invalid: {support}")
    policy = item.get("missing_policy")
    if policy is not None and policy not in MISSING_POLICIES:
        raise ValueError(f"{field}.missing_policy invalid: {policy}")
    ev_status = item.get("evidence_status")
    if ev_status is not None and ev_status not in EVIDENCE_STATUSES:
        raise ValueError(f"{field}.evidence_status invalid: {ev_status}")


def normalize_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Normalize an arbitrary contract dict into a canonical v2 contract.

    Single entry point used everywhere a contract is loaded (CLI, compiler,
    LLM roundtrip).  It accepts v1 or v2 dicts, legacy ``block_if``-only
    contracts, and partial dicts, then returns a fully-populated v2 contract
    with every section present as a list and ``schema_version`` set to v2.

    This is what makes the generate -> save -> load -> compile loop reliable:
    no matter which producer wrote the file, the consumer always sees one
    shape.

    LENIENT by design: a section that is present but the wrong type (e.g. a
    string where a list is expected) is coerced to an empty list so legacy
    migrations don't crash.  Use :func:`validate_strict_contract` for an
    explicit-contract path that must reject malformed sections.
    """
    if not isinstance(contract, dict):
        raise ValueError("contract must be a dict")

    version = contract.get("schema_version")
    if version == V1_VERSION:
        contract = migrate_contract_v1_to_v2(contract)
    elif version not in (SKILL_INPUT_CONTRACT_VERSION, V1_VERSION, None):
        raise ValueError(f"unexpected schema_version: {version}")

    def _list(name: str) -> list[dict[str, Any]]:
        v = contract.get(name, [])
        return list(v) if isinstance(v, list) else []

    block_if = _list("block_if")
    safety_blocks = _list("safety_blocks") or block_if

    return {
        "schema_version": SKILL_INPUT_CONTRACT_VERSION,
        "skill_id": contract.get("skill_id", "unknown_skill"),
        "skill_name": contract.get("skill_name", "Unknown Skill"),
        "skill_version": contract.get("skill_version", "0.0.0"),
        "skill_description": contract.get("skill_description", ""),
        "source_path": contract.get("source_path"),
        "source_sha256": contract.get("source_sha256"),
        "required_slots": _list("required_slots"),
        "ask_if_missing": _list("ask_if_missing"),
        "discover_if_missing": _list("discover_if_missing"),
        "safe_defaults": _list("safe_defaults"),
        "safety_blocks": safety_blocks,
        "authorization_requirements": _list("authorization_requirements"),
        "execution_constraints": _list("execution_constraints"),
        "forbidden_actions": _list("forbidden_actions"),
        "stop_conditions": _list("stop_conditions"),
        "contract_evidence": _list("contract_evidence"),
    }


def validate_strict_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Strict load path for explicit .yaml/.yml contract inputs.

    Unlike :func:`normalize_contract` (lenient, for legacy migration), this
    REJECTS type errors instead of silently emptying them.  A section present
    as a non-list (e.g. ``required_slots: "foo"``) raises ValueError so the
    caller (CLI) can fail-closed rather than silently dropping the user's
    declared slots.

    Returns the normalized v2 contract on success.
    """
    _LIST_SECTIONS = (
        "required_slots", "ask_if_missing", "discover_if_missing",
        "safe_defaults", "safety_blocks", "block_if",
        "authorization_requirements", "execution_constraints",
        "forbidden_actions", "stop_conditions", "contract_evidence",
    )
    for section in _LIST_SECTIONS:
        if section in contract and not isinstance(contract[section], list):
            raise ValueError(
                f"SkillInputContract.{section} must be a list, got "
                f"{type(contract[section]).__name__}"
            )
    normalized = normalize_contract(contract)
    validate_skill_input_contract(normalized)
    # confidence range check per slot
    for section in _LIST_SECTIONS:
        for idx, slot in enumerate(normalized.get(section, [])):
            conf = slot.get("confidence", 1.0)
            if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
                raise ValueError(f"{section}[{idx}].confidence must be in [0,1]")
            ans = slot.get("answer_source")
            if ans is not None and ans not in ANSWER_SOURCES:
                raise ValueError(f"{section}[{idx}].answer_source invalid: {ans}")
    return normalized


# ═══════════════════════════════════════════════════════════════
#  NEW: InputSlotState
# ═══════════════════════════════════════════════════════════════


def build_input_slot_state(
    *,
    name: str,
    description: str,
    category: str,  # from SLOT_STATUSES
    status: str,    # same values
    value: str | None = None,
    question: str | None = None,
    assumption: str | None = None,
    evidence_ids: list[str] | None = None,
    answer_source: str = "human",
    support: str = "recommended",
    risk: str = "low",
    ambiguity: str = "low",
    handling_reason: str = "",
    confidence: float = 1.0,
    missing_policy: str | None = None,
    evidence_status: str | None = None,
    value_source: str | None = None,
    value_source_span: list[int] | None = None,
    conflict: bool = False,
    on_discovery_failure: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": INPUT_SLOT_STATE_VERSION,
        "name": name,
        "text": description,       # compat alias
        "description": description,
        "category": category,
        "status": status,
        "value": value,
        "question": question,
        "assumption": assumption,
        "evidence_ids": evidence_ids or [],
        "answer_source": answer_source,
        "support": support,
        "risk": risk,
        "ambiguity": ambiguity,
        "handling_reason": handling_reason,
        "confidence": confidence,
    }
    if missing_policy is not None:
        result["missing_policy"] = missing_policy
    if evidence_status is not None:
        result["evidence_status"] = evidence_status
    if value_source is not None:
        result["value_source"] = value_source
    if value_source_span is not None:
        result["value_source_span"] = value_source_span
    if conflict:
        result["conflict"] = True
    if on_discovery_failure is not None:
        result["on_discovery_failure"] = on_discovery_failure
    return result


# ═══════════════════════════════════════════════════════════════
#  NEW: NormalizedSkillInput
# ═══════════════════════════════════════════════════════════════


def build_normalized_skill_input(
    *,
    run_id: str,
    skill_id: str,
    skill_name: str,
    raw_request: str,
    human_provided_inputs: list[dict[str, Any]],
    agent_discoverable_inputs: list[dict[str, Any]],
    safe_defaults: list[dict[str, Any]],
    requires_authorization: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    execution_constraints: list[dict[str, Any]],
    decision_kind: str,
    decision_reason: str,
    activation_instruction: str,
    expected_output: str = "",
    evidence_items: list[dict[str, Any]],
    forbidden_actions: list[dict[str, Any]] | None = None,
    stop_conditions: list[dict[str, Any]] | None = None,
    low_confidence_slots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": NORMALIZED_SKILL_INPUT_VERSION,
        "run_id": run_id,
        "skill_id": skill_id,
        "skill_name": skill_name,
        "raw_request": raw_request,
        "human_provided_inputs": human_provided_inputs,
        "agent_discoverable_inputs": agent_discoverable_inputs,
        "safe_defaults": safe_defaults,
        "requires_authorization": requires_authorization,
        "blocked": blocked,
        "execution_constraints": execution_constraints,
        "forbidden_actions": forbidden_actions or [],
        "stop_conditions": stop_conditions or [],
        "low_confidence_slots": low_confidence_slots or [],
        "decision_kind": decision_kind,
        "decision_reason": decision_reason,
        "activation_instruction": activation_instruction,
        "expected_output": expected_output,
        "evidence": evidence_items,
    }


# ═══════════════════════════════════════════════════════════════
#  LEGACY: TaskBrief validation (kept for backward compat)
# ═══════════════════════════════════════════════════════════════


def validate_statement(
    value: dict[str, Any],
    known_evidence_ids: set[str],
    *,
    field_name: str,
    require_evidence: bool = True,
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    if not isinstance(value.get("text"), str) or not value["text"].strip():
        raise ValueError(f"{field_name}.text must be a non-empty string")
    evidence_ids = value.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        raise ValueError(f"{field_name}.evidence_ids must be a list")
    if require_evidence and not evidence_ids:
        raise ValueError(f"{field_name} must be evidence-backed")
    missing = [item for item in evidence_ids if item not in known_evidence_ids]
    if missing:
        raise ValueError(f"{field_name} references missing evidence ids: {missing}")
    _legacy_validate_confidence(value.get("confidence", 1.0), f"{field_name}.confidence")


def validate_statement_list(
    values: list[dict[str, Any]],
    known_evidence_ids: set[str],
    *,
    field_name: str,
    require_evidence: bool = True,
) -> None:
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    for index, item in enumerate(values):
        validate_statement(
            item,
            known_evidence_ids,
            field_name=f"{field_name}[{index}]",
            require_evidence=require_evidence,
        )


def validate_taskbrief(taskbrief: dict[str, Any]) -> None:
    required = [
        "id", "run_id", "schema_version", "task_frame", "matched_capability",
        "decision_kind", "goal", "scope_in", "scope_out",
        "known_facts", "assumptions", "unresolved_unknowns",
        "execution_policy", "forbidden_actions", "verification_policy",
        "stop_conditions", "output_contract", "evidence",
    ]
    for key in required:
        if key not in taskbrief:
            raise ValueError(f"taskbrief missing required key: {key}")
    if taskbrief["schema_version"] != LEGACY_TASKBRIEF_VERSION:
        raise ValueError(f"unexpected schema_version: {taskbrief['schema_version']}")
    if taskbrief["decision_kind"] not in DECISION_KINDS:
        raise ValueError(f"invalid decision_kind: {taskbrief['decision_kind']}")

    evidence_items = taskbrief["evidence"]
    if not isinstance(evidence_items, list) or not evidence_items:
        raise ValueError("taskbrief.evidence must be a non-empty list")
    evidence_ids = set()
    for index, item in enumerate(evidence_items):
        _legacy_validate_evidence(item, f"evidence[{index}]")
        if item["id"] in evidence_ids:
            raise ValueError(f"duplicate evidence id: {item['id']}")
        evidence_ids.add(item["id"])

    task_frame = taskbrief["task_frame"]
    if task_frame.get("kind") not in TASK_KINDS:
        raise ValueError(f"invalid task kind: {task_frame.get('kind')}")
    if task_frame.get("goal") is not None:
        validate_statement(task_frame["goal"], evidence_ids, field_name="task_frame.goal")
    for field in ["target_objects", "user_constraints", "requested_outputs", "ambiguity_notes"]:
        validate_statement_list(task_frame.get(field, []), evidence_ids, field_name=f"task_frame.{field}")

    capability = taskbrief["matched_capability"]
    if capability is not None:
        _legacy_validate_capability(capability)

    validate_statement(taskbrief["goal"], evidence_ids, field_name="goal")
    for field in [
        "scope_in", "scope_out", "known_facts", "assumptions",
        "unresolved_unknowns", "execution_policy", "forbidden_actions",
        "verification_policy", "stop_conditions", "output_contract",
    ]:
        validate_statement_list(taskbrief[field], evidence_ids, field_name=field)


def validate_decision(decision: dict[str, Any], evidence_ids: set[str]) -> None:
    if decision.get("kind") not in DECISION_KINDS:
        raise ValueError(f"invalid decision kind: {decision.get('kind')}")
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        raise ValueError("decision.reason must be a non-empty string")
    _legacy_validate_confidence(decision.get("confidence", 1.0), "decision.confidence")
    if not isinstance(decision.get("questions", []), list):
        raise ValueError("decision.questions must be a list")
    for field in ["assumptions", "readonly_exploration_plan", "stop_conditions"]:
        validate_statement_list(decision.get(field, []), evidence_ids, field_name=f"decision.{field}")


def _legacy_validate_evidence(value: dict[str, Any], field_name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    if not isinstance(value.get("id"), str) or not value["id"]:
        raise ValueError(f"{field_name}.id must be a non-empty string")
    if value.get("source_kind") not in SOURCE_KINDS:
        raise ValueError(f"{field_name}.source_kind is invalid")
    if not isinstance(value.get("source_id"), str) or not value["source_id"]:
        raise ValueError(f"{field_name}.source_id must be a non-empty string")
    if value.get("quote") is not None and value.get("quote_hash") is not None:
        raise ValueError(f"{field_name} cannot contain both quote and quote_hash")
    _legacy_validate_confidence(value.get("confidence", 1.0), f"{field_name}.confidence")


def _legacy_validate_capability(value: dict[str, Any]) -> None:
    if value.get("id") not in CAPABILITY_IDS:
        raise ValueError(f"invalid capability id: {value.get('id')}")
    if value.get("task_kind") not in TASK_KINDS:
        raise ValueError(f"invalid capability task_kind: {value.get('task_kind')}")
    for field in [
        "triggers", "anti_triggers", "required_slots",
        "discoverable_slots", "must_ask_slots",
        "forbidden_actions", "verification_hints",
    ]:
        if not isinstance(value.get(field, []), list):
            raise ValueError(f"capability.{field} must be a list")


def _legacy_validate_confidence(value: Any, field_name: str) -> None:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    if value < 0 or value > 1:
        raise ValueError(f"{field_name} must be between 0 and 1")