"""LLM-assisted Skill Contract Discovery (Phase A).

Uses a four-stage pipeline to audit a SKILL.md and produce a SkillInputContract:

  Stage 1: Extract   — surface explicit content (triggers, steps, constraints)
  Stage 2: Infer     — derive missing input requirements from explicit content
  Stage 3: Classify  — assign answer_source per slot (human / agent / human_or_agent / …)
  Stage 4: Critique  — self-review: did we push non-human questions to the user?

Design principle:
  LLM discovers the contract.  The rules engine executes it (Phase B).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Output schema ─────────────────────────────────────────────


@dataclass
class DiscoveredSlot:
    """A single slot discovered by the LLM auditor."""
    name: str
    description: str
    necessity: str          # required | recommended | optional
    answer_source: str      # human | agent | human_or_agent | authorization | policy_default | blocked
    missing_policy: str     # ask_user | discover_then_ask | discover_only | assume_default | block
    support: str            # explicit | inferred | recommended | guessed
    confidence: float       # 0.0 – 1.0
    evidence: list[dict[str, str]] = field(default_factory=list)  # [{quote, rationale, quote_verified, quote_line_start}]
    # evidence_status is derived from quote verification; only set on the
    # canonical slot entries emitted by to_skill_input_contract().
    evidence_status: str = "unverified"


@dataclass
class DiscoveredContract:
    """Full contract discovered from a SKILL.md by the LLM auditor.

    The five safety categories mirror the canonical ``SkillInputContract`` so
    that :meth:`to_skill_input_contract` can emit a complete v2 contract with
    no information loss.  ``block_if``/``safe_defaults`` are backward-compat
    text views (lists of ``str``) kept for the human-readable printout and the
    legacy trace summary.
    """
    version: str = "skill_input_contract.v2"
    skill_id: str = ""
    skill_name: str = ""
    skill_version: str = "1.0.0"
    skill_description: str = ""
    activation: dict[str, Any] = field(default_factory=dict)  # {triggers: [...], anti_triggers: [...]}
    slots: list[DiscoveredSlot] = field(default_factory=list)
    safe_default_slots: list[DiscoveredSlot] = field(default_factory=list)
    safety_blocks: list[DiscoveredSlot] = field(default_factory=list)
    authorization_requirements: list[DiscoveredSlot] = field(default_factory=list)
    execution_constraints: list[DiscoveredSlot] = field(default_factory=list)
    forbidden_actions: list[DiscoveredSlot] = field(default_factory=list)
    stop_conditions: list[DiscoveredSlot] = field(default_factory=list)

    # ── backward-compat text views (list[str]) ──────────────
    @property
    def safe_defaults(self) -> list[str]:
        return [s.description for s in self.safe_default_slots]

    @safe_defaults.setter
    def safe_defaults(self, value: list[Any]) -> None:
        self.safe_default_slots = [_coerce_slot(v) for v in value]

    @property
    def block_if(self) -> list[str]:
        return [s.description for s in self.safety_blocks]

    @block_if.setter
    def block_if(self, value: list[Any]) -> None:
        self.safety_blocks = [_coerce_slot(v) for v in value]

    def to_skill_input_contract(self) -> dict[str, Any]:
        """Transform to a canonical, complete ``SkillInputContract`` v2 dict.

        This is the ONLY public serialization path.  Every slot — including
        ``policy_default`` and ``blocked`` — is preserved as a structured
        entry carrying ``support``, ``answer_source``, ``missing_policy``,
        ``confidence`` and ``evidence_status``, so nothing is lost on the way
        to ``SKILL.input.yaml`` and the rules engine can read every field.
        """
        from .schema import build_skill_input_contract, EVIDENCE_STATUSES

        category_map = {
            "human": "human_askable",
            "agent": "agent_discoverable",
            "human_or_agent": "human_askable",
            "authorization": "requires_authorization",
            "policy_default": "safe_assumption",
            "blocked": "blocked",
        }
        source_map: dict[str, str] = {
            "human": "human",
            "agent": "agent",
            "human_or_agent": "human_or_agent",
            "authorization": "authorization",
            "policy_default": "policy_default",
            "blocked": "blocked",
        }

        required: list[dict[str, Any]] = []
        ask_if_missing: list[dict[str, Any]] = []
        discover_if_missing: list[dict[str, Any]] = []

        def _entry(s: DiscoveredSlot, category: str) -> dict[str, Any]:
            return {
                "id": s.name,
                "text": s.description,
                "category": category,
                "support": s.support,
                "answer_source": source_map.get(s.answer_source, s.answer_source),
                "missing_policy": s.missing_policy,
                "confidence": round(s.confidence, 4),
                "evidence_status": s.evidence_status if s.evidence_status in EVIDENCE_STATUSES else "unverified",
            }

        for s in self.slots:
            cat = category_map.get(s.answer_source, "human_askable")
            entry = _entry(s, cat)
            if s.necessity == "required":
                required.append(entry)
            elif s.necessity == "recommended":
                ask_if_missing.append(entry)
            else:
                discover_if_missing.append(entry)

        safe_defaults = [_entry(s, "safe_assumption") for s in self.safe_default_slots]
        safety_blocks = [_entry(s, "blocked") for s in self.safety_blocks]
        authorization_requirements = [_entry(s, "requires_authorization") for s in self.authorization_requirements]
        execution_constraints = [_entry(s, "safe_assumption") for s in self.execution_constraints]
        forbidden_actions = [_entry(s, "blocked") for s in self.forbidden_actions]
        stop_conditions = [_entry(s, "blocked") for s in self.stop_conditions]

        return build_skill_input_contract(
            skill_id=self.skill_id,
            skill_name=self.skill_name,
            skill_version=self.skill_version,
            skill_description=self.skill_description or self.skill_name,
            required_slots=required,
            ask_if_missing=ask_if_missing,
            discover_if_missing=discover_if_missing,
            safe_defaults=safe_defaults,
            safety_blocks=safety_blocks,
            authorization_requirements=authorization_requirements,
            execution_constraints=execution_constraints,
            forbidden_actions=forbidden_actions,
            stop_conditions=stop_conditions,
        )

    # Backward-compat alias for older callers / tests.
    def to_builtin_format(self) -> dict[str, Any]:
        return self.to_skill_input_contract()

    @classmethod
    def from_llm_output(cls, raw_parsed: dict[str, Any]) -> dict[str, Any]:
        """Build a canonical ``SkillInputContract`` from the LLM's raw parsed output.

        This is the public bridge: feed it the JSON the LLM returned after
        the review stage and get back a validated contract dict suitable for
        ``yaml.safe_dump()`` or direct injection into ``BUILTIN_CONTRACTS``.
        """
        slots: list[dict[str, Any]] = raw_parsed.get("slots", [])
        if not isinstance(slots, list):
            slots = []

        contract = _build_discovered_contract_from_slots(slots, raw_parsed)
        return contract.to_skill_input_contract()


def _coerce_slot(value: Any) -> DiscoveredSlot:
    """Coerce a legacy slot representation (str or dict) into a DiscoveredSlot."""
    if isinstance(value, DiscoveredSlot):
        return value
    if isinstance(value, str):
        return DiscoveredSlot(
            name=_text_to_id(value),
            description=value,
            necessity="recommended",
            answer_source="policy_default",
            missing_policy="assume_default",
            support="recommended",
            confidence=0.5,
        )
    if isinstance(value, dict):
        return DiscoveredSlot(
            name=value.get("name") or value.get("id", "unknown"),
            description=value.get("description") or value.get("text", ""),
            necessity=value.get("necessity", "recommended"),
            answer_source=value.get("answer_source", "policy_default"),
            missing_policy=value.get("missing_policy", "assume_default"),
            support=value.get("support", "recommended"),
            confidence=float(value.get("confidence", 0.5)),
            evidence=value.get("evidence", []),
            evidence_status=value.get("evidence_status", "unverified"),
        )
    return DiscoveredSlot(
        name="unknown", description="", necessity="recommended",
        answer_source="policy_default", missing_policy="assume_default",
        support="recommended", confidence=0.5,
    )


def _build_discovered_contract_from_slots(
    slots: list[dict[str, Any]],
    raw_parsed: dict[str, Any],
) -> DiscoveredContract:
    """Reconstruct a DiscoveredContract from raw LLM slot dicts."""
    skill_name = raw_parsed.get("skill_name", "")
    skill_description = raw_parsed.get("skill_description") or skill_name

    normal_slots: list[DiscoveredSlot] = []
    safe_defaults: list[DiscoveredSlot] = []
    safety_blocks: list[DiscoveredSlot] = []
    authorization_requirements: list[DiscoveredSlot] = []
    execution_constraints: list[DiscoveredSlot] = []
    forbidden_actions: list[DiscoveredSlot] = []
    stop_conditions: list[DiscoveredSlot] = []

    for raw in slots:
        if not isinstance(raw, dict):
            continue
        ans = raw.get("answer_source", "human")
        slot = DiscoveredSlot(
            name=raw.get("name", "unknown"),
            description=raw.get("description", ""),
            necessity=raw.get("necessity", "recommended"),
            answer_source=ans,
            missing_policy=raw.get("missing_policy", "ask_user"),
            support=raw.get("support", "guessed"),
            confidence=float(raw.get("confidence", 0.5)),
            evidence=raw.get("evidence", []),
            evidence_status=raw.get("evidence_status", "unverified"),
        )
        if ans == "policy_default":
            safe_defaults.append(slot)
        elif ans == "blocked":
            # Map blocked slots to the right structural bucket when the LLM
            # tagged the safety class; otherwise default to safety_blocks.
            safety_class = (raw.get("safety_class") or "").strip().lower()
            if safety_class == "forbidden_action":
                forbidden_actions.append(slot)
            elif safety_class == "stop_condition":
                stop_conditions.append(slot)
            elif safety_class == "execution_constraint":
                execution_constraints.append(slot)
            else:
                safety_blocks.append(slot)
        elif ans == "authorization":
            authorization_requirements.append(slot)
        else:
            normal_slots.append(slot)

    return DiscoveredContract(
        skill_id=raw_parsed.get("skill_id", ""),
        skill_name=skill_name,
        skill_version=raw_parsed.get("skill_version", "1.0.0"),
        skill_description=skill_description,
        activation=raw_parsed.get("activation", {}),
        slots=normal_slots,
        safe_default_slots=safe_defaults,
        safety_blocks=safety_blocks,
        authorization_requirements=authorization_requirements,
        execution_constraints=execution_constraints,
        forbidden_actions=forbidden_actions,
        stop_conditions=stop_conditions,
    )


def _text_to_id(text: str) -> str:
    return text.lower().replace(" ", "_").replace("-", "_")[:40]


# ── Prompt templates ──────────────────────────────────────────


_STAGE1_EXTRACT = """You are auditing a skill instruction document (SKILL.md / AGENTS.md).

## Task: Extract explicit content

Read the skill document below and extract ONLY what is explicitly stated. Do NOT infer or guess anything.

Return a JSON object with these fields:

1. `activation_triggers`: list of strings — what triggers this skill? (e.g., "failing test", "user reports a bug")
2. `execution_steps`: list of strings — what does the agent do when activated?
3. `output_requirements`: list of strings — what must the agent produce?
4. `forbidden_actions`: list of strings — what must the agent NOT do?
5. `verification_statements`: list of strings — how to verify completion?
6. `safety_constraints`: list of strings — any safety or security rules mentioned?

If the document has no explicit information for a field, return an empty list.
If the document has a YAML front matter with structured fields, extract those.

Return ONLY valid JSON (no markdown, no explanation).

## Skill Document
{skill_content}
"""


_STAGE2_INFER = """You are auditing a skill instruction document.

## Task: Infer missing input requirements

You have extracted the following explicit content from the skill document:

{extracted_content}

Now, from the explicit execution steps, output requirements, and constraints,
INFER what input information the agent needs BEFORE activation.

For each inferred input, state:

1. `name`: short unique identifier (snake_case)
2. `description`: what information is needed, in plain language
3. `necessity`: "required" or "recommended" — is this strictly necessary for safe execution?
4. `evidence`: a list with one object: {{"quote": "exact quote from skill doc", "rationale": "why this quote implies this input need"}}

Rules:
- ONLY infer from explicit content. Do not fabricate.
- If the skill says "reproduce the failure", the agent needs a failure_symptom.
- If the skill says "report verification result", the agent needs verification_expectation.
- If the skill says "do not perform large refactors", the agent needs allowed_change_scope.
- If a slot is purely safe-practice (not from explicit doc), mark necessity as "recommended".

Return ONLY valid JSON: a list of inferred input objects.

## Explicit Content
{extracted_content}
"""


_STAGE3_CLASSIFY = """You are auditing a skill's input contract.

## Task: Classify who should provide each input

You have the following list of inputs the agent needs:

{inferred_slots}

For each input, determine:

1. `answer_source`: who should provide this information?
   - "human": the user knows this (intent, preference, scope, permissions, risk tolerance, success criteria, output audience)
   - "agent": the agent can discover this from local files (file structure, test commands, config, code patterns, dependencies)
   - "human_or_agent": the user might know, but the agent should try to discover first; only ask the user for minimal evidence if discovery fails
   - "authorization": requires explicit user authorization (delete files, push, deploy, change public API, payment, external service calls)
   - "policy_default": a safe default policy covers this (do not modify tests, do not delete files, read-only, minimal changes)

2. `missing_policy`: what to do if this input is missing?
   - "ask_user": must ask the user
   - "discover_then_ask": agent discovers first, asks user only if discovery fails
   - "discover_only": agent discovers from local context, never asks user
   - "assume_default": apply a safe conservative default
   - "block": refuse to proceed

3. `support`: how well-supported is this classification?
   - "explicit": directly stated in the skill document
   - "inferred": reasonably derived from explicit content
   - "recommended": best-practice default, not from document

4. `confidence`: 0.0 – 1.0

5. `safety_class` (only when answer_source is "blocked" or "authorization"): one of
   - "safety_block": dangerous request itself (credential/secret access, production mutation, destructive ops)
   - "forbidden_action": actions the agent must never perform (fabricating claims, introducing deps)
   - "stop_condition": conditions under which execution must halt (intent unclear, missing critical input)
   - "execution_constraint": invariants the agent must respect during execution (do not modify tests)
   - "authorization": action requires explicit user permission before proceeding (delete, push, deploy, payment)

CRITICAL: Do NOT classify as "human" things the agent can read from local files.
The agent CAN read: file structure, package config, test commands, build config, code, dependencies, conventions, README, CONTRIBUTING, CI config.
The agent CANNOT know: user intent, user preference, scope boundaries, risk tolerance, desired output audience.

Return ONLY valid JSON: a list of classified input objects (add answer_source, missing_policy, support, confidence to each).

## Inferred Inputs
{inferred_slots}
"""


_STAGE4_CRITIQUE = """You audited a skill and produced an input contract. Now review it critically.

## Previous output (slots with classifications):

{classified_slots}

## Self-Critique Checklist

For EACH slot, answer honestly:

1. **Misclassification check**: Is the answer_source correct? Did we classify something as "human"
   that the agent could discover from local files? If so, fix it.

2. **Over-asking check**: Did we ask the user for information they cannot reasonably answer?
   (e.g., "list all source files", "provide complete codebase", "describe all tests").
   If so, reclassify to agent or human_or_agent.

3. **Necessity check**: Is every "required" slot truly required for safe execution?
   If a slot is merely best-practice, downgrade to "recommended".

4. **Missing check**: Are there any obvious gaps — inputs the agent clearly needs
   based on the explicit content, but we missed?

5. **Evidence check**: For each slot with support="inferred" or "recommended",
   is the evidence accurate and traceable to the skill document?

## Task

Return the CORRECTED list of slots (same format, but with fixes applied).
Decrease confidence for any slot you changed.

Return ONLY valid JSON.

## Classified Slots
{classified_slots}
"""


# ── LLM abstraction ───────────────────────────────────────────


class LLMBackend:
    """Abstract LLM interface. Implementations: MockLLM, OpenAILLM."""

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class MockLLM(LLMBackend):
    """Returns predefined fixture contracts for testing."""

    FIXTURES: dict[str, dict[str, Any]] = {}

    @classmethod
    def register_fixture(cls, skill_name: str, fixture: dict[str, Any]) -> None:
        cls.FIXTURES[skill_name.lower()] = fixture

    def __init__(self, fixture_name: str | None = None):
        self._fixture = fixture_name

    def generate(self, prompt: str) -> str:
        lower = prompt.lower()
        lines = prompt.split("\n")
        first_line = lines[0].lower() if lines else ""

        # Stage 1: Extract — prompt starts with "You are auditing a skill instruction document"
        if "## Task: Extract explicit content" in prompt:
            if self._fixture and self._fixture in self.FIXTURES:
                return json.dumps(self.FIXTURES[self._fixture].get("extracted", {}))
            return json.dumps({})

        # Stage 2: Infer — prompt contains "Infer missing input requirements"
        if "## Task: Infer missing input requirements" in prompt:
            if self._fixture and self._fixture in self.FIXTURES:
                return json.dumps(self.FIXTURES[self._fixture].get("inferred", []))
            return json.dumps([])

        # Stage 3: Classify — prompt contains "Classify who should provide each input"
        if "## Task: Classify who should provide each input" in prompt:
            if self._fixture and self._fixture in self.FIXTURES:
                return json.dumps(self.FIXTURES[self._fixture].get("classified", []))
            return json.dumps([])

        # Stage 4: Critique — prompt contains "Now review it critically"
        if "## Self-Critique Checklist" in prompt:
            if self._fixture and self._fixture in self.FIXTURES:
                return json.dumps(self.FIXTURES[self._fixture].get("reviewed", []))
            return json.dumps([])

        return json.dumps({})


class OpenAILLM(LLMBackend):
    """OpenAI-compatible LLM backend.

    Works with any OpenAI-compatible endpoint (OpenAI, Azure-compatible
    gateways, USTC GLM, ZhipuAI, etc.) by setting ``base_url``. Credentials and
    model can be supplied via the constructor or environment variables:

      OPENAI_API_KEY      — API key (required)
      OPENAI_BASE_URL     — endpoint base URL (optional; e.g.
                            https://api.llm.ustc.edu.cn/v1)
      SKILLGATE_LLM_MODEL — model id override (optional; default gpt-4o-mini)
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.model = model or os.environ.get("SKILLGATE_LLM_MODEL") or "gpt-4o-mini"
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set and no api_key provided")

    def generate(self, prompt: str) -> str:
        import importlib
        openai_mod = importlib.import_module("openai")
        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = openai_mod.OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            timeout=300,
        )
        return response.choices[0].message.content or ""


# ── Four-stage pipeline ───────────────────────────────────────


def audit_skill_with_llm_traced(
    skill_content: str,
    llm: LLMBackend,
    skill_id_hint: str | None = None,
) -> tuple[DiscoveredContract, dict[str, Any]]:
    """Run the four-stage LLM audit pipeline, returning the contract and a trace.

    The trace captures each stage's raw LLM output and parsed result so audit
    quality can be evaluated for slot recall, slot precision, answer-source
    accuracy, evidence grounding, human burden, and safety coverage.

    Args:
        skill_content: Raw text content of the skill document.
        llm: LLM backend (MockLLM or OpenAILLM).
        skill_id_hint: Optional skill_id if known (e.g., from filename).

    Returns:
        ``(DiscoveredContract, trace)`` where ``trace`` is a dict with a
        ``stages`` list (extract / infer / classify / critique) and a
        ``contract`` summary.
    """
    trace: dict[str, Any] = {
        "llm_backend": type(llm).__name__,
        "model": getattr(llm, "model", None),
        "stages": [],
    }

    # ── Stage 1: Extract ──────────────────────────────────
    extracted_raw = llm.generate(_STAGE1_EXTRACT.format(skill_content=skill_content))
    try:
        extracted = json.loads(_clean_json(extracted_raw))
    except json.JSONDecodeError:
        extracted = {}
    if not isinstance(extracted, dict):
        extracted = {}
    trace["stages"].append({
        "stage": "extract",
        "raw": extracted_raw,
        "parsed": extracted,
    })

    # ── Stage 2: Infer ────────────────────────────────────
    inferred_raw = llm.generate(_STAGE2_INFER.format(
        extracted_content=json.dumps(extracted, indent=2)))
    try:
        inferred = json.loads(_clean_json(inferred_raw))
    except json.JSONDecodeError:
        inferred = []

    if not isinstance(inferred, list):
        inferred = [inferred] if isinstance(inferred, dict) else []

    # ── Quote verification ────────────────────────────────
    inferred = _verify_quotes(inferred, skill_content)

    trace["stages"].append({
        "stage": "infer",
        "raw": inferred_raw,
        "parsed": inferred,
    })

    # ── Stage 3: Classify ─────────────────────────────────
    classified_raw = llm.generate(_STAGE3_CLASSIFY.format(
        inferred_slots=json.dumps(inferred, indent=2)))
    try:
        classified = json.loads(_clean_json(classified_raw))
    except json.JSONDecodeError:
        classified = inferred  # fallback: keep unclassified

    if not isinstance(classified, list):
        classified = [classified] if isinstance(classified, dict) else inferred
    trace["stages"].append({
        "stage": "classify",
        "raw": classified_raw,
        "parsed": classified,
    })

    # ── Stage 4: Critique ─────────────────────────────────
    reviewed_raw = llm.generate(_STAGE4_CRITIQUE.format(
        classified_slots=json.dumps(classified, indent=2)))
    try:
        reviewed = json.loads(_clean_json(reviewed_raw))
    except json.JSONDecodeError:
        reviewed = classified  # fallback: keep pre-critique

    if not isinstance(reviewed, list):
        reviewed = classified
    trace["stages"].append({
        "stage": "critique",
        "raw": reviewed_raw,
        "parsed": reviewed,
    })

    # ── Build contract ────────────────────────────────────
    # Route every reviewed slot into the right contract section so that
    # to_skill_input_contract() emits a complete v2 contract with no loss.
    # The LLM tags answer_source + (optional) safety_class; we honor both.
    normal_slots: list[DiscoveredSlot] = []
    safe_default_slots: list[DiscoveredSlot] = []
    safety_blocks: list[DiscoveredSlot] = []
    authorization_slots: list[DiscoveredSlot] = []
    exec_constraint_slots: list[DiscoveredSlot] = []
    forbidden_slots: list[DiscoveredSlot] = []
    stop_condition_slots: list[DiscoveredSlot] = []

    def _make_slot(raw: dict[str, Any]) -> DiscoveredSlot:
        ev = raw.get("evidence", []) or []
        # Derive evidence_status from per-evidence quote_verified flags.
        ev_verified = [e.get("quote_verified") for e in ev if isinstance(e, dict)]
        if ev and all(ev_verified):
            evidence_status = "verified"
        elif ev and any(ev_verified):
            evidence_status = "partially_verified"
        else:
            evidence_status = "unverified"
        return DiscoveredSlot(
            name=raw.get("name", "unknown"),
            description=raw.get("description", ""),
            necessity=raw.get("necessity", "recommended"),
            answer_source=raw.get("answer_source", "human"),
            missing_policy=raw.get("missing_policy", "ask_user"),
            support=raw.get("support", "guessed"),
            confidence=float(raw.get("confidence", 0.5)),
            evidence=ev,
            evidence_status=evidence_status,
        )

    for raw in reviewed:
        if not isinstance(raw, dict):
            continue
        ans = raw.get("answer_source", "human")
        slot = _make_slot(raw)
        if ans == "policy_default":
            safe_default_slots.append(slot)
        elif ans == "blocked":
            safety_class = (raw.get("safety_class") or "").strip().lower()
            if safety_class == "forbidden_action":
                forbidden_slots.append(slot)
            elif safety_class == "stop_condition":
                stop_condition_slots.append(slot)
            elif safety_class == "execution_constraint":
                exec_constraint_slots.append(slot)
            else:
                safety_blocks.append(slot)
        elif ans == "authorization":
            authorization_slots.append(slot)
        else:
            normal_slots.append(slot)

    # Extract skill name from doc
    skill_name = _extract_skill_name(skill_content) or skill_id_hint or "unknown_skill"
    skill_id = skill_id_hint or _name_to_id(skill_name)

    # Activation triggers from extraction
    activation = {
        "triggers": extracted.get("activation_triggers", []),
    }

    contract = DiscoveredContract(
        skill_id=skill_id,
        skill_name=skill_name,
        activation=activation,
        slots=normal_slots,
        safe_default_slots=safe_default_slots,
        safety_blocks=safety_blocks,
        authorization_requirements=authorization_slots,
        execution_constraints=exec_constraint_slots,
        forbidden_actions=forbidden_slots,
        stop_conditions=stop_condition_slots,
    )

    trace["contract"] = {
        "skill_id": contract.skill_id,
        "skill_name": contract.skill_name,
        "activation": contract.activation,
        "slot_count": len(contract.slots),
        "safe_defaults": contract.safe_defaults,
        "block_if": contract.block_if,
        "safety_blocks": [s.description for s in contract.safety_blocks],
        "authorization_requirements": [s.description for s in contract.authorization_requirements],
        "execution_constraints": [s.description for s in contract.execution_constraints],
        "forbidden_actions": [s.description for s in contract.forbidden_actions],
        "stop_conditions": [s.description for s in contract.stop_conditions],
        "slots": [
            {
                "name": s.name,
                "description": s.description,
                "necessity": s.necessity,
                "answer_source": s.answer_source,
                "missing_policy": s.missing_policy,
                "support": s.support,
                "confidence": round(s.confidence, 2),
                "evidence_status": s.evidence_status,
                "evidence": s.evidence,
            }
            for s in contract.slots
        ],
    }
    return contract, trace


def audit_skill_with_llm(
    skill_content: str,
    llm: LLMBackend,
    skill_id_hint: str | None = None,
) -> DiscoveredContract:
    """Run the four-stage LLM audit pipeline on a SKILL.md.

    Thin wrapper around :func:`audit_skill_with_llm_traced` that discards the
    trace. Use the traced variant when you need the stage-by-stage audit output
    for quality evaluation.

    Args:
        skill_content: Raw text content of the skill document.
        llm: LLM backend (MockLLM or OpenAILLM).
        skill_id_hint: Optional skill_id if known (e.g., from filename).

    Returns:
        DiscoveredContract with slots, safe defaults, and block conditions.
    """
    contract, _trace = audit_skill_with_llm_traced(skill_content, llm, skill_id_hint)
    return contract


def audit_skill_file_with_llm(
    skill_path: str | Path,
    llm: LLMBackend,
    skill_id_hint: str | None = None,
) -> DiscoveredContract:
    """Audit a SKILL.md file using LLM."""
    path = Path(skill_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {skill_path}")
    content = path.read_text(encoding="utf-8")
    if skill_id_hint is None:
        skill_id_hint = path.stem.lower() if path.stem != "SKILL" else path.parent.name.lower().replace(" ", "_")

    contract = audit_skill_with_llm(content, llm, skill_id_hint=skill_id_hint)

    # Final deterministic verification of evidence quotes against the source
    # file, applied to EVERY slot section (not just normal slots).  Safety
    # slots need an intact evidence chain the most, so verify them too.
    all_sections = [
        contract.slots, contract.safe_default_slots, contract.safety_blocks,
        contract.authorization_requirements, contract.execution_constraints,
        contract.forbidden_actions, contract.stop_conditions,
    ]
    for section in all_sections:
        raw_slots: list[dict] = [
            {"name": s.name, "evidence": list(s.evidence), "confidence": s.confidence}
            for s in section
        ]
        verified = _verify_quotes(raw_slots, content)
        for slot, vs in zip(section, verified):
            slot.confidence = float(vs.get("confidence", slot.confidence))
            # Recompute evidence_status from the verified flags.
            ev_flags = [e.get("quote_verified") for e in slot.evidence if isinstance(e, dict)]
            if slot.evidence and all(ev_flags):
                slot.evidence_status = "verified"
            elif slot.evidence and any(ev_flags):
                slot.evidence_status = "partially_verified"
            else:
                slot.evidence_status = "unverified"
            for ev, vev in zip(slot.evidence, vs.get("evidence", [])):
                ev["quote_verified"] = vev.get("quote_verified", False)
                ev["quote_line_start"] = vev.get("quote_line_start")

    return contract


# ── Helpers ──────────────────────────────────────────────────


def _verify_quotes(slots: list[dict], source_text: str) -> list[dict]:
    """Verify that evidence quotes actually appear in the source document.

    For each slot with a non-empty ``quote`` field in its ``evidence`` list,
    checks whether the quote is an exact substring of *source_text*.

    Slots with missing or non-matching quotes are flagged with
    ``quote_verified: False`` and their ``confidence`` is set to **0.0**.

    Args:
        slots: List of slot dicts (from the infer or classify stage).
        source_text: Full text of the source SKILL.md document.

    Returns:
        The same list, mutated in-place, with per-evidence ``quote_verified``
        and ``quote_line_start`` fields added, and ``confidence`` zeroed out
        for any slot whose quotes cannot be verified.
    """
    for slot in slots:
        evidence_list = slot.get("evidence", [])
        all_verified = True

        for ev in evidence_list:
            quote = ev.get("quote", "")
            if not quote or not quote.strip():
                ev["quote_verified"] = False
                ev["quote_line_start"] = None
                all_verified = False
                continue

            idx = source_text.find(quote)
            if idx == -1:
                ev["quote_verified"] = False
                ev["quote_line_start"] = None
                all_verified = False
            else:
                line_num = source_text[:idx].count("\n") + 1
                ev["quote_verified"] = True
                ev["quote_line_start"] = line_num

        # If every evidence entry failed verification (or there are none),
        # zero out the slot-level confidence.
        if not all_verified:
            slot["confidence"] = 0.0
        elif not evidence_list:
            # No evidence at all — cannot verify.
            slot["confidence"] = 0.0

    return slots


def _clean_json(raw: str) -> str:
    """Strip markdown fences and extract JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        # Remove opening fence
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines)
    return raw


def _extract_skill_name(content: str) -> str | None:
    """Extract skill name from markdown title."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            name = line[2:].strip()
            # Strip common prefixes
            for prefix in ("Skill:", "Skill ", "skill:", "skill "):
                if name.lower().startswith(prefix.lower()):
                    name = name[len(prefix):].strip()
            return name
    return None


def _name_to_id(name: str) -> str:
    return name.lower().replace(" ", "_").replace("-", "_")


# ── YAML / JSON output ───────────────────────────────────────


def contract_to_yaml(contract: DiscoveredContract) -> str:
    """Serialize a DiscoveredContract to canonical ``SkillInputContract`` YAML.

    The output is the v2 contract produced by :meth:`to_skill_input_contract`,
    i.e. exactly what ``compile --skill-file`` will load back.  This keeps the
    ``audit-skill --write SKILL.input.yaml`` → ``compile --skill-file`` loop a
    faithful roundtrip with a single canonical format.
    """
    import yaml

    canonical = contract.to_skill_input_contract()
    from .schema import validate_skill_input_contract
    validate_skill_input_contract(canonical)
    return yaml.dump(canonical, default_flow_style=False, allow_unicode=True, sort_keys=False)


def contract_to_json(contract: DiscoveredContract) -> str:
    """Serialize a DiscoveredContract to canonical ``SkillInputContract`` JSON."""
    canonical = contract.to_skill_input_contract()
    from .schema import validate_skill_input_contract
    validate_skill_input_contract(canonical)
    return json.dumps(canonical, indent=2, ensure_ascii=False)
