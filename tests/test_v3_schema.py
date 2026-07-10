"""Tests for the v3 SkillInputContract schema, migrator, and engine adapter.

Commit 2 of the v0.4 refactor introduces v3 alongside v2. The runtime stays
v2-internal via the ``v3_to_v2_engine_view`` adapter; ``rules.py`` is
untouched. These tests exercise:

* v2 → v3 → v2 roundtrip (lossless across all 7 builtin contracts)
* v3 validation (accepts valid, rejects invalid importance)
* ``normalize_contract`` accepting v3 input and returning a v2 view
* JSON schema export for v3
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skillgate.capabilities import CONTRACT_REGISTRY
from skillgate.json_schema import write_json_schemas
from skillgate.schema import (
    SKILL_INPUT_CONTRACT_V3_VERSION,
    SKILL_INPUT_CONTRACT_VERSION,
    build_skill_input_contract_v3,
    migrate_contract_v2_to_v3,
    normalize_contract,
    validate_skill_input_contract_v3,
    v3_to_v2_engine_view,
)

_V2_SLOT_SECTIONS = (
    "required_slots",
    "ask_if_missing",
    "discover_if_missing",
    "safe_defaults",
    "safety_blocks",
    "authorization_requirements",
    "execution_constraints",
    "forbidden_actions",
    "stop_conditions",
)


class V2ToV3MigrationRoundtripTests(unittest.TestCase):
    """v2 → v3 → v2 preserves slot IDs in every section."""

    def test_bug_fix_roundtrip_preserves_sections(self) -> None:
        v2 = CONTRACT_REGISTRY.get("bug_fix")
        v3 = migrate_contract_v2_to_v3(v2)
        validate_skill_input_contract_v3(v3)
        back = v3_to_v2_engine_view(v3)
        for section in _V2_SLOT_SECTIONS:
            orig = sorted(s["id"] for s in v2.get(section, []))
            rt = sorted(s["id"] for s in back.get(section, []))
            self.assertEqual(orig, rt, f"section {section} drifted: {orig} vs {rt}")

    def test_bug_fix_required_slots_match_original(self) -> None:
        v2 = CONTRACT_REGISTRY.get("bug_fix")
        v3 = migrate_contract_v2_to_v3(v2)
        back = v3_to_v2_engine_view(v3)
        orig = sorted(s["id"] for s in v2["required_slots"])
        rt = sorted(s["id"] for s in back["required_slots"])
        self.assertEqual(orig, rt)


class AllBuiltinsMigrateTests(unittest.TestCase):
    """Every builtin contract migrates to v3, validates, and roundtrips."""

    BUILTINS = (
        "bug_fix",
        "failing_test_repair",
        "code_review",
        "refactor",
        "documentation_update",
        "feature_impl",
        "generic_unknown",
    )

    def test_all_builtins_migrate_and_roundtrip(self) -> None:
        for skill_id in self.BUILTINS:
            with self.subTest(skill_id=skill_id):
                v2 = CONTRACT_REGISTRY.get(skill_id)
                v3 = migrate_contract_v2_to_v3(v2)
                # v3 is structurally valid.
                validate_skill_input_contract_v3(v3)
                self.assertEqual(v3["schema_version"], SKILL_INPUT_CONTRACT_V3_VERSION)
                # No data loss: every v2 slot ID survives the roundtrip in
                # the same section.
                back = v3_to_v2_engine_view(v3)
                for section in _V2_SLOT_SECTIONS:
                    orig = sorted(s["id"] for s in v2.get(section, []))
                    rt = sorted(s["id"] for s in back.get(section, []))
                    self.assertEqual(orig, rt, f"{skill_id}.{section} drifted")


class V3BuilderValidationTests(unittest.TestCase):
    """build_skill_input_contract_v3 produces a valid contract."""

    def test_built_v3_contract_validates(self) -> None:
        contract = build_skill_input_contract_v3(
            skill_id="ut",
            skill_name="UT",
            skill_version="1.0.0",
            skill_description="unit test skill",
            slots=[
                {
                    "id": "target",
                    "description": "What target?",
                    "importance": "required",
                    "role": "execution_input",
                    "value_schema": {
                        "type": "path",
                        "cardinality": "many",
                        "allows_multiple": True,
                        "value_enum": None,
                    },
                    "acquisition": {
                        "allowed_sources": ["user"],
                        "strategy": "ask_user",
                        "resolver": None,
                    },
                    "confirmation": {"policy": "always", "prompt": None},
                    "missing": {"policy": "ask_user"},
                    "benefit": {
                        "reduces_exploration": "high",
                        "reduces_error_risk": "high",
                    },
                    "evidence_ids": [],
                }
            ],
            execution_policies=[
                {
                    "id": "preserve_api",
                    "text": "Do not change public API",
                    "enforcement": "advisory",
                    "category": "execution_constraint",
                    "evidence_ids": [],
                }
            ],
            activation_guards=[
                {
                    "id": "cred",
                    "text": "No credential exfiltration",
                    "type": "safety_block",
                    "evidence_ids": [],
                }
            ],
            contract_evidence=[],
        )
        # Should not raise.
        validate_skill_input_contract_v3(contract)

    def test_invalid_importance_rejected(self) -> None:
        contract = build_skill_input_contract_v3(
            skill_id="ut",
            skill_name="UT",
            skill_version="1.0.0",
            skill_description="unit test skill",
            slots=[
                {
                    "id": "target",
                    "description": "What target?",
                    "importance": "invalid",
                    "role": "execution_input",
                }
            ],
        )
        with self.assertRaises(ValueError):
            validate_skill_input_contract_v3(contract)


class NormalizeContractAcceptsV3Tests(unittest.TestCase):
    """normalize_contract must accept v3 and return a v2-shaped dict."""

    def test_normalize_contract_returns_v2_from_v3(self) -> None:
        v2 = CONTRACT_REGISTRY.get("bug_fix")
        v3 = migrate_contract_v2_to_v3(v2)
        normalized = normalize_contract(v3)
        self.assertEqual(normalized["schema_version"], SKILL_INPUT_CONTRACT_VERSION)
        # v2 sections present.
        for section in _V2_SLOT_SECTIONS:
            self.assertIn(section, normalized)
        # required_slots roundtrips.
        orig = sorted(s["id"] for s in v2["required_slots"])
        rt = sorted(s["id"] for s in normalized["required_slots"])
        self.assertEqual(orig, rt)


class V3JsonSchemaExportTests(unittest.TestCase):
    """write_json_schemas exports the v3 schema file."""

    def test_v3_schema_file_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = write_json_schemas(Path(tmp))
            names = {p.name for p in written}
            self.assertIn("skill_input_contract.v3.schema.json", names)
            self.assertIn("skill_input_contract.v2.schema.json", names)


if __name__ == "__main__":
    unittest.main()
