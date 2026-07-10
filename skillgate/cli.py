"""SkillGate CLI.

Commands:
    audit-skill    — Audit a SKILL.md and discover its input contract.
    compile        — Compile a user request into a normalized skill input.
    inspect        — Inspect allowlisted local context.
    explain        — Explain a decision artifact.
    answer         — Record a clarification answer.
    answer-batch   — Record answers from a JSON mapping.
    recompile      — Recompile after clarification (deprecated, use apply-patch).
    apply-patch    — Apply structured slot patches to a draft.
    schemas        — Export/validate JSON Schemas.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .clarification import (
    record_clarification_answer,
    record_clarification_answers,
    recompile_from_run,
    write_clarification_packet,
)
from .compiler import compile_against_skill
from .context import discover_context
from .json_schema import validate_published_schemas, write_json_schemas
from .skill_auditor import audit_skill, audit_skill_to_yaml
from .llm_auditor import (
    MockLLM,
    OpenAILLM,
    audit_skill_file_with_llm,
    contract_to_yaml as llm_contract_to_yaml,
    contract_to_json as llm_contract_to_json,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="skillgate",
        description="Pre-activation input compiler for agent skills. "
                    "Audit skills to discover missing input contracts, "
                    "then compile vague requests into skill-ready inputs.",
    )
    parser.add_argument("--version", action="version", version=f"skillgate {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    # ── audit-skill ───────────────────────────────────────
    _register_audit_skill(subparsers)

    # ── compile ───────────────────────────────────────────
    _register_compile(subparsers)

    # ── inspect ───────────────────────────────────────────
    _register_inspect(subparsers)

    # ── explain ───────────────────────────────────────────
    _register_explain(subparsers)

    # ── answer / answer-batch / recompile ─────────────────
    _register_answer(subparsers)
    _register_answer_batch(subparsers)
    _register_recompile(subparsers)

    # ── apply-patch ──────────────────────────────────────
    _register_apply_patch(subparsers)

    # ── schemas ───────────────────────────────────────────
    _register_schemas(subparsers)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return

    dispatcher = {
        "audit-skill": _cmd_audit_skill,
        "compile": _cmd_compile,
        "inspect": _cmd_inspect,
        "explain": _cmd_explain,
        "answer": _cmd_answer,
        "answer-batch": _cmd_answer_batch,
        "recompile": _cmd_recompile,
        "apply-patch": _cmd_apply_patch,
        "schemas": _cmd_schemas,
    }
    dispatcher[args.command](args)


# ── audit-skill ───────────────────────────────────────────────


def _register_audit_skill(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("audit-skill", help="Audit a SKILL.md and discover its input contract.")
    p.add_argument("skill_path", help="Path to SKILL.md or equivalent skill description.")
    p.add_argument("--write", dest="output_path", help="Write the discovered contract as YAML to this path.")
    p.add_argument("--json", action="store_true", help="Print the contract as JSON.")
    p.add_argument("--llm", choices=["mock", "openai"], default="mock",
                   help="Use LLM-assisted four-stage audit (mock for testing, openai for real).")
    p.add_argument("--rules-only", action="store_true",
                   help="Use rules-based baseline auditor instead of LLM.")


def _cmd_audit_skill(args: argparse.Namespace) -> None:
    # ── Rules-based baseline path (opt-in) ─────────────────
    if args.rules_only:
        if args.output_path:
            contract = audit_skill_to_yaml(args.skill_path, args.output_path)
            print(f"Discovered contract for: {contract['skill_id']}")
            print(f"Written to: {args.output_path}")
            print(f"Slots: {len(contract['required_slots'])} required, "
                  f"{len(contract['ask_if_missing'])} ask-if-missing, "
                  f"{len(contract['discover_if_missing'])} discoverable, "
                  f"{len(contract['safe_defaults'])} defaults, "
                  f"{len(contract['safety_blocks'])} block conditions")
        elif args.json:
            contract = audit_skill(args.skill_path)
            print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            contract = audit_skill(args.skill_path)
            print(f"Skill: {contract['skill_name']} ({contract['skill_id']})")
            print(f"Version: {contract['skill_version']}")
            print(f"Description: {contract['skill_description']}")
            print()
            _print_slot_section("Required Slots", contract["required_slots"])
            _print_slot_section("Ask If Missing", contract["ask_if_missing"])
            _print_slot_section("Discover If Missing", contract["discover_if_missing"])
            _print_slot_section("Safe Defaults", contract["safe_defaults"])
            _print_slot_section("Block If", contract["safety_blocks"])
        return

    # ── LLM-assisted path (default) ────────────────────────
    if args.llm == "mock":
        llm = MockLLM(fixture_name=Path(args.skill_path).stem.lower())
    else:
        llm = OpenAILLM()

    contract = audit_skill_file_with_llm(args.skill_path, llm)

    if args.output_path:
        from .llm_auditor import contract_to_yaml as llm_contract_to_yaml
        yaml_str = llm_contract_to_yaml(contract)
        Path(args.output_path).write_text(yaml_str, encoding="utf-8")
        print(f"LLM-discovered contract for: {contract.skill_id}")
        print(f"Written to: {args.output_path}")
        print(f"Slots: {len(contract.slots)}, "
              f"Safe defaults: {len(contract.safe_defaults)}, "
              f"Block: {len(contract.block_if)}")
        return

    if args.json:
        print(llm_contract_to_json(contract))
        return

    print(f"Skill: {contract.skill_name} ({contract.skill_id})")
    print(f"Activation triggers: {contract.activation.get('triggers', [])}")
    print()
    for s in contract.slots:
        print(f"  [{s.answer_source}] {s.name}: {s.description[:60]}")
        print(f"    necessity={s.necessity} support={s.support} "
              f"confidence={s.confidence} policy={s.missing_policy}")
        if s.evidence:
            for ev in s.evidence[:1]:
                print(f"    evidence: \"{ev.get('quote', '')[:50]}\" → {ev.get('rationale', '')[:50]}")
    if contract.safe_defaults:
        print(f"\n  Safe defaults: {len(contract.safe_defaults)}")
        for sd in contract.safe_defaults:
            print(f"    - {sd}")
    if contract.block_if:
        print(f"\n  Block if: {len(contract.block_if)}")
        for b in contract.block_if:
            print(f"    - {b}")


def _print_slot_section(title: str, slots: list[dict]) -> None:
    if not slots:
        return
    print(f"--- {title} ---")
    for s in slots:
        print(f"  [{s['category']}] {s['id']}: {s['text']}")
    print()


# ── compile ───────────────────────────────────────────────────


def _register_compile(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("compile", help="Compile a user request into a normalized skill input.")
    p.add_argument("request", nargs="+", help="Raw user request.")
    p.add_argument("--skill", help="Target skill id (required unless --skill-file is provided).")
    p.add_argument("--skill-file", help="Path to a SKILL.md or .input.yaml file. Overrides --skill.")
    p.add_argument("--root", default=".", help="Repository root to inspect.")
    p.add_argument("--out", help="Output run directory. Defaults to .skillgate/runs/<run_id>.")
    p.add_argument("--infer-contract", action="store_true",
                   help="Use LLM to infer contract from SKILL.md if no .input.yaml exists (requires --skill-file).")
    p.add_argument("--json", action="store_true", help="Print normalized input summary as JSON.")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="Write a clarification packet when user answers are needed.")


def _cmd_compile(args: argparse.Namespace) -> None:
    raw_request = " ".join(args.request)
    root = Path(args.root)
    out_dir = Path(args.out) if args.out else None

    # New skill-targeted path
    skill_id = args.skill

    if args.skill_file:
        skill_path = Path(args.skill_file)
        discovered_contract = None

        # Priority 1: explicit .input.yaml contract or one alongside SKILL.md
        if skill_path.suffix in {".yaml", ".yml"}:
            import yaml as _yaml
            loaded = _yaml.safe_load(skill_path.read_text(encoding="utf-8"))
            if loaded and "skill_id" in loaded:
                try:
                    from .schema import validate_strict_contract
                    # Strict: rejects wrong-typed sections, then validates.
                    discovered_contract = validate_strict_contract(loaded)
                    skill_id = loaded["skill_id"]
                    print(f"Using contract from {skill_path} (normalized to v2)")
                except (ValueError, KeyError) as e:
                    # Fail-closed: an explicit .yaml that is not a valid
                    # SkillInputContract must NOT silently degrade to a
                    # natural-language SKILL.md audit.  The user passed a
                    # contract file; if it is invalid, that is an error.
                    print(f"Error: {skill_path} is not a valid SkillInputContract: {e}")
                    raise SystemExit(1)
            else:
                print(f"Error: {skill_path} has no 'skill_id' — not a valid SkillInputContract.")
                raise SystemExit(1)
        elif skill_path.suffix == ".md":
            input_yaml = skill_path.with_suffix(".input.yaml")
            if not input_yaml.exists():
                input_yaml = skill_path.with_name(skill_path.stem + ".input.yaml")
            if input_yaml.exists():
                import yaml as _yaml
                try:
                    loaded = _yaml.safe_load(input_yaml.read_text(encoding="utf-8"))
                    if loaded and "skill_id" in loaded:
                        from .schema import normalize_contract, validate_skill_input_contract
                        discovered_contract = normalize_contract(loaded)
                        validate_skill_input_contract(discovered_contract)
                        skill_id = loaded["skill_id"]
                        print(f"Using contract from {input_yaml} (normalized to v2)")
                except Exception as e:
                    print(f"Warning: {input_yaml} is not a valid SkillInputContract: {e}")

        # Priority 2: --infer-contract → LLM audit
        if skill_id is None and args.infer_contract:
            from .llm_auditor import audit_skill_file_with_llm, OpenAILLM
            try:
                llm = OpenAILLM()
                contract = audit_skill_file_with_llm(skill_path, llm)
                builtin = contract.to_builtin_format()
                # Register the discovered contract in the runtime registry.
                from .capabilities import CONTRACT_REGISTRY
                CONTRACT_REGISTRY.register(builtin["skill_id"], builtin)
                skill_id = builtin["skill_id"]
                print(f"Inferred contract via LLM: {skill_id} ({len(contract.slots)} slots)")
            except Exception as e:
                print(f"LLM contract inference failed: {e}")

        # Priority 3: rules-based audit (existing)
        if skill_id is None:
            from .baselines.rule_auditor import audit_skill
            discovered_contract = audit_skill(str(skill_path))
            skill_id = discovered_contract["skill_id"]

        if discovered_contract is not None:
            from .capabilities import CONTRACT_REGISTRY
            CONTRACT_REGISTRY.register(discovered_contract["skill_id"], discovered_contract)

    if skill_id is None:
        print("Error: --skill or --skill-file is required (auto-classification removed)")
        raise SystemExit(1)

    result = compile_against_skill(raw_request, skill_id=skill_id, root=root, out_dir=out_dir)

    if args.interactive and result["analysis"].questions:
        clarification_packet = write_clarification_packet(
            Path(result["out_dir"]),
            {"decision": result["decision"], "run_id": result["run_id"],
             "out_dir": result["out_dir"]},
        )
        if args.json:
            payload = {
                "run_id": result["run_id"],
                "out_dir": result["out_dir"],
                "skill_id": result["skill_id"],
                "decision_kind": result["decision_kind"],
                "questions": result["analysis"].questions,
                "clarifications": clarification_packet,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(f"Decision: {result['decision_kind']}")
        if result["analysis"].questions:
            print("Questions:")
            for q in result["analysis"].questions:
                print(f"  - {q}")
        print(f"Clarifications: {Path(result['out_dir']) / 'clarifications.json'}")
        return

    if args.json:
        payload = {
            "run_id": result["run_id"],
            "out_dir": result["out_dir"],
            "skill_id": result["skill_id"],
            "decision_kind": result["decision_kind"],
            "human_provided": len(result["analysis"].human_provided),
            "agent_discoverable": len(result["analysis"].agent_discoverable),
            "safe_defaults": len(result["analysis"].safe_assumptions),
            "questions": result["analysis"].questions,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"Skill: {result['skill_id']}")
    print(f"Decision: {result['decision_kind']}")
    print(f"Reason: {result['decision_kind']}")
    if result["analysis"].questions:
        print("Questions:")
        for q in result["analysis"].questions:
            print(f"  - {q}")
    print(f"Run: {result['out_dir']}")
    print(f"Normalized input: {Path(result['out_dir']) / 'normalized_skill_input.md'}")


# ── inspect ───────────────────────────────────────────────────


def _register_inspect(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("inspect", help="Inspect allowlisted local context.")
    p.add_argument("--root", default=".", help="Repository root to inspect.")
    p.add_argument("--out", help="Optional output path for context_manifest.json.")


def _cmd_inspect(args: argparse.Namespace) -> None:
    context = discover_context(Path(args.root))
    manifest = context.manifest()
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


# ── explain ───────────────────────────────────────────────────


def _register_explain(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("explain", help="Explain a decision artifact.")
    p.add_argument("decision_path", help="Path to decision.json.")
    p.add_argument("--json", action="store_true", help="Print raw decision JSON.")


def _cmd_explain(args: argparse.Namespace) -> None:
    decision = json.loads(Path(args.decision_path).read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"Decision: {decision.get('kind', 'unknown')}")
    print(f"Reason: {decision.get('reason', '')}")
    if decision.get("questions"):
        print("Questions:")
        for q in decision["questions"]:
            print(f"  - {q}")
    if decision.get("skill_id"):
        print(f"Skill: {decision['skill_id']}")


# ── answer / answer-batch / recompile ────────────────────────


def _register_answer(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("answer", help="Record a clarification answer.")
    p.add_argument("run_dir", help="Path to run directory.")
    p.add_argument("answer", nargs="+", help="Clarification answer text.")
    p.add_argument("--question-id", help="Question id to answer.")
    p.add_argument("--redact-secrets", action="store_true")
    p.add_argument("--json", action="store_true")


def _cmd_answer(args: argparse.Namespace) -> None:
    answer_text = " ".join(args.answer)
    answers = record_clarification_answer(
        Path(args.run_dir), answer_text,
        question_id=args.question_id, redact_secrets=args.redact_secrets,
    )
    if args.json:
        print(json.dumps(answers, ensure_ascii=False, indent=2))
        return
    print(f"Recorded answer for: {args.run_dir}")
    print(f"Complete: {answers['complete']}")
    if answers["complete"]:
        print(f"Next: skillgate recompile {args.run_dir}")


def _register_answer_batch(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("answer-batch", help="Record answers from a JSON mapping.")
    p.add_argument("run_dir", help="Path to run directory.")
    p.add_argument("answers_path", help="JSON mapping question_ids → answers.")
    p.add_argument("--redact-secrets", action="store_true")
    p.add_argument("--json", action="store_true")


def _cmd_answer_batch(args: argparse.Namespace) -> None:
    answers_path = Path(args.answers_path)
    answers_by_id = json.loads(answers_path.read_text(encoding="utf-8"))
    answers = record_clarification_answers(
        Path(args.run_dir), answers_by_id, redact_secrets=args.redact_secrets,
    )
    if args.json:
        print(json.dumps(answers, ensure_ascii=False, indent=2))
        return
    print(f"Recorded {len(answers['answers'])} answers for: {args.run_dir}")
    if answers["complete"]:
        print(f"Next: skillgate recompile {args.run_dir}")


def _register_recompile(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("recompile", help="Recompile after clarification answers.")
    p.add_argument("run_dir", help="Path to run directory.")
    p.add_argument("--out", help="Output child run directory.")
    p.add_argument("--json", action="store_true")


def _cmd_recompile(args: argparse.Namespace) -> None:
    out_dir = Path(args.out) if args.out else None
    result = recompile_from_run(Path(args.run_dir), out_dir=out_dir)
    decision = result["decision"]
    if args.json:
        print(json.dumps({"run_id": result["run_id"], "out_dir": result["out_dir"],
                          "decision": decision}, ensure_ascii=False, indent=2))
        return
    print(f"Decision: {decision['kind']}")
    print(f"Run: {result['out_dir']}")


# ── apply-patch ────────────────────────────────────────────────


def _register_apply_patch(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("apply-patch", help="Apply structured slot patches to a draft.")
    p.add_argument("run_dir", help="Run directory containing draft.json")
    p.add_argument("--json", help="JSON string with operations list")


def _cmd_apply_patch(args: argparse.Namespace) -> None:
    from .draft import apply_slot_patch, load_draft, save_draft

    run_dir = Path(args.run_dir)
    draft = load_draft(run_dir)
    operations = json.loads(args.json).get("operations", [])
    draft = apply_slot_patch(draft, operations)
    save_draft(run_dir, draft)
    summary = {
        "status": draft["status"],
        "slots": {
            sid: {"state": s["state"], "confirmed": s["confirmed"]}
            for sid, s in draft.get("slots", {}).items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


# ── schemas ───────────────────────────────────────────────────


def _register_schemas(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("schemas", help="Export or validate canonical JSON Schemas.")
    p.add_argument("--out", default="schemas", help="Schema directory.")
    p.add_argument("--check", action="store_true", help="Validate without rewriting.")


def _cmd_schemas(args: argparse.Namespace) -> None:
    output_dir = Path(args.out).resolve()
    if args.check:
        result = validate_published_schemas(output_dir)
        print(f"Status: {'PASS' if result['passed'] else 'FAIL'}")
        print(f"Schemas: {len(result['schemas'])}")
        if result["errors"]:
            for error in result["errors"]:
                print(f"- {error}")
            raise SystemExit(1)
        return
    paths = write_json_schemas(output_dir)
    print(f"Exported: {len(paths)}")
    for p in paths:
        print(p)
