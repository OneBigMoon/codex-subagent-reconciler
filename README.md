# Codex Workflow Guardian

`codex-workflow-guardian` is a macOS-only Codex Plugin/Skill workflow, not an
MCP server: it has no live remote tool, data, or authentication boundary. It
combines Guardian orchestration, the strictly read-only
`reconcile-codex-subagents` Reconciler, and the explicitly invoked
`setup-codex-workflow-guardian` Setup Skill. The legacy repository slug is
`codex-subagent-reconciler`.

This is an unofficial community project, not an OpenAI official product.

Languages: [English](README.md) | [简体中文](README.zh-CN.md) |
[日本語](README.ja.md) | [Español](README.es.md)

Supported platform: macOS only. The core Plugin install requires Codex CLI with
Plugin marketplace support and does not require Python. Running any bundled
script requires Python 3.9+; `portable-full --apply` requires Python 3.11+.
The manifest has no OS installation gate, so another OS may discover or install
the Plugin; that does not make it supported. Every bundled executable Skill
invocation fails closed on a non-Darwin host before snapshot loading or managed
reads/writes. Support remains macOS-only.

## Three-step mental model and shortest path

1. Use the already installed, user-trusted normal `codex` command as the
   pre-Setup trust boundary. Register the GitHub-backed marketplace at the
   audited immutable SHA and install the Guardian Plugin. This supplies the
   three bundled Guardian Skills; it does not copy credentials, MCP
   configuration, or private OMC.

   ```sh
   GUARDIAN_REF="AUDITED_COMMIT_SHA"
   codex plugin marketplace add OneBigMoon/codex-subagent-reconciler \
     --ref "$GUARDIAN_REF" --json
   codex plugin add codex-workflow-guardian@onebigmoon-codex-workflows --json
   ```

2. Start a new Codex task and explicitly call `$setup-codex-workflow-guardian`.
   The Skill first sets `E` as its own installed bundled path and verifies the
   expected canonical root with a clean immutable `exact-ref` check. Then it reads
   `codex plugin list --json` and identifies the unique installed Guardian selector
   as `P`. It then verifies `P` as canonical marketplace provenance, and checks
   that `P` and `E` have the same `exact-ref`, before reading and validating the
   Plugin JSON from `E`. The Skill discovers Python from the fixed macOS
   candidates, and statically resolves a supported Codex wrapper when needed; do
   not parse JSON or fill native/Python paths by hand. CLI `0.146.0`
   `codex plugin list --json` rows may omit `installedPath`; do not glob/scan,
   guess, or re-run `plugin add` to recover paths.
   Review the public `--check` projection, then
   grant just-in-time approval for one `--apply`. That apply installs the
   pinned All in Luna Plugin, its exact CLI wheel, and 14 sanitized role
   templates into the selected persistent `CODEX_HOME`. Ponytail, Headroom,
   Node, lifecycle hooks, and HostAdapter relay stay optional and separately
   authorized. Plugins added by Setup, including All in Luna, remain
   `installed-but-unowned`; receipt ownership is limited to proven managed role
   files and the venv.
3. For each later delivery, call `$codex-workflow-guardian`. It reuses one
   matching durable run when All in Luna is verified. Bounded native/manual
   fallback is eligible only before any owner exists and after a fresh
   canonical zero-match proof; an owned scope returns `ACTION_RELAY_REQUIRED`
   when its relay is unproven or `HOST_CAPABILITY_BLOCKED` when fresh discovery
   proves the exact tool absent, and stops without fallback. Use
   `$reconcile-codex-subagents` only for advanced, sanitized, read-only diagnostics.

The two normal `codex` commands above are the pre-Setup trust boundary. The
direct-native Mach-O, sanitized PATH, explicit Git/Python bindings, signing
checks, cutover/rollback procedure, and isolated maintainer acceptance below
are hardened advanced material. Most users only need this three-step path.

| Dependency class | Items | Setup behavior |
| --- | --- | --- |
| `bundled-with-guardian-plugin` | Guardian, Reconciler, and Setup Skills | Present in the installed Guardian Plugin |
| `automatic-after-explicit-setup-apply` | All in Luna Plugin, pinned CLI wheel, and 14 roles | The Plugin is installed-but-unowned; only the wheel, roles, and managed venv are eligible for receipt-backed ownership after one explicitly authorized `--apply` |
| `external-prerequisite` | macOS, Codex CLI, Python `>=3.11` for `portable-full`, working macOS Command Line Tools (or another trusted provider) for `/usr/bin/git`, and GitHub HTTPS | Supplied and trusted outside the managed transaction; Setup only detects the provider and never installs it |
| `detect-then-separately-authorize` | Entire Ponytail Plugin including lifecycle hooks/Node, Headroom, Node integration, and HostAdapter relay | Detected only; never silently installed, enabled, or trusted |
| `account-dependent` | Codex login, model entitlement/quota, and private OMC | Requires fresh account/capability evidence |
| `none` | MCP (`mcp_servers: []`) | No MCP configuration, server, or credential is copied |

## Authority and delivery workflow

Invoke `$codex-workflow-guardian` explicitly for a delivery. It takes the
user's goal and plan through matching-run discovery or resume, exact TaskGraph
fingerprint and shared host-slot review, bounded author routing, compact/resume
recovery, and separate source, test, and live acceptance. Native Codex remains
the authority for actions and live state.

All in Luna, OMC, Headroom, and Ponytail are optional, capability-detected
components. All in Luna is the optional durable TaskGraph runtime; Guardian is
the composition, permission, and acceptance entry workflow. When available,
All in Luna owns the matching durable TaskGraph/Store/dependencies/recovery/root
completion; OMC owns Sol→Spark/Luna author routing and independent verification;
Headroom is transport observation only; and Ponytail `lite` is author-only. The
14 bundled OMC-compatible role templates are sanitized routing templates, not
the full/private OMC installation, its Skill, its model entitlement, or its
quota. If a capability is unavailable, apply the state-specific owner rule and
never simulate that component. Native/manual fallback is allowed
only when no durable run exists, a fresh canonical lookup proves that no active
matching run exists, and no `HostAction` has been issued. Once a durable run,
active matching run, or issued `HostAction` owns the scope, preserve that owner
and stop; `ACTION_RELAY_REQUIRED` and `HOST_CAPABILITY_BLOCKED` are owner/blocker
results and never permit fallback. Resume must load the saved `goal_ref`,
`revision`, `intent_id`, and `run_ref`; never rematch by prompt text or
regenerate `run_ref`.
A Store/schema/digest mismatch is `PROTOCOL_INTEGRITY_FAILURE`. No MCP server,
remote tool, or service is shipped because this repository has no remote
tool/service boundary.
Plugins installed by Setup are state observations, not receipt-owned resources:
Codex CLI `0.146.0` does not prove which operation created a Plugin. Plugin
removal is therefore always a separate, explicitly confirmed Codex operation
after preservation checks; legacy `owned_plugins` receipt fields are ignored.
First confirm the exact Guardian selector identity and the preserved baseline,
then remove only the exact Guardian selector; this selector step does not
require zero marketplace dependencies. After Guardian removal, capture a fresh
post-Guardian-removal Plugin list. Only then remove the marketplace if that
fresh list proves no remaining installed selector depends on it; otherwise keep
the registration and stop.

Guardian's built-in `author-lite` rules keep the core workflow equivalent
without Ponytail: separate requirement/implementation/verification roles,
typed handoffs, and independent evidence. Ponytail is an enhancement only.

The default durable Store is
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db`; every
All in Luna CLI call passes this canonical absolute path with `--db`. First
freeze a non-secret `goal_identity` that preserves the goal's outcome semantics;
replace credentials, secrets, raw tokens, and PII/customer identifiers with
caller-provided non-secret `<credential-ref>`, `<artifact-ref>`, or `<pii-ref>`
placeholders. Never derive a placeholder from the protected value or put the
original text in an ID or log. The deterministic `intent_id` is `cwfg-` plus the
first 16 lowercase SHA-256 hex characters of canonical JSON containing
`protocol`, canonical workspace, and that frozen `goal_identity`; it is opaque
and 21 characters long. The Store may retain an authorized full `RunIntent`,
but its `goal` field remains sanitized and protected values stay behind
separately authorized references. A zero-record Store starts once; one active
match may be inspected/reconciled/resumed; a terminal, mismatched, or multiple
match stops and requires an explicit new revision. Without a trusted Setup
directory and managed CLI, no durable run is claimed.

The only core-only zero-match proof is the machine-readable
`host_capability.zero_match_evidence` contract. The exact identity registry is
`<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json`:
its directory is `0700`, its atomic `guardian-run/v1` entry is `0600`, and it
must agree with the canonical Store and exact `intent_id`. A runtime-available
proof requires safe canonical paths, a successful exact Store query, no issued
`HostAction`, and a consistent Store/identity result; first successful `start`
persists that non-secret identity before dispatch, and persistence failure
preserves the new run as `recovery-required`. Store, sidecar, identity, schema,
or digest mismatch is `PROTOCOL_INTEGRITY_FAILURE`.

Only when All in Luna is freshly `unavailable` (not
`configured-unverified`/`blocked`), the canonical Store and sidecar and the
exact identity entry are freshly absent without following symlinks, no current
owner or issued `HostAction` exists, and no known legacy/alternate Store
reference exists may the workflow return `FRESH_ZERO_MATCH` for one bounded
non-durable native/manual flow. It makes no exactly-once or `resume` claim. Any
existing, unsafe, unreadable, mismatched, or uninspectable Store/identity state
returns `OWNER_LOOKUP_BLOCKED` with fallback false; never scan arbitrary disk.

The Reconciler is an advanced, sanitized, read-only diagnostic tool. It never
dispatches, closes, archives, deletes, repairs, publishes, deploys, or changes
a lifecycle target.

External publish, deploy, destructive, and lifecycle actions require exact
just-in-time user permission. A green source/test run is not live, browser, or
device acceptance; those claims require separate current evidence.

Natural-language requests such as “全自动处理” or “coordinate this delivery”
are usage suggestions only. They do not authorize Setup, release, deployment,
or lifecycle actions; only the exact `$codex-workflow-guardian`,
`$reconcile-codex-subagents`, and `$setup-codex-workflow-guardian` calls are
Skill authorization. All three Skills keep `allow_implicit_invocation: false`.

## Profiles and evidence boundaries

| Profile | Acceptance boundary |
| --- | --- |
| `core` | Guardian Plugin plus the three Skills: Guardian, Reconciler, and Setup. |
| `portable-full` | `core` plus the pinned All in Luna Plugin/CLI wheel and 14 roles; Python `>=3.11`. Setup does not install Ponytail and neither enables nor trusts hooks; `--apply` reaches installed only, while HostAdapter callable capability requires post-install fresh-task acceptance. |
| `machine-integration` | Desktop/CLI skew; Headroom presence, version, health, and routing; a user-supplied Node runtime when lifecycle hooks need it; hooks and local startup. Requires separate authorization and live acceptance. |
| `account-dependent` | Codex login, model entitlement/quota, and private OMC Skills. Requires a fresh receipt. GitHub network access is a separate external prerequisite, not proof of any Codex account capability. |

Keep source/tests, installed state, callable capability, and behavior/live
evidence separate. A green source/test run, an installed receipt, or role
templates cannot prove model access or current browser/log/DB/device behavior.
The portable-full target is functional public equivalence, not a byte-copy of
this machine's dirty RC2/OMC/Headroom state. This project targets a GitHub-backed
Codex repo marketplace source; it is not a claim of inclusion in the universal
public Plugins Directory.

## Dependency matrix

| Component | Required or verified baseline | Boundary |
| --- | --- | --- |
| Codex Desktop/CLI | Minimum `0.146.0`; local CLI verified `0.146.0` (the Desktop embedded version may differ) | Native Codex is the action and permission authority. The `plugin list --json`, marketplace, and `plugin add --json` shapes are tested only at CLI `0.146.0`; schema divergence fails closed. |
| Python | `3.9` for the bundled scripts and Setup Skill; `>=3.11` for the managed All in Luna CLI | No bytecode or mutable resolver is required |
| All in Luna | `2.0.0-rc.3`, commit `723088a7c0d7342f077ad675c6ea72d7e3996536`, Apache-2.0; wheel SHA-256 `e2e59ce76deab1b39efe6feb1b64c983101c313268ce52dfa25787b77295dee1` | Optional durable runtime; exact wheel only, no dependency resolver |
| Ponytail | `4.9.0`, commit `2ed6c52c9d7e5e56942508591085fd45dea277d3`, MIT | Ponytail Skills themselves do not require Node. Lifecycle hooks may require a user-supplied Node runtime; Setup does not install, enable, or trust hooks, and machine integration validates Node separately. |
| OMC-compatible roles | 14 sanitized templates—not full/private OMC: `code-reviewer`, `coder`, `debugger`, `document-specialist`, `executor`, `explore`, `luna-coder`, `luna-worker`, `security-reviewer`, `sol-lead`, `spark-verifier`, `test-engineer`, `tracer`, `verifier` | Model entitlement is `configured-unverified` until a fresh role/model/reasoning receipt |
| Headroom | `headroom-ai` `0.34.0`, canonical repository [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom), Apache-2.0, Python `>=3.10` | Detect-only/manual integration. Artifact hashes and transitive dependency hashes are not locked; presence/version never proves readiness, health, routing, launch, initialization, or trust. |
| Node.js | User-supplied `node` runtime for the `machine-integration` profile | Ponytail Skills do not need Node; only lifecycle hooks may. Setup never auto-installs or trusts hooks. |
| Xcode Command Line Tools | A working provider for fixed `/usr/bin/git` is required by `portable-full` | External prerequisite; Setup only detects it and never installs it. |
| HostAdapter / host relay (`host_capability`) | Exact top-level relay: `codex_app__create_thread` on Codex Desktop or native `lane-direct` on CLI/IDE | Not installed by Setup. Only before any durable run/`HostAction`, after fresh canonical zero-match proof, is bounded native/manual fallback eligible. An owned scope with an unproven relay returns `ACTION_RELAY_REQUIRED`, preserves its owner, and stops; fresh proof that the exact tool is absent returns `HOST_CAPABILITY_BLOCKED`, preserves its owner, and stops. Neither owned state permits fallback. |
| GitHub network | HTTPS access to the GitHub-backed Codex repo marketplace source and pinned artifact hosts | Connectivity only; it is separate from Codex login, model entitlement/quota, and private OMC access. |
| MCP and other remote services | None required; `mcp_servers` is `[]` | Never copy host MCP configuration, servers, or credentials. Memos, Context7, and similar external services are deliberately excluded. |

## Advanced installation and maintainer acceptance on macOS

Installation is intentionally two-step and immutable. The Setup target is
`portable-full`; the Guardian Plugin itself already supplies `core`. Keep the
ordinary installation on the persistent Codex home that you explicitly choose;
do not use a temporary home and expect it to appear in normal tasks.

### Advanced hardened persistent installation

Set an existing persistent home, Codex executable, Git executable, an explicit
trusted Python executable, and the exact audited 40-hex commit. For
`portable-full`, `PYTHON_BIN` must be `>=3.11` with both `venv` and `ensurepip`.
Use a trusted current interpreter, an explicitly supplied interpreter, or only
these fixed macOS candidates: `/opt/homebrew/bin/python3.14` through
`python3.11`, then `/usr/local/bin/python3.14` through `python3.11`. Setup's
automatic selection uses only that fixed list and never an ambient-PATH Python
launcher. Check the candidate and capabilities before continuing.

Executable trust is explicit: bind `GUARDIAN_CODEX_BIN`, `GUARDIAN_GIT_BIN`,
and `PYTHON_BIN` to canonical absolute paths; do not resolve them through
ambient `PATH`. Native Codex is launched only after its OpenAI Developer ID
signature is verified. A Homebrew JS launcher is only a constrained static
locator for the versioned bundled native Codex binary; Setup never executes
Node or the JavaScript wrapper. The trust boundary is root and the current
UID; only the standard Homebrew prefix may have `admin group-write` when it has
no `extended ACL`. This does not defend against same-UID, root, or a trusted
admin actor.

Do not chmod or relocate a normal existing `CODEX_HOME` for Setup. A
current-UID-owned home may be `0700`, `0750`, or `0755` when group and other
users cannot write and no extended ACL is present. Setup protects the sensitive
`workflow-guardian` directory as `0700` and its receipt, journal, and sidecar
files as `0600`; isolated maintainer acceptance uses a separate `0700`
temporary home.

The on-disk receipt is `private-local`. Stdout is only a `public-redacted`
projection and excludes paths, hashes, device/inode values, environment data,
and raw logs. If setup fails, retain the receipt, journal, and any quarantine
entry; do not overwrite them while investigating.
The stdout contract is `codex-workflow-guardian/bootstrap-stdout/v1`: it keeps
only `schema`, `projection`, `generation`, `mode`, `platform`, `status`,
`canonical_source_verified`, `guardian_ref_verified`, `existing_receipt`,
`installation_status`, `capability_status`, `acceptance_level`, non-sensitive
`planned` count/components/action data, `conflict_summary` count/categories,
component name/status/version summaries, rollback action summaries, notes,
conflict name/reason pairs, failure, and recovery. It excludes
absolute/relative paths, `*_relative` fields, SHA/hash/digest/commit/selector
values, device/inode, ownership/provenance, environment values, credentials,
and raw logs.
`acceptance_level` is one of `preflight|installed|uninstalled|transaction-recovery`;
`capability_status` remains `configured-unverified` until a real fresh-task
receipt. `planned` contains only non-sensitive All in Luna Plugin/CLI/venv and
14-role count/action summaries, never paths or hashes.

Add the immutable Git-backed repository marketplace and capture its JSON. This
is a marketplace registration, not an install from the universal public Plugins
Directory. `GUARDIAN_CODEX_BIN` must be the direct path to a vendor-signed
native Mach-O Codex binary. Do not use `/opt/homebrew/bin/codex` or
`/usr/local/bin/codex` when either resolves to a Node/JavaScript wrapper; the
restricted PATH below intentionally excludes Homebrew. Resolve the Apple
Silicon or Intel vendor binary from a trusted installation, then verify it:

```sh
GUARDIAN_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"  # review this persistent target
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_REF="AUDITED_COMMIT_SHA"  # replace with the audited 40-hex commit
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

STOP. Open `$MARKETPLACE_ADD_JSON` and manually verify valid JSON, the expected
marketplace name/source, the exact audited ref, and no unexpected registration.
Do not continue if any field is missing or unexpected.

Only after that stop, add the Guardian Plugin and capture its JSON:

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

STOP. Open `$PLUGIN_ADD_JSON` and manually verify valid JSON, the exact
`pluginId` selector, expected `name` and `marketplaceName`, non-empty `version`
and `installedPath`, and `authPolicy`. Do not continue or guess a path when any
field is missing or unexpected. Only after this inspection copy the exact
returned path and verify the installed bundle:

```sh
PLUGIN_INSTALLED_PATH="/absolute/path/from-the-installedPath-field"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-the-installedPath-field" || exit 1
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

STOP. Confirm the bootstrap, lock, marketplace, and all 14 role assets are
present at the inspected Plugin root. Start a new Codex task and explicitly
invoke `$setup-codex-workflow-guardian` from this installed Plugin. Never derive
the apply path from a source checkout. Run the Python capability probe and
read-only check in their own step:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

STOP. Open `$CHECK_RECEIPT_JSON` and review the public-redacted stdout
projection. Confirm its non-sensitive `existing_receipt`: `absent` means this
first check has no private receipt to inspect, so review only the public logical
plan; `present` means inspect the unchanged fixed receipt pair from the prior
successful `--apply`. Continue only when provenance, schema, capabilities,
conflicts, and planned owned paths are understood; a nonzero `--check` status is
not permission to apply. Request `--apply` explicitly, in a new step, only
after this review:

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Open `$APPLY_RECEIPT_JSON` and the live receipt pair for a separate human
acceptance review. This file is the public-redacted stdout projection; inspect
the private-local receipt on disk for ownership. The marketplace and Plugin
commands write the selected home, not this repository checkout. This persistent
target is the user's normal workflow. If apply fails, retain the receipt,
transaction journal, and any quarantine entry; do not overwrite them.

Guardian, Reconciler, and Setup each require explicit invocation with
`allow_implicit_invocation: false`. The marketplace and Plugin commands write
the selected home, not this repository checkout.

### Ordinary uninstall

Start a fresh Codex task, explicitly invoke `$setup-codex-workflow-guardian`, and
ask it for a safe uninstall. The Skill discovers and validates the exact
installed Plugin root/ref itself, then removes only unchanged roles and the
managed All in Luna venv proven receipt-owned. It never treats a Plugin as
receipt-owned: every Plugin installed by Setup, including All in Luna, remains
`installed-but-unowned`, and legacy `owned_plugins` fields cannot authorize
removal. After preservation checks, first confirm the exact Guardian selector
identity and the preserved non-Guardian baseline, then remove only the exact
Guardian selector as a separate, explicitly confirmed native Codex operation.
This selector step does not require zero marketplace dependencies. All in Luna
remains installed-but-unowned until a separate explicit deletion confirmation.
After Guardian removal, capture a fresh post-Guardian-removal `codex plugin list
--json`. Only then remove the marketplace if that fresh list proves that no
remaining installed selector depends on `onebigmoon-codex-workflows`; if a
dependency remains, keep the marketplace registration and stop.
On any conflict or retained state you do not understand, stop and keep the
receipt, journal, and quarantine evidence.

`plugin marketplace list --json` alone proves only that the registration exists.

The following long shell sequence is for maintainers performing isolated
recovery or an audited cutover. It is not the ordinary user path.

### Maintainer/recovery uninstall (advanced)

Use the exact installed Plugin root returned by `codex plugin add --json`; never derive this path from a source checkout. Setup uninstall is receipt-owned only for unchanged roles and the managed venv. Plugin selectors are always preserved by Setup and require a separate, explicitly confirmed Codex removal after the preservation checks below. Keep the receipt, journal, and quarantine evidence if any step fails.

```sh
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_CODEX_HOME="/absolute/path/to/explicit-codex-home"
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
GUARDIAN_REF="AUDITED_COMMIT_SHA"
PLUGIN_INSTALLED_PATH="/absolute/path/from-plugin-add-installedPath"
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-plugin-add-installedPath" || exit 1
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
UNINSTALL_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.uninstall.XXXXXX")"
test -n "$UNINSTALL_RECEIPT_JSON" && test -f "$UNINSTALL_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --guardian-ref "$GUARDIAN_REF" >"$UNINSTALL_RECEIPT_JSON"; then
  echo "uninstall failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

STOP. Inspect `$UNINSTALL_RECEIPT_JSON` and the private receipt/journal. Stop on any conflict, partial result, changed ownership, or retained state you do not understand; the receipt-owned uninstall never authorizes removing unrelated content.

```sh
POST_UNINSTALL_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-uninstall-plugin-list.XXXXXX")"
test -n "$POST_UNINSTALL_PLUGIN_LIST_JSON" && test -f "$POST_UNINSTALL_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_UNINSTALL_PLUGIN_LIST_JSON"; then
  echo "post-uninstall plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

STOP. Require valid JSON, confirm the exact Guardian selector is still present,
and verify the preserved non-Guardian rows and source identities remain
unchanged before removing only the exact Guardian selector. This selector step
does not require zero marketplace dependencies.

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin remove codex-workflow-guardian@onebigmoon-codex-workflows; then
  echo "Guardian selector removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

STOP. Confirm the exact Guardian selector is gone. Do not continue if any other selector changed.

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

STOP. Require valid fresh JSON from after Guardian removal. Only then remove the
marketplace if the fresh Plugin list proves that no installed selector still
references `onebigmoon-codex-workflows`; if any dependency remains, keep the
marketplace registration and stop. The marketplace list below proves
registration only.

```sh
MARKETPLACE_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace-list.XXXXXX")"
test -n "$MARKETPLACE_LIST_JSON" && test -f "$MARKETPLACE_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace list --json >"$MARKETPLACE_LIST_JSON"; then
  echo "marketplace list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

STOP. Remove this marketplace only after the fresh post-removal Plugin list
proves that no installed Plugin or retained dependency still uses
`onebigmoon-codex-workflows`; otherwise keep the registration and stop.

```sh
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace remove onebigmoon-codex-workflows; then
  echo "marketplace removal failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

STOP. Confirm only the exact marketplace registration was removed. On any failure, retain `$UNINSTALL_RECEIPT_JSON`, the private receipt, transaction journal, and quarantine entry.

### Maintainer isolated acceptance

Use a fresh temporary home only to verify a release. This is separate from the
ordinary installation above and does not automatically change a user's normal
Codex task. The same explicit Python boundary and fixed 3.11–3.14 candidate
list applies. Install the exact Plugin first and derive `SETUP_SKILL_DIR` only
from the Plugin root in its successful `codex plugin add --json` `installedPath`.
A maintainer checkout is permitted only for read-only validation when it is a
clean tracked checkout at the exact audited commit with complete file/hash
verification, canonical root, root/current-UID ownership, no world-writable or
unauthorized group-writable mode, and no extended ACL. Bootstrap, doctor, and
postflight must come from that same trusted root. Ordinary Setup and canonical
`--apply` must bind to the installed marketplace Plugin's bootstrap, lock,
marketplace, and 14 role assets:

```sh
GUARDIAN_CODEX_HOME="$(/usr/bin/mktemp -d "/tmp/codex-workflow-guardian.XXXXXX")"
test -n "$GUARDIAN_CODEX_HOME" && test -d "$GUARDIAN_CODEX_HOME" || exit 1
GUARDIAN_CODEX_HOME="$(cd "$GUARDIAN_CODEX_HOME" && pwd -P)" # canonical macOS path
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_REF="AUDITED_COMMIT_SHA"  # exact audited 40-hex commit
MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_ADD_JSON" && test -f "$MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$GUARDIAN_REF" --json >"$MARKETPLACE_ADD_JSON"; then
  echo "marketplace add failed; stop" >&2
  exit 1
fi
```

STOP. Inspect `$MARKETPLACE_ADD_JSON` and validate its JSON, source, name, and
immutable ref by hand before running the next block.

```sh
PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$PLUGIN_ADD_JSON" && test -f "$PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$PLUGIN_ADD_JSON"; then
  echo "Guardian plugin add failed; stop" >&2
  exit 1
fi
```

STOP. Inspect `$PLUGIN_ADD_JSON` and validate the schema, exact `pluginId`
selector, expected `name` and `marketplaceName`, non-empty `version` and
`installedPath`, and `authPolicy` by hand. Then copy that path and validate
every required asset before deriving the Setup path:

```sh
PLUGIN_INSTALLED_PATH="/absolute/path/from-the-installedPath-field"
test "$PLUGIN_INSTALLED_PATH" != "/absolute/path/from-the-installedPath-field" || exit 1
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test -f "$PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

STOP. Confirm the bootstrap, lock, marketplace, and all 14 role assets at the
inspected root. Run the explicit Python capability probe and `--check`, retaining
the public-redacted stdout projection for review and inspecting the private-local
receipt separately:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

STOP. Inspect `$CHECK_RECEIPT_JSON` as the public-redacted stdout projection and
confirm `existing_receipt`: if `absent`, review only the public logical plan; if
`present`, review the unchanged fixed receipt pair from the prior successful
`--apply`. Review all conflicts, provenance, capabilities, and ownership before
applying. The next block is the separate explicit apply step:

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Inspect `$APPLY_RECEIPT_JSON` and the live receipt pair before declaring the
isolated acceptance complete.

The marketplace and Plugin commands intentionally write the selected
`GUARDIAN_CODEX_HOME`; they do not modify this repository checkout. Start a new
Codex task after installation so it can pick up the newly installed Plugin.
Guardian, Reconciler, and Setup each require explicit invocation with
`allow_implicit_invocation: false`.

`--guardian-ref` must be an exact 40-hex commit. `--check` does not directly
write managed content, but it runs validated Codex/Python probes and external
programs may maintain their own state; release acceptance therefore compares a
fresh isolated home tree, mtimes, and hashes before and after. It exits nonzero
when changes, conflicts, or required capabilities are missing. Run `--apply`
only after reviewing that receipt; it writes only owned, receipt-backed paths.
The CLI exposes only `--check`, `--apply`, and `--uninstall`. `--uninstall`
removes only unchanged role files and the managed venv proven owned by a
matching receipt; it never removes a Plugin selector. Every Setup-added Plugin
is installed-but-unowned, including All in Luna, and legacy `owned_plugins`
entries are ignored. Pre-existing or modified managed content is preserved and
reported as a conflict. The private receipt exposes the managed All in
Luna executable under the explicit target's `venvs/allinluna/bin/allinluna` when
Python `>=3.11` is available. Ponytail hooks are never enabled or trusted
automatically: review them and enable them explicitly if desired.

Start a fresh Codex task after Setup and require role, model, and reasoning
receipts before treating any OMC-compatible lane as configured. A configured
template is not proof of model access, Headroom routing, hook activation, or
durable All in Luna completion.

To switch an immutable ref, treat the same-named marketplace registration as
part of that ref. The current Codex CLI has no `--ref` option for
`plugin marketplace upgrade`, so first capture a valid full list from
`plugin list --json` and derive a preserved baseline: every installed Plugin
row (`pluginId`, installed state, and source identity) is part of the
preservation check. No selector is excluded as receipt-owned because Codex CLI
0.146.0 supplies no creator/changed proof. Each destructive Plugin remove step
below has its own preceding JSON proof and human stop. This cutover does
not authorize removing any other Plugin. During the empty registration window,
retained Plugins may be temporarily undiscoverable; do not treat that expected
window as proof of deletion. After the marketplace is re-added, run
`plugin list --json` and compare it with the baseline. Stop on invalid JSON, any
missing/non-installed baseline row, or any unexpected source change.

Before the cutover, capture/archive the live old
`workflow-guardian/bootstrap-receipt.json` and
`workflow-guardian/bootstrap-receipt.sha256` as audit evidence, and retain the
old Plugin root and its derived Setup Skill path. A successful old Setup `--uninstall`
consumes/removes that live receipt pair; it is not available for reuse after
uninstall. Do not manually restore the archived pair. Setup uninstall preserves
every Plugin, including All in Luna; remove a Plugin only through the separate
manual Codex operation after the baseline check. Set the new Setup path only after the new `plugin add
--json` succeeds and its `installedPath` passes schema inspection; keep both
old and new paths available until the new `--check` and `--apply` succeed. The
forward cutover below is manual and has no automatic rollback. Set
`PYTHON_BIN` to a trusted current/explicit interpreter or fixed macOS candidate;
never use an ambient-PATH launcher:

```sh
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
GUARDIAN_CODEX_HOME="/absolute/path/to/explicit-codex-home"
GUARDIAN_CODEX_BIN="/absolute/path/to/trusted/native-codex"
test "$GUARDIAN_CODEX_BIN" != "/absolute/path/to/trusted/native-codex" || exit 1
test -x "$GUARDIAN_CODEX_BIN" || exit 1
CODEX_HOST_ARCH="$(/usr/bin/uname -m)"
case "$CODEX_HOST_ARCH" in
  arm64|x86_64) /usr/bin/file "$GUARDIAN_CODEX_BIN" | /usr/bin/grep -Eq "Mach-O.*$CODEX_HOST_ARCH" || exit 1 ;;
  *) exit 1 ;;
esac
/usr/bin/codesign --verify --strict --requirements '=anchor apple generic and identifier "codex" and certificate leaf[subject.OU] = "2DC432GLL2"' "$GUARDIAN_CODEX_BIN" || exit 1
GUARDIAN_GIT_BIN="/usr/bin/git"
GUARDIAN_SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PLUGIN_BASELINE_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin-baseline.XXXXXX")"
test -n "$PLUGIN_BASELINE_JSON" && test -f "$PLUGIN_BASELINE_JSON" || exit 1
OLD_PLUGIN_INSTALLED_PATH="/absolute/path/from-old-plugin-add-installedPath"
OLD_SETUP_SKILL_DIR="$OLD_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
OLD_GUARDIAN_REF="OLD_AUDITED_COMMIT_SHA"  # exact 40-hex old release
NEW_GUARDIAN_REF="NEW_AUDITED_COMMIT_SHA"  # exact 40-hex new release
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json > "$PLUGIN_BASELINE_JSON"
```

STOP. Inspect the complete baseline JSON and the old receipt. Keep every
installed Plugin row, including All in Luna, in the preserved baseline because
no Plugin creator/changed proof is available. Archive the old receipt pair
before continuing.

```sh
"$PYTHON_BIN" -I -B "$OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF"
```

STOP. Review the old uninstall receipt and confirm only receipt-owned role files
and venv state was removed; all Plugins must remain installed. Do not continue
on a conflict or partial receipt.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

STOP. Confirm the exact Guardian selector is still present and the preserved
baseline has not changed. Then remove only the exact Guardian selector; zero
marketplace dependency is not a prerequisite for this selector step.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

STOP. Confirm the Guardian selector removal result, then capture a fresh
post-removal Plugin list. Only then remove the marketplace if that fresh list
proves that no installed selector still references
`onebigmoon-codex-workflows`; keep the marketplace and stop if any dependency
remains.

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

STOP. The fresh post-removal Plugin list above is the dependency proof; the
marketplace list below proves registration only. Only then remove the
marketplace, and only if its exact registration and schema are valid.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace list --json
```

STOP. Only continue if the exact marketplace registration is present and the
JSON schema is valid.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

STOP. Confirm the registration was removed; do not remove any other marketplace.

```sh
MARKETPLACE_READD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$MARKETPLACE_READD_JSON" && test -f "$MARKETPLACE_READD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$NEW_GUARDIAN_REF" --json >"$MARKETPLACE_READD_JSON"; then
  echo "new marketplace add failed; stop" >&2
  exit 1
fi
```

STOP. Inspect `$MARKETPLACE_READD_JSON` and validate its schema, canonical
source/name, and exact new ref before discovery resumes.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

STOP. Compare this list with the preserved baseline. Every preserved row,
including All in Luna, must remain installed with the expected source; no
Plugin selector may disappear as a result of Setup uninstall.

```sh
NEW_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$NEW_PLUGIN_ADD_JSON" && test -f "$NEW_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$NEW_PLUGIN_ADD_JSON"; then
  echo "new Guardian plugin add failed; stop" >&2
  exit 1
fi
```

STOP. Inspect `$NEW_PLUGIN_ADD_JSON`; require valid JSON, the expected
`pluginId`, `name`, `marketplaceName`, `version`, `authPolicy`, and a non-empty
`installedPath`. Only then copy the path and verify the new installed bundle:

```sh
NEW_PLUGIN_INSTALLED_PATH="/absolute/path/from-new-plugin-add-installedPath"
NEW_SETUP_SKILL_DIR="$NEW_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$NEW_PLUGIN_INSTALLED_PATH" != "/absolute/path/from-new-plugin-add-installedPath" || exit 1
test -f "$NEW_PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$NEW_PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$NEW_SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$NEW_SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

STOP. Confirm the new bootstrap, lock, marketplace, and all 14 role assets.
Run `--check` in a separate step and review its receipt:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

STOP. Review `$CHECK_RECEIPT_JSON` and explicitly authorize apply only after the
receipt proves the intended provenance, capabilities, conflicts, and ownership:

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "new Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Review `$APPLY_RECEIPT_JSON` and the live receipt pair before declaring the
cutover complete. Keep both old and new paths as audit evidence until then.

### Manual rollback after a failed cutover

Run the following rollback snippets only after the forward cutover fails; they
are not a continuation of a successful forward run. Use the same explicit
variables above. The archived old receipt is audit evidence only and must not
be restored manually. Every destructive rollback operation requires the
preceding JSON inspection and a human stop.

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

STOP. If the new Guardian selector is present and the new receipt is available,
confirm that only new receipt-owned role files and venv state are eligible for
Setup uninstall. No Plugin selector is eligible; inspect it separately.

```sh
"$PYTHON_BIN" -I -B "$NEW_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" \
  --guardian-ref "$NEW_GUARDIAN_REF"
```

STOP. Review the new uninstall receipt; stop on conflict, partial failure, or
unexpected ownership. It must not remove a Plugin selector. Then inspect the
Plugin list again:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

STOP. Confirm the preserved baseline is intact. Only if the exact Guardian
selector is still present, remove only the exact Guardian selector; zero
marketplace dependency is not a prerequisite for this selector step:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin remove \
  codex-workflow-guardian@onebigmoon-codex-workflows
```

STOP. Confirm the Guardian selector removal result, then capture a fresh
post-removal Plugin list. Only then remove the marketplace if that fresh list
proves that no installed selector still references `onebigmoon-codex-workflows`;
keep the marketplace and stop if any dependency remains. The marketplace list
below proves registration only:

```sh
POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.post-guardian-remove-plugin-list.XXXXXX")"
test -n "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" && test -f "$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin list --json >"$POST_GUARDIAN_REMOVE_PLUGIN_LIST_JSON"; then
  echo "post-Guardian-removal plugin list failed; retain receipt, journal, and quarantine" >&2
  exit 1
fi
```

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace list --json
```

STOP. Only if the exact registration is present and the schema is valid, remove
that registration:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin marketplace remove \
  onebigmoon-codex-workflows
```

STOP. Confirm the registration was removed. Re-add the old immutable marketplace
and capture its JSON:

```sh
RESTORED_MARKETPLACE_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.marketplace.XXXXXX")"
test -n "$RESTORED_MARKETPLACE_ADD_JSON" && test -f "$RESTORED_MARKETPLACE_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" \
  plugin marketplace add OneBigMoon/codex-subagent-reconciler \
  --ref "$OLD_GUARDIAN_REF" --json >"$RESTORED_MARKETPLACE_ADD_JSON"; then
  echo "old marketplace re-add failed; stop" >&2
  exit 1
fi
```

STOP. Inspect `$RESTORED_MARKETPLACE_ADD_JSON` and validate its schema, canonical
source/name, and exact old ref. Then compare the preserved baseline:

```sh
/usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin list --json
```

STOP. Confirm every preserved non-Guardian row, including pre-existing unowned
All in Luna, remains installed with the expected source. Only then re-add the
Guardian Plugin and capture its new JSON:

```sh
RESTORED_PLUGIN_ADD_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.plugin.XXXXXX")"
test -n "$RESTORED_PLUGIN_ADD_JSON" && test -f "$RESTORED_PLUGIN_ADD_JSON" || exit 1
if ! /usr/bin/env PATH="$GUARDIAN_SAFE_PATH" CODEX_HOME="$GUARDIAN_CODEX_HOME" "$GUARDIAN_CODEX_BIN" plugin add \
  codex-workflow-guardian@onebigmoon-codex-workflows --json >"$RESTORED_PLUGIN_ADD_JSON"; then
  echo "old Guardian plugin re-add failed; stop" >&2
  exit 1
fi
```

STOP. Inspect `$RESTORED_PLUGIN_ADD_JSON`; require valid JSON, the expected
`pluginId`, `name`, `marketplaceName`, `version`, `authPolicy`, and a non-empty
`installedPath`. Never reuse the pre-cutover `OLD_PLUGIN_INSTALLED_PATH`. Copy
this new `installedPath`, derive the restored Setup path, and validate the
restored bundle:

```sh
RESTORED_OLD_PLUGIN_INSTALLED_PATH="/absolute/path/from-restored-old-plugin-add-installedPath"
RESTORED_OLD_SETUP_SKILL_DIR="$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test "$RESTORED_OLD_PLUGIN_INSTALLED_PATH" != "/absolute/path/from-restored-old-plugin-add-installedPath" || exit 1
test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/.codex-plugin/plugin.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/.agents/plugins/marketplace.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/codex-workflow-guardian/SKILL.md" && \
  test -f "$RESTORED_OLD_PLUGIN_INSTALLED_PATH/skills/reconcile-codex-subagents/SKILL.md" && \
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" || exit 1
for role in \
  code-reviewer coder debugger document-specialist executor explore luna-coder \
  luna-worker security-reviewer sol-lead spark-verifier test-engineer tracer verifier; do
  test -f "$RESTORED_OLD_SETUP_SKILL_DIR/assets/agents/$role.toml" || exit 1
done
```

STOP. Confirm the restored bootstrap, lock, marketplace, and all 14 role assets.
Run the restored old Setup `--check`, review it, then request apply separately:

```sh
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
CHECK_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.check.XXXXXX")"
test -n "$CHECK_RECEIPT_JSON" && test -f "$CHECK_RECEIPT_JSON" || exit 1
if "$PYTHON_BIN" -I -B "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --check --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF" >"$CHECK_RECEIPT_JSON"; then
  CHECK_STATUS=0
else
  CHECK_STATUS=$?
fi
test "$CHECK_STATUS" -eq 0 -o "$CHECK_STATUS" -eq 1 || exit "$CHECK_STATUS"
```

STOP. Review `$CHECK_RECEIPT_JSON`; a nonzero check is not permission to apply.

```sh
APPLY_RECEIPT_JSON="$(/usr/bin/mktemp "/tmp/codex-workflow-guardian.apply.XXXXXX")"
test -n "$APPLY_RECEIPT_JSON" && test -f "$APPLY_RECEIPT_JSON" || exit 1
if ! "$PYTHON_BIN" -I -B "$RESTORED_OLD_SETUP_SKILL_DIR/scripts/bootstrap_macos.py" \
  --apply --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF" >"$APPLY_RECEIPT_JSON"; then
  echo "restored old Guardian apply failed; retain and review the receipt" >&2
  exit 1
fi
```

Review the fresh restored receipt pair. Do not restore the archived pair, remove
other Plugins, or manually delete paths outside receipt-owned role/venv state.

### Standalone three-Skills alternative (exact audited commit only)

If the Plugin is not desired, `$skill-installer` or the direct installer script
must install Guardian, Reconciler, and Setup at the same exact
`AUDITED_COMMIT_SHA`; never use `main`:

```sh
SKILL_INSTALLER="/path/to/install-skill-from-github.py"
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
"$PYTHON_BIN" -I -B "$SKILL_INSTALLER" \
  --repo OneBigMoon/codex-subagent-reconciler \
  --ref AUDITED_COMMIT_SHA \
  --path skills/codex-workflow-guardian skills/reconcile-codex-subagents \
         skills/setup-codex-workflow-guardian
```

This fallback is `skill-only`, not `core`: it writes the three Skill directories
under the installer-selected target but does not create the installed Guardian
Plugin or its git marketplace provenance. Do not claim `core` or `portable-full`
from this path. The standalone Setup Skill is a documentation and diagnostic
entry only; its `--apply` fails closed as `configured-unverified` until a
canonical installed Guardian Plugin and matching git provenance exist.

## Guardian provenance

Portable-full provenance requires all of the following: the canonical GitHub
repository `https://github.com/OneBigMoon/codex-subagent-reconciler`; a user-
audited exact 40-hex `GUARDIAN_REF`; one unique installed Guardian returned by
the GitHub-backed Codex marketplace source; the canonical git marketplace URL
and checkout root; and a root Git `HEAD` equal to that ref. The lock and
marketplace files must match exactly for canonical repositories, paths, refs,
and artifact hashes. The checked-in local marketplace source is
development/testing-only. This is a Git-backed repo marketplace source, not a
claim of publication in the universal public Plugins Directory. If the
resolver cannot prove canonical provenance, portable-full `--apply` fails
closed as `configured-unverified`. A bootstrap receipt records operation and
ownership, not publisher identity, authorization, or signing authority.

## Read-only reconciler

Use `$reconcile-codex-subagents` only with synthetic or sanitized JSON. The
doctor reads one explicit regular file. Postflight reads one explicit before
file and one explicit after file.
The v1 schema remains available for diagnosis. Strict v2 operational checks
use a caller-generated `scope` and stable `record_key`; these are correlation
keys, never actionable lifecycle targets. `v1` keeps diagnosis compatibility:
an unknown event is reported as `unsupported-event` and exits `2`; strict `v2`
rejects unknown event kinds during schema validation.

```sh
RECONCILER_SKILL_DIR="/path/to/reconcile-codex-subagents"  # directory containing this SKILL.md
PYTHON_BIN="/absolute/path/to/trusted/python3.11"
test -x "$PYTHON_BIN" && /usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/doctor.py" \
  --input snapshot.json --json
"$PYTHON_BIN" -I -B "$RECONCILER_SKILL_DIR/scripts/postflight.py" \
  --before before-v2.json --after after-v2.json \
  --record-key record-1 --json
```

Set `RECONCILER_SKILL_DIR` only from the `installedPath` returned by a
successful `codex plugin add --json` for the installed Guardian Plugin. Verify
the plugin manifest, immutable ref, canonical marketplace provenance, and the
complete lock/role asset set; an unverified checkout is not an ordinary
operational root. Maintainer read-only validation requires a clean tracked
checkout at the exact audited commit, complete file/hash and ownership/
permission/ACL checks, and doctor/postflight from that same root. These
commands do not assume a repository current working directory.

Exit `0` means consistent/pass, `1` means mismatch/failure, and `2` means
invalid, ambiguous, inconclusive, or unsupported. A conclusive mismatching
before snapshot may be a repair baseline, but the after snapshot must be
consistent and the selected record must make a real, forward sequence transition.
Each resolved record must also correspond (`running` with declared
`live_state: running` and UI `active`, `terminal` with declared
`live_state: terminal` and UI `done`); opposite rows cannot cancel in aggregate. v2 additionally checks both
derived live counts and independently derived visible UI-row counts, so an
aggregate cannot conceal a stale selected row. Unchanged terminal records,
scope or key drift, old epochs, newer `not_found`, unknown fields, duplicate
keys, control characters, unsafe identifiers, and unsafe files fail closed.

Reports redact identifiers, but redaction is not anonymization. Snapshot output
and every v1/v2 JSON or text report carry `trust:
unauthenticated-consistency-evidence`; these are never authorization or
protocol truth. Public classifications use snapshot-claim labels. The bundled scripts are
stdlib-only, bounded, non-networked, non-polling, and non-writing. They never
close, interrupt, archive, delete, repair, deploy, or otherwise mutate a
lifecycle target.

## Acceptance levels

Keep these profiles and evidence sets separate:

- **`core`:** Guardian Plugin plus Guardian, Reconciler, and Setup Skills.
- **`portable-full`:** `core` plus the pinned All in Luna Plugin/CLI wheel and
  14 roles; Python `>=3.11`. Setup does not install Ponytail and neither enables
  nor trusts hooks; both require separate machine-integration authorization and
  live acceptance.
- **`machine-integration`:** Desktop/CLI skew, Headroom presence/version/health/
  routing, a user-supplied Node runtime when lifecycle hooks need it, hooks,
  and local startup; separate authorization and live acceptance. Ponytail
  Skills themselves do not require Node, and hooks are never auto-trusted.
- **`account-dependent`:** Codex login, model entitlement/quota, and private OMC
  Skills; fresh receipt required. GitHub network access is a separate
  external prerequisite and does not prove these account capabilities.

Source/tests (repository and validator results), installed state (resolver,
lock, hashes, paths, receipt), callable capability (Skills and role templates),
and behavior/live (fresh task plus browser/log/DB/device evidence) are distinct.
Reproducible Git delivery at the audited commit is also distinct. Success means
functional public equivalence, not byte-for-byte copying of a local dirty
RC2/OMC/Headroom state.

Do not use a bare `ready` label. Report `installation-ready` only when the
intended files and matching receipt are present, `capability-configured-unverified`
when configuration is present but no fresh callable-capability receipt exists,
and `acceptance-installed` only when the declared installation acceptance has
passed. None of these states proves current live behavior.

Receipt and journal SHA sidecars detect corruption or schema drift only; they do
not authenticate records or prevent a same-UID actor who can rewrite
`CODEX_HOME` from rewriting both record and sidecar. Setup accepts only fixed
managed path/plugin allowlists and re-observes live state around ownership-
sensitive operations.

## Acceptance and security

Run two unit suites, three Skill quick validators, one Plugin validator, three
script `--help` commands, JSON/TOML/README constant checks, the no-bytecode
check, and `git diff --check`. The official local validators may not be
available on a networkless CI runner without global skill paths; in that case
they are an explicit release gate, not a CI claim. These checks establish
source/test evidence only; they do not claim deployment or current live
availability. See
[`skills/reconcile-codex-subagents/SKILL.md`](skills/reconcile-codex-subagents/SKILL.md)
for exact schemas and [`SECURITY.md`](SECURITY.md) for the security boundary.
