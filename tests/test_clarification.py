from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from skillgate.cli import main as cli_main
from skillgate.clarification import (
    record_clarification_answer,
    record_clarification_answers,
    recompile_from_run,
    validate_clarification_artifacts,
    write_clarification_packet,
)
from skillgate.compiler import compile_request

ROOT = Path(__file__).resolve().parents[1]


class ClarificationLoopTests(unittest.TestCase):
    def test_answered_clarification_recompiles_to_child_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            initial_dir = tmp_root / "initial"
            child_dir = tmp_root / "child"

            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=initial_dir)
            self.assertEqual("ask_user", initial["decision"]["kind"])

            packet = write_clarification_packet(initial_dir, initial)
            self.assertEqual("open", packet["status"])
            self.assertEqual("q_001", packet["questions"][0]["id"])

            answers = record_clarification_answer(
                initial_dir,
                "面向首次贡献者，补充本地开发、测试命令和 PR 提交流程。",
            )
            answers = record_clarification_answer(
                initial_dir,
                "Only factual claims based on repository files.",
            )
            self.assertTrue(answers["complete"])

            child = recompile_from_run(initial_dir, out_dir=child_dir)
            self.assertNotEqual("ask_user", child["decision"]["kind"])
            self.assertTrue((child_dir / "taskbrief.md").exists())
            metadata = json.loads((child_dir / "recompile_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(initial["run_id"], metadata["parent_run_id"])
            self.assertEqual(child["run_id"], metadata["child_run_id"])
            self.assertEqual("q_001", metadata["answers"][0]["question_id"])
            validation = validate_clarification_artifacts(initial_dir, child_dir)
            self.assertTrue(validation["passed"], validation["errors"])

    def test_recompile_requires_answered_questions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initial_dir = Path(tmp) / "initial"
            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=initial_dir)
            write_clarification_packet(initial_dir, initial)

            with self.assertRaisesRegex(ValueError, "all clarification questions"):
                recompile_from_run(initial_dir, out_dir=Path(tmp) / "child")

    def test_answer_can_create_packet_from_existing_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initial_dir = Path(tmp) / "initial"
            compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=initial_dir)

            answers = record_clarification_answer(initial_dir, "面向维护者，补充发布流程。")
            answers = record_clarification_answer(initial_dir, "Only factual claims based on repository files.")

            self.assertTrue(answers["complete"])
            self.assertTrue((initial_dir / "clarifications.json").exists())
            packet = json.loads((initial_dir / "clarifications.json").read_text(encoding="utf-8"))
            self.assertEqual("answered", packet["status"])

    def test_cli_compile_answer_recompile_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            initial_dir = tmp_root / "initial"
            child_dir = tmp_root / "child"

            with redirect_stdout(StringIO()):
                cli_main(
                    [
                        "compile",
                        "-i",
                        "--root",
                        str(ROOT / "examples" / "docs_only"),
                        "--out",
                        str(initial_dir),
                        "完善 README",
                    ]
                )
                cli_main(
                    [
                        "answer",
                        str(initial_dir),
                        "面向首次贡献者，补充本地测试命令。",
                    ]
                )
                cli_main(
                    [
                        "answer",
                        str(initial_dir),
                        "Only factual claims based on repository files.",
                    ]
                )
                cli_main(
                    [
                        "recompile",
                        str(initial_dir),
                        "--out",
                        str(child_dir),
                    ]
                )

            self.assertTrue((initial_dir / "clarifications.json").exists())
            self.assertTrue((initial_dir / "clarification_answers.json").exists())
            self.assertTrue((child_dir / "recompile_metadata.json").exists())

    def test_multiple_answers_are_validated_and_written_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "initial"
            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=run_dir)
            multi = deepcopy(initial)
            multi["decision"]["questions"] = ["目标读者是谁？", "必须包含哪些章节？"]
            write_clarification_packet(run_dir, multi)

            with self.assertRaisesRegex(ValueError, "unknown clarification question id"):
                record_clarification_answers(
                    run_dir,
                    {"q_001": "首次贡献者", "q_999": "本地开发"},
                )
            unchanged = json.loads((run_dir / "clarifications.json").read_text(encoding="utf-8"))
            self.assertTrue(all(item["status"] == "open" for item in unchanged["questions"]))
            self.assertFalse((run_dir / "clarification_answers.json").exists())

            answers = record_clarification_answers(
                run_dir,
                {"q_001": "首次贡献者", "q_002": "本地开发、测试和 PR 流程"},
            )

            self.assertTrue(answers["complete"])
            self.assertEqual(2, len(answers["answers"]))
            self.assertEqual("skillgate.clarification_answers.v2", answers["schema_version"])

    def test_secret_like_answer_is_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "initial"
            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=run_dir)
            write_clarification_packet(run_dir, initial)

            with self.assertRaisesRegex(ValueError, "contains secret-like content"):
                record_clarification_answer(run_dir, "API key = sk-abcdefghijklmnop")

            packet_text = (run_dir / "clarifications.json").read_text(encoding="utf-8")
            self.assertNotIn("sk-abcdefghijklmnop", packet_text)
            self.assertFalse((run_dir / "clarification_answers.json").exists())

    def test_explicit_redaction_prevents_secret_propagation_to_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "initial"
            child_dir = tmp_path / "child"
            secret = "ghp_abcdefghijklmnopqrstuvwxyz1234"
            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=run_dir)
            write_clarification_packet(run_dir, initial)

            answers = record_clarification_answer(
                run_dir,
                f"use token={secret} only as an example",
                redact_secrets=True,
            )
            answers = record_clarification_answer(
                run_dir,
                "Only factual claims based on repository files.",
            )
            child = recompile_from_run(run_dir, out_dir=child_dir)

            self.assertTrue(answers["complete"])
            self.assertEqual(1, answers["redacted_answers"])
            self.assertTrue(answers["answers"][0]["redacted"])
            self.assertIn("[REDACTED:SECRET]", answers["answers"][0]["answer"])
            for path in [
                run_dir / "clarifications.json",
                run_dir / "clarification_answers.json",
                child_dir / "request.md",
                child_dir / "recompile_metadata.json",
            ]:
                self.assertNotIn(secret, path.read_text(encoding="utf-8"))
            metadata = json.loads((child_dir / "recompile_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(1, metadata["redacted_answers"])
            self.assertTrue(metadata["answers"][0]["redacted"])
            self.assertEqual(child["run_id"], metadata["child_run_id"])
            validation = validate_clarification_artifacts(run_dir, child_dir)
            self.assertTrue(validation["passed"], validation["errors"])

    def test_cli_batch_answer_reads_question_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "initial"
            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=run_dir)
            write_clarification_packet(run_dir, initial)
            answers_path = tmp_path / "answers.json"
            answers_path.write_text(
                json.dumps(
                    {
                        "q_001": "面向首次贡献者，说明本地开发流程。",
                        "q_002": "Only factual claims based on repository files.",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                cli_main(["answer-batch", str(run_dir), str(answers_path)])

            answers = json.loads((run_dir / "clarification_answers.json").read_text(encoding="utf-8"))
            self.assertTrue(answers["complete"])
            self.assertEqual(2, len(answers["answers"]))

    def test_legacy_v1_packet_is_migrated_before_recompile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "initial"
            child_dir = tmp_path / "child"
            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=run_dir)
            packet = write_clarification_packet(run_dir, initial)
            packet["schema_version"] = "skillgate.clarifications.v1"
            packet.pop("redaction_policy")
            packet["status"] = "answered"
            packet["questions"][0]["status"] = "answered"
            packet["questions"][0]["answer"] = "面向维护者，说明发布流程。"
            packet["questions"][0].pop("answer_sha256")
            packet["questions"][0].pop("redacted")
            (run_dir / "clarifications.json").write_text(
                json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            recompile_from_run(run_dir, out_dir=child_dir)

            metadata = json.loads((child_dir / "recompile_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual("skillgate.recompile.v2", metadata["schema_version"])
            self.assertFalse(metadata["answers"][0]["redacted"])

    def test_provenance_validator_detects_answer_hash_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "initial"
            child_dir = tmp_path / "child"
            initial = compile_request("完善 README", root=ROOT / "examples" / "docs_only", out_dir=run_dir)
            write_clarification_packet(run_dir, initial)
            record_clarification_answer(run_dir, "面向首次贡献者，说明测试流程。")
            record_clarification_answer(run_dir, "Only factual claims based on repository files.")
            recompile_from_run(run_dir, out_dir=child_dir)
            answers_path = run_dir / "clarification_answers.json"
            answers = json.loads(answers_path.read_text(encoding="utf-8"))
            answers["answers"][0]["answer_sha256"] = "0" * 64
            answers_path.write_text(
                json.dumps(answers, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            validation = validate_clarification_artifacts(run_dir, child_dir)

            self.assertFalse(validation["passed"])
            self.assertIn("answer hash mismatch: q_001", validation["errors"])


if __name__ == "__main__":
    unittest.main()
