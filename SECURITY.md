# Security

Codex Workflow Guardian is a macOS-only community project, not an OpenAI
official product. It is a delivery workflow. Guardian is the composition,
permission, and acceptance entry workflow; All in Luna is only an optional
durable TaskGraph runtime. It is distributed as a GitHub-backed Codex repo
marketplace source; this does not claim publication in the universal public
Plugins Directory. No MCP server, remote tool, or service is shipped because
there is no remote tool/service boundary. `mcp_servers` is intentionally `[]`;
never copy host MCP configuration, server registrations, or credentials.
The manifest has no OS installation gate, so a non-macOS system may discover or
install the Plugin; support remains macOS-only. Every bundled executable Skill
must fail closed on a non-Darwin host before loading snapshots or performing
managed reads/writes.
Authorized, in-scope implementation, configuration, and test writes may use
native host tools. Its bundled reconciler scripts are stdlib-only and strictly
read-only. Treat all input as untrusted even when it is described as sanitized.

Dependency and setup boundaries are explicit. `workflow-dependencies.lock.json`
is the source for the pinned All in Luna and Ponytail commits, licenses, and
artifact hashes; it records Headroom package `headroom-ai` `0.34.0`, canonical
repository `https://github.com/headroomlabs-ai/headroom`, Apache-2.0, and
Python `>=3.10`, but deliberately does not lock Headroom artifact or transitive
dependency hashes. Headroom is detect-only and needs separate authorization;
no presence or version observation is a readiness claim. The Guardian release
itself must be supplied by a canonical GitHub-backed repo marketplace resolver
and a user-audited exact 40-hex ref. During
`--check`, Guardian bootstrap itself does not directly write managed content,
but it executes validated Codex/Python probes and external programs may
maintain their own state. Release acceptance therefore uses a fresh isolated
`CODEX_HOME` and compares its tree, mtimes, and hashes before and after. Normal
installation may instead use an explicit selected persistent target. Only
explicit `--apply` may fetch and hash the locked All in Luna wheel or add the
locked Plugin selectors and sanitized agent templates under that selected
target; `--uninstall` only removes unchanged role files and the managed venv
proven owned by its receipt. Every Plugin added by Setup, including All in
Luna, is `installed-but-unowned`: Codex CLI 0.146.0 does not prove creator or
changed ownership, so no Plugin enters receipt ownership, rollback deletion, or
`--uninstall` deletion. Legacy `owned_plugins` fields are ignored and cannot
authorize removal. Plugin removal is always a separate, explicitly confirmed
Codex operation after preservation checks. No mutable branch, dependency
resolver, or transitive download is trusted.

External probes have bounded output and timeouts, and cleanup covers the
original process group. Portable macOS process-group cleanup cannot contain a
descendant that deliberately detaches with `setsid`; Setup therefore runs only
the explicitly selected, path-validated Codex, Git, and Python executables and
does not claim to sandbox a hostile executable.

Setup never auto-trusts or enables Ponytail hooks. Ponytail Skills themselves do
not require Node, but lifecycle hooks may execute a user-supplied Node runtime;
Node is therefore an external `machine-integration` prerequisite only. Existing
activation or trust state requires separate live acceptance; review and
explicitly authorize any hook outside the bootstrap receipt boundary. The 14
sanitized OMC-compatible agent templates provide role separation only, not the
full/private OMC Skill or service; model entitlement is
`configured-unverified` until a fresh Codex task returns role, model, and
reasoning receipts. Headroom is check-only/manual and its presence cannot prove
readiness, health, routing, launch, initialization, or trust. Receipt and
journal SHA sidecars detect corruption or schema drift, but are not
authentication or tamper-proofing: a same-UID actor who can rewrite
`CODEX_HOME` can rewrite both the record and its sidecar. Receipts are evidence
of the recorded operation, not authorization, protocol truth, publisher
identity, or proof of durable All in Luna completion. Plugin selectors are not
receipt-owned resources; ownership evidence applies only to managed role files
and the venv. Setup therefore accepts state only through fixed managed-path/
plugin allowlists and re-observes live state before and after each
ownership-sensitive operation.

Executable trust is an explicit canonical-path contract. Native Codex runs only
after the OpenAI Developer ID signature requirement passes. A Homebrew JS
launcher is accepted only as a constrained static locator for its versioned
bundled native Codex; Setup never executes Node or the JavaScript wrapper.
Git is bound to `/usr/bin/git`, Python is bound to an absolute `PYTHON_BIN`,
and child processes receive a sanitized PATH rather than the ambient PATH.
The trust boundary is root and the current UID. Only the standard Homebrew
prefix may use `admin group-write`, and only without an extended ACL. This
project does not defend against a same-UID actor, root, or a trusted admin
inside that compatibility boundary.

The on-disk bootstrap receipt is `private-local` ownership evidence. Stdout is
only a `public-redacted` projection: it excludes absolute paths, hashes,
device/inode values, environment values, and raw logs. On failure, retain the
receipt, transaction journal, and any journaled quarantine entry for recovery;
do not overwrite or manually delete them. The stdout schema is
`codex-workflow-guardian/bootstrap-stdout/v1`; it retains only:
`schema`, `projection`, `generation`, `mode`, `platform`, `status`,
`canonical_source_verified`, `guardian_ref_verified`, `existing_receipt`,
`installation_status`, `capability_status`, `acceptance_level`,
`planned` count/action summaries, `conflict_summary` count/categories,
and component name/status/reason/version summary, rollback performed/action
summary, notes, conflicts name/reason pairs, failure, and recovery.
It excludes absolute or relative paths, `*_relative` fields, SHA/hash/digest/commit/selector
values, device/inode, ownership/provenance, credentials, environment values, and
raw logs. Receipt and journal sidecars are not
authentication or publisher proof.
`acceptance_level` is one public four-value contract:
`preflight|installed|uninstalled|transaction-recovery`; it is distinct from
`capability_status` and does not prove a fresh callable capability.

The only core-only fallback proof is the strict
`host_capability.zero_match_evidence` contract. The canonical Store is
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db`, and
the exact run identity is
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json`
under a `0700` directory with an atomic `0600` `guardian-run/v1` entry. A
runtime-available proof requires safe canonical paths, the exact `intent_id`, a
successful exact Store query, no issued `HostAction`, and consistent
Store/identity results. The first successful `start` persists the non-secret
identity before dispatch; failure preserves the new run and stops
`recovery-required`. Store, sidecar, identity, schema, or digest mismatch is
`PROTOCOL_INTEGRITY_FAILURE`.

Only a fresh `unavailable` All in Luna state, fresh symlink-safe absence of the
canonical Store/sidecar and exact identity entry, no current owner or issued
`HostAction`, and no known legacy/alternate Store reference permits
`FRESH_ZERO_MATCH` for one bounded non-durable native/manual flow. It carries
no exactly-once or `resume` claim. Existing, unsafe, unreadable, inconsistent,
or uninspectable owner evidence returns `OWNER_LOOKUP_BLOCKED` with fallback
false; never scan arbitrary disk. `configured-unverified` and `blocked` never
qualify as unavailable.

- Supply only synthetic or sanitized snapshots. Never include raw logs,
  database exports, screenshots, credentials, usernames, paths, host serials,
  hardware UUIDs, or actionable lifecycle targets.
- v2 `scope` is a caller-generated lowercase-hex correlation token. It must not
  be derived from private host data. `record_key` is a stable, non-actionable
  correlation key. Both are redacted in reports.
- Every schema object has an exact declared field set. Duplicate JSON keys,
  constants such as NaN/Infinity, control or bidi characters, unsafe
  identifiers, and invalid scopes are rejected. v1 retains diagnosis
  compatibility: an unknown event is reported as `unsupported-event` and exits
  `2`; strict v2 rejects unknown event kinds during schema validation.
- File input is bounded and read-only. The scripts open one explicit regular
  file, use `fstat`, and request `O_NOFOLLOW` and `O_NONBLOCK` where available.
  They do not scan directories, follow a changing stream, poll, write, or
  mutate an application/database.
- Reports always use aliases. This is redaction, not anonymization, and the
  former `--show-identifiers` behavior is not available. Every v1/v2 JSON or
  text report carries `trust: unauthenticated-consistency-evidence`. Snapshot
  claims are never authorization or protocol truth; arbitrary source labels
  cannot prove a terminal state.
- Each resolved record must match lifecycle, declared `live_state`, and
  `ui_state` (`running`/`running`/`active` or `terminal`/`terminal`/`done`).
  Opposite records cannot cancel mismatches in aggregate counts.
- Scope, record-key, identity, target-mapping, schema, before/after count drift,
  and non-forward selected lifecycle sequences fail closed. A conclusive
  mismatching before snapshot may be used as a repair baseline, but an after
  mismatch or ambiguity cannot pass.
- This repository has no MCP server, app, network client, lifecycle
  mutator, archive/delete path, deployment path, or external integration in the
  bundled reconciler. External publish, deployment, and destructive actions
  still require just-in-time user permission through native workflow tools.

Guardian provenance is separate from receipt ownership. Portable-full requires
the Codex marketplace resolver to report one unique installed Guardian, the
canonical GitHub-backed repo marketplace URL/root, and a checkout whose Git
HEAD equals the user-audited 40-hex ref. The lock and marketplace files must
match exactly for the pinned repositories, paths, refs, and artifact hashes. A
local marketplace source is development/testing-only; portable-full `--apply`
fails closed as `configured-unverified` when the resolver cannot prove
canonical provenance. A receipt records the operation and owned paths; it is
not publisher identity, authorization, or proof of signing. An exact All in
Luna Plugin is always installed-but-unowned, whether pre-existing or added by
Setup, and is never removed by receipt rollback or Setup uninstall. Remove it
only through a separately confirmed Codex operation after the complete Plugin
preservation check.

GitHub HTTPS/network access is a connectivity prerequisite for the pinned repo
marketplace and artifact hosts. The manifest's `Network` capability describes
only the explicit `--apply` download boundary; it does not grant implicit
network permission. Network access is separate from account-dependent Codex
login, model entitlement, quota, and private OMC access; reachability never
proves those capabilities.

Private vulnerability reporting is not configured for this repository (current
truth). Never publish secrets, raw snapshots, or exploit details in a public
issue. This is a release limitation; if a report needs coordination, open only
a non-sensitive issue requesting a private contact path.
