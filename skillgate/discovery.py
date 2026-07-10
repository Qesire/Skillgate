"""Pre-activation discovery protocol.

Resolves ``agent_discoverable`` slots BEFORE the target skill is activated,
reducing post-activation exploration.  SkillGate generates a discovery plan,
executes it via deterministic local resolvers (or a host-provided resolver),
and merges the results into the draft.

The discovery protocol is READ-ONLY — it inspects the filesystem but never
writes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SKILL_DISCOVERY_PLAN_VERSION = "skillgate.discovery_plan.v1"
SKILL_DISCOVERY_RESULT_VERSION = "skillgate.discovery_result.v1"

_DISCOVERY_STRATEGIES = {
    "discover_then_confirm",
    "discover_then_ask",
    "infer_then_confirm",
}


# ── Plan / Result builders ─────────────────────────────────────


def build_discovery_plan(draft: dict[str, Any]) -> dict[str, Any]:
    """Build a discovery plan for unresolved discoverable/inferable slots."""
    requests: list[dict[str, Any]] = []
    for sid, slot in draft.get("slots", {}).items():
        if slot.get("state") != "unresolved":
            continue
        acquisition = slot.get("acquisition") or {}
        strategy = acquisition.get("strategy", "")
        if strategy not in _DISCOVERY_STRATEGIES:
            continue
        resolver = acquisition.get("resolver")
        hints = _hints_for_resolver(resolver)
        requests.append({
            "slot_id": sid,
            "resolver": resolver,
            "access": "read_only",
            "hints": hints,
        })
    return {
        "schema_version": SKILL_DISCOVERY_PLAN_VERSION,
        "run_id": draft.get("run_id", ""),
        "requests": requests,
    }


def _hints_for_resolver(resolver: str | None) -> list[str]:
    """Return filesystem hints for a known resolver."""
    if resolver == "project_test_command":
        return ["pyproject.toml", "pytest.ini", "setup.cfg", "package.json", "Makefile"]
    if resolver == "repository_path_search":
        return ["src/", "lib/", "tests/"]
    if resolver == "project_config":
        return ["pyproject.toml", "package.json", "Cargo.toml", "go.mod"]
    if resolver == "file_search":
        return []
    return []


# ── Run discovery ──────────────────────────────────────────────


def run_discovery(draft: dict[str, Any], root: Path) -> dict[str, Any]:
    """Execute the discovery plan against the local filesystem.

    Returns a ``DiscoveryResult`` dict whose ``results`` map slot_id to
    ``{status, value, evidence_ids, evidence}``.
    """
    root = Path(root)
    plan = build_discovery_plan(draft)
    results: dict[str, Any] = {}

    for req in plan["requests"]:
        sid = req["slot_id"]
        resolver_name = req.get("resolver")
        hints = req.get("hints", [])
        resolver_fn = RESOLVERS.get(resolver_name)
        if resolver_fn is None:
            results[sid] = {"status": "unresolved"}
            continue
        try:
            raw = resolver_fn(root, hints)
        except Exception:
            raw = {"status": "unresolved"}
        results[sid] = _normalize_resolver_result(sid, raw)

    return {
        "schema_version": SKILL_DISCOVERY_RESULT_VERSION,
        "run_id": draft.get("run_id", ""),
        "results": results,
    }


def _normalize_resolver_result(slot_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a resolver return dict into the standard result shape."""
    status = raw.get("status", "unresolved")
    evidence_ids: list[str] = []
    evidence: list[dict[str, Any]] = []

    for idx, ev in enumerate(raw.get("evidence") or []):
        eid = f"disc-{slot_id}-{idx:03d}"
        evidence_ids.append(eid)
        evidence.append({"id": eid, **ev})

    return {
        "status": status,
        "value": raw.get("value"),
        "candidates": raw.get("candidates"),
        "evidence_ids": evidence_ids,
        "evidence": evidence,
    }


# ── Deterministic local resolvers ──────────────────────────────


def resolve_project_test_command(root: Path, hints: list[str]) -> dict[str, Any]:
    """Detect the project's test command from config files."""
    root = Path(root)

    # pyproject.toml — [tool.pytest.ini_options] or [tool.poetry.scripts]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if "[tool.pytest" in text or "pytest" in text.lower():
            return {
                "status": "resolved",
                "value": "pytest",
                "evidence": [{"path": "pyproject.toml", "text": "pytest configuration found"}],
            }

    # pytest.ini
    pytest_ini = root / "pytest.ini"
    if pytest_ini.is_file():
        return {
            "status": "resolved",
            "value": "pytest",
            "evidence": [{"path": "pytest.ini", "text": "pytest.ini present"}],
        }

    # setup.cfg with pytest config
    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        text = setup_cfg.read_text(encoding="utf-8")
        if "[tool:pytest]" in text or "pytest" in text.lower():
            return {
                "status": "resolved",
                "value": "pytest",
                "evidence": [{"path": "setup.cfg", "text": "pytest configuration found"}],
            }

    # package.json — scripts.test
    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            test_script = (pkg.get("scripts") or {}).get("test")
            if test_script:
                return {
                    "status": "resolved",
                    "value": "npm test",
                    "evidence": [{"path": "package.json", "text": f"scripts.test = {test_script}"}],
                }
        except (json.JSONDecodeError, KeyError):
            pass

    # Makefile with test target
    makefile = root / "Makefile"
    if makefile.is_file():
        text = makefile.read_text(encoding="utf-8")
        if re.search(r"^test\s*:", text, re.MULTILINE):
            return {
                "status": "resolved",
                "value": "make test",
                "evidence": [{"path": "Makefile", "text": "test target found"}],
            }

    return {"status": "unresolved"}


def resolve_repository_path_search(root: Path, hints: list[str]) -> dict[str, Any]:
    """Search for source files matching common patterns.

    Returns ``resolved`` if exactly one path matches, ``conflict`` if
    multiple distinct paths match, and ``unresolved`` if none.
    """
    root = Path(root)
    candidates: list[str] = []

    # Search common source directories
    search_dirs = [root / d for d in ("src", "lib", "app")] + [root]
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for pattern in ("*.py", "*.js", "*.ts", "*.go", "*.rs", "*.java"):
            for match in search_dir.glob(pattern):
                if match.is_file():
                    rel = str(match.relative_to(root))
                    candidates.append(rel)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    if len(unique) == 0:
        return {"status": "unresolved"}
    if len(unique) == 1:
        return {
            "status": "resolved",
            "value": unique[0],
            "evidence": [{"path": unique[0], "text": "source file found"}],
        }
    # Multiple matches → conflict
    return {
        "status": "conflict",
        "candidates": unique[:10],  # cap to avoid huge lists
        "evidence": [{"path": c, "text": "candidate source file"} for c in unique[:10]],
    }


def resolve_project_config(root: Path, hints: list[str]) -> dict[str, Any]:
    """Read project config files and extract relevant config values."""
    root = Path(root)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        # Extract project name
        name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', text)
        if name_match:
            return {
                "status": "resolved",
                "value": name_match.group(1),
                "evidence": [{"path": "pyproject.toml", "text": f"project name: {name_match.group(1)}"}],
            }

    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            name = pkg.get("name")
            if name:
                return {
                    "status": "resolved",
                    "value": name,
                    "evidence": [{"path": "package.json", "text": f"project name: {name}"}],
                }
        except (json.JSONDecodeError, KeyError):
            pass

    return {"status": "unresolved"}


def resolve_file_search(root: Path, hints: list[str]) -> dict[str, Any]:
    """Generic file glob search using hints as glob patterns."""
    root = Path(root)
    candidates: list[str] = []

    for hint in hints:
        for match in root.glob(hint):
            if match.is_file():
                rel = str(match.relative_to(root))
                candidates.append(rel)

    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    if len(unique) == 0:
        return {"status": "unresolved"}
    if len(unique) == 1:
        return {
            "status": "resolved",
            "value": unique[0],
            "evidence": [{"path": unique[0], "text": "file matched"}],
        }
    return {
        "status": "conflict",
        "candidates": unique[:10],
        "evidence": [{"path": c, "text": "file matched"} for c in unique[:10]],
    }


RESOLVERS: dict[str, Any] = {
    "project_test_command": resolve_project_test_command,
    "repository_path_search": resolve_repository_path_search,
    "project_config": resolve_project_config,
    "file_search": resolve_file_search,
}


# ── Host resolver protocol ─────────────────────────────────────


class HostResolverProtocol:
    """Protocol for host-provided discovery resolvers.

    A host agent (e.g., a coding agent) can implement this to provide
    discovery capabilities beyond the deterministic local resolvers.
    SkillGate calls the host resolver; the host returns results.
    """

    def resolve(self, slot_id: str, resolver: str, hints: list[str], root: Path) -> dict[str, Any]:
        """Return {status: resolved|conflict|unresolved, value: ..., evidence: [...]}."""
        raise NotImplementedError


# ── Apply discovery to draft ───────────────────────────────────


def apply_discovery_to_draft(draft: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Apply a discovery result to the draft and recompute status.

    Delegates to ``draft.apply_discovery_result`` for the actual slot
    updates, then recomputes the draft status.
    """
    from .draft import apply_discovery_result

    results = result.get("results", result)
    draft = apply_discovery_result(draft, results)
    return draft
