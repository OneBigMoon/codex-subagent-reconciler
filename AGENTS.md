# Local project rules

- Keep this project stdlib-only and deterministic.
- Work only with explicit synthetic or sanitized snapshot JSON.
- Never add network, polling, filesystem scans, SQLite writes, app.asar edits,
  lifecycle calls, or production integration code.
- Preserve identifier redaction unless a command explicitly provides its
  show-identifiers option.
- Run `python -m unittest discover -s skills/reconcile-codex-subagents/tests -v`
  and both script `--help` commands before handoff.
