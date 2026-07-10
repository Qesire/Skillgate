"""SkillInvocationDraft lifecycle state machine.

Validates transitions between draft lifecycle states.  The draft itself
lives in ``skillgate.draft``; this module isolates the transition rules so
they can be exercised independently (and so ``draft.py`` does not need to
import a transition validator at module load when only creating drafts).
"""

from __future__ import annotations

LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"needs_discovery", "needs_user_input", "needs_confirmation", "ready", "invalid"},
    "needs_discovery": {"needs_user_input", "needs_confirmation", "ready", "conflicted", "cancelled"},
    "needs_user_input": {"needs_confirmation", "ready", "conflicted", "cancelled"},
    "needs_confirmation": {"ready", "conflicted", "cancelled"},
    "conflicted": {"needs_user_input", "needs_confirmation", "cancelled"},
    "ready": set(),
    "cancelled": set(),
    "invalid": set(),
}


def validate_transition(from_state: str, to_state: str) -> None:
    """Raise ``ValueError`` if ``from_state -> to_state`` is not a legal transition."""
    if to_state not in LEGAL_TRANSITIONS.get(from_state, set()):
        raise ValueError(f"illegal draft transition: {from_state} -> {to_state}")
