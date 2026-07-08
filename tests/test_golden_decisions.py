from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from skillgate.compiler import compile_request
from skillgate.context import discover_context
from skillgate.rules import analyze_request, classify_task
from skillgate.schema import validate_taskbrief


ROOT = Path(__file__).resolve().parents[1]


class GoldenDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        data = yaml.safe_load((ROOT / "tests" / "fixtures" / "p0_golden_requests.yaml").read_text(encoding="utf-8"))
        cls.items = data["items"]
        cls.context = discover_context(ROOT)

    def test_all_golden_decisions_match(self) -> None:
        wrong = []
        for item in self.items:
            analysis = analyze_request(item["raw_request"], self.context)
            if analysis.decision_kind != item["expected_decision"]:
                wrong.append(
                    {
                        "id": item["id"],
                        "expected": item["expected_decision"],
                        "actual": analysis.decision_kind,
                        "task_kind": analysis.task_kind,
                        "request": item["raw_request"],
                    }
                )
        self.assertEqual([], wrong)

    def test_all_golden_task_kinds_match(self) -> None:
        wrong = []
        for item in self.items:
            actual = classify_task(item["raw_request"])
            if actual != item["task_kind"]:
                wrong.append({"id": item["id"], "expected": item["task_kind"], "actual": actual})
        self.assertEqual([], wrong)

    def test_compile_writes_valid_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_request("pytest 里 test_parser_escape 挂了，修源码，不要动测试", root=ROOT, out_dir=Path(tmp))
            decision = json.loads((Path(tmp) / "decision.json").read_text(encoding="utf-8"))
            taskbrief = json.loads((Path(tmp) / "taskbrief.json").read_text(encoding="utf-8"))
            validate_taskbrief(taskbrief)
            self.assertEqual("explore_first", decision["kind"])
            self.assertTrue((Path(tmp) / "taskbrief.md").read_text(encoding="utf-8").startswith("# TaskBrief"))
            execution_brief = (Path(tmp) / "execution_brief.md").read_text(encoding="utf-8")
            self.assertTrue(execution_brief.startswith("# Execution Brief"))
            self.assertEqual(execution_brief, result["execution_brief"])
            self.assertEqual(result["run_id"], taskbrief["run_id"])

    def test_explicit_bug_repair_contract_authorizes_bounded_execution(self) -> None:
        request = (
            "Repair the defect in python_programs/gcd.py so the public function gcd satisfies its intended "
            "algorithmic behavior. Preserve the public API, modify only that source file, and run the public verification."
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_request(request, root=ROOT, out_dir=Path(tmp))

        taskbrief = result["taskbrief"]
        scope_in = {item["text"] for item in taskbrief["scope_in"]}
        scope_out = {item["text"] for item in taskbrief["scope_out"]}
        constraints = {item["text"] for item in taskbrief["task_frame"]["user_constraints"]}
        self.assertEqual("bug_fix", taskbrief["task_frame"]["kind"])
        self.assertTrue(len(scope_in) > 0, "scope_in should contain safe default policies")
        self.assertTrue(any("minimal" in text.lower() or "bounded" in text.lower() for text in scope_in),
                        f"scope_in should reference bounded scope: {scope_in}")
        self.assertNotIn("Executing code changes", scope_out)
        self.assertNotIn("Running tests", scope_out)
        self.assertTrue(len(constraints) > 0 or len(scope_in) > 0,
                        "at least one of user_constraints or scope_in should capture the user's constraints")
        self.assertIn("python_programs/gcd.py", taskbrief["task_frame"]["raw_request"])


if __name__ == "__main__":
    unittest.main()
