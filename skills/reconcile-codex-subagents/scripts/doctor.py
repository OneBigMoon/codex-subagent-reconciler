#!/usr/bin/env python3
"""Reconcile one explicit, sanitized Codex subagent snapshot."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from typing import Any

SCHEMA = "codex-subagent-snapshot/v1"
REPORT_SCHEMA = "codex-subagent-report/v1"
MAX_INPUT_BYTES = 1 << 20
MAX_AGENTS = 1000
MAX_EVENTS_PER_AGENT = 1000
MAX_TOTAL_EVENTS = 10000
MAX_STRING_LENGTH = 256
MAX_SOURCE_LENGTH = 128
MAX_SEQUENCE = (1 << 63) - 1

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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(value: Any, *, max_length: int, code: str, nonempty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > max_length or (nonempty and not value):
        raise SnapshotError(code)
    return value


def _normalize_live_state(value: Any) -> str:
    live_state = _string(value, max_length=MAX_STRING_LENGTH, code="invalid-live-state")
    normalized = LIVE_STATE_NORMALIZATION.get(live_state.lower())
    if normalized is None:
        raise SnapshotError("invalid-live-state")
    return normalized


def validate_snapshot(payload: Any) -> dict[str, Any]:
    """Validate and return a bounded, normalized snapshot without side effects."""
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise SnapshotError("unsupported-schema")

    counts = payload.get("ui_counts")
    if not isinstance(counts, dict) or set(counts) != {"active", "done"}:
        raise SnapshotError("invalid-ui-counts")
    for key in ("active", "done"):
        if not _is_int(counts[key]) or counts[key] < 0 or counts[key] > MAX_AGENTS:
            raise SnapshotError("invalid-ui-counts")

    agents = payload.get("agents")
    if not isinstance(agents, list) or len(agents) > MAX_AGENTS:
        raise SnapshotError("invalid-agents")

    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    normalized: list[dict[str, Any]] = []
    total_events = 0
    required = {"id", "name", "ui_state", "live_state", "events", "exact_target"}
    for agent in agents:
        if not isinstance(agent, dict) or not required.issubset(agent):
            raise SnapshotError("invalid-agent")
        identifier = _string(agent["id"], max_length=MAX_STRING_LENGTH, code="invalid-agent")
        if identifier in seen_ids:
            raise SnapshotError("duplicate-id")
        seen_ids.add(identifier)
        name = _string(agent["name"], max_length=MAX_STRING_LENGTH, code="invalid-agent")
        target = _string(agent["exact_target"], max_length=MAX_STRING_LENGTH, code="invalid-agent")
        if target in seen_targets:
            raise SnapshotError("duplicate-target")
        seen_targets.add(target)
        ui_state = agent["ui_state"]
        if ui_state not in ("active", "done"):
            raise SnapshotError("invalid-ui-state")
        live_state = _normalize_live_state(agent["live_state"])

        events = agent["events"]
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_AGENT:
            raise SnapshotError("invalid-events")
        total_events += len(events)
        if total_events > MAX_TOTAL_EVENTS:
            raise SnapshotError("too-many-events")
        clean_events: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict) or not {"seq", "kind", "source"}.issubset(event):
                raise SnapshotError("invalid-event")
            sequence = event["seq"]
            if not _is_int(sequence) or sequence < 0 or sequence > MAX_SEQUENCE:
                raise SnapshotError("invalid-event")
            kind = _string(event["kind"], max_length=MAX_STRING_LENGTH, code="invalid-event")
            source = _string(event["source"], max_length=MAX_SOURCE_LENGTH, code="invalid-event")
            clean_events.append({"seq": sequence, "kind": kind, "source": source})

        normalized.append(
            {
                "id": identifier,
                "name": name,
                "ui_state": ui_state,
                "live_state": live_state,
                "events": clean_events,
                "exact_target": target,
            }
        )
    id_indexes = {agent["id"]: index for index, agent in enumerate(normalized)}
    for index, agent in enumerate(normalized):
        target_owner = id_indexes.get(agent["exact_target"])
        if target_owner is not None and target_owner != index:
            raise SnapshotError("identifier-collision")
    return {"schema": SCHEMA, "ui_counts": dict(counts), "agents": normalized}


def _read_stdin_bytes() -> bytes:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    data = stream.read(MAX_INPUT_BYTES + 1)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes):
        raise SnapshotError("invalid-json")
    return data


def _read_file_bytes(path: str) -> bytes:
    descriptor: int | None = None
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SnapshotError("invalid-file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            return handle.read(MAX_INPUT_BYTES + 1)
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
    """Read only one explicit regular file, or one bounded stdin stream."""
    try:
        raw = _read_stdin_bytes() if path == "-" else _read_file_bytes(path)
        if len(raw) > MAX_INPUT_BYTES:
            raise SnapshotError("input-too-large")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs_no_duplicates)
        return validate_snapshot(payload)
    except SnapshotError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise SnapshotError("invalid-json")


def _lifecycle(agent: dict[str, Any]) -> dict[str, Any]:
    """Resolve lifecycle evidence in sequence order, conservatively."""
    try:
        ordered = sorted(enumerate(agent["events"]), key=lambda item: (item[1]["seq"], item[0]))
    except (KeyError, TypeError, RecursionError):
        return {"state": "ambiguous", "reason": "invalid-evidence", "latest": None, "latest_seq": None}
    lifecycle: list[dict[str, Any]] = []
    seen_at_seq: dict[int, str] = {}
    not_found = False
    unsupported = False
    for _, event in ordered:
        kind = event["kind"]
        if kind not in KNOWN:
            unsupported = True
            continue
        if kind == NOT_FOUND:
            not_found = True
            continue
        if kind in NEUTRAL:
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
        lifecycle.append(event)

    if unsupported:
        return {"state": "ambiguous", "reason": "unsupported-event", "latest": None, "latest_seq": None}
    if not lifecycle:
        if not_found:
            return {"state": "ambiguous", "reason": "not-found-only", "latest": None, "latest_seq": None}
        return {"state": "orphan-unknown", "reason": "no-lifecycle", "latest": None, "latest_seq": None}

    latest = lifecycle[-1]
    if latest["kind"] in TERMINATE:
        terminal_sources = {event["source"] for event in lifecycle if event["kind"] in TERMINATE}
        try:
            normalized_live_state = _normalize_live_state(agent["live_state"])
        except SnapshotError:
            normalized_live_state = "invalid"
        corroborated = (
            normalized_live_state in TERMINAL_LIVE_STATES or len(terminal_sources) >= 2
        )
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
    }


def analyze_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return report plus private evidence used by postflight."""
    records: list[dict[str, Any]] = []
    active = 0
    done = 0
    inconclusive = False
    for agent in snapshot["agents"]:
        evidence = _lifecycle(agent)
        state = evidence["state"]
        if state == "running":
            classification = "confirmed-running"
            active += 1
        elif state == "terminal":
            done += 1
            classification = (
                "stale-ui-candidate" if agent["ui_state"] == "active" else "confirmed-terminal"
            )
        elif state == "orphan-unknown":
            classification = "orphan-unknown"
            inconclusive = True
        else:
            classification = "ambiguous"
            inconclusive = True
        records.append({"agent": agent, "classification": classification, "evidence": evidence})

    derived = {"active": active, "done": done}
    counts_match = derived == snapshot["ui_counts"]
    if inconclusive:
        status = "inconclusive"
        exit_code = 2
    elif not counts_match:
        status = "mismatch"
        exit_code = 1
    else:
        status = "consistent"
        exit_code = 0

    return {
        "status": status,
        "exit_code": exit_code,
        "schema": REPORT_SCHEMA,
        "ui_counts": dict(snapshot["ui_counts"]),
        "derived_counts": derived,
        "counts_match": counts_match,
        "records": records,
        "inconclusive": inconclusive,
    }


def _alias_maps(records: list[dict[str, Any]]) -> dict[str, tuple[str, str, str]]:
    return {
        item["agent"]["id"]: (f"agent-{index}", f"worker-{index}", f"target-{index}")
        for index, item in enumerate(records, 1)
    }


def public_report(report: dict[str, Any], show_identifiers: bool = False) -> dict[str, Any]:
    aliases = _alias_maps(report["records"])
    records: list[dict[str, Any]] = []
    for item in report["records"]:
        agent = item["agent"]
        alias_id, alias_name, alias_target = aliases[agent["id"]]
        records.append(
            {
                "id": agent["id"] if show_identifiers else alias_id,
                "name": agent["name"] if show_identifiers else alias_name,
                "ui_state": agent["ui_state"],
                "live_state": agent["live_state"],
                "classification": item["classification"],
                "exact_target": agent["exact_target"] if show_identifiers else alias_target,
            }
        )
    return {
        "schema": report["schema"],
        "status": report["status"],
        "exit_code": report["exit_code"],
        "ui_counts": report["ui_counts"],
        "derived_counts": report["derived_counts"],
        "counts_match": report["counts_match"],
        "inconclusive": report["inconclusive"],
        "records": records,
    }


def error_report(error: SnapshotError) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "status": "inconclusive",
        "exit_code": 2,
        "error": error.code,
        "inconclusive": True,
    }


def _text_report(report: dict[str, Any]) -> str:
    lines = [f"status: {report['status']}"]
    if "ui_counts" in report:
        ui = report["ui_counts"]
        derived = report["derived_counts"]
        lines.append(f"ui: active={ui['active']} done={ui['done']}")
        lines.append(f"derived: active={derived['active']} done={derived['done']}")
        for record in report["records"]:
            lines.append(f"{record['id']} ({record['name']}): {record['classification']}")
    else:
        lines.append(f"error: {report['error']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile one explicit sanitized subagent snapshot.")
    parser.add_argument("--input", required=True, metavar="FILE|-", help="JSON snapshot file, or '-' for stdin")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a JSON report")
    parser.add_argument(
        "--show-identifiers",
        action="store_true",
        help="show input IDs, names, and exact targets (opt-in)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        snapshot = read_snapshot(args.input)
        report = public_report(analyze_snapshot(snapshot), args.show_identifiers)
    except SnapshotError as error:
        report = error_report(error)
    except RecursionError:
        report = error_report(SnapshotError("invalid-json"))
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(_text_report(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
