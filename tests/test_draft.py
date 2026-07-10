"""Tests for SkillInvocationDraft state machine and slot patch protocol."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skillgate.capabilities import CONTRACT_REGISTRY
from skillgate.context import discover_context
from skillgate.draft import (
    SKILL_INVOCATION_DRAFT_VERSION,
    apply_discovery_result,
    apply_slot_patch,
    bind_user_request,
    compute_draft_status,
    create_discovery_plan,
    create_draft,
    create_input_questions,
    load_draft,
    save_draft,
)
from skillgate.draft_lifecycle import validate_transition
from skillgate.rules import analyze_against_skill
from skillgate.schema import hash_text, migrate_contract_v2_to_v3

ROOT = Path(__file__).resolve().parents[1]


def _bug_fix_contract_v3() -> dict:
    contract_v2 = CONTRACT_REGISTRY.get("bug_fix")
    return migrate_contract_v2_to_v3(contract_v2)


def _make_draft(run_dir: Path | None = None) -> dict:
    contract_v3 = _bug_fix_contract_v3()
    contract_v2 = CONTRACT_REGISTRY.get("bug_fix")
    contract_sha = hash_text(json.dumps(contract_v2, sort_keys=True, ensure_ascii=False))
    contract_path = str(run_dir / "skill_contract.json") if run_dir else "/tmp/contract.json"
    if run_dir:
        (run_dir / "skill_contract.json").write_text(
            json.dumps(contract_v2, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return create_draft(
        raw_request="fix the login bug",
        skill_id="bug_fix",
        contract_v3=contract_v3,
        run_id="sg-test123",
        contract_sha256=contract_sha,
        contract_path=contract_path,
    )


class CreateDraftTests(unittest.TestCase):
    def test_create_draft_all_slots_unresolved(self) -> None:
        draft = _make_draft()
        self.assertEqual(SKILL_INVOCATION_DRAFT_VERSION, draft["schema_version"])
        self.assertEqual("draft", draft["status"])
        self.assertEqual("bug_fix", draft["skill_id"])
        self.assertTrue(len(draft["slots"]) > 0)
        for sid, slot in draft["slots"].items():
            self.assertEqual("unresolved", slot["state"], f"{sid} should be unresolved")
            self.assertIsNone(slot["value"])
            self.assertFalse(slot["confirmed"])

    def test_contract_referenced_by_hash_not_embedded(self) -> None:
        draft = _make_draft()
        self.assertIn("contract_sha256", draft)
        self.assertIn("contract_path", draft)
        # The contract body should NOT be embedded in the draft
        self.assertNotIn("contract", draft)
        self.assertNotIn("skill_contract", draft)


class BindUserRequestTests(unittest.TestCase):
    def test_bind_user_request_transitions_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = ROOT / "examples" / "python_pytest_minimal"
            context = discover_context(root)
            analysis = analyze_against_skill(
                "这个测试挂了，帮我修一下", skill_id="bug_fix", context=context,
            )
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            draft = _make_draft(run_dir)
            draft = bind_user_request(draft, analysis)
            self.assertNotEqual("draft", draft["status"])


class SlotPatchTests(unittest.TestCase):
    def test_apply_slot_patch_set(self) -> None:
        draft = _make_draft()
        slot_id = next(iter(draft["slots"]))
        draft = apply_slot_patch(draft, [
            {"slot_id": slot_id, "op": "set", "value": "test_value", "source": "user"},
        ])
        self.assertEqual("user_bound", draft["slots"][slot_id]["state"])
        self.assertEqual("test_value", draft["slots"][slot_id]["value"])
        self.assertFalse(draft["slots"][slot_id]["confirmed"])

    def test_apply_slot_patch_confirm(self) -> None:
        draft = _make_draft()
        slot_id = next(iter(draft["slots"]))
        draft = apply_slot_patch(draft, [
            {"slot_id": slot_id, "op": "set", "value": "test_value"},
            {"slot_id": slot_id, "op": "confirm"},
        ])
        self.assertEqual("confirmed", draft["slots"][slot_id]["state"])
        self.assertTrue(draft["slots"][slot_id]["confirmed"])

    def test_apply_slot_patch_reject(self) -> None:
        draft = _make_draft()
        slot_id = next(iter(draft["slots"]))
        draft = apply_slot_patch(draft, [
            {"slot_id": slot_id, "op": "set", "value": "test_value"},
            {"slot_id": slot_id, "op": "reject"},
        ])
        self.assertEqual("rejected", draft["slots"][slot_id]["state"])
        self.assertIsNone(draft["slots"][slot_id]["value"])
        self.assertTrue(draft["slots"][slot_id]["confirmed"])

    def test_apply_slot_patch_clear(self) -> None:
        draft = _make_draft()
        slot_id = next(iter(draft["slots"]))
        draft = apply_slot_patch(draft, [
            {"slot_id": slot_id, "op": "set", "value": "test_value"},
            {"slot_id": slot_id, "op": "clear"},
        ])
        self.assertEqual("unresolved", draft["slots"][slot_id]["state"])
        self.assertIsNone(draft["slots"][slot_id]["value"])
        self.assertFalse(draft["slots"][slot_id]["confirmed"])

    def test_apply_slot_patch_unknown_slot_raises(self) -> None:
        draft = _make_draft()
        with self.assertRaisesRegex(ValueError, "unknown slot id"):
            apply_slot_patch(draft, [{"slot_id": "nonexistent", "op": "set", "value": "x"}])


class ComputeStatusTests(unittest.TestCase):
    def test_needs_confirmation_when_values_unconfirmed(self) -> None:
        draft = _make_draft()
        # Set all slots to user_bound with values but unconfirmed
        for sid, slot in draft["slots"].items():
            slot["state"] = "user_bound"
            slot["value"] = "something"
            slot["confirmed"] = False
            slot["importance"] = "required"
        self.assertEqual("needs_confirmation", compute_draft_status(draft))

    def test_ready_when_all_required_confirmed(self) -> None:
        draft = _make_draft()
        for sid, slot in draft["slots"].items():
            slot["state"] = "confirmed"
            slot["value"] = "something"
            slot["confirmed"] = True
            slot["importance"] = "required"
        self.assertEqual("ready", compute_draft_status(draft))

    def test_conflicted_when_slot_conflicted(self) -> None:
        draft = _make_draft()
        first_slot = next(iter(draft["slots"].values()))
        first_slot["state"] = "conflicted"
        self.assertEqual("conflicted", compute_draft_status(draft))


class DraftPersistenceTests(unittest.TestCase):
    def test_draft_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            draft = _make_draft(run_dir)
            save_draft(run_dir, draft)
            loaded = load_draft(run_dir)
            self.assertEqual(draft["run_id"], loaded["run_id"])
            self.assertEqual(draft["skill_id"], loaded["skill_id"])
            self.assertEqual(set(draft["slots"].keys()), set(loaded["slots"].keys()))

    def test_contract_hash_mismatch_sets_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            draft = _make_draft(run_dir)
            # Tamper with the contract file
            contract_path = run_dir / "skill_contract.json"
            contract_path.write_text('{"skill_id": "tampered"}', encoding="utf-8")
            save_draft(run_dir, draft)
            loaded = load_draft(run_dir)
            self.assertEqual("invalid", loaded["status"])


class DiscoveryPlanTests(unittest.TestCase):
    def test_create_discovery_plan_for_unresolved_discoverable(self) -> None:
        draft = _make_draft()
        sid = next(iter(draft["slots"]))
        draft["slots"][sid]["acquisition"] = {"strategy": "discover_then_confirm", "resolver": "repository_path_search"}
        plan = create_discovery_plan(draft)
        self.assertTrue(len(plan["requests"]) > 0)
        self.assertEqual(sid, plan["requests"][0]["slot_id"])
        self.assertEqual("repository_path_search", plan["requests"][0]["resolver"])


class InputQuestionsTests(unittest.TestCase):
    def test_create_input_questions_for_ask_user_slots(self) -> None:
        draft = _make_draft()
        sid = next(iter(draft["slots"]))
        slot = draft["slots"][sid]
        slot["acquisition"] = {"strategy": "ask_user"}
        slot["confirmation"] = {"prompt": "What files should I investigate?", "policy": "never"}
        slot["importance"] = "required"
        questions = create_input_questions(draft)
        self.assertTrue(len(questions) > 0)
        self.assertEqual(sid, questions[0]["slot_id"])
        self.assertEqual("What files should I investigate?", questions[0]["text"])
        self.assertTrue(questions[0]["required"])


class LifecycleTransitionTests(unittest.TestCase):
    def test_legal_transition_ok(self) -> None:
        validate_transition("draft", "needs_discovery")
        validate_transition("needs_discovery", "needs_user_input")
        validate_transition("needs_user_input", "needs_confirmation")
        validate_transition("needs_confirmation", "ready")

    def test_illegal_transition_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "illegal draft transition"):
            validate_transition("ready", "draft")
        with self.assertRaisesRegex(ValueError, "illegal draft transition"):
            validate_transition("cancelled", "needs_discovery")


class DiscoveryResultTests(unittest.TestCase):
    def test_apply_discovery_result_resolved(self) -> None:
        draft = _make_draft()
        sid = next(iter(draft["slots"]))
        draft["slots"][sid]["acquisition"] = {"strategy": "discover_then_confirm"}
        draft = apply_discovery_result(draft, {
            sid: {"status": "resolved", "value": "pytest tests/", "evidence_ids": ["ev-001"]},
        })
        self.assertEqual("discovered", draft["slots"][sid]["state"])
        self.assertEqual("pytest tests/", draft["slots"][sid]["value"])

    def test_apply_discovery_result_conflict(self) -> None:
        draft = _make_draft()
        sid = next(iter(draft["slots"]))
        draft = apply_discovery_result(draft, {
            sid: {"status": "conflict", "candidates": ["src/a.py", "src/b.py"]},
        })
        self.assertEqual("conflicted", draft["slots"][sid]["state"])
        self.assertIsNone(draft["slots"][sid]["value"])
        self.assertEqual(["src/a.py", "src/b.py"], draft["slots"][sid]["candidates"])


if __name__ == "__main__":
    unittest.main()
