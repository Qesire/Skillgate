"""Skill-contract-aware request analysis.

Instead of the old generic task classification + decision engine,
this module now analyzes a raw request against a specific SkillInputContract.

The decision flow:
1. Load the skill contract
2. Classify task kind (for skill selection if not explicit)
3. Check safety_blocks, forbidden_actions, and stop_conditions
4. Evaluate each contract slot against the request
5. Classify slots as human_askable/agent_discoverable/safe_assumption/blocked
6. Decide: ask_user / explore_first / assume_and_continue / compile_directly / block_unsafe
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .capabilities import CONTRACT_REGISTRY
from .constants import CLARIFICATION_MARKER
from .context import ContextResult
from .schema import (
    SKILL_INPUT_CONTRACT_VERSION,
    INPUT_SLOT_STATE_VERSION,
    build_input_slot_state,
    evidence,
    normalize_contract,
    statement,
)


# ── Analysis result ──────────────────────────────────────────


@dataclass(frozen=True)
class SkillAnalysis:
    """Result of analyzing a user request against a SkillInputContract."""
    skill_id: str
    skill_contract: dict[str, Any]
    task_kind: str
    decision_kind: str
    decision_reason: str
    confidence: float

    # Slot states (categorized)
    human_provided: list[dict[str, Any]]
    human_askable: list[dict[str, Any]]
    agent_discoverable: list[dict[str, Any]]
    safe_assumptions: list[dict[str, Any]]
    requires_authorization: list[dict[str, Any]]
    blocked: list[dict[str, Any]]

    # Execution constraints (always-active, from contract)
    execution_constraints: list[dict[str, Any]]

    # Forbidden actions (always propagated; block on explicit violation)
    forbidden_actions: list[dict[str, Any]]

    # Stop conditions (state-evaluated)
    stop_conditions: list[dict[str, Any]]

    # Low-confidence slots (confidence == 0.0, skipped in decision-making)
    low_confidence_slots: list[dict[str, Any]]

    # Human-facing questions
    questions: list[str]

    # Legacy fields for backward compat
    goal: str
    assumptions: list[str]
    readonly_exploration_plan: list[str]
    forbidden_actions_legacy: list[str]
    verification_policy: list[str]
    unresolved_unknowns: list[str]


# ── Main entry point ─────────────────────────────────────────


def analyze_against_skill(
    raw_request: str,
    *,
    skill_id: str | None = None,
    context: ContextResult | None = None,
) -> SkillAnalysis:
    """Analyze a user request against a specific skill contract.

    Args:
        raw_request: The user's raw request.
        skill_id: Explicit skill to target (e.g., 'bug_fix'). Required —
            auto-classification has been removed.
        context: Optional pre-discovered context for discovery hints.

    Returns:
        SkillAnalysis with categorized slot states and decision.
    """
    if skill_id is None:
        raise ValueError("skill_id is required")
    task_kind = skill_id

    lower = raw_request.lower()

    contract = CONTRACT_REGISTRY.get(skill_id)
    contract = normalize_contract(contract)  # always canonical v2

    # P1: Quarantine low-confidence contract slots BEFORE any decision logic.
    # A slot with confidence=0.0 or evidence_status=unverified is moved out of
    # every contract section so it cannot pollute safe-default coverage, block
    # matching, authorization routing, or stop evaluation.  This prevents the
    # decision-pollution pattern where a low-confidence safe default covers an
    # authorization requirement, then gets deleted, leaving the requirement
    # silently unrecovered.
    quarantined_contract_slots: list[dict[str, Any]] = []

    def _is_actionable(slot: dict[str, Any]) -> bool:
        # A slot is quarantined only when its confidence is explicitly zeroed
        # (the fail-closed signal set by _verify_quotes when evidence cannot be
        # confirmed).  evidence_status=unverified alone is metadata, not a
        # hard quarantine — many legitimately-discovered slots have no quote
        # yet are still actionable at confidence > 0.
        if slot.get("confidence", 1.0) == 0.0:
            return False
        return True

    def _filter_section(name: str) -> list[dict[str, Any]]:
        section = contract.get(name, [])
        actionable = [s for s in section if _is_actionable(s)]
        quarantined_contract_slots.extend(s for s in section if not _is_actionable(s))
        return actionable

    for sec in ("required_slots", "ask_if_missing", "discover_if_missing",
                "safe_defaults", "safety_blocks", "authorization_requirements",
                "execution_constraints", "forbidden_actions", "stop_conditions"):
        contract[sec] = _filter_section(sec)

    # Read execution constraints (always-active constraints from the contract)
    exec_constraints = contract.get("execution_constraints", [])
    forbidden_actions = contract.get("forbidden_actions", [])
    stop_conditions = contract.get("stop_conditions", [])

    # 2. Safety blocks: a dangerous *request* blocks immediately.
    #    forbidden_actions and stop_conditions are NOT keyword-matched here —
    #    they have distinct runtime semantics handled below.
    block_reason = _check_safety_blocks(contract.get("safety_blocks", []), raw_request)
    if block_reason:
        return _make_decision(
            skill_id=skill_id,
            contract=contract,
            task_kind=task_kind,
            decision_kind="block_unsafe",
            reason=block_reason,
            confidence=0.95,
            execution_constraints=exec_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
            blocked=[_slot_entry_to_state(b, "blocked") for b in contract.get("safety_blocks", [])],
            human_provided=[],
        )

    # 3. Evaluate slots against the request
    human_provided: list[dict[str, Any]] = []
    human_askable: list[dict[str, Any]] = []
    agent_discoverable: list[dict[str, Any]] = []
    safe_assumptions: list[dict[str, Any]] = []
    requires_authorization: list[dict[str, Any]] = []
    blocked_slots: list[dict[str, Any]] = []

    # Required slots — missing_policy is NOT checked here because required
    # slots are, by definition, required; their category already encodes the
    # intended handling.  missing_policy only governs the ask_if_missing /
    # authorization_requirements / discover_if_missing sections.
    for slot in contract["required_slots"]:
        state = _evaluate_slot(slot, raw_request, context, is_required=True)
        _assign_slot(state, human_provided, human_askable, agent_discoverable,
                     safe_assumptions, requires_authorization, blocked_slots)

    # Safe defaults (process first so auth-coverage check can use them)
    for slot in contract["safe_defaults"]:
        safe_assumptions.append(_build_slot_state(slot, raw_request, "safe_assumption"))

    # Authorization requirements — missing_policy takes STRICT PRIORITY over
    # safe-default coverage.  A slot that declares missing_policy=block must
    # block even if a safe default would have covered it.
    for slot in contract.get("authorization_requirements", []):
        policy = slot.get("missing_policy")
        if _slot_is_filled(slot, raw_request, context):
            human_provided.append(_build_slot_state(slot, raw_request, "known"))
        elif policy == "block":
            blocked_slots.append(_rebuild_state_for_policy(slot, raw_request, "blocked"))
        elif policy == "assume_default":
            safe_assumptions.append(_rebuild_state_for_policy(slot, raw_request, "safe_assumption"))
        elif _is_covered_by_safe_default(slot, safe_assumptions):
            safe_assumptions.append(_build_slot_state(slot, raw_request, "safe_assumption"))
        else:
            requires_authorization.append(_build_slot_state(slot, raw_request, "requires_authorization"))

    # Ask-if-missing slots — missing_policy takes STRICT PRIORITY over
    # safe-default coverage.
    for slot in contract["ask_if_missing"]:
        policy = slot.get("missing_policy")
        if _slot_is_filled(slot, raw_request, context):
            human_provided.append(_build_slot_state(slot, raw_request, "known"))
        elif policy == "block":
            blocked_slots.append(_rebuild_state_for_policy(slot, raw_request, "blocked"))
        elif policy == "assume_default":
            safe_assumptions.append(_rebuild_state_for_policy(slot, raw_request, "safe_assumption"))
        elif policy in ("discover_only", "discover_then_ask"):
            # Both discover policies route to agent_discoverable at compile
            # time.  discover_then_ask differs only post-discovery (ask the
            # user if discovery fails), which is a runtime state transition
            # not representable at compile time.
            agent_discoverable.append(_rebuild_state_for_policy(slot, raw_request, "agent_discoverable"))
        elif _is_covered_by_safe_default(slot, safe_assumptions):
            # Slot (auth or human-askable) covered by a safe default.
            # For requires_authorization → downgrade to safe_assumption.
            # For human_askable → don't ask; safe default already encodes the answer.
            if slot["category"] == "requires_authorization":
                safe_assumptions.append(_build_slot_state(slot, raw_request, "safe_assumption"))
            # else: human_askable covered → skip (don't add to any list)
        else:
            _assign_slot(
                _build_slot_state(slot, raw_request, _effective_category(slot)),
                human_provided, human_askable, agent_discoverable,
                safe_assumptions, requires_authorization, blocked_slots,
            )

    # Agent-discoverable slots
    for slot in contract["discover_if_missing"]:
        policy = slot.get("missing_policy")
        if _slot_is_filled(slot, raw_request, context):
            human_provided.append(_build_slot_state(slot, raw_request, "known"))
        elif policy == "block":
            blocked_slots.append(_rebuild_state_for_policy(slot, raw_request, "blocked"))
        elif policy == "assume_default":
            safe_assumptions.append(_rebuild_state_for_policy(slot, raw_request, "safe_assumption"))
        else:
            agent_discoverable.append(_build_slot_state(slot, raw_request, "agent_discoverable"))

    # Check legacy high-ambiguity patterns — if the request is too vague even
    # though contract slots look satisfied, force ask_user.
    must_ask = _must_ask_from_request(raw_request, task_kind)
    if must_ask and not human_askable and not requires_authorization:
        human_askable.append(build_input_slot_state(
            name="ambiguity_check",
            description=must_ask,
            category="human_askable",
            status="human_askable",
            answer_source="human",
            support="recommended",
            question=must_ask,
            handling_reason="Request matched a high-ambiguity legacy pattern.",
        ))

    human_provided = _dedupe_slot_states(human_provided)
    human_askable = _dedupe_slot_states(human_askable)
    agent_discoverable = _dedupe_slot_states(agent_discoverable)
    safe_assumptions = _dedupe_slot_states(safe_assumptions)
    requires_authorization = _dedupe_slot_states(requires_authorization)
    blocked_slots = _dedupe_slot_states(blocked_slots)

    # forbidden_actions are always propagated downstream as constraints, but
    # they also block when the user EXPLICITLY requests the forbidden action
    # (not merely mentions a related word).  This distinguishes "propagate"
    # from "block" semantics.
    forbidden_violation = _check_forbidden_action_violation(forbidden_actions, raw_request)
    if forbidden_violation:
        return _make_decision(
            skill_id, contract, task_kind, "block_unsafe",
            forbidden_violation, 0.95,
            blocked=[_slot_entry_to_state(fa, "blocked") for fa in forbidden_actions],
            human_provided=human_provided,
            execution_constraints=exec_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
            safe_assumptions=safe_assumptions,
        )

    # stop_conditions are evaluated against slot STATE, not keyword search.
    # A stop condition fires when its predicate is true given the current
    # slot categorization (e.g. a critical required slot is unanswered AND the
    # request is too vague to recover it).
    stop_reason = _evaluate_stop_conditions(
        stop_conditions, human_askable, requires_authorization, raw_request, task_kind,
    )
    if stop_reason:
        return _make_decision(
            skill_id, contract, task_kind, "block_unsafe",
            stop_reason, 0.9,
            blocked=[_slot_entry_to_state(sc, "blocked") for sc in stop_conditions],
            human_provided=human_provided,
            execution_constraints=exec_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
            safe_assumptions=safe_assumptions,
        )

    # missing_policy is now interpreted STRICT-PRIORITY during slot
    # evaluation above (before safe-default coverage), so there is no
    # post-hoc rerouting here.  This avoids the state/list inconsistency
    # where a slot's category/status kept old values after being moved.

    # P2: Fail-closed evidence verification
    low_confidence_slots: list[dict[str, Any]] = []

    for slot_list in (blocked_slots, human_askable, requires_authorization,
                       agent_discoverable, safe_assumptions):
        for slot in slot_list[:]:
            if slot.get("confidence") == 0.0:
                slot_list.remove(slot)
                low_confidence_slots.append(slot)

    # Merge the contract-level quarantined slots (filtered out before any
    # decision logic) into low_confidence_slots so they are still visible in
    # the analysis output — they just never participated in the decision.
    for slot in quarantined_contract_slots:
        low_confidence_slots.append(_slot_entry_to_state(slot, slot.get("category", "blocked")))

    # Re-deduplicate after re-routing
    human_askable = _dedupe_slot_states(human_askable)
    agent_discoverable = _dedupe_slot_states(agent_discoverable)
    safe_assumptions = _dedupe_slot_states(safe_assumptions)
    requires_authorization = _dedupe_slot_states(requires_authorization)
    blocked_slots = _dedupe_slot_states(blocked_slots)
    low_confidence_slots = _dedupe_slot_states(low_confidence_slots)

    low_conf_note = ""
    if low_confidence_slots:
        names = [s.get("name", s.get("id", "?")) for s in low_confidence_slots]
        low_conf_note = f" (low-confidence slots skipped: {', '.join(names)})"

    # 4. Make decision — exact §7 ordering
    # §7: block → auth → human_askable → discover → assume → compile

    if blocked_slots:
        reason = "One or more blocking conditions are triggered."
        if low_conf_note:
            reason += " " + low_conf_note.strip()
        return _make_decision(
            skill_id, contract, task_kind, "block_unsafe",
            reason,
            0.95, blocked=blocked_slots,
            human_provided=human_provided,
            execution_constraints=exec_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
            safe_assumptions=safe_assumptions,
            low_confidence_slots=low_confidence_slots,
        )

    if requires_authorization:
        questions = _dedupe_texts([s["text"] for s in requires_authorization])
        reason = "Authorization is required before proceeding."
        if low_conf_note:
            reason += " " + low_conf_note.strip()
        return _make_decision(
            skill_id, contract, task_kind, "ask_user",
            reason,
            0.90, requires_authorization=requires_authorization, questions=questions,
            human_provided=human_provided,
            execution_constraints=exec_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
            safe_assumptions=safe_assumptions,
            low_confidence_slots=low_confidence_slots,
        )

    if human_askable:
        questions = _dedupe_texts([s["text"] for s in human_askable])
        reason = "Some required inputs need to be provided by the user."
        if low_conf_note:
            reason += " " + low_conf_note.strip()
        return _make_decision(
            skill_id, contract, task_kind, "ask_user",
            reason,
            0.85, human_askable=human_askable, questions=questions,
            human_provided=human_provided,
            execution_constraints=exec_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
            safe_assumptions=safe_assumptions,
            low_confidence_slots=low_confidence_slots,
        )

    if agent_discoverable:
        exploration = [f"Discover: {s['text']}" for s in agent_discoverable]
        reason = "Important context can be discovered through read-only local inspection."
        if low_conf_note:
            reason += " " + low_conf_note.strip()
        return _make_decision(
            skill_id, contract, task_kind, "explore_first",
            reason,
            0.84, agent_discoverable=agent_discoverable, readonly_exploration_plan=exploration,
            human_provided=human_provided,
            execution_constraints=exec_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
            safe_assumptions=safe_assumptions,
            low_confidence_slots=low_confidence_slots,
        )

    if safe_assumptions:
        reason = "Only low-risk gaps remain; conservative assumptions are recorded."
    else:
        reason = "All required inputs are satisfied."
    if low_conf_note:
        reason += " " + low_conf_note.strip()

    return _make_decision(
        skill_id, contract, task_kind,
        "assume_and_continue" if safe_assumptions else "compile_directly",
        reason, 0.82, safe_assumptions=safe_assumptions,
        human_provided=human_provided,
        execution_constraints=exec_constraints,
        forbidden_actions=forbidden_actions,
        stop_conditions=stop_conditions,
        low_confidence_slots=low_confidence_slots,
    )


# ── Helpers ──────────────────────────────────────────────────


def _check_safety_blocks(safety_blocks: list[dict[str, Any]], raw_request: str) -> str | None:
    """Check whether a safety_block condition matches the *request*.

    safety_blocks describe dangerous requests themselves (credential/secret
    access, production mutation, destructive ops, payment+secret).  A match
    blocks immediately.  This is the only block category evaluated by
    request-text matching; forbidden_actions and stop_conditions have their
    own semantics.

    Fail-closed on evidence: a safety_block whose ``confidence`` is 0.0 or
    whose ``evidence_status`` is ``unverified`` is **skipped** — an
    unverified safety condition must not block execution.  Built-in policy
    defaults carry no confidence (defaulting to 1.0 / no evidence_status),
    so they are always active.
    """
    lower = raw_request.lower()
    for condition in safety_blocks:
        # Fail-closed evidence: skip unverified safety conditions.
        if condition.get("confidence", 1.0) == 0.0:
            continue
        if condition.get("evidence_status") == "unverified":
            continue
        text = condition.get("text", "").lower()
        cid = condition.get("id", "").lower()
        if ("credential" in text or "secret" in text or "exfiltration" in text
                or "credential" in cid or "exfil" in cid) and _contains(
            lower, ["密钥", "secret", "exfiltrate", "泄露密钥", "dump", "token", "exfil"]
        ):
            return "The request may involve credential or secret access."
        if "production" in text and _contains(lower, ["生产数据库", "production database", "生产环境"]):
            return "The request may involve production data mutation."
        if "destructive" in text and _contains(lower, ["删除", "drop", "rm -rf", "清空"]):
            return "The request may involve destructive operations."
        if "payment" in text and _contains(
            lower, ["密钥", "secret", "api key", "credential", "stripe", "付款", "payment"]
        ):
            return "Payment integration with secret usage requires explicit safe sandbox setup."
    return None


def _check_forbidden_action_violation(
    forbidden_actions: list[dict[str, Any]], raw_request: str
) -> str | None:
    """Block only when the user EXPLICITLY requests a forbidden action.

    forbidden_actions are normally propagated downstream as constraints (they
    forbid the agent from doing X).  They become a hard block only when the
    user's request explicitly asks for the forbidden thing — not when a
    related word merely appears.  This requires a strong, intentional signal:
    an action verb tied to the forbidden action's object.
    """
    lower = raw_request.lower()
    # Map forbidden_action id/text fragments to explicit-request patterns.
    # Each pattern needs BOTH a verb and an object to avoid false positives.
    explicit_patterns = [
        # "Fabricating unsupported project claims" → user asks to invent metrics/claims
        (["fabricat", "claim", "metric", "adoption", "benchmark"],
         ["invent", "fabricate", "make up", "fake", "夸大", "编造", "虚构", "捏造"],
         "fabricate claims"),
        # Generic "never introduce dependencies" style
        (["dependenc", "引入依赖"],
         ["add dependenc", "introduce dependenc", "引入依赖", "加依赖", "添加依赖"],
         "introduce dependencies"),
    ]
    for fa in forbidden_actions:
        text = fa.get("text", "").lower()
        fid = fa.get("id", "").lower()
        for object_kws, verb_kws, _label in explicit_patterns:
            if any(o in text or o in fid for o in object_kws) and any(
                v in lower for v in verb_kws
            ):
                return (
                    f"The request explicitly requests a forbidden action: "
                    f"{fa.get('text', fid).strip()}."
                )
    return None


def _evaluate_stop_conditions(
    stop_conditions: list[dict[str, Any]],
    human_askable: list[dict[str, Any]],
    requires_authorization: list[dict[str, Any]],
    raw_request: str,
    task_kind: str,
) -> str | None:
    """Evaluate stop_conditions against slot STATE (not keyword search).

    A stop condition fires when its predicate is true given the current slot
    categorization.  Known predicates:
      - unclear_intent / "fundamentally unclear": the request is *actionably
        uninterpretable* — too short to even determine what to ask, AND the
        task-direction required slot is unanswered.  A merely vague request
        that can still be clarified (e.g. "优化一下") does NOT fire this; it
        should route to ask_user instead.
    """
    if not stop_conditions:
        return None

    ha_names = {s.get("name", s.get("id", "")) for s in human_askable}

    for sc in stop_conditions:
        cid = sc.get("id", "").lower()
        text = sc.get("text", "").lower()
        is_unclear = "unclear" in cid or "unclear" in text or "fundamentally" in text
        if not is_unclear:
            continue
        # Predicate: task direction is unanswered AND the request is
        # actionably uninterpretable (no recoverable task signal at all).
        direction_unanswered = any(
            n in ha_names for n in ("task_direction", "task_direction_primary", "scope")
        )
        uninterpretable = _is_uninterpretable_request(raw_request)
        if direction_unanswered and uninterpretable:
            return (
                f"Stop condition triggered: {sc.get('text', cid).strip()} "
                "(task direction is unanswerable and the request has no recoverable signal)."
            )
    return None


def _is_uninterpretable_request(raw_request: str) -> bool:
    """Detect requests so terse they cannot even drive a clarifying question.

    This is intentionally a HIGH bar: only near-empty requests or pure
    pleasantries qualify.  Vague-but-recoverable requests like "优化一下"
    ("optimize it") still have enough signal to ask "optimize what?", so they
    route to ask_user rather than block.
    """
    text = raw_request.strip().lower()
    if not text:
        return True
    # Pure pleasantries / non-requests with no task verb.
    non_requests = {
        "hello", "hi", "hey", "thanks", "ok", "okay", "yes", "no",
        "你好", "谢谢", "好的", "嗯",
    }
    return text in non_requests


def _is_vague_request(raw_request: str) -> bool:
    """Detect requests too vague to recover a task direction from."""
    text = raw_request.strip().lower()
    vague_set = {
        "优化一下", "清理一下项目", "你看着办", "you decide", "help", "do something",
        "fix it", "看看", "处理一下", "搞一下",
    }
    return text in vague_set or len(text) <= 6


def _ensure_modern_contract_sections(contract: dict[str, Any]) -> None:
    """Backward compat: if old contract only has 'block_if' but no new fields,
    copy block_if items to 'safety_blocks'. Also ensure empty lists for missing new fields."""
    # If the contract was built by the old build_skill_input_contract,
    # it will have "block_if" but may not have the five new sections.
    old_block = contract.get("block_if", [])
    if old_block and not contract.get("safety_blocks"):
        contract["safety_blocks"] = list(old_block)
    for key in ("safety_blocks", "authorization_requirements",
                "execution_constraints", "forbidden_actions", "stop_conditions"):
        if key not in contract:
            contract[key] = []


def _effective_category(slot: dict[str, Any]) -> str:
    """Get effective category considering answer_source.

    A slot with answer_source='human_or_agent' and category='human_askable'
    is treated as agent_discoverable when missing — the agent should try to
    discover it first, and only ask the user for minimal evidence if discovery
    fails. This implements the Human-Answerability Filter principle:
    '优先 agent 探索，失败后问最小证据'.
    """
    category = slot.get("category", "human_askable")
    answer_source = slot.get("answer_source", "")
    if answer_source == "human_or_agent" and category == "human_askable":
        return "agent_discoverable"
    return category


def _evaluate_slot(
    slot: dict[str, Any],
    raw_request: str,
    context: ContextResult | None,
    is_required: bool,
) -> dict[str, Any]:
    """Evaluate whether a slot is filled by the request or context."""
    slot_id = slot["id"]
    text = slot["text"]
    category = _effective_category(slot)
    answer_source = slot.get("answer_source", slot["category"])
    support = slot.get("support", "recommended")
    missing_policy = slot.get("missing_policy")
    confidence = slot.get("confidence", 1.0)

    # Check if request provides the info (with value binding)
    binding = _bind_slot(slot, raw_request, context)
    if binding["filled"]:
        return build_input_slot_state(
            name=slot_id,
            description=text,
            category="known",
            status="known",
            value=binding["value"],
            answer_source=answer_source,
            support=support,
            handling_reason="Request or context provides this information." if not binding["conflict"]
                else "Conflicting values detected; value is null pending user disambiguation.",
            confidence=binding["confidence"],
            missing_policy=missing_policy,
            value_source=binding["source"],
            value_source_span=binding["source_span"],
            conflict=binding["conflict"],
        )

    # Slot is NOT filled — missing_policy takes strict priority over the
    # category-based default routing.
    policy = slot.get("missing_policy")
    if policy == "block":
        return build_input_slot_state(
            name=slot_id, description=text, category="blocked", status="blocked",
            answer_source=answer_source, support=support,
            handling_reason=f"Slot '{slot_id}' is blocked by missing_policy.",
            confidence=confidence, missing_policy=missing_policy,
        )
    if policy == "assume_default":
        return build_input_slot_state(
            name=slot_id, description=text, category="safe_assumption", status="safe_assumption",
            answer_source=answer_source, support=support, assumption=text,
            handling_reason=f"Slot '{slot_id}' assumes default by missing_policy.",
            confidence=confidence, missing_policy=missing_policy,
        )
    if policy in ("discover_only", "discover_then_ask"):
        on_fail = "ask_user" if policy == "discover_then_ask" else "report_unresolved"
        return build_input_slot_state(
            name=slot_id, description=text, category="agent_discoverable", status="agent_discoverable",
            answer_source=answer_source, support=support,
            handling_reason=f"Slot '{slot_id}' routed to discovery by missing_policy.",
            confidence=confidence, missing_policy=missing_policy,
            on_discovery_failure=on_fail,
        )
    # No overriding policy (or ask_user) — fall back to category-based routing.
    return build_input_slot_state(
        name=slot_id,
        description=text,
        category=category,
        status=category,
        answer_source=answer_source,
        support=support,
        question=text if category in ("human_askable", "requires_authorization") else None,
        assumption=text if category == "safe_assumption" else None,
        handling_reason=f"Slot '{slot_id}' is {category} (support: {support}, answer: {answer_source}).",
        confidence=confidence,
        missing_policy=missing_policy,
    )


def _slot_is_filled(slot: dict[str, Any], raw_request: str, context: ContextResult | None) -> bool:
    """Heuristic: does the raw request or context already fill this slot?

    Each skill contract defines slots that describe what information the
    agent needs.  This function uses lightweight pattern matching (as
    opposed to an LLM call) to decide whether a slot is already filled
    by the user's raw request or the local context.

    The matching is intentionally generous: false-positives here push a
    slot from 'human_askable' → 'known', which means the request can
    proceed to 'explore_first' / 'compile' instead of blocking on the
    user.  False-negative detection (missing a fill) is worse because it
    causes an unnecessary interaction round-trip.
    """
    slot_id = slot["id"]
    slot_text = slot.get("text", "")
    lower = raw_request.lower()

    if _clarification_answers_by_question(raw_request).get(slot_text):
        return True

    # ── Test / build infrastructure ──────────────────────────
    if slot_id in ("test_framework", "test_command", "reproduction_command",
                   "smallest_test_command"):
        if _contains(lower, ["pytest", "cargo test", "npm test", "npm run",
                              "go test", "jest", "cargo build", "make"]):
            return True
        if context:
            for _, fact in context.facts():
                if "pytest" in fact.lower() or "test" in fact.lower():
                    return True

    if slot_id == "package_manager":
        return _contains(lower, ["npm", "pip", "cargo", "go mod", "yarn",
                                  "pnpm", "uv", "poetry", "maven", "gradle"])

    # ── File / target slots ──────────────────────────────────
    #   target_scope, target_document, review_target, failing_test_target
    if slot_id in ("target_scope", "target_document", "review_target",
                   "failing_test_target"):
        # Explicit file paths: src/foo.py, lib/bar.rs, etc.
        if re.search(r"(?<![\w./-])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+",
                       raw_request):
            return True
        # Specific test function names: test_parser_escape, etc.
        if re.search(r"\btest_\w+", lower):
            return True
        # Document names
        if _contains(lower, ["readme", "安装说明", "贡献指南", "项目介绍",
                              "changelog", "contributing"]):
            return True
        # Review / diff targets
        if _contains(lower, ["pr ", "pull request", "diff", "最近改动",
                              "改动", "最近"]):
            return True
        # Whole-repo / project references (broad but intentional)
        if _contains(lower, ["仓库", "项目", "repo", "这个库",
                              "凭据泄露"]):
            return True
        # File-like mentions (suffix or keyword)
        if _contains(lower, [".py", ".rs", ".ts", ".js", ".go",
                              "模块", "文件", "parser"]):
            return True

    # ── Symptom / focus / goal slots ─────────────────────────
    if slot_id == "failure_symptom":
        return _contains(lower, [
            "报错", "错误", "空白", "超时", "挂了", "异常", "不对",
            "error", "bug", "crash", "failed", "defect", "broken",
            "类型错误", "编译错误",
        ])

    if slot_id == "review_focus":
        return _contains(lower, [
            "安全", "凭据泄露", "数据丢失", "性能", "正确性",
            "security", "correctness", "performance", "data loss",
            "泄漏", "泄露",
        ])

    if slot_id == "refactor_goal":
        return _contains(lower, [
            "整理", "重复", "重构", "重新组织", "改名", "改善",
            "优化结构", "refactor", "restructure",
        ])

    if slot_id == "feature_behavior":
        return _contains(lower, [
            "加", "添加", "导出", "实现", "接入", "补", "创建",
            "add", "implement", "export",
        ])

    if slot_id == "expected_behavior":
        # Only fill when a concrete expected outcome is described.
        return _contains(lower, [
            "应该", "期望", "预期", "expected", "should",
            "显示用户面板",
        ])

    # ── Constraint / scope slots ─────────────────────────────
    if slot_id == "allowed_change_scope":
        return _contains(lower, [
            "不能改", "不能修改", "不要改", "保持", "不改",
            "定位并修复", "修复", "不能", "不允许", "不要引入",
        ])

    if slot_id == "behavior_preservation":
        return _contains(lower, [
            "保持行为不变", "不变", "行为不变", "保持",
            "preserve", "不改变功能",
        ])

    if slot_id == "severity_bar":
        return _contains(lower, [
            "只给", "只报告", "有没有", "高置信", "只有",
            "先不要改", "不要改", "只", "仅",
        ])

    if slot_id == "data_contract":
        return _contains(lower, [
            "csv", "json", "yaml", "xml", "筛选结果", "当前",
            "字段", "格式",
        ])

    # ── Audience / surface slots ─────────────────────────────
    if slot_id == "audience":
        return _contains(lower, [
            "开发", "用户", "安装", "贡献指南", "贡献",
            "developer", "user", "contributor",
        ])

    if slot_id == "target_surface":
        return _contains(lower, [
            "页面", "列表页", "按钮", "组件", "界面",
            "cli", "api", "ui", "screen", "page",
        ])

    # ── Claim / verification slots ───────────────────────────
    if slot_id == "allowed_claims":
        return _contains(lower, [
            "命令说明", "安装说明", "贡献指南", "不要写",
            "开发", "测试命令",
        ])

    if slot_id == "verification_expectation":
        return _contains(lower, [
            "npm run", "cargo build", "cargo test", "pytest",
            "make", "go build", "编译", "构建", "build",
        ])

    # ── Action / direction slots ─────────────────────────────
    if slot_id == "repair_scope":
        return _contains(lower, [
            "改断言", "修", "fix", "改", "修改",
        ])

    # ── Ask-if-missing: permissive detection ─────────────────
    if slot_id == "may_modify_tests":
        return _contains(lower, ["改测试", "修改测试", "改断言",
                                  "更新 snapshot"])
    if slot_id == "may_change_public_api":
        return _contains(lower, ["公开类型", "公开 api", "public api",
                                  "公开接口", "不能改公开"])
    if slot_id == "reproduction_evidence":
        return _contains(lower, ["复现", "reproduce", "reproduction",
                                  "重现", "登录后页面空白",
                                  "登录后页面"])
    if slot_id == "permission_to_modify":
        return _contains(lower, ["审核", "确认", "reviewed"])

    if slot_id == "may_update_snapshots":
        return _contains(lower, ["snapshot", "快照", "更新 snapshot",
                                  "更新快照", "update snapshot"])

    if slot_id in ("may_change_test_intent", "source_vs_test_fix"):
        return _contains(lower, ["改断言", "改测试", "不要动测试",
                                  "修源码", "修测试"])

    if slot_id == "audience_when_broad":
        # Filled when the target_document and audience are already clear
        # (they narrow the audience enough that this slot is redundant).
        return _contains(lower, ["开发", "安装说明", "贡献指南"])

    if slot_id in ("target_surface_when_missing", "dependency_policy"):
        # Filled when the primary slots already specify enough.
        return _contains(lower, ["页面", "列表页", "按钮", "组件"])

    # ── Generic unknown slots ────────────────────────────────
    if slot_id in ("task_direction", "task_direction_primary"):
        return _contains(lower, [
            "优化", "清理", "fix", "review", "refactor", "document",
            "implement", "feature", "bug", "重构", "审查", "文档",
        ])

    # Generic fallback for custom skill slots not in the hardcoded list.
    # CONSERVATIVE: a single common word matching is a false-positive hazard
    # (e.g. slot "What output format?" matching request "What should I do?"
    # on the word "what").  We only treat a custom slot as filled when there
    # is a strong signal: either the slot's id stem appears in the request,
    # or a multi-word phrase from the slot description appears verbatim.
    # Single short common words are explicitly excluded.
    slot_text = slot.get("text", "")
    if slot_text:
        lower_req = raw_request.lower()
        # 1) slot id stem present in the request (strong signal).
        id_stem = re.sub(r"[_\W]+", " ", slot_id).strip()
        if id_stem:
            id_tokens = [t for t in id_stem.split() if len(t) > 2]
            if id_tokens and all(t in lower_req for t in id_tokens):
                return True
        # 2) a multi-word phrase (>=2 words, each >2 chars) from the slot
        # description appears verbatim in the request.
        words = re.findall(r"[\w]+", slot_text)
        phrases = [
            " ".join(words[i:j]).lower()
            for i in range(len(words))
            for j in range(i + 2, min(i + 5, len(words) + 1))
            if all(len(w) > 2 for w in words[i:j])
        ]
        if any(p in lower_req for p in phrases[:30]):
            return True
    return False


def _bind_slot(
    slot: dict[str, Any], raw_request: str, context: ContextResult | None
) -> dict[str, Any]:
    """Bind a slot to a value extracted from the request, if filled.

    Returns a binding dict:
      {"filled": bool, "value": str|None, "source": str|None,
       "source_span": [start, end]|None, "confidence": float,
       "candidates": list[str], "conflict": bool}

    This upgrades slot evaluation from presence detection to value binding:
    when a slot is filled, we record WHAT value was bound, WHERE in the
    request it came from (character span), a confidence, the full candidate
    set, and whether there are conflicting values.  When a slot declares
    ``value_enum``, only values in that enum are accepted.
    """
    if not _slot_is_filled(slot, raw_request, context):
        return {"filled": False, "value": None, "source": None,
                "source_span": None, "confidence": slot.get("confidence", 1.0),
                "candidates": [], "conflict": False}

    slot_id = slot["id"]
    slot_text = slot.get("text", "")
    lower = raw_request.lower()
    value_enum = slot.get("value_enum")  # optional: list of allowed values

    # Value extraction heuristics, ordered by specificity.
    value: str | None = None
    source = "user_request"
    span: list[int] | None = None
    candidates: list[str] = []

    # Explicit clarification answer: highest-confidence value.
    answers = _clarification_answers_by_question(raw_request)
    if answers.get(slot_text):
        value = answers[slot_text]
        source = "clarification_answer"

    # Enum-based extraction: if the slot declares value_enum, search for any
    # enum member in the request (case-insensitive), collecting all as candidates.
    if value is None and value_enum and isinstance(value_enum, list):
        for candidate in value_enum:
            cand_lower = candidate.lower()
            idx = lower.find(cand_lower)
            if idx != -1:
                candidates.append(candidate)
                if value is None:
                    value = raw_request[idx: idx + len(candidate)]
                    span = [idx, idx + len(candidate)]
        if candidates and len(set(c.lower() for c in candidates)) > 1:
            # Multiple different enum members present → conflict.
            return {"filled": True, "value": None, "source": source,
                    "source_span": None, "confidence": 0.5,
                    "candidates": candidates, "conflict": True}

    # File-path-like values for target slots.
    if value is None and slot_id in (
        "target_scope", "target_document", "review_target", "failing_test_target",
    ):
        all_matches = re.findall(r"(?<![\w./-])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+", raw_request)
        if not all_matches:
            all_matches = re.findall(r"\btest_\w+", raw_request)
        if all_matches:
            candidates = list(dict.fromkeys(all_matches))  # dedupe, preserve order
            value = all_matches[0]
            m = re.search(re.escape(value), raw_request) if value else None
            span = [m.start(), m.end()] if m else None
            if len(set(all_matches)) > 1:
                return {"filled": True, "value": None, "source": source,
                        "source_span": None, "confidence": 0.5,
                        "candidates": candidates, "conflict": True}

    # Test/build command values.
    if value is None and slot_id in (
        "test_framework", "test_command", "reproduction_command", "smallest_test_command",
    ):
        for pat in [r"pytest\b", r"cargo test\b", r"npm test\b", r"npm run\b",
                    r"go test\b", r"jest\b", r"cargo build\b", r"make\b"]:
            m = re.search(pat, raw_request, re.IGNORECASE)
            if m:
                value = m.group(0)
                span = [m.start(), m.end()]
                break

    # Package manager values.
    if value is None and slot_id == "package_manager":
        for pat in [r"\bnpm\b", r"\bpip\b", r"\bcargo\b", r"\bgo mod\b",
                    r"\byarn\b", r"\bpnpm\b", r"\buv\b", r"\bpoetry\b"]:
            m = re.search(pat, raw_request)
            if m:
                value = m.group(0)
                span = [m.start(), m.end()]
                break

    # Generic fallback: the matched keyword phrase that triggered the fill.
    if value is None:
        # Find the first significant token from the slot that appears in the
        # request, as a best-effort value anchor.
        words = re.findall(r"[\w]+", slot_text)
        for w in words:
            if len(w) > 2:
                idx = lower.find(w.lower())
                if idx != -1:
                    value = raw_request[idx: idx + len(w)]
                    span = [idx, idx + len(w)]
                    break
        if value is None:
            # Filled via context or clarification marker; value unknown.
            value = None
            source = "context_or_request"

    return {"filled": True, "value": value, "source": source,
            "source_span": span, "confidence": slot.get("confidence", 1.0),
            "candidates": candidates, "conflict": False}


def _clarification_answers_by_question(raw_request: str) -> dict[str, str]:
    if CLARIFICATION_MARKER.lower() not in raw_request.lower():
        return {}
    answers: dict[str, str] = {}
    current_question: str | None = None
    for line in raw_request.splitlines():
        stripped = line.strip()
        if stripped.startswith("- Question:"):
            current_question = stripped.removeprefix("- Question:").strip()
        elif current_question and stripped.startswith("Answer:"):
            answers[current_question] = stripped.removeprefix("Answer:").strip()
            current_question = None
    return answers


def _is_covered_by_safe_default(
    slot: dict[str, Any],
    safe_assumptions: list[dict[str, Any]],
) -> bool:
    """Check if a slot (requires_authorization or human_askable) is covered by a safe default.

    For requires_authorization slots, coverage means the safe default makes
    the dangerous side effect unnecessary.  For human_askable ask_if_missing
    slots, coverage means the safe default already encodes the policy answer
    (e.g. 'do_not_modify_tests' covers 'may_modify_tests').
    """
    slot_id = slot.get("id", "").lower()
    slot_text = slot.get("text", "").lower()

    # Direct ID-based coverage: slot → safe default id
    id_coverage: dict[str, str] = {
        # Authorization → safe default
        "destructive_action_permission": "no_file_deletion",
        "external_service": "no_payment_secrets",
        "permission_to_modify": "read_only",
        "batch_file_moves": "no_file_deletion",
        # Human-askable ask_if_missing → safe default
        "may_modify_tests": "do_not_modify_tests",
        "may_change_public_api": "do_not_change_public_api",
        "public_api_change": "no_public_api_change",
        "audience_when_broad": "factual_only",
        "target_surface_when_missing": "follow_patterns",
        "dependency_policy": "no_large_deps",
        "may_update_snapshots": "do_not_modify_tests",
        "may_change_test_intent": "preserve_test_intent",
        "source_vs_test_fix": "minimal_source_fix",
    }

    covered_by = id_coverage.get(slot_id)
    if covered_by:
        for sa in safe_assumptions:
            name = (sa.get("name") or sa.get("id") or "")
            if name == covered_by or covered_by in name:
                return True

    # Batch moves also covered by localized_changes in refactor contracts
    if slot_id == "batch_file_moves":
        for sa in safe_assumptions:
            name = (sa.get("name") or sa.get("id") or "")
            if name in ("localized_changes", "preserve_behavior"):
                return True

    # Text-based: check if any safe default text negates the auth concept
    for sa in safe_assumptions:
        sa_text = (sa.get("text") or sa.get("description") or "").lower()
        if any(
            kw in sa_text and kw in slot_text
            for kw in ["file deletion", "delete file", "external side effect",
                       "payment", "push", "deploy"]
        ):
            return True

    return False


def _slot_entry_to_state(slot: dict[str, Any], status: str) -> dict[str, Any]:
    """Wrap a contract slot entry into a full InputSlotState.

    Used for blocked/forbidden/stop branches that build ``blocked`` lists.
    Produces a schema-valid InputSlotState (not a simplified {id,text,category}
    dict) so the normalized input passes its own JSON Schema.
    """
    return build_input_slot_state(
        name=slot.get("id", slot.get("name", "")),
        description=slot.get("text", ""),
        category=slot.get("category", status),
        status=status,
        answer_source=slot.get("answer_source", "blocked" if status == "blocked" else "policy_default"),
        support=slot.get("support", "recommended"),
        handling_reason=f"Slot '{slot.get('id','')}' → {status}.",
        confidence=slot.get("confidence", 1.0),
        missing_policy=slot.get("missing_policy"),
        evidence_status=slot.get("evidence_status"),
    )


def _rebuild_state_for_policy(
    slot: dict[str, Any], raw_request: str, target_status: str
) -> dict[str, Any]:
    """Build a fresh InputSlotState whose category/status/question/assumption
    are consistent with the missing_policy target.

    Unlike a bare list-move, this rebuilds the internal state so the slot's
    ``category``, ``status``, ``question`` and ``assumption`` match its new
    list — avoiding the state/list inconsistency where a slot moved to
    ``safe_assumptions`` still carried ``category: human_askable``.
    """
    return build_input_slot_state(
        name=slot["id"],
        description=slot["text"],
        category=target_status,
        status=target_status,
        answer_source=slot.get("answer_source", slot["category"]),
        support=slot.get("support", "recommended"),
        question=slot["text"] if target_status in ("human_askable", "requires_authorization") else None,
        assumption=slot["text"] if target_status == "safe_assumption" else None,
        handling_reason=f"Slot '{slot['id']}' rerouted by missing_policy → {target_status}.",
        confidence=slot.get("confidence", 1.0),
        missing_policy=slot.get("missing_policy"),
        evidence_status=slot.get("evidence_status"),
        on_discovery_failure=("ask_user" if slot.get("missing_policy") == "discover_then_ask"
                              else "report_unresolved" if slot.get("missing_policy") == "discover_only"
                              else None),
    )


def _build_slot_state(slot: dict[str, Any], raw_request: str, status: str) -> dict[str, Any]:
    """Build an InputSlotState from a slot entry."""
    effective_cat = _effective_category(slot)
    value = None
    value_source = None
    value_span = None
    conflict = False
    if status == "known":
        binding = _bind_slot(slot, raw_request, None)
        value = binding["value"]
        value_source = binding["source"]
        value_span = binding["source_span"]
        conflict = binding["conflict"]
    return build_input_slot_state(
        name=slot["id"],
        description=slot["text"],
        category=effective_cat,
        status=status,
        value=value,
        answer_source=slot.get("answer_source", slot["category"]),
        support=slot.get("support", "recommended"),
        question=slot["text"] if status in ("human_askable", "requires_authorization") else None,
        assumption=slot["text"] if status == "safe_assumption" else None,
        handling_reason=f"Slot '{slot['id']}' → {status}.",
        confidence=slot.get("confidence", 1.0),
        missing_policy=slot.get("missing_policy"),
        value_source=value_source,
        value_source_span=value_span,
        conflict=conflict,
    )


def _assign_slot(
    state: dict[str, Any],
    human_provided: list,
    human_askable: list,
    agent_discoverable: list,
    safe_assumptions: list,
    requires_authorization: list,
    blocked: list,
) -> None:
    """Assign an evaluated slot state to the correct category list."""
    cat = state.get("category", state.get("status", "human_askable"))
    if cat == "human_askable":
        human_askable.append(state)
    elif cat == "agent_discoverable":
        agent_discoverable.append(state)
    elif cat == "safe_assumption":
        safe_assumptions.append(state)
    elif cat == "requires_authorization":
        requires_authorization.append(state)
    elif cat == "blocked":
        blocked.append(state)
    elif cat == "known":
        human_provided.append(state)
    else:
        human_provided.append(state)


def _must_ask_from_request(raw_request: str, task_kind: str) -> str | None:
    """Detect high-ambiguity patterns that must be asked."""
    text = raw_request.lower()

    if CLARIFICATION_MARKER.lower() in text and "answer:" in text:
        return None

    if _contains(text, ["更新 snapshot", "snapshot 不一致", "update snapshot"]):
        return "Is the new snapshot output expected and reviewed?"

    if _contains(text, ["改断言", "把断言改"]):
        return "Are the current test expectations incorrect, or should the source behavior be fixed?"

    if _contains(text, ["这个报错", "报错帮我修", "这个接口", "偶尔超时", "有时挂", "偶尔报错"]):
        return "What error message or reproduction steps should be used?"

    if "审查这个仓库" in raw_request or text.strip() in {"review this repo", "review the repo"}:
        return "What review focus should be prioritized: correctness, security, performance, maintainability, or API risk?"

    if raw_request.strip() == "完善 README" or text.strip() == "improve readme":
        return "What README audience and section should be improved?"

    if raw_request.strip() == "优化一下" or text.strip() == "optimize it":
        return "What kind of optimization is intended: performance, memory, code structure, UX, docs, or workflow?"

    if raw_request.strip() == "清理一下项目":
        return "What cleanup scope is intended?"

    if _contains(text, ["提交并推送", "commit and push", "git push"]):
        return "What should be committed, what commit message should be used, and is push authorized?"

    if raw_request.strip() == "加一个导出 CSV 的功能" or text.strip() == "add csv export":
        return "Where should CSV export be exposed and what data should it include?"

    if _contains(text, ["具体你看着办", "you decide"]):
        return "What user-visible behavior should be added?"

    return None


def _make_decision(
    skill_id: str,
    contract: dict[str, Any],
    task_kind: str,
    decision_kind: str,
    reason: str,
    confidence: float,
    human_provided: list[dict[str, Any]] | None = None,
    human_askable: list[dict[str, Any]] | None = None,
    agent_discoverable: list[dict[str, Any]] | None = None,
    safe_assumptions: list[dict[str, Any]] | None = None,
    requires_authorization: list[dict[str, Any]] | None = None,
    blocked: list[dict[str, Any]] | None = None,
    execution_constraints: list[dict[str, Any]] | None = None,
    forbidden_actions: list[dict[str, Any]] | None = None,
    stop_conditions: list[dict[str, Any]] | None = None,
    low_confidence_slots: list[dict[str, Any]] | None = None,
    questions: list[str] | None = None,
    readonly_exploration_plan: list[str] | None = None,
) -> SkillAnalysis:
    """Build a SkillAnalysis result."""
    hp = human_provided or []
    ha = human_askable or []
    ad = agent_discoverable or []
    sa = safe_assumptions or []
    ra = requires_authorization or []
    bl = blocked or []
    ec = execution_constraints or []
    fa = forbidden_actions or []
    sc = stop_conditions or []
    lc = low_confidence_slots or []

    # Build goal from skill description
    goal = contract.get("skill_description", f"Execute {skill_id} task.")

    # Legacy compat fields (forbidden_actions_legacy keeps the old union of
    # blocked + safe_assumption texts so downstream legacy consumers keep
    # working; the structured forbidden_actions field is the new contract).
    assumptions = [s["text"] for s in sa]
    exploration_plan = readonly_exploration_plan or [f"Discover: {s['text']}" for s in ad]
    forbidden_legacy = [s["text"] for s in bl] + [s["text"] for s in sa] + [s["text"] for s in fa]
    verification = [s["text"] for s in ad[:3]]
    unknowns = [s["text"] for s in ha] + [s["text"] for s in ra]

    resolved_questions = _dedupe_texts(questions or [s["text"] for s in ha])

    return SkillAnalysis(
        skill_id=skill_id,
        skill_contract=contract,
        task_kind=task_kind,
        decision_kind=decision_kind,
        decision_reason=reason,
        confidence=confidence,
        human_provided=hp,
        human_askable=ha,
        agent_discoverable=ad,
        safe_assumptions=sa,
        requires_authorization=ra,
        blocked=bl,
        execution_constraints=ec,
        forbidden_actions=fa,
        stop_conditions=sc,
        low_confidence_slots=lc,
        questions=resolved_questions,
        goal=goal,
        assumptions=assumptions,
        readonly_exploration_plan=exploration_plan,
        forbidden_actions_legacy=forbidden_legacy,
        verification_policy=verification,
        unresolved_unknowns=unknowns,
    )


def _dedupe_slot_states(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep first occurrence of semantically identical slot states."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for slot in slots:
        key = (str(slot.get("name") or slot.get("id") or "").strip(), _normalize_question_text(str(slot.get("text", ""))))
        if key in seen:
            continue
        seen.add(key)
        result.append(slot)
    return result


def _dedupe_texts(texts: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for text in texts:
        normalized = _normalize_question_text(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return result


def _normalize_question_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


# ── String helpers ───────────────────────────────────────────


def _contains(text: str, needles: list[str]) -> bool:
    for needle in needles:
        lowered = needle.lower()
        if lowered == "pr":
            if re.search(r"\bpr\b", text):
                return True
            continue
        if lowered in text:
            return True
    return False


def _mentions_test_snapshot(text: str) -> bool:
    lowered = text.lower()
    return _contains(lowered, ["snapshot mismatch", "snapshot 不一致", "update snapshot", "更新 snapshot"])
