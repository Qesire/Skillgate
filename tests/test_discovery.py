"""Tests for pre-activation discovery protocol."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from skillgate.capabilities import CONTRACT_REGISTRY
from skillgate.cli import main as cli_main
from skillgate.discovery import (
    SKILL_DISCOVERY_PLAN_VERSION,
    SKILL_DISCOVERY_RESULT_VERSION,
    HostResolverProtocol,
    apply_discovery_to_draft,
    build_discovery_plan,
    run_discovery,
)
from skillgate.draft import create_draft, save_draft
from skillgate.schema import hash_text, migrate_contract_v2_to_v3

ROOT = Path(__file__).resolve().parents[1]


def _make_draft_with_discoverable_slot(tmp: Path) -> tuple[dict, Path]:
    """Create a draft with one unresolved discoverable slot."""
    contract_v2 = CONTRACT_REGISTRY.get("bug_fix")
    contract_v3 = migrate_contract_v2_to_v3(contract_v2)
    run_dir = tmp / "run"
    run_dir.mkdir()
    contract_sha = hash_text(json.dumps(contract_v2, sort_keys=True, ensure_ascii=False))
    (run_dir / "skill_contract.json").write_text(
        json.dumps(contract_v2, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    draft = create_draft(
        raw_request="fix the login bug",
        skill_id="bug_fix",
        contract_v3=contract_v3,
        run_id="sg-test",
        contract_sha256=contract_sha,
        contract_path=str(run_dir / "skill_contract.json"),
    )
    # Mark first slot as discoverable
    sid = next(iter(draft["slots"]))
    draft["slots"][sid]["acquisition"] = {
        "strategy": "discover_then_confirm",
        "resolver": "project_test_command",
    }
    save_draft(run_dir, draft)
    return draft, run_dir


class BuildDiscoveryPlanTests(unittest.TestCase):
    def test_plan_has_requests_for_discoverable_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft, _ = _make_draft_with_discoverable_slot(Path(tmp))
            plan = build_discovery_plan(draft)
            self.assertEqual(SKILL_DISCOVERY_PLAN_VERSION, plan["schema_version"])
            self.assertTrue(len(plan["requests"]) > 0)
            req = plan["requests"][0]
            self.assertIn("slot_id", req)
            self.assertEqual("project_test_command", req["resolver"])
            self.assertEqual("read_only", req["access"])

    def test_plan_empty_when_no_discoverable_slots(self) -> None:
        contract_v2 = CONTRACT_REGISTRY.get("bug_fix")
        contract_v3 = migrate_contract_v2_to_v3(contract_v2)
        draft = create_draft("fix bug", "bug_fix", contract_v3, "sg-x", hash_text("c"), "/tmp/c.json")
        # All slots have default acquisition (ask_user), no discoverable
        plan = build_discovery_plan(draft)
        self.assertEqual([], plan["requests"])


class RunDiscoveryTests(unittest.TestCase):
    def test_resolves_test_command_from_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
                encoding="utf-8",
            )
            draft, _ = _make_draft_with_discoverable_slot(root)
            result = run_discovery(draft, root)
            self.assertEqual(SKILL_DISCOVERY_RESULT_VERSION, result["schema_version"])
            sid = next(iter(draft["slots"]))
            self.assertEqual("resolved", result["results"][sid]["status"])
            self.assertEqual("pytest", result["results"][sid]["value"])

    def test_unresolved_for_unknown_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft, _ = _make_draft_with_discoverable_slot(Path(tmp))
            sid = next(iter(draft["slots"]))
            draft["slots"][sid]["acquisition"]["resolver"] = "nonexistent_resolver"
            result = run_discovery(draft, Path(tmp))
            self.assertEqual("unresolved", result["results"][sid]["status"])

    def test_conflict_on_multiple_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
            (root / "src" / "b.py").write_text("# b\n", encoding="utf-8")
            draft, _ = _make_draft_with_discoverable_slot(root)
            sid = next(iter(draft["slots"]))
            draft["slots"][sid]["acquisition"]["resolver"] = "repository_path_search"
            result = run_discovery(draft, root)
            self.assertEqual("conflict", result["results"][sid]["status"])
            self.assertIsNone(result["results"][sid]["value"])
            self.assertTrue(len(result["results"][sid]["candidates"]) >= 2)


class ApplyDiscoveryTests(unittest.TestCase):
    def test_apply_resolved_result_updates_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft, _ = _make_draft_with_discoverable_slot(Path(tmp))
            sid = next(iter(draft["slots"]))
            result = {
                "schema_version": SKILL_DISCOVERY_RESULT_VERSION,
                "run_id": "sg-test",
                "results": {
                    sid: {
                        "status": "resolved",
                        "value": "pytest tests/",
                        "evidence_ids": ["disc-ev-001"],
                        "evidence": [{"path": "pyproject.toml", "text": "pytest"}],
                    }
                },
            }
            draft = apply_discovery_to_draft(draft, result)
            self.assertEqual("discovered", draft["slots"][sid]["state"])
            self.assertEqual("pytest tests/", draft["slots"][sid]["value"])

    def test_full_flow_status_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
                encoding="utf-8",
            )
            draft, _ = _make_draft_with_discoverable_slot(root)
            plan = build_discovery_plan(draft)
            result = run_discovery(draft, root)
            draft = apply_discovery_to_draft(draft, result)
            # After discovery, the slot should be discovered (not unresolved)
            sid = next(iter(draft["slots"]))
            self.assertEqual("discovered", draft["slots"][sid]["state"])


class CLICommandsTests(unittest.TestCase):
    def test_cli_discovery_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft, run_dir = _make_draft_with_discoverable_slot(Path(tmp))
            output = StringIO()
            with redirect_stdout(output):
                cli_main(["discovery-plan", str(run_dir)])
            plan = json.loads(output.getvalue())
            self.assertEqual(SKILL_DISCOVERY_PLAN_VERSION, plan["schema_version"])
            self.assertTrue(len(plan["requests"]) > 0)

    def test_cli_discover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
                encoding="utf-8",
            )
            draft, run_dir = _make_draft_with_discoverable_slot(root)
            output = StringIO()
            with redirect_stdout(output):
                cli_main(["discover", str(run_dir), "--root", str(root)])
            summary = json.loads(output.getvalue())
            self.assertIn("status", summary)
            self.assertIn("results", summary)


class HostResolverProtocolTests(unittest.TestCase):
    def test_protocol_exists(self) -> None:
        self.assertTrue(hasattr(HostResolverProtocol, "resolve"))


if __name__ == "__main__":
    unittest.main()
