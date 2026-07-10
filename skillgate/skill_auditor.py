"""Compatibility shim. The rules-based auditor has been moved to baselines/.

Use skillgate.baselines.rule_auditor directly. This shim exists so existing
imports don't break during the transition period.
"""
from .baselines.rule_auditor import (
    audit_skill,
    audit_skill_to_yaml,
)

__all__ = ["audit_skill", "audit_skill_to_yaml"]
