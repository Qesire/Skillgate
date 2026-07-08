"""Render NormalizedSkillInput as markdown for target-skill consumption.

The normalized input is the contract handed to Codex/OpenCode/Claude Code
before skill activation. It clearly separates:
- What the user already provided
- What the agent should discover
- What defaults to apply
- What requires authorization
- What is blocked
"""

from __future__ import annotations

from typing import Any


def render_normalized_skill_input(raw_request: str, analysis: Any, *, task_root: str | None = None) -> str:
    """Render a NormalizedSkillInput markdown from a SkillAnalysis.

    Args:
        raw_request: The original user request.
        analysis: A SkillAnalysis (or dict with same fields).

    Returns:
        Markdown string for the downstream skill/agent.
    """
    # Support both SkillAnalysis dataclass and dict
    if hasattr(analysis, "skill_id"):
        skill_id = analysis.skill_id
        skill_name = analysis.skill_contract.get("skill_name", skill_id) if hasattr(analysis, "skill_contract") else skill_id
        decision_kind = analysis.decision_kind
        decision_reason = analysis.decision_reason
        human_provided = analysis.human_provided
        human_askable = analysis.human_askable
        agent_discoverable = analysis.agent_discoverable
        safe_assumptions = analysis.safe_assumptions
        requires_authorization = analysis.requires_authorization
        blocked = analysis.blocked
        questions = analysis.questions
    else:
        skill_id = analysis.get("skill_id", "unknown")
        skill_name = analysis.get("skill_name", skill_id)
        decision_kind = analysis.get("decision_kind", "unknown")
        decision_reason = analysis.get("decision_reason", "")
        human_provided = analysis.get("human_provided", [])
        human_askable = analysis.get("human_askable", [])
        agent_discoverable = analysis.get("agent_discoverable", [])
        safe_assumptions = analysis.get("safe_assumptions", [])
        requires_authorization = analysis.get("requires_authorization", [])
        blocked = analysis.get("blocked", [])
        questions = analysis.get("questions", [])

    lines: list[str] = [
        "# Normalized Skill Input",
        "",
        f"## Selected Skill",
        "",
        f"- **Skill:** `{skill_id}` — {skill_name}",
        f"- **Decision:** `{decision_kind}`",
    ]

    if decision_reason:
        lines.append(f"- **Reason:** {decision_reason}")

    if task_root:
        lines.extend([
            "",
            "## Task Root",
            "",
            f"- `{task_root}`",
            "- Treat this as the only workspace for discovery, edits, and verification unless the user explicitly authorizes another root.",
        ])

    lines.extend([
        "",
        "## User Request",
        "",
        raw_request.strip(),
        "",
    ])

    # ── Human-provided inputs ──
    if human_provided:
        lines.extend(["## Human-Provided Inputs", ""])
        for slot in human_provided:
            lines.append(f"- {_slot_text(slot)}")
        lines.append("")

    # ── Agent-discoverable inputs ──
    if agent_discoverable:
        lines.extend(["## Agent-Discoverable Inputs", ""])
        lines.append("> These are codebase facts. The agent should discover them through read-only exploration.")
        lines.append("")
        for slot in agent_discoverable:
            lines.append(f"- {_slot_text(slot)}")
        lines.append("")

    # ── Safe defaults ──
    if safe_assumptions:
        lines.extend(["## Safe Defaults", ""])
        for slot in safe_assumptions:
            lines.append(f"- {_slot_text(slot)}")
        lines.append("")

    # ── Authorization required ──
    if requires_authorization:
        lines.extend(["## Authorization Required", ""])
        lines.append("> These must be explicitly authorized before proceeding.")
        lines.append("")
        for slot in requires_authorization:
            lines.append(f"- {_slot_text(slot)}")
        lines.append("")

    # ── Blocked ──
    if blocked:
        lines.extend(["## Blocked", ""])
        lines.append("> Execution cannot proceed with these conditions active.")
        lines.append("")
        for slot in blocked:
            lines.append(f"- {_slot_text(slot)}")
        lines.append("")

    # ── Questions for user ──
    if decision_kind == "ask_user" and questions:
        lines.extend(["## Questions for User", ""])
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q}")
        lines.append("")

    # ── Activation instruction ──
    lines.extend([
        "## Activation Instruction",
        "",
    ])

    if decision_kind == "block_unsafe":
        lines.append("**Do not activate the skill.** The request cannot be safely executed.")
    elif decision_kind == "ask_user":
        lines.append("**Do not activate the skill yet.** Answer the questions above, then recompile.")
    else:
        lines.append("This Normalized Skill Input is the pre-activation gate for the target skill.")
        lines.append("Do not activate or execute the target skill from the raw request alone.")
        lines.append("")
        lines.append(f"Activate the `{skill_id}` skill with the inputs above.")
        lines.append("")
        lines.append("Before editing, perform read-only discovery for agent-discoverable slots.")
        if task_root:
            lines.append(f"Run discovery, edits, and verification under `{task_root}`.")
        if agent_discoverable:
            lines.append("Stop and ask the user only if a required non-discoverable input is missing.")
        if decision_kind == "explore_first":
            lines.append("Complete local exploration before proposing changes.")

    lines.append("")

    # ── Expected Target Skill Output ──
    if decision_kind not in ("block_unsafe", "ask_user"):
        expected = _expected_output_for(skill_id)
        if expected:
            lines.extend(["## Expected Target Skill Output", ""])
            for item in expected:
                lines.append(f"- {item}")
            lines.append("")

    return "\n".join(lines)


def render_legacy_taskbrief(taskbrief: dict[str, Any], decision: dict[str, Any]) -> str:
    """Legacy renderer for backward compatibility during migration."""
    lines: list[str] = ["# TaskBrief (Legacy)", ""]
    lines.append(f"## Goal")
    lines.append(f"- {taskbrief.get('goal', {}).get('text', 'Unknown')}")
    lines.append("")
    if decision.get("questions"):
        lines.append("## Questions for User")
        for q in decision["questions"]:
            lines.append(f"- {q}")
        lines.append("")
    # ── Context ──
    known_facts = taskbrief.get("known_facts", [])
    if known_facts:
        lines.append("## Context")
        for fact in known_facts:
            text = fact.get("text", str(fact)) if isinstance(fact, dict) else str(fact)
            lines.append(f"- {text}")
        lines.append("")
    lines.append(f"## Decision")
    lines.append(f"- Kind: `{decision.get('kind', 'unknown')}`")
    lines.append(f"- Reason: {decision.get('reason', '')}")
    lines.append("")
    return "\n".join(lines)


# ── helpers ──────────────────────────────────────────────────


def _slot_text(slot: dict[str, Any]) -> str:
    """Format a slot entry as a readable line."""
    if isinstance(slot, dict):
        return slot.get("text", str(slot))
    return str(slot)


_EXPECTED_OUTPUTS: dict[str, list[str]] = {
    "bug_fix": [
        "failure identified or discovery-failed reason",
        "root cause with evidence",
        "changed files list",
        "verification command and result",
        "remaining risks",
    ],
    "failing_test_repair": [
        "failing test identified or discovery-failed reason",
        "root cause: source defect or test expectation mismatch",
        "changed files list",
        "passing verification command and result",
        "remaining test gaps",
    ],
    "code_review": [
        "findings ordered by severity with file references",
        "evidence for each finding",
        "remaining review gaps or unscanned areas",
        "no file modifications unless authorized",
    ],
    "refactor": [
        "refactored targets with before/after summary",
        "behavior preservation evidence (test pass)",
        "changed files list",
        "any remaining structural concerns",
    ],
    "documentation_update": [
        "updated documentation sections",
        "facts grounded in repo files or user-provided context",
        "no fabricated metrics or claims",
    ],
    "feature_impl": [
        "implemented feature description",
        "changed files list",
        "verification command and result",
        "remaining known limitations",
    ],
    "generic_unknown": [
        "clarified task direction",
        "scope and success criteria identified",
        "recommended next compilation",
    ],
}


def _expected_output_for(skill_id: str) -> list[str]:
    return _EXPECTED_OUTPUTS.get(skill_id, [])


# ── backward compat exports ──────────────────────────────────


def render_taskbrief(taskbrief: dict[str, Any], decision: dict[str, Any]) -> str:
    """Backward compat: render legacy taskbrief. New code should use render_normalized_skill_input."""
    return render_legacy_taskbrief(taskbrief, decision)


def render_execution_brief(*, raw_request: str, taskbrief: dict[str, Any]) -> str:
    """Legacy execution brief. Kept for backward compat."""
    decision_kind = taskbrief.get("decision_kind", "unknown")
    lines = [
        "# Execution Brief (Legacy)",
        "",
        "## Request",
        "",
        raw_request.strip(),
        "",
        f"## Decision: `{decision_kind}`",
        "",
    ]
    # ── Context ──
    known_facts = taskbrief.get("known_facts", [])
    if known_facts:
        lines.append("## Context")
        lines.append("")
        for fact in known_facts:
            text = fact.get("text", str(fact)) if isinstance(fact, dict) else str(fact)
            lines.append(f"- {text}")
        lines.append("")
    if decision_kind in ("block_unsafe", "ask_user"):
        lines.append("- Do not modify files until the decision is resolved.")
        lines.append("")
    else:
        lines.append("- Inspect local context before modifying files.")
        lines.append("- Run relevant local verification and report the outcome.")
        lines.append("")
    return "\n".join(lines)
