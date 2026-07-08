from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWLIST_NAMES = [
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
]

ALLOWLIST_GLOBS = [
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "**/SKILL.md",
]

CONFIG_FILE_NAME = ".skillgate.toml"

DENYLIST_GLOBS = [
    ".env",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    ".npmrc",
    ".pypirc",
    "credentials*",
    "secrets*",
    "id_rsa",
    "id_ed25519",
]

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "__pycache__",
}

SECRET_PATTERNS = [
    re.compile(r"AWS_ACCESS_KEY_ID\s*=\s*[A-Z0-9]{16,}", re.IGNORECASE),
    re.compile(r"SECRET_ACCESS_KEY\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxoxb-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|client[_ -]?secret)\s*(?:is|[:=])\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~-]{16,}"),
    re.compile(r"(?i)\b(password|token|secret)\s*[:=]\s*\S+"),
]

MAX_FILE_BYTES = 20_000

_INLINE_CODE = r"`([^`\r\n]{1,160})`"
_VERIFICATION_LINE = re.compile(
    rf"(?:run|verify(?: with| using)?|test(?: with| using)?)\s+{_INLINE_CODE}",
    re.IGNORECASE,
)
_ENTRY_POINT_LINE = re.compile(
    rf"(?:documented\s+)?public\s+(?:entry\s*point|function|api)(?:\s+is|\s*:)?\s*{_INLINE_CODE}",
    re.IGNORECASE,
)
_TARGET_SOURCE_LINE = re.compile(
    rf"(?:target|implementation)\s+(?:source|file)(?:\s+is|\s*:)?\s*{_INLINE_CODE}",
    re.IGNORECASE,
)
_SAFE_VERIFICATION_COMMANDS = [
    re.compile(r"python(?:3)? -m unittest(?: [A-Za-z0-9_./*=-]+)*$"),
    re.compile(r"python(?:3)? -m pytest(?: [A-Za-z0-9_./*:=\[\]-]+)*$"),
    re.compile(r"pytest(?: [A-Za-z0-9_./*:=\[\]-]+)*$"),
    re.compile(r"cargo test(?: [A-Za-z0-9_./*:=\[\]-]+)*$"),
    re.compile(r"go test(?: [A-Za-z0-9_./*=-]+)*$"),
    re.compile(r"npm (?:run )?(?:test|build|lint|typecheck)(?: -- [A-Za-z0-9_./*=-]+)*$"),
]


@dataclass(frozen=True)
class ContextConfig:
    path: str
    read: bool
    include_defaults: bool
    max_file_bytes: int
    allowlist_paths: list[str]
    allowlist_globs: list[str]
    denylist_globs: list[str]
    exclude_dirs: list[str]
    errors: list[str]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "read": self.read,
            "include_defaults": self.include_defaults,
            "max_file_bytes": self.max_file_bytes,
            "allowlist_paths": self.allowlist_paths,
            "allowlist_globs": self.allowlist_globs,
            "denylist_globs": self.denylist_globs,
            "exclude_dirs": self.exclude_dirs,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class ContextFile:
    path: str
    kind: str
    bytes: int
    sha256: str | None
    read: bool
    redacted: bool
    skipped_reason: str | None
    text: str
    facts: list[str]

    def to_manifest_item(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "read": self.read,
            "redacted": self.redacted,
            "skipped_reason": self.skipped_reason,
            "facts": [{"text": fact} for fact in self.facts],
        }


@dataclass(frozen=True)
class ContextResult:
    root: str
    files: list[ContextFile]
    config: ContextConfig

    def manifest(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "config": self.config.to_manifest(),
            "files": [file.to_manifest_item() for file in self.files],
        }

    def facts(self) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for file in self.files:
            for fact in file.facts:
                result.append((file.path, fact))
        return result

    def has_path(self, suffix: str) -> bool:
        return any(file.path.endswith(suffix) and file.read for file in self.files)


def discover_context(root: Path, *, max_file_bytes: int = MAX_FILE_BYTES) -> ContextResult:
    root = root.resolve()
    config = _load_context_config(root, default_max_file_bytes=max_file_bytes)
    candidates = _candidate_files(root, config)
    files = [
        _read_context_file(root, path, config=config, max_file_bytes=config.max_file_bytes)
        for path in candidates
    ]
    files.sort(key=lambda item: item.path)
    return ContextResult(root=str(root), files=files, config=config)


def _load_context_config(root: Path, *, default_max_file_bytes: int) -> ContextConfig:
    config_path = root / CONFIG_FILE_NAME
    include_defaults = True
    max_file_bytes = default_max_file_bytes
    extra_allowlist: list[str] = []
    extra_denylist: list[str] = []
    extra_exclude_dirs: list[str] = []
    errors: list[str] = []
    read = False

    if config_path.exists() and config_path.is_file() and not config_path.is_symlink():
        try:
            data = config_path.read_bytes()
            if len(data) > default_max_file_bytes:
                errors.append(f"{CONFIG_FILE_NAME} exceeds max_file_bytes and was ignored")
            elif b"\x00" in data:
                errors.append(f"{CONFIG_FILE_NAME} appears to be binary and was ignored")
            else:
                raw = tomllib.loads(data.decode("utf-8", errors="ignore"))
                context = raw.get("context", {}) if isinstance(raw, dict) else {}
                if not isinstance(context, dict):
                    errors.append("[context] must be a table")
                else:
                    include_defaults = _read_bool(context, "include_defaults", default=True, errors=errors)
                    max_file_bytes = _read_int(context, "max_file_bytes", default=default_max_file_bytes, errors=errors)
                    extra_allowlist = _read_string_list(context, "allowlist", errors=errors)
                    extra_denylist = _read_string_list(context, "denylist", errors=errors)
                    extra_exclude_dirs = _read_string_list(context, "exclude_dirs", errors=errors)
                read = True
        except tomllib.TOMLDecodeError as exc:
            errors.append(f"invalid TOML in {CONFIG_FILE_NAME}: {exc}")
        except OSError as exc:
            errors.append(f"failed to read {CONFIG_FILE_NAME}: {exc}")
    elif config_path.exists():
        errors.append(f"{CONFIG_FILE_NAME} is not a regular file and was ignored")

    allowlist_paths: list[str] = list(ALLOWLIST_NAMES) if include_defaults else []
    allowlist_globs: list[str] = list(ALLOWLIST_GLOBS) if include_defaults else []
    denylist_globs: list[str] = list(DENYLIST_GLOBS)
    exclude_dirs = sorted(EXCLUDE_DIRS)

    for pattern in extra_allowlist:
        cleaned = _safe_relative_pattern(pattern, errors=errors, field="context.allowlist")
        if cleaned is None:
            continue
        if _has_glob(cleaned):
            allowlist_globs.append(cleaned)
        else:
            allowlist_paths.append(cleaned)

    for pattern in extra_denylist:
        cleaned = _safe_relative_pattern(pattern, errors=errors, field="context.denylist")
        if cleaned is not None:
            denylist_globs.append(cleaned)

    for name in extra_exclude_dirs:
        cleaned = _safe_dir_name(name, errors=errors, field="context.exclude_dirs")
        if cleaned is not None:
            exclude_dirs.append(cleaned)

    return ContextConfig(
        path=CONFIG_FILE_NAME,
        read=read,
        include_defaults=include_defaults,
        max_file_bytes=max_file_bytes,
        allowlist_paths=_dedupe(allowlist_paths),
        allowlist_globs=_dedupe(allowlist_globs),
        denylist_globs=_dedupe(denylist_globs),
        exclude_dirs=_dedupe(exclude_dirs),
        errors=errors,
    )


def _candidate_files(root: Path, config: ContextConfig) -> list[Path]:
    seen: set[Path] = set()
    candidates: list[Path] = []

    config_path = root / CONFIG_FILE_NAME
    if config_path.exists() and config_path.is_file():
        seen.add(config_path)
        candidates.append(config_path)

    for rel in config.allowlist_paths:
        path = root / rel
        if path.exists() and path.is_file() and path not in seen:
            seen.add(path)
            candidates.append(path)

    for pattern in config.allowlist_globs:
        for path in root.glob(pattern):
            if path.exists() and path.is_file() and path not in seen and not _is_in_excluded_dir(root, path, config):
                seen.add(path)
                candidates.append(path)

    return candidates


def _read_context_file(root: Path, path: Path, *, config: ContextConfig, max_file_bytes: int) -> ContextFile:
    rel = path.relative_to(root).as_posix()
    kind = _kind_for_path(rel)

    if _is_denied(rel, config):
        return ContextFile(rel, kind, 0, None, False, False, "denylisted_path", "", [])
    if path.is_symlink():
        return ContextFile(rel, kind, 0, None, False, False, "symlink_skipped", "", [])

    data = path.read_bytes()
    size = len(data)
    if size > max_file_bytes:
        return ContextFile(rel, kind, size, None, False, False, "file_too_large", "", [])
    if b"\x00" in data:
        return ContextFile(rel, kind, size, None, False, False, "binary_file", "", [])

    text = data.decode("utf-8", errors="ignore")
    redacted_text, redacted = redact_secret_like_text(text)
    digest = hashlib.sha256(data).hexdigest()
    facts = _facts_for_file(rel, redacted_text)
    return ContextFile(rel, kind, size, digest, True, redacted, None, redacted_text, facts)


def redact_secret_like_text(text: str) -> tuple[str, bool]:
    redacted = False
    result = text
    for pattern in SECRET_PATTERNS:
        result, count = pattern.subn("[REDACTED:SECRET]", result)
        redacted = redacted or count > 0
    return result, redacted


def _facts_for_file(rel: str, text: str) -> list[str]:
    facts: list[str] = []
    name = Path(rel).name

    if name == "README.md":
        facts.append("README.md is available as project documentation context")
        facts.extend(_facts_from_documentation(text))
    elif name == "AGENTS.md":
        facts.append("AGENTS.md is available as repository instruction context")
        facts.extend(_facts_from_documentation(text))
    elif name == "CONTRIBUTING.md":
        facts.append("CONTRIBUTING.md is available as contribution guidance context")
        facts.extend(_facts_from_documentation(text))
    elif name == CONFIG_FILE_NAME:
        facts.append(".skillgate.toml configures SkillGate local context discovery")
    elif name == "SKILL.md":
        facts.append(f"{rel} is available as a skill instruction file")
    elif name == "pyproject.toml":
        facts.extend(_facts_from_pyproject(text))
    elif name == "package.json":
        facts.extend(_facts_from_package_json(text))
    elif name == "Cargo.toml":
        facts.append("Cargo.toml indicates a Rust project")
        facts.append("cargo test is a candidate verification command for Rust tests")
    elif name == "go.mod":
        facts.append("go.mod indicates a Go project")
        facts.append("go test ./... is a candidate verification command for Go tests")
    elif rel.startswith(".github/workflows/"):
        facts.append(f"{rel} is available as CI workflow context")

    return facts


def _facts_from_documentation(text: str) -> list[str]:
    facts: list[str] = []
    for match in _ENTRY_POINT_LINE.finditer(text):
        symbol = match.group(1).strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", symbol):
            facts.append(f"Documented public entry point: {symbol}")
    for match in _TARGET_SOURCE_LINE.finditer(text):
        path = match.group(1).strip().replace("\\", "/")
        if _is_safe_documented_path(path):
            facts.append(f"Documented target source: {path}")
    for match in _VERIFICATION_LINE.finditer(text):
        command = " ".join(match.group(1).strip().split())
        if any(pattern.fullmatch(command) for pattern in _SAFE_VERIFICATION_COMMANDS):
            facts.append(f"Documented verification command: {command}")
    return _dedupe(facts)


def _is_safe_documented_path(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and len(value) <= 160
        and not path.is_absolute()
        and ".." not in path.parts
        and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path.parts)
    )


def _facts_from_pyproject(text: str) -> list[str]:
    facts = ["pyproject.toml indicates a Python project"]
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        data = {}
    project = data.get("project", {}) if isinstance(data, dict) else {}
    deps = " ".join(project.get("dependencies", []) or [])
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    optional_deps = " ".join(dep for values in optional.values() for dep in values) if isinstance(optional, dict) else ""
    if "pytest" in text or "pytest" in deps or "pytest" in optional_deps:
        facts.append("pytest appears in project configuration")
        facts.append("python -m pytest is a candidate verification command")
    if "[project.scripts]" in text:
        facts.append("pyproject.toml defines project console scripts")
    return facts


def _facts_from_package_json(text: str) -> list[str]:
    facts = ["package.json indicates a Node project"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return facts
    scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
    if isinstance(scripts, dict):
        for name in sorted(scripts):
            if name in {"test", "build", "lint", "typecheck"}:
                facts.append(f"npm run {name} is configured in package.json")
    return facts


def _kind_for_path(rel: str) -> str:
    name = Path(rel).name
    if name == "pyproject.toml":
        return "python_project_config"
    if name == "package.json":
        return "node_project_config"
    if name == "Cargo.toml":
        return "rust_project_config"
    if name == "go.mod":
        return "go_project_config"
    if name == "SKILL.md":
        return "skill"
    if name == CONFIG_FILE_NAME:
        return "skillgate_config"
    if rel.startswith(".github/workflows/"):
        return "ci_workflow"
    return "documentation"


def _is_denied(rel: str, config: ContextConfig) -> bool:
    name = Path(rel).name
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern) for pattern in config.denylist_globs)


def _is_in_excluded_dir(root: Path, path: Path, config: ContextConfig) -> bool:
    rel = path.relative_to(root)
    return any(part in set(config.exclude_dirs) for part in rel.parts)


def _read_bool(section: dict[str, Any], key: str, *, default: bool, errors: list[str]) -> bool:
    value = section.get(key, default)
    if isinstance(value, bool):
        return value
    errors.append(f"context.{key} must be a boolean; using {default!r}")
    return default


def _read_int(section: dict[str, Any], key: str, *, default: int, errors: list[str]) -> int:
    value = section.get(key, default)
    if isinstance(value, int) and value > 0:
        return value
    errors.append(f"context.{key} must be a positive integer; using {default!r}")
    return default


def _read_string_list(section: dict[str, Any], key: str, *, errors: list[str]) -> list[str]:
    value = section.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"context.{key} must be a list of strings; ignoring it")
        return []
    return value


def _safe_relative_pattern(pattern: str, *, errors: list[str], field: str) -> str | None:
    cleaned = pattern.strip().replace("\\", "/")
    if not cleaned:
        errors.append(f"{field} contains an empty pattern; ignoring it")
        return None
    path = Path(cleaned)
    if path.is_absolute() or cleaned.startswith("~") or ".." in path.parts:
        errors.append(f"{field} pattern escapes the repository root and was ignored: {pattern}")
        return None
    return cleaned


def _safe_dir_name(name: str, *, errors: list[str], field: str) -> str | None:
    cleaned = name.strip().replace("\\", "/")
    if not cleaned:
        errors.append(f"{field} contains an empty directory name; ignoring it")
        return None
    if "/" in cleaned or cleaned.startswith("~") or cleaned in {".", ".."}:
        errors.append(f"{field} must contain directory names, not paths: {name}")
        return None
    return cleaned


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
