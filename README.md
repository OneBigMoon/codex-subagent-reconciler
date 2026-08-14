# Codex Subagent Reconciler

`reconcile-codex-subagents` is a small, read-only Agent Skill for reconciling
sanitized Codex subagent snapshots. It compares lifecycle evidence with the
counts shown in a UI and fails acceptance when ghosts, stale rows, or compact /
resume inconsistencies recur.

Repository slug: `codex-subagent-reconciler`.

This is an unofficial community project. It is not a permanent upstream fix
for Codex Desktop creating ghost subagents. Self-check/prevention here means
detecting recurrence and failing acceptance; it does not prevent Desktop from
creating ghosts.

## Install

Install from `OneBigMoon/codex-subagent-reconciler` with `$skill-installer`,
using the path `skills/reconcile-codex-subagents`. For a manual install, copy
that directory into your Codex skills directory.

## Triggers

Use it when a run reports a ghost or stale subagent, a running-count mismatch,
or compact/resume inconsistency. Example prompts include:

- “Check the ghost subagent count from this sanitized snapshot.”
- “Investigate stale running rows after compact/resume.”
- “校准假运行的子智能体计数。”
- “检查 compact / resume 后的子智能体状态。”

Never provide raw logs, databases, screenshots, credentials, or guessed IDs.

## Snapshot schema

Input is explicit JSON with `schema: codex-subagent-snapshot/v1`:

```json
{
  "schema": "codex-subagent-snapshot/v1",
  "ui_counts": {"active": 1, "done": 0},
  "agents": [{
    "id": "synthetic-agent-1",
    "name": "synthetic-worker",
    "ui_state": "active",
    "live_state": "running",
    "events": [{"seq": 1, "kind": "task_started", "source": "test"}],
    "exact_target": "synthetic-agent-1"
  }]
}
```

Only synthetic or sanitized data belongs in a snapshot.

## Commands

```sh
python3 skills/reconcile-codex-subagents/scripts/doctor.py --input snapshot.json
python3 skills/reconcile-codex-subagents/scripts/doctor.py --input snapshot.json --json
python3 skills/reconcile-codex-subagents/scripts/doctor.py --input snapshot.json --json --show-identifiers
python3 skills/reconcile-codex-subagents/scripts/postflight.py \
  --before before.json --after after.json --target synthetic-agent-1 --json
```

Both tools are read-only and never scan, poll, use a network, or write files.
The doctor aliases identifiers by default; reveal them only with its explicit
`--show-identifiers` flag.

Exit codes are `0` for a consistent/pass result, `1` for a count or postflight
failure, and `2` for invalid, unsupported, ambiguous, or inconclusive input.
One terminal signal is insufficient: terminal lifecycle evidence must be
corroborated by a normalized terminal live state or two distinct event sources.

## Lifecycle boundary

Use official callable tools to build a sanitized snapshot, run the doctor, and
then postflight. Only when the user explicitly authorizes one exact confirmed
ghost and an official lifecycle API exists may an operator perform exactly one
closure through that API; never script the closure. Finish with fresh UI
verification. Do not write SQLite, patch `app.asar`, guess IDs, archive/delete,
batch-interrupt, upload raw data, or claim a terminal state from one signal.

The durable fix belongs upstream: Desktop should expose a stable lifecycle
contract and reconcile UI state after compact/resume. This skill only supplies
a deterministic acceptance check.
