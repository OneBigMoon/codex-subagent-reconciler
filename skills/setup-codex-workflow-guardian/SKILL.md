---
name: setup-codex-workflow-guardian
description: >-
  Explicitly invoked macOS-only setup and dependency verification for Codex
  Workflow Guardian. Runs a non-writing managed-content preflight by default
  and applies only exact, receipt-backed files and artifacts when requested.
---

# Setup Codex Workflow Guardian

Use this Skill only after explicit invocation. In a new Codex task,
`$setup-codex-workflow-guardian` resolves the bundled
`scripts/bootstrap_macos.py`. The explicit `PLUGIN_INSTALLED_PATH` and derived
`SETUP_SKILL_DIR` variables shown below come from the exact installed Plugin
root returned by a successful `codex plugin add --json`. The ordinary path
accepts only that `installedPath` after verifying `.codex-plugin/plugin.json`,
the immutable audited ref, canonical marketplace provenance, and a complete
lock/role asset set; an unverified source checkout or alternate root is never
an installation or `--apply` path. A maintainer may use a clean tracked checkout
at the exact audited commit for read-only validation only, after complete
file/hash verification, canonical-root and root/current-UID ownership checks,
non-world-writable and non-unauthorized-group-writable modes, and no extended
ACL; its bootstrap, doctor, and postflight must all come from that same trusted root.
Canonical `--apply` must bind to the installed GitHub-backed Codex
marketplace Plugin's bootstrap, lock, and role assets. The CLI's `installedPath`
is the Plugin root; derive
`SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"`
from it and validate the exact files before use. Stop on command failure,
invalid JSON, schema mismatch, wrong selector, or missing `installedPath`. The
script derives the repository and Skill roots from its own installed path.

## Ordinary quick path

After the user installs the marketplace Plugin with an already trusted normal
`codex` command, the new-task Setup invocation owns discovery. It first verifies
`E`, the Skill's own installed path, as the canonical bundled root and confirms
that same root is clean, immutable, and has an exact-ref match. It then reads
`codex plugin list --json` and identifies the unique installed Guardian selector
`P`, verifies `P` as canonical marketplace source, and checks that `P` and `E`
share the same exact-ref before validating the Plugin JSON and deriving
`SETUP_SKILL_DIR` from `E`. The user must not parse Plugin JSON, copy a source
checkout, or hand-fill an installed path. CLI `0.146.0` `plugin list --json`
rows may omit `installedPath`; never glob/scan, guess, or rerun `plugin add`
to recover paths. For the preflight probes, Setup discovers Python only from the fixed
macOS candidate list and statically resolves a supported versioned native Codex
binary when the normal command is a JavaScript wrapper; it never executes the
wrapper's Node process or inherits ambient PATH resolution. If the exact
selector, root, wrapper target, Python candidate, or capability evidence cannot
be verified, report `configured-unverified` and stop before any managed write.
The normal `codex` command is the pre-Setup trust boundary; the direct-native
Mach-O, sanitized-PATH, and explicit executable binding below are hardened
advanced/maintainer checks rather than ordinary user inputs.

## Safe default

The default is `--check`: Guardian bootstrap itself does not directly write
managed content, but it executes validated Codex/Python probes and an external
program may maintain its own state. `--check` does not create or update a
private receipt; its stdout is only the public logical plan/projection. Any
private receipt it observes must come from a previous successful `--apply` and
is checked, not replaced. Use a fresh isolated `--codex-home` and compare its
tree, mtimes, and hashes before/after release acceptance. `--apply` is the only
install/write path; `--uninstall` removes only role files and the managed venv
proven to have been created by a matching receipt and whose hashes are
unchanged. Every automatically added Plugin, including All in Luna, is
`installed-but-unowned`: it is never entered in receipt ownership, automatic
rollback, or `--uninstall` deletion because Codex 0.146 supplies no creator
proof. A legacy receipt's `owned_plugins` entries are invalidated, never
inherited, and never acted on. On any failure, retain the private receipt,
transaction journal, and any journaled quarantine entry for recovery; do not
retry by overwriting those artifacts. The Reconciler itself remains strictly
read-only.

### Ordinary-user uninstall / 普通用户安全卸载

Start a new Codex task and explicitly invoke `$setup-codex-workflow-guardian`.
The Skill owns discovery: use the trusted `codex plugin list --json` result to
find the unique Guardian selector `P`, identify the Skill's own installed root
`E`, and confirm `P` and `E` have consistent exact-ref evidence before reading
the existing private receipt to recover its exact old 40-hex `guardian_ref`; do
not ask the user to copy a
cache path, parse Plugin JSON, or guess a ref. Invoke the old installed
Guardian bootstrap with those discovered values and run its receipt-owned
`--uninstall`. If the selector, installed path, receipt, or old ref cannot be
verified, report `configured-unverified`/`conflict` and stop.

This ordinary path removes only unchanged receipt-owned role files and the
managed All in Luna venv. It preserves every installed-but-unowned Plugin,
including All in Luna and Ponytail; `--uninstall` must never issue a Plugin
remove for them. Removing the Guardian Plugin or its Git marketplace is a
separate, explicit user decision: only after confirmation may Codex guide the
user through the normal `codex plugin remove`/marketplace commands, followed by
a fresh read-only check. The Setup bootstrap never removes its own Plugin or
marketplace as part of receipt-owned uninstall.

中文：普通用户只需在新任务中显式调用本 Skill；由 Skill 自动发现唯一的
Guardian selector `P` 与同包的 `plugin` 安装根 `E`，并确认 `P/E` exact-ref 一致，再读取
receipt 中的旧 `guardian_ref`，然后执行旧版本的
receipt-owned 卸载。找不到唯一安装路径、receipt 或精确 ref 就停止并报告未验证。
卸载只删除未被修改的角色文件和 All in Luna 虚拟环境；所有
installed-but-unowned Plugin 都保留。Guardian Plugin/marketplace 必须单独确认，
再由 Codex 引导执行正常 Plugin 移除，不能由本卸载流程顺手删除。

Keep the Python boundary explicit. The ordinary quick path lets Setup select a
trusted executable from the documented fixed macOS candidates in order:
`/opt/homebrew/bin/python3.14` through `/opt/homebrew/bin/python3.11`, then
`/usr/local/bin/python3.14` through `/usr/local/bin/python3.11`. `portable-full --apply` requires
Python `>=3.11` with both `venv` and `ensurepip`; never use an ambient PATH
Python launcher. A manually supplied `PYTHON_BIN` is an advanced override and
must be a trusted absolute executable.

Bind every executable to a canonical absolute path. Native Codex is launched
only after the OpenAI Developer ID signature requirement passes. A Homebrew JS
launcher is accepted only as a constrained static locator for its versioned
bundled native Codex; Setup never executes Node or the JavaScript wrapper.
The ordinary quick path does not ask the user to fill `GUARDIAN_CODEX_BIN`:
Setup resolves a supported versioned native target statically behind the
pre-Setup normal-`codex` trust boundary. Do not assume that
`/opt/homebrew/bin/codex` or `/usr/local/bin/codex` is native: either may be a
Node/JavaScript wrapper, and Setup never executes that wrapper. For the hardened
path, `GUARDIAN_CODEX_BIN` must point directly to the vendor-signed native
Mach-O binary. Resolve the Apple Silicon or Intel vendor binary from a trusted
installation, then verify its current architecture and the exact Codex designated requirement
(`identifier "codex"`, Apple generic anchor, TeamIdentifier `2DC432GLL2`) with
`/usr/bin/file` and `/usr/bin/codesign` before invoking it. A universal Mach-O
is valid on both architectures; a thin binary must match the host.
Git is bound to `/usr/bin/git`, Python is bound to `PYTHON_BIN`, and subprocesses
do not inherit ambient PATH resolution. The trust boundary is root and the
current UID; only the standard Homebrew prefix may use `admin group-write` when
there is no `extended ACL`. This does not defend against same-UID, root, or a
trusted-admin actor.

Do not chmod or relocate a normal existing `CODEX_HOME` just for Setup. A
current-UID-owned home may be `0700`, `0750`, or `0755` when group and other
users cannot write and no extended ACL is present. Setup protects the sensitive
`workflow-guardian` directory as `0700` and its receipt, journal, and sidecar
files as `0600`; maintainer isolated acceptance uses a separate `0700` temporary
home.

```sh
GUARDIAN_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
test -n "$GUARDIAN_CODEX_HOME" && test -d "$GUARDIAN_CODEX_HOME" || exit 1
GUARDIAN_CODEX_HOME="$(cd "$GUARDIAN_CODEX_HOME" && pwd -P)"
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
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in \
    /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 \
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
    /usr/local/bin/python3.14 /usr/local/bin/python3.13 \
    /usr/local/bin/python3.12 /usr/local/bin/python3.11; do
    if [ -x "$candidate" ]; then PYTHON_BIN="$candidate"; break; fi
  done
fi
case "$PYTHON_BIN" in
  /*) ;;
  *) exit 1 ;;
esac
test -n "$PYTHON_BIN" && test -x "$PYTHON_BIN" || exit 1
/usr/bin/file "$PYTHON_BIN" | /usr/bin/grep -q 'Mach-O' || exit 1
# Set GUARDIAN_REF to the exact 40-hex ref used to install this plugin.
GUARDIAN_REF="${GUARDIAN_REF:?set the exact 40-hex ref used to install this plugin}"
# Set only after inspecting the successful plugin add --json installedPath.
PLUGIN_INSTALLED_PATH="/absolute/path/from-installedPath"
SETUP_SKILL_DIR="$PLUGIN_INSTALLED_PATH/skills/setup-codex-workflow-guardian"
test -f "$SETUP_SKILL_DIR/SKILL.md" && \
  test -f "$SETUP_SKILL_DIR/scripts/bootstrap_macos.py" && \
  test -f "$PLUGIN_INSTALLED_PATH/workflow-dependencies.lock.json" && \
  test -d "$SETUP_SKILL_DIR/assets/agents" || exit 1
"$PYTHON_BIN" -I --version
"$PYTHON_BIN" -I -c 'import ensurepip, venv; print("guardian-python-capable")'
```

Run the non-writing preflight and retain its public-redacted stdout projection
for review. Confirm its non-sensitive `existing_receipt` field: `absent` means
this is a first check and there is no private receipt to inspect; `present`
means inspect only the fixed, receipt-owned pair from the prior successful
`--apply`, without replacing it. The first check reviews the public logical
plan only:

```sh
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

Only after reviewing that public logical plan and, when `existing_receipt` is
`present`, the unchanged receipt pair, then explicitly authorizing the write,
run the separate apply step:

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

`--guardian-ref` must be the exact 40-hexadecimal Guardian release commit used
by the GitHub-backed Codex marketplace source. `--codex-home` and `--codex-bin`
keep every target explicit; `--check` exits nonzero when changes, conflicts,
schema/provenance mismatches, or required capabilities are missing. The JSON
contracts for marketplace/plugin probes are tested at Codex CLI `0.146.0`; a
different or unknown schema fails closed. The direct command below is
maintainer/recovery-only; ordinary users should use the new-task flow above.
For receipt-owned removal, use the old release's installed Plugin root/derived
Setup Skill first and pass the old exact ref as well:

```sh
"$PYTHON_BIN" -I -B "/path/to/old/setup-codex-workflow-guardian/scripts/bootstrap_macos.py" \
  --uninstall --codex-home "$GUARDIAN_CODEX_HOME" --codex-bin "$GUARDIAN_CODEX_BIN" \
  --git-bin "$GUARDIAN_GIT_BIN" --allinluna-python "$PYTHON_BIN" \
  --guardian-ref "$OLD_GUARDIAN_REF"
```

The private-local receipt never includes command output, environment values,
config contents, credentials, or raw logs in its public projection. Its stdout
is only a public-redacted projection: it excludes machine paths, hashes,
device/inode values, environment data, and raw logs. The private receipt and
transaction journal retain the ownership guards and rollback evidence needed
for recovery; they are not an authorization or publisher proof. The stdout
schema is `codex-workflow-guardian/bootstrap-stdout/v1` and retains only schema,
projection, generation, mode, platform, status, `canonical_source_verified`,
`guardian_ref_verified`, `existing_receipt`, `installation_status`,
`capability_status`, `acceptance_level`, non-sensitive `planned`
count/components/action, `conflict_summary` count/categories, component
name/status/version summaries, rollback action summaries, notes, conflict
name/reason pairs, failure, and recovery. `acceptance_level` is `preflight`,
`installed`, `uninstalled`, or `transaction-recovery`; `acceptance_level` is
one of the four public values `preflight|installed|uninstalled|transaction-recovery`;
`capability_status` remains
`configured-unverified` until a fresh task receipt. `planned` names only All in
Luna Plugin/CLI/venv and 14 roles with count/action, never paths or hashes. It
excludes absolute/relative paths, `*_relative` fields,
SHA/hash/digest/commit/selector, device/inode, ownership/provenance,
credentials, environment values, and raw logs.
Receipt and journal SHA sidecars detect corruption or schema drift, but do not
authenticate a record or make it tamper-proof against a same-UID actor who can
rewrite `$CODEX_HOME`. Setup accepts state only through fixed managed
path/plugin allowlists and re-observes live state before and after
ownership-sensitive operations.

## What is managed

- The bootstrap checks that the Codex marketplace resolver exposes one unique
  installed Guardian. Portable-full requires the canonical GitHub repository
  and marketplace git URL/root, with the checkout Git HEAD equal to the user-
  audited 40-hex ref. A local marketplace source is for development/testing
  only; portable-full `--apply` fails closed as `configured-unverified` when
  the resolver cannot prove that provenance. It does not copy duplicate Skills
  or manifests into `$CODEX_HOME`; install all three Skills through the
  installed Plugin before claiming portable-full. A standalone three-Skill
  install is `skill-only`, does not reach `core`, and does not create the
  installed Guardian Plugin or its git marketplace provenance. This Setup Skill
  remains a documentation/diagnostic entry and `--apply` fails closed until
  canonical installed Guardian Plugin git provenance exists. This Git-backed
  repo marketplace source is not the universal public Plugins Directory.
- All in Luna is added with an argv-only Codex plugin command from the pinned
  marketplace entry. Existing exact immutable installs are a no-op; a foreign
  marketplace/source is a conflict. Whether an exact Plugin was pre-existing
  or added by this Setup run, it is always `installed-but-unowned`: the receipt
  may record observed state, but it never grants Plugin ownership, rollback
  authority, or `--uninstall` deletion authority. Plugin removal requires a
  separate explicit user decision through the normal Codex command. Ponytail is
  detection-only here: it is never auto-added, enabled, or trusted, and any
  install/enable action needs separate authorization.
- Fourteen sanitized OMC-compatible role-separation templates are offered
  under `$CODEX_HOME/agents/`: `code-reviewer`, `coder`, `debugger`,
  `document-specialist`, `executor`, `explore`, `luna-coder`, `luna-worker`,
  `security-reviewer`, `sol-lead`, `spark-verifier`, `test-engineer`,
  `tracer`, and `verifier`. Existing same-name agent TOMLs are never edited;
  model entitlement remains `configured-unverified` until a fresh Codex task
  returns an actual role/model receipt.
- All in Luna is portable-full required. When Python >=3.11 is available, `--apply` creates
  `$CODEX_HOME/venvs/allinluna` and installs only the locked, dependency-free
  wheel after SHA-256 verification, with `--no-deps` and no mutable resolver.
  Python 3.9 remains sufficient for this setup Skill but cannot run the
  managed All in Luna venv; apply fails closed when no Python >=3.11 is
  available. The private receipt exposes the managed executable path; its
  public-redacted stdout projection does not.
- Headroom is presence detection/manual machine integration only. The package
  is `headroom-ai` `0.34.0` from the canonical
  `https://github.com/headroomlabs-ai/headroom`, Apache-2.0, Python `>=3.10`.
  Artifact hashes and transitive dependency hashes are not locked. `0.34.0` is
  an expected or observed target, not a bootstrap-verified version or readiness
  claim. The bootstrap never installs, initializes, launches, routes, trusts,
  or edits configuration for Headroom.
- Node is an external prerequisite only for the `machine-integration` profile
  when a lifecycle hook needs it. Ponytail Skills themselves do not require
  Node. Setup does not install, enable, or trust lifecycle hooks; a separately
  authorized machine-integration check must validate any user-supplied `node`.

No MCP server is required (`mcp_servers` is `[]`). Setup never copies host MCP
configuration, server registrations, or credentials.

No shell startup files, provider routing, launchd jobs, global AGENTS files,
hooks, MCP configuration, or existing agent files are changed. Hooks from
Ponytail are never auto-trusted.

## Immutable dependency boundary

`workflow-dependencies.lock.json` pins the All in Luna Git subdirectory,
its deterministic installed-tree digest, and the separately detected Ponytail
Git commit, package licenses, and both published All in Luna artifact hashes.
It records Headroom's package metadata and explicitly leaves its artifact and
transitive dependency hashes unlocked; that component is detect-only and
requires separate authorization.
The marketplace entries and lock must match exactly for repository, path, ref,
and artifact hashes. The bootstrap does not resolve mutable branches or
transitive dependencies.

The Guardian repository commit is an external immutable installer pin because
a commit cannot contain its own SHA. The Codex marketplace resolver must show
one installed Guardian from the canonical GitHub marketplace URL/root and its
checkout Git HEAD must equal the exact user-audited ref. The receipt records
operation and ownership; it is not publisher identity, authorization, or proof
that a release was signed. A missing or mutable ref is not portable-full parity.

GitHub HTTPS/network reachability is a separate external prerequisite for the
repo marketplace and pinned artifact hosts. The manifest's `Network`
capability describes this explicit `--apply` boundary: only an explicitly
authorized apply may download the pinned All in Luna wheel. It does not grant
implicit network permission or prove account-dependent Codex login, model
entitlement, quota, or private OMC access. Those capabilities remain
`configured-unverified` until a fresh Codex task supplies the required receipt.

The bundled role templates provide role separation and routing compatibility;
they are not a provenance claim that OMC 4.15.1 or an unavailable private OMC
Skill has been installed. Start a new Codex task after setup and require role,
model, and reasoning receipts before treating a lane as configured.

## Permissions and failure handling

`--check` reads source files, lock data, executable versions, and marketplace
inspection output, and runs validated Codex/Python probes (plus a presence
check for `headroom`). Guardian bootstrap itself does not directly write
managed content during this check, but those external programs may maintain
their own state; compare a fresh isolated home tree, mtimes, and hashes for the
release gate. `--apply` writes only the isolated home paths above and a managed venv. Every
write is preflighted before the first mutation. On a partial failure the
receipt lists rollback actions; rollback removes only content created by this
run when its expected hash still matches. Modified content is preserved and
reported. Uninstall refuses the whole operation if an owned path changed and
never removes a pre-existing marketplace/plugin copy.
