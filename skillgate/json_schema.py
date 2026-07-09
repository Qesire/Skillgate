from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .constants import (
    ANSWER_SCHEMA_VERSION,
    CLARIFICATION_SCHEMA_VERSION,
    RECOMPILE_SCHEMA_VERSION,
)
from .schema import CAPABILITY_IDS, DECISION_KINDS, SCHEMA_VERSION, SOURCE_KINDS, TASK_KINDS
from .schema import (
    ANSWER_SOURCES,
    INPUT_SLOT_STATE_VERSION,
    MISSING_POLICIES,
    NORMALIZED_SKILL_INPUT_VERSION,
    SKILL_INPUT_CONTRACT_VERSION,
    SLOT_STATUSES,
    SUPPORT_KINDS,
)


TASKBRIEF_SCHEMA_FILE = f"{SCHEMA_VERSION}.schema.json"
SKILL_INPUT_CONTRACT_SCHEMA_FILE = "skill_input_contract.v2.schema.json"
INPUT_SLOT_STATE_SCHEMA_FILE = "input_slot_state.v1.schema.json"
NORMALIZED_SKILL_INPUT_SCHEMA_FILE = "normalized_skill_input.v1.schema.json"
DECISION_SCHEMA_FILE = "decision.v1.schema.json"
CLARIFICATION_SCHEMA_FILE = "clarifications.v2.schema.json"
CLARIFICATION_ANSWERS_SCHEMA_FILE = "clarification_answers.v2.schema.json"
RECOMPILE_METADATA_SCHEMA_FILE = "recompile.v2.schema.json"


def taskbrief_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:skillgate:schema:{SCHEMA_VERSION}",
        "title": "SkillGate TaskBrief",
        "description": "Evidence-backed pre-execution task contract.",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id",
            "run_id",
            "schema_version",
            "task_frame",
            "matched_capability",
            "decision_kind",
            "goal",
            "scope_in",
            "scope_out",
            "known_facts",
            "assumptions",
            "unresolved_unknowns",
            "execution_policy",
            "forbidden_actions",
            "verification_policy",
            "stop_conditions",
            "output_contract",
            "evidence",
        ],
        "properties": {
            "id": _nonempty_string(),
            "run_id": _nonempty_string(),
            "schema_version": {"const": SCHEMA_VERSION},
            "task_frame": {"$ref": "#/$defs/task_frame"},
            "matched_capability": {
                "oneOf": [{"$ref": "#/$defs/capability"}, {"type": "null"}],
            },
            "decision_kind": {"enum": sorted(DECISION_KINDS)},
            "goal": {"$ref": "#/$defs/statement"},
            **{
                field: _statement_array()
                for field in [
                    "scope_in",
                    "scope_out",
                    "known_facts",
                    "assumptions",
                    "unresolved_unknowns",
                    "execution_policy",
                    "forbidden_actions",
                    "verification_policy",
                    "stop_conditions",
                    "output_contract",
                ]
            },
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/evidence"},
            },
        },
        "$defs": _common_definitions(),
    }


def decision_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:skillgate:schema:decision:v1",
        "title": "SkillGate Compile Decision",
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "reason", "confidence", "questions"],
        "properties": {
            "kind": {"enum": sorted(DECISION_KINDS)},
            "reason": _nonempty_string(),
            "confidence": _confidence(),
            "questions": _string_array(),
            "skill_id": _nonempty_string(),
            "assumptions": _statement_array(),
            "readonly_exploration_plan": _statement_array(),
            "blocking_slots": _string_array(),
            "stop_conditions": _statement_array(),
        },
        "$defs": {"statement": _statement_definition()},
    }


def skill_input_contract_json_schema() -> dict[str, Any]:
    slot = _slot_entry_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:skillgate:schema:skill-input-contract:v2",
        "title": "SkillGate SkillInputContract",
        "description": "Reusable pre-activation input contract for a target skill.",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "skill_id",
            "skill_name",
            "skill_version",
            "skill_description",
            "source_path",
            "source_sha256",
            "required_slots",
            "ask_if_missing",
            "discover_if_missing",
            "safe_defaults",
            "block_if",
            "safety_blocks",
            "authorization_requirements",
            "execution_constraints",
            "forbidden_actions",
            "stop_conditions",
            "contract_evidence",
        ],
        "properties": {
            "schema_version": {"const": SKILL_INPUT_CONTRACT_VERSION},
            "skill_id": _nonempty_string(),
            "skill_name": _nonempty_string(),
            "skill_version": _nonempty_string(),
            "skill_description": _nonempty_string(),
            "source_path": _nullable(_nonempty_string()),
            "source_sha256": _nullable(_sha256()),
            "required_slots": {"type": "array", "items": slot},
            "ask_if_missing": {"type": "array", "items": slot},
            "discover_if_missing": {"type": "array", "items": slot},
            "safe_defaults": {"type": "array", "items": slot},
            "block_if": {"type": "array", "items": slot},
            "safety_blocks": {"type": "array", "items": slot},
            "authorization_requirements": {"type": "array", "items": slot},
            "execution_constraints": {"type": "array", "items": slot},
            "forbidden_actions": {"type": "array", "items": slot},
            "stop_conditions": {"type": "array", "items": slot},
            "contract_evidence": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
        },
    }


def input_slot_state_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:skillgate:schema:input-slot-state:v1",
        "title": "SkillGate InputSlotState",
        "description": "Evaluation state for one target-skill input slot.",
        **_input_slot_state_schema(),
    }


def normalized_skill_input_json_schema() -> dict[str, Any]:
    slot_state_array = {"type": "array", "items": {"$ref": "#/$defs/input_slot_state"}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:skillgate:schema:normalized-skill-input:v1",
        "title": "SkillGate NormalizedSkillInput",
        "description": "Skill-ready input artifact produced before target skill activation.",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "run_id",
            "skill_id",
            "skill_name",
            "raw_request",
            "human_provided_inputs",
            "agent_discoverable_inputs",
            "safe_defaults",
            "requires_authorization",
            "blocked",
            "execution_constraints",
            "forbidden_actions",
            "stop_conditions",
            "low_confidence_slots",
            "decision_kind",
            "decision_reason",
            "activation_instruction",
            "expected_output",
            "evidence",
        ],
        "properties": {
            "schema_version": {"const": NORMALIZED_SKILL_INPUT_VERSION},
            "run_id": _nonempty_string(),
            "skill_id": _nonempty_string(),
            "skill_name": _nonempty_string(),
            "raw_request": _nonempty_string(),
            "human_provided_inputs": slot_state_array,
            "agent_discoverable_inputs": slot_state_array,
            "safe_defaults": slot_state_array,
            "requires_authorization": slot_state_array,
            "blocked": slot_state_array,
            "execution_constraints": slot_state_array,
            "forbidden_actions": slot_state_array,
            "stop_conditions": slot_state_array,
            "low_confidence_slots": slot_state_array,
            "decision_kind": {"enum": sorted(DECISION_KINDS)},
            "decision_reason": _nonempty_string(),
            "activation_instruction": _nonempty_string(),
            "expected_output": {"type": "string"},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/evidence"},
            },
        },
        "$defs": {
            "input_slot_state": _input_slot_state_schema(),
            "evidence": _common_definitions()["evidence"],
        },
    }


def clarification_json_schema() -> dict[str, Any]:
    question = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "text", "status", "answer", "answer_sha256", "redacted"],
        "properties": {
            "id": _nonempty_string(),
            "text": _nonempty_string(),
            "status": {"enum": ["open", "answered"]},
            "answer": _nullable(_nonempty_string()),
            "answer_sha256": _nullable(_sha256()),
            "redacted": {"type": "boolean"},
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "answered"}}},
                "then": {"properties": {"answer": _nonempty_string(), "answer_sha256": _sha256()}},
            },
            {
                "if": {"properties": {"status": {"const": "open"}}},
                "then": {
                    "properties": {
                        "answer": {"type": "null"},
                        "answer_sha256": {"type": "null"},
                        "redacted": {"const": False},
                    }
                },
            },
        ],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:skillgate:schema:clarifications:v2",
        "title": "SkillGate Clarification Packet",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "run_id",
            "decision_kind",
            "status",
            "questions",
            "answer_file",
            "redaction_policy",
        ],
        "properties": {
            "schema_version": {"const": CLARIFICATION_SCHEMA_VERSION},
            "run_id": _nonempty_string(),
            "decision_kind": {"enum": sorted(DECISION_KINDS)},
            "status": {"enum": ["open", "answered", "not_required"]},
            "questions": {"type": "array", "items": question},
            "answer_file": {"const": "clarification_answers.json"},
            "redaction_policy": {
                "enum": ["reject_secret_like_by_default", "explicit_redaction_applied", "legacy_unchecked"]
            },
            "migrated_from": {"const": "skillgate.clarifications.v1"},
        },
    }


def clarification_answers_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:skillgate:schema:clarification-answers:v2",
        "title": "SkillGate Clarification Answers",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "run_id",
            "complete",
            "redaction_policy",
            "redacted_answers",
            "answers",
        ],
        "properties": {
            "schema_version": {"const": ANSWER_SCHEMA_VERSION},
            "run_id": _nonempty_string(),
            "complete": {"type": "boolean"},
            "redaction_policy": {
                "enum": ["reject_secret_like_by_default", "explicit_redaction_applied", "legacy_unchecked"]
            },
            "redacted_answers": {"type": "integer", "minimum": 0},
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question_id", "question", "answer", "answer_sha256", "redacted"],
                    "properties": {
                        "question_id": _nonempty_string(),
                        "question": _nonempty_string(),
                        "answer": _nonempty_string(),
                        "answer_sha256": _sha256(),
                        "redacted": {"type": "boolean"},
                    },
                },
            },
        },
    }


def recompile_metadata_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:skillgate:schema:recompile:v2",
        "title": "SkillGate Recompile Metadata",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "parent_run_id",
            "parent_run_dir",
            "child_run_id",
            "original_request_sha256",
            "resolved_request_sha256",
            "redacted_answers",
            "answers",
        ],
        "properties": {
            "schema_version": {"const": RECOMPILE_SCHEMA_VERSION},
            "parent_run_id": _nonempty_string(),
            "parent_run_dir": _nonempty_string(),
            "child_run_id": _nonempty_string(),
            "original_request_sha256": _sha256(),
            "resolved_request_sha256": _sha256(),
            "redacted_answers": {"type": "integer", "minimum": 0},
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question_id", "question", "answer_sha256", "redacted"],
                    "properties": {
                        "question_id": _nonempty_string(),
                        "question": _nonempty_string(),
                        "answer_sha256": _sha256(),
                        "redacted": {"type": "boolean"},
                    },
                },
            },
        },
    }


def published_schema_documents() -> dict[str, dict[str, Any]]:
    return {
        SKILL_INPUT_CONTRACT_SCHEMA_FILE: skill_input_contract_json_schema(),
        INPUT_SLOT_STATE_SCHEMA_FILE: input_slot_state_json_schema(),
        NORMALIZED_SKILL_INPUT_SCHEMA_FILE: normalized_skill_input_json_schema(),
        DECISION_SCHEMA_FILE: decision_json_schema(),
        CLARIFICATION_SCHEMA_FILE: clarification_json_schema(),
        CLARIFICATION_ANSWERS_SCHEMA_FILE: clarification_answers_json_schema(),
        RECOMPILE_METADATA_SCHEMA_FILE: recompile_metadata_json_schema(),
    }


def write_json_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for file_name, schema in published_schema_documents().items():
        path = output_dir / file_name
        path.write_text(_render_json(schema), encoding="utf-8")
        written.append(path)
    return written


def validate_published_schemas(schema_dir: Path) -> dict[str, Any]:
    errors = []
    documents = published_schema_documents()
    extra = sorted(path.name for path in schema_dir.glob("*.json") if path.name not in documents)
    errors.extend(f"unexpected published schema: {name}" for name in extra)
    for file_name, expected in documents.items():
        path = schema_dir / file_name
        if not path.is_file():
            errors.append(f"missing published schema: {file_name}")
            continue
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"invalid published schema {file_name}: {exc}")
            continue
        if actual != expected:
            errors.append(f"published schema drift: {file_name}")
        try:
            Draft202012Validator.check_schema(actual)
        except SchemaError as exc:
            errors.append(f"invalid JSON Schema {file_name}: {exc}")
    return {
        "passed": not errors,
        "schema_dir": str(schema_dir),
        "schemas": sorted(documents),
        "errors": errors,
    }


def json_schema_errors(instance: Any, schema: dict[str, Any]) -> list[str]:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda error: tuple(str(part) for part in error.path))
    return [_format_validation_error(error) for error in errors]


def _common_definitions() -> dict[str, Any]:
    return {
        "statement": _statement_definition(),
        "evidence": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "source_kind",
                "source_id",
                "path",
                "line_start",
                "line_end",
                "quote",
                "quote_hash",
                "confidence",
            ],
            "properties": {
                "id": _nonempty_string(),
                "source_kind": {"enum": sorted(SOURCE_KINDS)},
                "source_id": _nonempty_string(),
                "path": _nullable(_nonempty_string()),
                "line_start": _nullable({"type": "integer", "minimum": 1}),
                "line_end": _nullable({"type": "integer", "minimum": 1}),
                "quote": _nullable({"type": "string"}),
                "quote_hash": _nullable({"type": "string", "pattern": "^[0-9a-f]{64}$"}),
                "confidence": _confidence(),
            },
        },
        "task_frame": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "raw_request",
                "kind",
                "goal",
                "target_objects",
                "user_constraints",
                "requested_outputs",
                "ambiguity_notes",
            ],
            "properties": {
                "raw_request": _nonempty_string(),
                "kind": {"enum": sorted(TASK_KINDS)},
                "goal": _nullable({"$ref": "#/$defs/statement"}),
                "target_objects": _statement_array(),
                "user_constraints": _statement_array(),
                "requested_outputs": _statement_array(),
                "ambiguity_notes": _statement_array(),
            },
        },
        "capability": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "task_kind",
                "name",
                "description",
                "triggers",
                "anti_triggers",
                "required_slots",
                "discoverable_slots",
                "must_ask_slots",
                "safe_defaults",
                "forbidden_actions",
                "verification_hints",
            ],
            "properties": {
                "id": {"enum": sorted(CAPABILITY_IDS)},
                "task_kind": {"enum": sorted(TASK_KINDS)},
                "name": _nonempty_string(),
                "description": _nonempty_string(),
                "triggers": _string_array(),
                "anti_triggers": _string_array(),
                "required_slots": _string_array(),
                "discoverable_slots": _string_array(),
                "must_ask_slots": _string_array(),
                "safe_defaults": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "forbidden_actions": _string_array(),
                "verification_hints": _string_array(),
            },
        },
    }


def _slot_entry_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["id", "text", "category"],
        "properties": {
            "id": _nonempty_string(),
            "text": _nonempty_string(),
            "category": {"enum": sorted(SLOT_STATUSES)},
            "answer_source": {"enum": sorted(ANSWER_SOURCES)},
            "support": {"enum": sorted(SUPPORT_KINDS)},
            "missing_policy": {"enum": sorted(MISSING_POLICIES)},
            "confidence": _confidence(),
            "evidence_status": {"enum": ["verified", "partially_verified", "unverified"]},
        },
    }


def _input_slot_state_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "name",
            "text",
            "description",
            "category",
            "status",
            "value",
            "question",
            "assumption",
            "evidence_ids",
            "answer_source",
            "support",
            "risk",
            "ambiguity",
            "handling_reason",
            "confidence",
        ],
        "properties": {
            "schema_version": {"const": INPUT_SLOT_STATE_VERSION},
            "name": _nonempty_string(),
            "text": _nonempty_string(),
            "description": _nonempty_string(),
            "category": {"enum": sorted(SLOT_STATUSES)},
            "status": {"enum": sorted(SLOT_STATUSES)},
            "value": _nullable(_nonempty_string()),
            "question": _nullable(_nonempty_string()),
            "assumption": _nullable(_nonempty_string()),
            "evidence_ids": _string_array(),
            "answer_source": {"enum": sorted(ANSWER_SOURCES)},
            "support": {"enum": sorted(SUPPORT_KINDS)},
            "risk": _nonempty_string(),
            "ambiguity": _nonempty_string(),
            "handling_reason": {"type": "string"},
            "confidence": _confidence(),
            "missing_policy": {"enum": sorted(MISSING_POLICIES)},
            "evidence_status": {"enum": ["verified", "partially_verified", "unverified"]},
            "value_source": {"type": "string"},
            "value_source_span": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "minItems": 2,
                "maxItems": 2,
            },
            "conflict": {"type": "boolean"},
        },
    }


def _statement_definition() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "evidence_ids", "confidence"],
        "properties": {
            "text": _nonempty_string(),
            "evidence_ids": _string_array(min_items=1),
            "confidence": _confidence(),
        },
    }


def _statement_array() -> dict[str, Any]:
    return {"type": "array", "items": {"$ref": "#/$defs/statement"}}


def _string_array(*, min_items: int = 0) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": min_items,
        "items": _nonempty_string(),
    }


def _unique_string_array() -> dict[str, Any]:
    return {
        "type": "array",
        "uniqueItems": True,
        "items": _nonempty_string(),
    }


def _nonempty_string() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def _confidence() -> dict[str, Any]:
    return {"type": "number", "minimum": 0.0, "maximum": 1.0}


def _sha256() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"oneOf": [schema, {"type": "null"}]}


def _relative_path() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$))(?!.*\\\\).+$"}


def _closed_object(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


def _format_validation_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "$"
    return f"{path}: {error.message}"


def _render_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
