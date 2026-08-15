---
name: codex-workflow-guardian
description: >-
  Explicitly invoked macOS-only Codex Plugin/Skill workflow (not an MCP server)
  for bounded intake, canonical matching-run recovery, capability-aware author
  routing, typed evidence handoff, and separate source, test, install,
  capability, and live acceptance.
---

# Codex Workflow Guardian

Use this Skill only after the exact explicit invocation
`$codex-workflow-guardian`. It is a general Codex delivery workflow, not a
snapshot-only diagnostic. Natural-language requests such as `全自动处理` or
`coordinate this delivery` are usage suggestions; they do not authorize Setup,
release, deployment, or lifecycle actions.

This repository ships a macOS-only Codex Plugin and Skills. It has no remote
tool, data, or authentication boundary, so `mcp_servers` is intentionally
empty and no MCP server is part of the workflow. All in Luna, OMC-compatible
roles, Headroom, and Ponytail are optional capabilities described by the
public lock contract. No private OMC Skill or OMC 4.15.1 installation is
included.

## macOS platform gate

This Skill is macOS-only. On a non-Darwin host, stop immediately: do not run
the workflow, inspect snapshots, or make any capability or diagnostic claim.
The bundled CLI `--help` remains available for documentation, but every real
doctor or postflight invocation exits `2` with `unsupported-platform` before
opening its input files. Resume only on macOS after a fresh platform check.

## Contract and bounded intake

Create one finite intake packet before dispatch. Preserve the user's wording
where possible. For reversible local work, derive bounded defaults from the
user message, the workspace, the nearest applicable `AGENTS.md`, and existing
tests; record every such assumption in the packet. Do not stop merely because
an optional field was omitted. Stop before a consequential action only when an
ambiguity can change the result, scope, or authorization boundary, or when
just-in-time permission is required:

```json
{
  "goal": "one concrete outcome",
  "constraints": ["platform, deadline, compatibility"],
  "allowed_scope": ["repositories, files, systems"],
  "forbidden_changes": ["explicit exclusions"],
  "validation_commands": ["commands the user permits"],
  "success_metric": "observable acceptance condition",
  "stop_condition": "declared stopping rule or blocker",
  "assumptions": ["reversible defaults and their evidence"],
  "permission_boundary": {
    "writes": "what may change",
    "external_actions": "what needs just-in-time permission"
  }
}
```

The packet is bounded by these named fields and finite lists. If a missing value
cannot be safely inferred, or if multiple interpretations would change the
result, scope, or authorization, surface the exact decision and stop. Do not
silently turn “全自动” into authorization, and do not broaden a list because a
tool suggests more work.

## Ownership, matching, and capability discovery

1. Native Codex is the authority for actions, permissions, and observed live
   state. Record the concrete goal and plan before changing files or systems.
2. Before any dispatch, perform the canonical lookup for the exact workspace,
   frozen goal identity, TaskGraph fingerprint, matching active run, and any
   issued `HostAction`. A prompt summary, compacted prose, guessed hash, or
   self-reported digest is not protocol truth.
3. Apply the dispatch ownership table below in order. Native/manual is a
   pre-dispatch fallback only: it is allowed only when no durable run has
   started, a fresh canonical lookup proves no active matching run exists, and
   no `HostAction` has been issued. Once a matching active run or issued
   `HostAction` exists, the run/action owns the scope. Preserve it and stop
   this dispatch; any later
   continuation may use only its canonical status/reconcile/resume or relay
   path. Never execute the same scope manually or via a subagent.
4. Discover each optional capability and classify it as `available`,
   `unavailable`, `configured-unverified`, or `blocked`, recording the source
   and freshness of the observation. Missing required capabilities fail closed;
   absence is reported and never simulated.
5. Before dispatch, use the runtime-provided shared host-slot budget when one
   exists. Count top-level Tasks and OMC-compatible author/verifier units
   together. If the budget or current count cannot be proven, do not parallelize
   on an assumed limit; use one bounded native/manual action only when the first
   row of the dispatch ownership table holds, otherwise preserve the owned run
   and stop.

### Dispatch ownership and HostAdapter errors

This is the single fallback/error table for a dispatch. A row that says “stop”
does not authorize a retry, subagent, or manual duplicate.

| Phase or fresh evidence | Required result | May continue? |
| --- | --- | --- |
| Pre-dispatch; no durable run started, a fresh canonical lookup proves no active matching run, and no issued `HostAction` | One bounded native/manual action may proceed. Do not claim durable resume or exactly-once. | Yes, within the bounded scope. |
| Matching active run or issued `HostAction` | Preserve the exact run/action and stop this dispatch. A later continuation may use only its canonical status, reconcile, resume, or relay path. | No fallback; owner-only continuation. |
| An issued typed `HostAction` requires a relay but no HostAdapter, permission, or fresh callable capability is proven | Preserve the typed action and return `ACTION_RELAY_REQUIRED`. | No; stop with no fallback. |
| For an issued typed `HostAction`, fresh discovery proves the exact requested host tool is absent | Preserve the typed action and return `HOST_CAPABILITY_BLOCKED`. | No; stop with no fallback. |
| Multiple/mismatching canonical records, or missing stable identity needed for lookup | Return an ambiguity/blocker, preserve any observed owner, and do not start another durable run. | Only the first row permits native/manual; otherwise stop. |

`ACTION_RELAY_REQUIRED` means “not yet proven callable”; it must not be
treated as tool absence. `HOST_CAPABILITY_BLOCKED` requires fresh discovery of
the exact tool and is not a permission or quota guess. Neither result permits a
manual or subagent substitute once the corresponding action or run exists.

All in Luna, when the pinned dependency is available, owns the matching
durable TaskGraph, Store, dependencies, recovery, and root completion. It does
not replace Guardian's permission or acceptance boundary. Store/schema/record
digest mismatch is the hard failure `PROTOCOL_INTEGRITY_FAILURE`.

### Durable Store and frozen run identity

The default durable Store is the selected persistent Codex home, not a
temporary directory:

```text
<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db
```

Resolve that path to a canonical absolute path and pass it explicitly as
`--db` on every All in Luna CLI invocation, including `status`, `start`,
`reconcile`, `resume`, and `drive`. Never rely on the current directory, an
environment default, or a relative database path. Setup must first establish
the trusted directory and the managed CLI. If either is not proven, do not
create a pretend Store or claim durable All in Luna completion; apply the
dispatch ownership table before any bounded native/manual decision.

Derive a deterministic, non-secret `intent_id` from a frozen non-secret
`goal_identity`, never from raw user text. The identity is a compact canonical
object, not a paraphrase of the request:

```json
{
  "schema": "guardian-goal/v1",
  "goal_ref": "goal-ref-example",
  "revision": "r1",
  "outcome_ids": ["outcome-example"],
  "deliverable_ids": ["deliverable-example"]
}
```

`goal_ref` is a stable, caller-owned, non-secret reference. `revision` is the
caller-owned revision for that goal. `outcome_ids` and `deliverable_ids` are
the frozen non-secret identifiers of the required outcomes and deliverables;
they are not generated by summarizing or rewording user text. All four values
must remain stable across fresh tasks for the same run identity. The object has
exactly these five fields; its scalar identifiers and arrays are non-empty,
each array identifier is non-empty, and arrays preserve the caller's frozen
order.

1. Freeze a `goal_identity` that preserves the outcome semantics needed for
   matching and recovery. Remove credentials, secrets, raw tokens, and PII or
   customer identifiers before freezing it. Replace each removed value with a
   caller-provided non-secret placeholder such as `<credential-ref>`,
   `<artifact-ref>`, or `<pii-ref>`; never derive a placeholder from the
   protected value, and never write the original value to an ID or log. The
   same approved reference must remain stable for the same identity; a random
   replacement would break deterministic matching. If safe redaction cannot
   preserve the required meaning, stop and request a safer reference.

2. Normalize only mechanical representation: use the exact allowed keys,
   UTF-8 canonical JSON, sorted object keys, compact separators, and stable
   caller-provided array order. Unicode normalization may be applied
   mechanically; never trim, translate, reinterpret, or semantically
   paraphrase an identifier.

```text
payload = canonical JSON, UTF-8:
  {"protocol":"guardian-intent/v1","workspace":"<canonical workspace root>","goal_identity":<frozen object above>}
intent_id = "cwfg-" + SHA-256(payload)[:16 lowercase hex]
```

Use sorted keys and compact JSON separators for the canonical encoding. The
result is opaque and exactly 21 characters long. Do not include assumptions,
plans, credentials, raw user text, or protected values in logs. The Store may
retain an authorized full `RunIntent` for operations, but its `goal` field must
remain sanitized; protected values may travel only through separately
authorized references. On the first successful `start`, persist and export a
non-secret run identity containing the exact runtime-issued values:

```json
{
  "protocol": "guardian-run/v1",
  "intent_id": "cwfg-<16 lowercase hex>",
  "run_ref": "<runtime-issued opaque reference>",
  "goal_identity": {
    "schema": "guardian-goal/v1",
    "goal_ref": "<exact frozen goal_ref>",
    "revision": "<exact frozen revision>",
    "outcome_ids": ["<exact frozen outcome id>"],
    "deliverable_ids": ["<exact frozen deliverable id>"]
  }
}
```

Fresh tasks must load the stored `intent_id` and `run_ref`; they must not
re-derive a `run_ref` or match by prompt text. A changed goal identity,
workspace, TaskGraph, or run reference is a mismatch and stops.

For one `intent_id`, inspect the Store before any dispatch. If there is no
record, `start` exactly once. If there is one active matching record, use only
`status`, `reconcile`, or `resume` as appropriate; never create a second run.
If there are multiple records, an ambiguous match, or a mismatch, stop. A
terminal record is not an invitation to start again: require the user to name a
new revision, then derive a new identity rather than silently reusing the old
one. If no stable `goal_ref`, revision, stored run identity, or canonical
lookup is available, do not claim exactly-once and do not start another durable
run. A failed or unavailable canonical lookup is not proof that no active run
exists. Native/manual is allowed only when the dispatch table's first row is
proven; active-run or issued-action evidence instead requires an ambiguity
stop.

### Executable zero-match evidence

The machine-readable `host_capability.zero_match_evidence` contract is the
only proof that permits a core-only fallback. The canonical run-identity
registry entry is exactly:

```text
<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json
```

Its directory is private (`0700`) and the entry is an atomic `0600` file that
contains exactly the non-secret `guardian-run/v1` identity for one exact
`intent_id`. A runtime-available proof requires safe canonical paths, that
exact `intent_id`, a successful exact Store query, no issued `HostAction`, and
a consistent Store/identity result. On the first successful All in Luna
`start`, persist the identity atomically with mode `0600` before any dispatch.
If that persistence fails, preserve the new run and stop with
`recovery-required`; any Store, sidecar, identity, schema, or digest mismatch
is `PROTOCOL_INTEGRITY_FAILURE`.

Core-only fresh zero-match is permitted only when All in Luna is freshly proven
`unavailable` (never `configured-unverified` or `blocked`), both the canonical
Store and its sidecar, plus the exact run-identity entry, are freshly proven
absent without following symlinks, no issued `HostAction` or current owner
evidence exists, and no known legacy or alternate Store reference exists. Only
then return `FRESH_ZERO_MATCH` and allow one bounded non-durable native/manual
flow; make no exactly-once or `resume` claim. If the Store or identity exists,
is unsafe or unreadable, its sidecar/existence state mismatches, the runtime is
`configured-unverified` or `blocked`, or a known legacy/alternate Store cannot
be inspected, return `OWNER_LOOKUP_BLOCKED` with fallback false. Never scan
arbitrary disk.

## Operational All in Luna binding

After creating the finite intake packet, perform fresh capability discovery for
the installed `$allinluna` Skill and runtime. Record its current receipt or
capability evidence and classify it as `available`, `unavailable`,
`configured-unverified`, or `blocked`. Mark it `available` only when that exact
installed Skill is callable and the runtime capability is verified now. A role
template, plugin listing, stale receipt, or Setup-only presence is not
executable proof.

When `available`, hand the unchanged intake packet—or a matching active-run
packet—to that exact installed `$allinluna` Skill. Load the Store with the
canonical absolute `--db` path before `status`, `start`, `resume`, `reconcile`,
or `drive`, and require the typed `RunIntent` and `TaskGraph` identity and
fingerprint to match exactly. Apply the zero/one/multiple/terminal decision
above; multiple or ambiguous matches stop, and a second run is never created.

Use a managed CLI only when its executable is named by fresh, verified Setup
receipt/capability evidence or is an explicitly supplied safe executable. Never
guess a path, search arbitrary locations, or treat a role template/plugin as an
executable. Relay each runtime action through the bound HostAdapter as the
exact `HostAction.tool` and `HostAction.arguments` (or its exact
`lane-direct-work/v1` plan); never translate, approximate, or substitute it.
When a typed `HostAction` requires a relay, apply the dispatch ownership and
HostAdapter error table for a missing relay, unproven permission/capability, or
fresh exact-tool absence. Ingest the raw runtime receipt immediately and tick
again; require matching
`action_contract_hash`, `actual_tool`, and `actual_capability`. A wrong tool is
`HOST_PROTOCOL_VIOLATION`, and a direct receipt is not completion: require the
verified `work-handoff/v1` and root runtime receipt. A `top_level_task` never
falls back to a subagent, the current thread, or direct execution.

For `unavailable`, `configured-unverified`, or `blocked`, use one bounded
native/manual flow only when the dispatch table's first row is proven and no
run/action is owned; otherwise preserve the owner and stop with the table's
result. Make no durable All in Luna completion, exactly-once resume, or
canonical-recovery claim for that flow.
Explicit `$codex-workflow-guardian` invocation authorizes this Skill only; it
never authorizes Setup, Setup writes, or external publish, deploy, destructive,
lifecycle, account, or machine actions. Request exact just-in-time permission
at those boundaries.

### HostAdapter capability boundary

HostAdapter is a host capability, not a Setup-installed dependency. Represent it
as the machine-readable `host_capability` contract and rediscover it in a fresh
new Codex task after `portable-full --apply`; Setup reaching `installed` never
proves that the relay is callable. The exact support matrix is:

| Host surface | Exact relay action | Fresh evidence and permission boundary |
| --- | --- | --- |
| Codex Desktop | `codex_app__create_thread` | The tool may be discoverable, but user permission is still required; without a fresh receipt it is `configured-unverified`. |
| Codex CLI | native `lane-direct` capability | Fresh new-task capability discovery and user permission are required; otherwise `configured-unverified`. |
| IDE | native `lane-direct` capability | Fresh new-task capability discovery and user permission are required; otherwise `configured-unverified`. |

If the exact surface, version, policy, or permission does not expose the
action, apply the dispatch ownership and HostAdapter error table. Never
install, simulate, or silently grant HostAdapter access. `portable-full
--apply` therefore stops at installed; a separate post-install capability
acceptance is required.

## Built-in author-lite baseline

The core workflow includes a minimal author-lite baseline and does not depend on
Ponytail. Keep Sol responsible for requirements, architecture, production-risk
framing, permission boundaries, and final acceptance; keep the implementation
author responsible for the bounded diff and its reproducible checks; and use a
fresh independent verifier for acceptance. Keep handoffs typed, keep the
evidence layers separate, and never let a template, hook, or green test stand
in for a capability or live receipt. Ponytail `lite` can simplify an
implementation author's work when explicitly available, but the entire
Ponytail Plugin—including lifecycle hooks and any Node boundary—is an optional
machine-integration enhancement. It is never silently installed, enabled, or
trusted for `core` or `portable-full`.

## Author routing and independent acceptance

For an OMC-compatible route, keep the roles distinct:

- Sol frames requirements, architecture, production-source risk, permission
  boundaries, and the acceptance plan. A Sol template is not proof that the
  model or entitlement is callable.
- Route a small, bounded, low-risk implementation or verification unit to
  Spark only when a fresh capability receipt proves the exact callable model
  and the shared-slot budget permits it. A generic error, timeout, or quota
  guess is not a capability receipt.
- Route material, security-sensitive, integration-heavy, long-chain, or
  Spark-unavailable work to Luna when its exact pinned capability is available.
  Once transferred, keep the contract with that author; do not silently
  bounce it between lanes.
- The implementation author may report changed paths and validations but may
  not be the sole final acceptor. Use an independent verifier (native Codex is
  acceptable) with a fresh view of the diff and evidence.

If Sol, Spark, Luna, or an independent verifier is unavailable, state the
native/manual fallback only if the dispatch table's first row is proven;
otherwise state the unfulfilled role and stop without a duplicate. Do not claim
OMC routing, quota, final acceptance, or durable completion from templates
alone. Ponytail `lite` is author-only and may simplify code; it never waives
security, validation, recovery, permission, or acceptance checks. Its hooks are
never auto-trusted.

## Typed handoffs and evidence

Every routed unit has a typed handoff. Keep it small, deterministic, and free
of credentials or raw logs:

```json
{
  "task_id": "runtime-issued identity",
  "role": "sol|spark|luna|verifier|native",
  "model_receipt": "fresh reference or configured-unverified",
  "input_digest": "runtime-issued canonical digest or null",
  "allowed_scope": ["paths or systems"],
  "changed_paths": ["observed paths"],
  "validation": [{"command": "...", "result": "pass|fail|skipped"}],
  "permissions_used": ["specific JIT grants"],
  "risks_and_blockers": ["explicit residuals"],
  "status": "complete|blocked|needs-review"
}
```

Attach evidence by layer, never as one undifferentiated “done” flag:

```json
{
  "layer": "source|tests|installed|capability|live",
  "observed_at": "2026-08-15T12:34:56Z",
  "receipt_ref": "optional-non-secret-runtime-receipt-ref",
  "source": "command, runtime Store, browser, log, DB, or device",
  "result": "what was actually observed",
  "trust": "direct-observation|runtime-receipt|command-output|unauthenticated-consistency-evidence"
}
```

`observed_at` is required and is an RFC3339 UTC timestamp with a `Z` suffix;
it records when the evidence was observed, never a receipt identifier.
`receipt_ref` is optional and separately carries a non-secret receipt
reference. Use `direct-observation` only for a current native observation, not
for a self-report or a stale template. Reconciler output remains
`unauthenticated-consistency-evidence`.

The implementation author can produce source and test evidence. Installation,
callable-capability, and live evidence need their own current checks. A green
test, an installed receipt, a role template, or a snapshot cannot prove model
access, current browser/log/database/device behavior, or deployment.

## Recovery, permissions, and acceptance

- After compact, resume, or transport interruption, rediscover the canonical
  matching run and reconcile its Store/dependencies/recovery state before
  continuing. Do not reconstruct protocol state from this document or prompt
  history.
- If a journal or receipt reports drift, incomplete rollback, stale ownership,
  or schema mismatch, stop with a concrete recovery blocker. Do not report a
  bare `ready` state. Use these separate states instead: `installation-ready`
  means the intended files and receipt are present; `capability-configured-unverified`
  means configuration was observed but no fresh callable capability receipt
  exists; and `acceptance-installed` means the installation layer passed its
  declared current acceptance. None of these states proves live behavior.
- Native Codex remains the permission authority. Obtain just-in-time, exact
  user permission immediately before external publish, deploy, destructive,
  lifecycle, account, or machine-integration actions. Permission for one
  layer does not authorize another.
- Keep these acceptance claims separate: source diff, test/validation result,
  installed target and receipt, callable capability, and current live/browser/
  log/DB/device behavior. Stop at the declared success metric or a concrete
  blocker; do not silently expand scope.
- Use `$reconcile-codex-subagents` only for explicit synthetic or sanitized
  read-only consistency evidence. It never closes, archives, deletes,
  interrupts, repairs, deploys, or authorizes a lifecycle target.

The `portable-full` target means reproducible public functional equivalence:
the pinned All in Luna Plugin/CLI wheel and 14 sanitized role templates. Setup
does not install Ponytail and neither enables nor trusts hooks; those are
separate machine-integration decisions requiring explicit authorization and
live acceptance. It is not a byte-copy of a dirty local RC2/OMC/Headroom state.
Start a fresh Codex task after Setup and require role, model, and reasoning
receipts before treating a lane as configured.

## Input and security boundary

The workflow itself may make authorized in-scope source/config/test changes
through native host tools. The bundled Reconciler scripts accept only explicit
regular JSON files containing synthetic or sanitized evidence. They do not use
network, databases, polling, filesystem scans, hooks, MCP, or app automation.
Their outputs are unauthenticated consistency claims, never protocol truth or
authorization; every v1/v2 JSON or text report carries
`trust: unauthenticated-consistency-evidence`.

Do not copy installed plugin/model/provider internals, expose credentials, or
hard-code local absolute paths. If a promise cannot be evidenced in the
current task, narrow the claim to the evidence that is available.
