from __future__ import annotations

import hashlib
from typing import Any

# ── schema versions ──────────────────────────────────────────
LEGACY_TASKBRIEF_VERSION = "taskbrief.v2.p0"

# Backward compat alias
SCHEMA_VERSION = "taskbrief.v2.p0"

SKILL_INPUT_CONTRACT_VERSION = "skillgate.skill_input_contract.v2"
SKILL_INPUT_CONTRACT_V3_VERSION = "skillgate.skill_input_contract.v3"
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

# ── v3 enums ─────────────────────────────────────────────────

SLOT_IMPORTANCE = {"required", "quality_amplifier", "optional"}

SLOT_ROLES = {
    "execution_input",
    "user_intent",
    "acceptance_criterion",
    "environment_fact",
    "permission",
    "output_preference",
}

VALUE_SCHEMA_TYPES = {
    "text",
    "path",
    "enum",
    "boolean",
    "command",
    "integer",
    "float",
}

VALUE_SCHEMA_CARDINALITIES = {"one", "many"}

ACQUISITION_STRATEGIES = {
    "ask_user",
    "discover_then_confirm",
    "discover_then_ask",
    "infer_then_confirm",
    "use_default_then_confirm",
}

CONFIRMATION_POLICIES = {
    "never",
    "always",
    "if_inferred",
    "if_discovered",
    "if_defaulted",
    "on_conflict",
}

MISSING_POLICIES_V3 = {"ask_user", "block", "skip"}

BENEFIT_LEVELS = {"high", "medium", "low", "none"}

ENFORCEMENT_LEVELS = {"downstream", "gate", "advisory"}

POLICY_CATEGORIES = {
    "execution_constraint",
    "safe_default",
    "forbidden_action",
}

GUARD_TYPES = {"safety_block", "authorization_required", "stop_condition"}


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
    if version == SKILL_INPUT_CONTRACT_V3_VERSION:
        # v3 input: convert to v2 view for the retained rules engine.
        # normalize_contract still returns v2 (the engine consumes v2);
        # it just needs to ACCEPT v3 input.
        return v3_to_v2_engine_view(contract)
    elif version == V1_VERSION:
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
#  NEW: SkillInputContract v3 (slots + execution_policies + activation_guards)
# ═══════════════════════════════════════════════════════════════


def build_skill_input_contract_v3(
    *,
    skill_id: str,
    skill_name: str,
    skill_version: str,
    skill_description: str,
    source_path: str | None = None,
    source_sha256: str | None = None,
    slots: list[dict[str, Any]] | None = None,
    execution_policies: list[dict[str, Any]] | None = None,
    activation_guards: list[dict[str, Any]] | None = None,
    contract_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a v3 SkillInputContract dict.

    v3 unifies the v2 slot sections (required_slots, ask_if_missing,
    discover_if_missing) into a single ``slots`` array annotated with
    ``importance`` / ``role`` / ``value_schema`` / ``acquisition`` /
    ``confirmation`` / ``missing`` / ``benefit``.  Always-active assertions
    (execution_constraints + safe_defaults + forbidden_actions) become
    ``execution_policies``; pre-activation blocks (safety_blocks +
    authorization_requirements + stop_conditions) become
    ``activation_guards``.
    """
    return {
        "schema_version": SKILL_INPUT_CONTRACT_V3_VERSION,
        "skill": {
            "id": skill_id,
            "name": skill_name,
            "version": skill_version,
            "description": skill_description,
            "source_path": source_path,
            "source_sha256": source_sha256,
        },
        "slots": slots or [],
        "execution_policies": execution_policies or [],
        "activation_guards": activation_guards or [],
        "contract_evidence": contract_evidence or [],
    }


# ── v2 → v3 migration helpers ────────────────────────────────


def _v3_role_for_slot(slot: dict[str, Any]) -> str:
    answer_source = slot.get("answer_source")
    slot_id = str(slot.get("id", "")).lower()
    if answer_source == "human":
        if "criteria" in slot_id or "success" in slot_id:
            return "acceptance_criterion"
        return "user_intent"
    if answer_source == "agent":
        return "environment_fact"
    if answer_source == "human_or_agent":
        return "execution_input"
    if answer_source == "authorization":
        return "permission"
    return "execution_input"


def _v3_value_schema_for_slot(slot: dict[str, Any]) -> dict[str, Any]:
    slot_id = str(slot.get("id", "")).lower()
    value_enum = slot.get("value_enum")
    if "scope" in slot_id or "path" in slot_id or "file" in slot_id:
        return {
            "type": "path",
            "cardinality": "many",
            "allows_multiple": True,
            "value_enum": None,
        }
    if "command" in slot_id:
        return {
            "type": "command",
            "cardinality": "one",
            "allows_multiple": False,
            "value_enum": None,
        }
    if value_enum:
        return {
            "type": "enum",
            "cardinality": "one",
            "allows_multiple": False,
            "value_enum": list(value_enum),
        }
    return {
        "type": "text",
        "cardinality": "one",
        "allows_multiple": False,
        "value_enum": None,
    }


def _v3_missing_policy(missing_policy: str | None) -> str:
    """Map a v2 missing_policy to a v3 missing.policy."""
    if missing_policy == "ask_user":
        return "ask_user"
    if missing_policy == "block":
        return "block"
    if missing_policy == "assume_default":
        return "skip"
    # discover_then_ask / discover_only → discovery handles it; ask on failure.
    return "ask_user"


def _v3_confirmation_policy(missing_policy: str | None) -> str:
    if missing_policy == "ask_user":
        return "never"  # already asked, no discovery
    if missing_policy == "discover_then_ask":
        return "if_discovered"
    if missing_policy == "discover_only":
        return "if_discovered"
    if missing_policy == "assume_default":
        return "if_defaulted"
    if missing_policy == "block":
        return "never"
    return "always"


def _v3_acquisition_strategy(
    importance: str, missing_policy: str | None
) -> str:
    """Infer a v3 acquisition.strategy from v2 importance + missing_policy."""
    if importance == "required":
        # required slot whose missing policy indicates discovery
        if missing_policy in ("discover_then_ask", "discover_only"):
            if missing_policy == "discover_then_ask":
                return "discover_then_ask"
            return "discover_then_confirm"
        if missing_policy == "assume_default":
            return "use_default_then_confirm"
    if importance == "quality_amplifier":
        if missing_policy in ("discover_then_ask", "discover_only"):
            if missing_policy == "discover_then_ask":
                return "discover_then_ask"
            return "discover_then_confirm"
        if missing_policy == "assume_default":
            return "use_default_then_confirm"
    return "ask_user"


def _v3_benefit(importance: str) -> dict[str, Any]:
    if importance == "required":
        return {"reduces_exploration": "high", "reduces_error_risk": "high"}
    if importance == "quality_amplifier":
        return {"reduces_exploration": "medium", "reduces_error_risk": "medium"}
    return {"reduces_exploration": "low", "reduces_error_risk": "low"}


def _v3_slot_from_v2(
    slot: dict[str, Any],
    *,
    importance: str,
    missing_policy: str | None,
    v2_section: str | None = None,
) -> dict[str, Any]:
    """Build a v3 slot dict from a v2 slot entry."""
    description = slot.get("text", "")
    strategy = _v3_acquisition_strategy(importance, missing_policy)
    value_schema = _v3_value_schema_for_slot(slot)
    v3_slot: dict[str, Any] = {
        "id": slot.get("id"),
        "description": description,
        "importance": importance,
        "role": _v3_role_for_slot(slot),
        "value_schema": value_schema,
        "acquisition": {
            "allowed_sources": _v3_allowed_sources(slot),
            "strategy": strategy,
            "resolver": None,
        },
        "confirmation": {
            "policy": _v3_confirmation_policy(missing_policy),
            "prompt": None,
        },
        "missing": {
            "policy": _v3_missing_policy(missing_policy),
        },
        "benefit": _v3_benefit(importance),
        "evidence_ids": list(slot.get("evidence_ids", [])),
        # v2 compat fields (used by the v3→v2 adapter):
        "answer_source": slot.get("answer_source"),
        "support": slot.get("support"),
        "confidence": slot.get("confidence", 1.0),
        "evidence_status": slot.get("evidence_status"),
    }
    # Preserve the originating v2 section so the v3→v2 adapter can route
    # the slot back to exactly the same section (lossless roundtrip). When
    # absent (hand-built v3 contracts), the adapter falls back to the
    # importance / acquisition.strategy heuristics.
    if v2_section is not None:
        v3_slot["v2_section"] = v2_section
    # Preserve any v2 missing_policy for the adapter's reverse mapping.
    if missing_policy is not None:
        v3_slot["missing_policy"] = missing_policy
    # Preserve value_enum at slot level too for round-trip fidelity.
    if slot.get("value_enum") is not None:
        v3_slot["value_enum"] = list(slot["value_enum"])
    return v3_slot


def _v3_allowed_sources(slot: dict[str, Any]) -> list[str]:
    answer_source = slot.get("answer_source")
    if answer_source == "human":
        return ["user"]
    if answer_source == "agent":
        return ["local_context"]
    if answer_source == "human_or_agent":
        return ["user", "local_context"]
    if answer_source == "authorization":
        return ["user"]
    return ["user"]


def _v3_policy_from_v2(
    slot: dict[str, Any], *, category: str
) -> dict[str, Any]:
    return {
        "id": slot.get("id"),
        "text": slot.get("text", ""),
        "enforcement": "advisory",
        "category": category,
        "evidence_ids": list(slot.get("evidence_ids", [])),
    }


def _v3_guard_from_v2(
    slot: dict[str, Any], *, guard_type: str
) -> dict[str, Any]:
    return {
        "id": slot.get("id"),
        "text": slot.get("text", ""),
        "type": guard_type,
        "evidence_ids": list(slot.get("evidence_ids", [])),
    }


def migrate_contract_v2_to_v3(contract_v2: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v2 SkillInputContract to v3.

    See the "v2→v3 migration mapping" table in the v0.4 refactor spec.
    ``safe_defaults`` become ``execution_policies`` (category=safe_default),
    NOT slots (oracle #8).
    """
    # Accept either raw v2 dicts or already-normalized v2.
    if contract_v2.get("schema_version") not in (
        SKILL_INPUT_CONTRACT_VERSION,
        V1_VERSION,
        None,
    ):
        # If it's already v3, return a normalized copy.
        if contract_v2.get("schema_version") == SKILL_INPUT_CONTRACT_V3_VERSION:
            return normalize_contract_v3(contract_v2)
        raise ValueError(
            f"migrate_contract_v2_to_v3 expects a v2 contract, got "
            f"{contract_v2.get('schema_version')}"
        )

    v2 = normalize_contract(contract_v2)

    slots: list[dict[str, Any]] = []
    seen_slot_ids: set[str] = set()

    def _add_slot(slot: dict[str, Any], *, importance: str, v2_section: str) -> None:
        sid = slot.get("id")
        if sid in seen_slot_ids:
            return
        seen_slot_ids.add(sid)
        slots.append(
            _v3_slot_from_v2(
                slot,
                importance=importance,
                missing_policy=slot.get("missing_policy"),
                v2_section=v2_section,
            )
        )

    # required_slots → importance=required
    for slot in v2.get("required_slots", []):
        _add_slot(slot, importance="required", v2_section="required_slots")

    # ask_if_missing → importance from support
    for slot in v2.get("ask_if_missing", []):
        support = slot.get("support")
        importance = (
            "required" if support in ("explicit", "recommended") else "quality_amplifier"
        )
        _add_slot(slot, importance=importance, v2_section="ask_if_missing")

    # discover_if_missing → importance from missing_policy
    for slot in v2.get("discover_if_missing", []):
        missing_policy = slot.get("missing_policy")
        if missing_policy in ("block", "discover_then_ask", "discover_only"):
            importance = "required"
        elif missing_policy == "assume_default":
            importance = "quality_amplifier"
        else:
            importance = "required"
        _add_slot(slot, importance=importance, v2_section="discover_if_missing")

    execution_policies: list[dict[str, Any]] = []
    for slot in v2.get("execution_constraints", []):
        execution_policies.append(
            _v3_policy_from_v2(slot, category="execution_constraint")
        )
    for slot in v2.get("safe_defaults", []):
        execution_policies.append(
            _v3_policy_from_v2(slot, category="safe_default")
        )
    for slot in v2.get("forbidden_actions", []):
        execution_policies.append(
            _v3_policy_from_v2(slot, category="forbidden_action")
        )

    activation_guards: list[dict[str, Any]] = []
    for slot in v2.get("safety_blocks", []):
        activation_guards.append(
            _v3_guard_from_v2(slot, guard_type="safety_block")
        )
    for slot in v2.get("authorization_requirements", []):
        activation_guards.append(
            _v3_guard_from_v2(slot, guard_type="authorization_required")
        )
    for slot in v2.get("stop_conditions", []):
        activation_guards.append(
            _v3_guard_from_v2(slot, guard_type="stop_condition")
        )

    return build_skill_input_contract_v3(
        skill_id=v2.get("skill_id", "unknown_skill"),
        skill_name=v2.get("skill_name", "Unknown Skill"),
        skill_version=v2.get("skill_version", "0.0.0"),
        skill_description=v2.get("skill_description", ""),
        source_path=v2.get("source_path"),
        source_sha256=v2.get("source_sha256"),
        slots=slots,
        execution_policies=execution_policies,
        activation_guards=activation_guards,
        contract_evidence=list(v2.get("contract_evidence", [])),
    )


# ── v3 → v2 engine adapter ───────────────────────────────────


def _v2_category_for_slot(slot_v3: dict[str, Any]) -> str:
    acquisition = slot_v3.get("acquisition") or {}
    strategy = acquisition.get("strategy")
    role = slot_v3.get("role")
    importance = slot_v3.get("importance")
    if strategy == "ask_user":
        return "human_askable"
    if strategy in (
        "discover_then_confirm",
        "discover_then_ask",
        "infer_then_confirm",
    ):
        return "agent_discoverable"
    if role == "permission":
        return "requires_authorization"
    if importance == "required":
        return "human_askable"
    return "human_askable"


def _v2_missing_policy_for_slot(slot_v3: dict[str, Any]) -> str:
    # Prefer the preserved v2 missing_policy for exact round-trip fidelity.
    if slot_v3.get("missing_policy") is not None:
        return slot_v3["missing_policy"]
    acquisition = slot_v3.get("acquisition") or {}
    strategy = acquisition.get("strategy")
    if strategy == "ask_user":
        return "ask_user"
    if strategy == "discover_then_confirm":
        return "discover_only"
    if strategy == "discover_then_ask":
        return "discover_then_ask"
    if strategy == "infer_then_confirm":
        return "assume_default"
    if strategy == "use_default_then_confirm":
        return "assume_default"
    missing = slot_v3.get("missing") or {}
    if missing.get("policy") == "block":
        return "block"
    return "ask_user"


def _v2_slot_from_v3(slot_v3: dict[str, Any]) -> dict[str, Any]:
    v2_slot: dict[str, Any] = {
        "id": slot_v3.get("id"),
        "text": slot_v3.get("description", ""),
        "category": _v2_category_for_slot(slot_v3),
    }
    # Carry v2 compat fields if present.
    for key in (
        "answer_source",
        "support",
        "confidence",
        "evidence_status",
        "missing_policy",
        "evidence_ids",
    ):
        if key in slot_v3 and slot_v3[key] is not None:
            v2_slot[key] = slot_v3[key]
    # Ensure a missing_policy is present for the engine.
    if "missing_policy" not in v2_slot:
        v2_slot["missing_policy"] = _v2_missing_policy_for_slot(slot_v3)
    # Carry value_enum if present.
    if slot_v3.get("value_enum") is not None:
        v2_slot["value_enum"] = list(slot_v3["value_enum"])
    return v2_slot


def v3_to_v2_engine_view(contract_v3: dict[str, Any]) -> dict[str, Any]:
    """Convert a v3 contract to a v2-shaped dict for the retained rules engine.

    The adapter runs at the engine entry point, NOT at registry time.
    CONTRACT_REGISTRY returns v3; this adapter converts just before
    consumption by ``rules.py`` (via ``normalize_contract``).
    """
    # Normalize v3 first so all sections are well-formed lists.
    v3 = normalize_contract_v3(contract_v3)

    skill = v3.get("skill") or {}

    required_slots: list[dict[str, Any]] = []
    ask_if_missing: list[dict[str, Any]] = []
    discover_if_missing: list[dict[str, Any]] = []
    required_ids: set[str] = set()
    ask_ids: set[str] = set()
    discover_ids: set[str] = set()

    # If the slot carries a v2_section provenance field (set by the v2→v3
    # migrator), route it back to exactly that section for a lossless
    # roundtrip. Otherwise fall back to the importance / acquisition.strategy
    # heuristics described in the spec.
    has_provenance = any(slot.get("v2_section") for slot in v3.get("slots", []))

    if has_provenance:
        for slot in v3.get("slots", []):
            sid = slot.get("id")
            section = slot.get("v2_section")
            v2_slot = _v2_slot_from_v3(slot)
            if section == "required_slots":
                if sid in required_ids:
                    continue
                required_slots.append(v2_slot)
                required_ids.add(sid)
            elif section == "ask_if_missing":
                if sid in ask_ids:
                    continue
                ask_if_missing.append(v2_slot)
                ask_ids.add(sid)
            elif section == "discover_if_missing":
                if sid in discover_ids:
                    continue
                discover_if_missing.append(v2_slot)
                discover_ids.add(sid)
            else:
                # Unknown provenance: fall back to importance-based routing.
                if slot.get("importance") == "required" and sid not in required_ids:
                    required_slots.append(v2_slot)
                    required_ids.add(sid)
    else:
        # First pass: required slots (importance=required).
        for slot in v3.get("slots", []):
            if slot.get("importance") == "required":
                required_slots.append(_v2_slot_from_v3(slot))
                required_ids.add(slot.get("id"))

        # Second pass: non-required slots routed by acquisition.strategy.
        for slot in v3.get("slots", []):
            sid = slot.get("id")
            if sid in required_ids:
                continue
            acquisition = slot.get("acquisition") or {}
            strategy = acquisition.get("strategy")
            if strategy == "ask_user":
                if sid in ask_ids:
                    continue
                ask_if_missing.append(_v2_slot_from_v3(slot))
                ask_ids.add(sid)
            elif strategy in (
                "discover_then_confirm",
                "discover_then_ask",
                "infer_then_confirm",
            ):
                if sid in discover_ids:
                    continue
                discover_if_missing.append(_v2_slot_from_v3(slot))
                discover_ids.add(sid)

    safe_defaults: list[dict[str, Any]] = []
    execution_constraints: list[dict[str, Any]] = []
    forbidden_actions: list[dict[str, Any]] = []
    for policy in v3.get("execution_policies", []):
        category = policy.get("category")
        v2_entry = {
            "id": policy.get("id"),
            "text": policy.get("text", ""),
            "category": "safe_assumption" if category == "safe_default"
            else ("blocked" if category == "forbidden_action" else "safe_assumption"),
        }
        if policy.get("evidence_ids"):
            v2_entry["evidence_ids"] = list(policy["evidence_ids"])
        if category == "safe_default":
            safe_defaults.append(v2_entry)
        elif category == "execution_constraint":
            execution_constraints.append(v2_entry)
        elif category == "forbidden_action":
            forbidden_actions.append(v2_entry)

    safety_blocks: list[dict[str, Any]] = []
    authorization_requirements: list[dict[str, Any]] = []
    stop_conditions: list[dict[str, Any]] = []
    for guard in v3.get("activation_guards", []):
        guard_type = guard.get("type")
        v2_entry = {
            "id": guard.get("id"),
            "text": guard.get("text", ""),
            "category": (
                "blocked" if guard_type == "safety_block"
                else ("requires_authorization" if guard_type == "authorization_required"
                      else "blocked")
            ),
        }
        if guard.get("evidence_ids"):
            v2_entry["evidence_ids"] = list(guard["evidence_ids"])
        if guard_type == "safety_block":
            safety_blocks.append(v2_entry)
        elif guard_type == "authorization_required":
            authorization_requirements.append(v2_entry)
        elif guard_type == "stop_condition":
            stop_conditions.append(v2_entry)

    return {
        "schema_version": SKILL_INPUT_CONTRACT_VERSION,
        "skill_id": skill.get("id", "unknown_skill"),
        "skill_name": skill.get("name", "Unknown Skill"),
        "skill_version": skill.get("version", "0.0.0"),
        "skill_description": skill.get("description", ""),
        "source_path": skill.get("source_path"),
        "source_sha256": skill.get("source_sha256"),
        "required_slots": required_slots,
        "ask_if_missing": ask_if_missing,
        "discover_if_missing": discover_if_missing,
        "safe_defaults": safe_defaults,
        "safety_blocks": safety_blocks,
        "authorization_requirements": authorization_requirements,
        "execution_constraints": execution_constraints,
        "forbidden_actions": forbidden_actions,
        "stop_conditions": stop_conditions,
        "contract_evidence": list(v3.get("contract_evidence", [])),
    }


# ── v3 normalization ─────────────────────────────────────────


def normalize_contract_v3(contract: dict[str, Any]) -> dict[str, Any]:
    """Normalize an arbitrary contract dict into a canonical v3 contract.

    Accepts v3 dicts (passes through with shape normalization), v2 dicts
    (migrated via :func:`migrate_contract_v2_to_v3`), and v1 dicts
    (migrated v1→v2→v3).  Returns a fully-populated v3 contract with every
    section present as a list and ``schema_version`` set to v3.
    """
    if not isinstance(contract, dict):
        raise ValueError("contract must be a dict")

    version = contract.get("schema_version")
    if version == SKILL_INPUT_CONTRACT_V3_VERSION:
        v3 = contract
    elif version in (SKILL_INPUT_CONTRACT_VERSION, None):
        v3 = migrate_contract_v2_to_v3(contract)
    elif version == V1_VERSION:
        v2 = migrate_contract_v1_to_v2(contract)
        v3 = migrate_contract_v2_to_v3(v2)
    else:
        raise ValueError(f"unexpected schema_version: {version}")

    def _list(name: str) -> list[dict[str, Any]]:
        v = v3.get(name, [])
        return list(v) if isinstance(v, list) else []

    skill = v3.get("skill") or {}
    if not isinstance(skill, dict):
        skill = {}

    return {
        "schema_version": SKILL_INPUT_CONTRACT_V3_VERSION,
        "skill": {
            "id": skill.get("id", "unknown_skill"),
            "name": skill.get("name", "Unknown Skill"),
            "version": skill.get("version", "0.0.0"),
            "description": skill.get("description", ""),
            "source_path": skill.get("source_path"),
            "source_sha256": skill.get("source_sha256"),
        },
        "slots": _list("slots"),
        "execution_policies": _list("execution_policies"),
        "activation_guards": _list("activation_guards"),
        "contract_evidence": _list("contract_evidence"),
    }


# ── v3 validation ────────────────────────────────────────────


def validate_skill_input_contract_v3(contract: dict[str, Any]) -> None:
    """Validate a v3 SkillInputContract. Raises ValueError on any problem."""
    if not isinstance(contract, dict):
        raise ValueError("contract must be a dict")
    if contract.get("schema_version") != SKILL_INPUT_CONTRACT_V3_VERSION:
        raise ValueError(
            f"unexpected schema_version: {contract.get('schema_version')} "
            f"(expected {SKILL_INPUT_CONTRACT_V3_VERSION})"
        )
    for key in (
        "schema_version",
        "skill",
        "slots",
        "execution_policies",
        "activation_guards",
        "contract_evidence",
    ):
        if key not in contract:
            raise ValueError(f"v3 SkillInputContract missing required key: {key}")

    skill = contract["skill"]
    if not isinstance(skill, dict):
        raise ValueError("v3 SkillInputContract.skill must be an object")
    for key in ("id", "name", "version", "description"):
        if key not in skill:
            raise ValueError(f"v3 SkillInputContract.skill missing {key}")
        val = skill[key]
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"v3 SkillInputContract.skill.{key} must be a non-empty string")

    for section in ("slots", "execution_policies", "activation_guards", "contract_evidence"):
        if not isinstance(contract[section], list):
            raise ValueError(f"v3 SkillInputContract.{section} must be a list")

    for idx, slot in enumerate(contract["slots"]):
        field = f"slots[{idx}]"
        if not isinstance(slot, dict):
            raise ValueError(f"{field} must be an object")
        for key in ("id", "description", "importance", "role"):
            if key not in slot:
                raise ValueError(f"{field} missing {key}")
        if slot["importance"] not in SLOT_IMPORTANCE:
            raise ValueError(f"{field}.importance invalid: {slot['importance']}")
        if slot["role"] not in SLOT_ROLES:
            raise ValueError(f"{field}.role invalid: {slot['role']}")
        value_schema = slot.get("value_schema")
        if value_schema is not None:
            if not isinstance(value_schema, dict):
                raise ValueError(f"{field}.value_schema must be an object")
            vs_type = value_schema.get("type")
            if vs_type is not None and vs_type not in VALUE_SCHEMA_TYPES:
                raise ValueError(f"{field}.value_schema.type invalid: {vs_type}")
            cardinality = value_schema.get("cardinality")
            if (
                cardinality is not None
                and cardinality not in VALUE_SCHEMA_CARDINALITIES
            ):
                raise ValueError(
                    f"{field}.value_schema.cardinality invalid: {cardinality}"
                )
        acquisition = slot.get("acquisition")
        if acquisition is not None:
            if not isinstance(acquisition, dict):
                raise ValueError(f"{field}.acquisition must be an object")
            strategy = acquisition.get("strategy")
            if strategy is not None and strategy not in ACQUISITION_STRATEGIES:
                raise ValueError(f"{field}.acquisition.strategy invalid: {strategy}")
        confirmation = slot.get("confirmation")
        if confirmation is not None:
            if not isinstance(confirmation, dict):
                raise ValueError(f"{field}.confirmation must be an object")
            policy = confirmation.get("policy")
            if policy is not None and policy not in CONFIRMATION_POLICIES:
                raise ValueError(f"{field}.confirmation.policy invalid: {policy}")
        missing = slot.get("missing")
        if missing is not None:
            if not isinstance(missing, dict):
                raise ValueError(f"{field}.missing must be an object")
            mpolicy = missing.get("policy")
            if mpolicy is not None and mpolicy not in MISSING_POLICIES_V3:
                raise ValueError(f"{field}.missing.policy invalid: {mpolicy}")

    for idx, policy in enumerate(contract["execution_policies"]):
        field = f"execution_policies[{idx}]"
        if not isinstance(policy, dict):
            raise ValueError(f"{field} must be an object")
        for key in ("id", "text", "category"):
            if key not in policy:
                raise ValueError(f"{field} missing {key}")
        if policy["category"] not in POLICY_CATEGORIES:
            raise ValueError(f"{field}.category invalid: {policy['category']}")
        enforcement = policy.get("enforcement")
        if (
            enforcement is not None
            and enforcement not in ENFORCEMENT_LEVELS
        ):
            raise ValueError(f"{field}.enforcement invalid: {enforcement}")

    for idx, guard in enumerate(contract["activation_guards"]):
        field = f"activation_guards[{idx}]"
        if not isinstance(guard, dict):
            raise ValueError(f"{field} must be an object")
        for key in ("id", "text", "type"):
            if key not in guard:
                raise ValueError(f"{field} missing {key}")
        if guard["type"] not in GUARD_TYPES:
            raise ValueError(f"{field}.type invalid: {guard['type']}")


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