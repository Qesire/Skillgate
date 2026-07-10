from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .compiler import compile_against_skill, compile_request
from .constants import (
    ANSWER_SCHEMA_VERSION,
    CLARIFICATION_MARKER,
    CLARIFICATION_SCHEMA_VERSION,
    LEGACY_CLARIFICATION_SCHEMA_VERSION,
    RECOMPILE_SCHEMA_VERSION,
)
from .context import redact_secret_like_text
from .json_schema import (
    clarification_answers_json_schema,
    clarification_json_schema,
    json_schema_errors,
    recompile_metadata_json_schema,
)
from .schema import hash_text


def write_clarification_packet(run_dir: Path, compile_result: dict[str, Any]) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    decision = compile_result["decision"]
    questions = [
        {
            "id": f"q_{index:03d}",
            "text": question,
            "status": "open",
            "answer": None,
            "answer_sha256": None,
            "redacted": False,
        }
        for index, question in enumerate(decision.get("questions", []), start=1)
    ]
    packet = {
        "schema_version": CLARIFICATION_SCHEMA_VERSION,
        "run_id": compile_result["run_id"],
        "decision_kind": decision["kind"],
        "status": "open" if questions else "not_required",
        "questions": questions,
        "answer_file": "clarification_answers.json",
        "redaction_policy": "reject_secret_like_by_default",
    }
    _write_json(run_dir / "clarifications.json", packet)
    return packet


def record_clarification_answer(
    run_dir: Path,
    answer: str,
    *,
    question_id: str | None = None,
    redact_secrets: bool = False,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    answer = answer.strip()
    if not answer:
        raise ValueError("answer must be non-empty")

    packet = _load_or_create_packet(run_dir)
    open_questions = [item for item in packet["questions"] if item.get("status") != "answered"]
    if not open_questions:
        raise ValueError("no open clarification questions remain")

    if question_id is None:
        question = open_questions[0]
    else:
        matches = [item for item in packet["questions"] if item["id"] == question_id]
        if not matches:
            raise ValueError(f"unknown clarification question id: {question_id}")
        question = matches[0]
        if question.get("status") == "answered":
            raise ValueError(f"clarification question is already answered: {question_id}")

    return _record_answers(
        run_dir,
        packet,
        {question["id"]: answer},
        redact_secrets=redact_secrets,
    )


def record_clarification_answers(
    run_dir: Path,
    answers_by_question_id: dict[str, str],
    *,
    redact_secrets: bool = False,
) -> dict[str, Any]:
    """Atomically record one or more explicitly identified clarification answers."""
    run_dir = run_dir.resolve()
    if not isinstance(answers_by_question_id, dict) or not answers_by_question_id:
        raise ValueError("answers must be a non-empty question-id mapping")
    packet = _load_or_create_packet(run_dir)
    return _record_answers(
        run_dir,
        packet,
        answers_by_question_id,
        redact_secrets=redact_secrets,
    )


def recompile_from_run(run_dir: Path, *, out_dir: Path | None = None) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    packet = _load_or_create_packet(run_dir)
    if packet["status"] != "answered":
        raise ValueError("all clarification questions must be answered before recompile")

    raw_request_path = run_dir / "request.md"
    context_manifest_path = run_dir / "context_manifest.json"
    if not raw_request_path.exists():
        raise ValueError(f"missing request artifact: {raw_request_path}")
    if not context_manifest_path.exists():
        raise ValueError(f"missing context manifest artifact: {context_manifest_path}")

    raw_request = raw_request_path.read_text(encoding="utf-8").strip()
    context_manifest = json.loads(context_manifest_path.read_text(encoding="utf-8"))
    root = Path(context_manifest["root"])
    resolved_request = build_resolved_request(raw_request, packet)
    result = _recompile_resolved_request(run_dir, resolved_request, root=root, out_dir=out_dir)
    metadata = {
        "schema_version": RECOMPILE_SCHEMA_VERSION,
        "parent_run_id": packet["run_id"],
        "parent_run_dir": str(run_dir),
        "child_run_id": result["run_id"],
        "original_request_sha256": hash_text(raw_request),
        "resolved_request_sha256": hash_text(resolved_request),
        "redacted_answers": sum(1 for item in packet["questions"] if item.get("redacted") is True),
        "answers": [
            {
                "question_id": item["id"],
                "question": item["text"],
                "answer_sha256": item.get("answer_sha256") or hash_text(item.get("answer") or ""),
                "redacted": item.get("redacted") is True,
            }
            for item in packet["questions"]
        ],
    }
    _write_json(Path(result["out_dir"]) / "recompile_metadata.json", metadata)
    return result


def build_resolved_request(raw_request: str, packet: dict[str, Any]) -> str:
    lines = [
        CLARIFICATION_MARKER,
        "",
        "Original request:",
        raw_request.strip(),
        "",
        "Clarification answers:",
    ]
    for item in packet["questions"]:
        answer = item.get("answer")
        if answer is None:
            continue
        lines.append(f"- Question: {item['text']}")
        lines.append(f"  Answer: {answer}")
    lines.extend(
        [
            "",
            "Compile the target skill input using these answers. Do not ask the same answered clarification questions again.",
        ]
    )
    return "\n".join(lines)


def validate_clarification_artifacts(pre_run: Path, post_run: Path) -> dict[str, Any]:
    """Validate a completed clarification transaction and its parent-child provenance."""
    pre_run = pre_run.resolve()
    post_run = post_run.resolve()
    required = {
        "clarifications": pre_run / "clarifications.json",
        "answers": pre_run / "clarification_answers.json",
        "pre_taskbrief": pre_run / "taskbrief.json",
        "pre_request": pre_run / "request.md",
        "post_taskbrief": post_run / "taskbrief.json",
        "post_request": post_run / "request.md",
        "metadata": post_run / "recompile_metadata.json",
    }
    errors = []
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        return {
            "passed": False,
            "pre_run": str(pre_run),
            "post_run": str(post_run),
            "errors": [f"missing artifact: {name}" for name in missing],
        }

    documents = {}
    for name, path in required.items():
        if path.suffix != ".json":
            continue
        try:
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
    if errors:
        return {"passed": False, "pre_run": str(pre_run), "post_run": str(post_run), "errors": errors}

    packet = documents["clarifications"]
    answers = documents["answers"]
    metadata = documents["metadata"]
    errors.extend(f"clarifications.json schema: {error}" for error in json_schema_errors(packet, clarification_json_schema()))
    errors.extend(
        f"clarification_answers.json schema: {error}"
        for error in json_schema_errors(answers, clarification_answers_json_schema())
    )
    errors.extend(
        f"recompile_metadata.json schema: {error}"
        for error in json_schema_errors(metadata, recompile_metadata_json_schema())
    )
    pre_taskbrief = documents["pre_taskbrief"]
    post_taskbrief = documents["post_taskbrief"]
    if not all(isinstance(value, dict) for value in [packet, answers, metadata, pre_taskbrief, post_taskbrief]):
        errors.append("clarification sidecars and TaskBriefs must be JSON objects")
        return {"passed": False, "pre_run": str(pre_run), "post_run": str(post_run), "errors": errors}

    if packet.get("status") != "answered" or answers.get("complete") is not True:
        errors.append("clarification transaction is not complete")
    if packet.get("run_id") != pre_taskbrief.get("run_id") or answers.get("run_id") != pre_taskbrief.get("run_id"):
        errors.append("clarification parent run ids do not match pre/taskbrief.json")
    if metadata.get("parent_run_id") != pre_taskbrief.get("run_id"):
        errors.append("recompile metadata parent_run_id does not match pre run")
    if metadata.get("child_run_id") != post_taskbrief.get("run_id"):
        errors.append("recompile metadata child_run_id does not match post run")
    if metadata.get("parent_run_dir") != str(pre_run):
        errors.append("recompile metadata parent_run_dir does not match pre run directory")

    answer_rows = {item.get("question_id"): item for item in answers.get("answers", []) if isinstance(item, dict)}
    metadata_rows = {item.get("question_id"): item for item in metadata.get("answers", []) if isinstance(item, dict)}
    if len(answer_rows) != len(answers.get("answers", [])):
        errors.append("duplicate or malformed question ids in clarification_answers.json")
    if len(metadata_rows) != len(metadata.get("answers", [])):
        errors.append("duplicate or malformed question ids in recompile_metadata.json")
    for question in packet.get("questions", []):
        if not isinstance(question, dict) or question.get("status") != "answered":
            errors.append(f"clarification question is not answered: {question.get('id') if isinstance(question, dict) else 'unknown'}")
            continue
        question_id = question["id"]
        answer_row = answer_rows.get(question_id)
        metadata_row = metadata_rows.get(question_id)
        if answer_row is None or metadata_row is None:
            errors.append(f"missing answer provenance row: {question_id}")
            continue
        answer = question.get("answer") or ""
        expected_hash = hash_text(answer)
        if question.get("answer_sha256") != expected_hash or answer_row.get("answer_sha256") != expected_hash:
            errors.append(f"answer hash mismatch: {question_id}")
        if metadata_row.get("answer_sha256") != expected_hash:
            errors.append(f"metadata answer hash mismatch: {question_id}")
        if answer_row.get("answer") != answer:
            errors.append(f"answer text mismatch: {question_id}")
        if answer_row.get("question") != question.get("text") or metadata_row.get("question") != question.get("text"):
            errors.append(f"question text mismatch: {question_id}")
        if answer_row.get("redacted") != question.get("redacted") or metadata_row.get("redacted") != question.get("redacted"):
            errors.append(f"redaction provenance mismatch: {question_id}")
        _, contains_secret = redact_secret_like_text(answer)
        if contains_secret:
            errors.append(f"stored answer still contains secret-like content: {question_id}")

    redacted_count = sum(1 for question in packet.get("questions", []) if question.get("redacted") is True)
    if answers.get("redacted_answers") != redacted_count or metadata.get("redacted_answers") != redacted_count:
        errors.append("redacted answer counts do not match")
    original_request = required["pre_request"].read_text(encoding="utf-8").strip()
    resolved_request = required["post_request"].read_text(encoding="utf-8").strip()
    if metadata.get("original_request_sha256") != hash_text(original_request):
        errors.append("original request hash mismatch")
    if metadata.get("resolved_request_sha256") != hash_text(resolved_request):
        errors.append("resolved request hash mismatch")
    if CLARIFICATION_MARKER not in resolved_request:
        errors.append("resolved request is missing the clarification marker")

    return {
        "passed": not errors,
        "pre_run": str(pre_run),
        "post_run": str(post_run),
        "questions": len(packet.get("questions", [])),
        "redacted_answers": redacted_count,
        "errors": errors,
    }


def audit_clarification_artifact_corpus(runs_dir: Path) -> dict[str, Any]:
    if not runs_dir.is_dir():
        return {
            "passed": False,
            "summary": {
                "transactions": 0,
                "valid_transactions": 0,
                "invalid_transactions": 0,
                "questions": 0,
                "redacted_answers": 0,
            },
            "transactions": [],
            "errors": [f"clarification runs directory does not exist: {runs_dir}"],
        }
    results = [
        validate_clarification_artifacts(task_dir / "pre", task_dir / "post")
        for task_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir())
    ]
    invalid = [result for result in results if not result["passed"]]
    return {
        "passed": bool(results) and not invalid,
        "summary": {
            "transactions": len(results),
            "valid_transactions": len(results) - len(invalid),
            "invalid_transactions": len(invalid),
            "questions": sum(result.get("questions", 0) for result in results),
            "redacted_answers": sum(result.get("redacted_answers", 0) for result in results),
        },
        "transactions": results,
    }


def _load_or_create_packet(run_dir: Path) -> dict[str, Any]:
    packet_path = run_dir / "clarifications.json"
    if packet_path.exists():
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        _validate_packet(packet, packet_path)
        return _upgrade_packet(packet)

    decision_path = run_dir / "decision.json"
    taskbrief_path = run_dir / "taskbrief.json"
    normalized_path = run_dir / "normalized_skill_input.json"
    if not decision_path.exists():
        raise ValueError(f"missing decision artifact: {decision_path}")

    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if taskbrief_path.exists():
        run_id = json.loads(taskbrief_path.read_text(encoding="utf-8"))["run_id"]
    elif normalized_path.exists():
        run_id = json.loads(normalized_path.read_text(encoding="utf-8"))["run_id"]
    else:
        raise ValueError(f"missing normalized skill input artifact: {normalized_path}")
    return write_clarification_packet(
        run_dir,
        {
            "run_id": run_id,
            "decision": decision,
        },
    )


def _recompile_resolved_request(
    run_dir: Path,
    resolved_request: str,
    *,
    root: Path,
    out_dir: Path | None,
) -> dict[str, Any]:
    normalized_path = run_dir / "normalized_skill_input.json"
    if not normalized_path.exists():
        raise ValueError(
            "Cannot recompile: normalized_skill_input.json not found and "
            "compile_request now requires an explicit skill_id"
        )

    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    skill_id = normalized.get("skill_id")
    if not isinstance(skill_id, str) or not skill_id:
        raise ValueError(f"normalized skill input missing skill_id: {normalized_path}")

    contract_path = run_dir / "skill_contract.json"
    if contract_path.exists():
        from .capabilities import CONTRACT_REGISTRY

        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        CONTRACT_REGISTRY.register(contract["skill_id"], contract)

    return compile_against_skill(resolved_request, skill_id=skill_id, root=root, out_dir=out_dir)


def _validate_packet(packet: dict[str, Any], path: Path) -> None:
    if not isinstance(packet, dict):
        raise ValueError(f"clarification packet must be an object: {path}")
    if packet.get("schema_version") not in {CLARIFICATION_SCHEMA_VERSION, LEGACY_CLARIFICATION_SCHEMA_VERSION}:
        raise ValueError(f"unsupported clarification packet schema in {path}")
    if not isinstance(packet.get("questions"), list):
        raise ValueError(f"clarification packet questions must be a list: {path}")
    seen_ids = set()
    for question in packet["questions"]:
        if not isinstance(question, dict):
            raise ValueError(f"clarification questions must be objects: {path}")
        if not isinstance(question.get("id"), str) or not question["id"]:
            raise ValueError(f"clarification question missing id: {path}")
        if question["id"] in seen_ids:
            raise ValueError(f"duplicate clarification question id: {question['id']}")
        seen_ids.add(question["id"])
        if not isinstance(question.get("text"), str) or not question["text"].strip():
            raise ValueError(f"clarification question missing text: {path}")
        if question.get("status") not in {"open", "answered"}:
            raise ValueError(f"invalid clarification question status: {question['id']}")


def _record_answers(
    run_dir: Path,
    packet: dict[str, Any],
    answers_by_question_id: dict[str, str],
    *,
    redact_secrets: bool,
) -> dict[str, Any]:
    questions_by_id = {item["id"]: item for item in packet["questions"]}
    normalized_answers: dict[str, tuple[str, bool]] = {}
    errors = []
    for question_id, raw_answer in answers_by_question_id.items():
        if question_id not in questions_by_id:
            errors.append(f"unknown clarification question id: {question_id}")
            continue
        if questions_by_id[question_id].get("status") == "answered":
            errors.append(f"clarification question is already answered: {question_id}")
            continue
        if not isinstance(raw_answer, str) or not raw_answer.strip():
            errors.append(f"answer must be non-empty: {question_id}")
            continue
        answer = raw_answer.strip()
        safe_answer, contains_secret = redact_secret_like_text(answer)
        if contains_secret and not redact_secrets:
            errors.append(
                f"answer contains secret-like content: {question_id}; remove it or explicitly enable redaction"
            )
            continue
        normalized_answers[question_id] = (safe_answer if contains_secret else answer, contains_secret)
    if errors:
        raise ValueError("; ".join(errors))

    for question_id, (answer, redacted) in normalized_answers.items():
        question = questions_by_id[question_id]
        question["answer"] = answer
        question["answer_sha256"] = hash_text(answer)
        question["redacted"] = redacted
        question["status"] = "answered"
    packet["status"] = "answered" if all(item.get("status") == "answered" for item in packet["questions"]) else "open"
    packet["schema_version"] = CLARIFICATION_SCHEMA_VERSION
    packet["redaction_policy"] = (
        "explicit_redaction_applied"
        if any(item.get("redacted") is True for item in packet["questions"])
        else "reject_secret_like_by_default"
    )

    answers = _answer_artifact(packet)
    _write_json(run_dir / "clarifications.json", packet)
    _write_json(run_dir / "clarification_answers.json", answers)
    return answers


def _answer_artifact(packet: dict[str, Any]) -> dict[str, Any]:
    answered = [item for item in packet["questions"] if item.get("status") == "answered"]
    return {
        "schema_version": ANSWER_SCHEMA_VERSION,
        "run_id": packet["run_id"],
        "complete": packet["status"] == "answered",
        "redaction_policy": packet.get("redaction_policy", "legacy_unchecked"),
        "redacted_answers": sum(1 for item in answered if item.get("redacted") is True),
        "answers": [
            {
                "question_id": item["id"],
                "question": item["text"],
                "answer": item.get("answer"),
                "answer_sha256": item.get("answer_sha256") or hash_text(item.get("answer") or ""),
                "redacted": item.get("redacted") is True,
            }
            for item in answered
        ],
    }


def _upgrade_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if packet.get("schema_version") == CLARIFICATION_SCHEMA_VERSION:
        return packet
    upgraded = deepcopy(packet)
    upgraded["schema_version"] = CLARIFICATION_SCHEMA_VERSION
    upgraded["migrated_from"] = LEGACY_CLARIFICATION_SCHEMA_VERSION
    upgraded["redaction_policy"] = "legacy_unchecked"
    for question in upgraded["questions"]:
        answer = question.get("answer")
        question["answer_sha256"] = hash_text(answer) if isinstance(answer, str) else None
        question["redacted"] = False
    return upgraded


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
