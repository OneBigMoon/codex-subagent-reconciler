#!/usr/bin/env python3
"""Read-only reconciliation of explicit, sanitized Codex snapshots.

The v1 schema is retained for diagnosis compatibility.  Operational
before/after checks use v2, which has no actionable lifecycle target and uses a
sanitized macOS scope fingerprint plus a stable ``record_key``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import stat
import sys
import unicodedata
from typing import Any

sys.dont_write_bytecode = True

SCHEMA = "codex-subagent-snapshot/v1"
SCHEMA_V2 = "codex-subagent-snapshot/v2"
REPORT_SCHEMA = "codex-subagent-report/v1"
REPORT_SCHEMA_V2 = "codex-subagent-report/v2"
TRUST_LABEL = "unauthenticated-consistency-evidence"
MAX_INPUT_BYTES = 1 << 20
MAX_AGENTS = 1000
MAX_EVENTS_PER_AGENT = 1000
MAX_TOTAL_EVENTS = 10000
MAX_STRING_LENGTH = 256
MAX_SOURCE_LENGTH = 128
MAX_SEQUENCE = (1 << 63) - 1
MAX_INTEGER_DIGITS = len(str(MAX_SEQUENCE))
SUPPORTED_PLATFORM = "Darwin"

START_EVENTS = frozenset({"task_started", "turn_started", "resumed"})
ACTIVATE = START_EVENTS | {"running"}
TERMINATE = frozenset(
    {"task_complete", "turn_completed", "turn_interrupted", "turn_failed", "interrupted"}
)
NEUTRAL = frozenset({"context_compacted"})
NOT_FOUND = "not_found"
KNOWN = ACTIVATE | TERMINATE | NEUTRAL | {NOT_FOUND}

LIVE_STATE_NORMALIZATION = {
    "running": "running",
    "active": "running",
    "pending": "running",
    "done": "terminal",
    "completed": "terminal",
    "idle": "terminal",
    "interrupted": "terminal",
    "failed": "terminal",
    "terminal": "terminal",
    "cancelled": "terminal",
    "canceled": "terminal",
}
TERMINAL_LIVE_STATES = frozenset({"terminal"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SCOPE_RE = re.compile(r"^macos:[0-9a-f]{16,128}$")


class SnapshotError(ValueError):
    """A user-supplied snapshot cannot be safely analyzed."""

    def __init__(self, code: str, message: str = "invalid snapshot") -> None:
        super().__init__(message)
        self.code = code


def _object_pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("duplicate-key")
        result[key] = value
    return result


def _reject_constants(value: str) -> Any:
    raise SnapshotError("invalid-json")


def _parse_int(value: str) -> int:
    """Parse only bounded JSON integer tokens before calling ``int``."""
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_INTEGER_DIGITS:
        raise SnapshotError("invalid-json")
    try:
        return int(value)
    except ValueError:
        raise SnapshotError("invalid-json")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_unsafe_text(value: str) -> bool:
    # Cc covers ASCII controls and C0/C1; Cf covers terminal/bidi format
    # controls.  Rejecting both keeps reports safe for terminals and logs.
    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)


def _string(value: Any, *, max_length: int, code: str, nonempty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > max_length or (nonempty and not value):
        raise SnapshotError(code)
    if _has_unsafe_text(value):
        raise SnapshotError("unsafe-text")
    return value


def _identifier(value: Any, *, code: str = "invalid-identifier") -> str:
    candidate = _string(value, max_length=MAX_STRING_LENGTH, code=code)
    if candidate in {".", ".."} or _IDENTIFIER_RE.fullmatch(candidate) is None:
        raise SnapshotError("unsafe-identifier")
    return candidate


def _scope(value: Any) -> str:
    candidate = _string(value, max_length=MAX_STRING_LENGTH, code="invalid-scope")
    if _SCOPE_RE.fullmatch(candidate) is None:
        raise SnapshotError("invalid-scope")
    return candidate


def _normalize_live_state(value: Any) -> str:
    live_state = _string(value, max_length=MAX_STRING_LENGTH, code="invalid-live-state")
    normalized = LIVE_STATE_NORMALIZATION.get(live_state.lower())
    if normalized is None:
        raise SnapshotError("invalid-live-state")
    return normalized


def _exact_object(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise SnapshotError(code)
    return value


def _validate_counts(value: Any) -> dict[str, int]:
    counts = _exact_object(value, {"active", "done"}, "invalid-ui-counts")
    for key in ("active", "done"):
        if not _is_int(counts[key]) or counts[key] < 0 or counts[key] > MAX_AGENTS:
            raise SnapshotError("invalid-ui-counts")
    return {"active": counts["active"], "done": counts["done"]}


def _validate_events(value: Any, *, strict_kinds: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_EVENTS_PER_AGENT:
        raise SnapshotError("invalid-events")
    cleaned: list[dict[str, Any]] = []
    for event in value:
        item = _exact_object(event, {"seq", "kind", "source"}, "invalid-event")
        sequence = item["seq"]
        if not _is_int(sequence) or sequence < 0 or sequence > MAX_SEQUENCE:
            raise SnapshotError("invalid-event")
        kind = _string(item["kind"], max_length=MAX_STRING_LENGTH, code="invalid-event")
        source = _string(item["source"], max_length=MAX_SOURCE_LENGTH, code="invalid-event")
        if strict_kinds and kind not in KNOWN:
            raise SnapshotError("unsupported-event")
        cleaned.append({"seq": sequence, "kind": kind, "source": source})
    return cleaned


def _validate_common_events(agents: list[dict[str, Any]]) -> None:
    total_events = 0
    for agent in agents:
        total_events += len(agent["events"])
        if total_events > MAX_TOTAL_EVENTS:
            raise SnapshotError("too-many-events")


def _validate_v1(payload: dict[str, Any]) -> dict[str, Any]:
    _exact_object(payload, {"schema", "ui_counts", "agents"}, "unknown-field")
    if payload["schema"] != SCHEMA:
        raise SnapshotError("unsupported-schema")
    counts = _validate_counts(payload["ui_counts"])
    agents = payload["agents"]
    if not isinstance(agents, list) or len(agents) > MAX_AGENTS:
        raise SnapshotError("invalid-agents")

    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    normalized: list[dict[str, Any]] = []
    required = {"id", "name", "ui_state", "live_state", "events", "exact_target"}
    for raw_agent in agents:
        item = _exact_object(raw_agent, required, "invalid-agent")
        identifier = _identifier(item["id"], code="invalid-agent")
        if identifier in seen_ids:
            raise SnapshotError("duplicate-id")
        seen_ids.add(identifier)
        name = _string(item["name"], max_length=MAX_STRING_LENGTH, code="invalid-agent")
        target = _identifier(item["exact_target"], code="invalid-agent")
        if target in seen_targets:
            raise SnapshotError("duplicate-target")
        seen_targets.add(target)
        ui_state = item["ui_state"]
        if ui_state not in ("active", "done"):
            raise SnapshotError("invalid-ui-state")
        normalized.append(
            {
                "id": identifier,
                "name": name,
                "ui_state": ui_state,
                "live_state": _normalize_live_state(item["live_state"]),
                "events": _validate_events(item["events"]),
                "exact_target": target,
            }
        )
    _validate_common_events(normalized)
    id_indexes = {agent["id"]: index for index, agent in enumerate(normalized)}
    for index, agent in enumerate(normalized):
        target_owner = id_indexes.get(agent["exact_target"])
        if target_owner is not None and target_owner != index:
            raise SnapshotError("identifier-collision")
    return {"schema": SCHEMA, "ui_counts": counts, "agents": normalized}


def _validate_v2(payload: dict[str, Any]) -> dict[str, Any]:
    _exact_object(payload, {"schema", "scope", "ui_counts", "records"}, "unknown-field")
    if payload["schema"] != SCHEMA_V2:
        raise SnapshotError("unsupported-schema")
    scope = _scope(payload["scope"])
    counts = _validate_counts(payload["ui_counts"])
    records = payload["records"]
    if not isinstance(records, list) or len(records) > MAX_AGENTS:
        raise SnapshotError("invalid-records")
    seen_keys: set[str] = set()
    normalized: list[dict[str, Any]] = []
    required = {"record_key", "ui_state", "live_state", "events"}
    for raw_record in records:
        item = _exact_object(raw_record, required, "invalid-record")
        key = _identifier(item["record_key"], code="invalid-record")
        if key in seen_keys:
            raise SnapshotError("duplicate-record-key")
        seen_keys.add(key)
        ui_state = item["ui_state"]
        if ui_state not in ("active", "done"):
            raise SnapshotError("invalid-ui-state")
        normalized.append(
            {
                "record_key": key,
                "ui_state": ui_state,
                "live_state": _normalize_live_state(item["live_state"]),
                "events": _validate_events(item["events"], strict_kinds=True),
            }
        )
    _validate_common_events(normalized)
    return {"schema": SCHEMA_V2, "scope": scope, "ui_counts": counts, "records": normalized}


def validate_snapshot(payload: Any) -> dict[str, Any]:
    """Validate and return a bounded, normalized snapshot without side effects."""
    if not isinstance(payload, dict):
        raise SnapshotError("unsupported-schema")
    schema = payload.get("schema")
    if schema == SCHEMA:
        return _validate_v1(payload)
    if schema == SCHEMA_V2:
        return _validate_v2(payload)
    raise SnapshotError("unsupported-schema")


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _read_file_bytes(path: str) -> bytes:
    descriptor: int | None = None
    try:
        named_before = os.lstat(path)
        if not stat.S_ISREG(named_before.st_mode):
            raise SnapshotError("invalid-file")
        flags = os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        descriptor_before = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_before.st_mode):
            raise SnapshotError("invalid-file")
        if not _same_file(named_before, descriptor_before):
            raise SnapshotError("invalid-file")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            raw = handle.read(MAX_INPUT_BYTES + 1)
            descriptor_after = os.fstat(handle.fileno())
            named_after = os.lstat(path)
            if not _same_file(named_before, descriptor_after) or not _same_file(
                named_before, named_after
            ):
                raise SnapshotError("invalid-file")
            return raw
    except SnapshotError:
        raise
    except (OSError, UnicodeError):
        raise SnapshotError("invalid-file")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def read_snapshot(path: str) -> dict[str, Any]:
    """Read one explicit bounded regular file."""
    try:
        raw = _read_file_bytes(path)
        if len(raw) > MAX_INPUT_BYTES:
            raise SnapshotError("input-too-large")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_pairs_no_duplicates,
            parse_constant=_reject_constants,
            parse_int=_parse_int,
        )
        return validate_snapshot(payload)
    except SnapshotError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise SnapshotError("invalid-json")


def _lifecycle(agent: dict[str, Any]) -> dict[str, Any]:
    """Resolve lifecycle evidence conservatively, one start/resume epoch at a time."""
    try:
        ordered = sorted(enumerate(agent["events"]), key=lambda item: (item[1]["seq"], item[0]))
    except (KeyError, TypeError, RecursionError):
        return {"state": "ambiguous", "reason": "invalid-evidence", "latest": None, "latest_seq": None}

    lifecycle: list[dict[str, Any]] = []
    not_found_events: list[dict[str, Any]] = []
    seen_at_seq: dict[int, str] = {}
    unsupported = False
    for _, event in ordered:
        kind = event["kind"]
        if kind not in KNOWN:
            unsupported = True
            continue
        previous = seen_at_seq.get(event["seq"])
        if previous is not None and previous != kind:
            return {
                "state": "ambiguous",
                "reason": "conflicting-same-seq",
                "latest": None,
                "latest_seq": None,
            }
        seen_at_seq[event["seq"]] = kind
        if kind == NOT_FOUND:
            not_found_events.append(event)
        elif kind not in NEUTRAL:
            lifecycle.append(event)

    if unsupported:
        return {"state": "ambiguous", "reason": "unsupported-event", "latest": None, "latest_seq": None}
    if not lifecycle:
        if not_found_events:
            return {"state": "ambiguous", "reason": "not-found-only", "latest": None, "latest_seq": None}
        return {"state": "orphan-unknown", "reason": "no-lifecycle", "latest": None, "latest_seq": None}

    latest = lifecycle[-1]
    if not_found_events and not_found_events[-1]["seq"] >= latest["seq"]:
        return {
            "state": "ambiguous",
            "reason": "not-found-newer",
            "latest": latest,
            "latest_seq": latest["seq"],
        }

    # A start/resume begins a new epoch.  Terminal corroboration from an old
    # epoch cannot prove a later terminal after the reactivation.
    epoch: list[dict[str, Any]] = []
    for event in lifecycle:
        if event["kind"] in ACTIVATE:
            epoch = [event]
        else:
            epoch.append(event)

    if latest["kind"] in TERMINATE:
        terminal_kinds = {event["kind"] for event in epoch if event["kind"] in TERMINATE}
        try:
            normalized_live_state = _normalize_live_state(agent["live_state"])
        except SnapshotError:
            normalized_live_state = "invalid"
        # Distinct terminal event kinds are independent sanitized evidence;
        # source labels are deliberately not treated as authority.
        corroborated = normalized_live_state in TERMINAL_LIVE_STATES or len(terminal_kinds) >= 2
        if not corroborated:
            return {
                "state": "ambiguous",
                "reason": "insufficient-terminal-corroboration",
                "latest": latest,
                "latest_seq": latest["seq"],
            }
    return {
        "state": "running" if latest["kind"] in ACTIVATE else "terminal",
        "reason": "latest-lifecycle",
        "latest": latest,
        "latest_seq": latest["seq"],
        "epoch_start_seq": epoch[0]["seq"] if epoch else None,
    }


def _agent_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return snapshot["agents"] if snapshot["schema"] == SCHEMA else snapshot["records"]


def _item_key(item: dict[str, Any], schema: str) -> str:
    return item["id"] if schema == SCHEMA else item["record_key"]


def _lifecycle_ui_match(state: str, ui_state: str) -> bool | None:
    expected_ui_state = {"running": "active", "terminal": "done"}.get(state)
    if expected_ui_state is None:
        return None
    return ui_state == expected_ui_state


def _lifecycle_live_match(state: str, live_state: str) -> bool | None:
    if state not in {"running", "terminal"}:
        return None
    return state == live_state


def analyze_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a report plus private evidence used by postflight."""
    snapshot = validate_snapshot(snapshot)
    schema = snapshot.get("schema")
    if schema not in (SCHEMA, SCHEMA_V2):
        raise SnapshotError("unsupported-schema")
    records: list[dict[str, Any]] = []
    active = 0
    done = 0
    inconclusive = False
    record_correspondence_match = True
    for item in _agent_items(snapshot):
        evidence = _lifecycle(item)
        state = evidence["state"]
        if state == "running":
            classification = "confirmed-running"
            active += 1
        elif state == "terminal":
            done += 1
            classification = "stale-ui-candidate" if item["ui_state"] == "active" else "confirmed-terminal"
        elif state == "orphan-unknown":
            classification = "orphan-unknown"
            inconclusive = True
        else:
            classification = "ambiguous"
            inconclusive = True
        lifecycle_ui_match = _lifecycle_ui_match(state, item["ui_state"])
        lifecycle_live_match = _lifecycle_live_match(state, item["live_state"])
        if lifecycle_ui_match is False or lifecycle_live_match is False:
            record_correspondence_match = False
        records.append(
            {
                "agent": item,
                "classification": classification,
                "evidence": evidence,
                "lifecycle_ui_match": lifecycle_ui_match,
                "lifecycle_live_match": lifecycle_live_match,
            }
        )

    derived = {"active": active, "done": done}
    ui_rows = {
        "active": sum(item["ui_state"] == "active" for item in _agent_items(snapshot)),
        "done": sum(item["ui_state"] == "done" for item in _agent_items(snapshot)),
    }
    live_counts_match = derived == snapshot["ui_counts"]
    ui_rows_match = ui_rows == snapshot["ui_counts"]
    counts_match = live_counts_match and ui_rows_match
    if inconclusive:
        status, exit_code = "inconclusive", 2
    elif not counts_match or not record_correspondence_match:
        status, exit_code = "mismatch", 1
    else:
        status, exit_code = "consistent", 0

    report: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "schema": REPORT_SCHEMA if schema == SCHEMA else REPORT_SCHEMA_V2,
        "snapshot_schema": schema,
        "ui_counts": dict(snapshot["ui_counts"]),
        "derived_counts": derived,
        "ui_row_counts": ui_rows,
        "live_counts_match": live_counts_match,
        "ui_rows_match": ui_rows_match,
        "counts_match": counts_match,
        "record_correspondence_match": record_correspondence_match,
        "inconclusive": inconclusive,
        "records": records,
    }
    if schema == SCHEMA_V2:
        report["scope"] = snapshot["scope"]
    return report


def _alias_maps(records: list[dict[str, Any]], schema: str) -> dict[str, tuple[str, str, str]]:
    if schema == SCHEMA:
        return {
            item["agent"]["id"]: (f"agent-{index}", f"worker-{index}", f"target-{index}")
            for index, item in enumerate(records, 1)
        }
    return {
        item["agent"]["record_key"]: (f"record-{index}", f"record-{index}", f"record-{index}")
        for index, item in enumerate(records, 1)
    }


def public_report(report: dict[str, Any], *_legacy_options: Any) -> dict[str, Any]:
    """Return a redacted report; legacy display options are ignored."""
    schema = report.get("snapshot_schema", SCHEMA)
    aliases = _alias_maps(report["records"], schema)
    claims = {
        "confirmed-running": "snapshot-running-claim",
        "confirmed-terminal": "snapshot-terminal-claim",
        "stale-ui-candidate": "snapshot-stale-ui-claim",
        "ambiguous": "snapshot-ambiguous-claim",
        "orphan-unknown": "snapshot-unknown-claim",
    }
    records: list[dict[str, Any]] = []
    for item in report["records"]:
        agent = item["agent"]
        key = _item_key(agent, schema)
        alias_id, alias_name, alias_target = aliases[key]
        if schema == SCHEMA:
            records.append(
                {
                    "id": alias_id,
                    "name": alias_name,
                    "ui_state": agent["ui_state"],
                    "live_state": agent["live_state"],
                    "classification": claims.get(item["classification"], "snapshot-claim"),
                    "exact_target": alias_target,
                    "lifecycle_ui_match": item["lifecycle_ui_match"],
                    "lifecycle_live_match": item["lifecycle_live_match"],
                }
            )
        else:
            records.append(
                {
                    "record_key": alias_id,
                    "ui_state": agent["ui_state"],
                    "live_state": agent["live_state"],
                    "classification": claims.get(item["classification"], "snapshot-claim"),
                    "lifecycle_ui_match": item["lifecycle_ui_match"],
                    "lifecycle_live_match": item["lifecycle_live_match"],
                }
            )
    result: dict[str, Any] = {
        "schema": report["schema"],
        "status": report["status"],
        "exit_code": report["exit_code"],
        "ui_counts": report["ui_counts"],
        "derived_counts": report["derived_counts"],
        "ui_row_counts": report["ui_row_counts"],
        "live_counts_match": report["live_counts_match"],
        "ui_rows_match": report["ui_rows_match"],
        "counts_match": report["counts_match"],
        "record_correspondence_match": report["record_correspondence_match"],
        "inconclusive": report["inconclusive"],
        "records": records,
        "trust": TRUST_LABEL,
    }
    if schema == SCHEMA_V2:
        result["scope"] = "scope-1"
    return result


def error_report(error: SnapshotError) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "status": "inconclusive",
        "exit_code": 2,
        "error": error.code,
        "inconclusive": True,
        "trust": TRUST_LABEL,
    }


def _text_report(report: dict[str, Any]) -> str:
    lines = [f"status: {report['status']}", f"trust: {report['trust']}"]
    if "ui_counts" in report:
        ui = report["ui_counts"]
        derived = report["derived_counts"]
        lines.append(f"ui: active={ui['active']} done={ui['done']}")
        lines.append(f"derived: active={derived['active']} done={derived['done']}")
        for record in report["records"]:
            if "id" in record:
                lines.append(f"{record['id']} ({record['name']}): {record['classification']}")
            else:
                lines.append(f"{record['record_key']}: {record['classification']}")
    else:
        lines.append(f"error: {report['error']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile one explicit sanitized subagent snapshot.")
    parser.add_argument("--input", required=True, metavar="FILE", help="one explicit JSON snapshot file")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if platform.system() != SUPPORTED_PLATFORM:
        # Keep --help available, but never inspect a snapshot on an
        # unsupported host.  The error report is deliberately host-agnostic.
        report = error_report(SnapshotError("unsupported-platform"))
    else:
        try:
            snapshot = read_snapshot(args.input)
            report = public_report(analyze_snapshot(snapshot))
        except SnapshotError as error:
            report = error_report(error)
        except RecursionError:
            report = error_report(SnapshotError("invalid-json"))
    if args.as_json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        print(_text_report(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
