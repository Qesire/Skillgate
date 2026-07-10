"""SkillInvocationDraft — persistent, run_dir-based mutable compilation state.

A draft is the mutable compilation state between raw request and finalized
skill input.  It references its contract by ``contract_sha256`` +
``contract_path`` (oracle #9: no embedded copy) and tracks per-slot state
through a unified state machine (unresolved / user_bound / inferred /
discovered / defaulted / conflicted / confirmed / rejected).

The draft is produced alongside (not instead of) the existing
``NormalizedSkillInput`` which stays for backward compatibility.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import hash_text

SKILL_INVOCATION_DRAFT_VERSION = "skillgate.skill_invocation_draft.v1"

DRAFT_STATES = {
    "draft",
    "needs_discovery",
    "needs_user_input",
    "needs_confirmation",
    "ready",
    "conflicted",
    "cancelled",
    "invalid",
}

SLOT_STATES = {
    "unresolved",
    "user_bound",
    "inferred",
    "discovered",
    "defaulted",
    "conflicted",
    "confirmed",
    "rejected",
}

# Slot states that carry a value (used by status computation).
_VALUE_STATES = {"user_bound", "inferred", "discovered", "defaulted", "confirmed"}

_DISCOVERY_STRATEGIES = {
    "discover_then_confirm",
    "discover_then_ask",
    "infer_then_confirm",
}


# ── create / load / save ──────────────────────────────────────


def create_draft(
    raw_request: str,
    skill_id: str,
    contract_v3: dict[str, Any],
    run_id: str,
    contract_sha256: str,
    contract_path: str,
) -> dict[str, Any]:
    """Create a new draft dict.

    All slots from the v3 contract start in the ``unresolved`` state.  The
    contract itself is NOT embedded — only its hash and path are stored.
    """
    now = _now_iso()
    slots: dict[str, dict[str, Any]] = {}
    for slot in contract_v3.get("slots", []):
        sid = slot.get("id")
        if not sid:
            continue
        slots[sid] = _empty_slot(slot)

    return {
        "schema_version": SKILL_INVOCATION_DRAFT_VERSION,
        "run_id": run_id,
        "skill_id": skill_id,
        "contract_sha256": contract_sha256,
        "contract_path": contract_path,
        "request": {
            "raw": raw_request,
            "request_hash": hash_text(raw_request),
        },
        "status": "draft",
        "slots": slots,
        "confirmation": {
            "summary": None,
            "confirmed_at": None,
            "confirmation_hash": None,
        },
        "parent_run_id": None,
        "created_at": now,
        "updated_at": now,
    }


def _empty_slot(slot: dict[str, Any]) -> dict[str, Any]:
    """Build the initial unresolved slot state from a v3 slot definition."""
    return {
        "state": "unresolved",
        "value": None,
        "candidates": [],
        "source": None,
        "confidence": 0.0,
        "confirmed": False,
        # Carry the immutable definition fields the status/patch logic needs,
        # so callers do not have to reload the contract to compute status.
        "importance": slot.get("importance", "optional"),
        "acquisition": slot.get("acquisition") or {},
        "confirmation": slot.get("confirmation") or {},
        "description": slot.get("description", ""),
    }


def load_draft(run_dir: Path) -> dict[str, Any]:
    """Load ``draft.json`` from ``run_dir``.

    Verifies ``contract_sha256`` against the contract file at
    ``contract_path``.  On mismatch the draft status is forced to ``invalid``.
    """
    run_dir = Path(run_dir)
    path = run_dir / "draft.json"
    draft = json.loads(path.read_text(encoding="utf-8"))

    contract_path = draft.get("contract_path")
    expected_sha = draft.get("contract_sha256")
    if contract_path and expected_sha:
        contract_file = Path(contract_path)
        if contract_file.is_file():
            try:
                contract_text = contract_file.read_text(encoding="utf-8")
                contract_obj = json.loads(contract_text)
                actual_sha = hash_text(json.dumps(contract_obj, sort_keys=True, ensure_ascii=False))
            except (json.JSONDecodeError, OSError):
                actual_sha = None
            if actual_sha != expected_sha:
                draft["status"] = "invalid"
        else:
            draft["status"] = "invalid"
    return draft


def save_draft(run_dir: Path, draft: dict[str, Any]) -> None:
    """Write ``draft.json`` to ``run_dir``."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    draft = dict(draft)
    draft["updated_at"] = _now_iso()
    (run_dir / "draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ── bind analysis ─────────────────────────────────────────────


def bind_user_request(draft: dict[str, Any], analysis: Any) -> dict[str, Any]:
    """Populate slot values from a ``SkillAnalysis`` and recompute status.

    Matching is by slot ``name`` (analysis slot states use ``name`` == the
    contract slot ``id``).  Slots not present in the analysis stay
    ``unresolved``.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for bucket in (
        analysis.human_provided,
        analysis.agent_discoverable,
        analysis.safe_assumptions,
        analysis.requires_authorization,
        analysis.blocked,
        analysis.low_confidence_slots,
    ):
        for state in bucket or []:
            name = state.get("name") or state.get("id")
            if name:
                by_name[name] = state

    slots = draft.get("slots", {})
    for sid, slot in slots.items():
        state = by_name.get(sid)
        if state is None:
            continue
        _apply_analysis_state(slot, state)

    draft["status"] = compute_draft_status(draft)
    return draft


def _apply_analysis_state(slot: dict[str, Any], state: dict[str, Any]) -> None:
    """Mutate a draft slot from an analysis slot-state dict."""
    answer_source = state.get("answer_source")
    conflict = state.get("conflict") is True
    value = state.get("value")
    # Candidates from the analysis (filled on enum/path conflicts).
    candidates = state.get("candidates") or []

    if conflict:
        slot["state"] = "conflicted"
        slot["value"] = None
        slot["candidates"] = list(candidates)
        slot["confirmed"] = False
        slot["source"] = None
        return

    status = state.get("status") or state.get("category")

    if status == "known":
        # human_provided — value may be None if filled via context/marker.
        if answer_source == "agent":
            slot["state"] = "discovered"
            slot["source"] = {"type": "local_context"}
        else:
            slot["state"] = "user_bound"
            slot["source"] = {"type": "user"}
        slot["value"] = value
        slot["confidence"] = state.get("confidence", 1.0)
    elif status == "agent_discoverable":
        if value is not None:
            slot["state"] = "discovered"
            slot["source"] = {"type": "local_context"}
            slot["value"] = value
            slot["confidence"] = state.get("confidence", 1.0)
        # else: leave unresolved — discovery has not run yet
    elif status == "safe_assumption":
        slot["state"] = "defaulted"
        slot["source"] = {"type": "default"}
        slot["value"] = value if value is not None else state.get("assumption")
        slot["confidence"] = state.get("confidence", 1.0)
    elif status == "requires_authorization":
        # Needs explicit user permission; leave unresolved until confirmed.
        pass
    elif status == "blocked":
        # Blocked slots are not fillable; leave unresolved (discovery/user
        # cannot resolve a structural block).
        pass
    else:
        # Unknown category — leave as-is.
        pass


# ── slot patch protocol ───────────────────────────────────────


def apply_slot_patch(draft: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply structured JSON slot operations and recompute status.

    Supported ops: ``set``, ``confirm``, ``reject``, ``clear``.
    """
    slots = draft.get("slots", {})
    for op in operations or []:
        sid = op.get("slot_id")
        if not sid or sid not in slots:
            raise ValueError(f"unknown slot id in patch: {sid}")
        slot = slots[sid]
        kind = op.get("op")
        if kind == "set":
            source = op.get("source", "user")
            slot["value"] = op.get("value")
            slot["confirmed"] = False
            slot["candidates"] = []
            if source == "inferred":
                slot["state"] = "inferred"
                slot["source"] = {"type": "inferred"}
            else:
                slot["state"] = "user_bound"
                slot["source"] = {"type": "user"}
            slot["confidence"] = 1.0
        elif kind == "confirm":
            slot["confirmed"] = True
            slot["state"] = "confirmed"
        elif kind == "reject":
            slot["state"] = "rejected"
            slot["value"] = None
            slot["confirmed"] = True
            slot["candidates"] = []
        elif kind == "clear":
            slot["state"] = "unresolved"
            slot["value"] = None
            slot["confirmed"] = False
            slot["candidates"] = []
            slot["source"] = None
            slot["confidence"] = 0.0
        else:
            raise ValueError(f"unsupported slot patch op: {kind!r}")

    draft["status"] = compute_draft_status(draft)
    return draft


# ── status computation ────────────────────────────────────────


def compute_draft_status(draft: dict[str, Any]) -> str:
    """Determine the draft status from slot states."""
    has_conflict = False
    needs_discovery = False
    needs_user_input = False
    needs_confirmation = False

    for slot in draft.get("slots", {}).values():
        state = slot.get("state", "unresolved")
        if state == "conflicted":
            has_conflict = True
            continue
        if state == "unresolved":
            strategy = (slot.get("acquisition") or {}).get("strategy", "ask_user")
            if strategy in _DISCOVERY_STRATEGIES:
                needs_discovery = True
            else:
                needs_user_input = True
            continue
        if state in _VALUE_STATES and not slot.get("confirmed"):
            if slot.get("importance") == "required":
                needs_confirmation = True

    if has_conflict:
        return "conflicted"
    if needs_discovery:
        return "needs_discovery"
    if needs_user_input:
        return "needs_user_input"
    if needs_confirmation:
        return "needs_confirmation"
    return "ready"


# ── discovery plan / results ──────────────────────────────────


def create_discovery_plan(draft: dict[str, Any]) -> dict[str, Any]:
    """Build a discovery plan for unresolved discoverable/inferable slots."""
    requests: list[dict[str, Any]] = []
    for sid, slot in draft.get("slots", {}).items():
        if slot.get("state") != "unresolved":
            continue
        strategy = (slot.get("acquisition") or {}).get("strategy")
        if strategy not in _DISCOVERY_STRATEGIES:
            continue
        requests.append(
            {
                "slot_id": sid,
                "resolver": (slot.get("acquisition") or {}).get("resolver"),
                "access": "read_only",
                "hints": [],
            }
        )
    return {"requests": requests}


def apply_discovery_result(draft: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Apply a discovery result to the draft and recompute status.

    ``result`` is a mapping of ``slot_id -> {status, value, candidates,
    evidence_ids}`` where ``status`` is ``resolved`` / ``conflict`` /
    ``unresolved``.
    """
    slots = draft.get("slots", {})
    for sid, res in (result or {}).items():
        if sid not in slots:
            continue
        slot = slots[sid]
        status = res.get("status")
        if status == "resolved":
            slot["state"] = "discovered"
            slot["value"] = res.get("value")
            slot["source"] = {
                "type": "local_context",
                "evidence_ids": list(res.get("evidence_ids") or []),
            }
            slot["confidence"] = res.get("confidence", 1.0)
            slot["confirmed"] = False
        elif status == "conflict":
            slot["state"] = "conflicted"
            slot["value"] = None
            slot["candidates"] = list(res.get("candidates") or [])
            slot["confirmed"] = False
        # unresolved → leave as-is
    draft["status"] = compute_draft_status(draft)
    return draft


# ── user input questions ──────────────────────────────────────


def create_input_questions(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate user-facing questions for unresolved ask_user slots."""
    questions: list[dict[str, Any]] = []
    index = 1
    for sid, slot in draft.get("slots", {}).items():
        if slot.get("state") != "unresolved":
            continue
        strategy = (slot.get("acquisition") or {}).get("strategy", "ask_user")
        if strategy != "ask_user":
            continue
        confirmation = slot.get("confirmation") or {}
        text = confirmation.get("prompt") or slot.get("description") or sid
        required = slot.get("importance") == "required"
        questions.append(
            {
                "id": f"q_{index:03d}",
                "slot_id": sid,
                "text": text,
                "required": required,
            }
        )
        index += 1
    return questions


# ── helpers ───────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
