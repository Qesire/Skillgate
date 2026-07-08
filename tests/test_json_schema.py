from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from skillgate.compiler import compile_request
from skillgate.compiler import compile_against_skill
from skillgate.cli import main as cli_main
from skillgate.json_schema import (
    decision_json_schema,
    normalized_skill_input_json_schema,
    skill_input_contract_json_schema,
    json_schema_errors,
    taskbrief_json_schema,
    validate_published_schemas,
)
from skillgate.skill_auditor import audit_skill

ROOT = Path(__file__).resolve().parents[1]


class JsonSchemaTests(unittest.TestCase):
    def test_generated_taskbrief_conforms_to_published_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_request(
                "这个测试挂了，帮我修一下",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )

            errors = json_schema_errors(result["taskbrief"], taskbrief_json_schema())

            self.assertEqual([], errors)

    def test_schema_rejects_undeclared_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_request(
                "完善 README",
                root=ROOT / "examples" / "docs_only",
                out_dir=Path(tmp) / "run",
            )
            result["taskbrief"]["undeclared"] = True

            errors = json_schema_errors(result["taskbrief"], taskbrief_json_schema())

            self.assertTrue(any("Additional properties are not allowed" in error for error in errors), errors)

    def test_repository_schemas_match_canonical_generator(self) -> None:
        result = validate_published_schemas(ROOT / "schemas")

        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(
            [
                "clarification_answers.v2.schema.json",
                "clarifications.v2.schema.json",
                "decision.v1.schema.json",
                "input_slot_state.v1.schema.json",
                "normalized_skill_input.v1.schema.json",
                "recompile.v2.schema.json",
                "skill_input_contract.v1.schema.json",
            ],
            result["schemas"],
        )

    def test_core_skill_contract_and_normalized_input_match_published_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_path = ROOT / "examples" / "real_skills" / "bug_fix" / "SKILL.md"
            contract = audit_skill(skill_path)
            result = compile_against_skill(
                "这个报错帮我修一下",
                skill_id="bug_fix",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )

            self.assertEqual([], json_schema_errors(contract, skill_input_contract_json_schema()))
            self.assertEqual([], json_schema_errors(result["normalized_input"], normalized_skill_input_json_schema()))
            self.assertEqual([], json_schema_errors(result["decision"], decision_json_schema()))

    def test_installed_cli_surface_exports_and_checks_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "schemas"
            with redirect_stdout(StringIO()):
                cli_main(["schemas", "--out", str(output_dir)])
                cli_main(["schemas", "--check", "--out", str(output_dir)])

            self.assertEqual(7, len(list(output_dir.glob("*.json"))))

    def test_cli_reports_package_version(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit) as caught, redirect_stdout(output):
            cli_main(["--version"])

        self.assertEqual(0, caught.exception.code)
        self.assertEqual("skillgate 0.3.0", output.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
