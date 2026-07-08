"""Core test suite for SkillGate's contract-aware input compiler.

Mock LLM fixtures for 5 real skills are registered at import time.
"""

from skillgate.llm_auditor import MockLLM

# ── Register fixtures ──────────────────────────────────────────

# Fixtures registered below when this module is imported

# ── bug_fix ────────────────────────────────────────────────────
MockLLM.register_fixture("bug_fix", {
    "extracted": {
        "activation_triggers": [
            "A user reports a runtime error, crash, unexpected output, or broken behavior",
            "A test fails unexpectedly because the source code is incorrect",
        ],
        "execution_steps": [
            "Read and understand the reported symptom.",
            "Reproduce the failure using the smallest relevant command.",
            "Isolate the root cause.",
            "Propose a minimal source fix.",
            "Do not modify test expectations unless explicitly authorized.",
            "Report the fix and verification result.",
        ],
        "output_requirements": [
            "Root cause analysis with evidence from source files",
            "A minimal source change that fixes the reported behavior",
            "The relevant test command and its result",
        ],
        "forbidden_actions": [
            "Modify tests unless explicitly authorized",
            "Change public API surface",
            "Delete files",
            "Introduce new dependencies",
            "Call external services",
        ],
        "verification_statements": [
            "Run the smallest relevant test command",
            "Report verification result",
        ],
        "safety_constraints": [
            "Do not modify tests unless explicitly authorized",
            "Do not change public API surface",
            "Prefer minimal source changes over broad refactors",
            "Do not delete files",
        ],
    },
    "inferred": [
        {"name": "failure_symptom", "description": "The reported symptom: error message, traceback, blank page, timeout, or crash", "necessity": "required", "evidence": [{"quote": "Read and understand the reported symptom.", "rationale": "Agent needs to know what went wrong before it can fix it"}]},
        {"name": "allowed_change_scope", "description": "What changes are permitted: source-only, tests allowed, public API changes", "necessity": "required", "evidence": [{"quote": "Do not modify test expectations unless explicitly authorized.", "rationale": "Scope of allowed changes determines valid fix approaches"}]},
        {"name": "target_scope", "description": "Which source files or module should be investigated", "necessity": "recommended", "evidence": [{"quote": "Reproduce the failure using the smallest relevant command.", "rationale": "Agent needs scope to focus investigation effort"}]},
        {"name": "no_file_deletion", "description": "Do not delete files without explicit authorization", "necessity": "recommended", "evidence": [{"quote": "Do not delete files.", "rationale": "Explicitly forbidden action from constraints section"}]},
        {"name": "no_external_services", "description": "Do not call external services", "necessity": "recommended", "evidence": [{"quote": "Do not call external services.", "rationale": "Explicit constraint preventing side effects"}]},
        {"name": "introduce_dependencies", "description": "Do not introduce new dependencies", "necessity": "recommended", "evidence": [{"quote": "Do not introduce new dependencies.", "rationale": "Explicit constraint keeping changes minimal"}]},
        {"name": "change_public_api", "description": "Do not change public API surface", "necessity": "recommended", "evidence": [{"quote": "Do not change public API surface.", "rationale": "Explicit constraint preserving backward compatibility"}]},
    ],
    "classified": [
        {"name": "failure_symptom", "description": "The reported symptom: error message, traceback, blank page, timeout, or crash", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.92, "evidence": [{"quote": "Read and understand the reported symptom.", "rationale": "Agent needs to know what went wrong before it can fix it"}]},
        {"name": "allowed_change_scope", "description": "What changes are permitted: source-only, tests allowed, public API changes", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.88, "evidence": [{"quote": "Do not modify test expectations unless explicitly authorized.", "rationale": "Scope of allowed changes determines valid fix approaches"}]},
        {"name": "target_scope", "description": "Which source files or module should be investigated", "necessity": "recommended", "answer_source": "human_or_agent", "missing_policy": "discover_then_ask", "support": "inferred", "confidence": 0.82, "evidence": [{"quote": "Reproduce the failure using the smallest relevant command.", "rationale": "Agent needs scope to focus investigation effort"}]},
        {"name": "no_file_deletion", "description": "Do not delete files without explicit authorization", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not delete files.", "rationale": "Explicitly forbidden action from constraints section"}]},
        {"name": "no_external_services", "description": "Do not call external services", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not call external services.", "rationale": "Explicit constraint preventing side effects"}]},
        {"name": "introduce_dependencies", "description": "Do not introduce new dependencies", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not introduce new dependencies.", "rationale": "Explicit constraint keeping changes minimal"}]},
        {"name": "change_public_api", "description": "Do not change public API surface", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not change public API surface.", "rationale": "Explicit constraint preserving backward compatibility"}]},
    ],
    "reviewed": [
        {"name": "failure_symptom", "description": "The reported symptom: error message, traceback, blank page, timeout, or crash", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.92, "evidence": [{"quote": "Read and understand the reported symptom.", "rationale": "Agent needs to know what went wrong before it can fix it"}]},
        {"name": "allowed_change_scope", "description": "What changes are permitted: source-only, tests allowed, public API changes", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.88, "evidence": [{"quote": "Do not modify test expectations unless explicitly authorized.", "rationale": "Scope of allowed changes determines valid fix approaches"}]},
        {"name": "target_scope", "description": "Which source files or module should be investigated", "necessity": "recommended", "answer_source": "human_or_agent", "missing_policy": "discover_then_ask", "support": "inferred", "confidence": 0.82, "evidence": [{"quote": "Reproduce the failure using the smallest relevant command.", "rationale": "Agent needs scope to focus investigation effort"}]},
        {"name": "no_file_deletion", "description": "Do not delete files without explicit authorization", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not delete files.", "rationale": "Explicitly forbidden action from constraints section"}]},
        {"name": "no_external_services", "description": "Do not call external services", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not call external services.", "rationale": "Explicit constraint preventing side effects"}]},
        {"name": "introduce_dependencies", "description": "Do not introduce new dependencies", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not introduce new dependencies.", "rationale": "Explicit constraint keeping changes minimal"}]},
        {"name": "change_public_api", "description": "Do not change public API surface", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not change public API surface.", "rationale": "Explicit constraint preserving backward compatibility"}]},
    ],
})

# ── code_review ─────────────────────────────────────────────────
MockLLM.register_fixture("code_review", {
    "extracted": {
        "activation_triggers": [
            "A user asks to review code changes, a PR, or a diff",
            "A user requests a security audit or correctness check on specific files",
        ],
        "execution_steps": [
            "Identify the review target (files, diff, PR).",
            "Read the changed or specified code.",
            "Analyze for the requested focus: correctness, security, performance, maintainability, or API risk.",
            "Report only findings that meet the severity bar.",
            "Cite specific file paths and line ranges as evidence.",
        ],
        "output_requirements": [
            "A structured review report",
            "Each finding: severity, file, line range, description, evidence",
            "Optional: suggested fixes (reported separately, not applied)",
        ],
        "forbidden_actions": [
            "Modify any files",
            "Speculate without evidence",
            "Print full secret values",
            "Push, deploy, or call external services",
        ],
        "verification_statements": [
            "Review is read-only",
            "Only report evidence-backed findings",
            "Redact secret-like values in evidence",
        ],
        "safety_constraints": [
            "Review is read-only. Do not modify any files.",
            "Only report evidence-backed findings. Do not speculate.",
            "Redact secret-like values in evidence.",
            "Do not print full secret values.",
            "Do not push, deploy, or call external services.",
        ],
    },
    "inferred": [
        {"name": "review_target", "description": "Which files, diff, or PR should be reviewed", "necessity": "required", "evidence": [{"quote": "Identify the review target (files, diff, PR).", "rationale": "Agent needs to know what to review"}]},
        {"name": "review_focus", "description": "Review focus: correctness, security, performance, maintainability, or API risk", "necessity": "required", "evidence": [{"quote": "Analyze for the requested focus: correctness, security, performance, maintainability, or API risk.", "rationale": "Focus determines what findings to surface"}]},
        {"name": "severity_bar", "description": "Minimum severity of findings that should be reported", "necessity": "recommended", "evidence": [{"quote": "Report only findings that meet the severity bar.", "rationale": "Severity threshold controls review scope and noise"}]},
        {"name": "read_only", "description": "Review is read-only; do not modify any files", "necessity": "recommended", "evidence": [{"quote": "Review is read-only. Do not modify any files.", "rationale": "Explicit constraint from the first constraint bullet"}]},
        {"name": "no_secret_printing", "description": "Do not print full secret values", "necessity": "recommended", "evidence": [{"quote": "Do not print full secret values.", "rationale": "Explicit safety constraint preventing credential exposure"}]},
    ],
    "classified": [
        {"name": "review_target", "description": "Which files, diff, or PR should be reviewed", "necessity": "required", "answer_source": "human_or_agent", "missing_policy": "discover_then_ask", "support": "inferred", "confidence": 0.85, "evidence": [{"quote": "Identify the review target (files, diff, PR).", "rationale": "Agent needs to know what to review"}]},
        {"name": "review_focus", "description": "Review focus: correctness, security, performance, maintainability, or API risk", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.90, "evidence": [{"quote": "Analyze for the requested focus: correctness, security, performance, maintainability, or API risk.", "rationale": "Focus determines what findings to surface"}]},
        {"name": "severity_bar", "description": "Minimum severity of findings that should be reported", "necessity": "recommended", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.78, "evidence": [{"quote": "Report only findings that meet the severity bar.", "rationale": "Severity threshold controls review scope and noise"}]},
        {"name": "read_only", "description": "Review is read-only; do not modify any files", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Review is read-only. Do not modify any files.", "rationale": "Explicit constraint from the first constraint bullet"}]},
        {"name": "no_secret_printing", "description": "Do not print full secret values", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not print full secret values.", "rationale": "Explicit safety constraint preventing credential exposure"}]},
    ],
    "reviewed": [
        {"name": "review_target", "description": "Which files, diff, or PR should be reviewed", "necessity": "required", "answer_source": "human_or_agent", "missing_policy": "discover_then_ask", "support": "inferred", "confidence": 0.85, "evidence": [{"quote": "Identify the review target (files, diff, PR).", "rationale": "Agent needs to know what to review"}]},
        {"name": "review_focus", "description": "Review focus: correctness, security, performance, maintainability, or API risk", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.90, "evidence": [{"quote": "Analyze for the requested focus: correctness, security, performance, maintainability, or API risk.", "rationale": "Focus determines what findings to surface"}]},
        {"name": "severity_bar", "description": "Minimum severity of findings that should be reported", "necessity": "recommended", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.78, "evidence": [{"quote": "Report only findings that meet the severity bar.", "rationale": "Severity threshold controls review scope and noise"}]},
        {"name": "read_only", "description": "Review is read-only; do not modify any files", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Review is read-only. Do not modify any files.", "rationale": "Explicit constraint from the first constraint bullet"}]},
        {"name": "no_secret_printing", "description": "Do not print full secret values", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not print full secret values.", "rationale": "Explicit safety constraint preventing credential exposure"}]},
    ],
})

# ── refactor ────────────────────────────────────────────────────
MockLLM.register_fixture("refactor", {
    "extracted": {
        "activation_triggers": [
            "A user asks to restructure, rename, or simplify code",
            "A user requests deduplication or code reorganization",
            "A user asks to improve naming or modularity",
        ],
        "execution_steps": [
            "Identify the refactoring target and goal.",
            "Read the current code and its test coverage.",
            "Identify related tests.",
            "Apply structural changes.",
            "Run the smallest relevant test command.",
            "Verify that behavior is preserved.",
        ],
        "output_requirements": [
            "Summary of changes made",
            "Before/after structure comparison",
            "Test results confirming behavior preservation",
        ],
        "forbidden_actions": [
            "Change behavior",
            "Change public API without explicit authorization",
            "Batch move or rename files without authorization",
            "Delete files",
            "Perform broad refactors unless explicitly requested",
        ],
        "verification_statements": [
            "Run tests to verify behavior preservation before declaring completion.",
            "Verify that behavior is preserved.",
        ],
        "safety_constraints": [
            "Behavior must be strictly preserved.",
            "Do not change public API without explicit authorization.",
            "Do not batch move or rename files without authorization.",
            "Keep changes localized. Do not perform broad refactors unless explicitly requested.",
            "Do not delete files.",
        ],
    },
    "inferred": [
        {"name": "target_scope", "description": "What code should be refactored", "necessity": "required", "evidence": [{"quote": "Identify the refactoring target and goal.", "rationale": "Agent needs to know what to refactor"}]},
        {"name": "refactor_goal", "description": "What improvement: naming, structure, deduplication, performance", "necessity": "required", "evidence": [{"quote": "Identify the refactoring target and goal.", "rationale": "Goal drives the refactoring approach"}]},
        {"name": "behavior_preservation", "description": "Must behavior be strictly preserved", "necessity": "recommended", "evidence": [{"quote": "Behavior must be strictly preserved.", "rationale": "Core constraint of the refactor skill"}]},
        {"name": "no_file_deletion", "description": "Do not delete files without explicit authorization", "necessity": "recommended", "evidence": [{"quote": "Do not delete files.", "rationale": "Explicit constraint from safety section"}]},
        {"name": "no_broad_refactors", "description": "Keep changes localized; do not perform broad refactors unless requested", "necessity": "recommended", "evidence": [{"quote": "Keep changes localized. Do not perform broad refactors unless explicitly requested.", "rationale": "Scope constraint"}]},
        {"name": "batch_file_moves", "description": "Do not batch move or rename files without authorization", "necessity": "recommended", "evidence": [{"quote": "Do not batch move or rename files without authorization.", "rationale": "Explicitly requires authorization"}]},
    ],
    "classified": [
        {"name": "target_scope", "description": "What code should be refactored", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.90, "evidence": [{"quote": "Identify the refactoring target and goal.", "rationale": "Agent needs to know what to refactor"}]},
        {"name": "refactor_goal", "description": "What improvement: naming, structure, deduplication, performance", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.88, "evidence": [{"quote": "Identify the refactoring target and goal.", "rationale": "Goal drives the refactoring approach"}]},
        {"name": "behavior_preservation", "description": "Must behavior be strictly preserved", "necessity": "recommended", "answer_source": "human", "missing_policy": "ask_user", "support": "explicit", "confidence": 0.85, "evidence": [{"quote": "Behavior must be strictly preserved.", "rationale": "Core constraint of the refactor skill"}]},
        {"name": "no_file_deletion", "description": "Do not delete files without explicit authorization", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not delete files.", "rationale": "Explicit constraint from safety section"}]},
        {"name": "no_broad_refactors", "description": "Keep changes localized; do not perform broad refactors unless requested", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.92, "evidence": [{"quote": "Keep changes localized. Do not perform broad refactors unless explicitly requested.", "rationale": "Scope constraint"}]},
        {"name": "batch_file_moves", "description": "Do not batch move or rename files without authorization", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.90, "evidence": [{"quote": "Do not batch move or rename files without authorization.", "rationale": "Explicitly requires authorization"}]},
    ],
    "reviewed": [
        {"name": "target_scope", "description": "What code should be refactored", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.90, "evidence": [{"quote": "Identify the refactoring target and goal.", "rationale": "Agent needs to know what to refactor"}]},
        {"name": "refactor_goal", "description": "What improvement: naming, structure, deduplication, performance", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.88, "evidence": [{"quote": "Identify the refactoring target and goal.", "rationale": "Goal drives the refactoring approach"}]},
        {"name": "behavior_preservation", "description": "Must behavior be strictly preserved", "necessity": "recommended", "answer_source": "human", "missing_policy": "ask_user", "support": "explicit", "confidence": 0.85, "evidence": [{"quote": "Behavior must be strictly preserved.", "rationale": "Core constraint of the refactor skill"}]},
        {"name": "no_file_deletion", "description": "Do not delete files without explicit authorization", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Do not delete files.", "rationale": "Explicit constraint from safety section"}]},
        {"name": "no_broad_refactors", "description": "Keep changes localized; do not perform broad refactors unless requested", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.92, "evidence": [{"quote": "Keep changes localized. Do not perform broad refactors unless explicitly requested.", "rationale": "Scope constraint"}]},
        {"name": "batch_file_moves", "description": "Do not batch move or rename files without authorization", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.90, "evidence": [{"quote": "Do not batch move or rename files without authorization.", "rationale": "Explicitly requires authorization"}]},
    ],
})

# ── documentation_update ────────────────────────────────────────
MockLLM.register_fixture("documentation_update", {
    "extracted": {
        "activation_triggers": [
            "A user asks to update README, installation docs, contribution guides, or API docs",
            "A user requests new documentation sections with specific content descriptions",
        ],
        "execution_steps": [
            "Identify the target document and the intended audience.",
            "Read the existing document and related project files.",
            "Discover project commands, package manager, and conventions from local config.",
            "Write or update the documentation section.",
            "Ground every factual claim in repo evidence.",
        ],
        "output_requirements": [
            "Updated documentation file",
            "A list of claims with evidence sources",
        ],
        "forbidden_actions": [
            "Invent metrics, adoption numbers, or benchmark results",
            "Fabricate features or capabilities the project does not have",
            "Write promotional language without explicit user request",
        ],
        "verification_statements": [
            "Ground every factual claim in repo evidence.",
        ],
        "safety_constraints": [
            "Only use facts grounded in repo files (README, pyproject, config, source).",
            "Do not invent metrics, adoption numbers, or benchmark results.",
            "Do not fabricate features or capabilities the project does not have.",
            "Do not write promotional language without explicit user request.",
        ],
    },
    "inferred": [
        {"name": "target_document", "description": "Which document should be updated", "necessity": "required", "evidence": [{"quote": "Identify the target document and the intended audience.", "rationale": "Agent needs to know which document to update"}]},
        {"name": "audience", "description": "Who is the target audience: developers, users, contributors", "necessity": "required", "evidence": [{"quote": "Identify the target document and the intended audience.", "rationale": "Audience shapes the content and tone of the documentation"}]},
        {"name": "factual_only", "description": "Only use facts grounded in repo files", "necessity": "recommended", "evidence": [{"quote": "Only use facts grounded in repo files (README, pyproject, config, source).", "rationale": "Core constraint of documentation skill"}]},
        {"name": "no_fabrication", "description": "Do not invent metrics, adoption numbers, or benchmark results", "necessity": "recommended", "evidence": [{"quote": "Do not invent metrics, adoption numbers, or benchmark results.", "rationale": "Explicit safety constraint"}]},
        {"name": "fabricated_claims", "description": "Fabricating unsupported project claims", "necessity": "recommended", "evidence": [{"quote": "Do not fabricate features or capabilities the project does not have.", "rationale": "Explicitly forbidden"}]},
    ],
    "classified": [
        {"name": "target_document", "description": "Which document should be updated", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.90, "evidence": [{"quote": "Identify the target document and the intended audience.", "rationale": "Agent needs to know which document to update"}]},
        {"name": "audience", "description": "Who is the target audience: developers, users, contributors", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.88, "evidence": [{"quote": "Identify the target document and the intended audience.", "rationale": "Audience shapes the content and tone of the documentation"}]},
        {"name": "factual_only", "description": "Only use facts grounded in repo files", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Only use facts grounded in repo files (README, pyproject, config, source).", "rationale": "Core constraint of documentation skill"}]},
        {"name": "no_fabrication", "description": "Do not invent metrics, adoption numbers, or benchmark results", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not invent metrics, adoption numbers, or benchmark results.", "rationale": "Explicit safety constraint"}]},
        {"name": "fabricated_claims", "description": "Fabricating unsupported project claims", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.94, "evidence": [{"quote": "Do not fabricate features or capabilities the project does not have.", "rationale": "Explicitly forbidden"}]},
    ],
    "reviewed": [
        {"name": "target_document", "description": "Which document should be updated", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.90, "evidence": [{"quote": "Identify the target document and the intended audience.", "rationale": "Agent needs to know which document to update"}]},
        {"name": "audience", "description": "Who is the target audience: developers, users, contributors", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.88, "evidence": [{"quote": "Identify the target document and the intended audience.", "rationale": "Audience shapes the content and tone of the documentation"}]},
        {"name": "factual_only", "description": "Only use facts grounded in repo files", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.95, "evidence": [{"quote": "Only use facts grounded in repo files (README, pyproject, config, source).", "rationale": "Core constraint of documentation skill"}]},
        {"name": "no_fabrication", "description": "Do not invent metrics, adoption numbers, or benchmark results", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not invent metrics, adoption numbers, or benchmark results.", "rationale": "Explicit safety constraint"}]},
        {"name": "fabricated_claims", "description": "Fabricating unsupported project claims", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.94, "evidence": [{"quote": "Do not fabricate features or capabilities the project does not have.", "rationale": "Explicitly forbidden"}]},
    ],
})

# ── experiment_debug ────────────────────────────────────────────
MockLLM.register_fixture("experiment_debug", {
    "extracted": {
        "activation_triggers": [
            "An experiment run produces unexpected results, crashes, or non-deterministic output",
            "A user asks to debug a research pipeline, benchmark run, or evaluation script",
        ],
        "execution_steps": [
            "Read the experiment log or error output.",
            "Identify the failing component: data loading, model inference, scoring, or output serialization.",
            "Reproduce the failure with the smallest relevant input.",
            "Isolate the root cause.",
            "Propose a minimal fix.",
        ],
        "output_requirements": [
            "Root cause analysis with log evidence",
            "A minimal fix that preserves the original experiment intent",
            "The smallest reproduction command and its result after fix",
        ],
        "forbidden_actions": [
            "Change the experiment protocol or evaluation metric",
            "Modify benchmark scores or expected outputs without explicit authorization",
            "Alter the experiment's random seed or data split",
            "Delete experiment results or logs",
            "Introduce new dependencies",
        ],
        "verification_statements": [
            "Reproduce the failure with the smallest relevant input.",
            "Report root cause analysis with log evidence.",
        ],
        "safety_constraints": [
            "Do not change the experiment protocol or evaluation metric.",
            "Do not modify benchmark scores or expected outputs without explicit authorization.",
            "Do not alter the experiment's random seed or data split.",
            "Do not delete experiment results or logs.",
        ],
    },
    "inferred": [
        {"name": "failure_symptom", "description": "The experiment failure symptom: error message, unexpected results, crash, non-deterministic output", "necessity": "required", "evidence": [{"quote": "Read the experiment log or error output.", "rationale": "Agent needs to know what went wrong before debugging"}]},
        {"name": "failing_component", "description": "Which component failed: data loading, model inference, scoring, or output serialization", "necessity": "recommended", "evidence": [{"quote": "Identify the failing component: data loading, model inference, scoring, or output serialization.", "rationale": "Narrowing the component speeds up debugging"}]},
        {"name": "preserve_protocol", "description": "Do not change the experiment protocol or evaluation metric", "necessity": "recommended", "evidence": [{"quote": "Do not change the experiment protocol or evaluation metric.", "rationale": "Explicit constraint preserving experiment integrity"}]},
        {"name": "no_seed_change", "description": "Do not alter the experiment's random seed or data split", "necessity": "recommended", "evidence": [{"quote": "Do not alter the experiment's random seed or data split.", "rationale": "Explicit constraint maintaining reproducibility"}]},
        {"name": "no_dep_change", "description": "Do not introduce new dependencies", "necessity": "recommended", "evidence": [{"quote": "Do not introduce new dependencies.", "rationale": "Explicit constraint keeping the fix minimal"}]},
        {"name": "preserve_results", "description": "Do not delete experiment results or logs", "necessity": "recommended", "evidence": [{"quote": "Do not delete experiment results or logs.", "rationale": "Explicit constraint preserving audit trail"}]},
    ],
    "classified": [
        {"name": "failure_symptom", "description": "The experiment failure symptom: error message, unexpected results, crash, non-deterministic output", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.91, "evidence": [{"quote": "Read the experiment log or error output.", "rationale": "Agent needs to know what went wrong before debugging"}]},
        {"name": "failing_component", "description": "Which component failed: data loading, model inference, scoring, or output serialization", "necessity": "recommended", "answer_source": "human_or_agent", "missing_policy": "discover_then_ask", "support": "inferred", "confidence": 0.80, "evidence": [{"quote": "Identify the failing component: data loading, model inference, scoring, or output serialization.", "rationale": "Narrowing the component speeds up debugging"}]},
        {"name": "preserve_protocol", "description": "Do not change the experiment protocol or evaluation metric", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.94, "evidence": [{"quote": "Do not change the experiment protocol or evaluation metric.", "rationale": "Explicit constraint preserving experiment integrity"}]},
        {"name": "no_seed_change", "description": "Do not alter the experiment's random seed or data split", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.92, "evidence": [{"quote": "Do not alter the experiment's random seed or data split.", "rationale": "Explicit constraint maintaining reproducibility"}]},
        {"name": "no_dep_change", "description": "Do not introduce new dependencies", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not introduce new dependencies.", "rationale": "Explicit constraint keeping the fix minimal"}]},
        {"name": "preserve_results", "description": "Do not delete experiment results or logs", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.91, "evidence": [{"quote": "Do not delete experiment results or logs.", "rationale": "Explicit constraint preserving audit trail"}]},
    ],
    "reviewed": [
        {"name": "failure_symptom", "description": "The experiment failure symptom: error message, unexpected results, crash, non-deterministic output", "necessity": "required", "answer_source": "human", "missing_policy": "ask_user", "support": "inferred", "confidence": 0.91, "evidence": [{"quote": "Read the experiment log or error output.", "rationale": "Agent needs to know what went wrong before debugging"}]},
        {"name": "failing_component", "description": "Which component failed: data loading, model inference, scoring, or output serialization", "necessity": "recommended", "answer_source": "human_or_agent", "missing_policy": "discover_then_ask", "support": "inferred", "confidence": 0.80, "evidence": [{"quote": "Identify the failing component: data loading, model inference, scoring, or output serialization.", "rationale": "Narrowing the component speeds up debugging"}]},
        {"name": "preserve_protocol", "description": "Do not change the experiment protocol or evaluation metric", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.94, "evidence": [{"quote": "Do not change the experiment protocol or evaluation metric.", "rationale": "Explicit constraint preserving experiment integrity"}]},
        {"name": "no_seed_change", "description": "Do not alter the experiment's random seed or data split", "necessity": "recommended", "answer_source": "policy_default", "missing_policy": "assume_default", "support": "explicit", "confidence": 0.92, "evidence": [{"quote": "Do not alter the experiment's random seed or data split.", "rationale": "Explicit constraint maintaining reproducibility"}]},
        {"name": "no_dep_change", "description": "Do not introduce new dependencies", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.93, "evidence": [{"quote": "Do not introduce new dependencies.", "rationale": "Explicit constraint keeping the fix minimal"}]},
        {"name": "preserve_results", "description": "Do not delete experiment results or logs", "necessity": "recommended", "answer_source": "blocked", "missing_policy": "block", "support": "explicit", "confidence": 0.91, "evidence": [{"quote": "Do not delete experiment results or logs.", "rationale": "Explicit constraint preserving audit trail"}]},
    ],
})