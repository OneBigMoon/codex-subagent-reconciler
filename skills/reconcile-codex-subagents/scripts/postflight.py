#!/usr/bin/env python3
"""Check a before/after pair of explicit sanitized snapshots."""

from __future__ import annotations

import argparse
import json
from typing import Any

try:
    from doctor import START_EVENTS, SnapshotError, analyze_snapshot, read_snapshot
except ImportError:  # pragma: no cover - supports package-style imports
    from .doctor import START_EVENTS, SnapshotError, analyze_snapshot, read_snapshot


TERMINAL_CLASSES = frozenset({"confirmed-terminal", "stale-ui-candidate"})
AUTHORITATIVE_EVENTS = START_EVENTS | {"running", "task_complete", "turn_completed", "turn_interrupted", "turn_failed", "interrupted"}


def _issue(code: str, identifier: str | None = None) -> dict[str, str]:
    issue = {"code": code}
    if identifier is not None:
        issue["id"] = identifier
    return issue


def _aliases(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    index = 1
    for snapshot in (after, before):
        if snapshot is None:
            continue
        for agent in snapshot["agents"]:
            if agent["id"] not in result:
                result[agent["id"]] = f"agent-{index}"
                index += 1
    return result


def _public_issue(issue: dict[str, str], aliases: dict[str, str]) -> dict[str, str]:
    result = {"code": issue["code"]}
    if "id" in issue:
        result["id"] = aliases.get(issue["id"], "agent-unknown")
    return result


def _latest_start_after(agent: dict[str, Any], sequence: int | None) -> bool:
    if sequence is None:
        return False
    latest = [event for event in agent.get("events", []) if event.get("kind") in AUTHORITATIVE_EVENTS]
    authoritative = max(latest, key=lambda event: (event["seq"], event.get("kind", "")), default=None)
    return bool(
        authoritative
        and authoritative["kind"] in START_EVENTS
        and authoritative["seq"] > sequence
    )


def _duplicate_targets(snapshot: dict[str, Any] | None) -> set[str]:
    if snapshot is None:
        return set()
    counts: dict[str, int] = {}
    for agent in snapshot.get("agents", []):
        target = agent.get("exact_target")
        counts[target] = counts.get(target, 0) + 1
    return {target for target, count in counts.items() if count > 1}


def _namespace_collisions(snapshot: dict[str, Any] | None) -> set[str]:
    if snapshot is None:
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


def reconcile(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    targets: list[str] | None = None,
    before_error: SnapshotError | None = None,
    after_error: SnapshotError | None = None,
) -> dict[str, Any]:
    """Compare reports without polling or mutating either input."""
    if not targets:
        return {
            "schema": "codex-subagent-postflight/v1",
            "status": "inconclusive",
            "exit_code": 2,
            "issues": [{"code": "target-required"}],
        }
    issues: list[dict[str, str]] = []
    before_report = analyze_snapshot(before) if before is not None else None
    after_report = analyze_snapshot(after) if after is not None else None

    if before_error is not None:
        issues.append(_issue(f"before-{before_error.code}"))
    if after_error is not None:
        if after_error.code == "duplicate-id":
            issues.append(_issue("duplicate-id"))
        else:
            issues.append(_issue(f"after-{after_error.code}"))

    if before_report is not None and before_report["exit_code"] != 0:
        issues.append(_issue("before-doctor-inconclusive" if before_report["exit_code"] == 2 else "before-doctor-mismatch"))
    if after_report is not None:
        if after_report["exit_code"] == 1:
            issues.append(_issue("after-doctor-mismatch"))
        elif after_report["exit_code"] == 2:
            issues.append(_issue("after-doctor-inconclusive"))

    before_agents = {item["agent"]["id"]: item for item in before_report["records"]} if before_report else {}
    after_agents = {item["agent"]["id"]: item for item in after_report["records"]} if after_report else {}
    duplicate_before_targets = _duplicate_targets(before)
    duplicate_after_targets = _duplicate_targets(after)
    namespace_before = _namespace_collisions(before)
    namespace_after = _namespace_collisions(after)
    if duplicate_before_targets or duplicate_after_targets:
        issues.append(_issue("duplicate-target"))
    if namespace_before or namespace_after:
        issues.append(_issue("identifier-collision"))

    if before_report is not None and after_report is not None:
        before_active = before_report["derived_counts"]["active"]
        after_active = after_report["derived_counts"]["active"]
        valid_reactivations = 0

        for identifier in sorted(set(before_agents) & set(after_agents)):
            old = before_agents[identifier]
            new = after_agents[identifier]
            if old["classification"] in TERMINAL_CLASSES and new["classification"] == "confirmed-running":
                if not _latest_start_after(new["agent"], old["evidence"]["latest_seq"]):
                    issues.append(_issue("terminal-to-running-without-newer-start", identifier))
                else:
                    valid_reactivations += 1

        if after_active - before_active - valid_reactivations > 0:
            issues.append(_issue("unexpected-active-increase"))

        for identifier, item in after_agents.items():
            if identifier not in before_agents and item["classification"] == "confirmed-running":
                issues.append(_issue("new-unknown-running", identifier))

        target_groups: dict[str, list[dict[str, Any]]] = {}
        for item in after_agents.values():
            target_groups.setdefault(item["agent"]["exact_target"], []).append(item)
        target_items = {
            target: items[0] for target, items in target_groups.items() if len(items) == 1
        }
        for identifier in targets or []:
            if namespace_after:
                continue
            if identifier in duplicate_after_targets:
                issues.append(_issue("duplicate-target"))
                continue
            item = after_agents.get(identifier) or target_items.get(identifier)
            if item is None:
                issues.append(_issue("target-not-found", identifier))
            elif item["classification"] not in TERMINAL_CLASSES:
                issues.append(_issue("target-not-terminal", identifier))

    elif targets:
        for identifier in targets:
            issues.append(_issue("target-not-found", identifier))

    after_inconclusive = (
        after_error is not None
        or bool(duplicate_after_targets)
        or bool(namespace_after)
        or (after_report is not None and after_report["exit_code"] == 2)
    )
    before_inconclusive = (
        before_error is not None
        or bool(duplicate_before_targets)
        or bool(namespace_before)
        or (before_report is not None and before_report["exit_code"] == 2)
    )
    if after_inconclusive or before_inconclusive:
        status, exit_code = "inconclusive", 2
    elif issues:
        status, exit_code = "fail", 1
    else:
        status, exit_code = "pass", 0

    aliases = _aliases(before, after)
    result: dict[str, Any] = {
        "schema": "codex-subagent-postflight/v1",
        "status": status,
        "exit_code": exit_code,
        "issues": [_public_issue(item, aliases) for item in issues],
    }
    if before_report is not None:
        result["before"] = {
            "status": before_report["status"],
            "derived_counts": before_report["derived_counts"],
            "ui_counts": before_report["ui_counts"],
        }
    if after_report is not None:
        result["after"] = {
            "status": after_report["status"],
            "derived_counts": after_report["derived_counts"],
            "ui_counts": after_report["ui_counts"],
        }
    return result


def _load_optional(path: str) -> tuple[dict[str, Any] | None, SnapshotError | None]:
    try:
        return read_snapshot(path), None
    except (SnapshotError, RecursionError) as error:
        if isinstance(error, RecursionError):
            error = SnapshotError("invalid-json")
        return None, error


def _text_report(result: dict[str, Any]) -> str:
    lines = [f"status: {result['status']}"]
    for issue in result["issues"]:
        suffix = f" ({issue['id']})" if "id" in issue else ""
        lines.append(f"issue: {issue['code']}{suffix}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check one explicit before/after snapshot pair.")
    parser.add_argument("--before", required=True, metavar="FILE", help="before JSON snapshot")
    parser.add_argument("--after", required=True, metavar="FILE", help="after JSON snapshot")
    parser.add_argument("--target", action="append", required=True, metavar="ID", help="exact target ID; repeatable")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    before, before_error = _load_optional(args.before)
    after, after_error = _load_optional(args.after)
    result = reconcile(before, after, args.target, before_error, after_error)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(_text_report(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
