# Local project rules

- Keep the project macOS-only and deterministic. Normal in-scope implementation,
  configuration, and test writes requested by the user may use native host
  tools. The bundled reconciler scripts are stdlib-only, bounded, and strictly
  read-only on explicit sanitized JSON files. They never perform network,
  polling, filesystem scans, database access, or lifecycle writes. The separate
  Setup Skill is explicit: `--check` does not directly write managed content,
  but it runs validated Codex/Python probes whose external programs may maintain
  their own state; `--apply` may perform the pinned artifact and `$CODEX_HOME`
  writes described in its receipt; `--uninstall` is receipt-owned and
  hash-guarded. The Reconciler itself remains strictly read-only.
- Preserve v1 diagnosis compatibility while using strict v2 for operational
  before/after checks (`scope` plus `record_key`). Every schema level rejects
  unknown fields and duplicate JSON keys; output is always identifier-redacted.
- For the Reconciler, do not add network clients, polling, filesystem scans,
  database access or writes, app bundle edits, lifecycle calls, publish/deploy
  paths, or external integrations. Setup remains limited to the pinned network,
  explicit target, receipt-owned writes, and permission boundaries above.
- Keep `allow_implicit_invocation: false` for all three Skills. Only the exact
  `$codex-workflow-guardian`, `$reconcile-codex-subagents`, and
  `$setup-codex-workflow-guardian` calls are Skill authorization. Natural-language
  requests such as “全自动处理” are usage suggestions and do not grant Setup,
  release, deploy, or lifecycle permission. The supporting reconciler is strictly
  diagnostic/read-only and contains no lifecycle closure,
  archive, delete, or repair instructions.
- Use Ponytail `lite` only as the implementation author's simplification mode;
  never waive validation, security, or evidence requirements.
- Run before handoff:

  ```sh
  CODEX_SKILL_CREATOR_ROOT="${CODEX_SKILL_CREATOR_ROOT:-${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator}"
  CODEX_PLUGIN_CREATOR_ROOT="${CODEX_PLUGIN_CREATOR_ROOT:-${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator}"
  PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s skills/reconcile-codex-subagents/tests -v
  PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s skills/setup-codex-workflow-guardian/tests -v
  python3 -B "$CODEX_SKILL_CREATOR_ROOT/scripts/quick_validate.py" skills/codex-workflow-guardian
  python3 -B "$CODEX_SKILL_CREATOR_ROOT/scripts/quick_validate.py" skills/reconcile-codex-subagents
  python3 -B "$CODEX_SKILL_CREATOR_ROOT/scripts/quick_validate.py" skills/setup-codex-workflow-guardian
  python3 -B "$CODEX_PLUGIN_CREATOR_ROOT/scripts/validate_plugin.py" .
  PYTHONDONTWRITEBYTECODE=1 python3 -B skills/reconcile-codex-subagents/scripts/doctor.py --help
  PYTHONDONTWRITEBYTECODE=1 python3 -B skills/reconcile-codex-subagents/scripts/postflight.py --help
  PYTHONDONTWRITEBYTECODE=1 python3 -B skills/setup-codex-workflow-guardian/scripts/bootstrap_macos.py --help
  test -z "$(find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -quit)"
  git diff --check
  # After exact staging, this also covers previously untracked release files.
  git diff --cached --check
  ```

- The validator set is intentionally explicit: two unit suites, three Skill
  quick validators (Guardian, Reconciler, Setup), one Plugin validator, three
  script `--help` commands, JSON/TOML/README constant checks, no-bytecode, and
  worktree and staged `git diff --check` gates. The official local validators
  may be unavailable on a networkless runner without the global skill paths;
  they remain a release gate, not a CI claim. Source/tests, installed state,
  capability, reproducible Git delivery, and current live/new-task acceptance
  remain separate claims.

- Before exact staging, run this same local-artifact gate used by CI. It applies
  `.gitignore` to tracked paths with `--no-index`, so `git add -f` cannot bypass
  the release check, and it self-tests legal source names and forbidden local
  Store/DB, receipt, journal, lock, staging, and quarantine names:

  ```sh
  PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
  import subprocess

  def is_ignored(path):
      result = subprocess.run(
          ["git", "check-ignore", "--no-index", "-q", "--", path],
          stdout=subprocess.DEVNULL,
          stderr=subprocess.PIPE,
          check=False,
      )
      if result.returncode not in (0, 1):
          raise SystemExit(result.stderr.decode("utf-8", "replace"))
      return result.returncode == 0

  allowed = {
      ".gitignore", "workflow-dependencies.lock.json", "config.lock.json",
      "docs/runtime.md", "docs/receipt-guide.md",
      "skills/setup-codex-workflow-guardian/scripts/bootstrap_macos.py",
  }
  denied = {
      "runtime.db", "runtime.db-shm", "runtime.db-wal",
      "nested/allinluna-runtime.db", "nested/allinluna-runtime.db-wal",
      "nested/fixture.db-shm", "workflow-guardian/bootstrap-receipt.json",
      "workflow-guardian/bootstrap-receipt.sha256",
      "workflow-guardian/bootstrap-journal.json", "workflow-guardian/bootstrap.lock",
      "runtime.receipt.json", "runtime.journal.json", "runtime.lock", "store.lock",
      "venvs/.guardian-venv-allinluna", "agents/.guardian-copy-abc",
      "guardian-install.json",
  }
  assert not {path for path in allowed if is_ignored(path)}
  assert {path for path in denied if not is_ignored(path)} == set()
  tracked = [
      raw.decode("utf-8")
      for raw in subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
      if raw
  ]
  assert not sorted(path for path in tracked if is_ignored(path))
  print(f"release artifact gate passed: {len(tracked)} tracked paths")
  PY
  ```

- Release pin cutover is a strict two-commit contract: source commit A may
  retain the bare `AUDITED_COMMIT_SHA` placeholder; only after A is pushed, its
  CI passes, and canonical A is fixed and accepted may all bare
  `AUDITED_COMMIT_SHA` occurrences in the four language READMEs be replaced
  with A in documentation-only commit B. B CI must reject bare placeholders
  and mismatched refs; any later source change must restore the placeholder and
  begin a new A/B audit cycle.

- Confirm validation did not modify tracked files. Stage, commit, or push only
  exact paths with explicit user authorization; this task's branch publication
  remains root-owned. Merge, tag, release, history rewrite, and user-config
  mutation remain forbidden without exact authorization.
