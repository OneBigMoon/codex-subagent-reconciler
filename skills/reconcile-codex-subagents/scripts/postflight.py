#!/usr/bin/env python3
"""Check one explicit, sanitized before/after snapshot pair."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
import platform
import stat
import types
from typing import Any


MAX_DOCTOR_SOURCE_BYTES = 256 * 1024
SUPPORTED_PLATFORM = "Darwin"


class _DoctorLoadError(ImportError):
    """The sibling doctor source could not be safely bound and loaded."""


def _same_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _load_sibling_source(directory_fd: int, name: str) -> tuple[bytes, str]:
    """Read one bounded regular sibling through a directory-anchored FD."""
    try:
        named_before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(named_before.st_mode) or named_before.st_size > MAX_DOCTOR_SOURCE_BYTES:
            raise _DoctorLoadError("doctor module unavailable")
        source_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            descriptor_before = os.fstat(source_fd)
            if not stat.S_ISREG(descriptor_before.st_mode):
                raise _DoctorLoadError("doctor module unavailable")
            if not _same_stat(named_before, descriptor_before):
                raise _DoctorLoadError("doctor module unavailable")
            if descriptor_before.st_size > MAX_DOCTOR_SOURCE_BYTES:
                raise _DoctorLoadError("doctor module unavailable")
            source = os.read(source_fd, MAX_DOCTOR_SOURCE_BYTES + 1)
            descriptor_after = os.fstat(source_fd)
            if not _same_stat(descriptor_before, descriptor_after):
                raise _DoctorLoadError("doctor module unavailable")
            if len(source) != descriptor_after.st_size or len(source) > MAX_DOCTOR_SOURCE_BYTES:
                raise _DoctorLoadError("doctor module unavailable")
            named_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _same_stat(named_before, named_after):
                raise _DoctorLoadError("doctor module unavailable")
            return source, name
        finally:
            try:
                os.close(source_fd)
            except OSError:
                raise _DoctorLoadError("doctor module unavailable") from None
    except _DoctorLoadError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise _DoctorLoadError("doctor module unavailable") from None


def _load_sibling_doctor() -> Any:
    """Compile and execute bytes bound from the exact sibling doctor file."""
    script_path = os.path.realpath(__file__)
    directory_path = os.path.dirname(script_path)
    directory_fd: int | None = None
    try:
        directory_fd = os.open(
            directory_path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_info = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_info.st_mode):
            raise _DoctorLoadError("doctor module unavailable")
        source, doctor_name = _load_sibling_source(directory_fd, "doctor.py")
        code = compile(source, os.path.join(directory_path, doctor_name), "exec", dont_inherit=True)
        module = types.ModuleType("_codex_reconcile_sibling_doctor")
        module.__file__ = os.path.join(directory_path, doctor_name)
        exec(code, module.__dict__)
        required = (
            "SCHEMA",
            "SCHEMA_V2",
            "START_EVENTS",
            "SnapshotError",
            "TRUST_LABEL",
            "_identifier",
            "analyze_snapshot",
            "read_snapshot",
            "validate_snapshot",
        )
        if any(not hasattr(module, name) for name in required):
            raise _DoctorLoadError("doctor module unavailable")
        return module
    except _DoctorLoadError:
        raise
    except Exception:
        raise _DoctorLoadError("doctor module unavailable") from None
    finally:
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                raise _DoctorLoadError("doctor module unavailable") from None


# Keep the public contract names available without touching the sibling file.
# They are replaced with the bound doctor's exact values on first Darwin use.
SCHEMA = "codex-subagent-snapshot/v1"
SCHEMA_V2 = "codex-subagent-snapshot/v2"
START_EVENTS = frozenset({"task_started", "turn_started", "resumed"})
TRUST_LABEL = "unauthenticated-consistency-evidence"


class SnapshotError(ValueError):
    """A user-supplied snapshot cannot be safely analyzed."""

    def __init__(self, code: str, message: str = "invalid snapshot") -> None:
        super().__init__(message)
        self.code = code


_doctor: Any | None = None
_identifier: Any
analyze_snapshot: Any
read_snapshot: Any
validate_snapshot: Any


def _ensure_doctor_loaded() -> Any:
    """Bind the exact sibling doctor only after the platform gate passes."""
    global _doctor, SCHEMA, SCHEMA_V2, START_EVENTS, SnapshotError, TRUST_LABEL
    global _identifier, analyze_snapshot, read_snapshot, validate_snapshot
    if _doctor is None:
        if platform.system() != SUPPORTED_PLATFORM:
            raise _DoctorLoadError("unsupported-platform")
        _doctor = _load_sibling_doctor()
        SCHEMA = _doctor.SCHEMA
        SCHEMA_V2 = _doctor.SCHEMA_V2
        START_EVENTS = _doctor.START_EVENTS
        SnapshotError = _doctor.SnapshotError
        TRUST_LABEL = _doctor.TRUST_LABEL
        _identifier = _doctor._identifier
        analyze_snapshot = _doctor.analyze_snapshot
        read_snapshot = _doctor.read_snapshot
        validate_snapshot = _doctor.validate_snapshot
    return _doctor


TERMINAL_CLASSES = frozenset({"confirmed-terminal", "stale-ui-candidate"})


def _issue(code: str, identifier: str | None = None) -> dict[str, str]:
    issue = {"code": code}
    if identifier is not None:
        issue["key"] = identifier
    return issue


def _schema_of(snapshot: dict[str, Any] | None) -> str | None:
    return snapshot.get("schema") if snapshot is not None else None


def _items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return snapshot["agents"] if snapshot["schema"] == SCHEMA else snapshot["records"]


def _key(item: dict[str, Any], schema: str) -> str:
    return item["id"] if schema == SCHEMA else item["record_key"]


def _latest_start_after(agent: dict[str, Any], sequence: int | None) -> bool:
    if sequence is None:
        return False
    authoritative = [
        event for event in agent.get("events", []) if event.get("kind") in START_EVENTS
    ]
    latest = max(authoritative, key=lambda event: (event["seq"], event.get("kind", "")), default=None)
    # A newer explicit start/resume must begin the current running epoch. A
    # following running event is valid; a terminal event without another start
    # is not a reactivation.
    lifecycle = [
        event
        for event in agent.get("events", [])
        if event.get("kind") not in {"context_compacted", "not_found"}
    ]
    latest_lifecycle = max(lifecycle, key=lambda event: (event["seq"], event.get("kind", "")), default=None)
    if not latest or not latest_lifecycle or latest["seq"] <= sequence:
        return False
    if latest_lifecycle["kind"] not in {"running", "task_started", "turn_started", "resumed"}:
        return False
    return not any(
        event.get("kind") in {"task_complete", "turn_completed", "turn_interrupted", "turn_failed", "interrupted"}
        and event.get("seq", -1) > latest["seq"]
        for event in agent.get("events", [])
    )


def _aliases(before: dict[str, Any] | None, after: dict[str, Any] | None, schema: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    index = 1
    for snapshot in (after, before):
        if snapshot is None:
            continue
        for item in _items(snapshot):
            key = _key(item, snapshot["schema"])
            if key not in result:
                result[key] = f"record-{index}" if schema == SCHEMA_V2 else f"agent-{index}"
                index += 1
    return result


def _public_issue(issue: dict[str, str], aliases: dict[str, str], schema: str | None) -> dict[str, str]:
    result = {"code": issue["code"]}
    if "key" in issue:
        field = "record_key" if schema == SCHEMA_V2 else "id"
        result[field] = aliases.get(issue["key"], "record-unknown" if schema == SCHEMA_V2 else "agent-unknown")
    return result


def _duplicate_targets(snapshot: dict[str, Any] | None) -> set[str]:
    if snapshot is None or snapshot.get("schema") != SCHEMA:
        return set()
    counts: dict[str, int] = {}
    for agent in snapshot.get("agents", []):
        target = agent.get("exact_target")
        counts[target] = counts.get(target, 0) + 1
    return {target for target, count in counts.items() if count > 1}


def _namespace_collisions(snapshot: dict[str, Any] | None) -> set[str]:
    if snapshot is None or snapshot.get("schema") != SCHEMA:
        return set()
    agents = snapshot.get("agents", [])
    owners: dict[str, int] = {}
    for index, agent in enumerate(agents):
        identifier = agent.get("id")
        if identifier in owners:
            return {str(identifier)}
        owners[identifier] = index
    collisions = {
        agent.get("exact_target")
        for index, agent in enumerate(agents)
        if agent.get("exact_target") in owners and owners[agent.get("exact_target")] != index
    }
    return {str(value) for value in collisions}


def _transitioned(before_item: dict[str, Any], after_item: dict[str, Any], schema: str) -> bool:
    before_agent = before_item["agent"]
    after_agent = after_item["agent"]
    old_terminal = before_item["classification"] in TERMINAL_CLASSES
    new_terminal = after_item["classification"] in TERMINAL_CLASSES
    if old_terminal and new_terminal:
        # A replayed or higher terminal sequence is not a repair. Only the
        # visible stale-active -> done correction is a semantic transition.
        return before_agent["ui_state"] == "active" and after_agent["ui_state"] == "done"
    if not old_terminal and new_terminal:
        return before_agent["ui_state"] == "active" and after_agent["ui_state"] == "done"
    return False


def _coerce(value: dict[str, Any] | None) -> tuple[dict[str, Any] | None, SnapshotError | None]:
    if value is None:
        return None, None
    try:
        return validate_snapshot(value), None
    except SnapshotError as error:
        return None, error


def reconcile(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    targets: list[str] | None = None,
    before_error: SnapshotError | None = None,
    after_error: SnapshotError | None = None,
    *,
    record_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Compare reports without polling or mutating either input."""
    _ensure_doctor_loaded()
    selected = record_keys if record_keys is not None else targets
    if not selected:
        return {
            "schema": "codex-subagent-postflight/v1",
            "status": "inconclusive",
            "exit_code": 2,
            "issues": [{"code": "target-required"}],
            "trust": TRUST_LABEL,
        }
    for value in selected:
        try:
            _identifier(value)
        except SnapshotError:
            return {
                "schema": "codex-subagent-postflight/v1",
                "status": "inconclusive",
                "exit_code": 2,
                "issues": [{"code": "unsafe-target"}],
                "trust": TRUST_LABEL,
            }

    before, before_validation_error = _coerce(before)
    after, after_validation_error = _coerce(after)
    before_error = before_error or before_validation_error
    after_error = after_error or after_validation_error
    schema_before = _schema_of(before)
    schema_after = _schema_of(after)
    schema = schema_after or schema_before
    issues: list[dict[str, str]] = []

    if schema_before and schema_after and schema_before != schema_after:
        issues.append(_issue("schema-drift"))
    if before_error is not None:
        code = before_error.code if before_error.code in {"duplicate-id", "duplicate-target", "identifier-collision"} else f"before-{before_error.code}"
        issues.append(_issue(code))
    if after_error is not None:
        code = after_error.code if after_error.code in {"duplicate-id", "duplicate-target", "identifier-collision"} else f"after-{after_error.code}"
        issues.append(_issue(code))

    before_report = analyze_snapshot(before) if before is not None else None
    after_report = analyze_snapshot(after) if after is not None else None

    # A conclusive mismatching before snapshot is a valid repair baseline.  A
    # mismatching or inconclusive after snapshot can never pass acceptance.
    if before_report is not None and before_report["exit_code"] == 2:
        issues.append(_issue("before-doctor-inconclusive"))
    if after_report is not None:
        if after_report["exit_code"] == 1:
            issues.append(_issue("after-doctor-mismatch"))
        elif after_report["exit_code"] == 2:
            issues.append(_issue("after-doctor-inconclusive"))

    duplicate_before_targets = _duplicate_targets(before)
    duplicate_after_targets = _duplicate_targets(after)
    namespace_before = _namespace_collisions(before)
    namespace_after = _namespace_collisions(after)
    if duplicate_before_targets or duplicate_after_targets:
        issues.append(_issue("duplicate-target"))
    if namespace_before or namespace_after:
        issues.append(_issue("identifier-collision"))

    before_items = {}
    after_items = {}
    if before_report is not None and before is not None:
        before_items = {_key(item["agent"], schema_before or SCHEMA): item for item in before_report["records"]}
    if after_report is not None and after is not None:
        after_items = {_key(item["agent"], schema_after or SCHEMA): item for item in after_report["records"]}

    if schema == SCHEMA_V2:
        if before is None or after is None or schema_before != SCHEMA_V2 or schema_after != SCHEMA_V2:
            issues.append(_issue("snapshot-required"))
        elif before["scope"] != after["scope"]:
            issues.append(_issue("scope-drift"))
        elif set(before_items) != set(after_items):
            issues.append(_issue("record-key-drift"))
        if targets is not None and record_keys is None and schema in (SCHEMA_V2,):
            # The public v2 contract names this selector record_key.  The
            # Python compatibility parameter is tolerated, but the CLI uses
            # --record-key and reports this mismatch explicitly when needed.
            pass
    elif schema == SCHEMA and before is not None and after is not None:
        if set(before_items) != set(after_items):
            issues.append(_issue("identity-drift"))

    if before_report is not None and after_report is not None and not any(
        issue["code"] in {"schema-drift", "scope-drift", "record-key-drift"} for issue in issues
    ):
        before_active = before_report["derived_counts"]["active"]
        after_active = after_report["derived_counts"]["active"]
        valid_reactivations = 0

        for identifier in sorted(set(before_items) & set(after_items)):
            old = before_items[identifier]
            new = after_items[identifier]
            if old["classification"] in TERMINAL_CLASSES and new["classification"] == "confirmed-running":
                if not _latest_start_after(new["agent"], old["evidence"].get("latest_seq")):
                    issues.append(_issue("terminal-to-running-without-newer-start", identifier))
                else:
                    valid_reactivations += 1

            if schema == SCHEMA and old["agent"]["exact_target"] != new["agent"]["exact_target"]:
                issues.append(_issue("target-mapping-drift", identifier))

        if after_active - before_active - valid_reactivations > 0:
            issues.append(_issue("unexpected-active-increase"))

        if schema == SCHEMA:
            for identifier, item in after_items.items():
                if identifier not in before_items and item["classification"] == "confirmed-running":
                    issues.append(_issue("new-unknown-running", identifier))

        for identifier in selected:
            old = before_items.get(identifier)
            new = after_items.get(identifier)
            if schema == SCHEMA:
                if old is None:
                    old_candidates = [
                        item for item in before_items.values() if item["agent"].get("exact_target") == identifier
                    ]
                    if len(old_candidates) == 1:
                        old = old_candidates[0]
                if new is None:
                    new_candidates = [
                        item for item in after_items.values() if item["agent"].get("exact_target") == identifier
                    ]
                    if len(new_candidates) == 1:
                        new = new_candidates[0]
            if new is None:
                # v1 accepts an exact_target selector, but never resolves a
                # target through a drifted or ambiguous namespace.
                issues.append(_issue("target-not-found", identifier))
                continue
            if new["classification"] not in TERMINAL_CLASSES:
                issues.append(_issue("target-not-terminal", identifier))
                continue
            if new["classification"] == "stale-ui-candidate" or new["agent"]["ui_state"] != "done":
                issues.append(_issue("target-ui-not-done", identifier))
                continue
            if old is None:
                issues.append(_issue("target-not-found", identifier))
                continue
            before_sequence = old["evidence"].get("latest_seq")
            after_sequence = new["evidence"].get("latest_seq")
            if (
                before_sequence is not None
                and after_sequence is not None
                and after_sequence <= before_sequence
            ):
                issues.append(_issue("target-sequence-rollback", identifier))
            if old["classification"] in TERMINAL_CLASSES and not _transitioned(old, new, schema or SCHEMA):
                issues.append(_issue("target-no-transition", identifier))

    elif selected:
        for identifier in selected:
            issues.append(_issue("target-not-found", identifier))

    # Validation errors and evidence ambiguity are inconclusive.  Mismatch or
    # lifecycle/target issues are deterministic failures.
    inconclusive_codes = {
        "schema-drift",
        "scope-drift",
        "record-key-drift",
        "identifier-collision",
        "duplicate-target",
        "before-doctor-inconclusive",
        "after-doctor-inconclusive",
        "snapshot-required",
        "identity-drift",
        "target-mapping-drift",
    }
    if before_error is not None or after_error is not None or any(
        issue["code"] in inconclusive_codes for issue in issues
    ):
        status, exit_code = "inconclusive", 2
    elif issues:
        status, exit_code = "fail", 1
    else:
        status, exit_code = "pass", 0

    aliases = _aliases(before, after, schema)
    result: dict[str, Any] = {
        "schema": "codex-subagent-postflight/v2" if schema == SCHEMA_V2 else "codex-subagent-postflight/v1",
        "status": status,
        "exit_code": exit_code,
        "issues": [_public_issue(item, aliases, schema) for item in issues],
        "trust": TRUST_LABEL,
    }
    if schema == SCHEMA_V2 and schema_after == SCHEMA_V2 and after is not None:
        result["scope"] = "scope-1"
    if before_report is not None:
        result["before"] = {
            "status": before_report["status"],
            "derived_counts": before_report["derived_counts"],
            "ui_counts": before_report["ui_counts"],
            "record_correspondence_match": before_report["record_correspondence_match"],
        }
    if after_report is not None:
        result["after"] = {
            "status": after_report["status"],
            "derived_counts": after_report["derived_counts"],
            "ui_counts": after_report["ui_counts"],
            "record_correspondence_match": after_report["record_correspondence_match"],
        }
    return result


def _load_optional(path: str) -> tuple[dict[str, Any] | None, SnapshotError | None]:
    _ensure_doctor_loaded()
    try:
        return read_snapshot(path), None
    except (SnapshotError, RecursionError) as error:
        if isinstance(error, RecursionError):
            error = SnapshotError("invalid-json")
        return None, error


def _text_report(result: dict[str, Any]) -> str:
    lines = [f"status: {result['status']}", f"trust: {result['trust']}"]
    for issue in result["issues"]:
        if "id" in issue:
            suffix = f" ({issue['id']})"
        elif "record_key" in issue:
            suffix = f" ({issue['record_key']})"
        else:
            suffix = ""
        lines.append(f"issue: {issue['code']}{suffix}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check one explicit before/after snapshot pair.")
    parser.add_argument("--before", required=True, metavar="FILE", help="before JSON snapshot")
    parser.add_argument("--after", required=True, metavar="FILE", help="after JSON snapshot")
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--target", action="append", metavar="ID", help="v1 target selector; repeatable")
    selectors.add_argument("--record-key", action="append", dest="record_keys", metavar="KEY", help="v2 stable record key; repeatable")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if platform.system() != SUPPORTED_PLATFORM:
        # Keep --help available, but never inspect either snapshot on an
        # unsupported host.  Do not include host details in this report.
        result = {
            "schema": "codex-subagent-postflight/v1",
            "status": "inconclusive",
            "exit_code": 2,
            "issues": [{"code": "unsupported-platform"}],
            "trust": TRUST_LABEL,
        }
    else:
        before, before_error = _load_optional(args.before)
        after, after_error = _load_optional(args.after)
        # Selectors are checked after loading so a v2 invocation cannot silently
        # use the legacy target spelling.
        schema = _schema_of(after) or _schema_of(before)
        if schema == SCHEMA_V2 and args.target:
            result = {
                "schema": "codex-subagent-postflight/v2",
                "status": "inconclusive",
                "exit_code": 2,
                "issues": [{"code": "record-key-required"}],
                "trust": TRUST_LABEL,
            }
        elif schema == SCHEMA and args.record_keys:
            result = {
                "schema": "codex-subagent-postflight/v1",
                "status": "inconclusive",
                "exit_code": 2,
                "issues": [{"code": "target-required"}],
                "trust": TRUST_LABEL,
            }
        else:
            result = reconcile(
                before,
                after,
                args.target,
                before_error,
                after_error,
                record_keys=args.record_keys,
            )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    else:
        print(_text_report(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
