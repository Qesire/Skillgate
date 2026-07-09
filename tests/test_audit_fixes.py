"""Regression tests for the second-round audit fixes.

Covers:
  P1-1: LLM YAML roundtrip (generate -> save -> load -> compile) with a
        single canonical format that preserves all 5 safety sections.
  P1-2: missing_policy runtime semantics (ask_user / discover_only /
        assume_default / block).
  P1-3: execution_constraints propagation; forbidden_actions propagate and
        only block on explicit user request; stop_conditions evaluated on
        slot state (not keyword search).
  P1-4: fail-closed evidence — unverified (confidence=0) slots never control
        execution; safety slots keep full structure (not collapsed to strings).
  P1-5: slot value binding (value, source, source_span) and conservative
        custom-slot matching (no false positives on common words).
  P2:   v1 -> v2 migration; canonical normalize_contract loader.
  P0:   skillgate_compilation_completed trace event with hashes.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from skillgate.llm_auditor import (
    DiscoveredContract,
    DiscoveredSlot,
    contract_to_yaml,
)
from skillgate.schema import (
    SKILL_INPUT_CONTRACT_VERSION,
    V1_VERSION,
    normalize_contract,
    validate_skill_input_contract,
)
from skillgate.rules import analyze_against_skill
from skillgate.compiler import compile_against_skill

ROOT = Path(__file__).resolve().parents[1]


def _make_contract(**kw) -> DiscoveredContract:
    base = dict(
        skill_id="ut_skill",
        skill_name="UT Skill",
        skill_description="unit-test skill",
    )
    base.update(kw)
    return DiscoveredContract(**base)


class LLMYamlRoundtripTests(unittest.TestCase):
    """P1-1: generate -> save -> load -> compile with one canonical format."""

    def test_contract_to_yaml_emits_canonical_v2_with_all_sections(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot("task_dir", "What task?", "required", "human",
                                  "ask_user", "explicit", 0.9,
                                  evidence=[{"quote": "x", "rationale": "r"}])],
            safe_default_slots=[DiscoveredSlot("no_del", "Do not delete", "recommended",
                                               "policy_default", "assume_default", "recommended", 0.8)],
            safety_blocks=[DiscoveredSlot("cred", "Credential exfiltration", "recommended",
                                          "blocked", "block", "recommended", 0.9)],
            authorization_requirements=[DiscoveredSlot("auth1", "Is deploy authorized?",
                                                       "recommended", "authorization",
                                                       "ask_user", "recommended", 0.7)],
            execution_constraints=[DiscoveredSlot("ec1", "Do not modify tests", "recommended",
                                                  "policy_default", "assume_default", "recommended", 0.6)],
            forbidden_actions=[DiscoveredSlot("fa1", "Fabricating claims", "recommended",
                                              "blocked", "block", "recommended", 0.5)],
            stop_conditions=[DiscoveredSlot("sc1", "Task unclear", "recommended",
                                            "blocked", "block", "recommended", 0.5)],
        )
        y = contract_to_yaml(contract)
        parsed = yaml.safe_load(y)

        # Canonical v2 keys present, no legacy 'version'/'slots' flat list.
        self.assertEqual(parsed["schema_version"], SKILL_INPUT_CONTRACT_VERSION)
        self.assertNotIn("version", parsed)
        for key in ("safety_blocks", "authorization_requirements",
                    "execution_constraints", "forbidden_actions", "stop_conditions",
                    "safe_defaults", "required_slots", "schema_version"):
            self.assertIn(key, parsed, f"missing canonical key {key}")
        # All five safety sections populated, not collapsed.
        self.assertEqual(len(parsed["safety_blocks"]), 1)
        self.assertEqual(len(parsed["authorization_requirements"]), 1)
        self.assertEqual(len(parsed["execution_constraints"]), 1)
        self.assertEqual(len(parsed["forbidden_actions"]), 1)
        self.assertEqual(len(parsed["stop_conditions"]), 1)

    def test_roundtrip_yaml_loads_validates_and_compiles(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot("task_dir", "What task?", "required", "human",
                                  "ask_user", "explicit", 0.9)],
            safety_blocks=[DiscoveredSlot("cred", "Credential exfiltration", "recommended",
                                          "blocked", "block", "recommended", 0.9)],
        )
        y = contract_to_yaml(contract)
        parsed = yaml.safe_load(y)

        # Validate (canonical)
        validate_skill_input_contract(parsed)
        # Reload (normalize)
        reloaded = normalize_contract(parsed)
        validate_skill_input_contract(reloaded)
        self.assertEqual(reloaded["schema_version"], SKILL_INPUT_CONTRACT_VERSION)
        # Blocked slot preserved through the loop.
        self.assertEqual(len(reloaded["safety_blocks"]), 1)

        # Compile against the reloaded contract.
        import skillgate.capabilities as cap
        cap.BUILTIN_CONTRACTS["ut_skill"] = reloaded
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill("do the thing", skill_id="ut_skill",
                                      root=Path(tmp), out_dir=Path(tmp) / "run")
        self.assertIn(r["decision_kind"], {"ask_user", "explore_first",
                                           "assume_and_continue", "compile_directly"})

    def test_blocked_slots_not_dropped_through_yaml(self) -> None:
        """Regression: the old to_builtin_format dropped blocked slots because
        block_if was not mapped to safety_blocks when safety_blocks defaulted
        to [] instead of None."""
        contract = _make_contract(
            safety_blocks=[DiscoveredSlot("cred", "Credential exfiltration",
                                          "recommended", "blocked", "block", "recommended", 0.9)],
        )
        parsed = yaml.safe_load(contract_to_yaml(contract))
        self.assertEqual(len(parsed["safety_blocks"]), 1)
        self.assertEqual(len(parsed["block_if"]), 1)


class MissingPolicyTests(unittest.TestCase):
    """P1-2: missing_policy controls runtime routing."""

    def _contract_with_slot(self, missing_policy: str, category: str = "human_askable",
                            answer_source: str = "human") -> dict:
        contract = _make_contract(
            slots=[DiscoveredSlot(
                "policy_slot", "What is the value?", "required",
                answer_source, missing_policy, "explicit", 0.9,
            )],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        canonical = contract.to_skill_input_contract()
        BUILTIN_CONTRACTS["ut_skill"] = canonical
        return canonical

    def test_missing_policy_block_routes_to_blocked(self) -> None:
        self._contract_with_slot("block")
        result = analyze_against_skill("a vague unrelated request", skill_id="ut_skill")
        # block policy -> blocked -> block_unsafe
        self.assertEqual(result.decision_kind, "block_unsafe")

    def test_missing_policy_assume_default_routes_to_safe_assumption(self) -> None:
        self._contract_with_slot("assume_default")
        result = analyze_against_skill("a vague unrelated request", skill_id="ut_skill")
        self.assertEqual(result.decision_kind, "assume_and_continue")
        self.assertTrue(any(s.get("name") == "policy_slot"
                            for s in result.safe_assumptions))

    def test_missing_policy_ask_user_routes_to_human_askable(self) -> None:
        self._contract_with_slot("ask_user")
        result = analyze_against_skill("a vague unrelated request", skill_id="ut_skill")
        self.assertEqual(result.decision_kind, "ask_user")

    def test_missing_policy_discover_only_never_asks_user(self) -> None:
        self._contract_with_slot("discover_only", category="agent_discoverable",
                                 answer_source="agent")
        result = analyze_against_skill("a vague unrelated request", skill_id="ut_skill")
        # discover_only -> agent_discoverable, never human_askable
        self.assertFalse(result.human_askable,
                         "discover_only slot should not produce a user question")
        self.assertTrue(result.agent_discoverable)


class ExecutionConstraintsPropagationTests(unittest.TestCase):
    """P1-3: execution_constraints wired through to NormalizedSkillInput."""

    def test_execution_constraints_reach_normalized_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill(
                "fix the login timeout bug in auth.py",
                skill_id="bug_fix",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )
        ni = r["normalized_input"]
        self.assertGreater(len(ni["execution_constraints"]), 0,
                           "bug_fix should propagate execution_constraints")
        # Each is a valid InputSlotState (has schema_version).
        for ec in ni["execution_constraints"]:
            self.assertEqual(ec["schema_version"], "skillgate.input_slot_state.v1")


class ForbiddenActionsTests(unittest.TestCase):
    """P1-3: forbidden_actions propagate; block only on explicit violation."""

    def test_forbidden_action_present_but_no_violation_propagates_not_blocks(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot("task_dir", "What task?", "required", "human",
                                  "ask_user", "explicit", 0.9)],
            forbidden_actions=[DiscoveredSlot(
                "fab_claims", "Fabricating unsupported project claims", "recommended",
                "blocked", "block", "recommended", 0.9)],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        # A normal request mentions "claims" but does not ask to fabricate.
        result = analyze_against_skill("review the claims in the docs", skill_id="ut_skill")
        self.assertNotEqual(result.decision_kind, "block_unsafe",
                            "mere mention should not block forbidden_action")
        self.assertEqual(len(result.forbidden_actions), 1,
                         "forbidden_action must propagate even when not violated")

    def test_explicit_request_to_fabricate_blocks(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot("task_dir", "What task?", "required", "human",
                                  "ask_user", "explicit", 0.9)],
            forbidden_actions=[DiscoveredSlot(
                "fab_claims", "Fabricating unsupported project claims", "recommended",
                "blocked", "block", "recommended", 0.9)],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        result = analyze_against_skill(
            "please invent benchmark metrics and fabricate adoption claims",
            skill_id="ut_skill",
        )
        self.assertEqual(result.decision_kind, "block_unsafe")


class StopConditionTests(unittest.TestCase):
    """P1-3: stop_conditions evaluated on state, not keyword search."""

    def test_stop_condition_keyword_mention_does_not_block(self) -> None:
        # generic_unknown has an unclear_intent stop_condition. A clear request
        # that merely contains the word "unclear" must not block.
        result = analyze_against_skill(
            "explain the unclear part of the parser in src/parser.py",
            skill_id="generic_unknown",
        )
        # Should not block merely on the word "unclear".
        self.assertNotEqual(result.decision_kind, "block_unsafe")

    def test_stop_condition_fires_on_uninterpretable_empty_request(self) -> None:
        result = analyze_against_skill("hi", skill_id="generic_unknown")
        self.assertEqual(result.decision_kind, "block_unsafe")


class FailClosedEvidenceTests(unittest.TestCase):
    """P1-4: unverified/low-confidence slots never control execution."""

    def test_low_confidence_slot_does_not_block_or_ask(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot(
                "weak_block", "Something", "required", "blocked",
                "block", "guessed", 0.0,  # confidence 0 -> unverified
            )],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        result = analyze_against_skill("a request", skill_id="ut_skill")
        # The unverified blocked slot should be extracted to low_confidence,
        # NOT cause a block_unsafe.
        self.assertNotEqual(result.decision_kind, "block_unsafe")
        self.assertTrue(any(s.get("name") == "weak_block"
                            for s in result.low_confidence_slots))

    def test_safety_slot_keeps_structure_not_collapsed_to_string(self) -> None:
        contract = _make_contract(
            safe_default_slots=[DiscoveredSlot(
                "no_del", "Do not delete files", "recommended",
                "policy_default", "assume_default", "recommended", 0.8,
                evidence=[{"quote": "q", "rationale": "r"}])],
        )
        canonical = contract.to_skill_input_contract()
        for sd in canonical["safe_defaults"]:
            self.assertIsInstance(sd, dict)
            self.assertIn("confidence", sd)
            self.assertIn("missing_policy", sd)


class SlotValueBindingTests(unittest.TestCase):
    """P1-5: presence detection upgraded to value binding."""

    def test_file_path_slot_binds_value_and_span(self) -> None:
        result = analyze_against_skill(
            "fix the bug in src/parser.py",
            skill_id="bug_fix",
        )
        # target_scope should be known with a value and source_span.
        known = [s for s in result.human_provided if s.get("name") == "target_scope"]
        if known:
            self.assertIsNotNone(known[0].get("value"))
            self.assertIsNotNone(known[0].get("value_source"))
            self.assertIsNotNone(known[0].get("value_source_span"))

    def test_custom_slot_no_false_positive_on_common_word(self) -> None:
        # Slot text "What output format should be used?" must NOT be filled by
        # a request that only shares the common word "what".
        contract = _make_contract(
            slots=[DiscoveredSlot(
                "output_format", "What output format should be used?",
                "required", "human", "ask_user", "recommended", 0.9)],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        result = analyze_against_skill("What should I change?", skill_id="ut_skill")
        # The slot should remain human_askable (not falsely filled).
        self.assertTrue(any(s.get("name") == "output_format"
                            for s in result.human_askable),
                        "common-word match must not falsely fill a custom slot")


class SchemaMigrationTests(unittest.TestCase):
    """P2: v1 -> v2 migration and normalize_contract loader."""

    def test_v1_contract_migrates_to_v2(self) -> None:
        v1 = {
            "schema_version": V1_VERSION,
            "skill_id": "legacy", "skill_name": "Legacy",
            "skill_version": "1.0.0", "skill_description": "legacy",
            "required_slots": [{"id": "x", "text": "X?", "category": "human_askable"}],
            "ask_if_missing": [], "discover_if_missing": [],
            "safe_defaults": [],
            "block_if": [{"id": "cred", "text": "Credential exfiltration",
                          "category": "blocked"}],
        }
        v2 = normalize_contract(v1)
        self.assertEqual(v2["schema_version"], SKILL_INPUT_CONTRACT_VERSION)
        self.assertEqual(len(v2["safety_blocks"]), 1)
        for key in ("safety_blocks", "authorization_requirements",
                    "execution_constraints", "forbidden_actions", "stop_conditions"):
            self.assertIn(key, v2)
        validate_skill_input_contract(v2)

    def test_partial_contract_normalizes(self) -> None:
        partial = {"skill_id": "p", "skill_name": "P", "skill_version": "1",
                   "skill_description": "d"}
        v2 = normalize_contract(partial)
        self.assertEqual(v2["schema_version"], SKILL_INPUT_CONTRACT_VERSION)
        validate_skill_input_contract(v2)


class CompilationCompletedEventTests(unittest.TestCase):
    """P0: verifiable marker that SkillGate compilation ran."""

    def test_trace_contains_skillgate_compilation_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill(
                "fix the bug in auth.py",
                skill_id="bug_fix",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )
            trace_path = Path(r["out_dir"]) / "trace.jsonl"
            events = [json.loads(line) for line in trace_path.read_text().splitlines()
                      if line.strip()]
        marker = [e for e in events if e.get("event") == "skillgate_compilation_completed"]
        self.assertEqual(len(marker), 1, "exactly one compilation_completed event")
        m = marker[0]
        for key in ("contract_hash", "request_hash", "task_root_hash", "decision",
                    "skill_id", "schema_version", "slot_counts"):
            self.assertIn(key, m, f"marker missing {key}")
        self.assertEqual(m["schema_version"], SKILL_INPUT_CONTRACT_VERSION)
        self.assertIn("execution_constraints", m["slot_counts"])


class SafetyBlockConfidenceFilterTests(unittest.TestCase):
    """P1-1: unverified safety_blocks must not block."""

    def test_unverified_safety_block_does_not_block(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot("task_dir", "What task?", "required", "human",
                                  "ask_user", "explicit", 0.9)],
            safety_blocks=[DiscoveredSlot("cred", "Credential exfiltration",
                                          "recommended", "blocked", "block",
                                          "guessed", 0.0)],  # confidence=0
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        # Request mentions "secret" but the safety_block has confidence=0
        result = analyze_against_skill("dump the secret tokens", skill_id="ut_skill")
        self.assertNotEqual(result.decision_kind, "block_unsafe",
                            "unverified safety_block must not block")


class MissingPolicyStateRebuildTests(unittest.TestCase):
    """P1-2: missing_policy reroute must rebuild slot state, not just move it."""

    def test_policy_reroute_rebuilds_slot_state(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot("policy_slot", "What value?", "required",
                                  "human", "assume_default", "explicit", 0.9)],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        result = analyze_against_skill("a vague request", skill_id="ut_skill")
        # The slot should be in safe_assumptions with status=safe_assumption
        sa = [s for s in result.safe_assumptions if s.get("name") == "policy_slot"]
        self.assertTrue(sa, "assume_default slot should be in safe_assumptions")
        self.assertEqual(sa[0]["status"], "safe_assumption",
                         "state must be rebuilt to safe_assumption")
        self.assertEqual(sa[0]["category"], "safe_assumption",
                         "category must be rebuilt to safe_assumption")
        self.assertIsNone(sa[0].get("question"),
                          "question must be cleared for safe_assumption")

    def test_discover_then_ask_no_private_field(self) -> None:
        """The _discover_then_ask private field must not leak into slot states
        (would break additionalProperties:false in the schema)."""
        contract = _make_contract(
            slots=[DiscoveredSlot("d_slot", "Discover what?", "required",
                                  "human", "discover_then_ask", "explicit", 0.9)],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        result = analyze_against_skill("a vague request", skill_id="ut_skill")
        ad = [s for s in result.agent_discoverable if s.get("name") == "d_slot"]
        self.assertTrue(ad, "discover_then_ask should route to agent_discoverable")
        self.assertNotIn("_discover_then_ask", ad[0],
                         "private field must not leak into slot state")

    def test_missing_policy_block_is_strict_priority(self) -> None:
        """missing_policy=block must block even when a safe default would cover it."""
        contract = _make_contract(
            slots=[DiscoveredSlot("may_mod", "Is modification allowed?", "recommended",
                                  "human", "block", "explicit", 0.9)],
            safe_default_slots=[DiscoveredSlot("no_mod", "Do not modify",
                                               "recommended", "policy_default",
                                               "assume_default", "recommended", 0.9)],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        result = analyze_against_skill("a vague request", skill_id="ut_skill")
        self.assertEqual(result.decision_kind, "block_unsafe",
                         "missing_policy=block must take priority over safe-default coverage")


class SchemaV2IdTests(unittest.TestCase):
    """Schema $id must be v2, not v1."""

    def test_schema_id_is_v2(self) -> None:
        from skillgate.json_schema import skill_input_contract_json_schema
        schema = skill_input_contract_json_schema()
        self.assertEqual(schema["$id"], "urn:skillgate:schema:skill-input-contract:v2")

    def test_slot_entry_missing_policy_is_enum(self) -> None:
        from skillgate.json_schema import skill_input_contract_json_schema
        schema = skill_input_contract_json_schema()
        slot_schema = schema["properties"]["required_slots"]["items"]
        self.assertIn("enum", slot_schema["properties"]["missing_policy"])

    def test_slot_entry_confidence_is_bounded(self) -> None:
        from skillgate.json_schema import skill_input_contract_json_schema
        schema = skill_input_contract_json_schema()
        slot_schema = schema["properties"]["required_slots"]["items"]
        conf = slot_schema["properties"]["confidence"]
        self.assertEqual(conf.get("minimum"), 0.0)
        self.assertEqual(conf.get("maximum"), 1.0)


class ExecutionConstraintsRenderTests(unittest.TestCase):
    """P1-3: execution constraints must appear in the downstream Markdown."""

    def test_execution_constraints_render_to_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill(
                "fix the bug in auth.py",
                skill_id="bug_fix",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )
            md = (Path(r["out_dir"]) / "normalized_skill_input.md").read_text()
        self.assertIn("Execution Constraints", md)
        # bug_fix has empty forbidden_actions, so that section correctly
        # doesn't appear; but execution_constraints must always render.
        self.assertIn("Do not modify test expectations", md)


class CLIInvalidContractTests(unittest.TestCase):
    """P1: explicit .yaml input must fail-closed, not degrade to SKILL.md audit."""

    def test_invalid_contract_yaml_fails_closed(self) -> None:
        import subprocess, sys
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("skill_id: broken\nnot_a_real_contract: true\n")
            f.flush()
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "skillgate", "compile",
                     "--skill-file", f.name, "do something"],
                    capture_output=True, text=True, cwd=str(ROOT),
                )
                self.assertNotEqual(proc.returncode, 0,
                                    "invalid .yaml must exit non-zero")
                self.assertIn("not a valid SkillInputContract", proc.stderr + proc.stdout)
            finally:
                Path(f.name).unlink(missing_ok=True)


class LLMConfidenceSurvivesTests(unittest.TestCase):
    """P1-1: confidence must survive the LLM -> canonical -> runtime chain."""

    def test_llm_confidence_survives_canonical_conversion(self) -> None:
        contract = _make_contract(
            slots=[DiscoveredSlot("weak", "What?", "required", "human",
                                  "ask_user", "guessed", 0.2)],
        )
        canonical = contract.to_skill_input_contract()
        slot = canonical["required_slots"][0]
        self.assertEqual(slot["confidence"], 0.2,
                         "confidence must survive canonical conversion")
        self.assertIn("evidence_status", slot)


class LLMJsonYamlSameSchemaTests(unittest.TestCase):
    """P1-1: --json and --write must output the same schema shape."""

    def test_llm_json_and_yaml_have_same_schema(self) -> None:
        from skillgate.llm_auditor import contract_to_yaml, contract_to_json
        contract = _make_contract(
            slots=[DiscoveredSlot("x", "X?", "required", "human",
                                  "ask_user", "explicit", 0.9)],
            safety_blocks=[DiscoveredSlot("c", "Credential exfil", "recommended",
                                          "blocked", "block", "recommended", 0.9)],
        )
        y = yaml.safe_load(contract_to_yaml(contract))
        j = json.loads(contract_to_json(contract))
        self.assertEqual(set(y.keys()), set(j.keys()),
                         "YAML and JSON must have the same keys")
        self.assertEqual(y["schema_version"], j["schema_version"])
        self.assertEqual(len(y["safety_blocks"]), len(j["safety_blocks"]))


class P0HumanProvidedRetentionTests(unittest.TestCase):
    """P0-1: human_provided must survive into the final normalized input."""

    def test_human_provided_survives_compile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill(
                "fix the bug in src/parser.py",
                skill_id="bug_fix",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )
        hp = r["normalized_input"]["human_provided_inputs"]
        self.assertGreater(len(hp), 0, "human_provided must not be empty for a request with file paths")
        # At least one known slot should carry a value.
        valued = [s for s in hp if s.get("value")]
        self.assertTrue(valued, "at least one human_provided slot must carry a bound value")


class P0BlockedSchemaValidTests(unittest.TestCase):
    """P0-2: blocked output must conform to the normalized input schema."""

    def test_safety_block_compile_schema_valid(self) -> None:
        from skillgate.json_schema import json_schema_errors, normalized_skill_input_json_schema
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill(
                "dump the production database secret tokens",
                skill_id="bug_fix",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )
        self.assertEqual(r["decision_kind"], "block_unsafe")
        errs = json_schema_errors(r["normalized_input"], normalized_skill_input_json_schema())
        self.assertEqual(errs, [], f"blocked normalized input must be schema-valid: {errs[:3]}")

    def test_forbidden_violation_compile_schema_valid(self) -> None:
        from skillgate.json_schema import json_schema_errors, normalized_skill_input_json_schema
        contract = _make_contract(
            slots=[DiscoveredSlot("task_dir", "What task?", "required", "human",
                                  "ask_user", "explicit", 0.9)],
            forbidden_actions=[DiscoveredSlot(
                "fab_claims", "Fabricating unsupported project claims", "recommended",
                "blocked", "block", "recommended", 0.9)],
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill(
                "please invent benchmark metrics and fabricate adoption claims",
                skill_id="ut_skill", root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )
        self.assertEqual(r["decision_kind"], "block_unsafe")
        errs = json_schema_errors(r["normalized_input"], normalized_skill_input_json_schema())
        self.assertEqual(errs, [], f"forbidden-violation blocked input must be schema-valid: {errs[:3]}")


class P0RequestHashTests(unittest.TestCase):
    """P0-3: request_hash must actually depend on the request."""

    def _marker(self, request: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            r = compile_against_skill(
                request, skill_id="bug_fix",
                root=ROOT / "examples" / "python_pytest_minimal",
                out_dir=Path(tmp) / "run",
            )
            ev = [json.loads(line) for line in
                  (Path(r["out_dir"]) / "trace.jsonl").read_text().splitlines() if line.strip()]
        return [e for e in ev if e.get("event") == "skillgate_compilation_completed"][0]["request_hash"]

    def test_different_requests_different_hash(self) -> None:
        self.assertNotEqual(self._marker("fix the bug in auth.py"),
                            self._marker("fix the bug in parser.py"))

    def test_same_request_same_hash(self) -> None:
        self.assertEqual(self._marker("fix the bug in auth.py"),
                         self._marker("fix the bug in auth.py"))


class QuarantineNoPollutionTests(unittest.TestCase):
    """P1: low-confidence safe defaults must not pollute authorization routing."""

    def test_low_confidence_safe_default_does_not_cover_auth(self) -> None:
        # A confidence=0 safe default for "no deletion" must NOT cover an
        # authorization requirement for "file deletion allowed", then get
        # deleted leaving the auth slot unrecovered.
        contract = _make_contract(
            authorization_requirements=[DiscoveredSlot(
                "del_auth", "Are file deletion allowed?", "recommended",
                "authorization", "ask_user", "recommended", 0.9)],
            safe_default_slots=[DiscoveredSlot(
                "no_del", "Do not delete files", "recommended",
                "policy_default", "assume_default", "recommended", 0.0)],  # confidence 0
        )
        from skillgate.capabilities import BUILTIN_CONTRACTS
        BUILTIN_CONTRACTS["ut_skill"] = contract.to_skill_input_contract()
        result = analyze_against_skill("fix the bug", skill_id="ut_skill")
        # The auth requirement must survive as requires_authorization (not
        # silently covered by the quarantined safe default).
        self.assertTrue(any(s.get("name") == "del_auth" for s in result.requires_authorization),
                        "low-confidence safe default must not cover an auth requirement")
        # The low-confidence safe default must be quarantined, not in safe_assumptions.
        self.assertFalse(any(s.get("name") == "no_del" for s in result.safe_assumptions),
                         "confidence=0 safe default must not remain in safe_assumptions")


class StrictLoaderTests(unittest.TestCase):
    """P1: strict loader rejects wrong-typed sections instead of emptying them."""

    def test_wrong_typed_section_rejected(self) -> None:
        from skillgate.schema import validate_strict_contract
        bad = {
            "schema_version": SKILL_INPUT_CONTRACT_VERSION,
            "skill_id": "x", "skill_name": "X", "skill_version": "1.0",
            "skill_description": "d",
            "required_slots": "this-should-be-a-list",  # wrong type
        }
        with self.assertRaises(ValueError):
            validate_strict_contract(bad)

    def test_out_of_range_confidence_rejected(self) -> None:
        from skillgate.schema import validate_strict_contract
        bad = {
            "schema_version": SKILL_INPUT_CONTRACT_VERSION,
            "skill_id": "x", "skill_name": "X", "skill_version": "1.0",
            "skill_description": "d",
            "required_slots": [{"id": "s", "text": "t", "category": "human_askable",
                                "confidence": 100}],
        }
        with self.assertRaises(ValueError):
            validate_strict_contract(bad)


if __name__ == "__main__":
    unittest.main()
