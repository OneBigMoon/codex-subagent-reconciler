---
name: reconcile-codex-subagents
description: "Reconcile sanitized Codex subagent snapshots when ghost/stale agents, running-count mismatches, or compact/resume inconsistencies appear; 发现 ghost、stale、running 计数不一致、假运行、子智能体计数异常或 compact/resume 不一致时校准脱敏快照。"
---

# Codex Subagent Reconciler

Use this skill for a read-only, evidence-backed reconciliation of Codex
subagent UI counts and lifecycle events. Triggers include ghost, stale, running
count mismatch, compact/resume inconsistency, 假运行, 子智能体计数异常, and
compact/resume 不一致.

## Workflow

1. Use official callable tools to build one sanitized snapshot with the
   `codex-subagent-snapshot/v1` schema. Never ingest raw logs, databases,
   screenshots, credentials, or guessed identifiers.
2. Run `scripts/doctor.py` against that explicit snapshot. It classifies each
   record and compares derived active/done counts with the UI counts.
3. If the user explicitly authorizes one exact, confirmed ghost and an official
   lifecycle API exists, perform exactly one closure through that API. Never
   script a closure, write a local database, or mutate the app bundle.
4. Capture a fresh sanitized after snapshot and run `scripts/postflight.py`.
5. Re-read the live UI with official tools and report evidence. A single
   terminal signal is not enough.

The scripts are read-only checks, not a permanent upstream fix. Self-check /
prevention means detecting recurrence and failing acceptance; it does not stop
Codex Desktop from creating ghosts.

## Explicit bans

Do not write SQLite, patch `app.asar`, guess IDs, archive or delete agents,
batch-interrupt agents, upload raw data, poll in a loop, or claim a terminal
state from one signal. Keep all fixtures synthetic or sanitized.
