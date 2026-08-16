#!/usr/bin/env python3
"""Deterministic, receipt-backed setup for Codex Workflow Guardian on macOS."""

import argparse
import copy
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import selectors
import signal
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


SCRIPT_PATH = Path(__file__).resolve()
SETUP_SKILL_DIR = SCRIPT_PATH.parents[1]
REPOSITORY_ROOT = SCRIPT_PATH.parents[3]
LOCK_PATH = REPOSITORY_ROOT / "workflow-dependencies.lock.json"
MARKETPLACE_PATH = REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json"
RECEIPT_RELATIVE = Path("workflow-guardian") / "bootstrap-receipt.json"
RECEIPT_HASH_RELATIVE = Path("workflow-guardian") / "bootstrap-receipt.sha256"
SCHEMA = "codex-workflow-guardian/bootstrap-receipt/v2"
PUBLIC_SCHEMA = "codex-workflow-guardian/bootstrap-stdout/v1"
JOURNAL_SCHEMA = "codex-workflow-guardian/bootstrap-journal/v2"
PUBLIC_ACCEPTANCE_LEVELS = ("preflight", "installed", "uninstalled", "transaction-recovery")
MACOS_ACL_TYPE_EXTENDED = 0x00000100
MIN_PYTHON = (3, 9)
MIN_ALLINLUNA_PYTHON = (3, 11)
MAX_WHEEL_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_JSON_NODES = 20000
MAX_JSON_DEPTH = 64
MAX_COMMAND_OUTPUT = 4 * 1024 * 1024
PROCESS_CLEANUP_TIMEOUT = 2
WHEEL_DEADLINE_SECONDS = 180
PLUGIN_TREE_ALGORITHM = "codex-git-subdir-content-tree-sha256-v1"
MAX_PLUGIN_TREE_ENTRIES = 100000
MAX_PLUGIN_TREE_BYTES = 128 * 1024 * 1024
# The signed native Codex distributed by npm is currently about 271 MB. Keep
# its executable proof bounded without applying the plugin-tree limit to it;
# this ceiling is deliberately finite and leaves room for signed updates.
MAX_CODEX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_TREE_DEPTH = 64
CHILD_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PYTHONIOENCODING",
        "PYTHONNOUSERSITE",
        "TMPDIR",
    }
)
ALLOWED_SYSTEM_SYMLINKS = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}
VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
GUARDIAN_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
PLUGIN_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
MUTABLE_REF_RE = re.compile(r"(?:^|[/@])(?:main|master|latest)$", re.IGNORECASE)
OWNER_MARKER = {"owner": "codex-workflow-guardian/bootstrap/v2"}
OWNER_MARKER_BYTES = b'{"owner":"codex-workflow-guardian/bootstrap/v2"}\n'
JOURNAL_RELATIVE = Path("workflow-guardian") / "bootstrap-journal.json"
LOCK_RELATIVE = Path("workflow-guardian") / "bootstrap.lock"
GUARDIAN_REQUIRED_PATHS = (
    ".codex-plugin/plugin.json",
    ".agents/plugins/marketplace.json",
    "skills/codex-workflow-guardian/SKILL.md",
    "skills/reconcile-codex-subagents/SKILL.md",
    "skills/setup-codex-workflow-guardian/SKILL.md",
    "skills/setup-codex-workflow-guardian/scripts/bootstrap_macos.py",
    "workflow-dependencies.lock.json",
)

ROLE_TEMPLATE_NAMES = (
    "code-reviewer",
    "coder",
    "debugger",
    "document-specialist",
    "executor",
    "explore",
    "luna-coder",
    "luna-worker",
    "security-reviewer",
    "sol-lead",
    "spark-verifier",
    "test-engineer",
    "tracer",
    "verifier",
)
WORKFLOW_MARKETPLACE = "onebigmoon-codex-workflows"
GUARDIAN_SCRIPT_RELATIVE = Path("skills") / "setup-codex-workflow-guardian" / "scripts" / "bootstrap_macos.py"
GUARDIAN_REQUIRED_PATHS = GUARDIAN_REQUIRED_PATHS + tuple(
    f"skills/setup-codex-workflow-guardian/assets/agents/{name}.toml"
    for name in ROLE_TEMPLATE_NAMES
)
MANAGED_ROLE_RELATIVES = tuple(f"agents/{name}.toml" for name in ROLE_TEMPLATE_NAMES)
MANAGED_VENV_RELATIVE = "venvs/allinluna"
MANAGED_PATH_RELATIVES = frozenset(MANAGED_ROLE_RELATIVES + (MANAGED_VENV_RELATIVE,))
MANAGED_PLUGIN_NAMES = ("allinluna", "ponytail")
MANAGED_PLUGIN_SELECTORS = frozenset(f"{name}@{WORKFLOW_MARKETPLACE}" for name in MANAGED_PLUGIN_NAMES)
AUTO_INSTALL_PLUGIN_SELECTORS = frozenset((f"allinluna@{WORKFLOW_MARKETPLACE}",))
CANONICAL_GUARDIAN_REPOSITORY = "https://github.com/OneBigMoon/codex-subagent-reconciler"
MACOS_PYTHON_CANDIDATES = (
    "/opt/homebrew/bin/python3.14",
    "/opt/homebrew/bin/python3.13",
    "/opt/homebrew/bin/python3.12",
    "/opt/homebrew/bin/python3.11",
    "/usr/local/bin/python3.14",
    "/usr/local/bin/python3.13",
    "/usr/local/bin/python3.12",
    "/usr/local/bin/python3.11",
)
TRACKED_PLUGIN_NAMES = frozenset(("codex-workflow-guardian", "allinluna", "ponytail"))
IMMUTABLE_PLUGIN_NAMES = frozenset(("allinluna", "ponytail"))
# These are contract tokens, not component names.  Keep the account-dependent
# set independent from the lock's own profile data so a typo cannot
# self-authorize by appearing in ``profiles.account-dependent.requires``.
ACCOUNT_DEPENDENT_REQUIREMENTS = frozenset(
    ("codex-login", "codex-quota", "model-entitlement", "private-omc-skills")
)

SYSTEM_EXEC_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
CODEX_TEAM_ID = "2DC432GLL2"
CODEX_VERSION = "0.146.0"
MAX_GIT_HEAD_BYTES = 4096
MAX_GIT_REF_BYTES = 256
MAX_GIT_PACKED_REFS_BYTES = 1024 * 1024
MAX_SIDECAR_BYTES = 128
MAX_MARKER_BYTES = 64 * 1024
INTERNAL_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_NO_LAZY_FETCH": "1",
}
_VALIDATED_SOURCE_PROOFS: Dict[str, Dict[str, Dict[str, Any]]] = {}
_VALIDATED_GUARDIAN_PROOFS: Dict[str, Dict[str, Dict[str, Any]]] = {}
_VALIDATED_ROOT_PROOFS: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class LaunchSpec:
    """Immutable, already-verified executable identity used by subprocesses."""

    path: Path
    kind: str
    git_dir: Path
    device: int = 0
    inode: int = 0
    sha256: str = ""
    trust_scope: str = ""
    trust_anchor: str = ""
    trust_chain: Tuple[Tuple[int, int, int, int, int], ...] = ()


@dataclass(frozen=True)
class PluginAddOutcome:
    """Small typed result for Codex plugin add: path, uncertainty, or bad output."""

    kind: str
    path: Optional[str] = None

    @classmethod
    def valid(cls, path: str) -> "PluginAddOutcome":
        return cls("valid", path)

    @classmethod
    def uncertain(cls) -> "PluginAddOutcome":
        return cls("uncertain")

    @classmethod
    def invalid_response(cls) -> "PluginAddOutcome":
        return cls("invalid-response")

    @property
    def status(self) -> str:
        """Alias kept for callers that use status-oriented result handling."""
        return self.kind


class BootstrapError(Exception):
    """Expected, user-actionable setup failure without sensitive detail."""


_LOCK_ALLOWED_FIELDS = {
    (): {
        "lock_version", "contract_version", "platform", "repository", "classifications", "components", "skills",
        "plugins", "system", "roles", "mcp_servers", "host_capability", "profiles", "install_stages", "receipts", "agent_templates",
    },
    ("classifications",): {"account-dependent", "automatic-after-explicit-setup-apply", "automatic-after-explicit-setup-invocation", "bundled-with-guardian-plugin", "detect-then-separately-authorize", "external-prerequisite", "host-capability"},
    ("components",): {"codex_cli", "python", "guardian_plugin", "allinluna", "ponytail", "headroom", "node"},
    ("components", "codex_cli"): {"classification", "required_profiles", "command", "minimum_version", "verified_version", "verification"},
    ("components", "python"): {"classification", "required_profiles", "command", "minimum_version", "verification"},
    ("components", "guardian_plugin"): {"classification", "required_profiles", "version", "repository", "release_pin", "release_pin_required", "license", "skills", "verification"},
    ("components", "allinluna"): {"classification", "required_profiles", "version", "repository", "commit", "plugin_path", "license", "requires_python", "dependencies", "marketplace_source", "pypi", "verification", "content_tree_algorithm", "content_tree_sha256", "content_tree_entries", "content_tree_directories", "content_tree_files", "content_tree_symlinks", "content_tree_file_bytes"},
    ("components", "ponytail"): {"classification", "required_profiles", "version", "repository", "commit", "license", "marketplace_source", "hooks"},
    ("components", "headroom"): {"classification", "required_profiles", "package", "version", "repository", "requires_python", "artifact_hashes", "transitive_dependency_hashes", "license", "mode", "install", "verification"},
    ("components", "node"): {"classification", "required_profiles", "command", "verification"},
    ("components", "allinluna", "pypi"): {"name", "version", "wheel", "sdist"},
    ("components", "allinluna", "pypi", "wheel"): {"filename", "url", "sha256"},
    ("components", "allinluna", "pypi", "sdist"): {"filename", "url", "sha256"},
    ("skills",): {"codex-workflow-guardian", "reconcile-codex-subagents", "setup-codex-workflow-guardian"},
    ("skills", "codex-workflow-guardian"): {"classification", "profiles", "installation", "invocation", "allow_implicit_invocation", "provides"},
    ("skills", "reconcile-codex-subagents"): {"classification", "profiles", "installation", "invocation", "allow_implicit_invocation", "provides"},
    ("skills", "setup-codex-workflow-guardian"): {"classification", "profiles", "installation", "invocation", "allow_implicit_invocation", "provides"},
    ("plugins",): {"codex-workflow-guardian", "allinluna", "ponytail"},
    ("plugins", "codex-workflow-guardian"): {"component", "classification", "profiles", "installation", "authorization"},
    ("plugins", "allinluna"): {"component", "classification", "profiles", "installation", "authorization"},
    ("plugins", "ponytail"): {"component", "classification", "profiles", "installation", "authorization"},
    ("host_capability",): {"classification", "installation", "fresh_capability_discovery", "available_when", "dispatch_ownership", "zero_match_evidence", "portable_full_apply", "post_install_acceptance", "support_matrix"},
    ("host_capability", "dispatch_ownership"): {"pre_dispatch_unowned", "relay_unproven_owner_preserved", "exact_tool_absent_owner_preserved"},
    ("host_capability", "dispatch_ownership", "pre_dispatch_unowned"): {"requires", "result", "preserve_owner", "fallback_allowed"},
    ("host_capability", "dispatch_ownership", "relay_unproven_owner_preserved"): {"requires", "result", "preserve_owner", "fallback_allowed"},
    ("host_capability", "dispatch_ownership", "exact_tool_absent_owner_preserved"): {"requires", "result", "preserve_owner", "fallback_allowed"},
    ("host_capability", "zero_match_evidence"): {"schema", "canonical_store", "run_identity_registry", "runtime_available_proof", "core_only_proof", "blocked_proof", "never_scan_arbitrary_disk"},
    ("host_capability", "zero_match_evidence", "run_identity_registry"): {"path", "protocol", "entry", "safe_path", "no_symlink_following", "directory_mode", "mode", "atomic_persist_before_dispatch"},
    ("host_capability", "zero_match_evidence", "runtime_available_proof"): {"requires", "first_successful_start", "persistence_failure", "sidecar_or_store_existence_mismatch", "integrity_failure"},
    ("host_capability", "zero_match_evidence", "core_only_proof"): {"requires", "result", "fallback", "exactly_once", "resume"},
    ("host_capability", "zero_match_evidence", "blocked_proof"): {"conditions", "result", "fallback_allowed", "arbitrary_disk_scan"},
    ("host_capability", "support_matrix"): {"codex_desktop", "codex_cli", "ide"},
    ("host_capability", "support_matrix", "codex_desktop"): {"surface", "action", "discovery", "status_without_fresh_receipt", "permission"},
    ("host_capability", "support_matrix", "codex_cli"): {"surface", "action", "discovery", "status_without_fresh_receipt", "permission"},
    ("host_capability", "support_matrix", "ide"): {"surface", "action", "discovery", "status_without_fresh_receipt", "permission"},
    ("system",): {"platform", "git", "network", "xcode_command_line_tools"},
    ("system", "platform"): {"classification", "required_profiles", "os", "architecture", "verification"},
    ("system", "git"): {"classification", "required_profiles", "command", "verification"},
    ("system", "network"): {"classification", "required_profiles", "verification"},
    ("system", "xcode_command_line_tools"): {"classification", "required_profiles", "verification"},
    ("roles",): {"source", "classification", "profiles", "installation", "names", "model_capability", "entitlement"},
    ("roles", "model_capability"): {"classification", "status_before_fresh_receipt", "required_evidence"},
    ("profiles",): {"core", "portable-full", "machine-integration", "account-dependent"},
    ("profiles", "core"): {"extends", "requires", "target", "acceptance"},
    ("profiles", "portable-full"): {"extends", "requires", "target", "acceptance", "hooks", "apply_boundary", "post_install_acceptance"},
    ("profiles", "machine-integration"): {"extends", "requires", "target", "authorization", "acceptance"},
    ("profiles", "account-dependent"): {"extends", "requires", "target", "authorization", "acceptance"},
    ("install_stages", "*"): {"id", "classification", "requires", "writes", "note"},
    ("receipts",): {"bootstrap", "stdout_projection", "transaction", "role_model_reasoning", "evidence_layers"},
    ("receipts", "bootstrap"): {"classification", "schema", "relative_path", "integrity_sidecar", "public", "contains", "excludes"},
    ("receipts", "stdout_projection"): {"classification", "schema", "public", "contains", "acceptance_levels", "excludes"},
    ("receipts", "transaction"): {"classification", "schema", "relative_path", "purpose"},
    ("receipts", "role_model_reasoning"): {"classification", "source", "required_for", "status_without_receipt"},
    ("agent_templates",): {"source", "classification", "profiles", "installation", "entitlement", "roles"},
    ("agent_templates", "roles"): {"sol", "spark", "luna"},
    ("agent_templates", "roles", "sol"): {"model", "reasoning_effort"},
    ("agent_templates", "roles", "spark"): {"model", "reasoning_effort"},
    ("agent_templates", "roles", "luna"): {"model", "reasoning_effort"},
}


def _validate_lock_shape(value: Any) -> None:
    """Reject unknown dependency-lock fields at every object level."""
    def visit(item: Any, path: Tuple[str, ...] = ()) -> None:
        if isinstance(item, dict):
            allowed = _LOCK_ALLOWED_FIELDS.get(path)
            if allowed is not None and set(item) - allowed:
                raise BootstrapError("dependency lock contains an unknown field")
            for key, child in item.items():
                visit(child, path + (str(key),))
        elif isinstance(item, list):
            for child in item:
                visit(child, path + ("*",))
    visit(value)


def _validate_profile_graph(value: Dict[str, Any]) -> None:
    profiles = value.get("profiles")
    if not isinstance(profiles, dict):
        raise BootstrapError("dependency lock profiles are invalid")
    expected_profiles = {"core", "portable-full", "machine-integration", "account-dependent"}
    if set(profiles) != expected_profiles:
        raise BootstrapError("dependency lock profiles are incomplete")
    components = value.get("components")
    if not isinstance(components, dict):
        raise BootstrapError("dependency lock components are invalid")
    allowed_requirements = set(components) | {"roles"} | set(ACCOUNT_DEPENDENT_REQUIREMENTS)
    parents: Dict[str, List[str]] = {}
    direct_requires: Dict[str, List[str]] = {}
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise BootstrapError("dependency lock profile is invalid")
        extends = profile.get("extends")
        requires = profile.get("requires")
        if (
            not isinstance(extends, list)
            or not all(isinstance(item, str) for item in extends)
            or len(set(extends)) != len(extends)
            or any(item not in expected_profiles for item in extends)
            or not isinstance(requires, list)
            or not all(isinstance(item, str) for item in requires)
            or len(set(requires)) != len(requires)
        ):
            raise BootstrapError("dependency lock profile graph is invalid")
        if set(requires) - allowed_requirements:
            raise BootstrapError("dependency lock profile requirement is unknown")
        parents[name] = list(extends)
        direct_requires[name] = list(requires)

    if set(direct_requires["account-dependent"]) != set(ACCOUNT_DEPENDENT_REQUIREMENTS):
        raise BootstrapError("dependency lock account-dependent requirements are invalid")

    visiting: set = set()
    visited: set = set()
    closure: Dict[str, set] = {}

    def resolve(name: str) -> set:
        if name in visiting:
            raise BootstrapError("dependency lock profile graph contains a cycle")
        if name in visited:
            return closure[name]
        visiting.add(name)
        effective = set(direct_requires[name])
        for parent in parents[name]:
            effective.update(resolve(parent))
        visiting.remove(name)
        visited.add(name)
        closure[name] = effective
        return effective

    for name in expected_profiles:
        resolve(name)

    expected_by_component: Dict[str, set] = {name: set() for name in components}
    for profile_name, requirements in closure.items():
        for requirement in requirements:
            if requirement in expected_by_component:
                expected_by_component[requirement].add(profile_name)
    for name, component in components.items():
        if not isinstance(component, dict):
            raise BootstrapError("dependency lock component is invalid")
        required = component.get("required_profiles")
        if (
            not isinstance(required, list)
            or not all(isinstance(item, str) for item in required)
            or len(set(required)) != len(required)
            or set(required) != expected_by_component[name]
        ):
            raise BootstrapError("dependency lock component profile closure is invalid")

    roles = value.get("roles")
    if not isinstance(roles, dict):
        raise BootstrapError("dependency lock roles are invalid")
    role_profiles = roles.get("profiles")
    expected_role_profiles = {name for name, requirements in closure.items() if "roles" in requirements}
    if (
        not isinstance(role_profiles, list)
        or not all(isinstance(item, str) and item in expected_profiles for item in role_profiles)
        or len(set(role_profiles)) != len(role_profiles)
        or set(role_profiles) != expected_role_profiles
    ):
        raise BootstrapError("dependency lock roles profile closure is invalid")


def _validate_receipt_contract(value: Dict[str, Any]) -> None:
    """Validate the private receipt and redacted stdout contracts in the lock."""
    receipts = value.get("receipts")
    if not isinstance(receipts, dict):
        raise BootstrapError("dependency lock receipts are invalid")
    projection = receipts.get("stdout_projection")
    if not isinstance(projection, dict):
        raise BootstrapError("dependency lock stdout projection is invalid")
    if (
        projection.get("classification") != "automatic-after-explicit-setup-invocation"
        or projection.get("schema") != PUBLIC_SCHEMA
        or projection.get("public") is not True
        or not isinstance(projection.get("contains"), list)
        or not all(isinstance(item, str) and item for item in projection["contains"])
        or projection.get("acceptance_levels") != list(PUBLIC_ACCEPTANCE_LEVELS)
        or not isinstance(projection.get("excludes"), list)
        or not all(isinstance(item, str) and item for item in projection["excludes"])
    ):
        raise BootstrapError("dependency lock stdout projection contract is invalid")
    required_exclusions = {
        "absolute paths", "path", "relative_path", "*_relative", "sha", "sha256", "hash",
        "digest", "commit", "selector", "device", "inode", "ownership", "provenance",
        "credentials", "environment-values", "raw-logs",
    }
    if not required_exclusions.issubset(set(projection["excludes"])):
        raise BootstrapError("dependency lock stdout projection exclusions are incomplete")


def _validate_host_capability_contract(value: Dict[str, Any]) -> None:
    """Keep host capability discovery explicit and outside Setup installation."""
    host = value.get("host_capability")
    if not isinstance(host, dict):
        raise BootstrapError("dependency lock host capability contract is invalid")
    if (
        host.get("classification") != "detect-then-separately-authorize"
        or host.get("installation") != "not-installed-by-setup"
        or host.get("portable_full_apply") != "installed only; no HostAdapter callable-capability claim"
        or host.get("fresh_capability_discovery") != "required in a fresh new Codex task"
        or host.get("post_install_acceptance") != "fresh new-task capability discovery and permission review"
    ):
        raise BootstrapError("dependency lock host capability boundary is invalid")
    expected_dispatch_ownership = {
        "pre_dispatch_unowned": {
            "requires": ["no_durable_run", "fresh_zero_match", "no_issued_host_action"],
            "result": "BOUNDED_NATIVE_MANUAL",
            "preserve_owner": False,
            "fallback_allowed": True,
        },
        "relay_unproven_owner_preserved": {
            "requires": ["active_run_or_issued_host_action", "relay_unproven"],
            "result": "ACTION_RELAY_REQUIRED",
            "preserve_owner": True,
            "fallback_allowed": False,
        },
        "exact_tool_absent_owner_preserved": {
            "requires": ["active_run_or_issued_host_action", "fresh_exact_tool_absence"],
            "result": "HOST_CAPABILITY_BLOCKED",
            "preserve_owner": True,
            "fallback_allowed": False,
        },
    }
    if host.get("dispatch_ownership") != expected_dispatch_ownership:
        raise BootstrapError("dependency lock host capability dispatch ownership is invalid")
    expected_zero_match_evidence = {
        "schema": "guardian-zero-match/v1",
        "canonical_store": "<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db",
        "run_identity_registry": {
            "path": "<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json",
            "protocol": "guardian-run/v1",
            "entry": "one exact intent_id",
            "safe_path": True,
            "no_symlink_following": True,
            "directory_mode": "0700",
            "mode": "0600",
            "atomic_persist_before_dispatch": True,
        },
        "runtime_available_proof": {
            "requires": [
                "safe_canonical_paths",
                "exact_intent_id",
                "successful_exact_store_query",
                "no_issued_host_action",
                "consistent_store_identity_result",
            ],
            "first_successful_start": "persist non-secret run identity atomically with mode 0600 before any dispatch",
            "persistence_failure": "preserve new run and stop recovery-required",
            "sidecar_or_store_existence_mismatch": "PROTOCOL_INTEGRITY_FAILURE",
            "integrity_failure": "PROTOCOL_INTEGRITY_FAILURE",
        },
        "core_only_proof": {
            "requires": [
                "allinluna_freshly_unavailable",
                "canonical_store_freshly_absent",
                "canonical_store_sidecar_freshly_absent",
                "exact_run_identity_freshly_absent",
                "no_issued_host_action",
                "no_current_owner_evidence",
                "no_known_legacy_or_alternate_store_reference",
            ],
            "result": "FRESH_ZERO_MATCH",
            "fallback": "one bounded non-durable native/manual flow",
            "exactly_once": False,
            "resume": False,
        },
        "blocked_proof": {
            "conditions": [
                "store_or_identity_exists",
                "store_sidecar_or_identity_existence_mismatch",
                "store_or_identity_unsafe_or_unreadable",
                "allinluna_configured_unverified_or_blocked",
                "known_legacy_or_alternate_store_uninspectable",
            ],
            "result": "OWNER_LOOKUP_BLOCKED",
            "fallback_allowed": False,
            "arbitrary_disk_scan": False,
        },
        "never_scan_arbitrary_disk": True,
    }
    if host.get("zero_match_evidence") != expected_zero_match_evidence:
        raise BootstrapError("dependency lock host capability zero-match evidence is invalid")
    support = host.get("support_matrix")
    if not isinstance(support, dict) or set(support) != {"codex_desktop", "codex_cli", "ide"}:
        raise BootstrapError("dependency lock host capability support matrix is invalid")
    for name, entry in support.items():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"surface", "action", "discovery", "status_without_fresh_receipt", "permission"}
            or not all(isinstance(entry.get(key), str) and entry[key] for key in entry)
            or entry.get("status_without_fresh_receipt") != "configured-unverified"
            or not entry.get("permission", "").startswith("required")
        ):
            raise BootstrapError(f"dependency lock host capability lane is invalid: {name}")


def _absolute_lexical(path: Path) -> Path:
    """Return an absolute, normalized path without resolving symlinks."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _path_chain(path: Path) -> Iterable[Path]:
    current = _absolute_lexical(path)
    chain: List[Path] = []
    while True:
        chain.append(current)
        if current == current.parent:
            break
        current = current.parent
    return reversed(chain)


def _assert_safe_path(path: Path, allow_missing: bool = True, anchor: Optional[Path] = None) -> None:
    """Reject links below a managed anchor; trusted system ancestors may link."""
    target = _absolute_lexical(path)
    chain = list(_path_chain(target))
    if anchor is not None:
        anchor_abs = _absolute_lexical(anchor)
        try:
            target.relative_to(anchor_abs)
        except ValueError as exc:
            raise BootstrapError("managed path is outside its anchor") from exc
    for index, node in enumerate(chain):
        try:
            info = node.lstat()
        except FileNotFoundError:
            if allow_missing:
                continue
            raise BootstrapError("managed path is missing")
        except OSError as exc:
            raise BootstrapError("managed path cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            allowed_target = ALLOWED_SYSTEM_SYMLINKS.get(node)
            if allowed_target is None or index == len(chain) - 1 or Path(os.path.realpath(str(node))) != allowed_target:
                raise BootstrapError("managed path contains a symlink or special file")
            continue
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise BootstrapError("managed path contains a symlink or special file")
        if index < len(chain) - 1 and not stat.S_ISDIR(info.st_mode):
            raise BootstrapError("managed path ancestor is not a directory")


def _safe_relative_path(root: Path, relative: str) -> Path:
    """Join a receipt relative path without lexical escape or link traversal."""
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise BootstrapError("receipt path is not relative")
    parts = Path(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise BootstrapError("receipt path is not safe")
    target = _absolute_lexical(root.joinpath(*parts))
    root_abs = _absolute_lexical(root)
    try:
        target.relative_to(root_abs)
    except ValueError as exc:
        raise BootstrapError("receipt path escapes CODEX_HOME") from exc
    _assert_safe_path(target, anchor=root_abs)
    return target


def _relative_path_string(path: Path, root: Path) -> str:
    """Return a receipt-safe POSIX path, never a host absolute path."""
    try:
        return _absolute_lexical(path).relative_to(_absolute_lexical(root)).as_posix()
    except ValueError as exc:
        raise BootstrapError("managed path is outside CODEX_HOME") from exc


def _journal_path(codex_home: Path) -> Path:
    return _safe_relative_path(codex_home, JOURNAL_RELATIVE.as_posix())


def _lock_path(codex_home: Path) -> Path:
    return _safe_relative_path(codex_home, LOCK_RELATIVE.as_posix())


@contextmanager
def _home_lock(codex_home: Path, exclusive: bool):
    """Share check access and serialize mutating operations per CODEX_HOME."""
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - macOS always provides fcntl
        raise BootstrapError("platform locking is unavailable") from exc
    try:
        descriptor = os.open(
            str(codex_home),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
    except (BootstrapError, OSError) as exc:
        raise BootstrapError("CODEX_HOME operation is locked or unsafe") from exc


def _rename_noreplace(
    source: Path,
    target: Path,
    source_ownership: Optional[Dict[str, int]] = None,
    source_digest: Optional[str] = None,
    source_mode: Optional[int] = None,
) -> None:
    """Publish source exactly once and verify the published inode and bytes."""
    source_text, target_text = os.fspath(source), os.fspath(target)
    try:
        source_info = source.lstat()
    except OSError:
        raise
    if stat.S_ISLNK(source_info.st_mode) or not (stat.S_ISREG(source_info.st_mode) or stat.S_ISDIR(source_info.st_mode)):
        raise BootstrapError("atomic no-replace source proof is unavailable")
    if source_mode is not None and (
        stat.S_IMODE(source_info.st_mode) != source_mode
        or source_info.st_uid != os.getuid()
        or _has_acl(source)
    ):
        raise BootstrapError("atomic no-replace source mode is unsafe")
    source_identity = _identity(source_info)
    if source_ownership is not None and not _identity_matches(source_info, source_ownership):
        raise BootstrapError("atomic no-replace source identity changed")
    expected_identity = source_ownership or source_identity
    if source_digest is None:
        source_digest = _tree_hash(source)
    if not isinstance(source_digest, str) or not SHA256_RE.fullmatch(source_digest) or _tree_hash(source) != source_digest:
        raise BootstrapError("atomic no-replace source hash changed")

    def verify_target() -> None:
        try:
            target_info = target.lstat()
        except OSError as exc:
            raise BootstrapError("atomic no-replace target proof is unavailable") from exc
        if (
            stat.S_ISLNK(target_info.st_mode)
            or not _identity_matches(target_info, expected_identity)
            or source_mode is not None and (
                stat.S_IMODE(target_info.st_mode) != source_mode
                or target_info.st_uid != os.getuid()
                or _has_acl(target)
            )
            or _tree_hash(target) != source_digest
        ):
            raise BootstrapError("atomic no-replace target proof failed")

    at_fdcwd = -2 if sys.platform == "darwin" else -100
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is not None:
            renameatx_np.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            renameatx_np.restype = ctypes.c_int
            if renameatx_np(at_fdcwd, source_text.encode(), at_fdcwd, target_text.encode(), 0x00000004) == 0:
                verify_target()
                return
            error = ctypes.get_errno()
            if error != 17:  # EEXIST
                raise OSError(error, os.strerror(error))
            raise FileExistsError(target_text)
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            renameat2.restype = ctypes.c_int
            if renameat2(at_fdcwd, source_text.encode(), at_fdcwd, target_text.encode(), 1) == 0:
                verify_target()
                return
            error = ctypes.get_errno()
            if error != 17:
                raise OSError(error, os.strerror(error))
            raise FileExistsError(target_text)
    if stat.S_ISREG(source_info.st_mode):
        try:
            os.link(source_text, target_text)
        except FileExistsError:
            raise
        verify_target()
        if not _unlink_path_owned(source, source_identity, source_digest):
            raise BootstrapError("atomic no-replace source changed during publish")
        return
    raise BootstrapError("atomic no-replace directory publish is unavailable")


def _safe_json_loads(raw: Any, label: str = "JSON") -> Any:
    """Parse bounded JSON with duplicate-key and depth/node rejection."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_JSON_BYTES:
            raise BootstrapError(f"{label} is too large")
        text = raw.decode("utf-8")
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise BootstrapError(f"{label} is too large")
        text = raw
    else:
        raise BootstrapError(f"{label} is not text")

    def pairs(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise BootstrapError(f"{label} contains duplicate keys")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise BootstrapError(f"{label} contains a non-finite number: {value}")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, BootstrapError) as exc:
        if isinstance(exc, BootstrapError):
            raise
        raise BootstrapError(f"{label} is invalid") from exc

    stack: List[Tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise BootstrapError(f"{label} is too deeply nested")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


def _homebrew_prefix(path: Path) -> bool:
    for prefix in (Path("/opt/homebrew"), Path("/usr/local")):
        try:
            path.relative_to(prefix)
            return True
        except ValueError:
            continue
    return False


def _admin_gid() -> Optional[int]:
    if platform.system() != "Darwin":
        return None
    try:
        import grp as unix_grp

        return int(unix_grp.getgrnam("admin").gr_gid)
    except (ImportError, KeyError, OSError, TypeError, ValueError):
        return None


def _account_home() -> Optional[Path]:
    """Return the verified account home without importing Unix-only modules off macOS."""
    actual_platform = platform.system()
    try:
        if actual_platform == "Darwin":
            import pwd as unix_pwd

            return _absolute_lexical(Path(unix_pwd.getpwuid(os.getuid()).pw_dir))
        return _absolute_lexical(Path.home())
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _has_acl(path: Path) -> bool:
    """Return whether a macOS extended ACL is present; fail closed on errors."""
    if platform.system() != "Darwin":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        acl_get_file = libc.acl_get_file
        acl_free = libc.acl_free
        acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        acl_get_file.restype = ctypes.c_void_p
        acl_free.argtypes = [ctypes.c_void_p]
        acl_free.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = acl_get_file(os.fsencode(str(path)), MACOS_ACL_TYPE_EXTENDED)
        error = ctypes.get_errno()
        if not acl:
            if error not in (errno.ENOENT, errno.EINVAL):
                return True
            try:
                path.lstat()
            except OSError:
                return True
            return False
        try:
            release_error = acl_free(acl)
        except Exception:
            return True
        if release_error != 0:
            return True
        return True
    except Exception:
        return True


def _trusted_file_mode(path: Path, info: os.stat_result) -> bool:
    if (
        info.st_uid not in (0, os.getuid())
        or info.st_mode & stat.S_IWOTH
        or info.st_mode & (stat.S_ISUID | stat.S_ISGID)
    ):
        return False
    if info.st_mode & stat.S_IWGRP:
        admin_gid = _admin_gid()
        if not _homebrew_prefix(path) or admin_gid is None or info.st_gid != admin_gid:
            return False
    return not _has_acl(path)


def _safe_program(path: Path) -> Optional[Path]:
    """Resolve an absolute launcher to a canonical executable with trusted parents."""
    candidate = _absolute_lexical(path)
    try:
        canonical = Path(os.path.realpath(str(candidate)))
        _assert_safe_path(canonical, allow_missing=False)
        info = canonical.lstat()
    except (BootstrapError, OSError):
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or not (info.st_mode & stat.S_IXUSR)
        or not _trusted_file_mode(canonical, info)
    ):
        return None
    for parent in _path_chain(canonical.parent):
        try:
            parent_info = parent.lstat()
        except OSError:
            return None
        mode = parent_info.st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or not _trusted_file_mode(parent, parent_info):
            return None
    return canonical


def _managed_component_trusted(path: Path, info: os.stat_result) -> bool:
    return (
        (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        and info.st_uid == os.getuid()
        and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID)
        and not _has_acl(path)
    )


def _managed_venv_program_proof(path: Path, codex_home: Path) -> Tuple[Path, Tuple[Tuple[int, int, int, int, int], ...]]:
    """Validate one of the two fixed managed-venv Python coordinates."""
    home = _absolute_lexical(codex_home)
    _validate_codex_home(home)
    canonical_home = Path(os.path.realpath(str(home)))
    lexical = _absolute_lexical(path)
    try:
        lexical.relative_to(home)
    except ValueError as exc:
        raise BootstrapError("managed venv Python is outside CODEX_HOME") from exc
    try:
        canonical = Path(os.path.realpath(str(lexical)))
    except OSError as exc:
        raise BootstrapError("managed venv Python cannot be resolved") from exc
    try:
        relative = canonical.relative_to(canonical_home).as_posix()
    except ValueError as exc:
        raise BootstrapError("managed venv Python escapes CODEX_HOME") from exc
    if relative not in (
        "venvs/.guardian-venv-allinluna/bin/python",
        "venvs/allinluna/bin/python",
    ):
        raise BootstrapError("managed venv Python coordinate is not fixed")
    _assert_safe_path(lexical, allow_missing=False, anchor=home)
    chain = (
        canonical_home,
        canonical_home / "venvs",
        canonical_home / Path(relative).parent.parent,
        canonical_home / Path(relative).parent,
        canonical,
    )
    proof: List[Tuple[int, int, int, int, int]] = []
    for index, item in enumerate(chain):
        try:
            info = item.lstat()
        except OSError as exc:
            raise BootstrapError("managed venv Python chain is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not _managed_component_trusted(item, info):
            raise BootstrapError("managed venv Python chain is unsafe")
        if index < len(chain) - 1 and not stat.S_ISDIR(info.st_mode):
            raise BootstrapError("managed venv Python chain is not a directory")
        if index == len(chain) - 1:
            if not stat.S_ISREG(info.st_mode) or not (info.st_mode & stat.S_IXUSR):
                raise BootstrapError("managed venv Python is not executable")
            if platform.system() == "Darwin" and not _native_macho(canonical):
                raise BootstrapError("managed venv Python is not native Mach-O")
        proof.append((int(info.st_dev), int(info.st_ino), int(stat.S_IMODE(info.st_mode)), int(info.st_uid), int(info.st_gid)))
    return canonical, tuple(proof)


def _safe_managed_venv_program(path: Path, codex_home: Path) -> Optional[Path]:
    try:
        return _managed_venv_program_proof(path, codex_home)[0]
    except (BootstrapError, OSError, ValueError):
        return None


def _resolve_program(value: str) -> Optional[Path]:
    if not isinstance(value, str) or not os.path.isabs(value):
        return None
    return _safe_program(Path(value))


def _current_architecture() -> str:
    value = platform.machine().lower()
    return "arm64" if value in ("arm64", "aarch64") else "x86_64" if value in ("x86_64", "amd64") else value


def _read_stable_prefix(path: Path, limit: int, anchor: Optional[Path] = None) -> bytes:
    """Read a bounded prefix from one no-followed FD without a size cap on the file."""
    if not isinstance(limit, int) or limit <= 0:
        raise BootstrapError("bounded prefix limit is invalid")
    _assert_safe_path(path, allow_missing=False, anchor=anchor)
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BootstrapError("bounded prefix is not a regular file")
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not _same_stat(before, opened):
            raise BootstrapError("bounded prefix changed before read")
        expected = min(int(opened.st_size), limit)
        chunks: List[bytes] = []
        total = 0
        while total < expected:
            chunk = os.read(descriptor, expected - total)
            if not chunk:
                raise BootstrapError("bounded prefix ended during read")
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise BootstrapError("bounded prefix path disappeared during read") from exc
        if not _same_stat(opened, after) or not _same_stat(opened, after_path) or total != expected:
            raise BootstrapError("bounded prefix changed during read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _macho_architectures(path: Path) -> Optional[set]:
    try:
        # Mach-O headers are at the start of normal binaries, but the binary
        # itself is commonly much larger than this bounded inspection window.
        raw = _read_stable_prefix(path, 4096, None)
    except (BootstrapError, OSError):
        return None
    if len(raw) < 4:
        return None
    magic = int.from_bytes(raw[:4], "big")
    thin = {
        0xFEEDFACE: ("big", 32),
        0xCEFAEDFE: ("little", 32),
        0xFEEDFACF: ("big", 64),
        0xCFFAEDFE: ("little", 64),
    }
    if magic in thin:
        endian, bits = thin[magic]
        if len(raw) < 8:
            return None
        cputype = int.from_bytes(raw[4:8], endian, signed=False)
        arch = {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(cputype)
        return {arch} if arch else set()
    if magic in (0xCAFEBABE, 0xCAFEBABF, 0xBEBAFECA, 0xBFBAFECA):
        endian = "big" if magic in (0xCAFEBABE, 0xCAFEBABF) else "little"
        if len(raw) < 8:
            return None
        count = int.from_bytes(raw[4:8], endian, signed=False)
        entry_size = 20 if magic in (0xCAFEBABE, 0xBEBAFECA) else 32
        if count > 32 or 8 + count * entry_size > len(raw):
            return None
        result = set()
        for offset in range(8, 8 + count * entry_size, entry_size):
            cputype = int.from_bytes(raw[offset : offset + 4], endian, signed=False)
            arch = {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(cputype)
            if arch:
                result.add(arch)
        return result
    return set()


def _native_macho(path: Path) -> bool:
    if platform.system() != "Darwin":
        # Unit fixtures run on Linux; real macOS operations take the strict
        # branch and require a current-architecture Mach-O below.
        return True
    architectures = _macho_architectures(path)
    return architectures is not None and _current_architecture() in architectures


def _launch_hash_limit(kind: str) -> int:
    return MAX_CODEX_EXECUTABLE_BYTES if kind == "codex" else MAX_PLUGIN_TREE_BYTES


def _launch_spec(
    path: Path,
    kind: str,
    git_path: Optional[Path] = None,
    trust_scope: str = "",
    trust_anchor: Optional[Path] = None,
) -> Optional[LaunchSpec]:
    trust_chain: Tuple[Tuple[int, int, int, int, int], ...] = ()
    if trust_scope == "managed-venv":
        if trust_anchor is None:
            return None
        try:
            canonical, trust_chain = _managed_venv_program_proof(path, trust_anchor)
            anchor_value = str(Path(os.path.realpath(str(_absolute_lexical(trust_anchor)))))
        except (BootstrapError, OSError, ValueError):
            return None
    elif trust_scope:
        return None
    else:
        canonical = _safe_program(path)
        anchor_value = ""
    if canonical is None or (platform.system() == "Darwin" and not _native_macho(canonical)):
        return None
    try:
        info = canonical.lstat()
        digest = _sha256_file(canonical, _launch_hash_limit(kind))
    except (BootstrapError, OSError):
        return None
    git_dir = _absolute_lexical(git_path.parent if git_path is not None else Path("/usr/bin"))
    return LaunchSpec(
        canonical,
        kind,
        git_dir,
        int(info.st_dev),
        int(info.st_ino),
        digest,
        trust_scope,
        anchor_value,
        trust_chain,
    )


def _verify_codex_signature(path: Path, codex_home: Optional[Path]) -> bool:
    if platform.system() != "Darwin":
        return True
    codesign_path = _safe_program(Path("/usr/bin/codesign"))
    if codesign_path is None:
        return False
    codesign = _launch_spec(codesign_path, "codesign")
    if codesign is None:
        return False
    requirement = (
        '=anchor apple generic and identifier "codex" '
        f'and certificate leaf[subject.OU] = "{CODEX_TEAM_ID}"'
    )
    database_home = _account_home()
    if database_home is None:
        return False
    code, _, _ = _run_argv(
        [codesign, "--verify", "--strict", "--requirements", requirement, str(path)],
        codex_home or database_home,
        path.parent,
        timeout=20,
    )
    return code == 0


def _codex_native_from_wrapper(wrapper: Path, codex_home: Optional[Path], git_path: Optional[Path]) -> Optional[Path]:
    architecture = _current_architecture()
    platform_tag = "darwin-arm64" if architecture == "arm64" else "darwin-x64" if architecture == "x86_64" else ""
    npm_cpu = "arm64" if architecture == "arm64" else "x64" if architecture == "x86_64" else ""
    vendor_arch = "aarch64-apple-darwin" if architecture == "arm64" else "x86_64-apple-darwin" if architecture == "x86_64" else ""
    if not platform_tag or not npm_cpu or not vendor_arch:
        return None
    # ``_codex_launch_spec`` passes the canonical npm wrapper.  Its package
    # root is the fixed two-level relationship used by the published package
    # (``<main-root>/bin/codex[.js]``); do not guess by walking ancestors.
    wrapper = _absolute_lexical(wrapper)
    main_root = wrapper.parent.parent

    def read_package(path: Path, label: str) -> Optional[Dict[str, Any]]:
        try:
            value = _safe_json_loads(_read_stable_bytes(path, MAX_JSON_BYTES, None), label)
        except (BootstrapError, OSError):
            return None
        return value if isinstance(value, dict) else None

    def verify_native(package_root: Path, candidate: Path) -> Optional[Path]:
        # A package root is itself part of the trust boundary.  Reject a
        # symlinked package/vendor path before resolving the native executable,
        # so a valid Mach-O cannot escape the pinned platform package.
        try:
            _assert_safe_path(package_root, allow_missing=False)
            _assert_safe_path(candidate, allow_missing=False, anchor=package_root)
        except (BootstrapError, OSError):
            return None
        native = _safe_program(candidate)
        if native is None:
            return None
        try:
            _assert_safe_path(native, allow_missing=False, anchor=package_root)
        except (BootstrapError, OSError):
            return None
        if not _native_macho(native) or not _verify_codex_signature(native, codex_home):
            return None
        return native

    try:
        _assert_safe_path(main_root, allow_missing=False)
        # Keep the canonical wrapper tied to the package root whose metadata
        # is about to be checked; a path outside it is never a candidate.
        wrapper.relative_to(main_root)
    except (BootstrapError, OSError, ValueError):
        return None

    package = read_package(main_root / "package.json", "Codex npm package")
    if not isinstance(package, dict) or package.get("name") != "@openai/codex" or package.get("version") != CODEX_VERSION:
        return None

    def resolve_platform(package_root: Path, allow_legacy_layout: bool) -> Optional[Path]:
        try:
            _assert_safe_path(package_root, allow_missing=False)
        except (BootstrapError, OSError):
            return None
        platform_package = read_package(package_root / "package.json", "Codex platform package")
        if not isinstance(platform_package, dict):
            return None

        # Current npm packages use an aliased package name/version and
        # explicitly identify the Darwin CPU they contain.
        if (
            platform_package.get("name") == "@openai/codex"
            and platform_package.get("version") == f"{CODEX_VERSION}-{platform_tag}"
            and platform_package.get("os") == ["darwin"]
            and platform_package.get("cpu") == [npm_cpu]
        ):
            candidate = package_root / "vendor" / vendor_arch / "bin" / "codex"
            return verify_native(package_root, candidate)

        if not allow_legacy_layout:
            return None

        # Retain compatibility with the earlier pinned nested layout while
        # requiring its complete, exact package identity.
        if (
            platform_package.get("name") == f"@openai/codex-{platform_tag}"
            and platform_package.get("version") == CODEX_VERSION
            and (
                ("os" not in platform_package and "cpu" not in platform_package)
                or (
                    platform_package.get("os") == ["darwin"]
                    and platform_package.get("cpu") == [npm_cpu]
                )
            )
        ):
            for candidate in (package_root / "bin" / "codex", package_root / "codex"):
                native = verify_native(package_root, candidate)
                if native is not None:
                    return native
        return None

    nested_root = main_root / "node_modules" / "@openai" / f"codex-{platform_tag}"
    try:
        # Check lexical existence before parsing anything.  An existing but
        # malformed nested entry is authoritative and must fail closed.
        _assert_safe_path(nested_root, allow_missing=True, anchor=main_root)
        nested_root.lstat()
    except FileNotFoundError:
        nested_root = None
    except (BootstrapError, OSError):
        return None

    if nested_root is not None:
        return resolve_platform(nested_root, allow_legacy_layout=True)

    # Hoisted npm installs are accepted only for the exact main package shape;
    # this is a direct sibling lookup, never an ancestor/scope scan.
    if not (
        main_root.name == "codex"
        and main_root.parent.name == "@openai"
        and main_root.parent.parent.name == "node_modules"
    ):
        return None
    sibling_root = main_root.parent / f"codex-{platform_tag}"
    try:
        _assert_safe_path(sibling_root, allow_missing=False, anchor=main_root.parent.parent)
    except (BootstrapError, OSError):
        return None
    return resolve_platform(sibling_root, allow_legacy_layout=False)


def _codex_launch_spec(value: Optional[str], codex_home: Optional[Path] = None, git_path: Optional[Path] = None) -> Optional[LaunchSpec]:
    if not isinstance(value, str) or not os.path.isabs(value):
        return None
    candidate = _safe_program(Path(value))
    if candidate is None:
        return None
    if _native_macho(candidate):
        if not _verify_codex_signature(candidate, codex_home):
            return None
        return _launch_spec(candidate, "codex", git_path)
    native = _codex_native_from_wrapper(candidate, codex_home, git_path)
    return _launch_spec(native, "codex", git_path) if native is not None else None


def _git_launch_spec(value: Optional[Path]) -> Optional[LaunchSpec]:
    if value is None or not Path(value).is_absolute():
        return None
    spec = _launch_spec(Path(value), "git", Path(value))
    if spec is None:
        return None
    if platform.system() == "Darwin":
        if spec.path != Path("/usr/bin/git"):
            return None
        try:
            if spec.path.lstat().st_uid != 0:
                return None
        except OSError:
            return None
    return spec


def _python_launch_spec(value: Optional[Path], codex_home: Optional[Path] = None, venv: bool = False) -> Optional[LaunchSpec]:
    if value is None or not Path(value).is_absolute():
        return None
    if venv:
        if codex_home is None:
            return None
        try:
            anchor = _absolute_lexical(codex_home)
            _validate_codex_home(anchor)
        except (BootstrapError, OSError, ValueError):
            return None
        return _launch_spec(Path(value), "python", trust_scope="managed-venv", trust_anchor=anchor)
    spec = _launch_spec(Path(value), "python")
    return spec


def _canonical_repository(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    return text[:-4] if text.lower().endswith(".git") else text


def _git_checkout_head(root: Path, codex_home: Path) -> Optional[str]:
    """Read a checkout HEAD without trusting a resolver-provided SHA."""
    try:
        _assert_safe_path(root, allow_missing=False, anchor=codex_home)
        if not stat.S_ISDIR(root.lstat().st_mode):
            return None
        git_dir = root / ".git"
        _assert_safe_path(git_dir, allow_missing=False, anchor=codex_home)
        git_info = git_dir.lstat()
        if stat.S_ISLNK(git_info.st_mode):
            return None
        if stat.S_ISREG(git_info.st_mode):
            pointer = _read_stable_bytes(git_dir, MAX_GIT_HEAD_BYTES, codex_home).decode("utf-8", "strict").strip()
            if not pointer.startswith("gitdir:"):
                return None
            return None
        if not stat.S_ISDIR(git_info.st_mode):
            return None
        head = git_dir / "HEAD"
        _assert_safe_path(head, allow_missing=False, anchor=codex_home)
        head_text = _read_stable_bytes(head, MAX_GIT_HEAD_BYTES, codex_home).decode("utf-8", "strict")
        line = head_text.strip()
        if COMMIT_RE.fullmatch(line):
            return line.lower()
        if not line.startswith("ref: refs/"):
            return None
        ref = line[5:]
        if not re.fullmatch(r"refs/[A-Za-z0-9._/-]+", ref):
            return None
        ref_path = git_dir / ref
        try:
            _assert_safe_path(ref_path, allow_missing=False, anchor=codex_home)
            value = _read_stable_bytes(ref_path, MAX_GIT_REF_BYTES, codex_home).decode("utf-8", "strict").strip()
        except (BootstrapError, OSError):
            value = ""
        if COMMIT_RE.fullmatch(value):
            return value.lower()
        packed = git_dir / "packed-refs"
        try:
            _assert_safe_path(packed, allow_missing=False, anchor=codex_home)
            data = _read_stable_bytes(packed, MAX_GIT_PACKED_REFS_BYTES, codex_home).decode("utf-8", "strict")
        except (BootstrapError, OSError):
            return None
        for item in data.splitlines():
            parts = item.split(" ", 1)
            if len(parts) == 2 and parts[1] == ref and COMMIT_RE.fullmatch(parts[0]):
                return parts[0].lower()
    except (BootstrapError, OSError, UnicodeError):
        return None
    return None


def _git_output(git_bin: Path, codex_home: Path, root: Path, args: Sequence[str]) -> Optional[str]:
    # Git validation is deliberately isolated from ambient config and network
    # helpers.  The working tree is compared below without `git status`, whose
    # clean-filter path can execute checkout-controlled commands.
    git_spec = _git_launch_spec(git_bin)
    if git_spec is None:
        return None
    safe_args = [
        git_spec,
        "-C",
        str(root),
        "--no-pager",
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "protocol.allow=never",
        "-c",
        "fetch.ifMissing=false",
        *args,
    ]
    code, stdout, _ = _run_argv(
        safe_args,
        codex_home,
        root,
        timeout=30,
        env_overrides=INTERNAL_GIT_ENV,
    )
    if code != 0:
        return None
    return stdout.strip()


def _git_path_records(raw: str, staged: bool) -> Optional[Dict[str, Tuple[str, str]]]:
    """Parse raw tree/index records without invoking Git's clean machinery."""
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_PLUGIN_TREE_BYTES:
        return None
    records: Dict[str, Tuple[str, str]] = {}
    for record in raw.split("\0"):
        if not record:
            continue
        if len(records) >= MAX_PLUGIN_TREE_ENTRIES:
            return None
        if "\t" not in record:
            return None
        header, path = record.split("\t", 1)
        fields = header.split()
        if staged:
            if len(fields) != 3 or fields[2] != "0":
                return None
            mode, object_id = fields[0], fields[1]
        else:
            if len(fields) != 3 or fields[1] != "blob":
                return None
            mode, object_id = fields[0], fields[2]
        if not path or path in records or not re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", object_id):
            return None
        if mode not in ("100644", "100755"):
            return None
        records[path] = (mode, object_id.lower())
    return records


def _raw_git_checkout_clean(
    root: Path,
    codex_home: Path,
    git_bin: Path,
    expected: str,
) -> bool:
    """Compare HEAD, index, and raw bytes without filters or external hooks."""
    tree_raw = _git_output(git_bin, codex_home, root, ["ls-tree", "-r", "-z", "--full-tree", "HEAD", "--"])
    index_raw = _git_output(git_bin, codex_home, root, ["ls-files", "--stage", "-z", "--"])
    if tree_raw is None or index_raw is None:
        return False
    tree = _git_path_records(tree_raw, staged=False)
    index = _git_path_records(index_raw, staged=True)
    if tree is None or index is None or tree != index:
        return False
    paths: Dict[str, str] = {}
    state = {"entries": 0, "file_bytes": 0}

    def walk(directory_fd: int, prefix: str, is_root: bool, depth: int) -> None:
        if depth > MAX_TREE_DEPTH:
            raise BootstrapError("Git checkout is too deep")
        remaining = MAX_PLUGIN_TREE_ENTRIES - state["entries"]
        names: List[str] = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if is_root and entry.name == ".git":
                    continue
                names.append(entry.name)
                if len(names) > remaining:
                    raise BootstrapError("Git checkout has too many entries")
        names.sort(key=os.fsencode)
        for name in names:
            if state["entries"] >= MAX_PLUGIN_TREE_ENTRIES:
                raise BootstrapError("Git checkout has too many entries")
            info_before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            state["entries"] += 1
            relative = name if not prefix else prefix + "/" + name
            if stat.S_ISDIR(info_before.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    child_info = os.fstat(child_fd)
                    if not _same_stat(info_before, child_info):
                        raise BootstrapError("Git checkout changed during verification")
                    walk(child_fd, relative, False, depth + 1)
                finally:
                    os.close(child_fd)
                info_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if not _same_stat(info_before, info_after):
                    raise BootstrapError("Git checkout changed during verification")
                continue
            if not stat.S_ISREG(info_before.st_mode):
                raise BootstrapError("Git checkout contains a special file")
            mode = "100755" if info_before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else "100644"
            if relative not in tree or tree[relative][0] != mode:
                raise BootstrapError("Git checkout path or mode differs from HEAD")
            object_id = tree[relative][1]
            algorithm = hashlib.sha256 if len(object_id) == 64 else hashlib.sha1
            child_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                child_info = os.fstat(child_fd)
                if not _same_stat(info_before, child_info):
                    raise BootstrapError("Git checkout changed during verification")
                digest = _read_hash_fd(
                    child_fd,
                    state,
                    prefix=(f"blob {child_info.st_size}\0").encode("ascii"),
                    algorithm=algorithm,
                )
            finally:
                os.close(child_fd)
            info_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _same_stat(info_before, info_after) or digest != object_id:
                raise BootstrapError("Git checkout changed during verification")
            paths[relative] = mode

    try:
        root_info = root.lstat()
        directory_fd = os.open(
            str(root),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(directory_fd)
            if not _same_stat(root_info, opened):
                return False
            walk(directory_fd, "", True, 0)
        finally:
            os.close(directory_fd)
    except (BootstrapError, OSError, UnicodeError, ValueError):
        return False
    return set(paths) == set(tree)


def _validate_git_checkout(
    root: Path,
    codex_home: Path,
    git_bin: Optional[Path],
    guardian_ref: Optional[str],
    expected_root_proof: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Prove one clean, canonical, tracked Guardian checkout at an exact commit."""
    if git_bin is None or not guardian_ref or not COMMIT_RE.fullmatch(guardian_ref):
        return False, "exact Git tooling and Guardian commit are required"
    try:
        root = _absolute_lexical(root)
        root_key = str(root)
        if expected_root_proof is None:
            expected_root_proof = _VALIDATED_ROOT_PROOFS.get(root_key)
        root.relative_to(_absolute_lexical(codex_home))
        _assert_safe_path(root, allow_missing=False, anchor=codex_home)
        root_info = root.lstat()
        if not stat.S_ISDIR(root_info.st_mode):
            return False, "marketplace root is not a directory"
        if (
            expected_root_proof is not None
            and (
                not _root_identity_matches(root_info, expected_root_proof)
            )
        ):
            return False, "Guardian checkout root identity changed"
        git_meta = root / ".git"
        _assert_safe_path(git_meta, allow_missing=False, anchor=codex_home)
        if not stat.S_ISDIR(git_meta.lstat().st_mode):
            return False, "marketplace root is not an owned Git checkout"
    except (BootstrapError, OSError, ValueError):
        return False, "marketplace root is unsafe"

    expected = guardian_ref.lower()
    head = _git_output(git_bin, codex_home, root, ["rev-parse", "--verify", "HEAD^{commit}"])
    verified = _git_output(git_bin, codex_home, root, ["cat-file", "-e", f"{expected}^{{commit}}"])
    if head is None or verified is None or head.lower() != expected:
        return False, "Git HEAD is not the declared Guardian commit"
    # Read the configured value itself; `remote get-url` applies url.*.insteadOf
    # and could turn an untrusted origin into the canonical-looking URL.
    origin = _git_output(git_bin, codex_home, root, ["config", "--local", "--get-all", "remote.origin.url"])
    expected_repo = CANONICAL_GUARDIAN_REPOSITORY
    if origin is None or _canonical_repository(origin) != _canonical_repository(expected_repo):
        return False, "Git origin is not the canonical Guardian repository"
    replacements = _git_output(git_bin, codex_home, root, ["for-each-ref", "--format=%(refname)", "refs/replace/"])
    if replacements is None or replacements:
        return False, "Guardian checkout contains replacement refs"
    index_states = _git_output(git_bin, codex_home, root, ["ls-files", "-v", "-z"])
    if index_states is None:
        return False, "Guardian tracked-file listing failed"
    for record in index_states.split("\0"):
        if not record:
            continue
        if len(record) < 3 or record[0] != "H" or record[1] != " " or not record[2:]:
            return False, "Guardian checkout contains concealed or non-normal index entries"
    if not _raw_git_checkout_clean(root, codex_home, git_bin, expected):
        return False, "Guardian checkout is not clean"
    indexed = _git_output(git_bin, codex_home, root, ["ls-files", "--stage"])
    if indexed is None:
        return False, "Guardian tracked-file listing failed"
    entries: Dict[str, str] = {}
    for line in indexed.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            return False, "Guardian tracked-file listing is malformed"
        mode_hash, path_text = parts
        mode_parts = mode_hash.split()
        if len(mode_parts) != 3 or mode_parts[0] in ("120000", "160000") or mode_parts[2] != "0":
            return False, "Guardian checkout contains a symlink, submodule, or conflict entry"
        entries[path_text] = mode_parts[0]
    for required in GUARDIAN_REQUIRED_PATHS:
        if required not in entries or entries[required] not in ("100644", "100755"):
            return False, "Guardian required files are not tracked regular files"
        try:
            path = _safe_relative_path(root, required)
            if stat.S_ISLNK(path.lstat().st_mode) or not stat.S_ISREG(path.lstat().st_mode):
                return False, "Guardian required files are unsafe"
        except (BootstrapError, OSError):
            return False, "Guardian required files are unsafe"
    try:
        _strict_owned_path(root, codex_home)
        root_proof = _source_directory_proof(root, codex_home)
        if expected_root_proof is not None and not _root_identity_matches(root.lstat(), expected_root_proof):
            return False, "Guardian checkout root identity changed"
        guardian_frozen: Dict[str, Dict[str, Any]] = {}
        for required in GUARDIAN_REQUIRED_PATHS:
            guardian_frozen[required] = _source_asset_proof(root / required, root)
        frozen: Dict[str, Dict[str, Any]] = {}
        for _, source, relative, kind in _target_specs(root):
            if kind != "file":
                continue
            frozen[relative.as_posix()] = guardian_frozen[_relative_path(source, root)]
        # Re-run the raw checkout proof after freezing every local source
        # proof.  A file can be changed after the first clean check but before
        # its proof is captured; never publish that altered snapshot.
        if not _raw_git_checkout_clean(root, codex_home, git_bin, expected):
            return False, "Guardian checkout changed during validation"
        if not _source_binding_matches(root, guardian_frozen, root_proof):
            return False, "Guardian checkout changed during validation"
        for _, source, relative, kind in _target_specs(root):
            if kind != "file" or not _source_asset_matches(source, root, frozen.get(relative.as_posix())):
                return False, "Guardian checkout changed during validation"
        root_after = root.lstat()
        if (
            not _same_stat(root_info, root_after)
            or not _stat_proof_matches(root_after, root_proof)
            or (expected_root_proof is not None and not _root_identity_matches(root_after, expected_root_proof))
        ):
            return False, "Guardian checkout changed during validation"
        # Publish only after every local proof and the final root identity check
        # succeeds; failed validation never replaces an existing binding.
        _VALIDATED_SOURCE_PROOFS[root_key] = frozen
        _VALIDATED_GUARDIAN_PROOFS[root_key] = guardian_frozen
        _VALIDATED_ROOT_PROOFS[root_key] = root_proof
    except (BootstrapError, OSError):
        return False, "Guardian managed source proof could not be frozen"
    return True, "verified"


def _version_text(actual: Tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in actual)


def _normalized_distribution_version(value: Any) -> str:
    return re.sub(r"[-_.]", "", str(value or "")).lower()


def _validated_wheel_filename(wheel: Any, pypi: Any) -> Optional[str]:
    """Return the one safe wheel basename bound to lock metadata and URL."""
    if not isinstance(wheel, dict) or not isinstance(pypi, dict):
        return None
    filename = wheel.get("filename")
    url = wheel.get("url")
    if not isinstance(filename, str) or not isinstance(url, str):
        return None
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        return None
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.whl", filename)
        or "/" in filename
        or "\\" in filename
        or filename.startswith(".")
    ):
        return None
    stem = filename[:-4]
    parts = stem.split("-")
    if len(parts) not in (5, 6) or any(not re.fullmatch(r"[A-Za-z0-9_.]+", part) for part in parts):
        return None
    if _normalized_distribution_version(parts[0]) != _normalized_distribution_version(pypi.get("name")):
        return None
    if _normalized_distribution_version(parts[1]) != _normalized_distribution_version(pypi.get("version")):
        return None
    parsed = urllib.parse.urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "files.pythonhosted.org"
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or urllib.parse.unquote(parsed.path) != parsed.path
        or parsed.path.rsplit("/", 1)[-1] != filename
    ):
        return None
    return filename


def _version_tuple(value: str) -> Optional[Tuple[int, int, int]]:
    match = VERSION_RE.search(value or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _version_at_least(actual: Optional[Tuple[int, int, int]], minimum: Tuple[int, int, int]) -> bool:
    return actual is not None and actual >= minimum


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(
    path: Path,
    limit: int = MAX_PLUGIN_TREE_BYTES,
    anchor: Optional[Path] = None,
) -> str:
    """Hash one regular file through the bounded stable-read primitive."""
    return _sha256_bytes(_read_stable_bytes(path, limit, anchor))


def _same_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_uid == right.st_uid
        and left.st_gid == right.st_gid
        and left.st_size == right.st_size
        and getattr(left, "st_mtime_ns", 0) == getattr(right, "st_mtime_ns", 0)
        and getattr(left, "st_ctime_ns", 0) == getattr(right, "st_ctime_ns", 0)
    )


def _stat_proof(info: os.stat_result, kind: Optional[str] = None) -> Dict[str, Any]:
    proof: Dict[str, Any] = {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "mode": int(stat.S_IMODE(info.st_mode)),
        "uid": int(info.st_uid),
        "gid": int(info.st_gid),
        "size": int(info.st_size),
        "mtime_ns": int(getattr(info, "st_mtime_ns", 0)),
        "ctime_ns": int(getattr(info, "st_ctime_ns", 0)),
    }
    if kind is not None:
        proof["kind"] = kind
    return proof


def _stat_proof_matches(info: os.stat_result, proof: Any) -> bool:
    if not isinstance(proof, dict):
        return False
    expected = _stat_proof(info)
    return all(proof.get(key) == value for key, value in expected.items())


def _root_identity_matches(info: os.stat_result, proof: Any) -> bool:
    if not isinstance(proof, dict) or proof.get("kind") != "directory":
        return False
    expected = _stat_proof(info)
    return all(proof.get(key) == expected[key] for key in ("device", "inode", "mode", "uid", "gid"))


def _strict_owned_path(path: Path, anchor: Path, *, allow_missing: bool = False) -> None:
    """Require an existing managed path chain to be current-UID/private.

    Source and managed target paths have a narrower trust boundary than the
    system launchers: every component below the explicit anchor must belong to
    the current user, be non-writable by group/other users, and have no ACL.
    """
    target = _absolute_lexical(path)
    root = _absolute_lexical(anchor)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BootstrapError("managed path is outside its trust anchor") from exc
    chain = list(_path_chain(target))
    try:
        start = chain.index(root)
    except ValueError as exc:
        raise BootstrapError("managed path trust anchor is invalid") from exc
    for node in chain[start:]:
        try:
            info = node.lstat()
        except FileNotFoundError:
            if allow_missing and node == target:
                return
            raise BootstrapError("managed path is missing")
        except OSError as exc:
            raise BootstrapError("managed path cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise BootstrapError("managed path contains a symlink or special file")
        if node != target and not stat.S_ISDIR(info.st_mode):
            raise BootstrapError("managed path ancestor is not a directory")
        if info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or _has_acl(node):
            raise BootstrapError("managed path ownership or mode is unsafe")


def _strict_existing_parent_chain(path: Path, anchor: Path) -> None:
    """Validate the existing suffix of a possibly-not-yet-created parent."""
    candidate = _absolute_lexical(path)
    root = _absolute_lexical(anchor)
    while True:
        try:
            candidate.lstat()
            break
        except FileNotFoundError:
            if candidate == root:
                break
            candidate = candidate.parent
        except OSError as exc:
            raise BootstrapError("managed parent cannot be inspected") from exc
    _strict_owned_path(candidate, root)


def _source_asset_proof(path: Path, anchor: Path) -> Dict[str, Any]:
    """Freeze one trusted regular source file's identity, metadata, and bytes."""
    source = _absolute_lexical(path)
    _strict_owned_path(source, anchor)
    before = source.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BootstrapError("source asset is not a regular file")
    digest = _sha256_file(source, MAX_PLUGIN_TREE_BYTES, anchor)
    after = source.lstat()
    if not _same_stat(before, after) or _has_acl(source):
        raise BootstrapError("source asset changed during proof")
    return dict(_stat_proof(before), sha256=digest)


def _source_directory_proof(path: Path, anchor: Path) -> Dict[str, Any]:
    """Freeze one trusted checkout root's stable directory identity."""
    source = _absolute_lexical(path)
    _strict_owned_path(source, anchor)
    before = source.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise BootstrapError("source root is not a regular directory")
    after = source.lstat()
    if not _same_stat(before, after) or _has_acl(source):
        raise BootstrapError("source root changed during proof")
    return _stat_proof(before, "directory")


def _source_asset_matches(path: Path, anchor: Path, proof: Any) -> bool:
    """Revalidate a previously frozen source proof without rebasing it."""
    if not isinstance(proof, dict) or not SHA256_RE.fullmatch(str(proof.get("sha256", ""))):
        return False
    try:
        source = _absolute_lexical(path)
        _strict_owned_path(source, anchor)
        before = source.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return False
        if not _stat_proof_matches(before, proof):
            return False
        digest = _sha256_file(source, MAX_PLUGIN_TREE_BYTES, anchor)
        after = source.lstat()
        return digest == proof["sha256"] and _same_stat(before, after) and not _has_acl(source)
    except (BootstrapError, OSError, TypeError, ValueError):
        return False


def _source_binding_matches(
    root: Path,
    source_proofs: Any,
    root_proof: Any,
) -> bool:
    """Revalidate one frozen checkout binding without rebasing it."""
    source_root = _absolute_lexical(root)
    if (
        not isinstance(source_proofs, dict)
        or set(source_proofs) != set(GUARDIAN_REQUIRED_PATHS)
        or not isinstance(root_proof, dict)
        or root_proof.get("kind") != "directory"
    ):
        return False
    try:
        root_before = source_root.lstat()
        if (
            stat.S_ISLNK(root_before.st_mode)
            or not stat.S_ISDIR(root_before.st_mode)
            or not _stat_proof_matches(root_before, root_proof)
            or _has_acl(source_root)
        ):
            return False
        if not all(
            _source_asset_matches(source_root / relative, source_root, proof)
            for relative, proof in source_proofs.items()
        ):
            return False
        root_after = source_root.lstat()
        return _same_stat(root_before, root_after) and _stat_proof_matches(root_after, root_proof) and not _has_acl(source_root)
    except (BootstrapError, OSError, TypeError, ValueError):
        return False


def _operation_source_proof_gate(
    bindings: Sequence[Tuple[Path, Dict[str, Dict[str, Any]], Dict[str, Any]]],
) -> bool:
    """Require every P/E binding to remain the exact validated checkout."""
    return all(_source_binding_matches(root, source_proofs, root_proof) for root, source_proofs, root_proof in bindings)


def _validated_source_binding(root: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    source_root = _absolute_lexical(root)
    source_proofs = _VALIDATED_GUARDIAN_PROOFS.get(str(source_root))
    root_proof = _VALIDATED_ROOT_PROOFS.get(str(source_root))
    if (
        not isinstance(source_proofs, dict)
        or set(source_proofs) != set(GUARDIAN_REQUIRED_PATHS)
        or not all(isinstance(value, dict) for value in source_proofs.values())
        or not isinstance(root_proof, dict)
        or root_proof.get("kind") != "directory"
    ):
        raise BootstrapError("frozen Guardian source proof is unavailable")
    return source_proofs, root_proof


def _managed_target_trust(path: Path, anchor: Path, expected: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return one stable target proof, refusing identical but untrusted bytes."""
    try:
        target = _absolute_lexical(path)
        _strict_owned_path(target.parent, anchor)
        before = target.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode))
            or before.st_uid != os.getuid()
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or _has_acl(target)
        ):
            return None
        digest = _tree_hash(target)
        after = target.lstat()
        if not _same_stat(before, after) or _has_acl(target) or (expected is not None and digest != expected):
            return None
        return {"device": int(before.st_dev), "inode": int(before.st_ino), "sha256": digest}
    except (BootstrapError, OSError):
        return None


def _read_hash_fd(
    descriptor: int,
    state: Dict[str, int],
    prefix: bytes = b"",
    algorithm: Callable[[], Any] = hashlib.sha256,
) -> str:
    """Hash an already no-followed file descriptor with bounded, stable reads."""
    before = os.fstat(descriptor)
    digest = algorithm()
    digest.update(prefix)
    total = 0
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        state["file_bytes"] += len(chunk)
        if state["file_bytes"] > MAX_PLUGIN_TREE_BYTES:
            raise BootstrapError("managed tree is too large")
        digest.update(chunk)
    after = os.fstat(descriptor)
    if not _same_stat(before, after) or total != before.st_size:
        raise BootstrapError("managed tree changed during verification")
    return digest.hexdigest()


def _read_stable_bytes(
    path: Path,
    limit: int,
    anchor: Optional[Path] = None,
    expected_proof: Optional[Dict[str, Any]] = None,
    expected_root_proof: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Read bytes and, when supplied, bind them to frozen source identities."""
    if not isinstance(limit, int) or limit < 0:
        raise BootstrapError("bounded read limit is invalid")
    root_before: Optional[os.stat_result] = None
    if expected_root_proof is not None:
        if anchor is None:
            raise BootstrapError("frozen source root is unavailable")
        root_before = anchor.lstat()
        if (
            not stat.S_ISDIR(root_before.st_mode)
            or stat.S_ISLNK(root_before.st_mode)
            or expected_root_proof.get("kind") != "directory"
            or not _stat_proof_matches(root_before, expected_root_proof)
            or _has_acl(anchor)
        ):
            raise BootstrapError("frozen source root identity changed")
    _assert_safe_path(path, allow_missing=False, anchor=anchor)
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise BootstrapError("bounded file is too large or unsafe")
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not _same_stat(before, opened) or opened.st_size > limit:
            raise BootstrapError("bounded file changed before read")
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise BootstrapError("bounded file is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if not _same_stat(opened, after) or total != opened.st_size:
            raise BootstrapError("bounded file changed during read")
        content = b"".join(chunks)
        if expected_proof is not None:
            if not _stat_proof_matches(opened, expected_proof) or _sha256_bytes(content) != expected_proof.get("sha256"):
                raise BootstrapError("frozen source file identity changed")
            path_after = path.lstat()
            if not _same_stat(opened, path_after):
                raise BootstrapError("frozen source file path changed during read")
        if expected_root_proof is not None and root_before is not None:
            root_after = anchor.lstat() if anchor is not None else None
            if (
                root_after is None
                or not stat.S_ISDIR(root_after.st_mode)
                or not _same_stat(root_before, root_after)
                or not _stat_proof_matches(root_after, expected_root_proof)
                or _has_acl(anchor)
            ):
                raise BootstrapError("frozen source root changed during read")
        return content
    finally:
        os.close(descriptor)


def _read_bounded_file(path: Path, codex_home: Path) -> Optional[bytes]:
    try:
        return _read_stable_bytes(path, MAX_JSON_BYTES, codex_home)
    except (BootstrapError, OSError):
        return None


def _tree_hash_fd(descriptor: int, depth: int, state: Dict[str, int]) -> str:
    if depth > MAX_TREE_DEPTH:
        raise BootstrapError("managed tree is too deep")
    before_directory = os.fstat(descriptor)
    remaining = MAX_PLUGIN_TREE_ENTRIES - state["entries"]
    names: List[str] = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > remaining:
                raise BootstrapError("managed tree has too many entries")
    names.sort()
    children: List[Tuple[str, str, str]] = []
    for name in names:
        if state["entries"] >= MAX_PLUGIN_TREE_ENTRIES:
            raise BootstrapError("managed tree has too many entries")
        info_before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        state["entries"] += 1
        if stat.S_ISDIR(info_before.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                child_info = os.fstat(child_fd)
                if not _same_stat(info_before, child_info):
                    raise BootstrapError("managed tree changed during verification")
                child_hash = _tree_hash_fd(child_fd, depth + 1, state)
            finally:
                os.close(child_fd)
            info_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not _same_stat(info_before, info_after):
                raise BootstrapError("managed tree changed during verification")
            children.append(("dir", name, child_hash))
        elif stat.S_ISREG(info_before.st_mode):
            child_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            try:
                child_info = os.fstat(child_fd)
                if not _same_stat(info_before, child_info):
                    raise BootstrapError("managed tree changed during verification")
                child_hash = _read_hash_fd(child_fd, state)
            finally:
                os.close(child_fd)
            info_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not _same_stat(info_before, info_after):
                raise BootstrapError("managed tree changed during verification")
            children.append(("file", name, child_hash))
        else:
            raise BootstrapError("managed tree contains a special file")
    after_directory = os.fstat(descriptor)
    if not _same_stat(before_directory, after_directory):
        raise BootstrapError("managed tree changed during verification")
    payload = "".join("\0".join(item) + "\n" for item in children).encode("utf-8")
    return _sha256_bytes(payload)


def _tree_hash(path: Path) -> Optional[str]:
    """Hash a regular file/directory with bounded no-followed FD reads."""
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            return None
        state = {"entries": 1, "file_bytes": 0}
        if stat.S_ISREG(info.st_mode):
            descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(descriptor)
                if not _same_stat(info, opened):
                    return None
                digest = _read_hash_fd(descriptor, state)
            finally:
                os.close(descriptor)
            after = path.lstat()
            return digest if _same_stat(info, after) else None
        descriptor = os.open(
            str(path),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not _same_stat(info, opened):
                return None
            digest = _tree_hash_fd(descriptor, 0, state)
        finally:
            os.close(descriptor)
        after = path.lstat()
        return digest if _same_stat(info, after) else None
    except (BootstrapError, OSError, RecursionError, UnicodeError):
        return None


def _content_tree_records(
    directory_fd: int,
    prefix: bytes,
    records: List[Tuple[bytes, bytes, bytes]],
    counts: Dict[str, int],
    is_root: bool = True,
    depth: int = 0,
) -> None:
    """Collect a bounded, no-following-links plugin content tree by directory FD."""
    if depth > MAX_TREE_DEPTH:
        raise BootstrapError("plugin tree is too deep")
    before_directory = os.fstat(directory_fd)
    remaining = MAX_PLUGIN_TREE_ENTRIES - len(records)
    names: List[str] = []
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            name = entry.name
            if is_root and name == ".git":
                continue
            names.append(name)
            if len(names) > remaining:
                raise BootstrapError("plugin tree has too many entries")
    names.sort(key=os.fsencode)
    for name in names:
        if is_root and name == ".git":
            continue
        if len(records) >= MAX_PLUGIN_TREE_ENTRIES:
            raise BootstrapError("plugin tree has too many entries")
        name_bytes = os.fsencode(name)
        relative = name_bytes if not prefix else prefix + b"/" + name_bytes
        info_before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(info_before.st_mode):
            target = os.readlink(name, dir_fd=directory_fd)
            payload = os.fsencode(target)
            records.append((relative, b"L-", payload))
            counts["symlinks"] += 1
            info_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _same_stat(info_before, info_after):
                raise BootstrapError("plugin tree changed during verification")
            continue
        if stat.S_ISDIR(info_before.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            try:
                child_info = os.fstat(child_fd)
                if not _same_stat(info_before, child_info):
                    raise BootstrapError("plugin tree changed during verification")
                records.append((relative, b"D-", b""))
                counts["directories"] += 1
                _content_tree_records(child_fd, relative, records, counts, False, depth + 1)
            finally:
                os.close(child_fd)
            info_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _same_stat(info_before, info_after):
                raise BootstrapError("plugin tree changed during verification")
            continue
        if stat.S_ISREG(info_before.st_mode):
            child_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                child_info = os.fstat(child_fd)
                if not _same_stat(info_before, child_info):
                    raise BootstrapError("plugin tree changed during verification")
                payload_parts: List[bytes] = []
                total = 0
                while True:
                    chunk = os.read(child_fd, 1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if counts["file_bytes"] + total > MAX_PLUGIN_TREE_BYTES:
                        raise BootstrapError("plugin tree is too large")
                    payload_parts.append(chunk)
                payload = b"".join(payload_parts)
                after_read = os.fstat(child_fd)
                if not _same_stat(child_info, after_read):
                    raise BootstrapError("plugin tree changed during verification")
            finally:
                os.close(child_fd)
            kind = b"FX" if info_before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else b"FN"
            records.append((relative, kind, payload))
            counts["files"] += 1
            counts["file_bytes"] += len(payload)
            continue
        raise BootstrapError("plugin tree contains a special file")
    after_directory = os.fstat(directory_fd)
    if not _same_stat(before_directory, after_directory):
        raise BootstrapError("plugin tree changed during verification")


def _content_tree_proof(path: Path, codex_home: Path) -> Optional[Dict[str, Any]]:
    """Hash a plugin cache tree without following links or trusting its resolver claim."""
    try:
        path = _absolute_lexical(path)
        relative = _relative_path_string(path, codex_home)
        _assert_safe_path(path, allow_missing=False, anchor=codex_home)
        _strict_existing_parent_chain(path.parent, codex_home)
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or _has_acl(path)
        ):
            return None
        descriptor = os.open(
            str(path),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not _same_stat(info, opened):
                return None
            records: List[Tuple[bytes, bytes, bytes]] = []
            counts = {"directories": 0, "files": 0, "symlinks": 0, "file_bytes": 0}
            _content_tree_records(descriptor, b"", records, counts)
            if len(records) > MAX_PLUGIN_TREE_ENTRIES:
                return None
            after = os.fstat(descriptor)
            if not _same_stat(opened, after):
                return None
        finally:
            os.close(descriptor)
        digest = hashlib.sha256()
        digest.update(PLUGIN_TREE_ALGORITHM.encode("ascii") + b"\0")
        for relative_bytes, kind, payload in sorted(records, key=lambda item: item[0]):
            digest.update(b"E")
            digest.update(kind)
            digest.update(struct.pack(">Q", len(relative_bytes)))
            digest.update(relative_bytes)
            digest.update(struct.pack(">Q", len(payload)))
            digest.update(payload)
        return {
            "path": relative,
            "tree_sha256": digest.hexdigest(),
            "entries": len(records),
            "device": int(opened.st_dev),
            "inode": int(opened.st_ino),
            "mode": int(stat.S_IMODE(opened.st_mode)),
            "uid": int(opened.st_uid),
            "gid": int(opened.st_gid),
            "size": int(opened.st_size),
            "mtime_ns": int(getattr(opened, "st_mtime_ns", 0)),
            "ctime_ns": int(getattr(opened, "st_ctime_ns", 0)),
            **counts,
        }
    except (BootstrapError, OSError, UnicodeError, RecursionError, struct.error):
        return None


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _reject_source_symlinks(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BootstrapError("source asset cannot be inspected") from exc
    if stat.S_ISLNK(info.st_mode):
        raise BootstrapError("source contains a symlink")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise BootstrapError("source contains a special file")
    if stat.S_ISDIR(info.st_mode):
        try:
            for child in path.iterdir():
                _reject_source_symlinks(child)
        except RecursionError as exc:
            raise BootstrapError("source asset is too deeply nested") from exc


def _validate_urls(value: Any) -> None:
    """Reject non-HTTPS URLs and mutable refs in lock/marketplace data."""
    if isinstance(value, str):
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme or value.startswith("//"):
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise BootstrapError("dependency metadata contains an unsafe URL")
            if MUTABLE_REF_RE.search(value):
                raise BootstrapError("dependency metadata contains a mutable reference")
    elif isinstance(value, dict):
        for child in value.values():
            _validate_urls(child)
    elif isinstance(value, list):
        for child in value:
            _validate_urls(child)


def _validate_marketplace(
    lock: Dict[str, Any],
    marketplace_path: Path = MARKETPLACE_PATH,
    *,
    expected_source_proof: Optional[Dict[str, Any]] = None,
    expected_root_proof: Optional[Dict[str, Any]] = None,
    source_root: Optional[Path] = None,
) -> None:
    source_root = _absolute_lexical(source_root or Path(marketplace_path).parents[2])
    try:
        raw = _read_stable_bytes(
            marketplace_path,
            MAX_JSON_BYTES,
            source_root,
            expected_source_proof,
            expected_root_proof,
        )
        marketplace = _safe_json_loads(raw, "marketplace")
    except (OSError, BootstrapError) as exc:
        raise BootstrapError("marketplace is unreadable") from exc
    if not isinstance(marketplace, dict) or set(marketplace) != {"name", "interface", "plugins"}:
        raise BootstrapError("marketplace schema is unsupported")
    if marketplace.get("name") != "onebigmoon-codex-workflows":
        raise BootstrapError("marketplace identity is invalid")
    interface = marketplace.get("interface")
    if not isinstance(interface, dict) or set(interface) != {"displayName"} or not isinstance(interface.get("displayName"), str):
        raise BootstrapError("marketplace interface is invalid")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 3:
        raise BootstrapError("marketplace must contain exactly three plugins")
    expected_names = {"codex-workflow-guardian", "allinluna", "ponytail"}
    seen: set = set()
    expected_components = lock["components"]
    for item in plugins:
        if not isinstance(item, dict) or set(item) != {"name", "source", "policy", "category"}:
            raise BootstrapError("marketplace plugin schema is invalid")
        name = item.get("name")
        if name not in expected_names or name in seen:
            raise BootstrapError("marketplace plugin names are not unique")
        seen.add(name)
        if item.get("category") not in ("Developer Tools", "Productivity"):
            raise BootstrapError("marketplace plugin category is invalid")
        policy = item.get("policy")
        if policy != {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}:
            raise BootstrapError("marketplace plugin policy is invalid")
        source = item.get("source")
        if name == "codex-workflow-guardian":
            if source != {"source": "local", "path": "./"}:
                raise BootstrapError("Guardian marketplace source is invalid")
        elif name == "allinluna":
            expected = expected_components.get(name, {})
            if _strict_allinluna_plugin_path(expected.get("plugin_path")) is None:
                raise BootstrapError("All in Luna plugin path is invalid")
            expected_source = {
                "source": "git-subdir",
                "url": expected.get("repository"),
                "sha": expected.get("commit"),
                "path": expected.get("plugin_path"),
            }
            if source != expected_source:
                raise BootstrapError("All in Luna marketplace source is not pinned")
        else:
            expected = expected_components.get(name, {})
            expected_source = {
                "source": "url",
                "url": expected.get("repository"),
                "sha": expected.get("commit"),
            }
            if source != expected_source:
                raise BootstrapError("Ponytail marketplace source is not pinned")
    if seen != expected_names:
        raise BootstrapError("marketplace plugin set is incomplete")
    _validate_urls(marketplace)


def _load_lock(
    repository_root: Optional[Path] = None,
    expected_source_proofs: Optional[Dict[str, Dict[str, Any]]] = None,
    expected_root_proof: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if repository_root is None:
        lock_path = _absolute_lexical(LOCK_PATH)
        source_root = lock_path.parent
    else:
        source_root = _absolute_lexical(repository_root)
        lock_path = source_root / "workflow-dependencies.lock.json"
    expected_lock_proof = None
    expected_marketplace_proof = None
    if expected_source_proofs is not None:
        expected_lock_proof = expected_source_proofs.get("workflow-dependencies.lock.json")
        expected_marketplace_proof = expected_source_proofs.get(".agents/plugins/marketplace.json")
        if not isinstance(expected_lock_proof, dict) or not isinstance(expected_marketplace_proof, dict):
            raise BootstrapError("frozen Guardian source proof is incomplete")
    try:
        value = _safe_json_loads(
            _read_stable_bytes(
                lock_path,
                MAX_JSON_BYTES,
                source_root,
                expected_lock_proof,
                expected_root_proof,
            ),
            "dependency lock",
        )
    except (OSError, BootstrapError) as exc:
        raise BootstrapError("dependency lock is unreadable") from exc
    if not isinstance(value, dict) or value.get("lock_version") != 1:
        raise BootstrapError("dependency lock schema is unsupported")
    _validate_lock_shape(value)
    _validate_profile_graph(value)
    _validate_receipt_contract(value)
    _validate_host_capability_contract(value)
    mutable: List[str] = []

    def visit(item: Any, key: str = "") -> None:
        if isinstance(item, str) and (MUTABLE_REF_RE.search(item) or "@latest" in item.lower()):
            mutable.append(key or "value")
        elif isinstance(item, dict):
            for child_key, child_value in item.items():
                visit(child_value, f"{key}.{child_key}" if key else str(child_key))
        elif isinstance(item, list):
            for index, child_value in enumerate(item):
                visit(child_value, f"{key}[{index}]")

    visit(value)
    if mutable:
        raise BootstrapError("dependency lock contains a mutable reference")
    _validate_urls(value)
    components = value.get("components")
    required_components = {"codex_cli", "python", "guardian_plugin", "allinluna", "ponytail", "headroom", "node"}
    if not isinstance(components, dict) or set(components) != required_components:
        raise BootstrapError("dependency lock components are incomplete")
    codex_lock = components.get("codex_cli")
    if not isinstance(codex_lock, dict) or codex_lock.get("minimum_version") != "0.146.0" or codex_lock.get("verified_version") != "0.146.0":
        raise BootstrapError("dependency lock Codex minimum is invalid")
    python_lock = components.get("python")
    if not isinstance(python_lock, dict) or python_lock.get("minimum_version") != "3.9":
        raise BootstrapError("dependency lock Python minimum is invalid")
    for name in ("allinluna", "ponytail"):
        component = components.get(name)
        if not isinstance(component, dict) or not COMMIT_RE.fullmatch(str(component.get("commit", ""))):
            raise BootstrapError("dependency lock commit pin is invalid")
        if not isinstance(component.get("repository"), str) or not component["repository"].startswith("https://"):
            raise BootstrapError("dependency lock repository pin is invalid")
    allinluna_lock = components.get("allinluna", {})
    if (
        allinluna_lock.get("content_tree_algorithm") != PLUGIN_TREE_ALGORITHM
        or not SHA256_RE.fullmatch(str(allinluna_lock.get("content_tree_sha256", "")))
        or not all(isinstance(allinluna_lock.get(key), int) and not isinstance(allinluna_lock.get(key), bool) and allinluna_lock.get(key) >= 0 for key in ("content_tree_entries", "content_tree_directories", "content_tree_files", "content_tree_symlinks", "content_tree_file_bytes"))
        or allinluna_lock.get("content_tree_entries") != allinluna_lock.get("content_tree_directories", 0) + allinluna_lock.get("content_tree_files", 0) + allinluna_lock.get("content_tree_symlinks", 0)
    ):
        raise BootstrapError("All in Luna content-tree lock is invalid")
    wheel = components.get("allinluna", {}).get("pypi", {}).get("wheel", {})
    if not isinstance(wheel, dict) or not SHA256_RE.fullmatch(str(wheel.get("sha256", ""))):
        raise BootstrapError("dependency lock wheel pin is invalid")
    pypi = components.get("allinluna", {}).get("pypi", {})
    if _validated_wheel_filename(wheel, pypi) is None:
        raise BootstrapError("dependency lock wheel filename is invalid")
    wheel_url = str(wheel.get("url", ""))
    parsed_wheel = urllib.parse.urlparse(wheel_url)
    if parsed_wheel.scheme != "https" or parsed_wheel.hostname != "files.pythonhosted.org":
        raise BootstrapError("dependency lock wheel URL is not an approved HTTPS host")
    _validate_marketplace(
        value,
        source_root / ".agents" / "plugins" / "marketplace.json",
        expected_source_proof=expected_marketplace_proof,
        expected_root_proof=expected_root_proof,
        source_root=source_root,
    )
    return value


def _run_argv(
    argv: Sequence[Any],
    codex_home: Path,
    cwd: Path,
    timeout: int = 20,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    """Run one immutable LaunchSpec vector with a sealed child environment."""
    if not argv:
        return -1, "", ""
    first = argv[0]
    if isinstance(first, LaunchSpec):
        spec = first
        if spec.trust_scope == "managed-venv":
            try:
                if not spec.trust_anchor:
                    return -1, "", ""
                frozen_anchor = Path(spec.trust_anchor)
                if Path(os.path.realpath(str(_absolute_lexical(codex_home)))) != frozen_anchor:
                    return -1, "", ""
                current_path, current_chain = _managed_venv_program_proof(spec.path, frozen_anchor)
                current = spec.path.lstat()
                if (
                    spec.device <= 0
                    or spec.inode <= 0
                    or not _identity_matches(current, {"device": spec.device, "inode": spec.inode})
                    or current_path != spec.path
                    or current_chain != spec.trust_chain
                    or _sha256_file(spec.path, _launch_hash_limit(spec.kind)) != spec.sha256
                    or platform.system() == "Darwin" and not _native_macho(spec.path)
                ):
                    return -1, "", ""
            except (BootstrapError, OSError):
                return -1, "", ""
        elif spec.trust_scope:
            return -1, "", ""
        elif platform.system() == "Darwin":
            try:
                current = spec.path.lstat()
                if (
                    spec.device <= 0
                    or spec.inode <= 0
                    or not _identity_matches(current, {"device": spec.device, "inode": spec.inode})
                    or _safe_program(spec.path) != spec.path
                    or _sha256_file(spec.path, _launch_hash_limit(spec.kind)) != spec.sha256
                    or not _native_macho(spec.path)
                ):
                    return -1, "", ""
            except (BootstrapError, OSError):
                return -1, "", ""
        command = [str(spec.path), *[str(item) for item in argv[1:]]]
    else:
        # Legacy raw vectors remain usable by Linux-only unit fixtures.  Real
        # macOS execution is fail-closed and must pass LaunchSpec.
        if platform.system() == "Darwin":
            return -1, "", ""
        try:
            path = _absolute_lexical(Path(str(first)))
        except (TypeError, ValueError):
            return -1, "", ""
        spec = LaunchSpec(path, "legacy-test", path.parent)
        command = [str(item) for item in argv]
    if env_overrides is not None:
        if set(env_overrides) - set(INTERNAL_GIT_ENV) or any(
            str(env_overrides.get(key)) != value for key, value in INTERNAL_GIT_ENV.items() if key in env_overrides
        ):
            return -1, "", ""
    try:
        _assert_safe_path(_absolute_lexical(cwd), allow_missing=False)
        database_home = _account_home()
        if database_home is None:
            return -1, "", ""
    except (OSError, BootstrapError):
        return -1, "", ""
    environment = {
        "CODEX_HOME": str(_absolute_lexical(codex_home)),
        "HOME": str(database_home),
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PATH": os.pathsep.join((str(spec.git_dir), SYSTEM_EXEC_PATH)),
    }
    if env_overrides:
        environment.update({str(key): str(value) for key, value in env_overrides.items()})
    process: Optional[subprocess.Popen] = None
    selector: Optional[selectors.BaseSelector] = None
    stdout = bytearray()
    stderr = bytearray()
    overflow = False
    timed_out = False
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = key.data
                buffer.extend(chunk)
                if len(stdout) + len(stderr) > MAX_COMMAND_OUTPUT:
                    overflow = True
                    break
            if overflow:
                break
        if timed_out or overflow:
            _kill_process_group(process)
        try:
            process.wait(timeout=PROCESS_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            try:
                process.wait(timeout=PROCESS_CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired:
                return -1, "", ""
    except (OSError, subprocess.SubprocessError, ValueError):
        if process is not None:
            _kill_process_group(process)
        return -1, "", ""
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
    if timed_out or overflow or process is None:
        return -1, "", ""
    return process.returncode, bytes(stdout).decode("utf-8", errors="replace"), bytes(stderr).decode("utf-8", errors="replace")


def _run_codex_plugin_argv(
    argv: Sequence[Any],
    codex_home: Path,
    cwd: Path,
    timeout: int = 20,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    """Run only the immutable, non-destructive Plugin command vocabulary."""
    if not isinstance(argv, (list, tuple)) or not argv or not isinstance(argv[0], LaunchSpec):
        return -1, "", ""
    spec = argv[0]
    if spec.kind != "codex" or any(not isinstance(item, str) for item in argv[1:]):
        return -1, "", ""
    tokens = list(argv[1:])
    allowed = tokens in (
        ["plugin", "list", "--json"],
        ["plugin", "list", "--available", "--json"],
        ["plugin", "marketplace", "list", "--json"],
    )
    if len(tokens) == 4 and tokens[:2] == ["plugin", "add"] and tokens[3] == "--json":
        allowed = tokens[2] in AUTO_INSTALL_PLUGIN_SELECTORS
    if not allowed:
        return -1, "", ""
    return _run_argv(argv, codex_home, cwd, timeout=timeout, env_overrides=env_overrides)


def _kill_process_group(process: subprocess.Popen) -> None:
    """Kill the bounded process group; a descendant that calls setsid can escape it."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _resolve_codex(codex_bin: Optional[str]) -> Optional[Path]:
    spec = _codex_launch_spec(codex_bin)
    return spec.path if spec is not None else None


def _validate_codex_home(home: Path) -> None:
    """Require an explicit, existing, private directory owned by this UID."""
    _assert_safe_path(home, allow_missing=False)
    info = home.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise BootstrapError("CODEX_HOME is not a directory")
    if info.st_uid != os.getuid():
        raise BootstrapError("CODEX_HOME is not owned by the current user")
    # A normal persistent macOS home is often 0755.  It is safe for this
    # purpose as long as group/other users cannot write it; all managed
    # descendants are checked separately and are created private by default.
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BootstrapError("CODEX_HOME must not be writable by group or other users")
    if _has_acl(home):
        raise BootstrapError("CODEX_HOME must not have an extended ACL")


def _codex_component(
    lock: Dict[str, Any],
    codex_home: Path,
    codex_bin: Optional[str],
    repository_root: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Optional[Path]]:
    configured = lock.get("components", {}).get("codex_cli", {})
    expected_text = str(configured.get("verified_version", "0.146.0"))
    expected = _version_tuple(expected_text)
    minimum = _version_tuple(str(configured.get("minimum_version", expected_text))) or expected
    executable = _resolve_codex(codex_bin)
    if executable is None:
        return {"name": "codex-cli", "status": "unavailable", "reason": "executable not found"}, None
    spec = _codex_launch_spec(str(executable), codex_home)
    if spec is None:
        return {"name": "codex-cli", "status": "unavailable", "reason": "Codex executable trust verification failed"}, None
    code, stdout, stderr = _run_argv([spec, "--version"], codex_home, repository_root or REPOSITORY_ROOT)
    del stderr
    actual = _version_tuple(stdout)
    if code != 0 or actual is None:
        return {
            "name": "codex-cli",
            "status": "unavailable",
            "reason": "version command failed",
        }, executable
    if minimum is not None and actual < minimum:
        return {
            "name": "codex-cli",
            "status": "unavailable",
            "version": ".".join(str(part) for part in actual),
            "minimum_version": str(configured.get("minimum_version", expected_text)),
            "reason": "Codex CLI is older than the locked minimum",
        }, executable
    skew = expected is not None and actual != expected
    return {
        "name": "codex-cli",
        "status": "present",
        "version": ".".join(str(part) for part in actual),
        "expected_version": expected_text,
        "version_skew": skew,
    }, executable


def _plugin_entry(value: Any, label: str) -> Optional[Dict[str, Any]]:
    keys = {
        "pluginId",
        "name",
        "marketplaceName",
        "version",
        "installed",
        "enabled",
        "source",
        "marketplaceSource",
        "installPolicy",
        "authPolicy",
    }
    if not isinstance(value, dict) or set(value) not in (keys, keys | {"installedPath"}):
        return None
    if not all(isinstance(value.get(key), str) for key in ("pluginId", "name", "marketplaceName", "installPolicy", "authPolicy")):
        return None
    if not isinstance(value.get("installed"), bool) or not isinstance(value.get("enabled"), bool):
        return None
    if "installedPath" in value and not isinstance(value.get("installedPath"), str):
        return None
    if value.get("pluginId") != f"{value['name']}@{value['marketplaceName']}":
        return None
    version = value.get("version")
    if version is None:
        if label != "available" or value["installed"] or value["enabled"]:
            return None
    elif not isinstance(version, str):
        return None
    source = value.get("source")
    if not isinstance(source, dict) or not set(source) <= {"source", "path", "url", "sha", "treeSha256"}:
        return None
    if not isinstance(source.get("source"), str) or not all(isinstance(source.get(key), str) for key in set(source) - {"source"}):
        return None
    marketplace_source = value.get("marketplaceSource")
    if not isinstance(marketplace_source, dict) or set(marketplace_source) != {"sourceType", "source"}:
        return None
    if marketplace_source.get("sourceType") not in ("git", "local") or not isinstance(marketplace_source.get("source"), str):
        return None
    return value


def _marketplace_resolver_payload(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, dict) or set(value) != {"marketplaces"} or not isinstance(value["marketplaces"], list):
        return None
    entries: List[Dict[str, Any]] = []
    for item in value["marketplaces"]:
        if not isinstance(item, dict) or item.get("name") != WORKFLOW_MARKETPLACE:
            continue
        if any(entry.get("name") == WORKFLOW_MARKETPLACE for entry in entries):
            return None
        if set(item) != {"name", "root", "marketplaceSource"}:
            return None
        source = item.get("marketplaceSource")
        if not isinstance(item.get("name"), str) or not isinstance(item.get("root"), str):
            return None
        if not isinstance(source, dict) or set(source) != {"sourceType", "source"}:
            return None
        if source.get("sourceType") not in ("git", "local") or not isinstance(source.get("source"), str):
            return None
        entries.append(item)
    return entries


def _resolver_marketplace_root(
    codex_home: Path,
    executable: Optional[Path],
    repository_root: Optional[Path] = None,
) -> Optional[Path]:
    """Read one canonical marketplace root from the resolver, never from setup globals."""
    if executable is None:
        return None
    cwd = _absolute_lexical(repository_root or codex_home)
    codex_spec = _codex_launch_spec(str(executable), codex_home)
    if codex_spec is None:
        return None
    code, stdout, _ = _run_codex_plugin_argv(
        [codex_spec, "plugin", "marketplace", "list", "--json"],
        codex_home,
        cwd,
    )
    if code != 0:
        return None
    try:
        payload = _safe_json_loads(stdout, "marketplace resolver inspection")
    except BootstrapError:
        return None
    marketplaces = _marketplace_resolver_payload(payload)
    if marketplaces is None:
        return None
    matches = [item for item in marketplaces if item.get("name") == WORKFLOW_MARKETPLACE]
    if len(matches) != 1:
        return None
    item = matches[0]
    source = item.get("marketplaceSource", {})
    if source.get("sourceType") != "git" or _canonical_repository(source.get("source")) != _canonical_repository(CANONICAL_GUARDIAN_REPOSITORY):
        return None
    try:
        root = _absolute_lexical(Path(str(item.get("root"))))
        root.relative_to(_absolute_lexical(codex_home))
        _assert_safe_path(root, allow_missing=False, anchor=codex_home)
    except (BootstrapError, OSError, ValueError):
        return None
    return root


def _verified_guardian_root(
    codex_home: Path,
    executable: Optional[Path],
    guardian_ref: Optional[str],
    git_bin: Optional[Path],
) -> Tuple[Optional[Path], str]:
    root = _resolver_marketplace_root(codex_home, executable)
    if root is None:
        return None, "canonical Guardian marketplace root is unavailable"
    prior_root_proof = _VALIDATED_ROOT_PROOFS.get(str(_absolute_lexical(root)))
    valid, reason = _validate_git_checkout(
        root,
        codex_home,
        git_bin,
        guardian_ref,
        expected_root_proof=prior_root_proof,
    )
    return (root, reason) if valid else (None, reason)


def _guardian_cache_root(codex_home: Path, lock: Dict[str, Any]) -> Optional[Path]:
    version = lock.get("components", {}).get("guardian_plugin", {}).get("version")
    if not isinstance(version, str) or not GUARDIAN_VERSION_RE.fullmatch(version):
        return None
    return _absolute_lexical(
        codex_home / "plugins" / "cache" / WORKFLOW_MARKETPLACE
        / "codex-workflow-guardian" / version
    )


def _strict_allinluna_plugin_path(value: Any) -> Optional[str]:
    if value in ("plugins/allinluna", "./plugins/allinluna"):
        return "plugins/allinluna"
    return None


def _allinluna_cache_root(codex_home: Path, lock: Dict[str, Any]) -> Optional[Path]:
    version = lock.get("components", {}).get("allinluna", {}).get("version")
    if not isinstance(version, str) or PLUGIN_VERSION_RE.fullmatch(version) is None:
        return None
    return _absolute_lexical(
        codex_home / "plugins" / "cache" / WORKFLOW_MARKETPLACE / "allinluna" / version
    )


def _strict_locked_plugin_relative_path(
    name: str,
    raw: Any,
    codex_home: Path,
    lock: Dict[str, Any],
) -> Optional[str]:
    """Accept only the lock coordinate itself, relative or its exact absolute form."""
    if name != "allinluna" or not isinstance(raw, str) or not raw:
        return None
    root = _allinluna_cache_root(codex_home, lock)
    if root is None:
        return None
    try:
        relative = _relative_path_string(root, codex_home)
        absolute = str(_absolute_lexical(root))
    except (BootstrapError, OSError, ValueError):
        return None
    if raw not in (relative, absolute):
        return None
    try:
        _safe_relative_path(codex_home, relative)
    except (BootstrapError, OSError, ValueError):
        return None
    return relative


def _execution_guardian_root(codex_home: Path, lock: Dict[str, Any]) -> Optional[Path]:
    """Derive the exact versioned installed Guardian root from this script."""
    expected = _guardian_cache_root(codex_home, lock)
    if expected is None:
        return None
    actual = _absolute_lexical(SCRIPT_PATH)
    try:
        actual.relative_to(actual.parents[3] / GUARDIAN_SCRIPT_RELATIVE)
    except (ValueError, IndexError):
        return None
    root = actual.parents[3]
    return root if root == expected else None


def _script_bound_to_guardian(guardian_root: Path) -> bool:
    expected = _absolute_lexical(guardian_root / GUARDIAN_SCRIPT_RELATIVE)
    actual = _absolute_lexical(SCRIPT_PATH)
    if actual != expected:
        return False
    try:
        _assert_safe_path(actual, allow_missing=False, anchor=guardian_root)
        actual_info = actual.lstat()
        expected_info = expected.lstat()
    except (BootstrapError, OSError):
        return False
    return (
        stat.S_ISREG(actual_info.st_mode)
        and not stat.S_ISLNK(actual_info.st_mode)
        and _identity_matches(actual_info, _identity(expected_info))
    )


def _plugin_list_payload(value: Any, label: str) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, dict) or set(value) != {"installed", "available"}:
        return None
    items = value.get(label)
    if not isinstance(items, list):
        return None
    entries: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("name") not in TRACKED_PLUGIN_NAMES:
            plugin_id = item.get("pluginId")
            if not isinstance(plugin_id, str) or plugin_id.split("@", 1)[0] not in TRACKED_PLUGIN_NAMES:
                continue
        parsed = _plugin_entry(item, label)
        if parsed is None:
            return None
        entries.append(parsed)
    return entries


def _plugin_source_matches(name: str, entry: Dict[str, Any], lock: Dict[str, Any]) -> bool:
    if entry.get("pluginId") != f"{name}@{WORKFLOW_MARKETPLACE}" or entry.get("name") != name:
        return False
    if entry.get("marketplaceName") != WORKFLOW_MARKETPLACE:
        return False
    expected_marketplace = lock.get("components", {}).get("guardian_plugin", {})
    expected_marketplace_repo = _canonical_repository(expected_marketplace.get("repository"))
    marketplace_source = entry.get("marketplaceSource", {})
    if not isinstance(marketplace_source, dict) or marketplace_source.get("sourceType") != "git" or _canonical_repository(marketplace_source.get("source")) != expected_marketplace_repo:
        return False
    source = entry.get("source", {})
    if not isinstance(source, dict):
        return False
    component_name = "guardian_plugin" if name == "codex-workflow-guardian" else name
    component = lock.get("components", {}).get(component_name, {})
    expected_version = str(component.get("version", ""))
    actual_version = entry.get("version")
    if actual_version is None:
        if (
            name not in IMMUTABLE_PLUGIN_NAMES
            or entry.get("installed")
            or entry.get("enabled")
            or source.get("sha") != component.get("commit")
        ):
            return False
    elif not expected_version or _normalized_distribution_version(actual_version) != _normalized_distribution_version(expected_version):
        return False
    if name == "codex-workflow-guardian":
        return source.get("source") == "local" and isinstance(source.get("path"), str)
    expected_repo = _canonical_repository(component.get("repository"))
    actual_repo = _canonical_repository(source.get("url"))
    if name == "allinluna":
        expected_path = _strict_allinluna_plugin_path(component.get("plugin_path"))
        actual_path = _strict_allinluna_plugin_path(source.get("path"))
        return (
            source.get("source") == "git-subdir"
            and actual_repo == expected_repo
            and expected_path is not None
            and actual_path is not None
            and actual_path == expected_path
            and (source.get("sha") is None or source.get("sha") == component.get("commit"))
        )
    return (
        source.get("source") in ("url", "git")
        and actual_repo == expected_repo
        and not source.get("path")
        and (source.get("sha") is None or source.get("sha") == component.get("commit"))
    )


def _plugin_policy_matches(name: str, entry: Dict[str, Any]) -> bool:
    """Require install policy; resolver enabled state is not hook trust evidence."""
    del name
    return entry.get("installPolicy") == "AVAILABLE" and entry.get("authPolicy") == "ON_INSTALL"


def _validate_dependency_git_checkout(
    root: Path,
    codex_home: Path,
    git_bin: Optional[Path],
    expected_commit: str,
    expected_repository: str,
) -> Optional[str]:
    """Return an observed clean checkout HEAD for a pinned dependency."""
    if git_bin is None or not COMMIT_RE.fullmatch(expected_commit):
        return None
    try:
        root = _absolute_lexical(root)
        root.relative_to(_absolute_lexical(codex_home))
        _assert_safe_path(root, allow_missing=False, anchor=codex_home)
        root_info = root.lstat()
        git_dir = root / ".git"
        _assert_safe_path(git_dir, allow_missing=False, anchor=codex_home)
        git_info = git_dir.lstat()
        if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(git_info.st_mode) or stat.S_ISLNK(git_info.st_mode):
            return None
    except (BootstrapError, OSError, ValueError):
        return None
    head = _git_output(git_bin, codex_home, root, ["rev-parse", "--verify", "HEAD^{commit}"])
    if head is None or head.lower() != expected_commit.lower():
        return None
    origin = _git_output(git_bin, codex_home, root, ["config", "--local", "--get-all", "remote.origin.url"])
    if origin is None or _canonical_repository(origin) != _canonical_repository(expected_repository):
        return None
    replacements = _git_output(git_bin, codex_home, root, ["for-each-ref", "--format=%(refname)", "refs/replace/"])
    if replacements is None or replacements:
        return None
    if not _raw_git_checkout_clean(root, codex_home, git_bin, expected_commit):
        return None
    return head.lower()


def _plugin_observed_relative_path(
    name: str,
    entry: Dict[str, Any],
    codex_home: Path,
    lock: Dict[str, Any],
    known_path: Optional[str],
    codex_version: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Resolve one row/receipt path without guessing outside a fixed coordinate."""
    row_path: Optional[str] = None
    if "installedPath" in entry:
        raw = entry.get("installedPath")
        if not isinstance(raw, str) or not raw:
            return None
        row_path = raw
    normalized: List[str] = []
    for raw in (row_path, known_path):
        if raw is None:
            continue
        if name == "allinluna":
            exact = _strict_locked_plugin_relative_path(name, raw, codex_home, lock)
            if exact is None:
                return None
            normalized.append(exact)
            continue
        try:
            candidate = Path(raw)
            absolute = _absolute_lexical(candidate if candidate.is_absolute() else codex_home / candidate)
            relative = _relative_path_string(absolute, codex_home)
            _safe_relative_path(codex_home, relative)
        except (BootstrapError, OSError, ValueError):
            return None
        normalized.append(relative)
    coordinate: Optional[str] = None
    if name == "allinluna":
        root = _allinluna_cache_root(codex_home, lock)
        if root is None:
            return None
        try:
            coordinate = _relative_path_string(root, codex_home)
        except BootstrapError:
            return None
        if any(value != coordinate for value in normalized):
            return None
        if not normalized:
            expected = lock.get("components", {}).get("codex_cli", {}).get("verified_version")
            if (
                not isinstance(codex_version, dict)
                or codex_version.get("status") != "present"
                or codex_version.get("version") != expected
                or codex_version.get("version_skew") is not False
            ):
                return None
        return coordinate
    if not normalized:
        return None
    if len(set(normalized)) != 1:
        return None
    return normalized[0]


def _plugin_install_proof(
    name: str,
    entry: Dict[str, Any],
    codex_home: Path,
    lock: Dict[str, Any],
    git_bin: Optional[Path] = None,
    installed_path: Optional[str] = None,
    codex_version: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return only observed path, commit, and cache content proof."""
    if not entry.get("installed"):
        return None
    relative = _plugin_observed_relative_path(name, entry, codex_home, lock, installed_path, codex_version)
    if relative is None:
        return None
    try:
        path = _safe_relative_path(codex_home, relative)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            return None
    except (BootstrapError, OSError, ValueError):
        return None
    component_name = "guardian_plugin" if name == "codex-workflow-guardian" else name
    component = lock.get("components", {}).get(component_name, {})
    source = entry.get("source", {})
    expected_commit = str(component.get("commit", ""))
    observed_source_sha = source.get("sha")
    if observed_source_sha is not None and observed_source_sha != expected_commit:
        return None
    proof: Dict[str, Any] = {"path": relative}
    if name == "ponytail":
        observed_commit = _validate_dependency_git_checkout(path, codex_home, git_bin, expected_commit, str(component.get("repository", "")))
        if observed_commit is None:
            return None
        if observed_source_sha is not None and observed_source_sha != observed_commit:
            return None
        proof["commit"] = observed_commit
        return proof
    expected_tree = component.get("content_tree_sha256")
    if component.get("content_tree_algorithm") != PLUGIN_TREE_ALGORITHM or not isinstance(expected_tree, str):
        return None
    observed_tree = _content_tree_proof(path, codex_home)
    if not isinstance(observed_tree, dict) or observed_tree.get("tree_sha256") != expected_tree:
        return None
    for key in ("entries", "directories", "files", "symlinks", "file_bytes"):
        if observed_tree.get(key) != component.get("content_tree_" + key):
            return None
    if not isinstance(observed_source_sha, str) or observed_source_sha != expected_commit:
        return None
    proof["commit"] = observed_source_sha
    proof["tree_sha256"] = observed_tree["tree_sha256"]
    proof["expected_tree_sha256"] = expected_tree
    proof["tree_entries"] = observed_tree["entries"]
    proof["tree_file_bytes"] = observed_tree["file_bytes"]
    proof["tree_proof"] = observed_tree
    return proof


def _safe_plugin_hit(entry: Dict[str, Any], location: str, verified: bool) -> Dict[str, Any]:
    return {
        "plugin": entry.get("name", "invalid") if entry.get("name") in {"codex-workflow-guardian", "allinluna", "ponytail"} else "other",
        "location": location,
        "marketplace": "expected" if entry.get("marketplaceName") == "onebigmoon-codex-workflows" else "foreign",
        "source": "verified" if verified else "mismatch",
        "installed": bool(entry.get("installed")),
        "enabled": bool(entry.get("enabled")),
    }


def _plugin_command_component(
    codex_home: Path,
    executable: Optional[Path],
    lock: Dict[str, Any],
    guardian_ref: Optional[str],
    git_bin: Optional[Path] = None,
    known_installed_paths: Optional[Dict[str, str]] = None,
    repository_root: Optional[Path] = None,
    include_tree_proof: bool = False,
    validated_source_proofs: Optional[Dict[str, Dict[str, Any]]] = None,
    validated_root_proof: Optional[Dict[str, Any]] = None,
    codex_version: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = {"name": "codex-plugin-command", "command": "plugin list --json; plugin list --available --json; plugin marketplace list --json"}
    if (validated_source_proofs is None) != (validated_root_proof is None):
        return dict(base, status="conflict", reason="Guardian source proof binding is incomplete")
    if validated_source_proofs is not None:
        if repository_root is None or not _source_binding_matches(repository_root, validated_source_proofs, validated_root_proof):
            return dict(base, status="conflict", reason="verified Guardian source changed before plugin inspection")
    if executable is None:
        return dict(base, status="unavailable", reason="Codex CLI unavailable")
    codex_spec = _codex_launch_spec(str(executable), codex_home, git_bin)
    if codex_spec is None:
        return dict(base, status="unavailable", reason="Codex executable trust verification failed")
    responses: List[Tuple[str, Sequence[Any]]] = [
        ("installed", [codex_spec, "plugin", "list", "--json"]),
        ("available", [codex_spec, "plugin", "list", "--available", "--json"]),
        ("marketplaces", [codex_spec, "plugin", "marketplace", "list", "--json"]),
    ]
    payloads: Dict[str, Any] = {}
    for label, argv in responses:
        code, stdout, _ = _run_codex_plugin_argv(argv, codex_home, repository_root or REPOSITORY_ROOT)
        if code != 0:
            return dict(base, status="unavailable", reason="plugin inspection failed")
        try:
            payloads[label] = _safe_json_loads(stdout, f"{label} plugin inspection")
        except BootstrapError:
            return dict(base, status="unavailable", reason="plugin inspection was not JSON")
    installed = _plugin_list_payload(payloads["installed"], "installed")
    available = _plugin_list_payload(payloads["available"], "available")
    marketplaces = _marketplace_resolver_payload(payloads["marketplaces"])
    if installed is None or available is None or marketplaces is None:
        return dict(base, status="conflict", reason="resolver JSON schema is not the strict Codex 0.146 schema")
    expected_name = "onebigmoon-codex-workflows"
    matches = [item for item in marketplaces if item["name"] == expected_name]
    if len(matches) != 1:
        return dict(base, status="unavailable", reason="canonical Guardian marketplace is not unique")
    marketplace = matches[0]
    marketplace_source = marketplace["marketplaceSource"]
    root_value = marketplace["root"]
    try:
        root = _absolute_lexical(Path(root_value))
        root.relative_to(_absolute_lexical(codex_home))
    except (ValueError, OSError):
        return dict(base, status="conflict", reason="marketplace root is outside CODEX_HOME")
    if repository_root is not None and root != _absolute_lexical(repository_root):
        return dict(base, status="conflict", reason="resolver marketplace root changed during verification")
    if validated_source_proofs is not None and not _source_binding_matches(root, validated_source_proofs, validated_root_proof):
        return dict(base, status="conflict", reason="verified Guardian source changed during plugin inspection")
    if marketplace_source["sourceType"] != "git":
        return dict(base, status="configured-unverified", reason="local marketplace source is development-only")
    guardian_repo = lock.get("components", {}).get("guardian_plugin", {}).get("repository", "")
    if (
        _canonical_repository(marketplace_source["source"]) != _canonical_repository(guardian_repo)
        or _canonical_repository(guardian_repo) != _canonical_repository(CANONICAL_GUARDIAN_REPOSITORY)
    ):
        return dict(base, status="conflict", reason="marketplace repository is not canonical")
    guardian_entries = [item for item in installed if item["name"] == "codex-workflow-guardian" and item["installed"]]
    if len(guardian_entries) != 1:
        return dict(base, status="unavailable", reason="Guardian is not exactly one installed plugin")
    guardian = guardian_entries[0]
    expected_cache_root = _guardian_cache_root(codex_home, lock)
    if expected_cache_root is None:
        return dict(base, status="conflict", reason="locked Guardian cache coordinate is invalid")
    if "installedPath" in guardian:
        try:
            reported = Path(guardian["installedPath"])
            installed_root = _absolute_lexical(reported if reported.is_absolute() else codex_home / reported)
            if installed_root != expected_cache_root:
                return dict(base, status="conflict", reason="installed Guardian path is not the locked cache coordinate")
            _assert_safe_path(installed_root, allow_missing=False, anchor=codex_home)
            installed_info = installed_root.lstat()
            if stat.S_ISLNK(installed_info.st_mode) or not stat.S_ISDIR(installed_info.st_mode):
                return dict(base, status="conflict", reason="installed Guardian path is unsafe")
        except (BootstrapError, OSError, ValueError):
            return dict(base, status="conflict", reason="installed Guardian path is unsafe")
    if guardian["source"].get("source") != "local" or _absolute_lexical(Path(guardian["source"].get("path", ""))) != root:
        return dict(base, status="conflict", reason="installed Guardian source root does not match marketplace root")
    try:
        if validated_source_proofs is None:
            prior_root_proof = _VALIDATED_ROOT_PROOFS.get(str(_absolute_lexical(root)))
            git_ok, git_reason = _validate_git_checkout(
                root,
                codex_home,
                git_bin,
                guardian_ref,
                expected_root_proof=prior_root_proof,
            )
            if not git_ok:
                return dict(base, status="configured-unverified", reason=git_reason)
            source_proofs, root_proof = _validated_source_binding(root)
        else:
            source_proofs, root_proof = validated_source_proofs, validated_root_proof
        _validate_marketplace(
            lock,
            root / ".agents" / "plugins" / "marketplace.json",
            expected_source_proof=source_proofs[".agents/plugins/marketplace.json"],
            expected_root_proof=root_proof,
            source_root=root,
        )
    except BootstrapError:
        return dict(base, status="conflict", reason="Guardian checkout marketplace pin is invalid")
    tracked = {"codex-workflow-guardian", "allinluna", "ponytail"}
    all_entries = installed + available
    known_installed_paths = known_installed_paths or {}

    def verified_entry(item: Dict[str, Any], location: str) -> bool:
        name = item["name"]
        if not _plugin_source_matches(name, item, lock) or not _plugin_policy_matches(name, item):
            return False
        if location == "installed" and name in ("allinluna", "ponytail"):
            return _plugin_install_proof(
                name,
                item,
                codex_home,
                lock,
                git_bin,
                known_installed_paths.get(name),
                codex_version,
            ) is not None
        return True

    hits = [_safe_plugin_hit(item, "installed" if item in installed else "available", verified_entry(item, "installed" if item in installed else "available")) for item in all_entries if item["name"] in tracked]
    collisions: List[str] = []
    installed_map: Dict[str, bool] = {}
    available_map: Dict[str, bool] = {}
    installed_paths: Dict[str, str] = {}
    installed_sha: Dict[str, str] = {}
    installed_tree_sha256: Dict[str, str] = {}
    expected_tree_sha256: Dict[str, str] = {}
    installed_tree_proof: Dict[str, Dict[str, Any]] = {}
    for collection, location, output in ((installed, "installed", installed_map), (available, "available", available_map)):
        seen: set = set()
        for item in collection:
            name = item["name"]
            if name not in tracked:
                continue
            if name in seen:
                collisions.append(name)
            seen.add(name)
            if location == "available" and (item["installed"] or item["enabled"]):
                collisions.append(name)
            verified = verified_entry(item, location)
            if not verified:
                collisions.append(name)
            if item["installed"] if location == "installed" else True:
                if verified:
                    output[name] = True
                else:
                    output[name] = False
                if location == "installed" and name in ("allinluna", "ponytail") and verified:
                    proof = _plugin_install_proof(
                        name,
                        item,
                        codex_home,
                        lock,
                        git_bin,
                        known_installed_paths.get(name),
                        codex_version,
                    )
                    if proof is not None:
                        installed_paths[name] = proof["path"]
                        if proof.get("commit"):
                            installed_sha[name] = proof["commit"]
                        if proof.get("tree_sha256"):
                            installed_tree_sha256[name] = proof["tree_sha256"]
                        if proof.get("expected_tree_sha256"):
                            expected_tree_sha256[name] = proof["expected_tree_sha256"]
                        if isinstance(proof.get("tree_proof"), dict):
                            installed_tree_proof[name] = copy.deepcopy(proof["tree_proof"])
    if collisions:
        return dict(base, status="conflict", reason="installed or available plugin source is not pinned", collisions=sorted(set(collisions)), hits=hits)
    if not verified_entry(guardian, "installed"):
        return dict(base, status="conflict", reason="Guardian source is not local to the canonical marketplace", hits=hits)
    result = dict(
        base,
        status="present",
        installed={name: True for name in ("allinluna", "ponytail") if installed_map.get(name)},
        available={name: True for name in ("allinluna", "ponytail") if available_map.get(name)},
        installed_paths=installed_paths,
        installed_sha=installed_sha,
        installed_tree_sha256=installed_tree_sha256,
        expected_tree_sha256=expected_tree_sha256,
        guardian_ref=guardian_ref,
        guardian_provenance="canonical git marketplace and clean exact Git checkout",
        marketplace_root_relative=_relative_path_string(root, codex_home),
        hits=hits,
    )
    if include_tree_proof:
        result["installed_tree_proof"] = installed_tree_proof
    return result


def _plugin_resolver_row_digest(
    codex_home: Path,
    executable: Optional[Path],
    name: str,
    repository_root: Optional[Path] = None,
) -> Optional[str]:
    if executable is None:
        return None
    spec = _codex_launch_spec(str(executable), codex_home)
    if spec is None:
        return None
    code, stdout, _ = _run_codex_plugin_argv([spec, "plugin", "list", "--json"], codex_home, repository_root or REPOSITORY_ROOT)
    if code != 0:
        return None
    try:
        payload = _safe_json_loads(stdout, "installed plugin inspection")
    except BootstrapError:
        return None
    entries = _plugin_list_payload(payload, "installed")
    if entries is None:
        return None
    matches = [entry for entry in entries if entry.get("name") == name and entry.get("installed") is True]
    if len(matches) != 1:
        return None
    entry = matches[0]
    projection = {
        key: entry.get(key)
        for key in (
            "pluginId", "name", "marketplaceName", "version", "installed", "enabled", "installedPath",
            "installPolicy", "authPolicy", "source", "marketplaceSource",
        )
    }
    encoded = json.dumps(projection, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(encoded.encode("utf-8"))


def _plugin_plans(lock: Dict[str, Any], plugin_component: Dict[str, Any]) -> List[Dict[str, Any]]:
    components = lock.get("components", {})
    plans: List[Dict[str, Any]] = []
    for name in ("allinluna", "ponytail"):
        expected = str(components.get(name, {}).get("commit", ""))
        installed = plugin_component.get("installed", {}).get(name)
        available = plugin_component.get("available", {}).get(name)
        observed_sha = plugin_component.get("installed_sha", {}).get(name)
        observed_tree_sha256 = plugin_component.get("installed_tree_sha256", {}).get(name)
        expected_tree_sha256 = plugin_component.get("expected_tree_sha256", {}).get(name)
        observed_installed = any(
            isinstance(hit, dict)
            and hit.get("plugin") == name
            and hit.get("location") == "installed"
            and hit.get("installed") is True
            for hit in plugin_component.get("hits", [])
        )
        if installed is True:
            status = "present"
        elif name == "ponytail":
            status = "optional"
        elif installed is False or available is False:
            status = "conflict"
        else:
            status = "planned"
        plans.append({
            "name": f"plugin:{name}",
            "plugin_name": name,
            "selector": f"{name}@onebigmoon-codex-workflows",
            "expected_sha": expected,
            "observed_sha": observed_sha,
            "observed_tree_sha256": observed_tree_sha256,
            "expected_tree_sha256": expected_tree_sha256,
            "observed_installed": observed_installed,
            "status": status,
            "owned": False,
            "hooks": "never auto-trust" if name == "ponytail" else "none",
        })
    return plans


def _plugin_add(
    executable: Path,
    codex_home: Path,
    selector: str,
    lock: Optional[Dict[str, Any]] = None,
    repository_root: Optional[Path] = None,
) -> PluginAddOutcome:
    spec = _codex_launch_spec(str(executable), codex_home)
    if spec is None:
        return PluginAddOutcome.uncertain()
    code, stdout, stderr = _run_codex_plugin_argv(
        [spec, "plugin", "add", selector, "--json"],
        codex_home,
        repository_root or REPOSITORY_ROOT,
        timeout=120,
    )
    del stderr
    if code != 0:
        return PluginAddOutcome.uncertain()
    if not isinstance(stdout, str) or not stdout.strip():
        return PluginAddOutcome.uncertain()
    try:
        payload = _safe_json_loads(stdout, "plugin add result")
    except BootstrapError:
        return PluginAddOutcome.invalid_response()
    required = {"pluginId", "name", "marketplaceName", "version", "installedPath", "authPolicy"}
    if not isinstance(payload, dict) or set(payload) != required or not all(isinstance(payload.get(key), str) for key in required):
        return PluginAddOutcome.invalid_response()
    if "@" not in selector:
        return PluginAddOutcome.invalid_response()
    name, marketplace_name = selector.split("@", 1)
    if (
        not name
        or marketplace_name != WORKFLOW_MARKETPLACE
        or payload.get("pluginId") != selector
        or payload.get("name") != name
        or payload.get("marketplaceName") != WORKFLOW_MARKETPLACE
        or payload.get("authPolicy") != "ON_INSTALL"
    ):
        return PluginAddOutcome.invalid_response()
    expected_version = None
    effective_lock = lock
    if lock is not None:
        expected_version = lock.get("components", {}).get(name, {}).get("version")
    if expected_version is None:
        try:
            effective_lock = _load_lock(repository_root)
            expected_version = effective_lock.get("components", {}).get(name, {}).get("version")
        except BootstrapError:
            return PluginAddOutcome.invalid_response()
    if not isinstance(expected_version, str) or _normalized_distribution_version(payload.get("version")) != _normalized_distribution_version(expected_version):
        return PluginAddOutcome.invalid_response()
    try:
        raw_path = payload["installedPath"]
        if name == "allinluna":
            if _strict_locked_plugin_relative_path(name, raw_path, codex_home, effective_lock or {}) is None:
                return PluginAddOutcome.invalid_response()
        else:
            reported = Path(raw_path)
            candidate = _absolute_lexical(reported if reported.is_absolute() else codex_home / reported)
            relative = _relative_path_string(candidate, codex_home)
            if not relative:
                return PluginAddOutcome.invalid_response()
            _safe_relative_path(codex_home, relative)
    except (BootstrapError, ValueError):
        return PluginAddOutcome.invalid_response()
    return PluginAddOutcome.valid(raw_path)


def _plugin_observation_has_installed(observed: Dict[str, Any], name: str) -> bool:
    return any(
        isinstance(hit, dict)
        and hit.get("plugin") == name
        and hit.get("location") == "installed"
        and hit.get("installed") is True
        for hit in observed.get("hits", [])
    )


def _plugin_observation_proof(
    observed: Dict[str, Any],
    codex_home: Path,
    name: str,
    expected_sha: str,
) -> Optional[str]:
    installed = observed.get("installed")
    installed_sha = observed.get("installed_sha")
    installed_paths = observed.get("installed_paths")
    if observed.get("status") != "present" or not isinstance(installed, dict) or not installed.get(name):
        return None
    if not isinstance(installed_sha, dict) or installed_sha.get(name) != expected_sha:
        return None
    relative = installed_paths.get(name) if isinstance(installed_paths, dict) else None
    if not isinstance(relative, str) or not relative:
        return None
    try:
        _safe_relative_path(codex_home, relative)
    except BootstrapError:
        return None
    return relative


def _plugin_ownership_proof(
    observed: Dict[str, Any],
    codex_home: Path,
    name: str,
    expected_sha: str,
) -> Optional[Dict[str, Any]]:
    """Bind a newly verified plugin to its exact installed cache inode."""
    relative = _plugin_observation_proof(observed, codex_home, name, expected_sha)
    if relative is None:
        return None
    try:
        path = _safe_relative_path(codex_home, relative)
        info = path.lstat()
    except (BootstrapError, OSError):
        return None
    if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        return None
    try:
        _strict_existing_parent_chain(path.parent, codex_home)
    except (BootstrapError, OSError):
        return None
    if info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or _has_acl(path):
        return None
    if name == "allinluna":
        installed_trees = observed.get("installed_tree_sha256")
        expected_tree = installed_trees.get(name) if isinstance(installed_trees, dict) else None
        initial_trees = observed.get("installed_tree_proof")
        initial_tree = initial_trees.get(name) if isinstance(initial_trees, dict) else None
        tree_proof = _content_tree_proof(path, codex_home)
        if (
            not isinstance(tree_proof, dict)
            or tree_proof.get("tree_sha256") != expected_tree
            or not isinstance(initial_tree, dict)
            or any(tree_proof.get(key) != initial_tree.get(key) for key in ("tree_sha256", "device", "inode", "mode", "uid", "gid", "size", "mtime_ns", "ctime_ns"))
            or not _identity_matches(info, tree_proof)
        ):
            return None
    return {
        "relative_path": relative,
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
    }


def _python_component() -> Dict[str, Any]:
    actual = tuple(sys.version_info[:3])
    status = "present" if _version_at_least(actual, MIN_PYTHON) else "unavailable"
    return {
        "name": "python",
        "status": status,
        "version": ".".join(str(part) for part in actual),
        "minimum_version": "3.9",
    }


def _headroom_component() -> Dict[str, Any]:
    executable = shutil.which("headroom")
    if executable:
        return {
            "name": "headroom",
            "status": "configured-unverified",
            "mode": "check-only",
            "reason": "presence is not installation, launch, routing, or trust",
        }
    return {
        "name": "headroom",
        "status": "unavailable",
        "mode": "check-only",
        "reason": "not detected; manual separately authorized integration only",
    }


def _allinluna_python(
    lock: Dict[str, Any],
    requested: Optional[str],
    codex_home: Optional[Path] = None,
    repository_root: Optional[Path] = None,
) -> Optional[Path]:
    candidates: List[Path] = []
    if requested:
        requested_path = Path(requested).expanduser()
        if not requested_path.is_absolute():
            return None
        candidates.append(requested_path)
    current_version = tuple(sys.version_info[:3])
    if _version_at_least(current_version, MIN_ALLINLUNA_PYTHON):
        candidates.append(Path(sys.executable))
    candidates.extend(Path(item) for item in MACOS_PYTHON_CANDIDATES)
    seen: set = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        spec = _python_launch_spec(candidate, codex_home)
        if spec is None or not spec.path.is_file():
            continue
        path = spec.path
        hint = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?(?!\d)", path.name)
        hinted_version = (
            (int(hint.group(1)), int(hint.group(2)), int(hint.group(3) or 0))
            if hint is not None
            else None
        )
        if hinted_version is not None and hinted_version < MIN_ALLINLUNA_PYTHON:
            continue
        code, stdout, stderr = _run_argv(
            [spec, "-I", "--version"],
            codex_home or REPOSITORY_ROOT,
            repository_root or REPOSITORY_ROOT,
        )
        del stderr
        if code != 0 or not _version_at_least(_version_tuple(stdout), MIN_ALLINLUNA_PYTHON):
            continue
        capability_code, _, capability_stderr = _run_argv(
            [
                spec,
                "-I",
                "-c",
                "import ensurepip, venv; print('guardian-python-capable')",
            ],
            codex_home or REPOSITORY_ROOT,
            repository_root or REPOSITORY_ROOT,
        )
        del capability_stderr
        if capability_code == 0:
            return path
    return None


def _verify_allinluna_static_distribution(
    root: Path,
    codex_home: Path,
    expected_version: str,
) -> bool:
    """Verify the fixed venv entrypoints and locked dist-info without execution."""
    required = (
        root / "bin" / "python",
        root / "bin" / "allinluna",
        root / "pyvenv.cfg",
    )
    for path in required:
        try:
            _assert_safe_path(path, allow_missing=False, anchor=codex_home)
            info = path.lstat()
        except (BootstrapError, OSError):
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return False
        if path.name in ("python", "allinluna") and not (info.st_mode & stat.S_IXUSR):
            return False
    pyvenv = _read_bounded_file(root / "pyvenv.cfg", codex_home)
    if pyvenv is None:
        return False
    try:
        pyvenv_lines = pyvenv.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError:
        return False
    if "include-system-site-packages = false" not in {line.strip().lower() for line in pyvenv_lines}:
        return False
    metadata_candidates: List[Path] = []
    for lib_name in ("lib", "lib64"):
        lib_root = root / lib_name
        try:
            _assert_safe_path(lib_root, allow_missing=True, anchor=codex_home)
            if not lib_root.exists():
                continue
            lib_info = lib_root.lstat()
            if stat.S_ISLNK(lib_info.st_mode) or not stat.S_ISDIR(lib_info.st_mode):
                return False
            python_dirs: List[Path] = []
            for item in lib_root.iterdir():
                if len(python_dirs) >= MAX_PLUGIN_TREE_ENTRIES:
                    return False
                if re.fullmatch(r"python\d+\.\d+", item.name):
                    python_dirs.append(item)
            python_dirs.sort()
        except (BootstrapError, OSError):
            return False
        for python_dir in python_dirs:
            site_packages = python_dir / "site-packages"
            try:
                _assert_safe_path(site_packages, allow_missing=False, anchor=codex_home)
                if stat.S_ISLNK(site_packages.lstat().st_mode) or not stat.S_ISDIR(site_packages.lstat().st_mode):
                    return False
                item_count = 0
                for item in site_packages.iterdir():
                    item_count += 1
                    if item_count > MAX_PLUGIN_TREE_ENTRIES:
                        return False
                    if item.is_dir() and not item.is_symlink() and item.name.startswith("allinluna-") and item.name.endswith(".dist-info"):
                        metadata_candidates.append(item)
            except (BootstrapError, OSError):
                return False
    if len(metadata_candidates) != 1:
        return False
    metadata_dir = metadata_candidates[0]
    version_suffix = metadata_dir.name[len("allinluna-") : -len(".dist-info")]
    if _normalized_distribution_version(version_suffix) != _normalized_distribution_version(expected_version):
        return False
    metadata = _read_bounded_file(metadata_dir / "METADATA", codex_home)
    wheel = _read_bounded_file(metadata_dir / "WHEEL", codex_home)
    record = _read_bounded_file(metadata_dir / "RECORD", codex_home)
    if metadata is None or wheel is None or record is None:
        return False
    try:
        lines = metadata.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError:
        return False
    fields = {
        line.split(":", 1)[0].strip().lower(): line.split(":", 1)[1].strip()
        for line in lines
        if ":" in line
    }
    if _normalized_distribution_version(fields.get("name")) != "allinluna":
        return False
    if _normalized_distribution_version(fields.get("version")) != _normalized_distribution_version(expected_version):
        return False
    return not any(line.lower().startswith("requires-dist:") for line in lines)


def _verify_allinluna_runtime(
    root: Path,
    codex_home: Path,
    expected_version: str,
    repository_root: Optional[Path] = None,
) -> Optional[List[str]]:
    python = root / "bin" / "python"
    executable = root / "bin" / "allinluna"
    try:
        _assert_safe_path(python, allow_missing=False, anchor=codex_home)
        _assert_safe_path(executable, allow_missing=False, anchor=codex_home)
        python_info = python.lstat()
        executable_info = executable.lstat()
    except (BootstrapError, OSError):
        return None
    if not stat.S_ISREG(python_info.st_mode) or not stat.S_ISREG(executable_info.st_mode):
        return None
    if not (python_info.st_mode & stat.S_IXUSR) or not (executable_info.st_mode & stat.S_IXUSR):
        return None
    python_spec = _python_launch_spec(python, codex_home, venv=True)
    if python_spec is None:
        return None
    probe = (
        "import importlib.metadata as m, json, sys; "
        "print(json.dumps({'python': list(sys.version_info[:3]), 'name': 'allinluna', "
        "'version': m.version('allinluna'), "
        "'dependencies': m.metadata('allinluna').get_all('Requires-Dist') or []}))"
    )
    code, stdout, _ = _run_argv(
        [python_spec, "-I", "-c", probe],
        codex_home,
        repository_root or REPOSITORY_ROOT,
        timeout=20,
    )
    if code != 0:
        return None
    try:
        value = _safe_json_loads(stdout.strip(), "All in Luna runtime probe")
    except BootstrapError:
        return None
    if not isinstance(value, dict) or value.get("name") != "allinluna":
        return None
    version = value.get("version")
    runtime_python = value.get("python")
    dependencies = value.get("dependencies")
    if _normalized_distribution_version(version) != _normalized_distribution_version(expected_version):
        return None
    if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies) or dependencies:
        return None
    if not isinstance(runtime_python, list) or len(runtime_python) < 2:
        return None
    try:
        if tuple(int(item) for item in runtime_python[:3]) < MIN_ALLINLUNA_PYTHON:
            return None
    except (TypeError, ValueError):
        return None
    return dependencies


def _allinluna_component(
    lock: Dict[str, Any],
    codex_home: Path,
    python_component: Dict[str, Any],
    requested_python: Optional[str],
    repository_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _absolute_lexical(codex_home / "venvs" / "allinluna")
    marker = root / "guardian-install.json"
    owner_marker = root / "guardian-bootstrap-owner.json"
    executable = root / "bin" / "allinluna"
    allin = lock.get("components", {}).get("allinluna", {})
    wheel = allin.get("pypi", {}).get("wheel", {})
    expected_hash = wheel.get("sha256")
    if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
        return {"name": "allinluna", "status": "unavailable", "reason": "wheel pin is invalid"}
    try:
        _assert_safe_path(root, anchor=codex_home)
        _strict_existing_parent_chain(root.parent, codex_home)
        root_info = root.lstat()
        root_exists = True
    except FileNotFoundError:
        root_info = None
        root_exists = False
    except (BootstrapError, OSError):
        return {"name": "allinluna", "status": "conflict", "reason": "managed venv path is unsafe"}
    if root_exists and (
        root_info is None
        or stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.getuid()
        or root_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or _has_acl(root)
    ):
        return {"name": "allinluna", "status": "conflict", "reason": "managed venv path is unsafe"}
    if root_exists:
        try:
            _assert_safe_path(owner_marker, allow_missing=False, anchor=codex_home)
            _assert_safe_path(marker, allow_missing=False, anchor=codex_home)
            if not stat.S_ISREG(owner_marker.lstat().st_mode) or not stat.S_ISREG(marker.lstat().st_mode):
                raise BootstrapError("managed venv marker is not a regular file")
        except (BootstrapError, OSError):
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv is not owned by this bootstrap"}
        try:
            owner_bytes = _read_stable_bytes(owner_marker, MAX_MARKER_BYTES, codex_home)
            marker_bytes = _read_stable_bytes(marker, MAX_JSON_BYTES, codex_home)
            owner_data = _safe_json_loads(owner_bytes, "managed venv owner marker")
            marker_data = _safe_json_loads(marker_bytes, "managed venv install marker")
        except (OSError, BootstrapError):
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv receipt is invalid"}
        expected_version = str(allin.get("pypi", {}).get("version", ""))
        if owner_data != OWNER_MARKER or owner_bytes != OWNER_MARKER_BYTES or not isinstance(marker_data, dict):
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv ownership marker is invalid"}
        if (
            set(marker_data) != {"component", "dependencies", "version", "wheel_sha256"}
            or marker_data.get("component") != "allinluna"
            or marker_data.get("dependencies") != []
            or marker_data.get("version") != expected_version
            or marker_data.get("wheel_sha256") != expected_hash
        ):
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv artifact differs"}
        existing, receipt_error = _read_existing_receipt(codex_home)
        root_hash = _tree_hash(root)
        if receipt_error or not isinstance(existing, dict) or not isinstance(root_hash, str):
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv ownership receipt is missing"}
        proof = next(
            (
                item
                for item in existing.get("owned_paths", [])
                if isinstance(item, dict) and item.get("relative_path") == "venvs/allinluna"
            ),
            None,
        )
        if not isinstance(proof, dict) or proof.get("sha256") != root_hash or proof.get("kind") != "directory":
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv tree ownership proof is invalid"}
        if (
            not isinstance(proof.get("device"), int)
            or isinstance(proof.get("device"), bool)
            or not isinstance(proof.get("inode"), int)
            or isinstance(proof.get("inode"), bool)
            or not _identity_matches(root_info, proof)
            or not _identity_matches(root.lstat(), proof)
        ):
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv ownership identity is invalid"}
        if not _verify_allinluna_static_distribution(root, codex_home, expected_version):
            return {"name": "allinluna", "status": "conflict", "reason": "managed venv static distribution is not verified"}
        return {
            "name": "allinluna",
            "status": "present",
            "path_relative": "venvs/allinluna/bin/allinluna",
            "python_relative": "venvs/allinluna/bin/python",
            "python_minimum": ">=3.11",
            "wheel_sha256": expected_hash,
            "version": expected_version,
            "managed_venv_relative_path": "venvs/allinluna",
        }
    interpreter = _allinluna_python(lock, requested_python, codex_home, repository_root)
    if interpreter is None:
        return {
            "name": "allinluna",
            "status": "unavailable",
            "reason": "no Python >=3.11 interpreter found",
            "python_minimum": ">=3.11",
        }
    return {
        "name": "allinluna",
        "status": "planned",
        "path_relative": "venvs/allinluna/bin/allinluna",
        "python_relative": "venvs/allinluna/bin/python",
        "python_minimum": ">=3.11",
        "wheel_sha256": expected_hash,
        "reason": "apply will fetch and verify the pinned wheel only",
    }


def _target_specs(repository_root: Optional[Path] = None) -> List[Tuple[str, Path, Path, str]]:
    specs: List[Tuple[str, Path, Path, str]] = []
    source_root = _absolute_lexical(repository_root or REPOSITORY_ROOT)
    for role_name in ROLE_TEMPLATE_NAMES:
        source = source_root / "skills" / "setup-codex-workflow-guardian" / "assets" / "agents" / f"{role_name}.toml"
        specs.append((f"agent:{role_name}", source, Path("agents") / f"{role_name}.toml", "file"))
    return specs


def _copy_staging_relative_path(relative: str) -> str:
    target = Path(relative)
    return (target.parent / _stable_temp_name(".guardian-copy-", relative)).as_posix()


def _preflight_targets(
    codex_home: Path,
    repository_root: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    plans: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    source_root = _absolute_lexical(repository_root or REPOSITORY_ROOT)
    frozen_sources = _VALIDATED_SOURCE_PROOFS.get(str(source_root), {})
    for name, source, relative, kind in _target_specs(repository_root):
        try:
            cached = frozen_sources.get(relative.as_posix())
            if cached is not None:
                source_proof = copy.deepcopy(cached)
                if not _source_asset_matches(source, source_root, source_proof):
                    raise BootstrapError("frozen source proof changed after checkout validation")
            else:
                source_proof = _source_asset_proof(source, source_root)
            expected = source_proof["sha256"]
        except (BootstrapError, OSError):
            conflicts.append({"name": name, "reason": "source asset proof is unsafe"})
            source_proof = None
            expected = None
        try:
            target = _safe_relative_path(codex_home, relative.as_posix())
            _strict_existing_parent_chain(target.parent, codex_home)
        except (BootstrapError, OSError):
            conflicts.append({"name": name, "relative_path": relative.as_posix(), "reason": "target path contains an unsafe link"})
            plans.append({
                "name": name,
                "source": source,
                "relative_path": relative.as_posix(),
                "target": _absolute_lexical(codex_home / relative),
                "codex_home": codex_home,
                "source_anchor": source_root,
                "kind": kind,
                "expected_sha256": expected,
                "source_proof": source_proof,
                "status": "conflict",
            })
            continue
        try:
            target_info = target.lstat()
            exists = True
        except FileNotFoundError:
            target_info = None
            exists = False
        except OSError:
            target_info = None
            exists = True
        target_proof = _managed_target_trust(target, codex_home, expected) if exists and expected is not None else None
        status = "present" if exists and target_proof is not None else "installed"
        if source_proof is None:
            status = "conflict"
        if exists and (target_info is None or target_proof is None):
            status = "conflict"
            conflicts.append({"name": name, "relative_path": relative.as_posix(), "reason": "existing state is untrusted or differs"})
        plans.append({
            "name": name,
            "source": source,
            "relative_path": relative.as_posix(),
            "target": target,
            "codex_home": codex_home,
            "source_anchor": source_root,
            "kind": kind,
            "expected_sha256": expected,
            "source_proof": source_proof,
            "target_proof": target_proof,
            "status": status,
        })
    return plans, conflicts


def _component_from_plan(plan: Dict[str, Any], mode: str) -> Dict[str, Any]:
    status = str(plan["status"])
    if mode == "check" and status == "installed":
        status = "skipped"
    return {
        "name": plan["name"],
        "status": status,
        "relative_path": plan["relative_path"],
        "sha256": plan["expected_sha256"],
    }


def _rename_noreplace_at(
    directory_fd: int,
    source_name: str,
    target_name: str,
    source_ownership: Optional[Dict[str, Any]] = None,
    source_digest: Optional[str] = None,
) -> None:
    """Atomically rename two entries in one opened directory without clobbering."""
    source_bytes = os.fsencode(source_name)
    target_bytes = os.fsencode(target_name)
    try:
        source_info = os.stat(source_name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        raise
    if (
        stat.S_ISLNK(source_info.st_mode)
        or not (stat.S_ISREG(source_info.st_mode) or stat.S_ISDIR(source_info.st_mode))
        or source_ownership is not None and not _identity_matches(source_info, source_ownership)
        or source_digest is not None and _tree_hash_at(directory_fd, source_name) != source_digest
    ):
        raise BootstrapError("rename source ownership changed")
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is not None:
            renameatx_np.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            renameatx_np.restype = ctypes.c_int
            if renameatx_np(directory_fd, source_bytes, directory_fd, target_bytes, 0x00000004) == 0:
                return
            error = ctypes.get_errno()
            if error == 17:
                raise FileExistsError(target_name)
            raise OSError(error, os.strerror(error))
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            renameat2.restype = ctypes.c_int
            if renameat2(directory_fd, source_bytes, directory_fd, target_bytes, 1) == 0:
                return
            error = ctypes.get_errno()
            if error == 17:
                raise FileExistsError(target_name)
            raise OSError(error, os.strerror(error))
    if stat.S_ISDIR(source_info.st_mode):
        raise BootstrapError("atomic no-replace directory publish is unavailable")
    try:
        os.link(source_name, target_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
    except FileExistsError:
        raise
    if not _unlink_at_owned(directory_fd, source_name, _identity(source_info), source_digest):
        raise BootstrapError("rename source ownership changed during publish")


def _validate_open_directory(
    descriptor: int,
    path: Path,
    required_mode: Optional[int] = None,
) -> None:
    """Validate one opened managed directory before and after path inspection."""
    before = os.fstat(descriptor)

    def valid(info: os.stat_result) -> bool:
        return (
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid()
            and not (info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            and (required_mode is None or stat.S_IMODE(info.st_mode) == required_mode)
        )

    if not valid(before):
        raise BootstrapError("managed directory ownership or mode is unsafe")
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise BootstrapError("managed directory cannot be inspected") from exc
    if stat.S_ISLNK(path_before.st_mode) or not _identity_matches(path_before, _identity(before)):
        raise BootstrapError("managed directory identity changed")
    if _has_acl(path):
        raise BootstrapError("managed directory has an extended ACL")

    after = os.fstat(descriptor)
    try:
        path_after = path.lstat()
    except OSError as exc:
        raise BootstrapError("managed directory disappeared during validation") from exc
    if (
        not valid(after)
        or not _identity_matches(after, _identity(before))
        or not _identity_matches(path_after, _identity(after))
        or _has_acl(path)
    ):
        raise BootstrapError("managed directory changed during validation")


def _open_relative_parent(
    codex_home: Path,
    relative: str,
    required_mode: Optional[int] = None,
) -> Tuple[int, str]:
    """Open/create a target parent through trusted, ownership-checked FDs."""
    safe = _safe_relative_path(codex_home, relative)
    parts = Path(relative).parts
    if len(parts) < 1:
        raise BootstrapError("copy target is not relative")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root = _absolute_lexical(codex_home)
    descriptor = os.open(str(root), flags)
    try:
        _validate_open_directory(descriptor, root)
        current = root
        for component in parts[:-1]:
            child_path = current / component
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
            try:
                _validate_open_directory(child, child_path, required_mode=required_mode)
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            current = child_path
        return descriptor, safe.name
    except BaseException:
        os.close(descriptor)
        raise


def _ensure_workflow_guardian_directory(codex_home: Path) -> None:
    """Create or validate the fixed private directory used by guardian state."""
    descriptor, _ = _open_relative_parent(
        codex_home,
        RECEIPT_RELATIVE.as_posix(),
        required_mode=0o700,
    )
    os.close(descriptor)


def _private_state_file(path: Path, info: os.stat_result) -> bool:
    """Return whether a receipt/journal inode is safe to read or replace."""
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600
        and not _has_acl(path)
    )


def _validate_existing_workflow_guardian_directory(codex_home: Path) -> None:
    """Validate an existing state directory without creating it in read-only modes."""
    directory = _absolute_lexical(codex_home / RECEIPT_RELATIVE.parent)
    _assert_safe_path(directory, anchor=codex_home)
    try:
        directory.lstat()
    except FileNotFoundError:
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(directory), flags)
    try:
        _validate_open_directory(descriptor, directory, required_mode=0o700)
    finally:
        os.close(descriptor)


def _stable_temp_name(prefix: str, key: str) -> str:
    return prefix + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _temporary_file_at(directory_fd: int, name: Optional[str] = None) -> Tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    if name is None:
        name = _stable_temp_name(".guardian-copy-", str(directory_fd))
    try:
        return name, os.open(name, flags, 0o600, dir_fd=directory_fd)
    except FileExistsError as exc:
        raise BootstrapError("temporary copy name is unavailable") from exc


def _temporary_directory_at(directory_fd: int, name: Optional[str] = None) -> str:
    if name is None:
        name = _stable_temp_name(".guardian-copy-", str(directory_fd))
    try:
        os.mkdir(name, 0o700, dir_fd=directory_fd)
        return name
    except FileExistsError as exc:
        raise BootstrapError("temporary copy directory is unavailable") from exc


def _tree_hash_at(directory_fd: int, name: str) -> Optional[str]:
    """Hash an entry through an opened directory with bounded FD reads."""
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
            return None
        state = {"entries": 1, "file_bytes": 0}
        if stat.S_ISREG(before.st_mode):
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                opened = os.fstat(descriptor)
                if not _same_stat(before, opened):
                    return None
                digest = _read_hash_fd(descriptor, state)
            finally:
                os.close(descriptor)
        else:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            try:
                opened = os.fstat(descriptor)
                if not _same_stat(before, opened):
                    return None
                digest = _tree_hash_fd(descriptor, 0, state)
            finally:
                os.close(descriptor)
        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return digest if _same_stat(before, after) else None
    except (BootstrapError, OSError, RecursionError, UnicodeError):
        return None


def _copy_stream_bounded(source_handle: Any, target_handle: Any, state: Dict[str, int]) -> None:
    """Copy source bytes with the same aggregate limits as tree hashing."""
    while True:
        chunk = source_handle.read(1024 * 1024)
        if not chunk:
            return
        state["file_bytes"] += len(chunk)
        if state["file_bytes"] > MAX_PLUGIN_TREE_BYTES:
            raise BootstrapError("managed copy is too large")
        target_handle.write(chunk)


def _copy_tree_at(
    source: Path,
    directory_fd: int,
    target_name: str,
    state: Optional[Dict[str, int]] = None,
) -> None:
    """Copy a checked source tree into an opened destination directory."""
    if state is None:
        state = {"entries": 1, "file_bytes": 0}
    os.mkdir(target_name, 0o700, dir_fd=directory_fd)
    target_fd = os.open(target_name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    try:
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            state["entries"] += 1
            if state["entries"] > MAX_PLUGIN_TREE_ENTRIES:
                raise BootstrapError("managed copy has too many entries")
            _reject_source_symlinks(child)
            child_info = child.lstat()
            if stat.S_ISDIR(child_info.st_mode):
                _copy_tree_at(child, target_fd, child.name, state)
                continue
            child_name, child_fd = _temporary_file_at(target_fd, _stable_temp_name(".guardian-copy-", child.name))
            child_ownership: Optional[Dict[str, int]] = None
            child_digest: Optional[str] = None
            try:
                source_descriptor = os.open(str(child), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(source_descriptor, "rb") as source_handle, os.fdopen(child_fd, "wb") as target_handle:
                    child_ownership = _identity(os.fstat(target_handle.fileno()))
                    opened_source = os.fstat(source_handle.fileno())
                    if not _same_stat(child_info, opened_source):
                        raise BootstrapError("source changed during copy")
                    _copy_stream_bounded(source_handle, target_handle, state)
                    os.fchmod(target_handle.fileno(), stat.S_IMODE(child_info.st_mode))
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
                child_digest = _tree_hash_at(target_fd, child_name)
                if child_digest is None:
                    raise BootstrapError("managed copy source proof is unavailable")
                _rename_noreplace_at(
                    target_fd,
                    child_name,
                    child.name,
                    source_ownership=child_ownership,
                    source_digest=child_digest,
                )
            except BaseException:
                if child_ownership is not None:
                    _unlink_at_owned(target_fd, child_name, child_ownership, child_digest)
                raise
    finally:
        os.close(target_fd)


def _remove_tree_at(
    directory_fd: int,
    name: str,
    expected: Optional[str] = None,
    ownership: Optional[Dict[str, Any]] = None,
) -> None:
    """Remove a quarantined tree with per-entry identity/hash quarantine guards."""
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    root_info = os.fstat(descriptor)
    if ownership is not None and not _identity_matches(root_info, ownership):
        os.close(descriptor)
        raise BootstrapError("quarantined directory identity changed")
    try:
        if expected is not None:
            if not SHA256_RE.fullmatch(expected) or _tree_hash_at(directory_fd, name) != expected:
                raise BootstrapError("quarantined directory hash changed")
        names = sorted(os.listdir(descriptor), key=os.fsencode)
        if len(names) > MAX_PLUGIN_TREE_ENTRIES:
            raise BootstrapError("quarantined directory is too large")
        manifest: List[Tuple[str, Dict[str, int], str, bool]] = []
        for child in names:
            info = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise BootstrapError("quarantined path contains a special file")
            child_hash = _tree_hash_at(descriptor, child)
            if child_hash is None:
                raise BootstrapError("quarantined path hash is unavailable")
            after_hash = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if not _identity_matches(after_hash, _identity(info)):
                raise BootstrapError("quarantined path changed during hashing")
            manifest.append((child, _identity(info), child_hash, stat.S_ISDIR(info.st_mode)))
        for child, child_identity, child_hash, is_directory in manifest:
            current = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if not _identity_matches(current, child_identity) or _tree_hash_at(descriptor, child) != child_hash:
                raise BootstrapError("quarantined descendant was replaced")
            quarantine_name = _stable_temp_name(".guardian-remove-", name + "/" + child)
            _rename_noreplace_at(descriptor, child, quarantine_name)
            moved = True
            try:
                quarantined = os.stat(quarantine_name, dir_fd=descriptor, follow_symlinks=False)
                if not _identity_matches(quarantined, child_identity) or _tree_hash_at(descriptor, quarantine_name) != child_hash:
                    raise BootstrapError("quarantined descendant identity changed")
                if is_directory:
                    _remove_tree_at(descriptor, quarantine_name, child_hash, child_identity)
                else:
                    if not _unlink_at_owned(descriptor, quarantine_name, child_identity, child_hash):
                        raise BootstrapError("quarantined descendant could not be removed")
                moved = False
            finally:
                if moved:
                    try:
                        _rename_noreplace_at(
                            descriptor,
                            quarantine_name,
                            child,
                            source_ownership=child_identity,
                            source_digest=child_hash,
                        )
                    except (BootstrapError, OSError):
                        pass
        if os.listdir(descriptor):
            raise BootstrapError("quarantined directory received a new descendant")
        final_info = os.fstat(descriptor)
        if not _identity_matches(final_info, _identity(root_info)):
            raise BootstrapError("quarantined directory identity changed")
        if expected is not None and not SHA256_RE.fullmatch(expected):
            raise BootstrapError("quarantined directory hash is invalid")
        final_name_info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _identity_matches(final_name_info, _identity(root_info)) or not stat.S_ISDIR(final_name_info.st_mode):
            raise BootstrapError("quarantined directory identity changed")
    finally:
        os.close(descriptor)
    if not _rmdir_at_owned(directory_fd, name, _identity(root_info)):
        raise BootstrapError("quarantined directory could not be removed")


def _identity(info: os.stat_result) -> Dict[str, int]:
    return {"device": int(info.st_dev), "inode": int(info.st_ino)}


def _home_identity(codex_home: Path) -> Dict[str, int]:
    info = codex_home.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BootstrapError("CODEX_HOME identity is invalid")
    return {"home_device": int(info.st_dev), "home_inode": int(info.st_ino)}


def _managed_kind(relative: str) -> Optional[str]:
    if relative in MANAGED_ROLE_RELATIVES:
        return "file"
    if relative == MANAGED_VENV_RELATIVE:
        return "directory"
    return None


def _managed_target_fields(item: Dict[str, Any]) -> bool:
    return (
        isinstance(item, dict)
        and set(item) == {"relative_path", "kind", "ownership", "sha256", "device", "inode", "proof"}
        and isinstance(item.get("relative_path"), str)
        and _managed_kind(item["relative_path"]) == item.get("kind")
        and item.get("ownership") in ("owned", "preexisting-unowned")
        and item.get("proof") in ("transaction-owned", "preexisting-unowned")
        and item.get("ownership") == ("owned" if item.get("proof") == "transaction-owned" else "preexisting-unowned")
        and SHA256_RE.fullmatch(str(item.get("sha256"))) is not None
        and isinstance(item.get("device"), int)
        and not isinstance(item.get("device"), bool)
        and item["device"] >= 0
        and isinstance(item.get("inode"), int)
        and not isinstance(item.get("inode"), bool)
        and item["inode"] >= 0
    )


def _identity_matches(info: os.stat_result, ownership: Optional[Dict[str, Any]]) -> bool:
    return (
        isinstance(ownership, dict)
        and isinstance(ownership.get("device"), int)
        and not isinstance(ownership.get("device"), bool)
        and isinstance(ownership.get("inode"), int)
        and not isinstance(ownership.get("inode"), bool)
        and info.st_dev == ownership["device"]
        and info.st_ino == ownership["inode"]
    )


def _unlink_at_owned(
    directory_fd: int,
    name: str,
    ownership: Dict[str, Any],
    digest: Optional[str] = None,
) -> bool:
    """Unlink one proven regular-file inode through an opened parent FD."""
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return False
        if not _identity_matches(before, ownership):
            return False
        if digest is not None and _tree_hash_at(directory_fd, name) != digest:
            return False
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _identity_matches(current, ownership):
            return False
        os.unlink(name, dir_fd=directory_fd)
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    except (BootstrapError, OSError):
        return False


def _rmdir_at_owned(
    directory_fd: int,
    name: str,
    ownership: Dict[str, Any],
) -> bool:
    """Remove one proven empty directory inode through an opened parent FD."""
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            return False
        if not _identity_matches(before, ownership):
            return False
        child_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            if os.listdir(child_fd):
                return False
        finally:
            os.close(child_fd)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _identity_matches(current, ownership):
            return False
        os.rmdir(name, dir_fd=directory_fd)
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    except (BootstrapError, OSError):
        return False


def _unlink_path_owned(
    path: Path,
    ownership: Dict[str, Any],
    digest: Optional[str] = None,
    anchor: Optional[Path] = None,
) -> bool:
    """Unlink a proven regular file without following a parent/name swap."""
    try:
        _assert_safe_path(path, allow_missing=False, anchor=anchor)
        parent = _absolute_lexical(path.parent)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(str(parent), flags)
    except (BootstrapError, OSError):
        return False
    try:
        return _unlink_at_owned(directory_fd, path.name, ownership, digest)
    finally:
        os.close(directory_fd)


def _replace_from_owned_scratch(
    source: Path,
    target: Path,
    source_ownership: Dict[str, Any],
    source_digest: str,
    target_proof: Optional[Tuple[Dict[str, int], str]] = None,
    anchor: Optional[Path] = None,
    source_mode: Optional[int] = None,
) -> None:
    """Replace only when both scratch and prior target proofs still match."""
    _assert_safe_path(source, allow_missing=False, anchor=anchor)
    _assert_safe_path(target, anchor=anchor)
    source_info = source.lstat()
    if (
        stat.S_ISLNK(source_info.st_mode)
        or not stat.S_ISREG(source_info.st_mode)
        or not _identity_matches(source_info, source_ownership)
        or source_mode is not None and (
            stat.S_IMODE(source_info.st_mode) != source_mode
            or source_info.st_uid != os.getuid()
            or _has_acl(source)
        )
        or _sha256_file(source) != source_digest
    ):
        raise BootstrapError("replacement scratch ownership changed")
    try:
        target_info = target.lstat()
    except FileNotFoundError:
        target_info = None
    if target_proof is None:
        if target_info is not None:
            raise BootstrapError("replacement target was occupied concurrently")
    else:
        expected_identity, expected_digest = target_proof
        if (
            target_info is None
            or stat.S_ISLNK(target_info.st_mode)
            or not stat.S_ISREG(target_info.st_mode)
            or not _identity_matches(target_info, expected_identity)
            or _sha256_file(target) != expected_digest
        ):
            raise BootstrapError("replacement target changed concurrently")
    if target_proof is None:
        _rename_noreplace(
            source,
            target,
            source_ownership=source_ownership,
            source_digest=source_digest,
        )
    else:
        # Revalidate both sides immediately before the replacing syscall; the
        # earlier preflight only establishes the initial transaction view.
        current_source = source.lstat()
        current_target = target.lstat()
        if (
            stat.S_ISLNK(current_source.st_mode)
            or not stat.S_ISREG(current_source.st_mode)
            or stat.S_ISLNK(current_target.st_mode)
            or not stat.S_ISREG(current_target.st_mode)
            or not _identity_matches(current_source, source_ownership)
            or source_mode is not None and (
                stat.S_IMODE(current_source.st_mode) != source_mode
                or current_source.st_uid != os.getuid()
                or _has_acl(source)
            )
            or _sha256_file(source) != source_digest
            or not _identity_matches(current_target, target_proof[0])
            or _sha256_file(target) != target_proof[1]
        ):
            raise BootstrapError("replacement proof changed before publish")
        os.replace(str(source), str(target))
    try:
        installed = target.lstat()
    except OSError as exc:
        raise BootstrapError("replacement target disappeared") from exc
    if (
        not _identity_matches(installed, source_ownership)
        or source_mode is not None and (
            stat.S_IMODE(installed.st_mode) != source_mode
            or installed.st_uid != os.getuid()
            or _has_acl(target)
        )
        or _sha256_file(target) != source_digest
    ):
        raise BootstrapError("replacement target proof failed")


def _receipt_plugin_paths(receipt: Optional[Dict[str, Any]]) -> Dict[str, str]:
    paths: Dict[str, str] = {}
    if not isinstance(receipt, dict):
        return paths
    for component in receipt.get("components", []):
        if not isinstance(component, dict) or component.get("name") != "codex-plugin-command":
            continue
        values = component.get("installed_paths", {})
        if isinstance(values, dict):
            for name, relative in values.items():
                if name in ("allinluna", "ponytail") and isinstance(relative, str) and relative:
                    paths[name] = relative
    for plugin in receipt.get("owned_plugins", []):
        if (
            isinstance(plugin, dict)
            and plugin.get("name") in ("allinluna", "ponytail")
            and isinstance(plugin.get("relative_path"), str)
            and plugin.get("relative_path")
        ):
            paths.setdefault(plugin["name"], plugin["relative_path"])
    return paths


def _receipt_matches_state(
    existing: Dict[str, Any],
    codex_home: Path,
    plans: List[Dict[str, Any]],
    plugin_plans: List[Dict[str, Any]],
    allin_component: Dict[str, Any],
    plugin_component: Dict[str, Any],
    guardian_ref: Optional[str],
) -> bool:
    stored_ref = existing.get("guardian_ref")
    if not isinstance(stored_ref, str) or not guardian_ref or not COMMIT_RE.fullmatch(stored_ref) or not COMMIT_RE.fullmatch(str(guardian_ref)) or stored_ref.lower() != str(guardian_ref).lower():
        return False
    if existing.get("guardian_provenance") != plugin_component.get("guardian_provenance"):
        return False
    owned_paths = existing.get("owned_paths")
    managed_targets = existing.get("managed_targets")
    if not isinstance(owned_paths, list) or not isinstance(managed_targets, list) or len(managed_targets) != len(MANAGED_PATH_RELATIVES):
        return False
    target_map = {item.get("relative_path"): item for item in managed_targets if isinstance(item, dict)}
    if set(target_map) != set(MANAGED_PATH_RELATIVES) or not all(_managed_target_fields(item) for item in managed_targets):
        return False
    if {item.get("relative_path") for item in owned_paths if isinstance(item, dict)} != {
        item["relative_path"] for item in managed_targets if item.get("ownership") == "owned"
    }:
        return False
    for target in managed_targets:
        try:
            path = _safe_relative_path(codex_home, target["relative_path"])
            info = path.lstat()
        except (BootstrapError, OSError):
            return False
        if (
            stat.S_ISLNK(info.st_mode)
            or _managed_kind(target["relative_path"]) != target["kind"]
            or not _identity_matches(info, target)
            or _tree_hash(path) != target["sha256"]
        ):
            return False
    for item in owned_paths:
        if not isinstance(item, dict) or not isinstance(item.get("relative_path"), str) or not isinstance(item.get("sha256"), str):
            return False
        try:
            path = _safe_relative_path(codex_home, item["relative_path"])
            info = path.lstat()
        except (BootstrapError, OSError):
            return False
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or not _identity_matches(info, item) or _tree_hash(path) != item["sha256"]:
            return False
    owned_plugins = existing.get("owned_plugins", [])
    if not isinstance(owned_plugins, list):
        return False
    # A historical receipt may claim plugin ownership, but Codex 0.146 does
    # not expose creator evidence that can re-authorize that claim.  Force an
    # apply migration instead of treating the legacy record as ready state.
    if owned_plugins:
        return False
    plan_by_name = {plan["plugin_name"]: plan for plan in plugin_plans}
    stored_tree_sha256: Dict[str, str] = {}
    for component in existing.get("components", []):
        if isinstance(component, dict) and component.get("name") == "codex-plugin-command":
            values = component.get("installed_tree_sha256", {})
            if isinstance(values, dict):
                stored_tree_sha256.update({name: value for name, value in values.items() if isinstance(name, str) and isinstance(value, str)})
    for item in owned_plugins:
        if not isinstance(item, dict) or item.get("name") not in plan_by_name:
            return False
        name = item["name"]
        if plan_by_name[name].get("status") != "present":
            return False
        observed_path = plugin_component.get("installed_paths", {}).get(name)
        if observed_path != item.get("relative_path"):
            return False
        try:
            path = _safe_relative_path(codex_home, item["relative_path"])
            info = path.lstat()
        except (BootstrapError, OSError, KeyError):
            return False
        if stat.S_ISLNK(info.st_mode) or not _identity_matches(info, item):
            return False
        observed_sha = plan_by_name[name].get("observed_sha")
        if not isinstance(observed_sha, str) or item.get("sha") != observed_sha:
            return False
        observed_tree_sha256 = plan_by_name[name].get("observed_tree_sha256")
        if name == "allinluna" and stored_tree_sha256.get(name) != observed_tree_sha256:
            return False
        if name == "allinluna":
            tree_proof = _content_tree_proof(path, codex_home)
            if (
                not isinstance(tree_proof, dict)
                or tree_proof.get("tree_sha256") != observed_tree_sha256
                or not _identity_matches(info, tree_proof)
            ):
                return False
    if any(path.get("relative_path") == "venvs/allinluna" for path in owned_paths if isinstance(path, dict)):
        if allin_component.get("status") != "present":
            return False
    return all(plan.get("status") != "conflict" for plan in plans)


def _build_managed_targets(
    codex_home: Path,
    existing: Optional[Dict[str, Any]],
    created: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Record every managed target, including exact pre-existing assets."""
    prior_targets = {
        item.get("relative_path"): item
        for item in (existing or {}).get("managed_targets", [])
        if isinstance(item, dict)
    }
    prior_owned = {
        item.get("relative_path"): item
        for item in (existing or {}).get("owned_paths", [])
        if isinstance(item, dict)
    }
    recreated = {
        item.get("relative_path")
        for item in created
        if isinstance(item, dict) and isinstance(item.get("relative_path"), str)
    }

    def validate_prior_proof(relative: Any, item: Any) -> None:
        if (
            not isinstance(relative, str)
            or not isinstance(item, dict)
            or _managed_kind(relative) != item.get("kind")
            or not SHA256_RE.fullmatch(str(item.get("sha256", "")))
            or not isinstance(item.get("device"), int)
            or isinstance(item.get("device"), bool)
            or item["device"] < 0
            or not isinstance(item.get("inode"), int)
            or isinstance(item.get("inode"), bool)
            or item["inode"] < 0
        ):
            raise BootstrapError("prior managed target proof is invalid")
        path = _safe_relative_path(codex_home, relative)
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            # A receipt-owned target may have disappeared before apply.  It is
            # recoverable only when this transaction has already published a
            # new, independently attested inode for the same fixed target.
            # Any un-recreated disappearance remains a hard ownership failure.
            if relative in recreated:
                return
            raise BootstrapError("prior managed target disappeared") from exc
        except OSError as exc:
            raise BootstrapError("prior managed target disappeared") from exc
        if relative in recreated:
            return
        proof = _managed_target_trust(path, codex_home, item["sha256"])
        if proof is None or any(proof.get(key) != item.get(key) for key in ("device", "inode")):
            raise BootstrapError("prior managed target proof changed")

    for relative, item in prior_owned.items():
        validate_prior_proof(relative, item)
    for relative, target in prior_targets.items():
        if isinstance(target, dict) and target.get("ownership") == "owned":
            prior = prior_owned.get(relative)
            if prior is None or any(target.get(key) != prior.get(key) for key in ("kind", "sha256", "device", "inode")):
                raise BootstrapError("prior managed target ownership proof is inconsistent")
    created_map: Dict[str, Dict[str, Any]] = {}
    for item in created:
        if not isinstance(item, dict) or not isinstance(item.get("relative_path"), str):
            raise BootstrapError("created managed target proof is invalid")
        relative = item["relative_path"]
        if relative in created_map or _managed_kind(relative) != item.get("kind"):
            raise BootstrapError("created managed target is outside the fixed set")
        if (
            not SHA256_RE.fullmatch(str(item.get("sha256", "")))
            or not isinstance(item.get("device"), int)
            or isinstance(item.get("device"), bool)
            or item["device"] < 0
            or not isinstance(item.get("inode"), int)
            or isinstance(item.get("inode"), bool)
            or item["inode"] < 0
        ):
            raise BootstrapError("created managed target proof is invalid")
        path = _safe_relative_path(codex_home, relative)
        try:
            info = path.lstat()
        except OSError as exc:
            raise BootstrapError("created managed target disappeared") from exc
        proof = _managed_target_trust(path, codex_home, item["sha256"])
        if proof is None or any(proof.get(key) != item.get(key) for key in ("device", "inode")):
            raise BootstrapError("created managed target proof changed")
        created_map[relative] = item
    targets: List[Dict[str, Any]] = []
    owned_paths: List[Dict[str, Any]] = []
    for relative in sorted(MANAGED_PATH_RELATIVES):
        kind = _managed_kind(relative)
        if kind is None:
            raise BootstrapError("managed target kind is undefined")
        path = _safe_relative_path(codex_home, relative)
        info = path.lstat()
        proof = _managed_target_trust(path, codex_home)
        expected = proof.get("sha256") if proof is not None else None
        if proof is None or stat.S_ISLNK(info.st_mode):
            raise BootstrapError("managed target proof is unavailable")
        is_owned = relative in created_map or relative in prior_owned or prior_targets.get(relative, {}).get("ownership") == "owned"
        target = {
            "relative_path": relative,
            "kind": kind,
            "ownership": "owned" if is_owned else "preexisting-unowned",
            "sha256": expected,
            "device": int(proof["device"]),
            "inode": int(proof["inode"]),
            "proof": "transaction-owned" if is_owned else "preexisting-unowned",
        }
        targets.append(target)
        if is_owned:
            owned_paths.append({
                "relative_path": relative,
                "sha256": expected,
                "kind": kind,
                "device": int(proof["device"]),
                "inode": int(proof["inode"]),
            })
    return targets, owned_paths


def _receipt_replaced_paths(codex_home: Path, receipt: Optional[Dict[str, Any]]) -> List[str]:
    """Identify receipt-owned bytes that moved to a new live filesystem object."""
    replaced: List[str] = []
    if not isinstance(receipt, dict):
        return replaced
    for item in receipt.get("owned_paths", []):
        if not isinstance(item, dict) or not isinstance(item.get("relative_path"), str):
            continue
        try:
            path = _safe_relative_path(codex_home, item["relative_path"])
            info = path.lstat()
        except (BootstrapError, OSError):
            continue
        if (
            stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
        ) and _tree_hash(path) == item.get("sha256") and not _identity_matches(info, item):
            replaced.append(item["relative_path"])
    return replaced


def _copy_one(
    plan: Dict[str, Any],
    stage_callback: Optional[Callable[[str, Dict[str, int], str, Optional[str]], None]] = None,
) -> Dict[str, int]:
    source = Path(plan["source"])
    relative = str(plan["relative_path"])
    relative_parts = Path(relative).parts
    if (
        not relative_parts
        or os.path.isabs(relative)
        or any(part in ("", ".", "..") for part in relative_parts)
    ):
        raise BootstrapError("copy relative path is invalid")
    codex_home_value = plan.get("codex_home")
    if codex_home_value is None:
        # Keep direct unit-level callers safe while production plans remain
        # explicit: derive the anchor from the target and the validated
        # relative path, never from an untrusted parent guess.
        codex_home = _absolute_lexical(Path(plan["target"]))
        for _ in relative_parts:
            codex_home = codex_home.parent
    else:
        codex_home = _absolute_lexical(Path(codex_home_value))
    target = _safe_relative_path(codex_home, relative)
    if _absolute_lexical(Path(plan["target"])) != target:
        raise BootstrapError("copy target does not match its relative path")
    source_anchor_value = plan.get("source_anchor")
    source_anchor = _absolute_lexical(Path(source_anchor_value)) if source_anchor_value is not None else _absolute_lexical(source.parent)
    source_proof = plan.get("source_proof")
    if not _source_asset_matches(source, source_anchor, source_proof):
        raise BootstrapError("frozen source proof changed before copy")
    _reject_source_symlinks(source)
    directory_fd, target_name = _open_relative_parent(codex_home, relative)
    temporary_name: Optional[str] = None
    temporary_ownership: Optional[Dict[str, int]] = None
    temporary_hash: Optional[str] = None
    stage_callback_failed = False
    try:
        try:
            os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(target_name)
        planned_stage_relative = plan.get("staging_relative_path")
        planned_stage_name = Path(str(planned_stage_relative)).name if isinstance(planned_stage_relative, str) else None
        stage_relative: Optional[str] = planned_stage_relative if isinstance(planned_stage_relative, str) else None

        def notify_stage(ownership: Dict[str, int], state: str, stage_hash: Optional[str]) -> None:
            nonlocal stage_callback_failed, stage_relative
            if stage_callback is None:
                return
            if stage_relative is None:
                stage_relative = _relative_path_string(target.parent / str(temporary_name), codex_home)
            try:
                stage_callback(stage_relative, ownership, state, stage_hash)
            except BaseException:
                stage_callback_failed = True
                raise

        def assert_temporary_identity() -> None:
            if temporary_name is None or temporary_ownership is None:
                raise BootstrapError("temporary copy ownership is unavailable")
            current = os.stat(temporary_name, dir_fd=directory_fd, follow_symlinks=False)
            if not _identity_matches(current, temporary_ownership):
                raise BootstrapError("temporary copy was replaced during staging")

        if source.is_dir():
            temporary_name = _temporary_directory_at(
                directory_fd,
                planned_stage_name or _stable_temp_name(".guardian-copy-", relative),
            )
            temporary_fd = os.open(temporary_name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                ownership = _identity(os.fstat(temporary_fd))
            finally:
                os.close(temporary_fd)
            temporary_ownership = ownership
            notify_stage(ownership, "staged", None)
            assert_temporary_identity()
            _copy_tree_at(source, directory_fd, temporary_name)
        else:
            temporary_name, temporary_fd = _temporary_file_at(
                directory_fd,
                planned_stage_name or _stable_temp_name(".guardian-copy-", relative),
            )
            source_info = source.lstat()
            source_descriptor = os.open(str(source), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(source_descriptor, "rb") as source_handle, os.fdopen(temporary_fd, "wb") as target_handle:
                ownership = _identity(os.fstat(target_handle.fileno()))
                temporary_ownership = ownership
                notify_stage(ownership, "staged", None)
                assert_temporary_identity()
                opened_source = os.fstat(source_handle.fileno())
                if not _same_stat(source_info, opened_source):
                    raise BootstrapError("source changed during copy")
                _copy_stream_bounded(source_handle, target_handle, {"entries": 1, "file_bytes": 0})
                os.fchmod(target_handle.fileno(), stat.S_IMODE(source_info.st_mode))
                target_handle.flush()
                os.fsync(target_handle.fileno())
            if not _source_asset_matches(source, source_anchor, source_proof):
                raise BootstrapError("frozen source proof changed during copy")
            # The publish proof is required even when no journal callback is
            # supplied; otherwise a same-bytes foreign scratch inode could be
            # mistaken for this transaction's output.
            temporary_hash = _tree_hash_at(directory_fd, temporary_name)
            if temporary_hash != plan.get("expected_sha256"):
                raise BootstrapError("staged asset hash mismatch")
            assert_temporary_identity()
            notify_stage(ownership, "publishing", temporary_hash)
        if source.is_dir():
            temporary_hash = _tree_hash_at(directory_fd, temporary_name)
            if temporary_hash != plan.get("expected_sha256"):
                raise BootstrapError("staged asset hash mismatch")
            assert_temporary_identity()
            notify_stage(ownership, "publishing", temporary_hash)
        if temporary_hash is None:
            raise BootstrapError("staged asset proof is unavailable")
        _rename_noreplace_at(
            directory_fd,
            temporary_name,
            target_name,
            source_ownership=temporary_ownership,
            source_digest=temporary_hash,
        )
        live = os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            temporary_ownership is None
            or not _identity_matches(live, temporary_ownership)
            or _tree_hash_at(directory_fd, target_name) != temporary_hash
        ):
            raise BootstrapError("published asset proof failed")
        temporary_name = None
        return dict(temporary_ownership)
    finally:
        if temporary_name is not None and temporary_ownership is not None and not stage_callback_failed:
            try:
                ownership = dict(temporary_ownership)
                if temporary_hash is not None:
                    ownership["sha256"] = temporary_hash
                _remove_staged_artifact(
                    target.parent / temporary_name,
                    ownership,
                    codex_home,
                )
            except (OSError, BootstrapError):
                pass
        os.close(directory_fd)


def _remove_owned(
    path: Path,
    expected: str,
    anchor: Optional[Path] = None,
    ownership: Optional[Dict[str, Any]] = None,
    quarantine_callback: Optional[Callable[[str, Optional[Dict[str, int]], str, Optional[str]], None]] = None,
) -> str:
    """Quarantine, revalidate, and remove only the exact inode recorded as owned."""
    try:
        _assert_safe_path(path, allow_missing=False, anchor=anchor)
    except (BootstrapError, OSError):
        return "preserved-modified"
    try:
        info = path.lstat()
    except OSError:
        return "preserved-modified"
    if not _identity_matches(info, ownership):
        return "preserved-modified"
    actual = _tree_hash(path)
    if actual != expected:
        return "preserved-modified"
    root = _absolute_lexical(anchor or path.parent)
    try:
        relative = _relative_path_string(path, root)
        directory_fd, target_name = _open_relative_parent(root, relative)
    except (BootstrapError, OSError, ValueError):
        return "preserved-modified"
    quarantine_name: Optional[str] = None
    quarantine_relative: Optional[str] = None
    quarantine_identity: Optional[Dict[str, int]] = None
    try:
        current = os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
        if not _identity_matches(current, ownership) or _tree_hash(path) != expected:
            return "preserved-modified"
        quarantine_name = _stable_temp_name(".guardian-remove-", relative)
        quarantine_relative = _relative_path_string(path.parent / quarantine_name, root)
        if quarantine_callback is not None:
            quarantine_callback(quarantine_relative, None, "planned", expected)
        _rename_noreplace_at(directory_fd, target_name, quarantine_name)
        quarantined = os.stat(quarantine_name, dir_fd=directory_fd, follow_symlinks=False)
        quarantine_identity = _identity(quarantined)
        if quarantine_callback is not None:
            quarantine_callback(quarantine_relative, quarantine_identity, "quarantined", expected)
        if not _identity_matches(quarantined, ownership) or _tree_hash_at(directory_fd, quarantine_name) != expected:
            try:
                _rename_noreplace_at(
                    directory_fd,
                    quarantine_name,
                    target_name,
                    source_ownership=quarantine_identity,
                    source_digest=expected,
                )
                quarantine_name = None
            except (BootstrapError, OSError):
                pass
            return "preserved-modified"
        if stat.S_ISDIR(quarantined.st_mode):
            _remove_tree_at(directory_fd, quarantine_name, expected, ownership)
        else:
            if not _unlink_at_owned(directory_fd, quarantine_name, quarantine_identity, expected):
                raise BootstrapError("owned file could not be removed")
        if quarantine_callback is not None:
            quarantine_callback(quarantine_relative, quarantine_identity, "done", expected)
        quarantine_name = None
        return "removed"
    except (BootstrapError, OSError):
        return "remove-failed"
    finally:
        if quarantine_name is not None:
            try:
                os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                if quarantine_identity is not None:
                    try:
                        _rename_noreplace_at(
                            directory_fd,
                            quarantine_name,
                            target_name,
                            source_ownership=quarantine_identity,
                            source_digest=expected,
                        )
                        quarantine_name = None
                    except (BootstrapError, OSError):
                        pass
            except OSError:
                pass
        os.close(directory_fd)


def _receipt_paths(codex_home: Path) -> Tuple[Path, Path]:
    return _absolute_lexical(codex_home / RECEIPT_RELATIVE), _absolute_lexical(codex_home / RECEIPT_HASH_RELATIVE)


def _receipt_state_digest(receipt: Dict[str, Any]) -> str:
    # Device/inode values are live ownership guards, not semantic receipt state:
    # a proven recovery may recreate the same bytes with new inode numbers.
    owned_paths = []
    for item in receipt.get("owned_paths", []):
        if isinstance(item, dict):
            owned_paths.append({key: item.get(key) for key in ("relative_path", "sha256", "kind")})
        else:
            owned_paths.append(item)
    state = {
        key: receipt.get(key)
        for key in (
            "mode", "guardian_ref", "guardian_provenance", "owned_plugins", "managed_targets",
            "home_device", "home_inode",
        )
    }
    state["owned_paths"] = owned_paths
    encoded = json.dumps(state, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(encoded.encode("utf-8"))


def _assert_private_receipt(receipt: Dict[str, Any]) -> None:
    """Validate the complete on-disk receipt, including private proofs."""
    if not isinstance(receipt, dict):
        raise BootstrapError("receipt is not an object")

    def no_absolute(value: Any) -> None:
        if isinstance(value, str) and (os.path.isabs(value) or re.match(r"^[A-Za-z]:[\\/]", value)):
            raise BootstrapError("receipt contains an absolute machine path")
        if isinstance(value, dict):
            for child in value.values():
                no_absolute(child)
        elif isinstance(value, list):
            for child in value:
                no_absolute(child)

    no_absolute(receipt)
    allowed = {
        "schema", "generation", "state_digest", "mode", "platform", "status", "components", "owned_paths",
        "owned_plugins", "rollback", "notes", "would_write", "existing_receipt", "guardian_ref",
        "guardian_provenance", "conflicts", "failure", "recovery", "managed_targets", "home_device", "home_inode",
    }
    if set(receipt) - allowed or receipt.get("schema") != SCHEMA:
        raise BootstrapError("receipt schema is invalid")
    if not isinstance(receipt.get("generation"), int) or isinstance(receipt.get("generation"), bool) or receipt["generation"] < 1:
        raise BootstrapError("receipt generation is invalid")
    if not isinstance(receipt.get("state_digest"), str) or not SHA256_RE.fullmatch(receipt["state_digest"]):
        raise BootstrapError("receipt state digest is invalid")
    if receipt["state_digest"] != _receipt_state_digest(receipt):
        raise BootstrapError("receipt state digest does not match state")
    if receipt.get("mode") not in ("check", "apply", "uninstall") or receipt.get("platform") != "darwin":
        raise BootstrapError("receipt operation is invalid")
    if not isinstance(receipt.get("components"), list) or not isinstance(receipt.get("owned_paths"), list) or not isinstance(receipt.get("owned_plugins"), list):
        raise BootstrapError("receipt collections are invalid")
    for key in ("home_device", "home_inode"):
        if key in receipt and (
            not isinstance(receipt[key], int) or isinstance(receipt[key], bool) or receipt[key] < 0
        ):
            raise BootstrapError("receipt CODEX_HOME identity is invalid")
    component_allowed = {
        "name", "status", "reason", "version", "minimum_version", "expected_version", "version_skew", "mode",
        "command", "installed", "available", "installed_paths", "installed_sha", "installed_tree_sha256", "expected_tree_sha256", "guardian_ref", "guardian_provenance", "marketplace_root_relative",
        "hits", "collisions", "selector", "expected_sha", "hooks", "relative_path", "sha256", "path_relative",
        "python_relative", "python_minimum", "wheel_sha256", "version", "managed_venv_relative_path", "dependencies",
        "actions", "duplicates",
    }
    for item in receipt["components"]:
        if not isinstance(item, dict) or set(item) - component_allowed or not isinstance(item.get("name"), str) or not isinstance(item.get("status"), str):
            raise BootstrapError("receipt component schema is invalid")
        if "version_skew" in item and not isinstance(item["version_skew"], bool):
            raise BootstrapError("receipt component version schema is invalid")
        for key in ("installed", "available"):
            if key in item:
                values = item[key]
                if not isinstance(values, dict) or set(values) - {"allinluna", "ponytail"} or not all(isinstance(value, bool) for value in values.values()):
                    raise BootstrapError("receipt plugin state schema is invalid")
        for key in ("installed_paths", "installed_sha", "installed_tree_sha256", "expected_tree_sha256"):
            if key in item:
                values = item[key]
                if (
                    not isinstance(values, dict)
                    or set(values) - {"allinluna", "ponytail"}
                    or not all(
                        isinstance(value, str)
                        and not os.path.isabs(value)
                        and value
                        and not any(part in ("", ".", "..") for part in Path(value).parts)
                        for value in values.values()
                    )
                ):
                    raise BootstrapError("receipt plugin attestation schema is invalid")
        if "installed_sha" in item and not all(COMMIT_RE.fullmatch(value) for value in item["installed_sha"].values()):
            raise BootstrapError("receipt plugin attestation schema is invalid")
        for key in ("installed_tree_sha256", "expected_tree_sha256"):
            if key in item and not all(SHA256_RE.fullmatch(value) for value in item[key].values()):
                raise BootstrapError("receipt plugin tree attestation schema is invalid")
        if "hits" in item:
            hits = item["hits"]
            if not isinstance(hits, list):
                raise BootstrapError("receipt plugin hit schema is invalid")
            for hit in hits:
                if not isinstance(hit, dict) or set(hit) != {"plugin", "location", "marketplace", "source", "installed", "enabled"}:
                    raise BootstrapError("receipt plugin hit schema is invalid")
                if hit["plugin"] not in ("codex-workflow-guardian", "allinluna", "ponytail", "other") or hit["location"] not in ("installed", "available") or hit["marketplace"] not in ("expected", "foreign") or hit["source"] not in ("verified", "mismatch") or not isinstance(hit["installed"], bool) or not isinstance(hit["enabled"], bool):
                    raise BootstrapError("receipt plugin hit schema is invalid")
        if "actions" in item:
            if not isinstance(item["actions"], list):
                raise BootstrapError("receipt action schema is invalid")
            for action in item["actions"]:
                if not isinstance(action, dict) or set(action) - {"path", "selector", "action"}:
                    raise BootstrapError("receipt action schema is invalid")
                if not isinstance(action.get("action"), str) or set(action) - {"path", "selector", "action"}:
                    raise BootstrapError("receipt action schema is invalid")
                for key in ("path", "selector"):
                    if key in action and (not isinstance(action[key], str) or os.path.isabs(action[key])):
                        raise BootstrapError("receipt action path is invalid")
    for item in receipt["owned_paths"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"relative_path", "sha256", "kind", "device", "inode"}
            or not isinstance(item["relative_path"], str)
            or os.path.isabs(item["relative_path"])
            or not SHA256_RE.fullmatch(str(item["sha256"]))
            or item["kind"] not in ("file", "directory")
            or not isinstance(item["device"], int)
            or isinstance(item["device"], bool)
            or item["device"] < 0
            or not isinstance(item["inode"], int)
            or isinstance(item["inode"], bool)
            or item["inode"] < 0
        ):
            raise BootstrapError("receipt owned path schema is invalid")
    owned_relative = set()
    for item in receipt["owned_paths"]:
        relative = item["relative_path"]
        if relative in owned_relative or _managed_kind(relative) != item["kind"]:
            raise BootstrapError("receipt owned path is outside the fixed managed set")
        owned_relative.add(relative)
    managed_targets = receipt.get("managed_targets")
    if managed_targets is not None:
        if (
            not isinstance(managed_targets, list)
            or len(managed_targets) != len(MANAGED_PATH_RELATIVES)
            or not all(_managed_target_fields(item) for item in managed_targets)
        ):
            raise BootstrapError("receipt managed target schema is invalid")
        target_map = {item["relative_path"]: item for item in managed_targets}
        if set(target_map) != set(MANAGED_PATH_RELATIVES):
            raise BootstrapError("receipt managed target coverage is incomplete")
        if owned_relative != {
            item["relative_path"] for item in managed_targets if item["ownership"] == "owned"
        }:
            raise BootstrapError("receipt managed target ownership does not match owned paths")
        for item in receipt["owned_paths"]:
            target = target_map[item["relative_path"]]
            if target["ownership"] != "owned" or any(target[key] != item[key] for key in ("kind", "sha256", "device", "inode")):
                raise BootstrapError("receipt owned path proof does not match managed target")
    elif receipt.get("mode") == "apply" and receipt.get("status") == "ready":
        raise BootstrapError("receipt managed target coverage is missing")
    for item in receipt["owned_plugins"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"selector", "name", "sha", "hooks", "relative_path", "device", "inode"}
            or not isinstance(item.get("selector"), str)
            or item.get("name") not in MANAGED_PLUGIN_NAMES
            or item.get("selector") not in MANAGED_PLUGIN_SELECTORS
            or not COMMIT_RE.fullmatch(str(item.get("sha")))
            or not isinstance(item.get("relative_path"), str)
            or os.path.isabs(item["relative_path"])
            or not item["relative_path"]
            or any(part in ("", ".", "..") for part in Path(item["relative_path"]).parts)
            or not isinstance(item.get("device"), int)
            or isinstance(item["device"], bool)
            or item["device"] < 0
            or not isinstance(item.get("inode"), int)
            or isinstance(item["inode"], bool)
            or item["inode"] < 0
        ):
            raise BootstrapError("receipt owned plugin schema is invalid")
    if len({item["relative_path"] for item in receipt["owned_paths"]}) != len(receipt["owned_paths"]):
        raise BootstrapError("receipt owned paths are not unique")
    if len({item["selector"] for item in receipt["owned_plugins"]}) != len(receipt["owned_plugins"]):
        raise BootstrapError("receipt owned plugins are not unique")
    rollback = receipt.get("rollback")
    if not isinstance(rollback, dict) or set(rollback) != {"performed", "actions"} or not isinstance(rollback.get("performed"), bool) or not isinstance(rollback.get("actions"), list):
        raise BootstrapError("receipt rollback schema is invalid")
    for action in rollback["actions"]:
        if not isinstance(action, dict) or set(action) - {"path", "selector", "action"}:
            raise BootstrapError("receipt rollback action schema is invalid")
        for key in ("path", "selector", "action"):
            if key in action and (not isinstance(action[key], str) or os.path.isabs(action[key])):
                raise BootstrapError("receipt rollback path is invalid")
    for key in ("would_write", "notes"):
        if key in receipt and (not isinstance(receipt[key], list) or not all(isinstance(item, str) and not os.path.isabs(item) for item in receipt[key])):
            raise BootstrapError("receipt text collection is invalid")
    if "guardian_ref" in receipt and receipt["guardian_ref"] is not None and not COMMIT_RE.fullmatch(str(receipt["guardian_ref"])):
        raise BootstrapError("receipt Guardian ref is invalid")
    if "conflicts" in receipt:
        if not isinstance(receipt["conflicts"], list):
            raise BootstrapError("receipt conflict schema is invalid")
        for conflict in receipt["conflicts"]:
            if not isinstance(conflict, dict) or set(conflict) - {"name", "reason", "relative_path"} or not isinstance(conflict.get("name"), str) or not isinstance(conflict.get("reason"), str):
                raise BootstrapError("receipt conflict schema is invalid")
            if "relative_path" in conflict and (not isinstance(conflict["relative_path"], str) or os.path.isabs(conflict["relative_path"])):
                raise BootstrapError("receipt conflict path is invalid")
    for key in ("existing_receipt", "failure", "recovery", "guardian_provenance"):
        if key in receipt and not isinstance(receipt[key], str):
            raise BootstrapError("receipt text schema is invalid")


def _read_existing_receipt(codex_home: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    receipt_path, hash_path = _receipt_paths(codex_home)
    try:
        _assert_safe_path(receipt_path, anchor=codex_home)
        _assert_safe_path(hash_path, anchor=codex_home)
        try:
            receipt_exists = receipt_path.lstat()
        except FileNotFoundError:
            receipt_exists = None
        try:
            hash_exists = hash_path.lstat()
        except FileNotFoundError:
            hash_exists = None
    except (BootstrapError, OSError):
        return None, "receipt path contains a symlink or special file"
    if receipt_exists is None and hash_exists is None:
        return None, None
    if receipt_exists is None or hash_exists is None or not _private_state_file(receipt_path, receipt_exists) or not _private_state_file(hash_path, hash_exists):
        return None, "receipt pair is incomplete"
    try:
        receipt_identity = _identity(receipt_exists)
        sidecar_identity = _identity(hash_exists)
        raw = _read_stable_bytes(receipt_path, MAX_JSON_BYTES, codex_home)
        sidecar = _read_stable_bytes(hash_path, MAX_SIDECAR_BYTES, codex_home)
        if not _identity_matches(receipt_path.lstat(), receipt_identity) or not _identity_matches(hash_path.lstat(), sidecar_identity):
            return None, "receipt is unreadable"
        expected = sidecar.decode("utf-8").strip()
        value = _safe_json_loads(raw, "bootstrap receipt")
    except (OSError, UnicodeError, BootstrapError):
        return None, "receipt is unreadable"
    if _sha256_bytes(raw) != expected or not isinstance(value, dict):
        return None, "receipt integrity check failed"
    try:
        _assert_private_receipt(value)
    except BootstrapError:
        return None, "receipt schema or semantic integrity check failed"
    try:
        home_identity = _home_identity(codex_home)
        if value.get("home_device") is None or value.get("home_inode") is None or value.get("home_device") != home_identity["home_device"] or value.get("home_inode") != home_identity["home_inode"]:
            return None, "receipt CODEX_HOME identity does not match"
    except (BootstrapError, OSError):
        return None, "receipt CODEX_HOME identity cannot be verified"
    return value, None


def _receipt_file_digest(
    path: Path,
    limit: int,
    codex_home: Path,
) -> Tuple[bool, Optional[str]]:
    """Return (present, digest); a present non-regular/unreadable file is unknown."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False, None
    except (BootstrapError, OSError):
        return True, None
    if not _private_state_file(path, info):
        return True, None
    try:
        return True, _sha256_bytes(_read_stable_bytes(path, limit, codex_home))
    except (BootstrapError, OSError):
        return True, None


def _receipt_pair_digests(codex_home: Path) -> Tuple[bool, Optional[str], bool, Optional[str]]:
    receipt_path, hash_path = _receipt_paths(codex_home)
    receipt_present, receipt_digest = _receipt_file_digest(receipt_path, MAX_JSON_BYTES, codex_home)
    sidecar_present, sidecar_digest = _receipt_file_digest(hash_path, MAX_SIDECAR_BYTES, codex_home)
    return receipt_present, receipt_digest, sidecar_present, sidecar_digest


def _receipt_pair_matches(
    pair: Tuple[bool, Optional[str], bool, Optional[str]],
    receipt_digest: Optional[str],
    sidecar_digest: Optional[str],
) -> bool:
    receipt_present, actual_receipt, sidecar_present, actual_sidecar = pair
    return (
        (receipt_present if receipt_digest is not None else not receipt_present)
        and (receipt_digest is None or actual_receipt == receipt_digest)
        and (sidecar_present if sidecar_digest is not None else not sidecar_present)
        and (sidecar_digest is None or actual_sidecar == sidecar_digest)
    )


def _receipt_backup_target(path: Path) -> Path:
    return path.parent / (".guardian-receipt-old-" + path.name)


def _remove_receipt_scratch(
    path: Path,
    identity: Dict[str, int],
    digest: str,
) -> bool:
    try:
        if not _private_state_file(path, path.lstat()) or not _identity_matches(path.lstat(), identity):
            return False
    except (BootstrapError, OSError):
        return False
    return _unlink_path_owned(path, identity, digest)


def _receipt_backup_path(
    codex_home: Path,
    path: Path,
    content: Optional[bytes],
    on_created: Optional[Callable[[Path, Dict[str, int], str], None]] = None,
) -> Optional[Path]:
    if content is None:
        return None
    backup = _receipt_backup_target(path)
    created_proof: Optional[Tuple[Dict[str, int], str]] = None
    try:
        _assert_safe_path(backup, allow_missing=True, anchor=codex_home)
        descriptor = os.open(
            str(backup),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            os.close(descriptor)
            raise
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_safe_path(backup, allow_missing=False, anchor=codex_home)
        created_proof = (_identity(backup.lstat()), _sha256_bytes(content))
        if on_created is not None:
            on_created(backup, *created_proof)
        return backup
    except OSError:
        if created_proof is not None:
            _remove_receipt_scratch(backup, *created_proof)
        raise
    except BaseException:
        # A journal callback may have failed after the scratch inode became
        # durable.  Keep it for recovery instead of deleting unproven data.
        raise


def _cleanup_receipt_backups(
    paths: Iterable[Optional[Path]],
    proofs: Dict[Path, Tuple[Dict[str, int], str]],
) -> None:
    changed: set = set()
    for path in paths:
        if path is None:
            continue
        proof = proofs.get(path)
        if proof is not None and _remove_receipt_scratch(path, *proof):
            changed.add(path.parent)
    for parent in changed:
        try:
            _fsync_directory(parent)
        except OSError:
            pass


def _write_receipt(
    codex_home: Path,
    receipt: Dict[str, Any],
    before_replace: Optional[Callable[[Dict[str, Optional[str]]], None]] = None,
) -> Tuple[List[Path], Optional[str]]:
    receipt_path, hash_path = _receipt_paths(codex_home)
    candidate = copy.deepcopy(receipt)
    current, _ = _read_existing_receipt(codex_home)
    try:
        live_home = _home_identity(codex_home)
    except (BootstrapError, OSError):
        return [], "receipt write failed"
    if any(
        key in candidate and candidate.get(key) is not None and candidate.get(key) != value
        for key, value in live_home.items()
    ):
        return [], "receipt CODEX_HOME identity does not match"
    candidate.update(live_home)
    prior_generation = int(current.get("generation", 0)) if isinstance(current, dict) else 0
    supplied_generation = candidate.get("generation")
    if not isinstance(supplied_generation, int) or isinstance(supplied_generation, bool):
        supplied_generation = 0
    candidate["generation"] = max(supplied_generation, prior_generation + 1, 1)
    candidate["schema"] = SCHEMA
    candidate["platform"] = "darwin"
    candidate["state_digest"] = _receipt_state_digest(candidate)
    try:
        _assert_private_receipt(candidate)
    except BootstrapError as exc:
        return [], str(exc)
    try:
        raw = (json.dumps(candidate, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        return [], "receipt contains unsupported data"
    digest = _sha256_bytes(raw)
    sidecar = (digest + "\n").encode("ascii")
    try:
        _assert_safe_path(codex_home, anchor=codex_home)
        _assert_safe_path(receipt_path, anchor=codex_home)
        _assert_safe_path(hash_path, anchor=codex_home)
        _ensure_workflow_guardian_directory(codex_home)
        _assert_safe_path(receipt_path.parent, allow_missing=False, anchor=codex_home)
    except (OSError, BootstrapError):
        return [], "receipt write failed"
    old_receipt: Optional[bytes] = None
    old_hash: Optional[bytes] = None
    old_receipt_proof: Optional[Tuple[Dict[str, int], str]] = None
    old_hash_proof: Optional[Tuple[Dict[str, int], str]] = None
    try:
        try:
            receipt_info = receipt_path.lstat()
        except FileNotFoundError:
            receipt_info = None
        if receipt_info is not None:
            if not _private_state_file(receipt_path, receipt_info):
                return [], "receipt write failed"
            old_receipt = _read_stable_bytes(receipt_path, MAX_JSON_BYTES, codex_home)
            old_receipt_proof = (_identity(receipt_info), _sha256_bytes(old_receipt))
        try:
            hash_info = hash_path.lstat()
        except FileNotFoundError:
            hash_info = None
        if hash_info is not None:
            if not _private_state_file(hash_path, hash_info):
                return [], "receipt write failed"
            old_hash = _read_stable_bytes(hash_path, MAX_SIDECAR_BYTES, codex_home)
            old_hash_proof = (_identity(hash_info), _sha256_bytes(old_hash))
    except (BootstrapError, OSError):
        return [], "receipt write failed"
    if old_receipt == raw and old_hash == sidecar:
        return [], None
    temporary: List[Path] = [
        receipt_path.parent / ".guardian-receipt-new.json",
        receipt_path.parent / ".guardian-receipt-new.sha256",
    ]
    backups: List[Optional[Path]] = [None, None]
    scratch_proofs: Dict[Path, Tuple[Dict[str, int], str]] = {}

    def record_scratch(path: Path, identity: Dict[str, int], digest: str) -> None:
        scratch_proofs[path] = (identity, digest)
        if before_replace is None:
            return
        name = path.name
        if name == ".guardian-receipt-old-bootstrap-receipt.json":
            prefix = "prior_receipt_backup"
        elif name == ".guardian-receipt-old-bootstrap-receipt.sha256":
            prefix = "prior_sidecar_backup"
        elif name == ".guardian-receipt-new.json":
            prefix = "new_receipt_temp"
        elif name == ".guardian-receipt-new.sha256":
            prefix = "new_sidecar_temp"
        else:
            raise BootstrapError("receipt scratch path is not deterministic")
        before_replace({
            prefix + "_device": identity["device"],
            prefix + "_inode": identity["inode"],
            prefix + "_sha256": digest,
        })

    try:
        if before_replace is not None:
            backup_targets = [
                _receipt_backup_target(receipt_path) if old_receipt is not None else None,
                _receipt_backup_target(hash_path) if old_hash is not None else None,
            ]
            before_replace(
                {
                    "new_receipt_sha256": _sha256_bytes(raw),
                    "new_sidecar_sha256": _sha256_bytes(sidecar),
                    "prior_receipt_sha256": _sha256_bytes(old_receipt) if old_receipt is not None else None,
                    "prior_sidecar_sha256": _sha256_bytes(old_hash) if old_hash is not None else None,
                    "prior_receipt_backup_relative_path": _relative_path_string(backup_targets[0], codex_home) if backup_targets[0] is not None else None,
                    "prior_sidecar_backup_relative_path": _relative_path_string(backup_targets[1], codex_home) if backup_targets[1] is not None else None,
                    "new_receipt_temp_relative_path": _relative_path_string(temporary[0], codex_home),
                    "new_sidecar_temp_relative_path": _relative_path_string(temporary[1], codex_home),
                }
            )
            backups[0] = _receipt_backup_path(codex_home, receipt_path, old_receipt, record_scratch)
            backups[1] = _receipt_backup_path(codex_home, hash_path, old_hash, record_scratch)
        for temporary_path, content in zip(temporary, (raw, sidecar)):
            try:
                temporary_info = temporary_path.lstat()
            except FileNotFoundError:
                temporary_info = None
            if temporary_info is not None:
                return [], "receipt temporary path is already occupied"
            descriptor = os.open(
                str(temporary_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
            except OSError:
                os.close(descriptor)
                raise
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            record_scratch(temporary_path, _identity(temporary_path.lstat()), _sha256_bytes(content))
        new_receipt_proof = scratch_proofs.get(temporary[0])
        if new_receipt_proof is None:
            raise BootstrapError("receipt temporary ownership is unavailable")
        _replace_from_owned_scratch(
            temporary[0],
            receipt_path,
            *new_receipt_proof,
            target_proof=old_receipt_proof,
            anchor=codex_home,
            source_mode=0o600,
        )
        scratch_proofs.pop(temporary[0], None)
        temporary.pop(0)
        _fsync_directory(receipt_path.parent)
        new_hash_proof = scratch_proofs.get(temporary[0])
        if new_hash_proof is None:
            raise BootstrapError("sidecar temporary ownership is unavailable")
        _replace_from_owned_scratch(
            temporary[0],
            hash_path,
            *new_hash_proof,
            target_proof=old_hash_proof,
            anchor=codex_home,
            source_mode=0o600,
        )
        scratch_proofs.pop(temporary[0], None)
        temporary.pop(0)
        _fsync_directory(receipt_path.parent)
        _cleanup_receipt_backups(backups, scratch_proofs)
    except (BootstrapError, OSError):
        for item, proof in list(scratch_proofs.items()):
            _remove_receipt_scratch(item, *proof)
        try:
            def restore_previous(
                path: Path,
                content: Optional[bytes],
                old_proof: Optional[Tuple[Dict[str, int], str]],
                new_proof: Optional[Tuple[Dict[str, int], str]],
            ) -> None:
                if content is None:
                    _restore_file(path, None)
                    return
                try:
                    current_info = path.lstat()
                except FileNotFoundError:
                    _restore_file(path, content)
                    return
                current_digest = _sha256_file(path)
                current_proof = (_identity(current_info), current_digest)
                if old_proof is not None and current_proof == old_proof:
                    return
                if new_proof is None or current_proof != new_proof:
                    raise BootstrapError("receipt target changed during rollback")
                _restore_file(path, content, target_proof=new_proof)

            restore_previous(receipt_path, old_receipt, old_receipt_proof, locals().get("new_receipt_proof"))
            restore_previous(hash_path, old_hash, old_hash_proof, locals().get("new_hash_proof"))
            _cleanup_receipt_backups(backups, scratch_proofs)
            _fsync_directory(receipt_path.parent)
        except OSError:
            return [], "receipt write failed"
        return [], "receipt write failed"
    return [receipt_path, hash_path], None


def _journal_digest(journal: Dict[str, Any]) -> str:
    value = {key: journal.get(key) for key in ("schema", "generation", "mode", "phase", "steps", "home_device", "home_inode")}
    return _sha256_bytes(json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _validate_journal(journal: Dict[str, Any]) -> None:
    if not isinstance(journal, dict) or set(journal) != {"schema", "generation", "digest", "mode", "phase", "steps", "home_device", "home_inode"}:
        raise BootstrapError("transaction journal schema is invalid")
    if journal.get("schema") != JOURNAL_SCHEMA or journal.get("mode") not in ("apply", "uninstall") or journal.get("phase") not in ("PREPARED", "APPLYING", "COMMITTING", "UNINSTALLING"):
        raise BootstrapError("transaction journal state is invalid")
    if not isinstance(journal.get("generation"), int) or isinstance(journal["generation"], bool) or journal["generation"] < 1 or not SHA256_RE.fullmatch(str(journal.get("digest"))):
        raise BootstrapError("transaction journal generation is invalid")
    if journal["digest"] != _journal_digest(journal) or not isinstance(journal.get("steps"), list):
        raise BootstrapError("transaction journal digest is invalid")
    for key in ("home_device", "home_inode"):
        if not isinstance(journal.get(key), int) or isinstance(journal[key], bool) or journal[key] < 0:
            raise BootstrapError("transaction journal CODEX_HOME identity is invalid")
    seen_ids: set = set()
    receipt_steps = 0
    for step in journal["steps"]:
        if not isinstance(step, dict) or not set(step) <= {
            "id", "kind", "action", "state", "relative_path", "staging_relative_path", "resolver_digest",
            "sha256", "commit", "selector", "device", "inode", "preexisting", "quarantine_relative_path",
            "quarantine_state", "quarantine_device", "quarantine_inode", "quarantine_sha256",
            "new_receipt_sha256", "new_sidecar_sha256", "prior_receipt_sha256", "prior_sidecar_sha256",
            "prior_receipt_backup_relative_path", "prior_sidecar_backup_relative_path",
            "new_receipt_temp_relative_path", "new_sidecar_temp_relative_path",
            "prior_receipt_backup_device", "prior_receipt_backup_inode",
            "prior_receipt_backup_sha256", "prior_sidecar_backup_sha256",
            "prior_sidecar_backup_device", "prior_sidecar_backup_inode",
            "new_receipt_temp_device", "new_receipt_temp_inode", "new_receipt_temp_sha256",
            "new_sidecar_temp_device", "new_sidecar_temp_inode", "new_sidecar_temp_sha256",
        } or not {"id", "kind", "action", "state", "relative_path", "sha256", "commit", "selector"} <= set(step):
            raise BootstrapError("transaction journal step schema is invalid")
        if not isinstance(step.get("id"), str) or step.get("kind") not in ("plugin", "file", "venv", "receipt") or step.get("action") not in ("add", "publish", "remove", "write") or step.get("state") not in ("planned", "staged", "building", "publishing", "started", "owned", "intent", "done", "failed"):
            raise BootstrapError("transaction journal step state is invalid")
        if step["id"] in seen_ids:
            raise BootstrapError("transaction journal step ids are not unique")
        seen_ids.add(step["id"])
        if not isinstance(step.get("relative_path"), str) or os.path.isabs(step["relative_path"]):
            raise BootstrapError("transaction journal path is invalid")
        if step.get("staging_relative_path") is not None and (not isinstance(step.get("staging_relative_path"), str) or os.path.isabs(step["staging_relative_path"])):
            raise BootstrapError("transaction journal staging path is invalid")
        if step.get("quarantine_relative_path") is not None and (not isinstance(step.get("quarantine_relative_path"), str) or os.path.isabs(step["quarantine_relative_path"])):
            raise BootstrapError("transaction journal quarantine path is invalid")
        if step.get("quarantine_state") not in (None, "planned", "quarantined"):
            raise BootstrapError("transaction journal quarantine state is invalid")
        if step.get("resolver_digest") is not None and not SHA256_RE.fullmatch(str(step["resolver_digest"])):
            raise BootstrapError("transaction journal resolver digest is invalid")
        if step.get("sha256") is not None and not SHA256_RE.fullmatch(str(step["sha256"])):
            raise BootstrapError("transaction journal hash is invalid")
        for key in (
            "quarantine_sha256", "new_receipt_sha256", "new_sidecar_sha256", "prior_receipt_sha256", "prior_sidecar_sha256",
        ):
            if step.get(key) is not None and not SHA256_RE.fullmatch(str(step[key])):
                raise BootstrapError("transaction journal digest is invalid")
        for key in (
            "prior_receipt_backup_relative_path", "prior_sidecar_backup_relative_path",
            "new_receipt_temp_relative_path", "new_sidecar_temp_relative_path",
        ):
            if step.get(key) is not None and (not isinstance(step[key], str) or os.path.isabs(step[key])):
                raise BootstrapError("transaction journal receipt backup path is invalid")
        for key in (
            "prior_receipt_backup_device", "prior_receipt_backup_inode",
            "prior_sidecar_backup_device", "prior_sidecar_backup_inode",
            "new_receipt_temp_device", "new_receipt_temp_inode",
            "new_sidecar_temp_device", "new_sidecar_temp_inode",
        ):
            if key in step and step[key] is not None and (not isinstance(step[key], int) or isinstance(step[key], bool) or step[key] < 0):
                raise BootstrapError("transaction journal scratch identity is invalid")
        for key in (
            "prior_receipt_backup_sha256", "prior_sidecar_backup_sha256",
            "new_receipt_temp_sha256", "new_sidecar_temp_sha256",
        ):
            if key in step and step[key] is not None and not SHA256_RE.fullmatch(str(step[key])):
                raise BootstrapError("transaction journal scratch digest is invalid")
        if "preexisting" in step and not isinstance(step["preexisting"], bool):
            raise BootstrapError("transaction journal preexisting flag is invalid")
        if step.get("selector") is not None and not isinstance(step["selector"], str):
            raise BootstrapError("transaction journal selector is invalid")
        for key in ("device", "inode"):
            if key in step and step[key] is not None and (not isinstance(step[key], int) or isinstance(step[key], bool) or step[key] < 0):
                raise BootstrapError("transaction journal ownership identity is invalid")
        for key in ("quarantine_device", "quarantine_inode"):
            if key in step and step[key] is not None and (not isinstance(step[key], int) or isinstance(step[key], bool) or step[key] < 0):
                raise BootstrapError("transaction journal quarantine identity is invalid")
        kind = step["kind"]
        action = step["action"]
        if kind == "receipt":
            receipt_steps += 1
        if kind == "plugin":
            name = step["id"].split(":", 1)[-1] if isinstance(step.get("id"), str) else ""
            if action not in ("add", "remove") or step.get("id") != f"plugin:{name}" or name not in MANAGED_PLUGIN_NAMES or step["selector"] != f"{name}@{WORKFLOW_MARKETPLACE}" or (action == "remove" and step["relative_path"]) or step.get("staging_relative_path") is not None or step.get("quarantine_relative_path") is not None or (step.get("resolver_digest") is not None and step.get("preexisting") is not True) or not isinstance(step.get("preexisting"), bool) or step["sha256"] is not None or not isinstance(step.get("selector"), str) or not isinstance(step.get("commit"), str) or not COMMIT_RE.fullmatch(step["commit"]):
                raise BootstrapError("transaction journal plugin step is invalid")
            if action == "add" and step["relative_path"] and any(part in ("", ".", "..") for part in Path(step["relative_path"]).parts):
                raise BootstrapError("transaction journal plugin path is invalid")
        elif kind == "file":
            if action not in ("publish", "remove") or step["id"] != f"file:{step['relative_path']}" or step["relative_path"] not in MANAGED_ROLE_RELATIVES or step["selector"] is not None or step["commit"] is not None or step.get("resolver_digest") is not None or not isinstance(step.get("sha256"), str) or (action == "publish" and step.get("staging_relative_path") != _copy_staging_relative_path(step["relative_path"])):
                raise BootstrapError("transaction journal file step is invalid")
        elif kind == "venv":
            if action not in ("publish", "remove") or step["id"] != "venv:allinluna" or step["relative_path"] != MANAGED_VENV_RELATIVE or step["selector"] is not None or step["commit"] is not None or step.get("resolver_digest") is not None or (step["sha256"] is not None and not isinstance(step["sha256"], str)) or (action == "publish" and step.get("staging_relative_path") != "venvs/.guardian-venv-allinluna"):
                raise BootstrapError("transaction journal venv step is invalid")
        elif kind == "receipt":
            if action not in ("write", "remove") or step["relative_path"] != RECEIPT_RELATIVE.as_posix() or step.get("staging_relative_path") is not None or step.get("quarantine_relative_path") is not None or step.get("resolver_digest") is not None or step["sha256"] is not None or step["commit"] is not None or step["selector"] is not None:
                raise BootstrapError("transaction journal receipt step is invalid")
            if action == "write":
                expected_paths = {
                    "prior_receipt_backup_relative_path": (RECEIPT_RELATIVE.parent / ".guardian-receipt-old-bootstrap-receipt.json").as_posix(),
                    "prior_sidecar_backup_relative_path": (RECEIPT_RELATIVE.parent / ".guardian-receipt-old-bootstrap-receipt.sha256").as_posix(),
                    "new_receipt_temp_relative_path": (RECEIPT_RELATIVE.parent / ".guardian-receipt-new.json").as_posix(),
                    "new_sidecar_temp_relative_path": (RECEIPT_RELATIVE.parent / ".guardian-receipt-new.sha256").as_posix(),
                }
                if any(step.get(key) not in (None, expected) for key, expected in expected_paths.items()):
                    raise BootstrapError("transaction journal receipt path is not deterministic")
    if receipt_steps != 1:
        raise BootstrapError("transaction journal must contain exactly one receipt step")


def _read_journal(codex_home: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    path = _journal_path(codex_home)
    try:
        _assert_safe_path(path, anchor=codex_home)
        info = path.lstat()
    except FileNotFoundError:
        return None, None
    except (BootstrapError, OSError):
        return None, "transaction journal path is unsafe"
    if not _private_state_file(path, info):
        return None, "transaction journal is not a regular file"
    try:
        journal = _safe_json_loads(_read_stable_bytes(path, MAX_JSON_BYTES, codex_home), "transaction journal")
        _validate_journal(journal)
    except (OSError, BootstrapError):
        return None, "transaction journal is invalid"
    try:
        home = _home_identity(codex_home)
        if journal.get("home_device") != home["home_device"] or journal.get("home_inode") != home["home_inode"]:
            return None, "transaction journal CODEX_HOME identity does not match"
    except (BootstrapError, OSError):
        return None, "transaction journal CODEX_HOME identity cannot be verified"
    return journal, None


def _write_journal(codex_home: Path, journal: Dict[str, Any]) -> Optional[str]:
    candidate = copy.deepcopy(journal)
    candidate["schema"] = JOURNAL_SCHEMA
    current, _ = _read_journal(codex_home)
    candidate.setdefault("generation", int(current.get("generation", 0)) + 1 if isinstance(current, dict) else 1)
    try:
        home = _home_identity(codex_home)
    except (BootstrapError, OSError):
        return "transaction journal CODEX_HOME identity cannot be verified"
    if any(key in candidate and candidate.get(key) is not None and candidate.get(key) != value for key, value in home.items()):
        return "transaction journal CODEX_HOME identity does not match"
    candidate.update(home)
    candidate["digest"] = _journal_digest(candidate)
    try:
        _validate_journal(candidate)
        path = _journal_path(codex_home)
        _ensure_workflow_guardian_directory(codex_home)
        _assert_safe_path(path.parent, allow_missing=False, anchor=codex_home)
        target_proof: Optional[Tuple[Dict[str, int], str]] = None
        try:
            target_info = path.lstat()
        except FileNotFoundError:
            target_info = None
        if target_info is not None:
            if not _private_state_file(path, target_info):
                return "transaction journal target is unsafe"
            target_proof = (_identity(target_info), _sha256_file(path, MAX_JSON_BYTES, codex_home))
        temporary = path.parent / ".guardian-journal.tmp"
        try:
            temp_info = temporary.lstat()
        except FileNotFoundError:
            temp_info = None
        if temp_info is not None:
            return "transaction journal temporary path is already occupied"
        temporary_proof: Optional[Tuple[Dict[str, int], str]] = None
        descriptor = os.open(
            str(temporary),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            os.close(descriptor)
            raise
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((json.dumps(candidate, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary_proof = (_identity(temporary.lstat()), _sha256_file(temporary, MAX_JSON_BYTES, codex_home))
        _replace_from_owned_scratch(
            temporary,
            path,
            *temporary_proof,
            target_proof=target_proof,
            anchor=codex_home,
            source_mode=0o600,
        )
        _fsync_directory(path.parent)
        return None
    except (BootstrapError, OSError, TypeError, ValueError) as exc:
        if "temporary" in locals() and temporary_proof is not None:
            _remove_receipt_scratch(temporary, *temporary_proof)
        return str(exc)


def _remove_journal(codex_home: Path) -> bool:
    try:
        path = _journal_path(codex_home)
        _assert_safe_path(path, anchor=codex_home)
        temporary = path.parent / ".guardian-journal.tmp"
        try:
            temp_info = temporary.lstat()
        except FileNotFoundError:
            temp_info = None
        if temp_info is not None:
            if stat.S_ISLNK(temp_info.st_mode) or not stat.S_ISREG(temp_info.st_mode):
                return False
            # No transaction record carries an identity/hash proof for this
            # scratch inode.  Preserve both files so recovery can inspect it.
            return False
        info = path.lstat()
        if not _private_state_file(path, info):
            return False
        digest = _sha256_file(path, MAX_JSON_BYTES, codex_home)
        if not _unlink_path_owned(path, _identity(info), digest, codex_home):
            return False
        _fsync_directory(path.parent)
        return True
    except FileNotFoundError:
        # A leftover temporary has no durable ownership proof when the
        # journal itself is absent.  Preserve it for inspection/recovery.
        return not (_journal_path(codex_home).parent / ".guardian-journal.tmp").exists()
    except (BootstrapError, OSError):
        return False


def _journal_step(codex_home: Path, journal: Dict[str, Any], step_id: str, state: str) -> Optional[str]:
    for step in journal.get("steps", []):
        if step.get("id") == step_id:
            step["state"] = state
            journal["generation"] = int(journal.get("generation", 0)) + 1
            return _write_journal(codex_home, journal)
    return "transaction journal step is missing"


def _journal_quarantine_callback(
    codex_home: Path,
    journal: Dict[str, Any],
    step_id: str,
) -> Callable[[str, Optional[Dict[str, int]], str, Optional[str]], None]:
    def persist(
        relative: str,
        identity: Optional[Dict[str, int]],
        state: str,
        expected: Optional[str],
    ) -> None:
        for step in journal.get("steps", []):
            if step.get("id") != step_id:
                continue
            if state == "done":
                step["quarantine_relative_path"] = None
                step["quarantine_state"] = None
                step["quarantine_device"] = None
                step["quarantine_inode"] = None
                step["quarantine_sha256"] = None
            else:
                step["quarantine_relative_path"] = relative
                step["quarantine_state"] = state
                step["quarantine_device"] = identity.get("device") if identity is not None else None
                step["quarantine_inode"] = identity.get("inode") if identity is not None else None
                step["quarantine_sha256"] = expected
            journal["generation"] = int(journal.get("generation", 0)) + 1
            error = _write_journal(codex_home, journal)
            if error:
                raise BootstrapError("transaction journal update failed")
            return
        raise BootstrapError("transaction journal step is missing")

    return persist


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_file(
    path: Path,
    content: Optional[bytes],
    target_proof: Optional[Tuple[Dict[str, int], str]] = None,
) -> None:
    if content is None:
        try:
            path.lstat()
        except FileNotFoundError:
            return
        raise BootstrapError("restore target was created concurrently")
    temporary = path.parent / _stable_temp_name(".guardian-restore-", _absolute_lexical(path).as_posix())
    try:
        info = temporary.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BootstrapError("restore temporary path is unsafe")
        raise BootstrapError("restore temporary path is already occupied")
    descriptor = os.open(
        str(temporary),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
    except OSError:
        os.close(descriptor)
        raise
    temporary_identity: Optional[Dict[str, int]] = None
    temporary_digest = _sha256_bytes(content)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_identity = _identity(temporary.lstat())
        _replace_from_owned_scratch(
            temporary,
            path,
            temporary_identity,
            temporary_digest,
            target_proof=target_proof,
            source_mode=0o600,
        )
    finally:
        if temporary_identity is not None:
            _unlink_path_owned(temporary, temporary_identity, temporary_digest)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise BootstrapError("pinned artifact redirect is refused")


def _open_exact_url(url: str, timeout: float):
    opener = urllib.request.build_opener(_NoRedirect())
    return opener.open(urllib.request.Request(url, method="GET"), timeout=timeout)


def _set_response_socket_timeout(response: Any, timeout: float) -> None:
    """Refresh a urllib response socket timeout without requiring private types."""
    pending = [response]
    seen: set = set()
    while pending and len(seen) < 16:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        setter = getattr(current, "settimeout", None)
        if callable(setter):
            setter(timeout)
            return
        for attribute in ("fp", "raw", "_sock", "sock", "_connection"):
            try:
                child = getattr(current, attribute, None)
            except (AttributeError, OSError):
                child = None
            if child is not None:
                pending.append(child)


def _allinluna_install(
    lock: Dict[str, Any],
    codex_home: Path,
    requested_python: Optional[str],
    stage_callback: Optional[Callable[[str, Dict[str, int], str, Optional[str]], None]] = None,
    repository_root: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Optional[Path], List[Dict[str, Any]]]:
    deadline = time.monotonic() + WHEEL_DEADLINE_SECONDS

    def remaining_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BootstrapError("pinned wheel setup deadline exceeded")
        return max(0.001, remaining)

    component = _allinluna_component(lock, codex_home, _python_component(), requested_python, repository_root)
    if component.get("status") != "planned":
        return component, None, []
    interpreter = _allinluna_python(lock, requested_python, codex_home, repository_root)
    if interpreter is None:
        return {"name": "allinluna", "status": "unavailable", "reason": "no Python >=3.11 interpreter found"}, None, []
    root = _absolute_lexical(codex_home / "venvs" / "allinluna")
    staging: Optional[Path] = None
    staging_relative: Optional[str] = None
    venvs_fd: Optional[int] = None
    staging_name: Optional[str] = None
    staging_created = False
    staging_identity: Optional[Dict[str, int]] = None
    staging_hash: Optional[str] = None
    pypi = lock.get("components", {}).get("allinluna", {}).get("pypi", {})
    wheel = pypi.get("wheel", {}) if isinstance(pypi, dict) else {}
    wheel_filename = _validated_wheel_filename(wheel, pypi)
    if wheel_filename is None:
        return {"name": "allinluna", "status": "unavailable", "reason": "wheel filename pin is invalid"}, None, []
    wheel_path: Optional[Path] = None
    wheel_identity: Optional[Dict[str, int]] = None
    wheel_digest: Optional[str] = None
    wheel_size: Optional[int] = None
    wheel_fd: Optional[int] = None
    wheel_external_change = False
    created: List[Dict[str, Any]] = []
    owner_marker: Optional[Path] = None
    owner_bytes = OWNER_MARKER_BYTES
    stage_callback_failed = False
    staging_cleanup_failed = False

    def preserve_staging_unverified() -> None:
        if staging is None or not staging_created or staging_identity is None or staging_relative is None:
            return
        if any(item.get("path") == staging for item in created):
            return
        created.append(
            {
                "path": staging,
                "relative_path": staging_relative,
                "sha256": None,
                "kind": "directory",
                **staging_identity,
            }
        )

    def cleanup_wheel() -> None:
        nonlocal wheel_path, wheel_external_change
        if wheel_path is None:
            return
        if wheel_identity is None:
            try:
                wheel_external_change = wheel_external_change or wheel_path.exists() or wheel_path.is_symlink()
            except OSError:
                wheel_external_change = True
            return
        try:
            info = wheel_path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not _identity_matches(info, wheel_identity):
                wheel_external_change = True
                return
            if wheel_digest is not None and _sha256_file(wheel_path, MAX_WHEEL_BYTES, codex_home) != wheel_digest:
                wheel_external_change = True
                return
            if _unlink_path_owned(wheel_path, wheel_identity, wheel_digest, codex_home):
                wheel_path = None
            else:
                wheel_external_change = True
        except (BootstrapError, OSError):
            wheel_external_change = True

    def cleanup_staging() -> None:
        nonlocal staging_cleanup_failed
        if wheel_external_change or stage_callback_failed or staging is None or not staging_created or staging_identity is None:
            return
        ownership: Dict[str, Any] = dict(staging_identity)
        if staging_hash is not None:
            ownership["sha256"] = staging_hash
        try:
            if not _remove_staged_artifact(staging, ownership, codex_home, require_empty=staging_hash is None):
                staging_cleanup_failed = True
        except (BootstrapError, OSError):
            staging_cleanup_failed = True

    def assert_stage_boundary() -> None:
        if venvs_fd is None or staging_name is None or staging_identity is None:
            raise BootstrapError("managed venv staging identity is unavailable")
        _validate_open_directory(venvs_fd, root.parent)
        try:
            current = os.stat(staging_name, dir_fd=venvs_fd, follow_symlinks=False)
        except OSError as exc:
            raise BootstrapError("managed venv staging disappeared") from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or current.st_uid != os.getuid()
            or current.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not _identity_matches(current, staging_identity)
        ):
            raise BootstrapError("managed venv parent or staging identity changed")
        _assert_safe_path(staging, allow_missing=False, anchor=codex_home)

    def assert_wheel_boundary() -> None:
        if wheel_path is None or wheel_identity is None or wheel_digest is None or wheel_size is None:
            raise BootstrapError("pinned wheel proof is unavailable")
        try:
            current = wheel_path.lstat()
        except OSError as exc:
            raise BootstrapError("pinned wheel disappeared") from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or not _identity_matches(current, wheel_identity)
            or current.st_size != wheel_size
            or stat.S_IMODE(current.st_mode) != 0o600
            or _sha256_file(wheel_path, MAX_WHEEL_BYTES, codex_home) != wheel_digest
        ):
            raise BootstrapError("pinned wheel proof changed")

    try:
        _assert_safe_path(codex_home, allow_missing=False, anchor=codex_home)
        venvs_fd, target_name = _open_relative_parent(codex_home, MANAGED_VENV_RELATIVE)
        try:
            os.stat(target_name, dir_fd=venvs_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BootstrapError("managed venv target already exists")
        staging_name = ".guardian-venv-allinluna"
        _temporary_directory_at(venvs_fd, staging_name)
        staging = root.parent / staging_name
        staging_created = True
        staging_relative = _relative_path_string(staging, codex_home)
        stage_fd = os.open(
            staging_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=venvs_fd,
        )
        try:
            staging_identity = _identity(os.fstat(stage_fd))
        finally:
            os.close(stage_fd)
        assert_stage_boundary()

        def report_stage(state: str, stage_hash: Optional[str]) -> None:
            nonlocal stage_callback_failed
            if stage_callback is None:
                return
            try:
                stage_callback(staging_relative, staging_identity, state, stage_hash)
            except BaseException:
                stage_callback_failed = True
                raise

        if stage_callback is not None:
            report_stage("staged", None)
        owner_marker = staging / "guardian-bootstrap-owner.json"
        owner_marker.write_bytes(owner_bytes)
        if _sha256_file(owner_marker, MAX_MARKER_BYTES, codex_home) != _sha256_bytes(owner_bytes):
            raise BootstrapError("managed venv ownership marker failed")
        if stage_callback is not None:
            report_stage("building", None)
        assert_stage_boundary()
        interpreter_spec = _python_launch_spec(interpreter, codex_home)
        if interpreter_spec is None:
            raise BootstrapError("selected Python executable trust verification failed")
        code, stdout, stderr = _run_argv(
            [interpreter_spec, "-I", "-m", "venv", "--copies", str(staging)],
            codex_home,
            repository_root or REPOSITORY_ROOT,
            timeout=min(60, remaining_timeout()),
        )
        del stdout, stderr
        assert_stage_boundary()
        if code != 0:
            raise BootstrapError("managed venv creation failed")
        for hardened in (staging, staging / "bin"):
            _assert_safe_path(hardened, allow_missing=False, anchor=codex_home)
            hardened_info = hardened.lstat()
            if (
                stat.S_ISLNK(hardened_info.st_mode)
                or not stat.S_ISDIR(hardened_info.st_mode)
                or hardened_info.st_uid != os.getuid()
                or hardened_info.st_mode & (stat.S_ISUID | stat.S_ISGID)
                or _has_acl(hardened)
            ):
                raise BootstrapError("managed venv Python parent is unsafe")
            os.chmod(hardened, 0o700)
        parsed_url = urllib.parse.urlparse(str(wheel["url"]))
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "files.pythonhosted.org"
            or parsed_url.port is not None
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise BootstrapError("pinned wheel URL is not an approved HTTPS artifact")
        with _open_exact_url(str(wheel["url"]), timeout=min(30, remaining_timeout())) as response:
            declared_text = response.headers.get("Content-Length") if getattr(response, "headers", None) is not None else None
            declared = None
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except (TypeError, ValueError):
                    raise BootstrapError("pinned wheel declared size is invalid")
                if declared < 0 or declared > MAX_WHEEL_BYTES:
                    raise BootstrapError("pinned wheel declared size is invalid")
            expected_size = wheel.get("size")
            if expected_size is not None and (not isinstance(expected_size, int) or expected_size < 0 or declared != expected_size):
                raise BootstrapError("pinned wheel declared size differs from the lock")
            wheel_path = staging / wheel_filename
            _assert_safe_path(wheel_path, allow_missing=True, anchor=codex_home)
            try:
                wheel_fd = os.open(
                    str(wheel_path),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError as exc:
                # The regular file may be foreign even when its bytes look
                # plausible.  Preserve the whole staging inode for the outer
                # transaction rollback instead of claiming ownership.
                wheel_external_change = True
                raise BootstrapError("pinned wheel staging filename already exists") from exc
            opened_wheel = os.fstat(wheel_fd)
            if (
                not stat.S_ISREG(opened_wheel.st_mode)
                or opened_wheel.st_uid != os.getuid()
                or stat.S_IMODE(opened_wheel.st_mode) != 0o600
                or _has_acl(wheel_path)
            ):
                raise BootstrapError("pinned wheel staging file is unsafe")
            wheel_identity = _identity(opened_wheel)
            digest = hashlib.sha256()
            size = 0
            try:
                output = os.fdopen(wheel_fd, "wb")
                wheel_fd = None
            except OSError:
                os.close(wheel_fd)
                wheel_fd = None
                raise
            with output:
                while True:
                    _set_response_socket_timeout(response, remaining_timeout())
                    chunk = response.read(1024 * 1024)
                    remaining_timeout()
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_WHEEL_BYTES:
                        raise BootstrapError("pinned wheel is too large")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if declared is not None and size != declared:
                raise BootstrapError("pinned wheel streamed size differs from its declaration")
            wheel_size = size
        wheel_digest = digest.hexdigest()
        if wheel_digest != wheel["sha256"]:
            raise BootstrapError("pinned wheel hash mismatch")
        if (
            wheel_identity is None
            or wheel_size is None
            or not _identity_matches(wheel_path.lstat(), wheel_identity)
            or wheel_path.lstat().st_size != wheel_size
            or stat.S_IMODE(wheel_path.lstat().st_mode) != 0o600
            or _sha256_file(wheel_path, MAX_WHEEL_BYTES, codex_home) != wheel_digest
        ):
            raise BootstrapError("pinned wheel scratch changed during verification")
        venv_python = staging / "bin" / "python"
        assert_stage_boundary()
        _assert_safe_path(venv_python, allow_missing=False, anchor=codex_home)
        venv_python_info = venv_python.lstat()
        if (
            stat.S_ISLNK(venv_python_info.st_mode)
            or not stat.S_ISREG(venv_python_info.st_mode)
            or venv_python_info.st_uid != os.getuid()
            or venv_python_info.st_mode & (stat.S_ISUID | stat.S_ISGID)
            or _has_acl(venv_python)
        ):
            raise BootstrapError("managed venv Python file is unsafe")
        os.chmod(venv_python, 0o700)
        venv_python_spec = _python_launch_spec(venv_python, codex_home, venv=True)
        if venv_python_spec is None:
            raise BootstrapError("managed venv Python executable trust verification failed")
        assert_wheel_boundary()
        code, stdout, stderr = _run_argv(
            [venv_python_spec, "-I", "-m", "pip", "--isolated", "install", "--no-cache-dir", "--no-deps", "--no-index", "--disable-pip-version-check", str(wheel_path)],
            codex_home,
            repository_root or REPOSITORY_ROOT,
            timeout=min(120, remaining_timeout()),
        )
        del stdout, stderr
        assert_stage_boundary()
        assert_wheel_boundary()
        if code != 0:
            raise BootstrapError("pinned wheel installation failed")
        marker = staging / "guardian-install.json"
        expected_version = str(lock["components"]["allinluna"]["pypi"].get("version", ""))
        assert_stage_boundary()
        dependencies = None if staging is None else _verify_allinluna_runtime(staging, codex_home, expected_version, repository_root)
        assert_stage_boundary()
        if dependencies is None:
            raise BootstrapError("installed All in Luna distribution could not be verified")
        marker_data = {
            "component": "allinluna",
            "version": expected_version,
            "wheel_sha256": wheel["sha256"],
            "dependencies": dependencies,
        }
        _assert_safe_path(marker, anchor=codex_home)
        marker.write_text(json.dumps(marker_data, allow_nan=False, sort_keys=True) + "\n", encoding="utf-8")
        if wheel_path.exists():
            cleanup_wheel()
            if wheel_path is not None:
                raise BootstrapError("pinned wheel scratch could not be removed")
        assert_stage_boundary()
        staging_hash = _tree_hash_at(venvs_fd, staging_name)
        if staging_hash is None:
            raise BootstrapError("managed venv hash unavailable")
        if stage_callback is not None:
            report_stage("publishing", staging_hash)
        remaining_timeout()
        if (
            staging is None
            or staging_identity is None
            or staging_hash is None
            or not _identity_matches(os.stat(staging_name, dir_fd=venvs_fd, follow_symlinks=False), staging_identity)
            or _tree_hash_at(venvs_fd, staging_name) != staging_hash
        ):
            raise BootstrapError("managed venv staging proof changed before publish")
        _rename_noreplace_at(
            venvs_fd,
            staging_name,
            target_name,
            source_ownership=staging_identity,
            source_digest=staging_hash,
        )
        live_root = os.stat(target_name, dir_fd=venvs_fd, follow_symlinks=False)
        if not _identity_matches(live_root, staging_identity) or _tree_hash(root) != staging_hash:
            raise BootstrapError("managed venv publish proof failed")
        staging = None
        staging_name = None
        staging_identity = None
        component["status"] = "installed"
        component["path_relative"] = "venvs/allinluna/bin/allinluna"
        component["python_relative"] = "venvs/allinluna/bin/python"
        component.pop("reason", None)
        component["managed_venv_relative_path"] = "venvs/allinluna"
        created.append({"path": root, "sha256": staging_hash, "kind": "directory", "relative_path": "venvs/allinluna", **_identity(root.lstat())})
        return component, root, created
    except (BootstrapError, OSError, urllib.error.URLError):
        if wheel_external_change or stage_callback_failed or staging_cleanup_failed:
            preserve_staging_unverified()
        component = {"name": "allinluna", "status": "unavailable", "reason": "exact artifact setup failed"}
        return component, None, created
    finally:
        if wheel_fd is not None:
            try:
                os.close(wheel_fd)
            except OSError:
                pass
        cleanup_wheel()
        if wheel_external_change or stage_callback_failed or staging_cleanup_failed:
            preserve_staging_unverified()
        cleanup_staging()
        if staging_cleanup_failed:
            preserve_staging_unverified()
        if venvs_fd is not None:
            os.close(venvs_fd)


def _rollback(
    created: List[Dict[str, Any]],
    codex_home: Path,
    journal: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for item in reversed(created):
        path = item.get("path")
        expected = item.get("sha256")
        if not isinstance(path, Path):
            actions.append({"path": "<unverified-path>", "action": "preserved-unverified"})
            continue
        try:
            _assert_safe_path(path, allow_missing=False, anchor=codex_home)
        except (BootstrapError, OSError):
            try:
                relative = _relative_path_string(path, codex_home)
            except (BootstrapError, ValueError):
                try:
                    relative = _absolute_lexical(path).relative_to(_absolute_lexical(codex_home)).as_posix()
                except ValueError:
                    relative = "<unsafe-path>"
            actions.append({"path": relative, "action": "preserved-unsafe"})
            continue
        if not isinstance(expected, str):
            actions.append({"path": str(_absolute_lexical(path).relative_to(_absolute_lexical(codex_home))), "action": "preserved-unverified"})
            continue
        relative = _relative_path_string(path, codex_home)
        step_id = "venv:allinluna" if item.get("kind") == "directory" else f"file:{relative}"
        callback = _journal_quarantine_callback(codex_home, journal, step_id) if isinstance(journal, dict) else None
        action = _remove_owned(path, expected, anchor=codex_home, ownership=item, quarantine_callback=callback)
        actions.append({"path": relative, "action": action})
    return actions


def _base_receipt(mode: str) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generation": 1,
        "state_digest": "0" * 64,
        "mode": mode,
        "platform": "darwin",
        "components": [],
        "owned_paths": [],
        "owned_plugins": [],
        "rollback": {"performed": False, "actions": []},
        "notes": [
            "Codex is the action and permission authority; target-system evidence establishes live state.",
            "Role/model entitlements remain configured-unverified until a fresh Codex task returns receipts.",
        ],
    }


def _finalize_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    receipt["state_digest"] = _receipt_state_digest(receipt)
    return receipt


def _public_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Project the private receipt into a deliberately small stdout schema."""

    def safe_text(value: Any) -> str:
        if not isinstance(value, str):
            return "<redacted>"
        if (
            os.path.isabs(value)
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or "/" in value
            or "\\" in value
            or re.search(r"(?i)(?<![0-9a-f])[0-9a-f]{40}(?:[0-9a-f]{24})?(?![0-9a-f])", value)
        ):
            return "<redacted>"
        return value

    def public_component(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {"name": "unknown", "status": "unavailable", "reason": "invalid component"}
        result: Dict[str, Any] = {}
        for key in (
            "name", "status", "reason", "version", "minimum_version", "expected_version",
            "mode", "hooks", "python_minimum",
        ):
            if key in value:
                result[key] = safe_text(value[key])
        if isinstance(value.get("version_skew"), bool):
            result["version_skew"] = value["version_skew"]
        for key in ("installed", "available"):
            values = value.get(key)
            if isinstance(values, dict):
                result[key] = {
                    name: item
                    for name, item in values.items()
                    if name in MANAGED_PLUGIN_NAMES and isinstance(item, bool)
                }
        result.setdefault("name", "unknown")
        result.setdefault("status", "unavailable")
        return result

    def public_rollback(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {"performed": False, "actions": []}
        actions = value.get("actions")
        public_actions = []
        if isinstance(actions, list):
            for action in actions:
                if isinstance(action, dict) and isinstance(action.get("action"), str):
                    public_actions.append({"action": safe_text(action["action"])})
        return {"performed": bool(value.get("performed")), "actions": public_actions}

    def conflict_category(value: Any) -> str:
        name = value.get("name") if isinstance(value, dict) else None
        reason = value.get("reason") if isinstance(value, dict) else None
        if name == "receipt" or (isinstance(reason, str) and "receipt" in reason.lower()):
            return "receipt"
        if name == "guardian-source" or (isinstance(reason, str) and "guardian" in reason.lower()):
            return "guardian-source"
        if name == "transaction" or (isinstance(reason, str) and "transaction" in reason.lower()):
            return "transaction"
        if name in ("codex-cli", "python", "git", "dependency-lock"):
            return "tooling"
        if isinstance(name, str) and name.startswith("agent:"):
            return "managed-agent"
        if isinstance(name, str) and name.startswith("plugin:"):
            return "plugin"
        return "other"

    def planned_components(values: Any) -> List[Dict[str, str]]:
        if not isinstance(values, list):
            return []
        result: List[Dict[str, str]] = []
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("status"), str):
                continue
            name = item["name"]
            status = item["status"]
            if not (name.startswith("agent:") or name.startswith("plugin:") or name == "allinluna"):
                continue
            if status in ("present", "optional"):
                continue
            if name == "allinluna":
                action = "install-cli"
            elif name.startswith("agent:"):
                action = "publish"
            else:
                action = "install" if status in ("installed", "planned", "skipped") else "resolve"
            safe_name = safe_text(name)
            if safe_name != "<redacted>":
                result.append({"name": safe_name, "action": action})
        return result

    components = receipt.get("components") if isinstance(receipt.get("components"), list) else []
    planned = planned_components(components)
    conflicts = receipt.get("conflicts") if isinstance(receipt.get("conflicts"), list) else []
    conflict_categories = sorted({conflict_category(item) for item in conflicts if isinstance(item, dict)})
    guardian_component = next(
        (item for item in components if isinstance(item, dict) and item.get("name") == "codex-plugin-command"),
        {},
    )
    canonical_source_verified = (
        isinstance(guardian_component, dict)
        and guardian_component.get("status") == "present"
        and guardian_component.get("guardian_provenance") == "canonical git marketplace and clean exact Git checkout"
    )
    guardian_ref = guardian_component.get("guardian_ref") if isinstance(guardian_component, dict) else None
    guardian_ref_verified = canonical_source_verified and isinstance(guardian_ref, str) and COMMIT_RE.fullmatch(guardian_ref) is not None
    private_status = receipt.get("status")
    installation_status_value = private_status if isinstance(private_status, str) else None
    if receipt.get("mode") == "uninstall":
        uninstall_component = next(
            (item for item in components if isinstance(item, dict) and item.get("name") == "uninstall"),
            {},
        )
        uninstall_status = uninstall_component.get("status") if isinstance(uninstall_component, dict) else None
        if uninstall_status in ("present", "skipped"):
            installation_status_value = "not-installed" if uninstall_status == "skipped" else "removed"
        elif uninstall_status == "recovery-required":
            installation_status_value = "recovery-required"
        elif uninstall_status in ("conflict", "unavailable"):
            installation_status_value = "conflict"
        elif installation_status_value not in ("removed", "not-installed", "recovery-required", "conflict"):
            installation_status_value = "conflict"
    installation_status = safe_text(installation_status_value)
    if receipt.get("mode") == "uninstall":
        acceptance_level = "uninstalled" if installation_status_value in ("removed", "not-installed") else "transaction-recovery" if installation_status_value == "recovery-required" else "preflight"
    else:
        acceptance_level = "installed" if private_status == "ready" else "transaction-recovery" if private_status == "recovery-required" else "preflight"
    projected: Dict[str, Any] = {
        "schema": PUBLIC_SCHEMA,
        "projection": "public-redacted",
        "generation": receipt.get("generation") if isinstance(receipt.get("generation"), int) and not isinstance(receipt.get("generation"), bool) else 0,
        "mode": safe_text(receipt.get("mode")),
        "platform": safe_text(receipt.get("platform")),
        # Keep the legacy status key, but never leak a missing value as
        # ``<redacted>``.  Uninstall has an explicit removed/not-installed
        # vocabulary even though its private receipt historically omitted a
        # top-level status.
        "status": installation_status,
        "installation_status": installation_status,
        "capability_status": "configured-unverified",
        "acceptance_level": acceptance_level,
        "canonical_source_verified": canonical_source_verified,
        "guardian_ref_verified": guardian_ref_verified,
        "existing_receipt": "present" if receipt.get("existing_receipt") == "present" else "absent",
        "planned": {"count": len(planned), "components": planned},
        "conflict_summary": {"count": len(conflicts), "categories": conflict_categories},
        "components": [public_component(item) for item in components],
        "rollback": public_rollback(receipt.get("rollback")),
    }
    if isinstance(receipt.get("notes"), list):
        projected["notes"] = [safe_text(item) for item in receipt["notes"] if isinstance(item, str)]
    if isinstance(receipt.get("conflicts"), list):
        projected["conflicts"] = [
            {
                "name": safe_text(item.get("name")),
                "reason": conflict_category(item),
            }
            for item in receipt["conflicts"]
            if isinstance(item, dict)
        ]
    for key in ("failure", "recovery"):
        if isinstance(receipt.get(key), str):
            projected[key] = safe_text(receipt[key])
    return projected


def _preexisting_plugin_is_unchanged(
    codex_home: Path,
    step: Dict[str, Any],
    receipt: Optional[Dict[str, Any]],
    executable: Optional[Path],
    lock: Dict[str, Any],
    guardian_ref: Optional[str],
    git_bin: Optional[Path],
    repository_root: Optional[Path] = None,
    codex_version: Optional[Dict[str, Any]] = None,
) -> bool:
    """Prove a pre-existing plugin still matches the pinned install exactly."""
    if executable is None or step.get("kind") != "plugin" or step.get("preexisting") is not True:
        return False
    step_id = step.get("id")
    name = str(step_id).split(":", 1)[-1]
    selector = step.get("selector")
    if (
        not isinstance(step_id, str)
        or step_id != f"plugin:{name}"
        or step.get("action") != "add"
        or not name
        or selector != f"{name}@{WORKFLOW_MARKETPLACE}"
    ):
        return False
    if isinstance(receipt, dict) and any(
        isinstance(item, dict) and item.get("selector") == selector
        for item in receipt.get("owned_plugins", [])
    ):
        return False
    known_paths = _receipt_plugin_paths(receipt)
    relative = step.get("relative_path")
    if isinstance(relative, str) and relative:
        known_paths[name] = relative
    observed = _plugin_command_component(
        codex_home,
        executable,
        lock,
        guardian_ref,
        git_bin,
        known_paths,
        repository_root,
        codex_version=codex_version,
    )
    if observed.get("status") != "present" or not _plugin_observation_has_installed(observed, name):
        return False
    return _plugin_observation_proof(observed, codex_home, name, str(step.get("commit"))) is not None


def _preexisting_plugin_resolver_matches(
    codex_home: Path,
    step: Dict[str, Any],
    receipt: Optional[Dict[str, Any]],
    executable: Optional[Path],
    lock: Dict[str, Any],
    guardian_ref: Optional[str],
    git_bin: Optional[Path],
    repository_root: Optional[Path] = None,
    codex_version: Optional[Dict[str, Any]] = None,
) -> bool:
    if executable is None or step.get("preexisting") is not True:
        return False
    step_id = step.get("id")
    name = str(step_id).split(":", 1)[-1]
    selector = step.get("selector")
    if (
        not isinstance(step_id, str)
        or step_id != f"plugin:{name}"
        or step.get("action") != "add"
        or step.get("relative_path")
        or selector != f"{name}@{WORKFLOW_MARKETPLACE}"
        or not SHA256_RE.fullmatch(str(step.get("resolver_digest", "")))
    ):
        return False
    if isinstance(receipt, dict) and any(
        isinstance(item, dict) and item.get("selector") == selector
        for item in receipt.get("owned_plugins", [])
    ):
        return False
    observed = _plugin_command_component(
        codex_home,
        executable,
        lock,
        guardian_ref,
        git_bin,
        repository_root=repository_root,
        codex_version=codex_version,
    )
    if observed.get("status") != "present":
        return False
    if not any(
        isinstance(hit, dict)
        and hit.get("plugin") == name
        and hit.get("location") == "installed"
        and hit.get("installed") is True
        and hit.get("marketplace") == "expected"
        for hit in observed.get("hits", [])
    ):
        return False
    return _plugin_resolver_row_digest(codex_home, executable, name, repository_root) == step.get("resolver_digest")


def _receipt_proves_published_path(
    codex_home: Path,
    step: Dict[str, Any],
    path_map: Dict[str, Dict[str, Any]],
) -> bool:
    relative = step.get("relative_path")
    proof = path_map.get(relative) if isinstance(relative, str) else None
    if not isinstance(proof, dict) or proof.get("sha256") != step.get("sha256"):
        return False
    if not isinstance(step.get("sha256"), str) or not SHA256_RE.fullmatch(step["sha256"]):
        return False
    if not _identity_matches_safe(step):
        return False
    try:
        path = _safe_relative_path(codex_home, relative)
    except (BootstrapError, TypeError):
        return False
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        return False
    if not _identity_matches(info, step) or not _identity_matches(info, proof) or _tree_hash(path) != proof.get("sha256"):
        return False
    staging = step.get("staging_relative_path")
    if step.get("state") in ("started", "staged", "publishing") and (not isinstance(staging, str) or not staging or staging == relative):
        return False
    if isinstance(staging, str) and staging:
        if staging == relative:
            return False
        try:
            staging_path = _safe_relative_path(codex_home, staging)
        except BootstrapError:
            return False
        if staging_path.exists() or staging_path.is_symlink():
            return False
    return True


def _identity_matches_safe(value: Dict[str, Any]) -> bool:
    return (
        isinstance(value.get("device"), int)
        and not isinstance(value.get("device"), bool)
        and isinstance(value.get("inode"), int)
        and not isinstance(value.get("inode"), bool)
        and value["device"] >= 0
        and value["inode"] >= 0
    )


def _receipt_proves_apply_journal(
    codex_home: Path,
    journal: Dict[str, Any],
    receipt: Dict[str, Any],
    executable: Optional[Path],
    lock: Dict[str, Any],
    guardian_ref: Optional[str],
    git_bin: Optional[Path],
    repository_root: Optional[Path] = None,
    codex_version: Optional[Dict[str, Any]] = None,
) -> bool:
    """Require every journaled side effect to match the verified receipt state."""
    if not isinstance(guardian_ref, str) or receipt.get("guardian_ref") != guardian_ref:
        return False
    owned_paths = receipt.get("owned_paths")
    owned_plugins = receipt.get("owned_plugins")
    if not isinstance(owned_paths, list) or not isinstance(owned_plugins, list):
        return False
    path_map = {item.get("relative_path"): item for item in owned_paths if isinstance(item, dict)}
    plugin_map = {item.get("selector"): item for item in owned_plugins if isinstance(item, dict)}
    observed: Optional[Dict[str, Any]] = None
    for step in journal.get("steps", []):
        if not isinstance(step, dict) or step.get("kind") == "receipt":
            continue
        if step.get("kind") == "plugin" and step.get("preexisting") is True and step.get("state") == "failed":
            selector = step.get("selector")
            if isinstance(selector, str) and selector not in plugin_map:
                if step.get("relative_path"):
                    preexisting_proven = _preexisting_plugin_is_unchanged(
                        codex_home,
                        step,
                        receipt,
                        executable,
                        lock,
                        guardian_ref,
                        git_bin,
                        repository_root,
                        codex_version,
                    )
                else:
                    preexisting_proven = _preexisting_plugin_resolver_matches(
                        codex_home,
                        step,
                        receipt,
                        executable,
                        lock,
                        guardian_ref,
                        git_bin,
                        repository_root,
                        codex_version,
                    )
            else:
                preexisting_proven = False
            if not preexisting_proven:
                return False
            continue
        if step.get("kind") in ("file", "venv") and step.get("state") in ("started", "staged", "building", "publishing"):
            if not _receipt_proves_published_path(codex_home, step, path_map):
                return False
            continue
        if step.get("state") not in ("owned", "done"):
            return False
        if step.get("kind") == "plugin":
            selector = step.get("selector")
            proof = plugin_map.get(selector)
            if (
                not isinstance(proof, dict)
                or proof.get("sha") != step.get("commit")
                or proof.get("relative_path") != step.get("relative_path")
                or not _identity_matches_safe(proof)
            ):
                return False
            if observed is None:
                if executable is None:
                    return False
                observed = _plugin_command_component(
                    codex_home,
                    executable,
                    lock,
                    guardian_ref,
                    git_bin,
                    _receipt_plugin_paths(receipt),
                    repository_root,
                    codex_version=codex_version,
                )
            name = str(step.get("id", "")).split(":", 1)[-1]
            if observed.get("status") != "present" or not observed.get("installed", {}).get(name):
                return False
            if observed.get("installed_paths", {}).get(name) != proof.get("relative_path"):
                return False
            try:
                plugin_path = _safe_relative_path(codex_home, proof["relative_path"])
                plugin_info = plugin_path.lstat()
            except (BootstrapError, OSError, KeyError):
                return False
            if stat.S_ISLNK(plugin_info.st_mode) or not _identity_matches(plugin_info, proof):
                return False
            if name == "allinluna":
                installed_trees = observed.get("installed_tree_sha256")
                expected_tree = installed_trees.get(name) if isinstance(installed_trees, dict) else None
                tree_proof = _content_tree_proof(plugin_path, codex_home)
                if (
                    not isinstance(tree_proof, dict)
                    or tree_proof.get("tree_sha256") != expected_tree
                    or not _identity_matches(plugin_info, tree_proof)
                ):
                    return False
            continue
        if step.get("kind") not in ("file", "venv"):
            return False
        if not _receipt_proves_published_path(codex_home, step, path_map):
            return False
    return True


def _remove_partial_receipt_pair(codex_home: Path, step: Optional[Dict[str, Any]] = None) -> bool:
    """Remove a receipt pair created by a journaled transaction, fail-closed."""
    try:
        paths = _receipt_paths(codex_home)
        present = False
        proofs: List[Tuple[Path, Dict[str, int], str]] = []
        for path in paths:
            _assert_safe_path(path, anchor=codex_home)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            present = True
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                return False
            limit = MAX_SIDECAR_BYTES if path == paths[1] else MAX_JSON_BYTES
            content = _read_stable_bytes(path, limit, codex_home)
            digest = _sha256_bytes(content)
            if step is not None:
                expected = step.get("new_receipt_sha256" if path == paths[0] else "new_sidecar_sha256")
                if not isinstance(expected, str) or digest != expected:
                    return False
            proofs.append((path, _identity(info), digest))
        if not present:
            return True
        for path, identity, digest in proofs:
            if not _unlink_path_owned(path, identity, digest, codex_home):
                return False
        _fsync_directory(paths[0].parent)
        return True
    except (BootstrapError, OSError):
        return False


def _remove_staged_artifact(
    path: Path,
    ownership: Dict[str, Any],
    anchor: Path,
    quarantine_callback: Optional[Callable[[str, Optional[Dict[str, int]], str, Optional[str]], None]] = None,
    require_empty: bool = False,
) -> bool:
    try:
        _assert_safe_path(path, allow_missing=False, anchor=anchor)
        info = path.lstat()
        expected = ownership.get("sha256")
        if expected is None:
            require_empty = True
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or not _identity_matches(info, ownership):
            return False
        if expected is not None and (not isinstance(expected, str) or _tree_hash(path) != expected):
            return False
        relative = _relative_path_string(path, anchor)
        directory_fd, target_name = _open_relative_parent(anchor, relative)
    except (BootstrapError, OSError, ValueError):
        return False
    quarantine_name = _stable_temp_name(".guardian-remove-", relative)
    quarantine_relative = _relative_path_string(path.parent / quarantine_name, anchor)
    quarantine_name_live = True
    quarantine_identity: Optional[Dict[str, int]] = None
    try:
        if quarantine_callback is not None:
            quarantine_callback(quarantine_relative, None, "planned", expected if isinstance(expected, str) else None)
        _rename_noreplace_at(directory_fd, target_name, quarantine_name)
        quarantined = os.stat(quarantine_name, dir_fd=directory_fd, follow_symlinks=False)
        quarantine_identity = _identity(quarantined)
        if quarantine_callback is not None:
            quarantine_callback(quarantine_relative, quarantine_identity, "quarantined", expected if isinstance(expected, str) else None)
        if stat.S_ISLNK(quarantined.st_mode) or not _identity_matches(quarantined, ownership):
            return False
        if expected is not None and _tree_hash_at(directory_fd, quarantine_name) != expected:
            return False
        if require_empty:
            if stat.S_ISDIR(quarantined.st_mode):
                child_fd = os.open(
                    quarantine_name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    if os.listdir(child_fd):
                        return False
                finally:
                    os.close(child_fd)
            elif quarantined.st_size != 0:
                return False
        if stat.S_ISDIR(quarantined.st_mode):
            if require_empty:
                if not _rmdir_at_owned(directory_fd, quarantine_name, quarantine_identity):
                    raise BootstrapError("staged directory could not be removed")
            else:
                _remove_tree_at(directory_fd, quarantine_name, expected, ownership)
        else:
            if not _unlink_at_owned(directory_fd, quarantine_name, quarantine_identity, expected):
                raise BootstrapError("staged file could not be removed")
        quarantine_name_live = False
        if quarantine_callback is not None:
            quarantine_callback(quarantine_relative, quarantine_identity, "done", expected if isinstance(expected, str) else None)
        return True
    except (BootstrapError, OSError):
        return False
    finally:
        if quarantine_name_live:
            try:
                os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                try:
                    _rename_noreplace_at(
                        directory_fd,
                        quarantine_name,
                        target_name,
                        source_ownership=quarantine_identity,
                        source_digest=expected,
                    )
                except (BootstrapError, OSError):
                    pass
            except OSError:
                pass
        os.close(directory_fd)


def _recover_started_publish(
    codex_home: Path,
    step: Dict[str, Any],
    quarantine_callback: Optional[Callable[[str, Optional[Dict[str, int]], str, Optional[str]], None]] = None,
) -> bool:
    relative = step.get("relative_path")
    staging_relative = step.get("staging_relative_path")
    expected = step.get("sha256")
    if not isinstance(relative, str) or not relative:
        return False
    try:
        target = _safe_relative_path(codex_home, relative)
    except BootstrapError:
        return False
    target_exists = target.exists() or target.is_symlink()
    if not isinstance(staging_relative, str) or not staging_relative or staging_relative == relative:
        return step.get("state") == "started" and not target_exists
    try:
        staging = _safe_relative_path(codex_home, staging_relative)
    except BootstrapError:
        return False
    staging_exists = staging.exists() or staging.is_symlink()
    if step.get("state") == "started" and not target_exists and step.get("device") is None and step.get("inode") is None:
        expected_staging = None
        if step.get("kind") == "file":
            expected_staging = _copy_staging_relative_path(relative)
        elif step.get("kind") == "venv" and relative == "venvs/allinluna":
            expected_staging = "venvs/.guardian-venv-allinluna"
        if staging_relative != expected_staging:
            return False
        if not staging_exists:
            return True
        try:
            info = staging.lstat()
            if stat.S_ISLNK(info.st_mode) or step.get("kind") == "file" and not stat.S_ISREG(info.st_mode) or step.get("kind") == "venv" and not stat.S_ISDIR(info.st_mode):
                return False
            if stat.S_ISREG(info.st_mode):
                if info.st_size != 0:
                    return False
            elif not stat.S_ISDIR(info.st_mode) or any(staging.iterdir()):
                return False
            empty_digest = _tree_hash(staging)
            if not isinstance(empty_digest, str) or not SHA256_RE.fullmatch(empty_digest):
                return False
            return _remove_staged_artifact(
                staging,
                {**_identity(info), "sha256": empty_digest},
                codex_home,
                quarantine_callback=quarantine_callback,
                require_empty=True,
            )
        except (BootstrapError, OSError):
            return False
    if (
        staging_exists
        and expected is None
        and step.get("kind") in ("file", "venv")
        and step.get("state") in ("started", "staged", "building", "publishing")
    ):
        # A non-empty unhashed stage is not attributable to this transaction.
        # Never hand it to recursive removal; only a proven empty stage may
        # be removed without a complete hash proof.
        try:
            info = staging.lstat()
            expected_kind = stat.S_ISREG(info.st_mode) if step.get("kind") == "file" else stat.S_ISDIR(info.st_mode)
            if stat.S_ISLNK(info.st_mode) or not expected_kind or not _identity_matches_safe(step) or not _identity_matches(info, step):
                return False
            if stat.S_ISDIR(info.st_mode):
                if any(staging.iterdir()):
                    return False
            elif info.st_size != 0:
                return False
            empty_digest = _tree_hash(staging)
            if not isinstance(empty_digest, str) or not SHA256_RE.fullmatch(empty_digest):
                return False
            return _remove_staged_artifact(
                staging,
                {**_identity(info), "sha256": empty_digest},
                codex_home,
                quarantine_callback=quarantine_callback,
                require_empty=True,
            )
        except (BootstrapError, OSError):
            return False
    if not target_exists and not staging_exists:
        return step.get("state") == "started"
    if expected is not None and (not isinstance(expected, str) or not SHA256_RE.fullmatch(expected)):
        return False
    if not _identity_matches_safe(step):
        return False
    if target_exists and staging_exists:
        return _remove_staged_artifact(staging, step, codex_home, quarantine_callback=quarantine_callback)
    if target_exists:
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            return False
        if expected is not None and _tree_hash(target) == expected and not _identity_matches(info, step):
            # A concurrent target with the expected bytes is not ours.  Preserve
            # it and close this no-longer-ownable journal step.
            return True
        if expected is None or not _identity_matches(info, step) or _tree_hash(target) != expected:
            return False
        return _remove_owned(
            target, expected, anchor=codex_home, ownership=step, quarantine_callback=quarantine_callback
        ) == "removed"
    if staging_exists:
        return _remove_staged_artifact(staging, step, codex_home, quarantine_callback=quarantine_callback)
    return True


def _receipt_journal_path(codex_home: Path, relative: Optional[str]) -> Optional[Path]:
    if not isinstance(relative, str) or not relative:
        return None
    try:
        path = _safe_relative_path(codex_home, relative)
        info = path.lstat()
    except FileNotFoundError:
        return None
    except (BootstrapError, OSError):
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    return path


def _receipt_backup_bytes(codex_home: Path, relative: Optional[str], expected: Optional[str]) -> Optional[bytes]:
    if expected is None:
        return None
    path = _receipt_journal_path(codex_home, relative)
    if path is None:
        return None
    try:
        limit = MAX_SIDECAR_BYTES if path.name.endswith(".sha256") else MAX_JSON_BYTES
        content = _read_stable_bytes(path, limit, codex_home)
    except (BootstrapError, OSError):
        return None
    return content if _sha256_bytes(content) == expected else None


def _cleanup_receipt_journal_paths(codex_home: Path, step: Dict[str, Any]) -> bool:
    scratch = [
        ("prior_receipt_backup_relative_path", "prior_receipt_backup_device", "prior_receipt_backup_inode", "prior_receipt_sha256"),
        ("prior_sidecar_backup_relative_path", "prior_sidecar_backup_device", "prior_sidecar_backup_inode", "prior_sidecar_sha256"),
        ("new_receipt_temp_relative_path", "new_receipt_temp_device", "new_receipt_temp_inode", "new_receipt_temp_sha256"),
        ("new_sidecar_temp_relative_path", "new_sidecar_temp_device", "new_sidecar_temp_inode", "new_sidecar_temp_sha256"),
    ]
    for relative_key, device_key, inode_key, digest_key in scratch:
        relative = step.get(relative_key)
        if not isinstance(relative, str) or not relative:
            continue
        try:
            path = _safe_relative_path(codex_home, relative)
            info = path.lstat()
        except FileNotFoundError:
            continue
        except (BootstrapError, OSError):
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return False
        limit = MAX_SIDECAR_BYTES if path.name.endswith(".sha256") else MAX_JSON_BYTES
        if info.st_size > limit:
            return False
        if (
            not isinstance(step.get(device_key), int)
            or not isinstance(step.get(inode_key), int)
            or not _identity_matches(info, {"device": step[device_key], "inode": step[inode_key]})
            or not isinstance(step.get(digest_key), str)
            or _sha256_file(path, limit, codex_home) != step[digest_key]
        ):
            return False
        if not _identity_matches(path.lstat(), {"device": step[device_key], "inode": step[inode_key]}):
            return False
        if not _unlink_path_owned(
            path,
            {"device": step[device_key], "inode": step[inode_key]},
            step[digest_key],
            codex_home,
        ):
            return False
    try:
        _fsync_directory(_receipt_paths(codex_home)[0].parent)
    except OSError:
        return False
    return True


def _cleanup_unrecorded_receipt_temps(codex_home: Path, step: Dict[str, Any]) -> bool:
    """Refuse to remove receipt temps whose creation proof was never journaled."""
    expected_paths = {
        "new_receipt_temp_relative_path": RECEIPT_RELATIVE.parent / ".guardian-receipt-new.json",
        "new_sidecar_temp_relative_path": RECEIPT_RELATIVE.parent / ".guardian-receipt-new.sha256",
    }
    for relative_key, expected_relative in expected_paths.items():
        relative = step.get(relative_key)
        if relative is None:
            continue
        if relative != expected_relative.as_posix():
            return False
        try:
            path = _safe_relative_path(codex_home, relative)
            info = path.lstat()
        except FileNotFoundError:
            continue
        except (BootstrapError, OSError):
            return False
        # Exact names and matching bytes are not ownership proof.  Preserve
        # the transaction journal and scratch file until an inode/hash pair
        # recorded after O_EXCL creation is available.
        del info, relative_key
        return False
    return True


def _restore_receipt_pair_from_backups(codex_home: Path, step: Dict[str, Any]) -> bool:
    receipt_path, sidecar_path = _receipt_paths(codex_home)
    receipt_expected = step.get("prior_receipt_sha256")
    sidecar_expected = step.get("prior_sidecar_sha256")
    receipt_bytes = _receipt_backup_bytes(codex_home, step.get("prior_receipt_backup_relative_path"), receipt_expected)
    sidecar_bytes = _receipt_backup_bytes(codex_home, step.get("prior_sidecar_backup_relative_path"), sidecar_expected)
    if receipt_expected is not None and receipt_bytes is None:
        return False
    if sidecar_expected is not None and sidecar_bytes is None:
        return False
    def current_temp_proof(
        path: Path,
        prefix: str,
        limit: int,
    ) -> Optional[Tuple[Dict[str, int], str]]:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        expected_device = step.get(prefix + "_device")
        expected_inode = step.get(prefix + "_inode")
        expected_digest = step.get(prefix + "_sha256")
        if (
            not isinstance(expected_device, int)
            or not isinstance(expected_inode, int)
            or not isinstance(expected_digest, str)
            or not _identity_matches(info, {"device": expected_device, "inode": expected_inode})
        ):
            return None
        try:
            digest = _sha256_file(path, limit, codex_home)
        except (BootstrapError, OSError):
            return None
        return (_identity(info), digest) if digest == expected_digest else None

    try:
        def restore_if_needed(
            path: Path,
            content: Optional[bytes],
            target_proof: Optional[Tuple[Dict[str, int], str]],
            limit: int,
        ) -> None:
            if content is not None:
                try:
                    current = path.lstat()
                except FileNotFoundError:
                    current = None
                if current is not None and stat.S_ISREG(current.st_mode) and _sha256_file(path, limit, codex_home) == _sha256_bytes(content):
                    return
            _restore_file(path, content, target_proof=target_proof)

        restore_if_needed(
            receipt_path,
            receipt_bytes,
            current_temp_proof(receipt_path, "new_receipt_temp", MAX_JSON_BYTES),
            MAX_JSON_BYTES,
        )
        restore_if_needed(
            sidecar_path,
            sidecar_bytes,
            current_temp_proof(sidecar_path, "new_sidecar_temp", MAX_SIDECAR_BYTES),
            MAX_SIDECAR_BYTES,
        )
        return _receipt_pair_matches(_receipt_pair_digests(codex_home), receipt_expected, sidecar_expected)
    except (BootstrapError, OSError):
        return False


def _recover_receipt_write_state(codex_home: Path, step: Dict[str, Any]) -> Optional[str]:
    new_receipt = step.get("new_receipt_sha256")
    new_sidecar = step.get("new_sidecar_sha256")
    prior_receipt = step.get("prior_receipt_sha256")
    prior_sidecar = step.get("prior_sidecar_sha256")
    if not all(value is None or SHA256_RE.fullmatch(str(value)) for value in (new_receipt, new_sidecar, prior_receipt, prior_sidecar)):
        return None
    if new_receipt is None or new_sidecar is None:
        return None
    pair = _receipt_pair_digests(codex_home)
    if _receipt_pair_matches(pair, new_receipt, new_sidecar):
        live, error = _read_existing_receipt(codex_home)
        if not isinstance(live, dict) or error is not None:
            return None
        return "new" if _cleanup_receipt_journal_paths(codex_home, step) else None
    if prior_receipt is None and prior_sidecar is None:
        if not _cleanup_unrecorded_receipt_temps(codex_home, step):
            return None
        pair = _receipt_pair_digests(codex_home)
    allowed_receipt = {value for value in (new_receipt, prior_receipt) if value is not None}
    allowed_sidecar = {value for value in (new_sidecar, prior_sidecar) if value is not None}
    receipt_present, receipt_digest, sidecar_present, sidecar_digest = pair
    known = (
        (not receipt_present or receipt_digest in allowed_receipt)
        and (not sidecar_present or sidecar_digest in allowed_sidecar)
    )
    if not known:
        return None
    # An absent prior pair is not an ownership proof for a partial first
    # write. Remove only a known partial pair, then keep the journal pending.
    if prior_receipt is None or prior_sidecar is None:
        if not _remove_partial_receipt_pair(codex_home, step):
            return None
        return "partial" if _cleanup_receipt_journal_paths(codex_home, step) else None
    if _receipt_pair_matches(pair, prior_receipt, prior_sidecar):
        return "old" if _cleanup_receipt_journal_paths(codex_home, step) else None
    if not _restore_receipt_pair_from_backups(codex_home, step):
        return None
    return "old" if _cleanup_receipt_journal_paths(codex_home, step) else None


def _recover_receipt_remove_state(codex_home: Path, step: Dict[str, Any]) -> bool:
    prior_receipt = step.get("prior_receipt_sha256")
    prior_sidecar = step.get("prior_sidecar_sha256")
    if prior_receipt is None or prior_sidecar is None:
        return False
    pair = _receipt_pair_digests(codex_home)
    if not pair[0] and not pair[2]:
        return _cleanup_receipt_journal_paths(codex_home, step)
    for present, digest, expected in ((pair[0], pair[1], prior_receipt), (pair[2], pair[3], prior_sidecar)):
        if present and digest != expected:
            return False
    if pair[0] and pair[2]:
        if not _remove_receipt_pair(
            codex_home,
            expected_digests=(prior_receipt, prior_sidecar),
        ):
            return False
    elif pair[0] != pair[2]:
        # One member may already have been removed before a crash.  Remove
        # only the remaining inode whose journaled digest still matches.
        receipt_path, sidecar_path = _receipt_paths(codex_home)
        path = receipt_path if pair[0] else sidecar_path
        expected = prior_receipt if pair[0] else prior_sidecar
        try:
            info = path.lstat()
            limit = MAX_JSON_BYTES if pair[0] else MAX_SIDECAR_BYTES
            digest = _sha256_file(path, limit, codex_home)
            if not isinstance(expected, str) or digest != expected or not _unlink_path_owned(path, _identity(info), digest, codex_home):
                return False
        except (BootstrapError, OSError):
            return False
    return _cleanup_receipt_journal_paths(codex_home, step)


def _recover_quarantine_artifact(codex_home: Path, step: Dict[str, Any]) -> bool:
    relative = step.get("quarantine_relative_path")
    target_relative = step.get("relative_path")
    if not isinstance(relative, str) or not relative or not isinstance(target_relative, str) or not target_relative:
        return False
    try:
        quarantine = _safe_relative_path(codex_home, relative)
        target = _safe_relative_path(codex_home, target_relative)
    except BootstrapError:
        return False
    expected = step.get("quarantine_sha256")
    if expected is None:
        expected = step.get("sha256")
    q_exists = quarantine.exists() or quarantine.is_symlink()
    target_exists = target.exists() or target.is_symlink()
    if not q_exists and not target_exists and step.get("quarantine_state") == "quarantined":
        # The journal records that the atomic rename completed.  With both
        # names absent, the only remaining state is a completed deletion whose
        # final journal callback was interrupted.
        step["quarantine_relative_path"] = None
        step["quarantine_state"] = None
        step["quarantine_device"] = None
        step["quarantine_inode"] = None
        step["quarantine_sha256"] = None
        return True
    if q_exists:
        try:
            info = quarantine.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                return False
            q_device = step.get("quarantine_device")
            q_inode = step.get("quarantine_inode")
            if q_device is None or q_inode is None:
                q_device = step.get("device")
                q_inode = step.get("inode")
            if not _identity_matches(info, {"device": q_device, "inode": q_inode}):
                return False
            if expected is not None and _tree_hash(quarantine) != expected:
                return False
            directory_fd, name = _open_relative_parent(codex_home, relative)
            try:
                if stat.S_ISDIR(info.st_mode):
                    _remove_tree_at(directory_fd, name, expected, {"device": q_device, "inode": q_inode})
                else:
                    if not _unlink_at_owned(
                        directory_fd,
                        name,
                        {"device": q_device, "inode": q_inode},
                        expected,
                    ):
                        return False
            finally:
                os.close(directory_fd)
        except (BootstrapError, OSError):
            return False
    elif target_exists or step.get("quarantine_state") == "quarantined":
        # A planned rename that never happened leaves the original target for
        # the normal ownership proof.  If deletion failed after quarantine and
        # the safe finally-block restored the inode, prove that restoration and
        # retry removal instead of leaving a permanently stale quarantine state.
        if step.get("quarantine_state") == "quarantined":
            quarantine_device = step.get("quarantine_device")
            quarantine_inode = step.get("quarantine_inode")
            if quarantine_device is None or quarantine_inode is None:
                quarantine_device = step.get("device")
                quarantine_inode = step.get("inode")
            if not isinstance(expected, str) or not _identity_matches_safe({"device": quarantine_device, "inode": quarantine_inode}):
                return False
            candidates = [target_relative]
            staging_relative = step.get("staging_relative_path")
            if isinstance(staging_relative, str) and staging_relative:
                candidates.append(staging_relative)
            restored: Optional[Path] = None
            for candidate_relative in candidates:
                try:
                    candidate = _safe_relative_path(codex_home, candidate_relative)
                    expected_quarantine = _relative_path_string(
                        candidate.parent / _stable_temp_name(".guardian-remove-", candidate_relative),
                        codex_home,
                    )
                    if expected_quarantine != relative:
                        continue
                    info = candidate.lstat()
                except (BootstrapError, OSError):
                    continue
                if (
                    not stat.S_ISLNK(info.st_mode)
                    and (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                    and _identity_matches(info, {"device": quarantine_device, "inode": quarantine_inode})
                    and _tree_hash(candidate) == expected
                ):
                    restored = candidate
                    break
            if restored is None:
                return False
            if step.get("action") == "remove" and _remove_owned(
                restored,
                expected,
                anchor=codex_home,
                ownership={"device": quarantine_device, "inode": quarantine_inode},
            ) != "removed":
                return False
    # Callers persist the mutated journal before clearing it; this helper only
    # removes a fully proven orphan and leaves the journal as the source of
    # truth for an interrupted caller.
    step["quarantine_relative_path"] = None
    step["quarantine_state"] = None
    step["quarantine_device"] = None
    step["quarantine_inode"] = None
    step["quarantine_sha256"] = None
    return True


def _recover_apply_journal(
    codex_home: Path,
    journal: Dict[str, Any],
    executable: Optional[Path],
    lock: Dict[str, Any],
    guardian_ref: Optional[str],
    git_bin: Optional[Path],
    repository_root: Optional[Path] = None,
    codex_version: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Finish a proven commit or rollback only journaled additions with hashes."""
    existing_receipt, receipt_error = _read_existing_receipt(codex_home)
    receipt_step = next((step for step in journal.get("steps", []) if isinstance(step, dict) and step.get("kind") == "receipt"), None)
    receipt_state = receipt_step.get("state") if isinstance(receipt_step, dict) else None
    receipt_written = receipt_state in ("owned", "done")
    if receipt_state == "started":
        resolution = _recover_receipt_write_state(codex_home, receipt_step)
        if resolution is None:
            return False, "started receipt pair is unknown or invalid"
        if resolution == "partial":
            if any(
                isinstance(step, dict)
                and step.get("kind") != "receipt"
                and step.get("state") == "done"
                for step in journal.get("steps", [])
            ):
                return False, "started receipt pair is incomplete after committed effects"
            if _journal_step(codex_home, journal, "receipt", "failed"):
                return False, "transaction journal update failed"
            existing_receipt, receipt_error = _read_existing_receipt(codex_home)
            receipt_written = False
        if resolution == "new":
            existing_receipt, receipt_error = _read_existing_receipt(codex_home)
            receipt_written = True
        else:
            existing_receipt, receipt_error = _read_existing_receipt(codex_home)
            receipt_written = False
    if (
        not receipt_written
        and receipt_state in ("planned", "intent")
        and isinstance(existing_receipt, dict)
        and receipt_error is None
        and _receipt_proves_apply_journal(
            codex_home,
            journal,
            existing_receipt,
            executable,
            lock,
            guardian_ref,
            git_bin,
            repository_root,
            codex_version,
        )
    ):
        # A process can finish the receipt pair before its first journal
        # state transition.  A full receipt attestation is the only proof
        # that this is a commit rather than a stale transaction.
        receipt_written = True
    for pending_step in journal.get("steps", []):
        if not isinstance(pending_step, dict) or not pending_step.get("quarantine_relative_path"):
            continue
        if not _recover_quarantine_artifact(codex_home, pending_step):
            return False, "journaled quarantine cannot be proven or removed"
    if receipt_state == "started" and receipt_written:
        for step in journal.get("steps", []):
            if (
                isinstance(step, dict)
                and step.get("kind") == "plugin"
                and step.get("preexisting") is True
                and step.get("state") == "started"
            ):
                if not _preexisting_plugin_resolver_matches(
                    codex_home,
                    step,
                    existing_receipt,
                    executable,
                    lock,
                    guardian_ref,
                    git_bin,
                    repository_root,
                    codex_version,
                ):
                    return False, "pre-existing plugin resolver state cannot be verified during recovery"
                if _journal_step(codex_home, journal, str(step.get("id")), "failed"):
                    return False, "transaction journal update failed"
        if not _receipt_proves_apply_journal(
            codex_home,
            journal,
            existing_receipt,
            executable,
            lock,
            guardian_ref,
            git_bin,
            repository_root,
            codex_version,
        ):
            return False, "started receipt state is not fully attested"
        for step in journal.get("steps", []):
            if isinstance(step, dict) and step.get("kind") in ("file", "venv") and step.get("state") in ("started", "staged", "publishing"):
                step["state"] = "owned"
        receipt_step["state"] = "owned"
        journal["phase"] = "COMMITTING"
        journal["generation"] = int(journal.get("generation", 0)) + 1
        if _write_journal(codex_home, journal):
            return False, "transaction commit journal update failed"
        cleared = _remove_journal(codex_home)
        return cleared, "committed" if cleared else "transaction journal could not be cleared"
    if receipt_written:
        if not isinstance(existing_receipt, dict) or receipt_error is not None:
            return False, "written receipt is missing or invalid"
        if not _receipt_proves_apply_journal(
            codex_home,
            journal,
            existing_receipt,
            executable,
            lock,
            guardian_ref,
            git_bin,
            repository_root,
            codex_version,
        ):
            return False, "written receipt state is no longer unchanged"
        journal["phase"] = "COMMITTING"
        journal["generation"] = int(journal.get("generation", 0)) + 1
        if _write_journal(codex_home, journal):
            return False, "transaction commit journal update failed"
        cleared = _remove_journal(codex_home)
        return cleared, "committed" if cleared else "transaction journal could not be cleared"
    actions: List[Dict[str, Any]] = []
    for step in reversed(journal.get("steps", [])):
        if not isinstance(step, dict) or step.get("state") in ("failed",):
            continue
        if step.get("state") in ("planned", "intent"):
            if step.get("kind") in ("file", "venv"):
                try:
                    planned_path = _safe_relative_path(codex_home, str(step.get("relative_path")))
                    planned_stage = _safe_relative_path(codex_home, str(step.get("staging_relative_path"))) if step.get("staging_relative_path") else None
                    if planned_path.exists() or planned_path.is_symlink() or (planned_stage is not None and (planned_stage.exists() or planned_stage.is_symlink())):
                        return False, "planned path ownership is unproven; path may be modified or concurrently created"
                except BootstrapError:
                    return False, "planned path is unsafe"
            continue
        kind = step.get("kind")
        observed: Optional[Dict[str, Any]] = None
        if kind == "plugin" and step.get("state") == "started" and step.get("preexisting") is True:
            if step.get("relative_path"):
                proven = _preexisting_plugin_is_unchanged(
                    codex_home,
                    step,
                    existing_receipt,
                    executable,
                    lock,
                    guardian_ref,
                    git_bin,
                    repository_root,
                    codex_version,
                )
            else:
                proven = _preexisting_plugin_resolver_matches(
                    codex_home,
                    step,
                    existing_receipt,
                    executable,
                    lock,
                    guardian_ref,
                    git_bin,
                    repository_root,
                    codex_version,
                )
            if not proven:
                return False, "pre-existing plugin state cannot be verified during recovery"
            if _journal_step(codex_home, journal, str(step.get("id")), "failed"):
                return False, "transaction journal update failed"
            continue
        if kind == "plugin" and step.get("action") == "add" and step.get("preexisting") is False:
            # Codex 0.146 does not expose a creator/transaction identifier.
            # A started add can therefore never be treated as our owned
            # effect, even when a later resolver query proves that the exact
            # pinned plugin is present.  Preserve it as external state and
            # never call `plugin remove` from apply recovery.
            if step.get("state") in ("started", "owned"):
                if executable is None:
                    return False, "Codex is required to recover plugin state"
                name = str(step.get("id", "")).split(":", 1)[-1]
                known_paths: Dict[str, str] = {}
                if isinstance(step.get("relative_path"), str) and step.get("relative_path"):
                    known_paths[name] = step["relative_path"]
                observed = _plugin_command_component(
                    codex_home,
                    executable,
                    lock,
                    guardian_ref,
                    git_bin,
                    known_paths,
                    repository_root,
                    codex_version=codex_version,
                )
                if observed.get("status") != "present":
                    return False, "plugin state cannot be verified during recovery"
                if _plugin_observation_has_installed(observed, name):
                    relative = _plugin_observation_proof(observed, codex_home, name, str(step.get("commit")))
                    if relative is None:
                        return False, "plugin installation cannot be verified during recovery"
                    step["relative_path"] = relative
                    # `preexisting` is the journal's durable external/unowned
                    # marker after an add has been observed.  It does not
                    # grant removal authority.
                    step["preexisting"] = True
                if _journal_step(codex_home, journal, str(step.get("id")), "failed"):
                    return False, "transaction journal update failed"
                if _plugin_observation_has_installed(observed, name):
                    actions.append({"selector": step.get("selector"), "action": "preserved-unowned-add"})
            continue
        if kind in ("file", "venv") and step.get("state") in ("staged", "building", "publishing"):
            if not _recover_started_publish(codex_home, step, _journal_quarantine_callback(codex_home, journal, str(step.get("id")))):
                return False, "journaled publish ownership cannot be proven during recovery"
            if _journal_step(codex_home, journal, str(step.get("id")), "failed"):
                return False, "transaction journal update failed"
            actions.append({"path": step.get("relative_path"), "action": "recovered-remove"})
            continue
        if kind == "receipt":
            # Receipt recovery above owns this state; it is not a rollback
            # effect and must not fall through to the generic started guard.
            continue
        if step.get("state") == "started":
            if kind in ("file", "venv"):
                if not _recover_started_publish(codex_home, step, _journal_quarantine_callback(codex_home, journal, str(step.get("id")))):
                    return False, "journaled publish ownership cannot be proven during recovery"
                if _journal_step(codex_home, journal, str(step.get("id")), "failed"):
                    return False, "transaction journal update failed"
                actions.append({"path": step.get("relative_path"), "action": "recovered-remove"})
                continue
        if kind == "plugin":
            # Apply journals contain only plugin-add steps.  No such step has
            # a creator proof, so any legacy state is preserved and never
            # removed during recovery.
            continue
        elif kind in ("file", "venv"):
            try:
                path = _safe_relative_path(codex_home, str(step.get("relative_path")))
            except BootstrapError:
                return False, "journaled path is unsafe"
            if path.exists() or path.is_symlink():
                expected_hash = step.get("sha256")
                if not isinstance(expected_hash, str) or not _identity_matches_safe(step):
                    return False, "journaled path has no ownership identity"
                action = _remove_owned(
                    path, expected_hash, anchor=codex_home, ownership=step,
                    quarantine_callback=_journal_quarantine_callback(codex_home, journal, str(step.get("id"))),
                )
                if action != "removed":
                    return False, "journaled path was modified or could not be removed"
                actions.append({"path": step["relative_path"], "action": "recovered-remove"})
    if receipt_written:
        return False, "written receipt could not be proven during rollback"
    if isinstance(existing_receipt, dict) and receipt_error is None:
        pass
    elif receipt_error is not None:
        return False, "transaction receipt is invalid during recovery"
    elif not _remove_partial_receipt_pair(codex_home):
        return False, "transaction receipt could not be removed during recovery"
    if not _remove_journal(codex_home):
        return False, "transaction journal could not be cleared"
    return True, "recovered"


def _run_locked(
    mode: str,
    receipt: Dict[str, Any],
    home: Path,
    codex_bin: str,
    git_bin: Optional[str],
    allinluna_python: Optional[str],
    guardian_ref: Optional[str],
) -> Tuple[Dict[str, Any], int]:
    executable = _resolve_codex(codex_bin)
    git_executable = _resolve_program(git_bin) if git_bin else None
    if _git_launch_spec(git_executable) is None:
        git_executable = None
    try:
        receipt.update(_home_identity(home))
    except (BootstrapError, OSError):
        receipt["status"] = "changes-required"
        receipt["conflicts"] = [{"name": "codex-home", "reason": "CODEX_HOME identity cannot be verified"}]
        return _finalize_receipt(receipt), 1
    try:
        _validate_existing_workflow_guardian_directory(home)
    except (BootstrapError, OSError) as exc:
        receipt["status"] = "changes-required"
        receipt["conflicts"] = [{"name": "workflow-guardian", "reason": str(exc)}]
        return _finalize_receipt(receipt), 1
    guardian_root, guardian_reason = _verified_guardian_root(home, executable, guardian_ref, git_executable)
    if guardian_root is None:
        receipt["components"] = [
            {"name": "codex-plugin-command", "status": "configured-unverified", "reason": guardian_reason},
            _python_component(),
            _headroom_component(),
        ]
        if mode == "uninstall":
            receipt["components"].append({
                "name": "uninstall",
                "status": "conflict",
                "reason": "current Guardian provenance cannot be verified against the receipt",
            })
        receipt["status"] = "changes-required"
        receipt["conflicts"] = [{"name": "guardian-source", "reason": guardian_reason}]
        return _finalize_receipt(receipt), 1
    try:
        p_source_proofs, p_root_proof = _validated_source_binding(guardian_root)
        marketplace_lock = _load_lock(
            guardian_root,
            expected_source_proofs=p_source_proofs,
            expected_root_proof=p_root_proof,
        )
    except BootstrapError as exc:
        receipt["components"] = [{"name": "dependency-lock", "status": "unavailable", "reason": str(exc)}]
        return _finalize_receipt(receipt), 2
    execution_root = _execution_guardian_root(home, marketplace_lock)
    execution_valid = execution_root is not None
    execution_reason = None if execution_valid else "executing bootstrap is not the locked versioned Guardian cache copy"
    e_source_proofs: Optional[Dict[str, Dict[str, Any]]] = None
    e_root_proof: Optional[Dict[str, Any]] = None
    if execution_valid and execution_root is not None:
        prior_e_root_proof = _VALIDATED_ROOT_PROOFS.get(str(_absolute_lexical(execution_root)))
        execution_valid, validation_reason = _validate_git_checkout(
            execution_root,
            home,
            git_executable,
            guardian_ref,
            expected_root_proof=prior_e_root_proof,
        )
        execution_reason = None if execution_valid else validation_reason
        if execution_valid:
            try:
                e_source_proofs, e_root_proof = _validated_source_binding(execution_root)
            except BootstrapError as exc:
                execution_valid = False
                execution_reason = str(exc)
    if mode in ("apply", "uninstall") and not execution_valid:
        reason = execution_reason or "installed Guardian checkout is not verified"
        receipt["components"] = [
            {"name": "codex-plugin-command", "status": "configured-unverified", "reason": reason},
            _python_component(),
            _headroom_component(),
            {"name": "guardian-source", "status": "conflict", "reason": reason},
        ]
        receipt["status"] = "changes-required"
        return _finalize_receipt(receipt), 1
    if mode in ("apply", "uninstall") and execution_root is not None and not _script_bound_to_guardian(execution_root):
        receipt["components"] = [
            {"name": "codex-plugin-command", "status": "configured-unverified", "reason": "executing bootstrap is not the verified Guardian copy"},
            _python_component(),
            _headroom_component(),
            {"name": "guardian-source", "status": "conflict", "reason": "canonical writes require the verified Guardian bootstrap path"},
        ]
        receipt["status"] = "changes-required"
        return _finalize_receipt(receipt), 1
    try:
        # P remains the resolver provenance anchor. All write-driving lock,
        # marketplace, role, and artifact reads use the verified E checkout.
        source_root = execution_root if execution_valid and execution_root is not None else guardian_root
        if source_root == guardian_root:
            lock = marketplace_lock
        else:
            if e_source_proofs is None or e_root_proof is None:
                raise BootstrapError("frozen Guardian source proof is unavailable")
            lock = _load_lock(
                source_root,
                expected_source_proofs=e_source_proofs,
                expected_root_proof=e_root_proof,
            )
    except BootstrapError as exc:
        receipt["components"] = [{"name": "dependency-lock", "status": "unavailable", "reason": str(exc)}]
        return _finalize_receipt(receipt), 2
    operation_bindings: List[Tuple[Path, Dict[str, Dict[str, Any]], Dict[str, Any]]] = [
        (guardian_root, p_source_proofs, p_root_proof),
    ]
    if execution_valid and execution_root is not None and execution_root != guardian_root:
        if e_source_proofs is None or e_root_proof is None:
            receipt["components"] = [{"name": "guardian-source", "status": "conflict", "reason": "frozen Guardian source proof is unavailable"}]
            receipt["status"] = "changes-required"
            return _finalize_receipt(receipt), 1
        operation_bindings.append((execution_root, e_source_proofs, e_root_proof))
    if mode in ("apply", "uninstall") and not _operation_source_proof_gate(operation_bindings):
        receipt["components"] = [{"name": "guardian-source", "status": "conflict", "reason": "verified Guardian source changed before the operation"}]
        receipt["status"] = "changes-required"
        return _finalize_receipt(receipt), 1
    python_component = _python_component()
    codex_component, executable = _codex_component(lock, home, codex_bin, source_root)
    existing, existing_error = _read_existing_receipt(home)
    legacy_owned_plugins = (
        existing.get("owned_plugins", [])
        if isinstance(existing, dict) and isinstance(existing.get("owned_plugins", []), list)
        else []
    )
    if legacy_owned_plugins:
        receipt["notes"].append(
            "legacy plugin ownership was discarded; installed plugins remain unowned and require separate Codex removal."
        )
    plugin_component = _plugin_command_component(
        home,
        executable,
        lock,
        guardian_ref,
        git_executable,
        _receipt_plugin_paths(existing),
        guardian_root,
        validated_source_proofs=p_source_proofs,
        validated_root_proof=p_root_proof,
        codex_version=codex_component,
    )
    # The content-tree proof includes root device/inode and timestamps so it
    # can bind a verification to one live cache inode.  It is an internal
    # transaction proof, not part of the private receipt contract; keep the
    # full value for ownership checks but never persist it accidentally on a
    # no-op/reapply path.
    receipt_plugin_component = copy.deepcopy(plugin_component)
    receipt_plugin_component.pop("installed_tree_proof", None)
    plugin_plans = _plugin_plans(lock, plugin_component)
    headroom_component = _headroom_component()
    allin_component = _allinluna_component(lock, home, python_component, allinluna_python, source_root)
    receipt["components"] = [codex_component, receipt_plugin_component, python_component, headroom_component, allin_component]
    receipt["components"].extend(
        {
            "name": plan["name"],
            "status": plan["status"],
            "selector": plan["selector"],
            "expected_sha": plan["expected_sha"],
            "hooks": plan["hooks"],
        }
        for plan in plugin_plans
    )
    if mode in ("apply", "uninstall") and not _operation_source_proof_gate(operation_bindings):
        receipt["status"] = "changes-required"
        receipt["conflicts"] = [{"name": "guardian-source", "reason": "verified Guardian source changed before the operation"}]
        return _finalize_receipt(receipt), 1
    journal, journal_error = _read_journal(home)
    if journal_error:
        receipt["status"] = "recovery-required"
        receipt["recovery"] = "transaction journal is invalid"
        receipt["components"].append({"name": "transaction", "status": "recovery-required", "reason": "transaction journal is invalid"})
        return _finalize_receipt(receipt), 1
    if journal is not None:
        if mode == "check":
            receipt["status"] = "recovery-required"
            receipt["recovery"] = "transaction journal requires an exclusive apply or uninstall recovery"
            receipt["components"].append({"name": "transaction", "status": "recovery-required", "reason": "pending transaction journal"})
            return _finalize_receipt(receipt), 1
        if mode == "apply" and journal.get("mode") != "apply":
            # Never overwrite or bypass an uninstall transaction.  Its
            # ownership proof and recovery state must be completed first.
            receipt["status"] = "recovery-required"
            receipt["recovery"] = "journal operation does not match requested operation"
            receipt["components"].append({"name": "transaction", "status": "recovery-required", "reason": "journal operation mismatch"})
            return _finalize_receipt(receipt), 1
        if mode == "apply" and journal.get("mode") == "apply":
            recovered, reason = _recover_apply_journal(
                home,
                journal,
                executable,
                lock,
                guardian_ref,
                git_executable,
                guardian_root,
                codex_component,
            )
            if not recovered:
                receipt["status"] = "recovery-required"
                receipt["recovery"] = reason
                receipt["components"].append({"name": "transaction", "status": "recovery-required", "reason": reason})
                return _finalize_receipt(receipt), 1
            # Recovery may have changed state observed before the journal was
            # processed. Rebuild the operation view before a new apply.
            return _run_locked(mode, receipt, home, codex_bin, git_bin, allinluna_python, guardian_ref)
        elif mode == "uninstall" and journal.get("mode") != "uninstall":
            receipt["status"] = "recovery-required"
            receipt["recovery"] = "journal operation does not match requested operation"
            receipt["components"].append({"name": "transaction", "status": "recovery-required", "reason": "journal operation mismatch"})
            return _finalize_receipt(receipt), 1
    if mode == "uninstall":
        if not _operation_source_proof_gate(operation_bindings):
            receipt["status"] = "changes-required"
            receipt["conflicts"] = [{"name": "guardian-source", "reason": "verified Guardian source changed before uninstall"}]
            return _finalize_receipt(receipt), 1
        return _uninstall(home, receipt, guardian_ref, plugin_component)

    plans, conflicts = _preflight_targets(home, source_root)
    if mode == "check" and execution_reason:
        conflicts.append({"name": "guardian-source", "reason": execution_reason})
    receipt["components"].extend(_component_from_plan(plan, mode) for plan in plans)
    if mode in ("apply", "uninstall") and not _operation_source_proof_gate(operation_bindings):
        receipt["status"] = "changes-required"
        receipt["conflicts"] = [{"name": "guardian-source", "reason": "verified Guardian source changed after preflight"}]
        return _finalize_receipt(receipt), 1
    if existing_error:
        conflicts.append({"name": "receipt", "reason": existing_error})
    if existing is not None:
        receipt["existing_receipt"] = "present"
    for relative in _receipt_replaced_paths(home, existing):
        conflicts.append({
            "name": "receipt",
            "relative_path": relative,
            "reason": "receipt-owned content was replaced; ownership will not be re-established automatically",
        })
    receipt["would_write"] = [plan["relative_path"] for plan in plans if plan["status"] == "installed"]
    plugin_conflicts = [plan for plan in plugin_plans if plan["status"] == "conflict"]
    if plugin_conflicts:
        conflicts.extend({"name": plan["name"], "reason": "existing plugin source differs from the pinned immutable commit"} for plan in plugin_conflicts)
    if conflicts:
        receipt["conflicts"] = conflicts
    hard_statuses = {"unavailable", "conflict", "configured-unverified"}
    required_failure = (
        codex_component.get("status") in hard_statuses
        or plugin_component.get("status") in hard_statuses
        or python_component.get("status") in hard_statuses
        or allin_component.get("status") in hard_statuses
        or git_executable is None
        or not guardian_ref
        or not COMMIT_RE.fullmatch(str(guardian_ref))
    )
    if not guardian_ref or not COMMIT_RE.fullmatch(str(guardian_ref)):
        receipt["components"].append({"name": "guardian-ref", "status": "configured-unverified", "reason": "an exact external Guardian commit is required"})
    if (
        mode == "apply"
        and isinstance(existing, dict)
        and not conflicts
        and not required_failure
        and all(plan.get("status") in ("present", "optional") for plan in plugin_plans)
        and allin_component.get("status") == "present"
        and all(plan.get("status") == "present" for plan in plans)
        and _receipt_matches_state(existing, home, plans, plugin_plans, allin_component, plugin_component, guardian_ref)
    ):
        return existing, 0
    if mode == "check":
        receipt_state_ok = isinstance(existing, dict) and _receipt_matches_state(existing, home, plans, plugin_plans, allin_component, plugin_component, guardian_ref)
        if existing is not None and not receipt_state_ok:
            conflicts.append({"name": "receipt", "reason": "receipt-owned state is not unchanged"})
        all_required_present = (
            not required_failure
            and not conflicts
            and all(plan.get("status") in ("present", "optional") for plan in plugin_plans)
            and allin_component.get("status") == "present"
            and all(plan.get("status") == "present" for plan in plans)
            and receipt_state_ok
        )
        if conflicts:
            receipt["conflicts"] = conflicts
        receipt["status"] = "ready" if all_required_present else "changes-required"
        return _finalize_receipt(receipt), 0 if all_required_present else 1
    if mode != "apply":
        receipt["components"].append({"name": "mode", "status": "unavailable", "reason": "unsupported mode"})
        return _finalize_receipt(receipt), 2
    if required_failure or conflicts:
        receipt["status"] = "changes-required"
        return _finalize_receipt(receipt), 1
    if executable is None:
        receipt["components"].append({"name": "codex-cli", "status": "unavailable", "reason": "Codex CLI unavailable"})
        return _finalize_receipt(receipt), 1

    steps: List[Dict[str, Any]] = []
    for plan in plugin_plans:
        if plan["status"] not in ("present", "optional"):
            steps.append({"id": f"plugin:{plan['plugin_name']}", "kind": "plugin", "action": "add", "state": "planned", "relative_path": "", "staging_relative_path": None, "resolver_digest": None, "sha256": None, "commit": plan["expected_sha"], "selector": plan["selector"], "preexisting": bool(plan.get("observed_installed"))})
    for plan in plans:
        if plan["status"] != "present":
            steps.append({"id": f"file:{plan['relative_path']}", "kind": "file", "action": "publish", "state": "planned", "relative_path": plan["relative_path"], "staging_relative_path": _copy_staging_relative_path(plan["relative_path"]), "resolver_digest": None, "sha256": plan["expected_sha256"], "commit": None, "selector": None, "device": None, "inode": None})
    if allin_component.get("status") == "planned":
        steps.append({"id": "venv:allinluna", "kind": "venv", "action": "publish", "state": "planned", "relative_path": "venvs/allinluna", "staging_relative_path": "venvs/.guardian-venv-allinluna", "resolver_digest": None, "sha256": None, "commit": None, "selector": None, "device": None, "inode": None})
    steps.append({
        "id": "receipt", "kind": "receipt", "action": "write", "state": "planned",
        "relative_path": RECEIPT_RELATIVE.as_posix(), "sha256": None, "commit": None, "selector": None,
        "new_receipt_sha256": None, "new_sidecar_sha256": None, "prior_receipt_sha256": None,
        "prior_sidecar_sha256": None, "prior_receipt_backup_relative_path": None,
        "prior_sidecar_backup_relative_path": None, "new_receipt_temp_relative_path": None,
        "new_sidecar_temp_relative_path": None,
    })
    journal = {"schema": JOURNAL_SCHEMA, "generation": 1, "digest": "", "mode": "apply", "phase": "PREPARED", "steps": steps}
    if not _operation_source_proof_gate(operation_bindings):
        receipt["status"] = "changes-required"
        receipt["conflicts"] = [{"name": "guardian-source", "reason": "verified Guardian source changed before journal creation"}]
        return _finalize_receipt(receipt), 1
    journal_error = _write_journal(home, journal)
    if journal_error:
        receipt["components"].append({"name": "transaction", "status": "conflict", "reason": "transaction journal write failed"})
        return _finalize_receipt(receipt), 1
    journal["phase"] = "APPLYING"
    journal["generation"] += 1
    if _write_journal(home, journal):
        receipt["status"] = "recovery-required"
        receipt["recovery"] = "transaction journal phase update failed"
        return _finalize_receipt(receipt), 1

    created: List[Dict[str, Any]] = []
    # Never carry plugin ownership across transactions.  The resolver does
    # not provide a creator proof, so even a structurally valid legacy entry
    # is external/unowned and cannot be removed by Setup.
    owned_plugins: List[Dict[str, Any]] = []
    owned_plugin_steps: set = set()
    uncertain_plugin_step: Optional[str] = None
    uncertain_plugin_selector: Optional[str] = None
    receipt_written = False

    def persist_publish_stage(
        step_id: str,
        staging_relative: str,
        ownership: Dict[str, int],
        state: str,
        stage_hash: Optional[str],
    ) -> None:
        for step in journal.get("steps", []):
            if step.get("id") == step_id:
                step["staging_relative_path"] = staging_relative
                step["device"] = ownership["device"]
                step["inode"] = ownership["inode"]
                if stage_hash is not None:
                    step["sha256"] = stage_hash
                step["state"] = state
                journal["generation"] = int(journal.get("generation", 0)) + 1
                if _write_journal(home, journal):
                    raise BootstrapError("transaction journal update failed")
                return
        raise BootstrapError("transaction journal step is missing")

    def persist_receipt_prepare(digests: Dict[str, Optional[str]]) -> None:
        for step in journal.get("steps", []):
            if step.get("id") == "receipt":
                for key in (
                    "new_receipt_sha256", "new_sidecar_sha256", "prior_receipt_sha256", "prior_sidecar_sha256",
                    "prior_receipt_backup_relative_path", "prior_sidecar_backup_relative_path",
                    "new_receipt_temp_relative_path", "new_sidecar_temp_relative_path",
                    "prior_receipt_backup_device", "prior_receipt_backup_inode",
                    "prior_receipt_backup_sha256", "prior_sidecar_backup_device", "prior_sidecar_backup_inode",
                    "prior_sidecar_backup_sha256", "new_receipt_temp_device", "new_receipt_temp_inode",
                    "new_receipt_temp_sha256", "new_sidecar_temp_device", "new_sidecar_temp_inode",
                    "new_sidecar_temp_sha256",
                ):
                    if key in digests:
                        step[key] = digests[key]
                step["state"] = "started"
                journal["generation"] = int(journal.get("generation", 0)) + 1
                if _write_journal(home, journal):
                    raise BootstrapError("transaction journal update failed")
                return
        raise BootstrapError("transaction journal step is missing")

    def mark_plugin_unowned(step_id: str, relative: str) -> None:
        """Record an exact install as external because Codex gives no creator proof."""
        for step in journal.get("steps", []):
            if step.get("id") == step_id:
                step["preexisting"] = True
                step["relative_path"] = relative
                break
        else:
            raise BootstrapError("transaction journal plugin step is missing")
        if _journal_step(home, journal, step_id, "failed"):
            raise BootstrapError("transaction journal update failed")

    try:
        for plan in plugin_plans:
            if plan["status"] in ("present", "optional"):
                continue
            if not _operation_source_proof_gate(operation_bindings):
                raise BootstrapError("verified Guardian source changed before plugin installation")
            step_id = f"plugin:{plan['plugin_name']}"
            previously_owned = any(
                isinstance(item, dict) and item.get("selector") == plan["selector"]
                for item in owned_plugins
            )
            preexisting = bool(plan.get("observed_installed"))
            if preexisting:
                resolver_digest = _plugin_resolver_row_digest(home, executable, plan["plugin_name"], guardian_root)
                if resolver_digest is None:
                    raise BootstrapError("pre-existing plugin resolver state could not be attested")
                for step in journal.get("steps", []):
                    if step.get("id") == step_id:
                        step["resolver_digest"] = resolver_digest
                        break
                journal["generation"] = int(journal.get("generation", 0)) + 1
                if _write_journal(home, journal):
                    raise BootstrapError("transaction journal update failed")
            if _journal_step(home, journal, step_id, "started"):
                raise BootstrapError("transaction journal update failed")
            uncertain_plugin_step = step_id
            uncertain_plugin_selector = plan["selector"]
            if not _operation_source_proof_gate(operation_bindings):
                raise BootstrapError("verified Guardian source changed before plugin installation")
            add_outcome = _plugin_add(executable, home, plan["selector"], lock, execution_root)
            if not isinstance(add_outcome, PluginAddOutcome):
                add_outcome = PluginAddOutcome.invalid_response()
            if add_outcome.kind not in ("valid", "uncertain") or add_outcome.kind == "valid" and not isinstance(add_outcome.path, str):
                # A non-empty, malformed or mismatched response is evidence
                # of a resolver contract violation, not an uncertain add.
                # Keep the started journal and do not mask it with a query.
                raise BootstrapError("plugin add response is invalid")
            installed_path = add_outcome.path if add_outcome.kind == "valid" else None
            if add_outcome.kind == "uncertain":
                observed = _plugin_command_component(
                    home,
                    executable,
                    lock,
                    guardian_ref,
                    git_executable,
                    repository_root=guardian_root,
                    include_tree_proof=True,
                    validated_source_proofs=p_source_proofs,
                    validated_root_proof=p_root_proof,
                    codex_version=codex_component,
                )
                relative = _plugin_observation_proof(observed, home, plan["plugin_name"], plan["expected_sha"])
                if relative is None:
                    if _plugin_observation_has_installed(observed, plan["plugin_name"]):
                        # The plugin is present but Codex did not expose a
                        # stable installed path. Preserve the started journal
                        # so recovery can re-query; never guess ownership.
                        raise BootstrapError("plugin install path is ambiguous")
                    if _journal_step(home, journal, step_id, "failed"):
                        raise BootstrapError("transaction journal update failed")
                    uncertain_plugin_step = None
                    uncertain_plugin_selector = None
                    raise BootstrapError("plugin installation command failed")
                if preexisting:
                    raise BootstrapError("pre-existing plugin install response was ambiguous")
                expected_relative = relative
                verified = observed
            elif installed_path is not None:
                reported_path = Path(installed_path)
                expected_relative = _relative_path_string(
                    _absolute_lexical(reported_path if reported_path.is_absolute() else home / reported_path),
                    home,
                )
                verified = None
            # Persist the resolver-reported path before any later proof step.
            # This is an observation only: `preexisting=false` remains the
            # transaction fact, and recovery must still treat the install as
            # external because Codex supplies no creator proof.
            for step in journal.get("steps", []):
                if step.get("id") == step_id:
                    step["relative_path"] = expected_relative
                    journal["generation"] = int(journal.get("generation", 0)) + 1
                    if _write_journal(home, journal):
                        raise BootstrapError("transaction journal update failed")
                    break
            if verified is None:
                verified = _plugin_command_component(
                    home,
                    executable,
                    lock,
                    guardian_ref,
                    git_executable,
                    {plan["plugin_name"]: expected_relative},
                    guardian_root,
                    True,
                    p_source_proofs,
                    p_root_proof,
                    codex_component,
                )
            observed_relative = _plugin_observation_proof(verified, home, plan["plugin_name"], plan["expected_sha"])
            if observed_relative != expected_relative or (plan["plugin_name"] == "allinluna" and not SHA256_RE.fullmatch(str(verified.get("installed_tree_sha256", {}).get(plan["plugin_name"], "")))):
                raise BootstrapError("plugin installation could not be verified")
            if _plugin_ownership_proof(verified, home, plan["plugin_name"], plan["expected_sha"]) is None:
                raise BootstrapError("plugin tree ownership proof changed before commit")
            observed_sha = verified["installed_sha"][plan["plugin_name"]]
            if not preexisting:
                mark_plugin_unowned(step_id, expected_relative)
                uncertain_plugin_step = None
                uncertain_plugin_selector = None
                verified["reason"] = "installed but ownership is intentionally unclaimed; Codex provides no creator proof"
                receipt["notes"].append(
                    f"{plan['plugin_name']} is installed but unowned; remove it separately with Codex if required."
                )
            else:
                for step in journal.get("steps", []):
                    if step.get("id") == step_id:
                        step["relative_path"] = expected_relative
                        break
                if _journal_step(home, journal, step_id, "failed"):
                    raise BootstrapError("transaction journal update failed")
            plugin_component = verified
            for component in receipt["components"]:
                if component.get("name") == "codex-plugin-command":
                    component_value = dict(plugin_component)
                    component_value.pop("installed_tree_proof", None)
                    component.clear()
                    component.update(component_value)
                    plugin_component = component_value
                    break
            for component in receipt["components"]:
                if component.get("name") == plan["name"]:
                    component["status"] = "present"
            if previously_owned and not preexisting:
                # A receipt-owned plugin disappeared and was reinstalled by a
                # later apply.  The exact reinstall is preserved as external
                # state so uninstall cannot remove it under stale ownership.
                owned_plugins = [
                    item for item in owned_plugins
                    if not (isinstance(item, dict) and item.get("selector") == plan["selector"])
                ]
        for plan in plans:
            if plan["status"] == "present":
                continue
            if not _operation_source_proof_gate(operation_bindings):
                raise BootstrapError("verified Guardian source changed before managed publish")
            step_id = f"file:{plan['relative_path']}"
            if _journal_step(home, journal, step_id, "started"):
                raise BootstrapError("transaction journal update failed")
            copy_plan = dict(plan)
            copy_plan["codex_home"] = home
            copy_plan["staging_relative_path"] = _copy_staging_relative_path(plan["relative_path"])
            ownership = _copy_one(
                copy_plan,
                lambda staging_relative, identity, state, stage_hash: persist_publish_stage(
                    step_id, staging_relative, identity, state, stage_hash
                ),
            )
            created.append({"path": _absolute_lexical(Path(plan["target"])), "relative_path": plan["relative_path"], "sha256": plan["expected_sha256"], "kind": plan["kind"], **ownership})
            if _tree_hash(_absolute_lexical(Path(plan["target"]))) != plan["expected_sha256"]:
                raise BootstrapError("installed asset hash mismatch")
            for step in journal.get("steps", []):
                if step.get("id") == step_id:
                    step["device"], step["inode"] = ownership["device"], ownership["inode"]
                    break
            if _journal_step(home, journal, step_id, "owned"):
                raise BootstrapError("transaction journal update failed")
        if allin_component.get("status") == "planned":
            if not _operation_source_proof_gate(operation_bindings):
                raise BootstrapError("verified Guardian source changed before All in Luna setup")
            if _journal_step(home, journal, "venv:allinluna", "started"):
                raise BootstrapError("transaction journal update failed")
            if not _operation_source_proof_gate(operation_bindings):
                raise BootstrapError("verified Guardian source changed before All in Luna setup")
            allin_component, allin_root, allin_created = _allinluna_install(
                lock,
                home,
                allinluna_python,
                lambda staging_relative, identity, state, stage_hash: persist_publish_stage(
                    "venv:allinluna", staging_relative, identity, state, stage_hash
                ),
                source_root,
            )
            receipt["components"][4] = allin_component
            # Preserve unverified staging facts even when artifact setup
            # returns unavailable; the outer rollback owns the recovery state.
            created.extend(allin_created)
            if allin_component.get("status") != "installed" or allin_root is None:
                raise BootstrapError("All in Luna exact artifact setup failed")
            venv_hash = next((item.get("sha256") for item in allin_created if item.get("relative_path") == "venvs/allinluna"), None)
            if not isinstance(venv_hash, str) or not SHA256_RE.fullmatch(venv_hash):
                raise BootstrapError("managed venv ownership hash unavailable")
            for step in journal.get("steps", []):
                if step.get("id") == "venv:allinluna":
                    step["sha256"] = venv_hash
                    venv_identity = next((item for item in allin_created if item.get("relative_path") == "venvs/allinluna"), {})
                    step["device"] = venv_identity.get("device")
                    step["inode"] = venv_identity.get("inode")
                    journal["generation"] = int(journal.get("generation", 0)) + 1
                    if _write_journal(home, journal):
                        raise BootstrapError("transaction journal update failed")
                    break
            if _journal_step(home, journal, "venv:allinluna", "owned"):
                raise BootstrapError("transaction journal update failed")
        for item in receipt["components"]:
            if item.get("status") == "skipped":
                item["status"] = "present"
        managed_targets, receipt["owned_paths"] = _build_managed_targets(home, existing, created)
        receipt["managed_targets"] = managed_targets
        receipt["owned_plugins"] = owned_plugins
        receipt["guardian_ref"] = guardian_ref
        receipt["guardian_provenance"] = plugin_component.get("guardian_provenance", "")
        receipt["mode"] = "apply"
        receipt["status"] = "ready"
        receipt["rollback"] = {"performed": False, "actions": []}
        if not _operation_source_proof_gate(operation_bindings):
            raise BootstrapError("verified Guardian source changed before receipt write")
        receipt_created, error = _write_receipt(home, receipt, before_replace=persist_receipt_prepare)
        if error:
            raise BootstrapError(error)
        stored, stored_error = _read_existing_receipt(home)
        if stored_error or not isinstance(stored, dict):
            raise BootstrapError("receipt write could not be verified")
        receipt = stored
        receipt_written = True
        if _journal_step(home, journal, "receipt", "owned"):
            raise BootstrapError("transaction journal update failed")
        journal["phase"] = "COMMITTING"
        journal["generation"] += 1
        if _write_journal(home, journal):
            raise BootstrapError("transaction commit journal update failed")
        if not _remove_journal(home):
            receipt["status"] = "recovery-required"
            receipt["recovery"] = "transaction journal could not be cleared"
            return _finalize_receipt(receipt), 1
        return _finalize_receipt(receipt), 0
    except (BootstrapError, OSError):
        if receipt_written and isinstance(existing, dict) and existing_error is None:
            # The new receipt is already attested and includes the prior
            # ownership record. Keep it with a recoverable journal; deleting
            # it here would orphan paths that the prior receipt used to own.
            receipt["rollback"] = {"performed": False, "actions": []}
            receipt["failure"] = "apply commit journal failed after receipt attestation"
            receipt["status"] = "recovery-required"
            receipt["recovery"] = "rerun --apply to finish the committed receipt transaction"
            if journal.get("phase") == "COMMITTING":
                journal["phase"] = "APPLYING"
            journal["generation"] = int(journal.get("generation", 0)) + 1
            _write_journal(home, journal)
            return _finalize_receipt(receipt), 1
        if journal.get("phase") == "COMMITTING":
            journal["phase"] = "APPLYING"
        plugin_actions: List[Dict[str, Any]] = []
        rollback_failed = False
        if uncertain_plugin_step is not None:
            plugin_actions.append({"selector": uncertain_plugin_selector or "", "action": "preserved-uncertain-add"})
            rollback_failed = True
        actions = _rollback(created, home, journal)
        rollback_failed = rollback_failed or any(item.get("action") not in ("removed",) for item in actions)
        if receipt_written and not _remove_receipt_pair(home):
            rollback_failed = True
        receipt["rollback"] = {"performed": True, "actions": actions + plugin_actions}
        receipt["failure"] = "apply failed; only proven owned additions were rolled back"
        if rollback_failed:
            receipt["status"] = "recovery-required"
            receipt["recovery"] = "rollback was incomplete; rerun --apply for exclusive recovery"
            preserved_paths = {
                item.get("path")
                for item in actions
                if item.get("action") not in ("removed",) and isinstance(item.get("path"), str)
            }
            for step in journal.get("steps", []):
                if step.get("id") == uncertain_plugin_step or step.get("id") in owned_plugin_steps:
                    continue
                if (
                    step.get("kind") in ("file", "venv")
                    and (
                        step.get("relative_path") in preserved_paths
                        or step.get("staging_relative_path") in preserved_paths
                    )
                ):
                    continue
                if step.get("state") in ("planned", "started", "owned", "intent"):
                    step["state"] = "failed"
            _write_journal(home, journal)
        else:
            receipt["status"] = "changes-required"
            if not _remove_journal(home):
                receipt["status"] = "recovery-required"
                receipt["recovery"] = "transaction journal could not be cleared after rollback"
        return _finalize_receipt(receipt), 1


def run(
    mode: str = "check",
    codex_home: Optional[Path] = None,
    codex_bin: Optional[str] = None,
    allinluna_python: Optional[str] = None,
    guardian_ref: Optional[str] = None,
    git_bin: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    receipt = _base_receipt(mode)
    actual_platform = platform.system()
    if actual_platform != "Darwin":
        receipt["platform"] = actual_platform
        receipt["components"] = [{"name": "platform", "status": "unavailable", "reason": "macOS is required"}]
        return _finalize_receipt(receipt), 2
    account_home = _account_home()
    if account_home is None:
        receipt["components"] = [{"name": "platform", "status": "unavailable", "reason": "macOS account home could not be verified"}]
        return _finalize_receipt(receipt), 2
    if codex_home is None or codex_bin is None or git_bin is None:
        receipt["components"] = [{"name": "arguments", "status": "unavailable", "reason": "--codex-home, --codex-bin, and --git-bin must be explicit"}]
        return _finalize_receipt(receipt), 2
    raw_home = os.fspath(codex_home)
    if (
        not isinstance(raw_home, str)
        or not os.path.isabs(raw_home)
        or os.path.normpath(raw_home) != raw_home
        or any(part in ("", ".", "..") for part in Path(raw_home).parts if part not in (os.path.sep,))
    ):
        receipt["components"] = [{"name": "codex-home", "status": "unavailable", "reason": "--codex-home must be an absolute canonical lexical path"}]
        return _finalize_receipt(receipt), 2
    home = _absolute_lexical(Path(raw_home))
    try:
        ambient_home = _absolute_lexical(Path.home())
    except (OSError, RuntimeError):
        ambient_home = None
    broad_homes = {
        _absolute_lexical(Path("/")), _absolute_lexical(Path(tempfile.gettempdir())),
        _absolute_lexical(Path("/tmp")), _absolute_lexical(Path("/private/tmp")),
    }
    # HOME is an input that may be spoofed.  Keep its usual broad denylist for
    # compatibility, but derive the same roots and standard children from the
    # passwd database home so a spoof cannot make the real account tree
    # eligible as CODEX_HOME.
    broad_children = ("Documents", "Desktop", "Downloads", "Library", "Public")
    for home_root in (ambient_home, account_home):
        if home_root is None:
            continue
        broad_homes.update({home_root, home_root.parent, home_root.parent.parent})
        broad_homes.update(home_root / child for child in broad_children)
    if home in broad_homes:
        receipt["components"] = [{"name": "codex-home", "status": "unavailable", "reason": "explicit home is too broad"}]
        return _finalize_receipt(receipt), 2
    try:
        _validate_codex_home(home)
        with _home_lock(home, mode != "check"):
            return _run_locked(mode, receipt, home, codex_bin, git_bin, allinluna_python, guardian_ref)
    except (BootstrapError, OSError, TypeError, ValueError, KeyError) as exc:
        receipt["components"] = [{"name": "codex-home", "status": "unavailable", "reason": str(exc)}]
        return _finalize_receipt(receipt), 2


def _remove_receipt_pair(
    codex_home: Path,
    before_delete: Optional[Callable[[Dict[str, Optional[str]]], None]] = None,
    expected_digests: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> bool:
    receipt_path, hash_path = _receipt_paths(codex_home)
    paths = (receipt_path, hash_path)
    proofs: List[Tuple[Path, Dict[str, int], str, bytes]] = []
    try:
        for index, path in enumerate(paths):
            _assert_safe_path(path, allow_missing=False, anchor=codex_home)
            info = path.lstat()
            if not _private_state_file(path, info):
                return False
            limit = MAX_SIDECAR_BYTES if index else MAX_JSON_BYTES
            content = _read_stable_bytes(path, limit, codex_home)
            digest = _sha256_bytes(content)
            if expected_digests is not None and digest != expected_digests[index]:
                return False
            proofs.append((path, _identity(info), digest, content))
        if before_delete is not None:
            before_delete(
                {
                    "prior_receipt_sha256": proofs[0][2],
                    "prior_sidecar_sha256": proofs[1][2],
                }
            )
        removed: List[Tuple[Path, bytes]] = []
        for path, identity, digest, content in proofs:
            if not _unlink_path_owned(path, identity, digest, codex_home):
                for restored_path, restored_content in removed:
                    try:
                        _restore_file(restored_path, restored_content)
                    except (BootstrapError, OSError):
                        pass
                return False
            removed.append((path, content))
        _fsync_directory(receipt_path.parent)
        return True
    except (BootstrapError, OSError):
        return False


def _preflight_owned_paths(codex_home: Path, owned: List[Dict[str, Any]]) -> Optional[str]:
    """Prove every receipt-owned path is unchanged before external removals."""
    for item in owned:
        relative = item.get("relative_path")
        expected = item.get("sha256")
        kind = item.get("kind")
        try:
            path = _safe_relative_path(codex_home, relative)
        except (BootstrapError, TypeError):
            return "owned path is unsafe"
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return "owned path cannot be inspected"
        if stat.S_ISLNK(info.st_mode) or not _identity_matches(info, item) or kind == "file" and not stat.S_ISREG(info.st_mode) or kind == "directory" and not stat.S_ISDIR(info.st_mode):
            return "owned path was modified or could not be removed"
        if _tree_hash(path) != expected:
            return "owned path was modified or could not be removed"
    return None


def _recover_uninstall_without_receipt(
    codex_home: Path,
    journal: Dict[str, Any],
    plugin_component: Optional[Dict[str, Any]],
) -> Tuple[bool, str]:
    pending_plugin_steps = [
        step
        for step in journal.get("steps", [])
        if isinstance(step, dict) and step.get("kind") == "plugin" and step.get("state") != "done"
    ]
    installed: Dict[str, Any] = {}
    if pending_plugin_steps:
        if not isinstance(plugin_component, dict) or plugin_component.get("status") != "present":
            return False, "uninstall receipt is missing and plugin absence cannot be verified"
        installed_value = plugin_component.get("installed", {})
        if not isinstance(installed_value, dict):
            return False, "uninstall receipt is missing and plugin state is invalid"
        installed = installed_value
    for step in journal.get("steps", []):
        if not isinstance(step, dict) or step.get("state") == "done":
            continue
        kind = step.get("kind")
        if step.get("quarantine_relative_path") and not _recover_quarantine_artifact(codex_home, step):
            return False, "uninstall quarantine cannot be proven or removed"
        if kind == "receipt":
            if step.get("action") != "remove" or not _recover_receipt_remove_state(codex_home, step):
                return False, "uninstall receipt pair cannot be proven or removed"
            if _journal_step(codex_home, journal, str(step.get("id")), "done"):
                return False, "transaction journal update failed"
            continue
        if kind == "plugin":
            name = str(step.get("id", "")).split(":", 1)[-1]
            if installed.get(name):
                return False, "uninstall receipt is missing while an owned plugin remains"
        elif kind in ("file", "venv"):
            try:
                path = _safe_relative_path(codex_home, str(step.get("relative_path")))
            except BootstrapError:
                return False, "uninstall receipt is missing and an owned path is unsafe"
            if path.exists() or path.is_symlink():
                return False, "uninstall receipt is missing while an owned path remains"
        else:
            return False, "uninstall journal contains an unsupported step"
        if _journal_step(codex_home, journal, str(step.get("id")), "done"):
            return False, "transaction journal update failed"
    if any(
        isinstance(step, dict) and step.get("state") != "done"
        for step in journal.get("steps", [])
    ):
        return False, "uninstall journal remains incomplete"
    return (_remove_journal(codex_home), "recovered" if not _journal_path(codex_home).exists() else "transaction journal could not be cleared")


def _uninstall(
    codex_home: Path,
    receipt: Dict[str, Any],
    guardian_ref: Optional[str] = None,
    plugin_component: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], int]:
    pending_journal, pending_error = _read_journal(codex_home)
    if pending_error:
        if isinstance(pending_journal, dict) and pending_journal.get("mode") == "uninstall":
            recovered, reason = _recover_uninstall_without_receipt(codex_home, pending_journal, plugin_component)
            receipt["components"].append({"name": "uninstall", "status": "present" if recovered else "recovery-required", "reason": reason})
            return _finalize_receipt(receipt), 0 if recovered else 1
        receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": "transaction journal is invalid"})
        return _finalize_receipt(receipt), 1
    existing, error = _read_existing_receipt(codex_home)
    if error:
        if isinstance(pending_journal, dict) and pending_journal.get("mode") == "uninstall":
            recovered, reason = _recover_uninstall_without_receipt(codex_home, pending_journal, plugin_component)
            receipt["components"].append({"name": "uninstall", "status": "present" if recovered else "recovery-required", "reason": reason})
            return _finalize_receipt(receipt), 0 if recovered else 1
        receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "bootstrap receipt is invalid"})
        return _finalize_receipt(receipt), 1
    if existing is None:
        if isinstance(pending_journal, dict) and pending_journal.get("mode") == "uninstall":
            recovered, reason = _recover_uninstall_without_receipt(codex_home, pending_journal, plugin_component)
            if recovered:
                receipt["components"].append({"name": "uninstall", "status": "present", "reason": "uninstall transaction completed; journal recovered"})
                return _finalize_receipt(receipt), 0
            receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": reason})
            return _finalize_receipt(receipt), 1
        receipt["components"].append({"name": "uninstall", "status": "skipped", "reason": "no valid bootstrap receipt"})
        return _finalize_receipt(receipt), 0
    if isinstance(pending_journal, dict) and pending_journal.get("mode") == "uninstall":
        for step in pending_journal.get("steps", []):
            if isinstance(step, dict) and step.get("quarantine_relative_path"):
                if not _recover_quarantine_artifact(codex_home, step):
                    receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": "journaled quarantine cannot be proven or removed"})
                    return _finalize_receipt(receipt), 1
        receipt_step = next((step for step in pending_journal.get("steps", []) if isinstance(step, dict) and step.get("kind") == "receipt"), None)
        if isinstance(receipt_step, dict) and receipt_step.get("state") == "started":
            if not _recover_receipt_remove_state(codex_home, receipt_step):
                receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": "journaled receipt pair cannot be proven or removed"})
                return _finalize_receipt(receipt), 1
            if any(isinstance(step, dict) and step.get("kind") != "receipt" and step.get("state") != "done" for step in pending_journal.get("steps", [])):
                receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": "receipt was removed before all owned items were closed"})
                return _finalize_receipt(receipt), 1
            if _journal_step(codex_home, pending_journal, "receipt", "done") or not _remove_journal(codex_home):
                receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": "transaction journal could not be cleared"})
                return _finalize_receipt(receipt), 1
            receipt["components"].append({"name": "uninstall", "status": "present", "reason": "uninstall transaction completed; receipt pair recovered"})
            return _finalize_receipt(receipt), 0
    stored_guardian_ref = existing.get("guardian_ref")
    if (
        not isinstance(stored_guardian_ref, str)
        or not COMMIT_RE.fullmatch(stored_guardian_ref)
        or not isinstance(guardian_ref, str)
        or not COMMIT_RE.fullmatch(guardian_ref)
        or guardian_ref != stored_guardian_ref
    ):
        receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "receipt Guardian immutable ref is missing or current ref is not an exact match"})
        return _finalize_receipt(receipt), 1
    provenance = existing.get("guardian_provenance")
    if not isinstance(provenance, str) or not provenance.startswith("canonical git"):
        receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "receipt Guardian provenance is not verified"})
        return _finalize_receipt(receipt), 1
    if (
        not isinstance(plugin_component, dict)
        or plugin_component.get("status") != "present"
        or plugin_component.get("guardian_ref") != guardian_ref
        or plugin_component.get("guardian_provenance") != provenance
    ):
        receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "current Guardian provenance cannot be verified against the receipt"})
        return _finalize_receipt(receipt), 1
    owned = existing.get("owned_paths")
    owned_plugins = existing.get("owned_plugins", [])
    if not isinstance(owned, list) or not isinstance(owned_plugins, list):
        receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "receipt ownership is invalid"})
        return _finalize_receipt(receipt), 1
    if owned_plugins:
        # A legacy receipt cannot prove that this process created the plugin;
        # never turn its historical claim into a destructive Codex remove.
        receipt["components"].append({
            "name": "uninstall",
            "status": "conflict",
            "reason": "legacy plugin ownership is not trusted; remove the plugin separately with Codex",
        })
        return _finalize_receipt(receipt), 1

    path_conflict = _preflight_owned_paths(codex_home, owned)
    if path_conflict:
        receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": path_conflict})
        return _finalize_receipt(receipt), 1

    journal = pending_journal
    if journal is None:
        steps = []
        for item in owned:
            kind = "venv" if item.get("relative_path") == MANAGED_VENV_RELATIVE else "file"
            step_id = "venv:allinluna" if kind == "venv" else f"file:{item['relative_path']}"
            steps.append({"id": step_id, "kind": kind, "action": "remove", "state": "intent", "relative_path": item["relative_path"], "sha256": item["sha256"], "commit": None, "selector": None, "device": item.get("device"), "inode": item.get("inode")})
        steps.append({
            "id": "receipt", "kind": "receipt", "action": "remove", "state": "intent",
            "relative_path": RECEIPT_RELATIVE.as_posix(), "sha256": None, "commit": None, "selector": None,
            "new_receipt_sha256": None, "new_sidecar_sha256": None, "prior_receipt_sha256": None,
            "prior_sidecar_sha256": None, "prior_receipt_backup_relative_path": None,
            "prior_sidecar_backup_relative_path": None, "new_receipt_temp_relative_path": None,
            "new_sidecar_temp_relative_path": None,
        })
        journal = {"schema": JOURNAL_SCHEMA, "generation": 1, "digest": "", "mode": "uninstall", "phase": "UNINSTALLING", "steps": steps}
        journal_error = _write_journal(codex_home, journal)
        if journal_error:
            receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "transaction journal write failed"})
            return _finalize_receipt(receipt), 1

    def persist_receipt_remove_prepare(digests: Dict[str, Optional[str]]) -> None:
        for step in journal.get("steps", []):
            if step.get("id") != "receipt":
                continue
            step["state"] = "started"
            step["prior_receipt_sha256"] = digests.get("prior_receipt_sha256")
            step["prior_sidecar_sha256"] = digests.get("prior_sidecar_sha256")
            journal["generation"] = int(journal.get("generation", 0)) + 1
            if _write_journal(codex_home, journal):
                raise BootstrapError("transaction journal update failed")
            return
        raise BootstrapError("transaction journal step is missing")

    actions: List[Dict[str, Any]] = []
    remaining_paths = [item for item in owned if isinstance(item, dict)]
    for item in reversed([entry for entry in owned if isinstance(entry, dict)]):
        relative = item["relative_path"]
        step_id = "venv:allinluna" if relative == MANAGED_VENV_RELATIVE else f"file:{relative}"
        try:
            path = _safe_relative_path(codex_home, relative)
            exists = path.exists() or path.is_symlink()
        except BootstrapError:
            exists = True
            path = codex_home
        if not exists:
            remaining_paths = [entry for entry in remaining_paths if entry.get("relative_path") != relative]
            actions.append({"path": relative, "action": "already-absent"})
            if _journal_step(codex_home, journal, step_id, "done"):
                receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "transaction journal update failed"})
                return _finalize_receipt(receipt), 1
            continue
        if _journal_step(codex_home, journal, step_id, "intent"):
            receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "transaction journal update failed"})
            return _finalize_receipt(receipt), 1
        action = _remove_owned(
            path, item["sha256"], anchor=codex_home, ownership=item,
            quarantine_callback=_journal_quarantine_callback(codex_home, journal, step_id),
        )
        actions.append({"path": relative, "action": action})
        if action != "removed":
            receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "owned path was modified or could not be removed", "actions": actions})
            return _finalize_receipt(receipt), 1
        remaining_paths = [entry for entry in remaining_paths if entry.get("relative_path") != relative]
        if _journal_step(codex_home, journal, step_id, "done"):
            receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "transaction journal update failed"})
            return _finalize_receipt(receipt), 1
    if remaining_paths or not _remove_receipt_pair(codex_home, before_delete=persist_receipt_remove_prepare):
        receipt["components"].append({"name": "uninstall", "status": "conflict", "reason": "receipt-owned state remains", "actions": actions})
        return _finalize_receipt(receipt), 1
    if _journal_step(codex_home, journal, "receipt", "done"):
        receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": "transaction journal update failed", "actions": actions})
        return _finalize_receipt(receipt), 1
    if not _remove_journal(codex_home):
        receipt["components"].append({"name": "uninstall", "status": "recovery-required", "reason": "transaction journal removal failed", "actions": actions})
        return _finalize_receipt(receipt), 1
    receipt["components"].append({"name": "uninstall", "status": "present", "actions": actions})
    receipt["rollback"] = {"performed": False, "actions": actions}
    return _finalize_receipt(receipt), 0


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check or apply macOS Codex Workflow Guardian dependencies.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only preflight (default)")
    action.add_argument("--apply", action="store_true", help="apply an exact, receipt-backed setup")
    action.add_argument("--uninstall", action="store_true", help="remove only unmodified content owned by a receipt")
    parser.add_argument("--codex-home", type=Path, required=True, help="explicit target CODEX_HOME for this operation")
    parser.add_argument("--codex-bin", required=True, help="absolute Codex executable path (PATH names are refused)")
    parser.add_argument("--git-bin", required=True, help="absolute Git executable path (PATH names are refused)")
    parser.add_argument("--allinluna-python", help="explicit Python >=3.11 for the managed All in Luna venv")
    parser.add_argument("--guardian-ref", help="external immutable 40-hex Guardian commit declaration required for every operation")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    mode = "apply" if args.apply else "uninstall" if args.uninstall else "check"
    receipt, code = run(mode, args.codex_home, args.codex_bin, args.allinluna_python, guardian_ref=args.guardian_ref, git_bin=args.git_bin)
    print(json.dumps(_public_receipt(receipt), allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
