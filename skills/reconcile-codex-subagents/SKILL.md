---
name: reconcile-codex-subagents
description: >-
  Explicitly invoked, macOS-only, strictly diagnostic and read-only checks for
  sanitized Codex subagent snapshots, including ghost/stale rows, count
  contradictions, and compact/resume lifecycle evidence.
---

# Reconcile Codex Subagents

This supporting skill is only Guardian's read-only evidence component. Guardian
is the composition, permission, and acceptance entry workflow; All in Luna is
an optional durable TaskGraph runtime, not a substitute for either boundary.
No MCP server, remote tool, or service is shipped. This supporting skill never
authorizes or performs lifecycle mutation. It does
not close, interrupt, archive, delete, repair, or deploy anything. `doctor.py`
reads one explicit regular JSON file; `postflight.py` reads one explicit before
file and one explicit after file and emits a deterministic report.

Only synthetic or sanitized evidence is accepted. Do not include raw Codex
logs, databases, screenshots, credentials, names, paths, host identifiers, or
actionable lifecycle targets.

## macOS platform gate

This Skill is macOS-only. On a non-Darwin host, stop immediately: do not run
the diagnostic commands, inspect snapshots, or make any capability or
diagnostic claim. The bundled CLI `--help` remains available for documentation,
but every real doctor or postflight invocation exits `2` with
`unsupported-platform` before opening its input files. Resume only on macOS
after a fresh platform check.

## v1 diagnosis schema (compatibility)

`codex-subagent-snapshot/v1` has exactly these fields:

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

Every object has an exact field set; duplicate JSON keys are rejected. IDs and
targets are syntax-checked but are always replaced with aliases in output.
`--show-identifiers` is not supported.

## v2 operational before/after schema

Operational checks use `codex-subagent-snapshot/v2` with exactly these fields:

```json
{
  "schema": "codex-subagent-snapshot/v2",
  "scope": "macos:0123456789abcdef0123456789abcdef",
  "ui_counts": {"active": 1, "done": 0},
  "records": [{
    "record_key": "record-1",
    "ui_state": "active",
    "live_state": "running",
    "events": [{"seq": 1, "kind": "task_started", "source": "test"}]
  }]
}
```

`scope` is a caller-generated, stable, sanitized correlation token: lowercase
hex after `macos:`, 16–128 characters. It must not be a hardware UUID,
username, path, host serial, or a hash of private host data. `record_key` is a
stable non-actionable correlation key. Neither value is a lifecycle target.
The before and after scope and complete record-key set must match exactly.

For every resolved record, the derived lifecycle state must correspond to both
declared fields: `running` requires `live_state: running` and `ui_state: active`,
while `terminal` requires `live_state: terminal` and `ui_state: done`. A pair of
opposite records cannot cancel these mismatches in aggregate counts.

Allowed event kinds are `task_started`, `turn_started`, `resumed`, `running`,
`task_complete`, `turn_completed`, `turn_interrupted`, `turn_failed`,
`interrupted`, `context_compacted`, and `not_found`. Terminal evidence is
consistency evidence only: evidence from before the latest start/resume cannot
corroborate a later epoch; a newer `not_found` is inconclusive; arbitrary
free-form source labels alone never prove terminal state. For v1 diagnosis
compatibility, an otherwise well-formed unknown event kind is reported as
`unsupported-event` and exits `2` (inconclusive). Strict v2 rejects unknown
event kinds during schema validation and also exits `2`.

## Commands and exit codes

```sh
RECONCILER_SKILL_DIR="/path/to/reconcile-codex-subagents"  # directory containing this SKILL.md
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/doctor.py" --input snapshot.json
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/doctor.py" --input snapshot.json --json

# v1 compatibility selector
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/postflight.py" \
  --before before.json --after after.json --target synthetic-agent-1 --json

# strict v2 operational selector
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/postflight.py" \
  --before before-v2.json --after after-v2.json --record-key record-1 --json
```

For an ordinary installed-Plugin invocation, derive `RECONCILER_SKILL_DIR`
from the exact Reconciler Skill directory loaded by the host. Validate its
`../..` Plugin root contains `.codex-plugin/plugin.json` whose `name` is
`codex-workflow-guardian`; this installed-Plugin boundary is sufficient for
ordinary read-only use. Do not guess a source checkout or silently invoke
another explicit Skill. If stronger provenance is required, ask the user to
invoke `$setup-codex-workflow-guardian --check` separately.

Maintainers may instead bind doctor and postflight to a clean tracked checkout
at the exact audited commit, but only after the hardened file/hash,
canonical-root, root/current-UID ownership, permission, and no-extended-ACL
checks. This maintainer-only path does not add requirements to ordinary
installed-Plugin use. Set `PYTHON_BIN` to a trusted absolute executable; the
commands do not depend on the repository current working directory or ambient
`PATH`. Do not replace either binding with a PATH lookup, a bare interpreter
name, or a shell name lookup.

Exit `0` is pass/consistent, `1` is fail/mismatch, and `2` is invalid,
ambiguous, inconclusive, or unsupported. A conclusive mismatching before
snapshot can be a repair baseline; the after snapshot must be consistent. A
selected target must make a real transition, so an unchanged already-terminal
before/after pair fails. A selected transition must move lifecycle sequence
forward; replayed or rolled-back terminal evidence cannot pass. Stale UI/count
contradictions cannot return success.

For both v1 and v2, `doctor.py` derives live lifecycle counts and separately
counts the visible `ui_state` rows. It also reports per-record lifecycle/UI and
lifecycle/live correspondence. Both aggregate checks and every known record
must match; an aggregate count cannot hide a stale selected row. Public v1/v2
classifications are snapshot claims (`snapshot-running-claim`,
`snapshot-terminal-claim`, or `snapshot-stale-ui-claim`), not authority labels.

Both scripts are stdlib-only, bounded, non-networked, non-polling, and
non-writing. File input uses one regular-file descriptor with `fstat` and
available `O_NOFOLLOW`/`O_NONBLOCK` protections.
Redaction replaces identifiers with aliases but is not anonymization; every v1
and v2 JSON/text report carries `trust:
unauthenticated-consistency-evidence`. These snapshots and reports are
unauthenticated consistency claims, with snapshot-claim labels rather than
authorization language.
