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

from .capabilities import (
    get_contract_for_skill,
    get_contract_for_task_kind,
    TASK_KIND_TO_SKILL_ID,
)
from .constants import CLARIFICATION_MARKER
from .context import ContextResult
from .schema import (
    SKILL_INPUT_CONTRACT_VERSION,
    INPUT_SLOT_STATE_VERSION,
    build_input_slot_state,
    evidence,
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

    # Human-facing questions
    questions: list[str]

    # Legacy fields for backward compat
    goal: str
    assumptions: list[str]
    readonly_exploration_plan: list[str]
    forbidden_actions: list[str]
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
        skill_id: Explicit skill to target (e.g., 'bug_fix'). If None, classified from request.
        context: Optional pre-discovered context for discovery hints.

    Returns:
        SkillAnalysis with categorized slot states and decision.
    """
    lower = raw_request.lower()

    # 1. Determine skill
    if skill_id is None:
        task_kind = _classify_task(raw_request)
        skill_id = TASK_KIND_TO_SKILL_ID.get(task_kind, "generic_unknown")
    else:
        task_kind = _task_kind_for_skill(skill_id)

    contract = get_contract_for_skill(skill_id)

    # Backward compat: if old contract only has "block_if" but no new fields,
    # copy block_if items to safety_blocks.
    _ensure_modern_contract_sections(contract)

    # 2. Check block conditions (safety_blocks + forbidden_actions + stop_conditions)
    all_block_conditions = (
        contract.get("safety_blocks", [])
        + contract.get("forbidden_actions", [])
        + contract.get("stop_conditions", [])
    )
    block_reason = _check_block_conditions(all_block_conditions, raw_request)
    if block_reason:
        return _make_decision(
            skill_id=skill_id,
            contract=contract,
            task_kind=task_kind,
            decision_kind="block_unsafe",
            reason=block_reason,
            confidence=0.95,
            blocked=[{"id": b["id"], "text": b["text"], "category": "blocked"} for b in all_block_conditions],
        )

    # 3. Evaluate slots against the request
    human_provided: list[dict[str, Any]] = []
    human_askable: list[dict[str, Any]] = []
    agent_discoverable: list[dict[str, Any]] = []
    safe_assumptions: list[dict[str, Any]] = []
    requires_authorization: list[dict[str, Any]] = []
    blocked_slots: list[dict[str, Any]] = []

    # Required slots
    for slot in contract["required_slots"]:
        state = _evaluate_slot(slot, raw_request, context, is_required=True)
        _assign_slot(state, human_provided, human_askable, agent_discoverable,
                     safe_assumptions, requires_authorization, blocked_slots)

    # Safe defaults (process first so auth-coverage check can use them)
    for slot in contract["safe_defaults"]:
        safe_assumptions.append(_build_slot_state(slot, raw_request, "safe_assumption"))

    # Authorization requirements (separate from ask_if_missing for clarity)
    for slot in contract.get("authorization_requirements", []):
        if _slot_is_filled(slot, raw_request, context):
            human_provided.append(_build_slot_state(slot, raw_request, "known"))
        elif _is_covered_by_safe_default(slot, safe_assumptions):
            safe_assumptions.append(_build_slot_state(slot, raw_request, "safe_assumption"))
        else:
            requires_authorization.append(_build_slot_state(slot, raw_request, "requires_authorization"))

    # Ask-if-missing slots
    for slot in contract["ask_if_missing"]:
        if _slot_is_filled(slot, raw_request, context):
            human_provided.append(_build_slot_state(slot, raw_request, "known"))
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
        if _slot_is_filled(slot, raw_request, context):
            human_provided.append(_build_slot_state(slot, raw_request, "known"))
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

    # REMOVED: Safe defaults (processed above, before ask_if_missing)

    # 4. Make decision — exact §7 ordering
    # §7: block → auth → human_askable → discover → assume → compile

    if blocked_slots:
        return _make_decision(
            skill_id, contract, task_kind, "block_unsafe",
            "One or more blocking conditions are triggered.",
            0.95, blocked=blocked_slots,
            safe_assumptions=safe_assumptions,
        )

    if requires_authorization:
        questions = _dedupe_texts([s["text"] for s in requires_authorization])
        return _make_decision(
            skill_id, contract, task_kind, "ask_user",
            "Authorization is required before proceeding.",
            0.90, requires_authorization=requires_authorization, questions=questions,
            safe_assumptions=safe_assumptions,
        )

    if human_askable:
        questions = _dedupe_texts([s["text"] for s in human_askable])
        return _make_decision(
            skill_id, contract, task_kind, "ask_user",
            "Some required inputs need to be provided by the user.",
            0.85, human_askable=human_askable, questions=questions,
            safe_assumptions=safe_assumptions,
        )

    if agent_discoverable:
        exploration = [f"Discover: {s['text']}" for s in agent_discoverable]
        return _make_decision(
            skill_id, contract, task_kind, "explore_first",
            "Important context can be discovered through read-only local inspection.",
            0.84, agent_discoverable=agent_discoverable, readonly_exploration_plan=exploration,
            safe_assumptions=safe_assumptions,
        )

    if safe_assumptions:
        reason = "Only low-risk gaps remain; conservative assumptions are recorded."
    else:
        reason = "All required inputs are satisfied."

    return _make_decision(
        skill_id, contract, task_kind,
        "assume_and_continue" if safe_assumptions else "compile_directly",
        reason, 0.82, safe_assumptions=safe_assumptions,
    )


# ── Helpers ──────────────────────────────────────────────────


def _classify_task(raw_request: str) -> str:
    """Classify task kind from raw request. Same logic as before."""
    text = raw_request.lower()
    if _contains(text, ["readme", "文档", "安装说明", "贡献指南", "项目介绍"]):
        return "documentation"
    if _contains(text, ["测试", "test", "pytest", "cargo test", "断言"]) or _mentions_test_snapshot(text):
        return "failing_test"
    if _contains(text, ["review", "审查", "检查", "pr", "凭据泄露", "安全问题", "数据丢失"]):
        return "code_review"
    if _contains(text, ["重构", "重复的分支", "api 名字", "重新组织", "batch move", "refactor", "rename public api"]):
        return "refactor"
    if _contains(text, ["csv", "导出", "接入 stripe", "付款", "加一个", "加个功能", "功能", "实现"]):
        return "feature_impl"
    if _contains(
        text,
        ["报错", "bug", "空白", "超时", "类型错误", "接口", "登录", "数据库", "data", "defect", "repair", "fix"],
    ):
        return "bug_fix"
    return "unknown"


def _task_kind_for_skill(skill_id: str) -> str:
    """Reverse map skill_id to task_kind."""
    reverse = {v: k for k, v in TASK_KIND_TO_SKILL_ID.items()}
    return reverse.get(skill_id, "unknown")


def _check_block_conditions(block_if: list[dict[str, Any]], raw_request: str) -> str | None:
    """Check if any block conditions are triggered by the request."""
    lower = raw_request.lower()
    for condition in block_if:
        text = condition.get("text", "").lower()
        if ("credential" in text or "secret" in text or "exfiltration" in text) and _contains(
            lower, ["密钥", "secret", "exfiltrate", "泄露密钥", "dump", "token"]
        ):
            return "The request may involve credential or secret access."
        if "production" in text and _contains(lower, ["生产数据库", "production database", "生产环境"]):
            return "The request may involve production data mutation."
        if "destructive" in text and _contains(lower, ["删除", "drop", "rm -rf", "清空"]):
            return "The request may involve destructive operations."
        # Payment + secret combination (e.g., Stripe with API key)
        if "payment" in text and _contains(lower, ["密钥", "secret", "api key", "credential", "stripe", "付款", "payment"]):
            return "Payment integration with secret usage requires explicit safe sandbox setup."
    return None


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

    # Check if request provides the info
    if _slot_is_filled(slot, raw_request, context):
        return build_input_slot_state(
            name=slot_id,
            description=text,
            category="known",
            status="known",
            answer_source=answer_source,
            support=support,
            handling_reason="Request or context provides this information.",
        )

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
    # Use the slot's text/description for keyword matching against the raw request.
    slot_text = slot.get("text", "")
    if slot_text:
        # Tokenize the slot description and check if any significant word appears
        keywords = [w.lower() for w in slot_text.split() if len(w) > 2]
        if any(kw in raw_request.lower() for kw in keywords[:20]):  # cap at 20 keywords to avoid noise
            return True
    return False


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


def _build_slot_state(slot: dict[str, Any], raw_request: str, status: str) -> dict[str, Any]:
    """Build an InputSlotState from a slot entry."""
    effective_cat = _effective_category(slot)
    return build_input_slot_state(
        name=slot["id"],
        description=slot["text"],
        category=effective_cat,
        status=status,
        answer_source=slot.get("answer_source", slot["category"]),
        support=slot.get("support", "recommended"),
        question=slot["text"] if status in ("human_askable", "requires_authorization") else None,
        assumption=slot["text"] if status == "safe_assumption" else None,
        handling_reason=f"Slot '{slot['id']}' → {status}.",
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

    # Build goal from skill description
    goal = contract.get("skill_description", f"Execute {skill_id} task.")

    # Legacy compat fields
    assumptions = [s["text"] for s in sa]
    exploration_plan = readonly_exploration_plan or [f"Discover: {s['text']}" for s in ad]
    forbidden = [s["text"] for s in bl] + [s["text"] for s in sa]
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
        questions=resolved_questions,
        goal=goal,
        assumptions=assumptions,
        readonly_exploration_plan=exploration_plan,
        forbidden_actions=forbidden,
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


# ── Legacy entry point for backward compat ───────────────────


def analyze_request(raw_request: str, context: ContextResult) -> SkillAnalysis:
    """Legacy entry point: auto-classify and analyze. Use analyze_against_skill for new code."""
    return analyze_against_skill(raw_request, skill_id=None, context=context)


def classify_task(raw_request: str) -> str:
    """Legacy: classify task from raw request."""
    return _classify_task(raw_request)


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
