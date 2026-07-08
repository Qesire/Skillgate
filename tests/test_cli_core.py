from __future__ import annotations

import json
import importlib.resources
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from skillgate.cli import main as cli_main
from skillgate.schema import SKILL_INPUT_CONTRACT_VERSION
from skillgate.skill_auditor import audit_skill


ROOT = Path(__file__).resolve().parents[1]
BUG_FIX_SKILL = ROOT / "examples" / "real_skills" / "bug_fix" / "SKILL.md"


class CoreCliTests(unittest.TestCase):
    def test_bundled_metaskill_matches_source_skill(self) -> None:
        source = ROOT / "skills" / "skillgate-preactivation" / "SKILL.md"
        packaged = importlib.resources.files("skillgate").joinpath(
            "skills", "skillgate-preactivation", "SKILL.md"
        )

        self.assertEqual(source.read_text(encoding="utf-8"), packaged.read_text(encoding="utf-8"))

    def test_audit_skill_json_outputs_valid_contract(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            cli_main(["audit-skill", str(BUG_FIX_SKILL), "--json"])

        contract = json.loads(output.getvalue())
        self.assertEqual(SKILL_INPUT_CONTRACT_VERSION, contract["schema_version"])
        self.assertEqual("bug_fix", contract["skill_id"])
        self.assertTrue(contract["required_slots"])

    def test_audit_skill_write_outputs_yaml_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "SKILL.input.yaml"

            with redirect_stdout(StringIO()):
                cli_main(["audit-skill", str(BUG_FIX_SKILL), "--write", str(output_path)])

            contract = yaml.safe_load(output_path.read_text(encoding="utf-8"))
            self.assertEqual(SKILL_INPUT_CONTRACT_VERSION, contract["schema_version"])
            self.assertEqual("bug_fix", contract["skill_id"])
            self.assertTrue(contract["required_slots"])

    def test_audit_plain_frontmatter_skill_preserves_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "humaneval-python-solver"
            skill_dir.mkdir()
            skill_path = skill_dir / "SKILL.md"
            skill_path.write_text(
                """---
name: humaneval-python-solver
description: Use when solving HumanEval-style Python functions.
---

# HumanEval Python Solver

Read the stub, tests, and evaluator. Implement source only and verify with pytest.
""",
                encoding="utf-8",
            )

            contract = audit_skill(skill_path, use_builtin_fallback=False)

            self.assertEqual("humaneval-python-solver", contract["skill_id"])
            self.assertEqual("Humaneval Python Solver", contract["skill_name"])
            self.assertEqual("Use when solving HumanEval-style Python functions.", contract["skill_description"])

    def test_audit_external_frontmatter_skill_wins_over_builtin_keyword_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "rl-post-training"
            skill_dir.mkdir()
            skill_path = skill_dir / "SKILL.md"
            skill_path.write_text(
                """---
name: rl-post-training
description: Diagnostic guide for RL post-training bugs and tests.
---

# RL Post-Training

Use this skill when debugging training bugs, failing tests, and reward stagnation.
""",
                encoding="utf-8",
            )

            contract = audit_skill(skill_path)

            self.assertEqual("rl-post-training", contract["skill_id"])
            self.assertEqual("Rl Post Training", contract["skill_name"])
            self.assertEqual("Diagnostic guide for RL post-training bugs and tests.", contract["skill_description"])

    def test_compile_with_plain_frontmatter_skill_file_uses_real_skill_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_dir = tmp_path / "humaneval-python-solver"
            skill_dir.mkdir()
            skill_path = skill_dir / "SKILL.md"
            skill_path.write_text(
                """---
name: humaneval-python-solver
description: Use when solving HumanEval-style Python functions.
---

# HumanEval Python Solver

Read the stub, tests, and evaluator. Implement source only and verify with pytest.
""",
                encoding="utf-8",
            )
            out_dir = tmp_path / "run"

            output = StringIO()
            with redirect_stdout(output):
                cli_main([
                    "compile",
                    "--skill-file",
                    str(skill_path),
                    "--root",
                    str(tmp_path),
                    "--out",
                    str(out_dir),
                    "--json",
                    "Implement top_k_words in src/text_rank.py and verify with pytest.",
                ])

            payload = json.loads(output.getvalue())
            self.assertEqual("humaneval-python-solver", payload["skill_id"])
            self.assertEqual(len(payload["questions"]), len(set(payload["questions"])))
            normalized = (out_dir / "normalized_skill_input.md").read_text(encoding="utf-8")
            self.assertIn("`humaneval-python-solver`", normalized)
            self.assertIn("## Task Root", normalized)

    def test_compile_with_skill_file_outputs_normalized_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = StringIO()
            out_dir = Path(tmp) / "run"

            with redirect_stdout(output):
                cli_main([
                    "compile",
                    "--skill-file",
                    str(BUG_FIX_SKILL),
                    "--out",
                    str(out_dir),
                    "--json",
                    "这个报错帮我修一下",
                ])

            payload = json.loads(output.getvalue())
            self.assertEqual("bug_fix", payload["skill_id"])
            self.assertTrue((out_dir / "normalized_skill_input.json").is_file())
            self.assertTrue((out_dir / "normalized_skill_input.md").is_file())

    def test_compile_with_input_yaml_outputs_normalized_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = tmp_path / "SKILL.input.yaml"
            out_dir = tmp_path / "run"

            with redirect_stdout(StringIO()):
                cli_main(["audit-skill", str(BUG_FIX_SKILL), "--write", str(contract_path)])

            output = StringIO()
            with redirect_stdout(output):
                cli_main([
                    "compile",
                    "--skill-file",
                    str(contract_path),
                    "--out",
                    str(out_dir),
                    "--json",
                    "这个报错帮我修一下",
                ])

            lines = [line for line in output.getvalue().splitlines() if line.strip()]
            payload = json.loads("\n".join(lines[1:]))
            self.assertEqual("bug_fix", payload["skill_id"])
            self.assertTrue((out_dir / "skill_contract.json").is_file())

    def test_core_compile_answer_recompile_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            initial_dir = tmp_path / "initial"
            child_dir = tmp_path / "child"

            with redirect_stdout(StringIO()):
                cli_main([
                    "compile",
                    "--skill-file",
                    str(BUG_FIX_SKILL),
                    "--out",
                    str(initial_dir),
                    "这个报错帮我修一下",
                ])
                cli_main([
                    "answer",
                    str(initial_dir),
                    "Source changes only; do not change tests or public API.",
                ])
                cli_main([
                    "recompile",
                    str(initial_dir),
                    "--out",
                    str(child_dir),
                ])

            self.assertTrue((initial_dir / "clarifications.json").is_file())
            self.assertTrue((initial_dir / "clarification_answers.json").is_file())
            self.assertTrue((child_dir / "normalized_skill_input.json").is_file())
            self.assertTrue((child_dir / "recompile_metadata.json").is_file())
            child_input = json.loads((child_dir / "normalized_skill_input.json").read_text(encoding="utf-8"))
            self.assertNotEqual("ask_user", child_input["decision_kind"])


if __name__ == "__main__":
    unittest.main()
