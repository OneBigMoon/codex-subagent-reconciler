import importlib.util
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOCTOR_PATH = SCRIPTS / "doctor.py"
POSTFLIGHT_PATH = SCRIPTS / "postflight.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


doctor = load_module("doctor", DOCTOR_PATH)
postflight = load_module("reconcile_postflight", POSTFLIGHT_PATH)


def event(seq, kind, source="synthetic"):
    return {"seq": seq, "kind": kind, "source": source}


def agent(
    identifier="synthetic-agent-1",
    name="synthetic-worker",
    ui_state="active",
    live_state="running",
    events=None,
    exact_target=None,
):
    return {
        "id": identifier,
        "name": name,
        "ui_state": ui_state,
        "live_state": live_state,
        "events": list(events or []),
        "exact_target": exact_target or identifier,
    }


def snapshot(agents, active=None, done=None):
    if active is None:
        active = sum(doctor._lifecycle(item)["state"] == "running" for item in agents)
    if done is None:
        done = sum(doctor._lifecycle(item)["state"] == "terminal" for item in agents)
    return {"schema": doctor.SCHEMA, "ui_counts": {"active": active, "done": done}, "agents": agents}


class ReconcilerTests(unittest.TestCase):
    def test_healthy_snapshot_is_consistent(self):
        report = doctor.analyze_snapshot(
            snapshot([agent(events=[event(1, "task_started")])], active=1, done=0)
        )
        self.assertEqual(report["status"], "consistent")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["records"][0]["classification"], "confirmed-running")

    def test_terminal_and_interrupted_counts_mismatch(self):
        agents = [
            agent(f"terminal-{index}", events=[event(1, "task_complete")], ui_state="done", live_state="done")
            for index in range(31)
        ] + [
            agent(
                f"interrupted-{index}",
                events=[event(1, "turn_interrupted")],
                ui_state="done",
                live_state="done",
            )
            for index in range(9)
        ]
        report = doctor.analyze_snapshot(snapshot(agents, active=23, done=16))
        self.assertEqual(report["derived_counts"], {"active": 0, "done": 40})
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["exit_code"], 1)

    def test_legitimate_running_and_newer_resume(self):
        item = agent(events=[event(1, "task_complete"), event(2, "resumed")])
        report = doctor.analyze_snapshot(snapshot([item], active=1, done=0))
        self.assertEqual(report["records"][0]["classification"], "confirmed-running")

    def test_newer_terminal_wins_old_active(self):
        item = agent(events=[event(9, "task_started"), event(10, "turn_completed")], ui_state="done", live_state="done")
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["records"][0]["classification"], "confirmed-terminal")

    def test_compaction_out_of_order_and_duplicate_delivery(self):
        item = agent(
            events=[
                event(5, "task_complete"),
                event(2, "task_started"),
                event(3, "context_compacted"),
                event(5, "task_complete"),
            ],
            ui_state="done",
            live_state="done",
        )
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["status"], "consistent")
        self.assertEqual(report["records"][0]["classification"], "confirmed-terminal")

    def test_conflicting_same_sequence_is_ambiguous(self):
        item = agent(events=[event(4, "task_started"), event(4, "task_complete")])
        report = doctor.analyze_snapshot(snapshot([item], active=1, done=0))
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["records"][0]["classification"], "ambiguous")

    def test_unknown_event_and_not_found_are_inconclusive(self):
        for kind in ("made_up", "not_found"):
            item = agent(events=[event(1, kind)])
            report = doctor.analyze_snapshot(snapshot([item], active=0, done=0))
            self.assertEqual(report["exit_code"], 2)
            self.assertEqual(report["records"][0]["classification"], "ambiguous")

    def test_orphan_without_lifecycle_is_inconclusive(self):
        item = agent(events=[])
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=0))
        self.assertEqual(report["records"][0]["classification"], "orphan-unknown")
        self.assertEqual(report["exit_code"], 2)

    def test_terminal_active_ui_is_stale_candidate(self):
        item = agent(events=[event(1, "task_complete")], ui_state="active", live_state="done")
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["records"][0]["classification"], "stale-ui-candidate")

    def test_single_terminal_signal_with_running_live_state_is_inconclusive(self):
        item = agent(events=[event(1, "task_complete")], ui_state="done", live_state="running")
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["records"][0]["classification"], "ambiguous")

    def test_terminal_events_from_two_sources_corroborate(self):
        item = agent(
            events=[event(1, "task_complete", "source-a"), event(2, "turn_completed", "source-b")],
            ui_state="done",
            live_state="running",
        )
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["records"][0]["classification"], "confirmed-terminal")

    def test_default_output_redacts_identifiers(self):
        item = agent("secret-id", "private-name", events=[event(1, "running")])
        public = doctor.public_report(doctor.analyze_snapshot(snapshot([item])), False)
        encoded = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("secret-id", encoded)
        self.assertNotIn("private-name", encoded)
        self.assertIn("agent-1", encoded)
        shown = doctor.public_report(doctor.analyze_snapshot(snapshot([item])), True)
        self.assertIn("secret-id", json.dumps(shown))

    def test_stdout_stderr_and_json_never_leak_canary_by_default(self):
        canary = "/Users/private/uuid-123/token-secret/name-canary"
        payload = snapshot([agent(canary, canary, events=[event(1, "running", canary)])], active=1, done=0)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            json.dump(payload, handle)
            path = pathlib.Path(handle.name)
        try:
            for flags in ([], ["--json"]):
                result = subprocess.run(
                    [sys.executable, str(DOCTOR_PATH), "--input", str(path), *flags],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertNotIn(canary, result.stdout + result.stderr)
            bad_path = pathlib.Path(tempfile.gettempdir()) / canary.replace("/", "_")
            result = subprocess.run(
                [sys.executable, str(DOCTOR_PATH), "--input", str(bad_path), "--json"],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertNotIn(canary, result.stdout + result.stderr)
        finally:
            path.unlink()

    def test_live_state_is_normalized_and_not_arbitrary(self):
        item = agent(events=[event(1, "running")], live_state="ACTIVE")
        normalized = doctor.validate_snapshot(snapshot([item], active=1, done=0))
        self.assertEqual(normalized["agents"][0]["live_state"], "running")
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(snapshot([agent(events=[event(1, "running")], live_state="secret-token")], active=1, done=0))

    def test_duplicate_id_is_invalid(self):
        items = [agent("same", events=[event(1, "running")]), agent("same", events=[event(1, "running")])]
        with self.assertRaises(doctor.SnapshotError) as caught:
            doctor.validate_snapshot(snapshot(items, active=2, done=0))
        self.assertEqual(caught.exception.code, "duplicate-id")

    def test_duplicate_exact_target_is_invalid(self):
        items = [
            agent("one", events=[event(1, "running")], exact_target="same-target"),
            agent("two", events=[event(1, "running")], exact_target="same-target"),
        ]
        with self.assertRaises(doctor.SnapshotError) as caught:
            doctor.validate_snapshot(snapshot(items, active=2, done=0))
        self.assertEqual(caught.exception.code, "duplicate-target")

    def test_duplicate_json_keys_nested_are_rejected(self):
        raw = (
            '{"schema":"codex-subagent-snapshot/v1","ui_counts":{"active":0,"done":0},'
            '"agents":[],"agents":[]}'
        )
        original = sys.stdin
        try:
            sys.stdin = type("Input", (), {"read": lambda self, size=-1: raw})()
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot("-")
            self.assertEqual(caught.exception.code, "duplicate-key")
        finally:
            sys.stdin = original

        nested = (
            '{"schema":"codex-subagent-snapshot/v1","ui_counts":{"active":0,"done":0,"active":0},'
            '"agents":[]}'
        )
        try:
            sys.stdin = type("Input", (), {"read": lambda self, size=-1: nested})()
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot("-")
            self.assertEqual(caught.exception.code, "duplicate-key")
        finally:
            sys.stdin = original

    def test_postflight_duplicate_target_cannot_false_pass(self):
        before = snapshot([agent("one", events=[event(1, "running")])], active=1, done=0)
        after_agents = [
            agent("one", events=[event(1, "running"), event(2, "task_complete")], ui_state="done", live_state="done", exact_target="same"),
            agent("two", events=[event(1, "task_complete")], ui_state="done", live_state="done", exact_target="same"),
        ]
        result = postflight.reconcile(before, snapshot(after_agents, active=0, done=2), targets=["same"])
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("duplicate-target", {item["code"] for item in result["issues"]})

    def test_postflight_terminal_to_running_regression(self):
        before_item = agent(events=[event(2, "task_complete")], ui_state="done", live_state="done")
        after_item = agent(events=[event(2, "task_complete"), event(3, "running")])
        result = postflight.reconcile(
            snapshot([before_item], active=0, done=1), snapshot([after_item], active=1, done=0), targets=[before_item["id"]]
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("terminal-to-running-without-newer-start", {item["code"] for item in result["issues"]})

    def test_postflight_resume_terminal_running_masked_aggregate_regression(self):
        before_item = agent(events=[event(1, "task_complete")], ui_state="done", live_state="done")
        after_item = agent(
            events=[
                event(1, "task_complete"),
                event(2, "resumed"),
                event(3, "task_complete"),
                event(4, "running"),
            ],
            ui_state="active",
            live_state="running",
        )
        result = postflight.reconcile(
            snapshot([before_item], active=0, done=1), snapshot([after_item], active=1, done=0), targets=[before_item["id"]]
        )
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("terminal-to-running-without-newer-start", codes)
        self.assertIn("unexpected-active-increase", codes)
        self.assertEqual(result["status"], "fail")

    def test_postflight_new_orphan_running_and_duplicate(self):
        before = snapshot([agent(events=[event(1, "running")])], active=1, done=0)
        after = snapshot(
            [
                agent(events=[event(1, "running")]),
                agent("new", events=[event(1, "running")]),
            ],
            active=2,
            done=0,
        )
        result = postflight.reconcile(before, after, targets=["synthetic-agent-1"])
        self.assertEqual(result["status"], "fail")
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("new-unknown-running", codes)
        self.assertIn("unexpected-active-increase", codes)

        duplicate = [agent("same", events=[event(1, "running")]), agent("same", events=[event(1, "running")])]
        invalid = doctor.SnapshotError("duplicate-id")
        result = postflight.reconcile(before, None, targets=["synthetic-agent-1"], before_error=None, after_error=invalid)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("duplicate-id", {item["code"] for item in result["issues"]})

    def test_postflight_passes_exact_target_after_newer_closure(self):
        before_item = agent(events=[event(1, "running")])
        after_item = agent(
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
        )
        result = postflight.reconcile(
            snapshot([before_item], active=1, done=0),
            snapshot([after_item], active=0, done=1),
            targets=[before_item["id"]],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["issues"], [])

    def test_postflight_accepts_explicit_exact_target_alias(self):
        before_item = agent(events=[event(1, "running")], exact_target="target-token")
        after_item = agent(
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
            exact_target="target-token",
        )
        result = postflight.reconcile(
            snapshot([before_item], active=1, done=0),
            snapshot([after_item], active=0, done=1),
            targets=["target-token"],
        )
        self.assertEqual(result["status"], "pass")

    def test_postflight_latest_resume_is_a_valid_reactivation(self):
        before_item = agent("resume-agent", events=[event(1, "task_complete")], ui_state="done", live_state="done")
        after_item = agent(
            "resume-agent",
            events=[event(1, "task_complete"), event(2, "resumed")],
            ui_state="active",
            live_state="running",
        )
        closed = agent("closed-agent", events=[event(1, "task_complete")], ui_state="done", live_state="done")
        result = postflight.reconcile(
            snapshot([before_item, closed], active=0, done=2),
            snapshot([after_item, closed], active=1, done=1),
            targets=[closed["id"]],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["issues"], [])

    def test_postflight_compaction_after_latest_resume_does_not_mask_valid_reactivation(self):
        before_item = agent("resume-agent", events=[event(1, "task_complete")], ui_state="done", live_state="done")
        after_item = agent(
            "resume-agent",
            events=[event(1, "task_complete"), event(2, "resumed"), event(3, "context_compacted")],
            ui_state="active",
            live_state="running",
        )
        closed = agent("closed-agent", events=[event(1, "task_complete")], ui_state="done", live_state="done")
        result = postflight.reconcile(
            snapshot([before_item, closed], active=0, done=2),
            snapshot([after_item, closed], active=1, done=1),
            targets=[closed["id"]],
        )
        self.assertEqual(result["status"], "pass")

    def test_postflight_requires_target_directly(self):
        item = agent(events=[event(1, "running")])
        result = postflight.reconcile(snapshot([item], active=1, done=0), snapshot([item], active=1, done=0), targets=[])
        self.assertEqual(result, {
            "schema": "codex-subagent-postflight/v1",
            "status": "inconclusive",
            "exit_code": 2,
            "issues": [{"code": "target-required"}],
        })

    def test_postflight_cli_requires_target(self):
        result = subprocess.run(
            [sys.executable, str(POSTFLIGHT_PATH), "--before", "before.json", "--after", "after.json"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--target", result.stderr)

    def test_cross_namespace_identifier_collision_is_invalid(self):
        items = [
            agent("agent-one", events=[event(1, "running")], exact_target="target-one"),
            agent("agent-two", events=[event(1, "running")], exact_target="agent-one"),
        ]
        with self.assertRaises(doctor.SnapshotError) as caught:
            doctor.validate_snapshot(snapshot(items, active=2, done=0))
        self.assertEqual(caught.exception.code, "identifier-collision")

    def test_postflight_cross_namespace_collision_never_resolves_id_first(self):
        before = snapshot(
            [agent("agent-one", events=[event(1, "task_complete")], ui_state="done", live_state="done")],
            active=0,
            done=1,
        )
        after = snapshot(
            [
                agent("agent-one", events=[event(1, "task_complete")], ui_state="done", live_state="done", exact_target="target-one"),
                agent("agent-two", events=[event(1, "task_complete")], ui_state="done", live_state="done", exact_target="agent-one"),
            ],
            active=0,
            done=2,
        )
        result = postflight.reconcile(before, after, targets=["agent-one"])
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("identifier-collision", {item["code"] for item in result["issues"]})

    def test_postflight_target_must_be_terminal_or_stale_terminal(self):
        item = agent(events=[event(1, "running")])
        result = postflight.reconcile(snapshot([item], active=1, done=0), snapshot([item], active=1, done=0), targets=[item["id"]])
        self.assertEqual(result["status"], "fail")
        self.assertIn("target-not-terminal", {entry["code"] for entry in result["issues"]})

    def test_input_bytes_and_mtime_are_unchanged(self):
        payload = snapshot([agent(events=[event(1, "running")])], active=1, done=0)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            json.dump(payload, handle)
            path = pathlib.Path(handle.name)
        try:
            before_bytes = path.read_bytes()
            before_sha = hashlib.sha256(before_bytes).hexdigest()
            before_stat = path.stat()
            loaded = doctor.read_snapshot(str(path))
            self.assertEqual(loaded["schema"], doctor.SCHEMA)
            after_bytes = path.read_bytes()
            self.assertEqual(after_bytes, before_bytes)
            self.assertEqual(hashlib.sha256(after_bytes).hexdigest(), before_sha)
            after_stat = path.stat()
            self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)
            self.assertEqual(before_stat.st_size, after_stat.st_size)
        finally:
            path.unlink()

    def test_paths_must_be_regular_non_symlink_and_input_is_bounded(self):
        payload = json.dumps(snapshot([agent(events=[event(1, "running")])], active=1, done=0))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            regular = root / "snapshot.json"
            regular.write_text(payload, encoding="utf-8")
            directory_path = root / "directory"
            directory_path.mkdir()
            fifo = root / "fifo"
            os.mkfifo(fifo)
            symlink = root / "symlink"
            symlink.symlink_to(regular)
            for candidate in (directory_path, fifo, symlink):
                with self.assertRaises(doctor.SnapshotError):
                    doctor.read_snapshot(str(candidate))

            oversized = root / "oversized.json"
            oversized.write_bytes(b"{" + b" " * doctor.MAX_INPUT_BYTES)
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot(str(oversized))
            self.assertEqual(caught.exception.code, "input-too-large")

    def test_bounds_for_counts_events_strings_and_sequence(self):
        too_many_agents = [agent(f"id-{index}", events=[event(1, "running")]) for index in range(doctor.MAX_AGENTS + 1)]
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(snapshot(too_many_agents, active=doctor.MAX_AGENTS + 1, done=0))
        too_many_events = agent(events=[event(index, "running") for index in range(doctor.MAX_EVENTS_PER_AGENT + 1)])
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(snapshot([too_many_events], active=1, done=0))
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(snapshot([agent(events=[event(doctor.MAX_SEQUENCE + 1, "running")])], active=1, done=0))
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(snapshot([agent(name="x" * (doctor.MAX_STRING_LENGTH + 1), events=[event(1, "running")])], active=1, done=0))
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(snapshot([agent(events=[event(1, "running", "x" * (doctor.MAX_SOURCE_LENGTH + 1))])], active=1, done=0))

    def test_scripts_have_no_network_or_write_functions(self):
        source = (DOCTOR_PATH.read_text() + POSTFLIGHT_PATH.read_text()).lower()
        for forbidden in ("urllib", "requests", "socket", "sqlite", "subprocess", "os.remove", "os.unlink", 'os.open(.*os.o_wron'):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIn("stream.read", source)

    def test_source_tree_has_no_generated_artifacts(self):
        generated = [
            path
            for path in ROOT.rglob("*")
            if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}
        ]
        self.assertEqual(generated, [])

    def test_help_is_available(self):
        for script in (DOCTOR_PATH, POSTFLIGHT_PATH):
            result = subprocess.run(
                [sys.executable, str(script), "--help"], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("usage:", result.stdout.lower())

    def test_skill_metadata_and_openai_config(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\nname: reconcile-codex-subagents\ndescription:"))
        self.assertIn("假运行", skill)
        self.assertIn("compact/resume", skill)
        config = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            config.splitlines(),
            [
                "interface:",
                '  display_name: "Codex 子智能体状态校准器"',
                '  short_description: "校准脱敏快照中的 Codex 子智能体生命周期和界面计数"',
                '  default_prompt: "Use $reconcile-codex-subagents to inspect a synthetic snapshot, reconcile lifecycle evidence, fail safely on ambiguity, and run postflight acceptance."',
                "policy:",
                "  allow_implicit_invocation: true",
            ],
        )
        short_description = config.splitlines()[2].split('"', 2)[1]
        self.assertGreaterEqual(len(short_description), 25)
        self.assertLessEqual(len(short_description), 64)


if __name__ == "__main__":
    unittest.main()
