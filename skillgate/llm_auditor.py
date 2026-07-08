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
    evidence: list[dict[str, str]] = field(default_factory=list)  # [{quote, rationale}]


@dataclass
class DiscoveredContract:
    """Full contract discovered from a SKILL.md by the LLM auditor."""
    version: str = "skill_input_contract.v1"
    skill_id: str = ""
    skill_name: str = ""
    activation: dict[str, Any] = field(default_factory=dict)  # {triggers: [...], anti_triggers: [...]}
    slots: list[DiscoveredSlot] = field(default_factory=list)
    safe_defaults: list[str] = field(default_factory=list)
    block_if: list[str] = field(default_factory=list)

    def to_builtin_format(self) -> dict[str, Any]:
        """Convert to the format expected by capabilities.py / rules.py."""
        from .capabilities import _slot as cap_slot

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

        required = []
        ask_if_missing = []
        discover_if_missing = []
        safe_defaults = []
        blocked = []

        for s in self.slots:
            cat = category_map.get(s.answer_source, "human_askable")
            src = source_map.get(s.answer_source, "human")
            entry = cap_slot(s.name, s.description, cat,
                             support=s.support, answer_source=src,
                             missing_policy=s.missing_policy)

            if s.necessity == "required":
                required.append(entry)
            elif s.necessity == "recommended":
                ask_if_missing.append(entry)
            else:
                discover_if_missing.append(entry)

        for text in self.safe_defaults:
            safe_defaults.append(cap_slot(
                _text_to_id(text), text, "safe_assumption", support="recommended"))

        for text in self.block_if:
            blocked.append(cap_slot(
                _text_to_id(text), text, "blocked", support="recommended"))

        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "skill_version": "1.0.0",
            "skill_description": self.skill_name,
            "required_slots": required,
            "ask_if_missing": ask_if_missing,
            "discover_if_missing": discover_if_missing,
            "safe_defaults": safe_defaults,
            "block_if": blocked,
        }


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
    slots = []
    safe_defaults: list[str] = []
    block_if: list[str] = []

    for raw in reviewed:
        if not isinstance(raw, dict):
            continue
        ans = raw.get("answer_source", "human")
        if ans in ("policy_default",):
            # Policy defaults become safe_defaults, not slots
            safe_defaults.append(raw.get("description", raw.get("name", "")))
            continue
        if ans == "blocked":
            block_if.append(raw.get("description", raw.get("name", "")))
            continue

        slots.append(DiscoveredSlot(
            name=raw.get("name", "unknown"),
            description=raw.get("description", ""),
            necessity=raw.get("necessity", "recommended"),
            answer_source=ans,
            missing_policy=raw.get("missing_policy", "ask_user"),
            support=raw.get("support", "guessed"),
            confidence=float(raw.get("confidence", 0.5)),
            evidence=raw.get("evidence", []),
        ))

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
        slots=slots,
        safe_defaults=safe_defaults,
        block_if=block_if,
    )

    trace["contract"] = {
        "skill_id": contract.skill_id,
        "skill_name": contract.skill_name,
        "activation": contract.activation,
        "slot_count": len(contract.slots),
        "safe_defaults": contract.safe_defaults,
        "block_if": contract.block_if,
        "slots": [
            {
                "name": s.name,
                "description": s.description,
                "necessity": s.necessity,
                "answer_source": s.answer_source,
                "missing_policy": s.missing_policy,
                "support": s.support,
                "confidence": round(s.confidence, 2),
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
    return audit_skill_with_llm(content, llm, skill_id_hint=skill_id_hint)


# ── Helpers ──────────────────────────────────────────────────


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


# ── YAML output ──────────────────────────────────────────────


def contract_to_yaml(contract: DiscoveredContract) -> str:
    """Serialize a DiscoveredContract to human-readable YAML."""
    import yaml

    data: dict[str, Any] = {
        "version": contract.version,
        "skill_id": contract.skill_id,
        "skill_name": contract.skill_name,
        "activation": contract.activation,
        "slots": [],
        "safe_defaults": contract.safe_defaults,
        "block_if": contract.block_if,
    }
    for s in contract.slots:
        data["slots"].append({
            "name": s.name,
            "description": s.description,
            "necessity": s.necessity,
            "answer_source": s.answer_source,
            "missing_policy": s.missing_policy,
            "support": s.support,
            "confidence": round(s.confidence, 2),
            "evidence": s.evidence,
        })
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


def contract_to_json(contract: DiscoveredContract) -> str:
    """Serialize a DiscoveredContract to JSON."""
    data: dict[str, Any] = {
        "version": contract.version,
        "skill_id": contract.skill_id,
        "skill_name": contract.skill_name,
        "activation": contract.activation,
        "slots": [],
        "safe_defaults": contract.safe_defaults,
        "block_if": contract.block_if,
    }
    for s in contract.slots:
        data["slots"].append({
            "name": s.name,
            "description": s.description,
            "necessity": s.necessity,
            "answer_source": s.answer_source,
            "missing_policy": s.missing_policy,
            "support": s.support,
            "confidence": round(s.confidence, 2),
            "evidence": s.evidence,
        })
    return json.dumps(data, indent=2, ensure_ascii=False)
