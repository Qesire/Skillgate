"""End-to-end chain tests for the real-style skill examples (mock LLM only).

Verifies the full Phase A → Phase B chain for every skill in
``examples/real_skills/`` without depending on an API key:

    SKILL.md
      → DiscoveredContract          (audit_skill_with_llm_traced, MockLLM)
      → to_builtin_format()         (contract → capabilities/rules format)
      → compile_against_skill()     (Phase B: contract-aware compilation)
      → NormalizedSkillInput        (normalized_input dict + markdown)

Also guards the committed mock artifact set used by the core test suite.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import tests.core  # noqa: F401 -- registers MockLLM fixtures at import time
import yaml

from skillgate.capabilities import CONTRACT_REGISTRY
from skillgate.compiler import compile_against_skill
from skillgate.llm_auditor import (
    DiscoveredContract,
    MockLLM,
    audit_skill_with_llm_traced,
)
from skillgate.schema import NORMALIZED_SKILL_INPUT_VERSION

ROOT = Path(__file__).resolve().parents[2]
REAL_SKILLS_DIR = ROOT / "examples" / "real_skills"

SKILLS = ["bug_fix", "code_review", "refactor", "documentation_update", "experiment_debug"]
REQUEST_TAGS = ["vague", "complete"]

VALID_DECISIONS = {
    "block_unsafe", "ask_user", "explore_first",
    "assume_and_continue", "compile_directly",
}


def _load_requests() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load((REAL_SKILLS_DIR / "requests.yaml").read_text(encoding="utf-8"))
    return {skill: raw[skill] for skill in SKILLS if skill in raw}


REQUESTS = _load_requests()


class RealSkillChainTests(unittest.TestCase):
    """Full chain per skill, mock-only, no API key."""

    # ── Per-skill full chain ────────────────────────────────────

    def test_chain_bug_fix(self) -> None:
        self._assert_full_chain("bug_fix")

    def test_chain_code_review(self) -> None:
        self._assert_full_chain("code_review")

    def test_chain_refactor(self) -> None:
        self._assert_full_chain("refactor")

    def test_chain_documentation_update(self) -> None:
        self._assert_full_chain("documentation_update")

    def test_chain_experiment_debug(self) -> None:
        self._assert_full_chain("experiment_debug")

    def _assert_full_chain(self, skill: str) -> None:
        # ── Phase A: SKILL.md → DiscoveredContract (+ trace) ──
        skill_md = REAL_SKILLS_DIR / skill / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        contract, trace = audit_skill_with_llm_traced(content, MockLLM(fixture_name=skill), skill_id_hint=skill)

        self.assertIsInstance(contract, DiscoveredContract)
        self.assertEqual(skill, contract.skill_id)
        self.assertTrue(contract.skill_name, f"{skill}: skill_name empty")
        self.assertGreaterEqual(
            len(contract.slots), 2,
            f"{skill}: expected >=2 slots, got {len(contract.slots)}",
        )
        self.assertTrue(
            contract.safe_defaults or contract.block_if,
            f"{skill}: expected safe_defaults or block_if",
        )

        # Every slot must be fully classified.
        for slot in contract.slots:
            self.assertTrue(slot.name)
            self.assertIn(slot.answer_source, {"human", "agent", "human_or_agent", "authorization"})
            self.assertIn(slot.missing_policy, {"ask_user", "discover_then_ask", "discover_only"})
            self.assertIn(slot.support, {"explicit", "inferred", "recommended", "guessed"})
            self.assertGreater(slot.confidence, 0)
            self.assertLessEqual(slot.confidence, 1)

        # Trace captures all four stages.
        self.assertEqual(
            ["extract", "infer", "classify", "critique"],
            [s["stage"] for s in trace["stages"]],
            f"{skill}: trace stages mismatch",
        )
        self.assertEqual("MockLLM", trace["llm_backend"])

        # ── to_builtin_format: contract → capabilities/rules format ──
        builtin = contract.to_builtin_format()
        self.assertEqual(skill, builtin["skill_id"])
        for key in ("required_slots", "ask_if_missing", "discover_if_missing",
                    "safe_defaults", "safety_blocks"):
            self.assertIsInstance(builtin[key], list, f"{skill}: {key} not a list")
        all_slots = (builtin["required_slots"] + builtin["ask_if_missing"]
                     + builtin["discover_if_missing"])
        self.assertGreater(len(all_slots), 0, f"{skill}: no slots in builtin format")
        for s in all_slots:
            self.assertIn("id", s)
            self.assertIn("text", s)
            self.assertIn("category", s)
            self.assertIn("answer_source", s)

        # ── Phase B: compile → NormalizedSkillInput ──
        for tag in REQUEST_TAGS:
            request = REQUESTS[skill][tag]
            with tempfile.TemporaryDirectory() as tmp:
                result = self._compile_with_discovered(builtin, request, Path(tmp))
            self._assert_normalized_input(result, skill, tag)

    # ── to_builtin_format structure for all skills ─────────────

    def test_to_builtin_format_has_safe_defaults_or_blocks_for_all_skills(self) -> None:
        for skill in SKILLS:
            contract = self._discover(skill)
            builtin = contract.to_builtin_format()
            self.assertTrue(
                builtin["safe_defaults"] or builtin["safety_blocks"],
                f"{skill}: builtin should carry safe_defaults or safety_blocks",
            )

    # ── Audit trace evidence is grounded in SKILL.md ───────────

    def test_trace_evidence_quotes_are_in_skill_md(self) -> None:
        for skill in SKILLS:
            skill_text = (REAL_SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
            contract, _ = audit_skill_with_llm_traced(
                skill_text, MockLLM(fixture_name=skill), skill_id_hint=skill)
            for slot in contract.slots:
                for ev in slot.evidence:
                    quote = ev.get("quote", "").strip('"').strip("'")
                    self.assertIn(
                        quote, skill_text,
                        f"{skill}: slot '{slot.name}' quote not in SKILL.md: {quote}",
                    )

    # ── Committed mock artifact set is present ─────────────────

    def test_committed_mock_artifacts_exist(self) -> None:
        """The 8 artifacts per skill (mock mode) must be committed and non-empty."""
        for skill in SKILLS:
            mode_dir = REAL_SKILLS_DIR / skill / "mock"
            # Audit artifacts (request-independent)
            for name in ("SKILL.input.yaml", "audit_trace.json"):
                path = mode_dir / name
                self.assertTrue(path.exists(), f"missing {path}")
                self.assertGreater(path.stat().st_size, 0, f"empty {path}")
            # Compile artifacts (per request)
            for tag in REQUEST_TAGS:
                for name in ("decision.json", "input_slots.json", "normalized_input.md"):
                    path = mode_dir / tag / name
                    self.assertTrue(path.exists(), f"missing {path}")
                    self.assertGreater(path.stat().st_size, 0, f"empty {path}")

    def test_committed_audit_trace_is_well_formed(self) -> None:
        for skill in SKILLS:
            trace = json.loads(
                (REAL_SKILLS_DIR / skill / "mock" / "audit_trace.json").read_text(encoding="utf-8"))
            self.assertEqual(
                ["extract", "infer", "classify", "critique"],
                [s["stage"] for s in trace["stages"]],
                f"{skill}: committed trace stages mismatch",
            )
            self.assertEqual(skill, trace["contract"]["skill_id"])

    # ── helpers ────────────────────────────────────────────────

    def _discover(self, skill: str) -> DiscoveredContract:
        content = (REAL_SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        contract, _ = audit_skill_with_llm_traced(content, MockLLM(fixture_name=skill), skill_id_hint=skill)
        return contract

    def _compile_with_discovered(self, builtin: dict, request: str, root: Path) -> dict:
        """Inject the discovered contract, compile, then restore global state."""
        skill_id = builtin["skill_id"]
        saved = CONTRACT_REGISTRY._overlay.get(skill_id)
        CONTRACT_REGISTRY.register(skill_id, builtin)
        try:
            return compile_against_skill(request, skill_id=skill_id, root=root, out_dir=root / ".run")
        finally:
            if saved is None:
                CONTRACT_REGISTRY._overlay.pop(skill_id, None)
            else:
                CONTRACT_REGISTRY.register(skill_id, saved)

    def _assert_normalized_input(self, result: dict, skill: str, tag: str) -> None:
        normalized = result["normalized_input"]
        self.assertEqual(NORMALIZED_SKILL_INPUT_VERSION, normalized["schema_version"])
        self.assertEqual(skill, normalized["skill_id"])
        self.assertEqual(skill, result["skill_id"])
        self.assertIn(result["decision_kind"], VALID_DECISIONS,
                      f"{skill}/{tag}: invalid decision {result['decision_kind']}")

        decision = result["decision"]
        self.assertEqual(result["decision_kind"], decision["kind"])
        self.assertIsInstance(decision["questions"], list)

        md = result["normalized_input_markdown"]
        self.assertTrue(md.strip(), f"{skill}/{tag}: normalized_input.md empty")
        self.assertIn("# Normalized Skill Input", md)
        self.assertIn(skill, md)


if __name__ == "__main__":
    unittest.main()
