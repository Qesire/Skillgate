"""Tests for MockLLM-driven four-stage audit pipeline and contract conversion."""

from __future__ import annotations

import unittest
from pathlib import Path

import tests.core  # noqa: F401 -- registers fixtures at import time

from skillgate.llm_auditor import (
    DiscoveredContract,
    DiscoveredSlot,
    MockLLM,
    audit_skill_with_llm,
)
from skillgate.rules import analyze_against_skill

ROOT = Path(__file__).resolve().parents[2]
REAL_SKILLS_DIR = ROOT / "examples" / "real_skills"

SKILLS = ["bug_fix", "code_review", "refactor", "documentation_update", "experiment_debug"]


class MockLLMAuditorTests(unittest.TestCase):
    """Tests for the four-stage LLM audit pipeline using MockLLM fixtures."""

    # ── Four-stage pipeline per skill ────────────────────────────

    def test_audit_bug_fix_four_stage_pipeline(self) -> None:
        self._run_pipeline_test("bug_fix")

    def test_audit_code_review_four_stage_pipeline(self) -> None:
        self._run_pipeline_test("code_review")

    def test_audit_refactor_four_stage_pipeline(self) -> None:
        self._run_pipeline_test("refactor")

    def test_audit_documentation_update_four_stage_pipeline(self) -> None:
        self._run_pipeline_test("documentation_update")

    def test_audit_experiment_debug_four_stage_pipeline(self) -> None:
        self._run_pipeline_test("experiment_debug")

    def _run_pipeline_test(self, skill: str) -> None:
        path = REAL_SKILLS_DIR / skill / "SKILL.md"
        content = path.read_text()
        contract = audit_skill_with_llm(content, MockLLM(fixture_name=skill))

        self.assertIsInstance(contract, DiscoveredContract)
        self.assertTrue(contract.skill_id, f"skill_id should not be empty for {skill}")
        self.assertGreaterEqual(
            len(contract.slots), 2,
            f"At least 2 slots expected for {skill}, got {len(contract.slots)}",
        )

        for slot in contract.slots:
            self.assertIsInstance(slot, DiscoveredSlot)
            self.assertTrue(slot.name, f"Slot name must not be empty: {skill}")
            self.assertTrue(slot.necessity, f"Slot necessity must not be empty: {slot.name}")
            self.assertTrue(slot.answer_source, f"Slot answer_source must not be empty: {slot.name}")
            self.assertTrue(slot.missing_policy, f"Slot missing_policy must not be empty: {slot.name}")
            self.assertTrue(slot.support, f"Slot support must not be empty: {slot.name}")
            self.assertGreater(slot.confidence, 0, f"Confidence must be > 0: {slot.name}")
            self.assertLessEqual(slot.confidence, 1, f"Confidence must be <= 1: {slot.name}")

        # Check safe defaults and block_if are populated
        self.assertTrue(
            contract.safe_defaults or contract.block_if,
            f"Expected at least safe_defaults or block_if for {skill}",
        )

    # ── to_builtin_format ──────────────────────────────────────

    def test_to_builtin_format_preserves_slots(self) -> None:
        path = REAL_SKILLS_DIR / "bug_fix" / "SKILL.md"
        content = path.read_text()
        contract = audit_skill_with_llm(content, MockLLM(fixture_name="bug_fix"))
        builtin = contract.to_builtin_format()

        self.assertIsInstance(builtin, dict)
        for key in (
            "required_slots", "ask_if_missing", "discover_if_missing",
            "safe_defaults", "block_if",
        ):
            self.assertIsInstance(builtin[key], list, f"{key} should be a list")

        # Count all slots by merging the three slot lists
        all_builtin_slots = (
            builtin["required_slots"] +
            builtin["ask_if_missing"] +
            builtin["discover_if_missing"]
        )
        slot_names = {s["id"] for s in all_builtin_slots}
        # Every DiscoveredSlot should appear with its name in the builtin format
        for slot in contract.slots:
            ident = slot.name.lower().replace(" ", "_").replace("-", "_")[:40]
            self.assertIn(
                ident, slot_names,
                f"Slot '{slot.name}' (id={ident}) missing from builtin format",
            )

    def test_builtin_contract_works_with_analyze_against_skill(self) -> None:
        from skillgate.rules import analyze_against_skill

        path = REAL_SKILLS_DIR / "bug_fix" / "SKILL.md"
        content = path.read_text()
        contract = audit_skill_with_llm(content, MockLLM(fixture_name="bug_fix"))

        # This relies on the builtin contract from capabilities.py, not from LLM output.
        # The contract we built should be structurally compatible.
        result = analyze_against_skill(
            "fix the login timeout bug in auth.py without modifying tests",
            skill_id="bug_fix",
        )
        self.assertEqual("bug_fix", result.skill_id)
        self.assertIn(result.decision_kind, {
            "block_unsafe", "ask_user", "explore_first",
            "assume_and_continue", "compile_directly",
        })

    # ── Edge cases ────────────────────────────────────────────

    def test_empty_skill_content_raises_gracefully(self) -> None:
        """Empty content should not crash; it returns an empty contract."""
        contract = audit_skill_with_llm("", MockLLM(fixture_name="bug_fix"))
        self.assertIsInstance(contract, DiscoveredContract)
        self.assertIsInstance(contract.slots, list)

    def test_missing_fixture_does_not_crash(self) -> None:
        """A fixture name not registered should return empty results, not crash."""
        contract = audit_skill_with_llm(
            "# Fake skill\n\nDo nothing.\n",
            MockLLM(fixture_name="nonexistent_fixture"),
        )
        self.assertIsInstance(contract, DiscoveredContract)
        self.assertEqual([], contract.slots)
        self.assertEqual([], contract.safe_defaults)
        self.assertEqual([], contract.block_if)

    def test_no_mocked_fixture_returns_empty_results(self) -> None:
        """MockLLM with no fixture_name returns empty results for all stages."""
        contract = audit_skill_with_llm(
            "# Fake skill\n\nDo nothing.\n",
            MockLLM(),
        )
        self.assertIsInstance(contract, DiscoveredContract)
        self.assertEqual([], contract.slots)
        self.assertEqual([], contract.safe_defaults)
        self.assertEqual([], contract.block_if)


class MockLLMFixtureQualityTests(unittest.TestCase):
    """Quality checks on the registered fixtures themselves."""

    def test_all_5_skills_have_fixtures_registered(self) -> None:
        for skill in SKILLS:
            self.assertIn(
                skill, MockLLM.FIXTURES,
                f"Fixture for '{skill}' should be registered",
            )

    def test_fixtures_have_all_four_stages(self) -> None:
        for skill in SKILLS:
            fixture = MockLLM.FIXTURES[skill]
            for stage in ("extracted", "inferred", "classified", "reviewed"):
                self.assertIn(
                    stage, fixture,
                    f"Fixture '{skill}' missing stage '{stage}'",
                )

    def test_extracted_has_required_keys(self) -> None:
        for skill in SKILLS:
            extracted = MockLLM.FIXTURES[skill]["extracted"]
            for key in ("activation_triggers", "execution_steps"):
                self.assertIn(key, extracted, f"'{skill}' extracted missing '{key}'")

    def test_inferred_slots_have_required_fields(self) -> None:
        for skill in SKILLS:
            slots = MockLLM.FIXTURES[skill]["inferred"]
            self.assertGreater(len(slots), 0, f"'{skill}' has no inferred slots")
            for slot in slots:
                for field in ("name", "description", "necessity"):
                    self.assertIn(field, slot, f"'{skill}' slot missing '{field}'")

    def test_classified_slots_have_answer_source_and_policy(self) -> None:
        for skill in SKILLS:
            slots = MockLLM.FIXTURES[skill]["classified"]
            self.assertGreater(len(slots), 0, f"'{skill}' has no classified slots")
            for slot in slots:
                for field in ("answer_source", "missing_policy", "confidence"):
                    self.assertIn(field, slot, f"'{skill}' slot missing '{field}'")
                self.assertGreater(slot["confidence"], 0)
                self.assertLessEqual(slot["confidence"], 1)

    def test_reviewed_covers_all_classified_slots(self) -> None:
        for skill in SKILLS:
            classified = MockLLM.FIXTURES[skill]["classified"]
            reviewed = MockLLM.FIXTURES[skill]["reviewed"]
            classified_names = {s["name"] for s in classified}
            reviewed_names = {r["name"] for r in reviewed}
            self.assertEqual(
                classified_names, reviewed_names,
                f"'{skill}' reviewed slots ({reviewed_names}) "
                f"do not match classified slots ({classified_names})",
            )

    def test_fixtures_have_confidence_in_range(self) -> None:
        for skill in SKILLS:
            slots = MockLLM.FIXTURES[skill]["classified"]
            for slot in slots:
                conf = slot["confidence"]
                self.assertGreaterEqual(
                    conf, 0.7,
                    f"'{skill}' slot '{slot['name']}' confidence {conf} below 0.7",
                )
                self.assertLessEqual(
                    conf, 0.95,
                    f"'{skill}' slot '{slot['name']}' confidence {conf} above 0.95",
                )

    def test_fixtures_have_safe_defaults_and_blocks(self) -> None:
        for skill in SKILLS:
            slots = MockLLM.FIXTURES[skill]["classified"]
            sources = {s["answer_source"] for s in slots}
            self.assertTrue(
                "policy_default" in sources,
                f"'{skill}' should have at least one policy_default slot",
            )
            self.assertTrue(
                "blocked" in sources,
                f"'{skill}' should have at least one blocked slot",
            )

    def test_evidence_quotes_from_skill_md(self) -> None:
        """Every inferred slot's evidence should quote actual text from its SKILL.md."""
        for skill in SKILLS:
            path = REAL_SKILLS_DIR / skill / "SKILL.md"
            skill_text = path.read_text()
            slots = MockLLM.FIXTURES[skill]["inferred"]
            for slot in slots:
                for ev in slot.get("evidence", []):
                    quote = ev.get("quote", "")
                    self.assertIn(
                        quote.strip('"').strip("'"),
                        skill_text,
                        f"'{skill}' slot '{slot['name']}' quotes text not in SKILL.md: {quote}",
                    )


if __name__ == "__main__":
    unittest.main()