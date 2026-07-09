"""Skill-targeted pre-activation metaskill input compiler.

Phase B of SkillGate: compile a user request against a selected skill's
input contract to produce a NormalizedSkillInput ready for the target skill.

New entry point: compile_against_skill(raw_request, skill_id, root, out_dir)
Legacy entry point: compile_request(raw_request, root, out_dir) — auto-classifies
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .capabilities import get_capability_for_task
from .context import ContextResult, discover_context
from .render import render_execution_brief, render_legacy_taskbrief, render_normalized_skill_input
from .rules import SkillAnalysis, analyze_against_skill, analyze_request
from .schema import (
    NORMALIZED_SKILL_INPUT_VERSION,
    SKILL_INPUT_CONTRACT_VERSION,
    build_input_slot_state,
    build_normalized_skill_input,
    evidence,
    hash_text,
    short_hash,
    statement,
    validate_decision,
    validate_taskbrief,
)


def compile_against_skill(
    raw_request: str,
    *,
    skill_id: str | None = None,
    root: Path | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Compile a user request against a selected skill contract.

    Args:
        raw_request: The user's raw request.
        skill_id: Explicit skill id (e.g., 'bug_fix'). Auto-classified if None.
        root: Repository root for context discovery.
        out_dir: Output directory for artifacts.

    Returns:
        Dict with run_id, out_dir, normalized_input, decision, analysis.
    """
    root = Path(root) if root else Path.cwd()
    context = discover_context(root)
    analysis = analyze_against_skill(raw_request, skill_id=skill_id, context=context)

    run_id = _run_id(raw_request, analysis.skill_id, context)
    if out_dir is None:
        out_dir = root / ".skillgate" / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    artifacts = _build_normalized_artifacts(raw_request, analysis, context, run_id)
    _write_normalized_artifacts(out_dir, artifacts)

    return {
        "run_id": run_id,
        "out_dir": str(out_dir),
        "skill_id": analysis.skill_id,
        "decision_kind": analysis.decision_kind,
        "analysis": analysis,
        "normalized_input_markdown": artifacts["normalized_input_markdown"],
        "normalized_input": artifacts["normalized_input"],
        "decision": artifacts["decision"],
        "context_manifest": artifacts["context_manifest"],
    }


def _build_normalized_artifacts(
    raw_request: str,
    analysis: SkillAnalysis,
    context: ContextResult,
    run_id: str,
) -> dict[str, Any]:
    """Build all artifacts for a skill-targeted compilation."""
    evidence_items = _build_evidence(raw_request, analysis.skill_id, context)
    evidence_ids = {item["id"] for item in evidence_items}

    # Wrap always-active contract constraints (which are slot entries, not
    # evaluated slot states) into InputSlotState form so the normalized input
    # is internally consistent and schema-valid.
    ec_states = [_constraint_to_slot_state(s, "safe_assumption") for s in analysis.execution_constraints]
    fa_states = [_constraint_to_slot_state(s, "blocked") for s in analysis.forbidden_actions]
    sc_states = [_constraint_to_slot_state(s, "blocked") for s in analysis.stop_conditions]

    normalized_input = build_normalized_skill_input(
        run_id=run_id,
        skill_id=analysis.skill_id,
        skill_name=analysis.skill_contract.get("skill_name", analysis.skill_id),
        raw_request=raw_request,
        human_provided_inputs=analysis.human_provided,
        agent_discoverable_inputs=analysis.agent_discoverable,
        safe_defaults=analysis.safe_assumptions,
        requires_authorization=analysis.requires_authorization,
        blocked=analysis.blocked,
        execution_constraints=ec_states,
        forbidden_actions=fa_states,
        stop_conditions=sc_states,
        low_confidence_slots=analysis.low_confidence_slots,
        decision_kind=analysis.decision_kind,
        decision_reason=analysis.decision_reason,
        activation_instruction=_activation_instruction(analysis),
        expected_output=_expected_output_text(analysis.skill_id),
        evidence_items=evidence_items,
    )

    decision = {
        "kind": analysis.decision_kind,
        "reason": analysis.decision_reason,
        "confidence": analysis.confidence,
        "questions": analysis.questions,
        "skill_id": analysis.skill_id,
    }

    normalized_input_md = render_normalized_skill_input(raw_request, analysis, task_root=context.root)

    return {
        "request": raw_request,
        "context_manifest": context.manifest(),
        "skill_contract": analysis.skill_contract,
        "normalized_input": normalized_input,
        "normalized_input_markdown": normalized_input_md,
        "decision": decision,
        "trace": _trace_events(raw_request, analysis, context),
    }


def _write_normalized_artifacts(out_dir: Path, artifacts: dict[str, Any]) -> None:
    """Write all output artifacts to the run directory."""
    (out_dir / "request.md").write_text(artifacts["request"] + "\n", encoding="utf-8")
    _write_json(out_dir / "context_manifest.json", artifacts["context_manifest"])
    _write_json(out_dir / "skill_contract.json", artifacts["skill_contract"])
    _write_json(out_dir / "normalized_skill_input.json", artifacts["normalized_input"])
    _write_json(out_dir / "decision.json", artifacts["decision"])
    (out_dir / "normalized_skill_input.md").write_text(artifacts["normalized_input_markdown"], encoding="utf-8")

    with (out_dir / "trace.jsonl").open("w", encoding="utf-8") as fh:
        for event in artifacts["trace"]:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, value: dict[str, Any] | list[Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _constraint_to_slot_state(slot: dict[str, Any], status: str) -> dict[str, Any]:
    """Wrap a contract constraint slot entry into InputSlotState form.

    execution_constraints / forbidden_actions / stop_conditions in the
    contract are slot entries ({id, text, category, ...}), but the
    NormalizedSkillInput schema requires InputSlotState objects.  This wraps
    them so the constraints propagate downstream in a schema-valid shape while
    preserving id/text/support/confidence and any evidence_status.
    """
    return build_input_slot_state(
        name=slot.get("id", slot.get("name", "")),
        description=slot.get("text", ""),
        category=slot.get("category", status),
        status=status,
        answer_source=slot.get("answer_source", "policy_default"),
        support=slot.get("support", "recommended"),
        handling_reason=f"Always-active {status} constraint from contract.",
        confidence=slot.get("confidence", 1.0),
        missing_policy=slot.get("missing_policy"),
        evidence_status=slot.get("evidence_status"),
    )


# ── Evidence construction ────────────────────────────────────


def _build_evidence(raw_request: str, skill_id: str, context: ContextResult) -> list[dict[str, Any]]:
    items = [
        evidence("ev_user_request", "user", "raw_request", quote=raw_request, confidence=1.0),
        evidence(
            "ev_skill_contract",
            "skill",
            f"builtin:{skill_id}",
            quote=f"Built-in SkillInputContract for {skill_id}",
            confidence=0.95,
        ),
        evidence(
            "ev_policy_defaults",
            "policy",
            "skillgate_default_policy",
            quote="Read-only compilation; no external side effects; safe defaults applied.",
            confidence=1.0,
        ),
    ]

    counter = 1
    for file in context.files:
        for fact in file.facts:
            quote = None if file.redacted else fact
            quote_hash = hash_text(fact) if file.redacted else None
            source_kind = "repo_config" if file.kind == "skillgate_config" else "repo_file"
            items.append(
                evidence(
                    f"ev_repo_fact_{counter:03d}",
                    source_kind,
                    f"context:{file.path}",
                    path=file.path,
                    quote=quote,
                    quote_hash=quote_hash,
                    confidence=0.95,
                )
            )
            counter += 1
    return items


# ── Activation instruction ───────────────────────────────────


def _activation_instruction(analysis: SkillAnalysis) -> str:
    """Generate the activation instruction for the downstream agent."""
    if analysis.decision_kind == "block_unsafe":
        return "Do not activate the skill. The request cannot be safely executed."
    if analysis.decision_kind == "ask_user":
        return "Do not activate the skill yet. Answer the questions and recompile."

    # Build a concrete constraint summary so the downstream agent sees the
    # actual execution constraints, not just a generic "follow the rules".
    constraint_lines: list[str] = []
    for ec in analysis.execution_constraints:
        txt = ec.get("text") or ec.get("description") or ec.get("id", "")
        if txt:
            constraint_lines.append(f"- {txt}")
    for fa in analysis.forbidden_actions:
        txt = fa.get("text") or fa.get("description") or fa.get("id", "")
        if txt:
            constraint_lines.append(f"- NEVER: {txt}")
    for sc in analysis.stop_conditions:
        txt = sc.get("text") or sc.get("description") or sc.get("id", "")
        if txt:
            constraint_lines.append(f"- STOP IF: {txt}")

    constraint_block = ""
    if constraint_lines:
        constraint_block = "\nExecution constraints (always active):\n" + "\n".join(constraint_lines)

    if analysis.decision_kind == "explore_first":
        return (
            f"Activate the {analysis.skill_id} skill. "
            "Use the normalized SkillGate input as the pre-activation contract; do not execute from the raw request alone. "
            "Before editing, discover agent-discoverable slots through read-only inspection. "
            "Keep discovery, edits, and verification inside the configured task root unless the user authorizes another root. "
            "Stop if a required non-discoverable input is missing."
            + constraint_block
        )
    return (
        f"Activate the {analysis.skill_id} skill with the provided inputs. "
        "Use the normalized SkillGate input as the pre-activation contract; do not execute from the raw request alone. "
        "Follow the skill's execution rules. Report verification results."
        + constraint_block
    )


def _expected_output_text(skill_id: str) -> str:
    """Return expected output string for a skill."""
    outputs = {
        "bug_fix": (
            "1. Failure identified or discovery-failed reason\n"
            "2. Root cause with evidence\n"
            "3. Changed files\n"
            "4. Verification command and result\n"
            "5. Remaining risks"
        ),
        "failing_test_repair": (
            "1. Failing test identified or discovery-failed reason\n"
            "2. Root cause: source defect or test expectation mismatch\n"
            "3. Changed files\n"
            "4. Passing verification command and result\n"
            "5. Remaining test gaps"
        ),
        "code_review": (
            "1. Findings ordered by severity with file references\n"
            "2. Evidence for each finding\n"
            "3. Remaining review gaps or unscanned areas\n"
            "4. No file modifications unless authorized"
        ),
        "refactor": (
            "1. Refactored targets with before/after summary\n"
            "2. Behavior preservation evidence (test pass)\n"
            "3. Changed files\n"
            "4. Remaining structural concerns if any"
        ),
        "documentation_update": (
            "1. Updated documentation sections\n"
            "2. Facts grounded in repo files or user-provided context\n"
            "3. No fabricated metrics or claims"
        ),
        "feature_impl": (
            "1. Implemented feature description\n"
            "2. Changed files\n"
            "3. Verification command and result\n"
            "4. Remaining known limitations"
        ),
    }
    return outputs.get(skill_id, "Clarified task direction and recommended next steps.")


# ── Trace events ─────────────────────────────────────────────


def _trace_events(raw_request: str, analysis: SkillAnalysis, context: ContextResult) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"event": "skill_selected", "skill_id": analysis.skill_id},
        {"event": "contract_loaded", "schema_version": SKILL_INPUT_CONTRACT_VERSION},
    ]
    for file in context.files:
        events.append(
            {"event": "context_file_found", "path": file.path, "read": file.read, "redacted": file.redacted}
        )
    events.append({"event": "decision", "kind": analysis.decision_kind, "reason": analysis.decision_reason})

    # P0: explicit, verifiable marker that SkillGate's contract compilation
    # actually ran.  Carries hashes so an experiment collector can prove the
    # MetaSkill was invoked and produced a NormalizedSkillInput for this exact
    # request+task_root, distinguishing a real SkillGate condition from a
    # curated-skill condition that merely loaded skills.
    request_hash = hash_text(json.dumps(
        {"raw_request": raw_request, "skill_id": analysis.skill_id, "task_root": str(context.root)},
        sort_keys=True, ensure_ascii=False,
    ))
    contract_hash = hash_text(json.dumps(analysis.skill_contract, sort_keys=True, ensure_ascii=False))
    events.append({
        "event": "skillgate_compilation_completed",
        "skill_id": analysis.skill_id,
        "decision": analysis.decision_kind,
        "contract_hash": contract_hash[:16],
        "request_hash": request_hash[:16],
        "task_root_hash": hash_text(str(context.root))[:16],
        "schema_version": SKILL_INPUT_CONTRACT_VERSION,
        "slot_counts": {
            "human_provided": len(analysis.human_provided),
            "human_askable": len(analysis.human_askable),
            "agent_discoverable": len(analysis.agent_discoverable),
            "safe_assumptions": len(analysis.safe_assumptions),
            "requires_authorization": len(analysis.requires_authorization),
            "blocked": len(analysis.blocked),
            "execution_constraints": len(analysis.execution_constraints),
            "forbidden_actions": len(analysis.forbidden_actions),
            "stop_conditions": len(analysis.stop_conditions),
            "low_confidence_slots": len(analysis.low_confidence_slots),
        },
    })
    return events


# ── Run ID ───────────────────────────────────────────────────


def _run_id(raw_request: str, skill_id: str, context: ContextResult) -> str:
    manifest = context.manifest()
    manifest.pop("root", None)
    fingerprint = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    return f"sg-{short_hash(raw_request + skill_id + fingerprint, 16)}"


# ═══════════════════════════════════════════════════════════════
#  LEGACY: backward-compatible compile_request
# ═══════════════════════════════════════════════════════════════


def compile_request(raw_request: str, *, root: Path, out_dir: Path | None = None) -> dict[str, Any]:
    """Legacy entry point. Prefer compile_against_skill for new code.

    Auto-classifies task, builds old-format TaskBrief artifacts.
    """
    context = discover_context(root)
    analysis = analyze_request(raw_request, context)
    run_id = f"p0-{short_hash(raw_request, 16)}"

    if out_dir is None:
        out_dir = root / ".skillgate" / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Convert SkillAnalysis to old-format tasksbrief artifacts
    artifacts = _build_legacy_artifacts(raw_request, analysis, context, run_id)
    _write_legacy_artifacts(out_dir, artifacts)

    return {
        "run_id": run_id,
        "out_dir": str(out_dir),
        "decision": artifacts["decision"],
        "taskbrief": artifacts.get("taskbrief", {}),
        "execution_brief": artifacts.get("execution_brief_markdown", ""),
        "context_manifest": artifacts["context_manifest"],
    }


def _build_execution_brief_markdown(
    raw_request: str,
    taskbrief: dict[str, Any],
) -> str:
    """Build execution_brief_markdown, adding Execution Blocked prefix when needed."""
    decision_kind = taskbrief.get("decision_kind", "unknown")
    base = render_execution_brief(raw_request=raw_request, taskbrief=taskbrief)
    if decision_kind.startswith("block"):
        lines = base.split("\n")
        # Insert "## Execution Blocked: ..." after the header line + blank line
        lines.insert(2, f"## Execution Blocked: {decision_kind}")
        lines.insert(2, "")
        return "\n".join(lines)
    return base


def _build_legacy_artifacts(
    raw_request: str,
    analysis: SkillAnalysis,
    context: ContextResult,
    run_id: str,
) -> dict[str, Any]:
    evidence_items = _build_evidence(raw_request, analysis.skill_id, context)
    evidence_ids = {item["id"] for item in evidence_items}

    task_frame = {
        "raw_request": raw_request,
        "kind": analysis.task_kind,
        "goal": statement(analysis.goal, ["ev_user_request"]),
        "target_objects": [statement(s.get("text") or s.get("description", ""), ["ev_skill_contract"])
                           for s in analysis.agent_discoverable],
        "user_constraints": [statement(s.get("text") or s.get("description", ""), ["ev_skill_contract"])
                             for s in analysis.human_askable + analysis.requires_authorization],
        "requested_outputs": [statement("Normalized Skill Input", ["ev_skill_contract"])],
        "ambiguity_notes": [statement(s.get("question") or "", ["ev_skill_contract"])
                           for s in analysis.human_askable if s.get("question")],
    }

    taskbrief = {
        "id": f"tb-{short_hash(run_id)}",
        "run_id": run_id,
        "schema_version": "taskbrief.v2.p0",
        "task_frame": task_frame,
        "matched_capability": get_capability_for_task(analysis.task_kind),
        "decision_kind": analysis.decision_kind,
        "goal": statement(analysis.goal, ["ev_user_request"]),
        "scope_in": [statement(s.get("text") or s.get("description", ""), ["ev_skill_contract"])
                       for s in analysis.safe_assumptions],
        "scope_out": [statement(f, ["ev_policy_defaults"]) for f in analysis.forbidden_actions_legacy],
        "known_facts": [statement(raw_request, ["ev_user_request"])]
                       + [statement(fact, [f"ev_repo_fact_{i:03d}"])
                          for i, (_, fact) in enumerate(context.facts(), start=1)],
        "assumptions": [statement(a, ["ev_skill_contract"]) for a in analysis.assumptions],
        "unresolved_unknowns": [statement(u, ["ev_skill_contract"]) for u in analysis.unresolved_unknowns],
        "execution_policy": [],
        "forbidden_actions": [statement(f, ["ev_policy_defaults"]) for f in analysis.forbidden_actions_legacy],
        "verification_policy": [statement(v, ["ev_skill_contract"]) for v in analysis.verification_policy],
        "stop_conditions": [],
        "output_contract": [],
        "evidence": evidence_items,
    }

    decision = {
        "kind": analysis.decision_kind,
        "reason": analysis.decision_reason,
        "confidence": analysis.confidence,
        "questions": analysis.questions,
        "assumptions": [statement(a, ["ev_skill_contract"]) for a in analysis.assumptions],
        "readonly_exploration_plan": [statement(e, ["ev_skill_contract"]) for e in analysis.readonly_exploration_plan],
        "blocking_slots": [],
        "stop_conditions": [],
    }

    return {
        "request": raw_request,
        "context_manifest": context.manifest(),
        "taskbrief": taskbrief,
        "taskbrief_markdown": render_legacy_taskbrief(taskbrief, decision),
        "execution_brief_markdown": _build_execution_brief_markdown(
            raw_request, taskbrief),
        "decision": decision,
        "trace": _trace_events(raw_request, analysis, context),
    }


def _write_legacy_artifacts(out_dir: Path, artifacts: dict[str, Any]) -> None:
    (out_dir / "request.md").write_text(artifacts["request"] + "\n", encoding="utf-8")
    _write_json(out_dir / "context_manifest.json", artifacts["context_manifest"])
    _write_json(out_dir / "decision.json", artifacts["decision"])
    if "taskbrief" in artifacts:
        _write_json(out_dir / "taskbrief.json", artifacts["taskbrief"])
    (out_dir / "taskbrief.md").write_text(artifacts.get("taskbrief_markdown", ""), encoding="utf-8")
    (out_dir / "execution_brief.md").write_text(artifacts.get("execution_brief_markdown", ""), encoding="utf-8")
    with (out_dir / "trace.jsonl").open("w", encoding="utf-8") as fh:
        for event in artifacts["trace"]:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
