import ast
import hashlib
import io
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import ExitStack
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "setup-codex-workflow-guardian" / "scripts" / "bootstrap_macos.py"


def load_module():
    spec = importlib.util.spec_from_file_location("bootstrap_macos", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bootstrap = load_module()


_SAFE_PLUGIN_OPERATIONS = frozenset({"add", "list", "marketplace"})


def _plugin_remove_argv_calls(tree):
    """Return statically unsafe Codex plugin argv calls.

    The guard intentionally understands only literal string/list bindings and
    starred literal lists.  A known-safe plugin operation is allowed, while a
    dynamic or unknown operation after a literal ``plugin`` token is reported
    instead of being silently treated as non-removal.
    """
    unknown = object()
    bindings = {}

    def static_value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.List):
            return static_sequence(node)
        if isinstance(node, ast.Tuple):
            return static_sequence(node)
        if isinstance(node, ast.Name):
            return bindings.get(node.id, unknown)
        return unknown

    def static_sequence(node):
        if isinstance(node, ast.Name):
            value = bindings.get(node.id, unknown)
            return value if isinstance(value, list) else [unknown]
        if not isinstance(node, (ast.List, ast.Tuple)):
            return None
        values = []
        for item in node.elts:
            if isinstance(item, ast.Starred):
                expanded = static_sequence(item.value)
                values.extend(expanded if isinstance(expanded, list) else [unknown])
            else:
                values.append(static_value(item))
        return values

    # Resolve the small binding forms used by argv construction.  Unknown
    # values remain unknown; the call-site gate decides whether they matter.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = static_value(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = value

    matches = []
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_run_argv"
            and call.args
        ):
            continue
        argument = call.args[0]
        if not isinstance(argument, (ast.List, ast.Tuple, ast.Name)):
            continue
        tokens = static_sequence(argument)
        if not isinstance(tokens, list) or len(tokens) < 2:
            continue
        command = tokens[1:]
        if command[0] == "plugin":
            operation = command[1] if len(command) > 1 else unknown
            if operation not in _SAFE_PLUGIN_OPERATIONS:
                matches.append(call)
        elif command[0] is unknown and isinstance(argument, (ast.List, ast.Tuple)):
            # A literal argv with an unresolved starred expansion can become
            # a Plugin command; fail closed for that shape.  Unrelated calls
            # whose complete command starts with a known non-Plugin token pass.
            if any(isinstance(item, ast.Starred) for item in argument.elts):
                matches.append(call)
    return matches


class BootstrapV2Tests(unittest.TestCase):
    def setUp(self):
        # The suite uses portable shell/Python fixtures instead of signed
        # Darwin Mach-O binaries.  Keep those fixtures on the non-Darwin
        # compatibility lane; dedicated trust tests patch Darwin explicitly.
        self._platform_patcher = mock.patch.object(bootstrap.platform, "system", return_value="Linux")
        self._platform_patcher.start()
        # Use the account-scoped system temp root; production trust remains strict.
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.home = self.root / "codex-home"
        self.home.mkdir(mode=0o700)
        self.codex = self.root / "codex"
        self.git = pathlib.Path("/usr/bin/git") if pathlib.Path("/usr/bin/git").exists() else pathlib.Path(shutil.which("git") or "/usr/bin/git")
        self.guardian_root, self.guardian_ref, self.execution_root = self._make_guardian_checkout()
        self._write_codex()

    def tearDown(self):
        self.temp.cleanup()
        self._platform_patcher.stop()

    def _make_guardian_checkout(self):
        root = self.home / "marketplace" / "onebigmoon-codex-workflows"
        for relative in bootstrap.GUARDIAN_REQUIRED_PATHS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        subprocess.run([str(self.git), "init", "-q", str(root)], check=True)
        subprocess.run([str(self.git), "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run([str(self.git), "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run([str(self.git), "-C", str(root), "add", "."], check=True)
        subprocess.run([str(self.git), "-C", str(root), "commit", "-qm", "fixture"], check=True)
        ref = subprocess.check_output([str(self.git), "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run([str(self.git), "-C", str(root), "remote", "add", "origin", "https://github.com/OneBigMoon/codex-subagent-reconciler.git"], check=True)
        version = json.loads((ROOT / "workflow-dependencies.lock.json").read_text(encoding="utf-8"))["components"]["guardian_plugin"]["version"]
        execution = self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "codex-workflow-guardian" / version
        execution.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(self.git), "clone", "-q", str(root), str(execution)], check=True)
        subprocess.run([str(self.git), "-C", str(execution), "remote", "set-url", "origin", "https://github.com/OneBigMoon/codex-subagent-reconciler.git"], check=True)
        return root, ref, execution

    def _entry(self, name, source, installed=True, enabled=True):
        versions = {
            "codex-workflow-guardian": "0.2.0",
            "allinluna": "2.0.0rc3",
            "ponytail": "4.9.0",
        }
        value = {
            "pluginId": name + "@onebigmoon-codex-workflows",
            "name": name,
            "marketplaceName": "onebigmoon-codex-workflows",
            "version": versions[name] if installed else None,
            "installed": installed,
            "enabled": enabled,
            "source": source,
            "marketplaceSource": {"sourceType": "git", "source": "https://github.com/OneBigMoon/codex-subagent-reconciler"},
            "installPolicy": "AVAILABLE",
            "authPolicy": "ON_INSTALL",
        }
        if name in ("allinluna", "ponytail"):
            value["source"]["sha"] = {
                "allinluna": "723088a7c0d7342f077ad675c6ea72d7e3996536",
                "ponytail": "2ed6c52c9d7e5e56942508591085fd45dea277d3",
            }[name]
        return value

    def _write_codex(self, no_op_add=False, corrupt=False, output_noise="", omit_installed_path=False):
        script = textwrap.dedent(
            """
            #!/usr/bin/env python3
            import json, os, pathlib, sys
            home = pathlib.Path(os.environ["CODEX_HOME"])
            state_path = home / "plugin-state.json"
            state = json.loads(state_path.read_text()) if state_path.exists() else []
            root = pathlib.Path(%r)
            versions = {"codex-workflow-guardian": "0.2.0", "allinluna": "2.0.0rc3", "ponytail": "4.9.0"}
            allinluna_cache = home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3"
            def entry(name, installed=True, enabled=True):
                base = {"pluginId": name + "@onebigmoon-codex-workflows", "name": name, "marketplaceName": "onebigmoon-codex-workflows", "version": versions[name] if installed else None, "installed": installed, "enabled": enabled, "marketplaceSource": {"sourceType": "git", "source": "https://github.com/OneBigMoon/codex-subagent-reconciler"}, "installPolicy": "AVAILABLE", "authPolicy": "ON_INSTALL"}
                if name == "codex-workflow-guardian":
                    base["source"] = {"source": "local", "path": str(root)}
                    if not %r: base["installedPath"] = str(%r)
                elif name == "allinluna": base["source"] = {"source": "git-subdir", "url": "https://github.com/zenx0x/allinluna.git", "path": "./plugins/allinluna", "sha": "723088a7c0d7342f077ad675c6ea72d7e3996536"}
                else: base["source"] = {"source": "url", "url": "https://github.com/DietrichGebert/ponytail.git", "sha": "2ed6c52c9d7e5e56942508591085fd45dea277d3"}
                return base
            if %r: sys.stdout.write(%r)
            if "--version" in sys.argv:
                print("codex-cli 0.146.0"); raise SystemExit(0)
            if "plugin" in sys.argv and "add" in sys.argv:
                selector = next(item for item in sys.argv if item.endswith("@onebigmoon-codex-workflows")); name = selector.split("@", 1)[0]
                if not %r and name not in state: state.append(name); (allinluna_cache if name == "allinluna" else home / "plugin-cache" / name).mkdir(parents=True, exist_ok=True); state_path.write_text(json.dumps(state))
                print(json.dumps({"pluginId": selector, "name": name, "marketplaceName": "onebigmoon-codex-workflows", "version": versions[name], "installedPath": str(allinluna_cache if name == "allinluna" else home / "plugin-cache" / name), "authPolicy": "ON_INSTALL"})); raise SystemExit(0)
            if "plugin" in sys.argv and "marketplace" in sys.argv:
                print(json.dumps({"marketplaces": [{"name": "onebigmoon-codex-workflows", "root": str(root), "marketplaceSource": {"sourceType": "git", "source": "https://github.com/OneBigMoon/codex-subagent-reconciler"}}]})); raise SystemExit(0)
            if "plugin" in sys.argv and "list" in sys.argv:
                unrelated = {"pluginId": "other@other-marketplace", "name": "other", "marketplaceName": "other-marketplace", "version": None, "installed": False, "enabled": False, "source": {"source": "url", "url": "https://example.invalid/other.git"}, "installPolicy": "AVAILABLE", "authPolicy": "ON_INSTALL"}
                installed = [entry("codex-workflow-guardian")] + [entry(name) for name in state] + [unrelated]
                if %r: installed[0]["source"]["unknown"] = "spoof"
                available = [entry("allinluna", False, False), entry("ponytail", False, False), dict(unrelated)]
                print(json.dumps({"installed": installed if "--available" not in sys.argv else [], "available": [] if "--available" not in sys.argv else available })); raise SystemExit(0)
            raise SystemExit(1)
            """
            % (str(self.guardian_root), bool(omit_installed_path), str(self.execution_root), bool(output_noise), repr(output_noise), bool(no_op_add), bool(corrupt))
        )
        self.codex.write_text(script.lstrip(), encoding="utf-8")
        self.codex.chmod(0o700)

    def _run_on_darwin(self, *args, **kwargs):
        platform_calls = [0]

        def gate_then_linux():
            platform_calls[0] += 1
            return "Darwin" if platform_calls[0] <= 2 else "Linux"

        with mock.patch.object(bootstrap.platform, "system", side_effect=gate_then_linux):
            return bootstrap.run(*args, **kwargs)

    def _run(
        self,
        mode="check",
        allinluna_component=None,
        create_fake_venv=True,
        lock_mutator=None,
        script_path=None,
        load_lock_hook=None,
        preflight_hook=None,
        **kwargs,
    ):
        original_load_lock = bootstrap._load_lock

        def fixture_lock(repository_root=None, expected_source_proofs=None, expected_root_proof=None):
            def load():
                return original_load_lock(
                    repository_root,
                    expected_source_proofs=expected_source_proofs,
                    expected_root_proof=expected_root_proof,
                )

            loaded = load_lock_hook(repository_root, load) if load_lock_hook is not None else load()
            lock = json.loads(json.dumps(loaded))
            plugin_path = self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3"
            proof = bootstrap._content_tree_proof(plugin_path, self.home) if plugin_path.exists() else {
                "tree_sha256": hashlib.sha256(bootstrap.PLUGIN_TREE_ALGORITHM.encode("ascii") + b"\0").hexdigest(),
                "entries": 0,
                "directories": 0,
                "files": 0,
                "symlinks": 0,
                "file_bytes": 0,
            }
            component = lock["components"]["allinluna"]
            component["content_tree_sha256"] = proof["tree_sha256"]
            component["content_tree_entries"] = proof["entries"]
            component["content_tree_directories"] = proof["directories"]
            component["content_tree_files"] = proof["files"]
            component["content_tree_symlinks"] = proof["symlinks"]
            component["content_tree_file_bytes"] = proof["file_bytes"]
            if lock_mutator is not None:
                lock_mutator(lock)
            return lock

        default_component = {"name": "allinluna", "status": "present", "path_relative": "venvs/allinluna/bin/allinluna", "wheel_sha256": "0" * 64}
        if allinluna_component is None:
            patch = mock.patch.object(bootstrap, "_allinluna_component", return_value=default_component)
        elif callable(allinluna_component):
            patch = mock.patch.object(bootstrap, "_allinluna_component", side_effect=allinluna_component)
        else:
            patch = mock.patch.object(bootstrap, "_allinluna_component", return_value=allinluna_component)
        lock_patch = mock.patch.object(bootstrap, "_load_lock", side_effect=fixture_lock)
        original_preflight = bootstrap._preflight_targets

        def fixture_preflight(codex_home, repository_root=None):
            plans, conflicts = original_preflight(codex_home, repository_root)
            if preflight_hook is not None:
                preflight_hook(codex_home, repository_root)
            return plans, conflicts

        preflight_patch = mock.patch.object(bootstrap, "_preflight_targets", side_effect=fixture_preflight)
        script_patch = mock.patch.object(
            bootstrap,
            "SCRIPT_PATH",
            script_path or self.execution_root / "skills" / "setup-codex-workflow-guardian" / "scripts" / "bootstrap_macos.py",
        )
        with patch, lock_patch, preflight_patch, script_patch:
            if create_fake_venv and mode in ("apply", "uninstall"):
                fake_venv = self.home / "venvs" / "allinluna"
                fake_venv.mkdir(parents=True, exist_ok=True)
            return self._run_on_darwin(mode, self.home, str(self.codex), guardian_ref=self.guardian_ref, git_bin=str(self.git), **kwargs)

    def _home_snapshot(self):
        snapshot = {}
        for path in sorted(self.home.rglob("*")):
            relative = path.relative_to(self.home).as_posix()
            if path.is_symlink():
                snapshot[relative] = ("symlink", os.readlink(path))
            elif path.is_file():
                snapshot[relative] = ("file", path.read_bytes())
            elif path.is_dir():
                snapshot[relative] = ("directory",)
        return snapshot

    def test_apply_from_marketplace_script_is_rejected_without_writes(self):
        before = self._home_snapshot()
        receipt, code = self._run(
            "apply",
            create_fake_venv=False,
            script_path=self.guardian_root / bootstrap.GUARDIAN_SCRIPT_RELATIVE,
        )
        self.assertEqual(code, 1, receipt)
        self.assertEqual(before, self._home_snapshot())
        self.assertIn("versioned Guardian cache", str(receipt))

    def test_apply_from_wrong_cache_version_or_external_clean_clone_is_rejected(self):
        wrong_version = self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "codex-workflow-guardian" / "9.9.9"
        before = self._home_snapshot()
        receipt, code = self._run(
            "apply",
            create_fake_venv=False,
            script_path=wrong_version / bootstrap.GUARDIAN_SCRIPT_RELATIVE,
        )
        self.assertEqual(code, 1, receipt)
        self.assertEqual(before, self._home_snapshot())

        for invalid_version in ("..", "0.2.0/../0.2.0"):
            before = self._home_snapshot()
            receipt, code = self._run(
                "apply",
                create_fake_venv=False,
                lock_mutator=lambda lock, value=invalid_version: lock["components"]["guardian_plugin"].update(version=value),
            )
            self.assertEqual(code, 1, (invalid_version, receipt))
            self.assertEqual(before, self._home_snapshot(), invalid_version)

        outside = self.home / "alternate-cache" / "codex-workflow-guardian"
        outside.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(self.git), "clone", "-q", str(self.execution_root), str(outside)], check=True)
        subprocess.run([str(self.git), "-C", str(outside), "remote", "set-url", "origin", "https://github.com/OneBigMoon/codex-subagent-reconciler.git"], check=True)
        before = self._home_snapshot()
        receipt, code = self._run(
            "apply",
            create_fake_venv=False,
            script_path=outside / bootstrap.GUARDIAN_SCRIPT_RELATIVE,
        )
        self.assertEqual(code, 1, receipt)
        self.assertEqual(before, self._home_snapshot())

    def test_apply_rejects_any_tampered_execution_source_without_writes(self):
        relatives = (
            "skills/setup-codex-workflow-guardian/scripts/bootstrap_macos.py",
            "workflow-dependencies.lock.json",
            "skills/setup-codex-workflow-guardian/assets/agents/coder.toml",
            ".agents/plugins/marketplace.json",
            ".codex-plugin/plugin.json",
        )
        for relative in relatives:
            path = self.execution_root / relative
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\n")
                before = self._home_snapshot()
                receipt, code = self._run("apply", create_fake_venv=False)
                self.assertEqual(code, 1, (relative, receipt))
                self.assertEqual(before, self._home_snapshot(), relative)
            finally:
                path.write_bytes(original)
        for relative in relatives:
            path = self.execution_root / relative
            original = path.read_bytes()
            expected = self._home_snapshot()
            expected[(self.execution_root.relative_to(self.home) / relative).as_posix()] = ("file", original + b"\n")
            original_validate = bootstrap._validate_git_checkout

            def validate_then_swap(root, codex_home, git_bin, guardian_ref, expected_root_proof=None):
                result = original_validate(root, codex_home, git_bin, guardian_ref, expected_root_proof=expected_root_proof)
                if result[0] and pathlib.Path(root) == self.execution_root:
                    path.write_bytes(original + b"\n")
                return result

            try:
                with mock.patch.object(bootstrap, "_validate_git_checkout", side_effect=validate_then_swap):
                    receipt, code = self._run("apply", create_fake_venv=False)
                self.assertIn(code, (1, 2), (relative, receipt))
                self.assertEqual(expected, self._home_snapshot(), relative)
            finally:
                path.write_bytes(original)

    def test_apply_rejects_schema_valid_root_swap_during_execution_lock_load(self):
        replacement = self.root / "malicious-execution-root"
        shutil.copytree(self.execution_root, replacement)
        malicious_commit = "0" * 40
        lock_path = replacement / "workflow-dependencies.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["components"]["ponytail"]["commit"] = malicious_commit
        lock_path.write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        marketplace_path = replacement / ".agents" / "plugins" / "marketplace.json"
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        for plugin in marketplace["plugins"]:
            if plugin["name"] == "ponytail":
                plugin["source"]["sha"] = malicious_commit
        marketplace_path.write_text(json.dumps(marketplace, sort_keys=True, indent=2) + "\n", encoding="utf-8")

        moved_original = self.root / "original-execution-root"
        swapped = []

        def swap_during_load(repository_root, load):
            if pathlib.Path(repository_root or "") != self.execution_root:
                return load()
            swapped.append(True)
            self.execution_root.rename(moved_original)
            replacement.rename(self.execution_root)
            try:
                return load()
            finally:
                self.execution_root.rename(replacement)
                moved_original.rename(self.execution_root)

        before = self._home_snapshot()
        blocked = (
            "_plugin_command_component",
            "_plugin_add",
            "_write_journal",
            "_open_exact_url",
            "_copy_one",
            "_allinluna_install",
            "_write_receipt",
        )
        with ExitStack() as stack:
            probes = {
                name: stack.enter_context(
                    mock.patch.object(bootstrap, name, side_effect=AssertionError(f"unexpected downstream call: {name}"))
                )
                for name in blocked
            }
            receipt, code = self._run(
                "apply",
                create_fake_venv=False,
                load_lock_hook=swap_during_load,
            )
        self.assertTrue(swapped)
        self.assertEqual(code, 2, receipt)
        dependency = next(item for item in receipt["components"] if item["name"] == "dependency-lock")
        self.assertEqual(dependency["status"], "unavailable", receipt)
        self.assertTrue(all(not probe.called for probe in probes.values()))
        self.assertEqual(before, self._home_snapshot())

    def test_apply_rejects_execution_root_swap_after_preflight_before_mutation(self):
        replacement = self.root / "clean-execution-root"
        shutil.copytree(self.execution_root, replacement)
        moved_original = self.root / "original-execution-root"
        swapped = []

        def swap_after_preflight(codex_home, repository_root):
            del codex_home, repository_root
            self.execution_root.rename(moved_original)
            replacement.rename(self.execution_root)
            swapped.append(True)

        before = self._home_snapshot()
        blocked = (
            "_plugin_add",
            "_write_journal",
            "_open_exact_url",
            "_copy_one",
            "_allinluna_install",
            "_write_receipt",
        )
        try:
            with ExitStack() as stack:
                probes = {
                    name: stack.enter_context(
                        mock.patch.object(bootstrap, name, side_effect=AssertionError(f"unexpected downstream call: {name}"))
                    )
                    for name in blocked
                }
                receipt, code = self._run(
                    "apply",
                    create_fake_venv=False,
                    preflight_hook=swap_after_preflight,
                )
        finally:
            if swapped:
                self.execution_root.rename(replacement)
                moved_original.rename(self.execution_root)
        self.assertTrue(swapped)
        self.assertEqual(code, 1, receipt)
        self.assertIn("verified Guardian source changed after preflight", json.dumps(receipt, ensure_ascii=False))
        self.assertTrue(all(not probe.called for probe in probes.values()))
        self.assertEqual(before, self._home_snapshot())

    def test_apply_rejects_role_change_between_clean_and_local_proof_capture(self):
        role = self.execution_root / "skills" / "setup-codex-workflow-guardian" / "assets" / "agents" / "coder.toml"
        original = role.read_bytes()
        injected = []

        def proof_with_internal_change(path, anchor):
            if not injected and pathlib.Path(anchor) == self.execution_root and pathlib.Path(path) == role:
                role.write_bytes(original + b"\n# proof-capture race\n")
                injected.append(True)
            return original_proof(path, anchor)

        original_proof = bootstrap._source_asset_proof
        before = self._home_snapshot()
        execution_key = str(bootstrap._absolute_lexical(self.execution_root))
        blocked = (
            "_plugin_command_component",
            "_plugin_add",
            "_write_journal",
            "_open_exact_url",
            "_copy_one",
            "_allinluna_install",
            "_write_receipt",
        )
        try:
            with ExitStack() as stack:
                probes = {
                    name: stack.enter_context(
                        mock.patch.object(bootstrap, name, side_effect=AssertionError(f"unexpected downstream call: {name}"))
                    )
                    for name in blocked
                }
                stack.enter_context(mock.patch.object(bootstrap, "_source_asset_proof", side_effect=proof_with_internal_change))
                receipt, code = self._run("apply", create_fake_venv=False)
        finally:
            role.write_bytes(original)
        self.assertTrue(injected)
        self.assertNotEqual(code, 0, receipt)
        self.assertTrue(all(not probe.called for probe in probes.values()))
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertNotIn(execution_key, bootstrap._VALIDATED_SOURCE_PROOFS)
        self.assertNotIn(execution_key, bootstrap._VALIDATED_GUARDIAN_PROOFS)
        self.assertNotIn(execution_key, bootstrap._VALIDATED_ROOT_PROOFS)
        self.assertEqual(before, self._home_snapshot())

    def test_apply_rejects_role_change_after_second_clean_before_local_reproof(self):
        role = self.execution_root / "skills" / "setup-codex-workflow-guardian" / "assets" / "agents" / "coder.toml"
        original = role.read_bytes()
        clean_calls = [0]
        validation_results = []

        original_clean = bootstrap._raw_git_checkout_clean

        def clean_with_internal_change(root, codex_home, git_bin, expected):
            result = original_clean(root, codex_home, git_bin, expected)
            if pathlib.Path(root) == self.execution_root:
                clean_calls[0] += 1
                if clean_calls[0] == 2 and result:
                    role.write_bytes(original + b"\n# after-second-clean race\n")
            return result

        original_validate = bootstrap._validate_git_checkout

        def validate_and_record(root, codex_home, git_bin, guardian_ref, expected_root_proof=None):
            result = original_validate(
                root,
                codex_home,
                git_bin,
                guardian_ref,
                expected_root_proof=expected_root_proof,
            )
            if pathlib.Path(root) == self.execution_root:
                validation_results.append(result)
            return result

        before = self._home_snapshot()
        execution_key = str(bootstrap._absolute_lexical(self.execution_root))
        blocked = (
            "_plugin_command_component",
            "_plugin_add",
            "_write_journal",
            "_open_exact_url",
            "_copy_one",
            "_allinluna_install",
            "_write_receipt",
        )
        try:
            with ExitStack() as stack:
                probes = {
                    name: stack.enter_context(
                        mock.patch.object(bootstrap, name, side_effect=AssertionError(f"unexpected downstream call: {name}"))
                    )
                    for name in blocked
                }
                stack.enter_context(mock.patch.object(bootstrap, "_raw_git_checkout_clean", side_effect=clean_with_internal_change))
                stack.enter_context(mock.patch.object(bootstrap, "_validate_git_checkout", side_effect=validate_and_record))
                receipt, code = self._run("apply", create_fake_venv=False)
        finally:
            role.write_bytes(original)
        self.assertEqual(clean_calls[0], 2)
        self.assertEqual(validation_results, [(False, "Guardian checkout changed during validation")], receipt)
        self.assertNotEqual(code, 0, receipt)
        self.assertTrue(all(not probe.called for probe in probes.values()))
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertNotIn(execution_key, bootstrap._VALIDATED_SOURCE_PROOFS)
        self.assertNotIn(execution_key, bootstrap._VALIDATED_GUARDIAN_PROOFS)
        self.assertNotIn(execution_key, bootstrap._VALIDATED_ROOT_PROOFS)
        self.assertEqual(before, self._home_snapshot())

    def test_installed_guardian_path_must_match_locked_cache_coordinate(self):
        script = self.codex.read_text(encoding="utf-8")
        self.codex.write_text(script.replace(str(self.execution_root), str(self.guardian_root)), encoding="utf-8")
        before = self._home_snapshot()
        receipt, code = self._run("apply", create_fake_venv=False)
        self.assertEqual(code, 1, receipt)
        self.assertEqual(before, self._home_snapshot())
        self.assertIn("installed Guardian path", json.dumps(receipt, ensure_ascii=False))
        self._write_codex(omit_installed_path=True)
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        plugin = next(item for item in receipt["components"] if item["name"] == "codex-plugin-command")
        self.assertEqual(plugin["status"], "present")
        removed, removed_code = self._run("uninstall")
        self.assertEqual(removed_code, 0, removed)

    def _journal_for_receipt(self, receipt, phase="APPLYING", receipt_state="intent"):
        steps = []
        for plugin in receipt["owned_plugins"]:
            steps.append({
                "id": "plugin:" + plugin["name"],
                "kind": "plugin",
                "action": "add",
                "state": "done",
                "relative_path": plugin["relative_path"],
                "sha256": None,
                "commit": plugin["sha"],
                "selector": plugin["selector"],
                "preexisting": False,
                "device": plugin["device"],
                "inode": plugin["inode"],
            })
        for path in receipt["owned_paths"]:
            steps.append({
                "id": "file:" + path["relative_path"],
                "kind": "file",
                "action": "publish",
                "state": "done",
                "relative_path": path["relative_path"],
                "sha256": path["sha256"],
                "commit": None,
                "selector": None,
                "device": path["device"],
                "inode": path["inode"],
                "staging_relative_path": bootstrap._copy_staging_relative_path(path["relative_path"]),
                "resolver_digest": None,
            })
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        sidecar_digest = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
        steps.append({
            "id": "receipt",
            "kind": "receipt",
            "action": "write",
            "state": receipt_state,
            "relative_path": "workflow-guardian/bootstrap-receipt.json",
            "sha256": None,
            "commit": None,
            "selector": None,
            "new_receipt_sha256": receipt_digest if receipt_state == "started" else None,
            "new_sidecar_sha256": sidecar_digest if receipt_state == "started" else None,
            "prior_receipt_sha256": None,
            "prior_sidecar_sha256": None,
            "prior_receipt_backup_relative_path": None,
            "prior_sidecar_backup_relative_path": None,
            "new_receipt_temp_relative_path": None,
            "new_sidecar_temp_relative_path": None,
        })
        return {"schema": bootstrap.JOURNAL_SCHEMA, "generation": 1, "digest": "", "mode": "apply", "phase": phase, "steps": steps}

    def _receipt_only_journal(self):
        return {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [{
                "id": "receipt",
                "kind": "receipt",
                "action": "write",
                "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(),
                "sha256": None,
                "commit": None,
                "selector": None,
                "new_receipt_sha256": None,
                "new_sidecar_sha256": None,
                "prior_receipt_sha256": None,
                "prior_sidecar_sha256": None,
                "prior_receipt_backup_relative_path": None,
                "prior_sidecar_backup_relative_path": None,
                "new_receipt_temp_relative_path": None,
                "new_sidecar_temp_relative_path": None,
            }],
        }

    def _publish_crash_journal(self, kind, target_relative, state):
        if kind == "file" and target_relative not in bootstrap.MANAGED_ROLE_RELATIVES:
            target_relative = bootstrap.MANAGED_ROLE_RELATIVES[0]
        if kind == "venv":
            target_relative = bootstrap.MANAGED_VENV_RELATIVE
        target = self.home / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "file":
            stage = self.home / bootstrap._copy_staging_relative_path(target_relative)
            stage.parent.mkdir(parents=True, exist_ok=True)
            stage.write_bytes(b"publish-crash")
        else:
            stage = self.home / "venvs/.guardian-venv-allinluna"
            stage.mkdir()
            (stage / "bin").mkdir()
            (stage / "bin" / "python").write_bytes(b"publish-crash")
        identity = bootstrap._identity(stage.lstat())
        expected = bootstrap._tree_hash(stage)
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [{
                "id": ("file:" if kind == "file" else "venv:allinluna") + (target_relative if kind == "file" else ""),
                "kind": kind,
                "action": "publish",
                "state": state,
                "relative_path": target_relative,
                "staging_relative_path": stage.relative_to(self.home).as_posix(),
                "sha256": expected,
                "commit": None,
                "selector": None,
                "device": identity["device"],
                "inode": identity["inode"],
            }, {
                "id": "receipt",
                "kind": "receipt",
                "action": "write",
                "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(),
                "sha256": None,
                "commit": None,
                "selector": None,
            }],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        return stage, target, identity, expected, journal

    def _no_identity_publish_journal(self, kind, target_relative, state="started", staging_relative=None):
        if kind == "file" and target_relative not in bootstrap.MANAGED_ROLE_RELATIVES:
            target_relative = bootstrap.MANAGED_ROLE_RELATIVES[0]
        if kind == "venv":
            target_relative = bootstrap.MANAGED_VENV_RELATIVE
        target = self.home / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if staging_relative is None:
            staging_relative = (
                bootstrap._copy_staging_relative_path(target_relative)
                if kind == "file"
                else "venvs/.guardian-venv-allinluna"
            )
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [{
                "id": ("file:" if kind == "file" else "venv:allinluna") + (target_relative if kind == "file" else ""),
                "kind": kind,
                "action": "publish",
                "state": state,
                "relative_path": target_relative,
                "staging_relative_path": staging_relative,
                "sha256": "0" * 64 if kind == "file" else None,
                "commit": None,
                "selector": None,
                "device": None,
                "inode": None,
            }, {
                "id": "receipt",
                "kind": "receipt",
                "action": "write",
                "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(),
                "sha256": None,
                "commit": None,
                "selector": None,
            }],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        return target, self.home / staging_relative, journal

    def test_python_39_syntax_and_help_requires_git(self):
        ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT), feature_version=(3, 9))
        with self.assertRaises(SystemExit):
            bootstrap._parse_args(["--check", "--codex-home", str(self.home), "--codex-bin", str(self.codex)])

    def test_codex_home_allows_nonwritable_shared_modes_but_rejects_writable_or_acl(self):
        current_uid = os.getuid()
        for mode in (0o700, 0o750, 0o755):
            self.home.chmod(mode)
            self.assertIsNone(bootstrap._validate_codex_home(self.home))
        for mode in (0o775, 0o707):
            self.home.chmod(mode)
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._validate_codex_home(self.home)
        self.home.chmod(0o755)
        with mock.patch.object(bootstrap, "_has_acl", return_value=True):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._validate_codex_home(self.home)
        self.assertEqual(self.home.stat().st_uid, current_uid)

    def test_managed_parent_chain_checks_owner_mode_acl_and_creates_private(self):
        self.home.chmod(0o755)
        directory_fd, target_name = bootstrap._open_relative_parent(self.home, "agents/coder.toml")
        os.close(directory_fd)
        self.assertEqual(target_name, "coder.toml")
        agents = self.home / "agents"
        self.assertEqual(stat.S_IMODE(agents.stat().st_mode), 0o700)

        agents.chmod(0o755)
        directory_fd, _ = bootstrap._open_relative_parent(self.home, "agents/coder.toml")
        os.close(directory_fd)
        agents.chmod(0o775)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._open_relative_parent(self.home, "agents/coder.toml")
        agents.chmod(0o755)
        with mock.patch.object(bootstrap, "_has_acl", return_value=True):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._open_relative_parent(self.home, "agents/coder.toml")
        current_uid = os.getuid()
        with mock.patch.object(bootstrap.os, "getuid", return_value=current_uid + 1):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._open_relative_parent(self.home, "agents/coder.toml")

    def test_workflow_guardian_and_state_files_remain_private_and_are_not_repaired(self):
        journal = self._receipt_only_journal()
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        state_dir = self.home / "workflow-guardian"
        journal_path = bootstrap._journal_path(self.home)
        self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(journal_path.stat().st_mode), 0o600)

        state_dir.chmod(0o755)
        self.assertIsNotNone(bootstrap._write_journal(self.home, journal))
        self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode), 0o755)
        state_dir.chmod(0o700)
        journal_path.chmod(0o644)
        stored, error = bootstrap._read_journal(self.home)
        self.assertIsNone(stored)
        self.assertIsNotNone(error)
        self.assertIsNotNone(bootstrap._write_journal(self.home, journal))

    def test_non_finite_and_nested_spoof_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._safe_json_loads(value, "test")
        bad = self._entry("codex-workflow-guardian", {"source": "local", "path": str(self.guardian_root)})
        bad["source"]["spoof"] = True
        self.assertIsNone(bootstrap._plugin_entry(bad, "installed"))

    def test_real_0146_plugin_rows_use_selector_ids_and_null_available_versions(self):
        installed = self._entry("allinluna", {"source": "git-subdir", "url": "https://github.com/zenx0x/allinluna.git", "path": "./plugins/allinluna", "sha": "723088a7c0d7342f077ad675c6ea72d7e3996536"})
        available = self._entry("allinluna", installed["source"], installed=False, enabled=False)
        self.assertEqual(installed["pluginId"], "allinluna@onebigmoon-codex-workflows")
        self.assertIsNotNone(bootstrap._plugin_entry(installed, "installed"))
        self.assertIsNone(installed.get("installedPath"))
        self.assertIsNotNone(bootstrap._plugin_entry(available, "available"))
        self.assertIsNone(available["version"])

    def test_real_0146_marketplace_rows_filter_unrelated_sparse_entries(self):
        valid = {
            "name": "onebigmoon-codex-workflows",
            "root": str(self.guardian_root),
            "marketplaceSource": {"sourceType": "git", "source": "https://github.com/OneBigMoon/codex-subagent-reconciler"},
        }
        payload = {"marketplaces": [{"name": "unrelated", "root": "/tmp/unrelated"}, valid]}
        self.assertEqual(bootstrap._marketplace_resolver_payload(payload), [valid])
        self.assertIsNone(bootstrap._marketplace_resolver_payload({"marketplaces": [valid, dict(valid)]}))
        malformed = dict(valid)
        del malformed["marketplaceSource"]
        self.assertIsNone(bootstrap._marketplace_resolver_payload({"marketplaces": [malformed]}))

    def test_allinluna_missing_row_path_uses_only_locked_coordinate(self):
        lock = bootstrap._load_lock()
        entry = self._entry(
            "allinluna",
            {
                "source": "git-subdir",
                "url": "https://github.com/zenx0x/allinluna.git",
                "path": "plugins/allinluna",
                "sha": lock["components"]["allinluna"]["commit"],
            },
        )
        coordinate = "plugins/cache/onebigmoon-codex-workflows/allinluna/2.0.0-rc.3"
        exact_codex = {"status": "present", "version": "0.146.0", "version_skew": False}
        self.assertEqual(
            bootstrap._plugin_observed_relative_path("allinluna", entry, self.home, lock, None, exact_codex),
            coordinate,
        )
        skewed_codex = {"status": "present", "version": "0.147.0", "version_skew": True}
        self.assertIsNone(
            bootstrap._plugin_observed_relative_path("allinluna", entry, self.home, lock, None, skewed_codex)
        )
        entry["installedPath"] = str(self.home / coordinate)
        self.assertEqual(
            bootstrap._plugin_observed_relative_path("allinluna", entry, self.home, lock, coordinate, skewed_codex),
            coordinate,
        )
        for bad in (
            "plugin-cache/allinluna",
            "plugins/cache/onebigmoon-codex-workflows/allinluna/9.9.9",
            "plugins/cache/onebigmoon-codex-workflows/allinluna/2.0.0-rc.3/../9.9.9",
            "./" + coordinate,
            coordinate.replace("/cache/", "//cache/"),
            coordinate + "/",
        ):
            entry["installedPath"] = bad
            self.assertIsNone(bootstrap._plugin_observed_relative_path("allinluna", entry, self.home, lock, None), bad)
        entry["installedPath"] = coordinate
        self.assertIsNone(
            bootstrap._plugin_observed_relative_path("allinluna", entry, self.home, lock, "./" + coordinate, exact_codex)
        )

    def test_allinluna_source_path_accepts_one_form_and_rejects_traversal(self):
        lock = bootstrap._load_lock()
        for path in ("plugins/allinluna", "./plugins/allinluna"):
            entry = self._entry(
                "allinluna",
                {
                    "source": "git-subdir",
                    "url": "https://github.com/zenx0x/allinluna.git",
                    "path": path,
                    "sha": lock["components"]["allinluna"]["commit"],
                },
            )
            self.assertTrue(bootstrap._plugin_source_matches("allinluna", entry, lock), path)
        for path in ("././plugins/allinluna", "plugins//allinluna", "plugins/../plugins/allinluna", "/plugins/allinluna", r"plugins\\allinluna"):
            entry = self._entry(
                "allinluna",
                {
                    "source": "git-subdir",
                    "url": "https://github.com/zenx0x/allinluna.git",
                    "path": path,
                    "sha": lock["components"]["allinluna"]["commit"],
                },
            )
            self.assertFalse(bootstrap._plugin_source_matches("allinluna", entry, lock), path)

    def test_plugin_add_validates_real_0146_six_field_json(self):
        cache = self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3"
        payload = {
            "pluginId": "allinluna@onebigmoon-codex-workflows",
            "name": "allinluna",
            "marketplaceName": "onebigmoon-codex-workflows",
            "version": "2.0.0rc3",
            "installedPath": str(cache),
            "authPolicy": "ON_INSTALL",
        }
        with mock.patch.object(bootstrap, "_run_argv", return_value=(0, json.dumps(payload), "")):
            result = bootstrap._plugin_add(self.codex, self.home, "allinluna@onebigmoon-codex-workflows")
        self.assertEqual(result.kind, "valid")
        self.assertEqual(result.path, payload["installedPath"])

    def test_plugin_add_rejects_allinluna_path_aliases_as_invalid_response(self):
        lock = bootstrap._load_lock()
        coordinate = "plugins/cache/onebigmoon-codex-workflows/allinluna/2.0.0-rc.3"
        payload = {
            "pluginId": "allinluna@onebigmoon-codex-workflows",
            "name": "allinluna",
            "marketplaceName": "onebigmoon-codex-workflows",
            "version": "2.0.0rc3",
            "authPolicy": "ON_INSTALL",
        }
        for alias in ("./" + coordinate, coordinate + "/", coordinate.replace("/cache/", "//cache/"), coordinate + "/../allinluna"):
            with self.subTest(alias=alias):
                value = dict(payload, installedPath=alias)
                with mock.patch.object(bootstrap, "_run_argv", return_value=(0, json.dumps(value), "")):
                    result = bootstrap._plugin_add(
                        self.codex,
                        self.home,
                        "allinluna@onebigmoon-codex-workflows",
                        lock,
                    )
                self.assertEqual(result.kind, "invalid-response")

    def test_codex_plugin_runtime_wrapper_rejects_dynamic_or_destructive_argv(self):
        spec = bootstrap.LaunchSpec(self.codex, "codex", self.root)
        allowed = (
            [spec, "plugin", "list", "--json"],
            [spec, "plugin", "list", "--available", "--json"],
            [spec, "plugin", "marketplace", "list", "--json"],
            [spec, "plugin", "add", "allinluna@onebigmoon-codex-workflows", "--json"],
        )
        rejected = (
            [spec, "plugin", "remove", "allinluna@onebigmoon-codex-workflows", "--json"],
            [spec, "plugin", "remove"],
            [spec, "plugin", "list", "--json", "--unsafe"],
            [spec, "plugin", "add", "ponytail@onebigmoon-codex-workflows", "--json"],
            [spec, "plugin", "add", "codex-workflow-guardian@onebigmoon-codex-workflows", "--json"],
            [spec, "plugin", "add", "foreign@onebigmoon-codex-workflows", "--json"],
            [spec, "plugin", "add", "allinluna@foreign-marketplace", "--json"],
            [self.codex, "plugin", "list", "--json"],
        )
        with mock.patch.object(bootstrap, "_run_argv", return_value=(0, "", "")) as run_argv:
            for argv in allowed:
                with self.subTest(argv=argv):
                    self.assertEqual(bootstrap._run_codex_plugin_argv(argv, self.home, self.root), (0, "", ""))
            for argv in rejected:
                with self.subTest(argv=argv):
                    self.assertEqual(bootstrap._run_codex_plugin_argv(argv, self.home, self.root), (-1, "", ""))
        self.assertEqual(run_argv.call_count, len(allowed))

        operation = "remove"
        dynamic_tokens = ["plugin", operation]
        self.assertEqual(
            bootstrap._run_codex_plugin_argv([spec, *dynamic_tokens], self.home, self.root),
            (-1, "", ""),
        )
        starred_remove = ["plugin", "remove"]
        self.assertEqual(
            bootstrap._run_codex_plugin_argv([spec, *starred_remove], self.home, self.root),
            (-1, "", ""),
        )

    def test_bootstrap_has_no_plugin_remove_execution_surface(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("def _plugin_remove", source)
        tree = ast.parse(source, filename=str(SCRIPT))
        uninstall = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_uninstall")
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_plugin_remove"
                for node in ast.walk(uninstall)
            )
        )
        self.assertEqual(_plugin_remove_argv_calls(tree), [])

    def test_plugin_commands_have_no_direct_run_argv_surface(self):
        source = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SCRIPT))
        self.assertEqual(_plugin_remove_argv_calls(tree), [])
        self.assertGreaterEqual(source.count("_run_codex_plugin_argv"), 5)

    def test_plugin_remove_static_gate_catches_nonconstant_executable_mutation(self):
        source = SCRIPT.read_text(encoding="utf-8")
        mutated = source + "\n_run_argv([launch_spec(), 'plugin', 'remove'], codex_home, cwd)\n"
        tree = ast.parse(mutated, filename=str(SCRIPT))
        self.assertEqual(len(_plugin_remove_argv_calls(tree)), 1)

    def test_plugin_remove_static_gate_fails_closed_on_dynamic_operation_bindings(self):
        mutations = (
            "operation = 'remove'\n_run_argv([spec, 'plugin', operation], codex_home, cwd)\n",
            "tokens = ['plugin', 'remove']\n_run_argv([spec, *tokens], codex_home, cwd)\n",
        )
        source = SCRIPT.read_text(encoding="utf-8")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                tree = ast.parse(source + "\n" + mutation, filename=str(SCRIPT))
                self.assertEqual(len(_plugin_remove_argv_calls(tree)), 1)

    def test_setup_skill_declares_plugins_installed_but_unowned(self):
        skill = (SCRIPT.parent.parent / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Whether an exact Plugin was pre-existing", skill)
        self.assertIn("always `installed-but-unowned`", skill)
        self.assertIn("never grants Plugin ownership, rollback", skill)
        self.assertIn("Ponytail is\n  detection-only here", skill)
        self.assertNotIn("receipt-owned only when this Setup run added it", skill)
        self.assertNotIn("Only selectors newly\n  added by this run are recorded as owned", skill)

    def _run_with_blocked_unix_imports(self, arguments):
        child = textwrap.dedent(
            """
            import builtins
            import platform
            import runpy
            import sys

            real_import = builtins.__import__
            def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name.partition(".")[0] in {"grp", "pwd"}:
                    raise ModuleNotFoundError("blocked Unix-only module: " + name)
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = blocked_import
            platform.system = lambda: "Windows"
            script = sys.argv[1]
            sys.argv = [script, *sys.argv[2:]]
            runpy.run_path(script, run_name="__main__")
            """
        )
        return subprocess.run(
            [sys.executable, "-I", "-c", child, str(SCRIPT), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )

    def test_non_darwin_startup_does_not_require_grp_or_pwd(self):
        help_result = self._run_with_blocked_unix_imports(["--help"])
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("usage:", help_result.stdout)
        self.assertEqual(help_result.stderr, "")

        missing_home = self.root / "sentinel-missing-home"
        check_arguments = [
            "--check",
            "--codex-home",
            str(missing_home),
            "--codex-bin",
            str(self.root / "missing-codex"),
            "--git-bin",
            str(self.root / "missing-git"),
        ]
        missing_result = self._run_with_blocked_unix_imports(check_arguments)
        self.assertEqual(missing_result.returncode, 2)
        self.assertEqual(missing_result.stderr, "")
        missing_projection = json.loads(missing_result.stdout)
        self.assertEqual(missing_projection["platform"], "Windows")
        self.assertEqual(missing_projection["components"], [{"name": "platform", "status": "unavailable", "reason": "macOS is required"}])
        self.assertFalse(missing_home.exists())

        unreadable_home = self.root / "sentinel-unreadable-home"
        unreadable_home.mkdir(mode=0o700)
        unreadable_home.chmod(0o000)
        try:
            unreadable_result = self._run_with_blocked_unix_imports(
                [
                    "--check",
                    "--codex-home",
                    str(unreadable_home),
                    "--codex-bin",
                    str(self.root / "missing-codex"),
                    "--git-bin",
                    str(self.root / "missing-git"),
                ]
            )
        finally:
            unreadable_home.chmod(0o700)
        self.assertEqual(unreadable_result.returncode, 2)
        self.assertEqual(unreadable_result.stderr, "")
        unreadable_projection = json.loads(unreadable_result.stdout)
        self.assertEqual(unreadable_projection["platform"], "Windows")
        self.assertEqual(unreadable_projection["components"], [{"name": "platform", "status": "unavailable", "reason": "macOS is required"}])
        self.assertFalse((unreadable_home / "workflow-guardian").exists())

        for mode in ("--apply", "--uninstall"):
            sentinel_home = self.root / ("sentinel-" + mode[2:] + "-home")
            result = self._run_with_blocked_unix_imports(
                [
                    mode,
                    "--codex-home",
                    str(sentinel_home),
                    "--codex-bin",
                    str(self.root / "missing-codex"),
                    "--git-bin",
                    str(self.root / "missing-git"),
                ]
            )
            with self.subTest(mode=mode):
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "")
                projection = json.loads(result.stdout)
                self.assertEqual(projection["platform"], "Windows")
                self.assertEqual(projection["components"], [{"name": "platform", "status": "unavailable", "reason": "macOS is required"}])
                self.assertFalse(sentinel_home.exists())

    def test_run_has_no_platform_override_argument(self):
        argument_names = bootstrap.run.__code__.co_varnames[: bootstrap.run.__code__.co_argcount]
        self.assertNotIn("platform_name", argument_names)

    def test_process_group_cleanup_boundary_documents_setsid_escape(self):
        self.assertIn("setsid", bootstrap._kill_process_group.__doc__ or "")

    def test_process_group_cleanup_kills_normal_child_and_grandchild(self):
        for mode in ("timeout", "overflow"):
            with self.subTest(mode=mode):
                marker = self.root / ("late-" + mode)
                started = self.root / ("started-" + mode)
                script = self.root / ("process-tree-" + mode)
                grandchild = (
                    "import pathlib, time; pathlib.Path(%r).write_text('started'); "
                    "time.sleep(3.0); pathlib.Path(%r).write_text('late')"
                ) % (str(started), str(marker))
                burst = "sys.stderr.write('x' * 1000000); sys.stderr.flush()\n" if mode == "overflow" else ""
                script.write_text(
                    "#!/usr/bin/env python3\n"
                    "import pathlib, subprocess, sys, time\n"
                    "subprocess.Popen([sys.executable, '-I', '-c', %r])\n"
                    "deadline = time.monotonic() + 1.5\n"
                    "while not pathlib.Path(%r).exists() and time.monotonic() < deadline:\n"
                    "    time.sleep(0.01)\n"
                    "print('spawned', flush=True)\n"
                    "%s"
                    "time.sleep(5)\n" % (grandchild, str(started), burst),
                    encoding="utf-8",
                )
                script.chmod(0o700)
                old_limit = bootstrap.MAX_COMMAND_OUTPUT
                if mode == "overflow":
                    bootstrap.MAX_COMMAND_OUTPUT = 1024
                try:
                    result = bootstrap._run_argv([str(script)], self.home, self.root, timeout=2)
                finally:
                    bootstrap.MAX_COMMAND_OUTPUT = old_limit
                self.assertEqual(result, (-1, "", ""))
                self.assertTrue(started.exists())
                time.sleep(3.8)
                self.assertFalse(marker.exists())

    def test_apply_recovery_rejects_self_consistent_wrong_guardian_ref(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0)
        wrong_ref = "f" * 40
        receipt["guardian_ref"] = wrong_ref
        self.assertIsNone(bootstrap._write_receipt(self.home, receipt)[1])
        owned = next(item for item in receipt["owned_paths"] if item["kind"] == "file")
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [
                {"id": "file:" + owned["relative_path"], "kind": "file", "action": "publish", "state": "owned", "relative_path": owned["relative_path"], "sha256": owned["sha256"], "commit": None, "selector": None, "device": owned["device"], "inode": owned["inode"], "staging_relative_path": bootstrap._copy_staging_relative_path(owned["relative_path"]), "resolver_digest": None},
                {"id": "receipt", "kind": "receipt", "action": "write", "state": "owned", "relative_path": "workflow-guardian/bootstrap-receipt.json", "sha256": None, "commit": None, "selector": None},
            ],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        lock = bootstrap._load_lock()
        proven = bootstrap._receipt_proves_apply_journal(self.home, journal, receipt, self.codex, lock, self.guardian_ref, self.git)
        self.assertFalse(proven)

    def test_uninstall_requires_current_guardian_ref_and_rejects_wrong_ref(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0)
        missing, missing_code = self._run_on_darwin("uninstall", self.home, str(self.codex), guardian_ref=None, git_bin=str(self.git))
        self.assertEqual(missing_code, 1)
        self.assertEqual(next(item for item in missing["components"] if item["name"] == "uninstall")["status"], "conflict")
        wrong_ref = "f" * 40
        receipt["guardian_ref"] = wrong_ref
        self.assertIsNone(bootstrap._write_receipt(self.home, receipt)[1])
        wrong, wrong_code = self._run_on_darwin("uninstall", self.home, str(self.codex), guardian_ref=wrong_ref, git_bin=str(self.git))
        self.assertEqual(wrong_code, 1)
        self.assertEqual(next(item for item in wrong["components"] if item["name"] == "uninstall")["status"], "conflict")
        self.assertTrue(bootstrap._receipt_paths(self.home)[0].exists())

    def test_malformed_plugin_add_response_keeps_started_journal_without_requery(self):
        original_query = bootstrap._plugin_command_component
        with mock.patch.object(
            bootstrap,
            "_plugin_add",
            return_value=bootstrap.PluginAddOutcome.invalid_response(),
        ), mock.patch.object(bootstrap, "_plugin_command_component", wraps=original_query) as query:
            receipt, code = self._run("apply")
        self.assertEqual(code, 1, receipt)
        self.assertEqual(receipt["status"], "recovery-required")
        # The initial preflight query is allowed; invalid non-empty add output
        # must not trigger a same-turn re-query that masks the contract fault.
        self.assertEqual(query.call_count, 1)
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        step = next(item for item in journal["steps"] if item["id"] == "plugin:allinluna")
        self.assertEqual(step["state"], "started")

    def test_uncertain_plugin_add_strict_requery_keeps_install_unowned(self):
        def add_then_lose_response(_executable, home, selector, _lock, *_args):
            name = selector.split("@", 1)[0]
            cache = home / "plugins" / "cache" / "onebigmoon-codex-workflows" / name / "2.0.0-rc.3"
            cache.mkdir(parents=True, exist_ok=True)
            (home / "plugin-state.json").write_text(json.dumps([name]))
            return bootstrap.PluginAddOutcome.uncertain()

        with mock.patch.object(bootstrap, "_plugin_add", side_effect=add_then_lose_response):
            receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["status"], "ready")
        self.assertEqual(receipt["owned_plugins"], [])
        self.assertTrue(any("installed but unowned" in note for note in receipt["notes"]))
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_uncertain_plugin_add_recovery_requeries_and_preserves_unowned_install(self):
        lock = bootstrap._load_lock()
        commit = lock["components"]["allinluna"]["commit"]
        selector = "allinluna@onebigmoon-codex-workflows"
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [
                {
                    "id": "plugin:allinluna",
                    "kind": "plugin",
                    "action": "add",
                    "state": "started",
                    "relative_path": "",
                    "sha256": None,
                    "commit": commit,
                    "selector": selector,
                    "preexisting": False,
                },
                {
                    "id": "receipt",
                    "kind": "receipt",
                    "action": "write",
                    "state": "intent",
                    "relative_path": "workflow-guardian/bootstrap-receipt.json",
                    "sha256": None,
                    "commit": None,
                    "selector": None,
                },
            ],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        proven = {
            "status": "present",
            "installed": {"allinluna": True},
            "installed_paths": {"allinluna": "plugins/cache/onebigmoon-codex-workflows/allinluna/2.0.0-rc.3"},
            "installed_sha": {"allinluna": commit},
            "hits": [{"plugin": "allinluna", "location": "installed", "installed": True}],
        }
        with mock.patch.object(bootstrap, "_plugin_command_component", return_value=proven) as query:
            recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, lock, self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(next(step for step in journal["steps"] if step["id"] == "plugin:allinluna")["state"], "failed")
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_plugin_add_crash_before_unowned_marker_never_removes_install(self):
        class HardCrash(BaseException):
            pass

        with mock.patch.object(bootstrap, "_plugin_ownership_proof", side_effect=HardCrash()):
            with self.assertRaises(HardCrash):
                self._run("apply")
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        step = next(item for item in journal["steps"] if item["id"] == "plugin:allinluna")
        self.assertEqual(step["state"], "started")
        self.assertFalse(step["preexisting"])
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text()), ["allinluna"])

        recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text()), ["allinluna"])

    def test_valid_plugin_add_with_unavailable_post_query_keeps_owned_recovery_journal(self):
        original_component = bootstrap._plugin_command_component
        calls = []

        def query(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                return original_component(*args, **kwargs)
            return {"name": "codex-plugin-command", "status": "unavailable"}

        with mock.patch.object(bootstrap, "_plugin_command_component", side_effect=query):
            receipt, code = self._run("apply")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "recovery-required")
        self.assertTrue(any(item.get("action") == "preserved-uncertain-add" for item in receipt["rollback"]["actions"]))
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        step = next(item for item in journal["steps"] if item["id"] == "plugin:allinluna")
        self.assertEqual(step["state"], "started")
        self.assertFalse(step["preexisting"])

    def test_preexisting_plugin_pre_add_crash_without_path_reverifies_coordinate(self):
        self._write_codex(omit_installed_path=True)
        (self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3").mkdir(parents=True)
        (self.home / "plugin-state.json").write_text(json.dumps(["allinluna"]), encoding="utf-8")
        digest = bootstrap._plugin_resolver_row_digest(self.home, self.codex, "allinluna")
        self.assertIsNotNone(digest)
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [
                {
                    "id": "plugin:allinluna",
                    "kind": "plugin",
                    "action": "add",
                    "state": "started",
                    "relative_path": "",
                    "resolver_digest": digest,
                    "sha256": None,
                    "commit": bootstrap._load_lock()["components"]["allinluna"]["commit"],
                    "selector": "allinluna@onebigmoon-codex-workflows",
                    "preexisting": True,
                },
                {
                    "id": "receipt",
                    "kind": "receipt",
                    "action": "write",
                    "state": "intent",
                    "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(),
                    "sha256": None,
                    "commit": None,
                    "selector": None,
                },
            ],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        with mock.patch.object(bootstrap, "_plugin_add", wraps=bootstrap._plugin_add) as add:
            recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertEqual(recovered["status"], "ready")
        add.assert_not_called()
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text(encoding="utf-8")), ["allinluna"])

    def test_preexisting_plugin_pre_add_crash_candidate_disappeared_never_adds(self):
        self._write_codex(omit_installed_path=True)
        (self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3").mkdir(parents=True)
        (self.home / "plugin-state.json").write_text(json.dumps(["allinluna"]), encoding="utf-8")
        digest = bootstrap._plugin_resolver_row_digest(self.home, self.codex, "allinluna")
        self.assertIsNotNone(digest)
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [{
                "id": "plugin:allinluna", "kind": "plugin", "action": "add", "state": "started",
                "relative_path": "", "resolver_digest": digest, "sha256": None,
                "commit": bootstrap._load_lock()["components"]["allinluna"]["commit"],
                "selector": "allinluna@onebigmoon-codex-workflows", "preexisting": True,
            }, {
                "id": "receipt", "kind": "receipt", "action": "write", "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(), "sha256": None,
                "commit": None, "selector": None,
            }],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        (self.home / "plugin-state.json").write_text(json.dumps([]), encoding="utf-8")
        with mock.patch.object(bootstrap, "_plugin_add") as add:
            recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 1)
        self.assertEqual(recovered["status"], "recovery-required")
        add.assert_not_called()
        self.assertTrue(bootstrap._journal_path(self.home).exists())

    def test_file_and_venv_before_publish_crash_remove_only_staged_inode(self):
        for kind, relative in (("file", "agents/crash-before.txt"), ("venv", "venvs/crash-before")):
            with self.subTest(kind=kind):
                stage, target, _, _, journal = self._publish_crash_journal(kind, relative, "staged")
                recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
                self.assertTrue(recovered, reason)
                self.assertFalse(stage.exists())
                self.assertFalse(target.exists())

    def test_file_started_pre_callback_baseexception_is_no_effect(self):
        class HardCrash(BaseException):
            pass

        with mock.patch.object(bootstrap, "_copy_one", side_effect=HardCrash()):
            with self.assertRaises(HardCrash):
                self._run("apply")
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        self.assertEqual(next(step for step in journal["steps"] if step["kind"] == "file")["state"], "started")
        recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertEqual(recovered["status"], "ready")
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_subprocess_exit_after_file_stage_create_recovers_empty_unowned_stage(self):
        relative = bootstrap.MANAGED_ROLE_RELATIVES[0]
        source = self.root / "subprocess-source.txt"
        source.write_bytes(b"source")
        target, stage, journal = self._no_identity_publish_journal("file", relative)
        plan = {
            "source": str(source),
            "source_anchor": str(source.parent),
            "source_proof": bootstrap._source_asset_proof(source, source.parent),
            "codex_home": str(self.home),
            "relative_path": relative,
            "target": str(target),
            "kind": "file",
            "expected_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "staging_relative_path": stage.relative_to(self.home).as_posix(),
        }
        child = textwrap.dedent(
            """
            import importlib.util
            import json
            import os
            import sys
            spec = importlib.util.spec_from_file_location("bootstrap_macos", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            original = module._temporary_file_at
            def crash(directory_fd, name=None):
                original(directory_fd, name)
                os._exit(71)
            module._temporary_file_at = crash
            module._copy_one(json.loads(sys.argv[2]))
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child, str(SCRIPT), json.dumps(plan)],
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 71, result.stderr)
        self.assertTrue(stage.exists())
        self.assertEqual(stage.read_bytes(), b"")
        recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertFalse(stage.exists())
        self.assertFalse(target.exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_subprocess_exit_after_venv_stage_create_recovers_empty_stage(self):
        target, stage, journal = self._no_identity_publish_journal("venv", "venvs/allinluna")
        child = textwrap.dedent(
            """
            import importlib.util
            import os
            import pathlib
            import sys
            spec = importlib.util.spec_from_file_location("bootstrap_macos", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # This subprocess also uses the suite's portable unsigned Codex
            # fixture; keep it on the non-Darwin compatibility lane.
            module.platform.system = lambda: "Linux"
            home = pathlib.Path(sys.argv[2])
            stage = home / "venvs/.guardian-venv-allinluna"
            original_tempdir = module._temporary_directory_at
            def crash(directory_fd, name=None):
                result = original_tempdir(directory_fd, name)
                if name == ".guardian-venv-allinluna":
                    os._exit(72)
                return result
            module._temporary_directory_at = crash
            module._allinluna_component = lambda *args, **kwargs: {"name": "allinluna", "status": "planned"}
            module._allinluna_python = lambda *args, **kwargs: pathlib.Path(sys.executable)
            module._allinluna_install(module._load_lock(), home, None)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child, str(SCRIPT), str(self.home)],
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 72, result.stderr)
        self.assertTrue(stage.exists())
        self.assertEqual(list(stage.iterdir()), [])
        recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertFalse(stage.exists())
        self.assertFalse(target.exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_subprocess_exit_after_empty_stage_quarantine_journal_recovers_by_hash(self):
        relative = "agents/empty-quarantine-stage.txt"
        target, stage, journal = self._no_identity_publish_journal("file", relative)
        stage.touch()
        child = textwrap.dedent(
            """
            import importlib.util
            import os
            import pathlib
            import sys
            spec = importlib.util.spec_from_file_location("bootstrap_macos", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            home = pathlib.Path(sys.argv[2])
            journal, error = module._read_journal(home)
            if error or journal is None:
                raise RuntimeError(error or "journal missing")
            step = next(item for item in journal["steps"] if item["id"] == sys.argv[3])
            original = module._journal_quarantine_callback(home, journal, sys.argv[3])
            def crash(relative_path, identity, state, expected):
                original(relative_path, identity, state, expected)
                if state == "quarantined":
                    os._exit(77)
            module._recover_started_publish(home, step, quarantine_callback=crash)
            """
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                child,
                str(SCRIPT),
                str(self.home),
                "file:" + bootstrap.MANAGED_ROLE_RELATIVES[0],
            ],
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertFalse(stage.exists())
        pending, pending_error = bootstrap._read_journal(self.home)
        self.assertIsNone(pending_error)
        self.assertIsNotNone(pending)
        pending_step = next(step for step in pending["steps"] if step["id"] == "file:" + bootstrap.MANAGED_ROLE_RELATIVES[0])
        quarantine = self.home / pending_step["quarantine_relative_path"]
        self.assertTrue(quarantine.exists())
        self.assertIsNotNone(pending_step["quarantine_sha256"])
        self.assertEqual(bootstrap._tree_hash(quarantine), pending_step["quarantine_sha256"])
        recovered, reason = bootstrap._recover_apply_journal(self.home, pending, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertFalse(quarantine.exists())
        self.assertFalse(target.exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_started_publish_without_identity_requires_exact_empty_fixed_stage(self):
        cases = (
            ("nonempty", "file", "agents/nonempty-stage.txt"),
            ("symlink", "file", "agents/symlink-stage.txt"),
            ("fifo", "file", "agents/fifo-stage.txt"),
            ("kind-mismatch", "venv", "venvs/allinluna"),
            ("concurrent-target", "file", "agents/concurrent-stage.txt"),
            ("path-mismatch", "file", "agents/path-mismatch-stage.txt"),
            ("already-building", "file", "agents/already-building-stage.txt"),
        )
        for name, kind, relative in cases:
            with self.subTest(name=name):
                target, stage, journal = self._no_identity_publish_journal(
                    kind,
                    relative,
                    state="building" if name == "already-building" else "started",
                    staging_relative=None,
                )
                if name == "path-mismatch":
                    journal["steps"][0]["staging_relative_path"] = "agents/not-fixed"
                if name == "nonempty":
                    stage.write_bytes(b"not-empty")
                elif name == "symlink":
                    stage.parent.mkdir(parents=True, exist_ok=True)
                    stage.symlink_to(self.root)
                elif name == "fifo":
                    stage.parent.mkdir(parents=True, exist_ok=True)
                    os.mkfifo(stage)
                elif name == "kind-mismatch":
                    stage.parent.mkdir(parents=True, exist_ok=True)
                    stage.write_bytes(b"wrong-kind")
                elif name == "concurrent-target":
                    stage.touch()
                    target.write_bytes(b"concurrent")
                elif name == "path-mismatch":
                    actual = self.home / bootstrap._copy_staging_relative_path(relative)
                    actual.parent.mkdir(parents=True, exist_ok=True)
                    actual.touch()
                    stage = actual
                elif name == "already-building":
                    stage.touch()
                recovered, _ = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
                self.assertFalse(recovered)
                self.assertTrue(stage.exists() or stage.is_symlink())
                self.assertTrue(bootstrap._journal_path(self.home).exists())
                bootstrap._remove_journal(self.home)
                if stage.is_dir() and not stage.is_symlink():
                    stage.rmdir()
                elif stage.exists() or stage.is_symlink():
                    stage.unlink()
                if target.exists() or target.is_symlink():
                    target.unlink()

    def test_venv_build_callback_baseexception_preserves_unhashed_stage(self):
        class HardCrash(BaseException):
            pass

        lock = bootstrap._load_lock()
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [
                {
                    "id": "venv:allinluna", "kind": "venv", "action": "publish", "state": "started",
                    "relative_path": "venvs/allinluna", "staging_relative_path": "venvs/.guardian-venv-allinluna",
                    "sha256": None, "commit": None, "selector": None, "device": None, "inode": None,
                },
                {
                    "id": "receipt", "kind": "receipt", "action": "write", "state": "intent",
                    "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(), "sha256": None, "commit": None, "selector": None,
                },
            ],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))

        def persist(staging_relative, identity, state, stage_hash):
            step = next(item for item in journal["steps"] if item["id"] == "venv:allinluna")
            step.update({"staging_relative_path": staging_relative, "device": identity["device"], "inode": identity["inode"], "state": state})
            if stage_hash is not None:
                step["sha256"] = stage_hash
            journal["generation"] += 1
            self.assertIsNone(bootstrap._write_journal(self.home, journal))
            if state == "building":
                raise HardCrash()

        component = {"name": "allinluna", "status": "planned"}
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=component), \
             mock.patch.object(bootstrap, "_allinluna_python", return_value=pathlib.Path("/usr/bin/python3")), \
             mock.patch.object(bootstrap.shutil, "rmtree", side_effect=HardCrash()):
            with self.assertRaises(HardCrash):
                bootstrap._allinluna_install(lock, self.home, None, persist)
        self.assertTrue((self.home / "venvs" / ".guardian-venv-allinluna").exists())
        recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, lock, self.guardian_ref, self.git)
        self.assertFalse(recovered)
        self.assertIn("ownership", reason)
        self.assertTrue((self.home / "venvs" / ".guardian-venv-allinluna").exists())
        self.assertTrue(bootstrap._journal_path(self.home).exists())

    def test_publishing_staging_drift_is_fail_closed(self):
        stage, target, _, _, journal = self._publish_crash_journal("file", "agents/staging-drift.txt", "publishing")
        stage.write_bytes(b"drift")
        recovered, _ = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertFalse(recovered)
        self.assertTrue(stage.exists())
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        bootstrap._remove_journal(self.home)
        stage.unlink()

    def test_nonempty_unhashed_venv_stage_is_preserved_during_recovery(self):
        stage, target, identity, _expected, journal = self._publish_crash_journal(
            "venv", "venvs/unhashed-recovery", "building"
        )
        step = next(item for item in journal["steps"] if item["id"] == "venv:allinluna")
        step["sha256"] = None
        journal["generation"] += 1
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        before = (stage.stat().st_ino, bootstrap._tree_hash(stage))
        self.assertFalse(bootstrap._recover_started_publish(self.home, step))
        self.assertTrue(stage.exists())
        self.assertEqual((stage.stat().st_ino, bootstrap._tree_hash(stage)), before)
        self.assertTrue(target.parent.exists())

    def test_target_same_hash_different_inode_is_preserved_and_closed(self):
        stage, target, identity, _, journal = self._publish_crash_journal("file", "agents/same-bytes.txt", "publishing")
        content = stage.read_bytes()
        replacement = target.parent / ".concurrent-target"
        replacement.write_bytes(content)
        stage.unlink()
        replacement.rename(target)
        self.assertNotEqual(target.stat().st_ino, identity["inode"])
        recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertEqual(target.read_bytes(), content)
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_file_and_venv_after_publish_crash_remove_only_transaction_inode(self):
        for kind, relative in (("file", "agents/crash-after.txt"), ("venv", "venvs/crash-after")):
            with self.subTest(kind=kind):
                stage, target, identity, expected, journal = self._publish_crash_journal(kind, relative, "publishing")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(stage, target)
                recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
                self.assertTrue(recovered, reason)
                self.assertFalse(target.exists())
                self.assertFalse(bootstrap._journal_path(self.home).exists())

                stage, target, identity, expected, journal = self._publish_crash_journal(kind, relative, "publishing")
                other = target.parent / ".concurrent-publish"
                if kind == "file":
                    other.write_bytes(b"publish-crash")
                else:
                    other.mkdir()
                    (other / "bin").mkdir()
                    (other / "bin" / "python").write_bytes(b"publish-crash")
                os.unlink(stage) if stage.is_file() else shutil.rmtree(stage)
                os.rename(other, target)
                self.assertNotEqual(target.stat().st_ino, identity["inode"])
                recovered, _ = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
                self.assertTrue(recovered)
                self.assertTrue(target.exists())
                self.assertFalse(stage.exists())
                self.assertFalse(bootstrap._journal_path(self.home).exists())
                shutil.rmtree(target) if target.is_dir() else target.unlink()

    def test_content_tree_digest_uses_record_prefix(self):
        tree = self.home / "tree"
        tree.mkdir()
        (tree / "tool").write_bytes(b"abc")
        (tree / "tool").chmod(0o700)
        proof = bootstrap._content_tree_proof(tree, self.home)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["tree_sha256"], "89a1fb4c0366c6b73d4ccc76b0e2d36c255b461a13d820935611d4f6d28236d1")

    def test_content_tree_excludes_only_root_git_and_keeps_nested_git(self):
        tree = self.home / "nested-git-tree"
        (tree / ".git").mkdir(parents=True)
        (tree / ".git" / "root-config").write_text("ignored", encoding="utf-8")
        (tree / "package" / ".git").mkdir(parents=True)
        (tree / "package" / ".git" / "nested-config").write_text("visible", encoding="utf-8")
        proof = bootstrap._content_tree_proof(tree, self.home)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["files"], 1)
        self.assertEqual(proof["directories"], 2)
        self.assertEqual(proof["file_bytes"], len(b"visible"))

    def test_content_tree_entry_limit_is_enforced_during_traversal(self):
        tree = self.home / "bounded-tree"
        tree.mkdir()
        for name in ("a", "b", "c"):
            (tree / name).write_text(name, encoding="utf-8")
        old_limit = bootstrap.MAX_PLUGIN_TREE_ENTRIES
        bootstrap.MAX_PLUGIN_TREE_ENTRIES = 2
        try:
            self.assertIsNone(bootstrap._content_tree_proof(tree, self.home))
        finally:
            bootstrap.MAX_PLUGIN_TREE_ENTRIES = old_limit

    def test_content_tree_entry_limit_stops_scandir_at_first_over_limit_candidate(self):
        tree = self.home / "scandir-boundary-tree"
        tree.mkdir()
        names = [f"candidate-{index}" for index in range(5)]
        for name in names:
            (tree / name).write_text(name, encoding="utf-8")
        consumed = []

        class Entry:
            def __init__(self, name):
                self.name = name

        class Entries:
            def __init__(self, values):
                self.values = iter(values)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def __iter__(self):
                return self

            def __next__(self):
                name = next(self.values)
                consumed.append(name)
                return Entry(name)

        def bounded_scandir(directory_fd):
            del directory_fd
            return Entries(names)

        old_limit = bootstrap.MAX_PLUGIN_TREE_ENTRIES
        bootstrap.MAX_PLUGIN_TREE_ENTRIES = 2
        try:
            with mock.patch.object(bootstrap.os, "scandir", side_effect=bounded_scandir):
                self.assertIsNone(bootstrap._content_tree_proof(tree, self.home))
        finally:
            bootstrap.MAX_PLUGIN_TREE_ENTRIES = old_limit
        self.assertEqual(consumed, names[:3])
        self.assertNotIn(names[3], consumed)
        self.assertNotIn(names[4], consumed)

    def test_tree_hash_at_enforces_entry_byte_and_depth_limits(self):
        tree = self.home / "bounded-entry-tree"
        tree.mkdir()
        (tree / "a").write_bytes(b"a")
        (tree / "b").write_bytes(b"b")
        descriptor = os.open(str(self.home), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            old_entries = bootstrap.MAX_PLUGIN_TREE_ENTRIES
            bootstrap.MAX_PLUGIN_TREE_ENTRIES = 2
            try:
                self.assertIsNone(bootstrap._tree_hash_at(descriptor, "bounded-entry-tree"))
            finally:
                bootstrap.MAX_PLUGIN_TREE_ENTRIES = old_entries
        finally:
            os.close(descriptor)

        payload = self.home / "bounded-byte-file"
        payload.write_bytes(b"0123456789")
        descriptor = os.open(str(self.home), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            old_bytes = bootstrap.MAX_PLUGIN_TREE_BYTES
            bootstrap.MAX_PLUGIN_TREE_BYTES = 4
            try:
                self.assertIsNone(bootstrap._tree_hash_at(descriptor, "bounded-byte-file"))
            finally:
                bootstrap.MAX_PLUGIN_TREE_BYTES = old_bytes
        finally:
            os.close(descriptor)

        deep = self.home / "bounded-depth-tree" / "one" / "two"
        deep.mkdir(parents=True)
        (deep / "leaf").write_bytes(b"leaf")
        descriptor = os.open(str(self.home), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            old_depth = bootstrap.MAX_TREE_DEPTH
            bootstrap.MAX_TREE_DEPTH = 1
            try:
                self.assertIsNone(bootstrap._tree_hash_at(descriptor, "bounded-depth-tree"))
            finally:
                bootstrap.MAX_TREE_DEPTH = old_depth
        finally:
            os.close(descriptor)

    def test_tree_hash_at_rejects_same_name_replacement_after_read(self):
        tree = self.home / "replacement-tree"
        tree.mkdir()
        entry = tree / "entry"
        entry.write_bytes(b"original")
        replacement = tree / "replacement"
        replacement.write_bytes(b"replacement")
        descriptor = os.open(str(tree), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        original_stat = bootstrap.os.stat
        calls = 0

        def racing_stat(path, *args, **kwargs):
            nonlocal calls
            if kwargs.get("dir_fd") == descriptor and path == "entry":
                calls += 1
                if calls == 2:
                    entry.rename(tree / "original-entry")
                    replacement.rename(entry)
            return original_stat(path, *args, **kwargs)

        try:
            with mock.patch.object(bootstrap.os, "stat", side_effect=racing_stat):
                self.assertIsNone(bootstrap._tree_hash_at(descriptor, "entry"))
            self.assertEqual(calls, 2)
        finally:
            os.close(descriptor)

    def test_safe_program_requires_trusted_nonwritable_executable(self):
        program = self.home / "trusted-program"
        program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        program.chmod(0o700)
        self.assertEqual(bootstrap._safe_program(program), program.resolve())
        for mode in (0o720, 0o702):
            with self.subTest(mode=oct(mode)):
                program.chmod(mode)
                self.assertIsNone(bootstrap._safe_program(program))
        program.chmod(0o700)
        owner = program.stat().st_uid
        with mock.patch.object(bootstrap.os, "getuid", return_value=owner + 1):
            self.assertIsNone(bootstrap._safe_program(program))

    def test_main_stdout_is_explicit_public_projection(self):
        receipt = bootstrap._base_receipt("check")
        receipt.update({
            "status": "changes-required",
            "guardian_ref": "a" * 40,
            "guardian_provenance": str(self.root / "private-guardian"),
            "failure": "failed at " + str(self.home),
            "recovery": "rerun from " + str(self.root / "recovery"),
            "home_device": 1,
            "home_inode": 2,
            "owned_paths": [{
                "device": 3,
                "inode": 4,
                "relative_path": "agents/coder.toml",
                "sha256": "b" * 64,
            }],
            "owned_plugins": [{
                "selector": "allinluna@onebigmoon-codex-workflows",
                "relative_path": "plugins/allinluna",
                "sha": "c" * 40,
            }],
            "managed_targets": [{
                "relative_path": "agents/coder.toml",
                "device": 5,
                "inode": 6,
                "sha256": "d" * 64,
            }],
            "components": [{
                "name": "codex-plugin-command",
                "status": "conflict",
                "reason": "failed at " + str(self.home),
                "selector": "allinluna@onebigmoon-codex-workflows",
                "path_relative": "plugins/allinluna",
                "installed_sha": {"allinluna": "e" * 40},
                "installed_tree_sha256": {"allinluna": "f" * 64},
                "device": 7,
                "inode": 8,
                "installed": {"allinluna": True},
            }],
            "rollback": {
                "performed": True,
                "actions": [{
                    "path": "agents/coder.toml",
                    "selector": "allinluna@onebigmoon-codex-workflows",
                    "action": "removed",
                }],
            },
            "conflicts": [{
                "name": "receipt",
                "relative_path": "workflow-guardian/bootstrap-receipt.json",
                "reason": "unsafe at " + str(self.home),
            }],
            "nested": {"quarantine_device": 5, "quarantine_inode": 6},
        })
        output = io.StringIO()
        with mock.patch.object(bootstrap, "run", return_value=(receipt, 0)), mock.patch.object(bootstrap.sys, "stdout", output):
            self.assertEqual(bootstrap.main(["--check", "--codex-home", str(self.home), "--codex-bin", str(self.codex), "--git-bin", str(self.git)]), 0)
        rendered = json.loads(output.getvalue())
        self.assertEqual(rendered["schema"], bootstrap.PUBLIC_SCHEMA)
        self.assertEqual(rendered["projection"], "public-redacted")
        forbidden = re.compile(r"(?:path|relative_path|_relative$|sha|hash|digest|commit|selector|device|inode)", re.IGNORECASE)

        def assert_safe(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertIsNone(forbidden.search(key), key)
                    assert_safe(child)
            elif isinstance(value, list):
                for child in value:
                    assert_safe(child)
            elif isinstance(value, str):
                self.assertFalse(os.path.isabs(value), value)
                self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", value), value)
                self.assertNotIn(str(self.home), value)

        assert_safe(rendered)
        self.assertEqual(rendered["rollback"], {"performed": True, "actions": [{"action": "removed"}]})
        self.assertEqual(rendered["components"][0]["installed"], {"allinluna": True})
        self.assertEqual(rendered["capability_status"], "configured-unverified")
        self.assertEqual(rendered["acceptance_level"], "preflight")
        self.assertEqual(rendered["existing_receipt"], "absent")
        self.assertEqual(rendered["conflict_summary"]["count"], 1)
        self.assertEqual(rendered["conflicts"], [{"name": "receipt", "reason": "receipt"}])
        self.assertEqual(receipt["home_device"], 1)
        self.assertEqual(receipt["owned_paths"][0]["sha256"], "b" * 64)

    def test_public_uninstall_projection_has_explicit_removed_status(self):
        receipt = bootstrap._base_receipt("uninstall")
        receipt["components"] = [{"name": "uninstall", "status": "present", "actions": []}]
        projected = bootstrap._public_receipt(receipt)
        self.assertEqual(projected["status"], "removed")
        self.assertEqual(projected["installation_status"], "removed")
        self.assertEqual(projected["acceptance_level"], "uninstalled")

    def test_public_uninstall_projection_distinguishes_no_receipt_and_conflict(self):
        no_receipt = bootstrap._base_receipt("uninstall")
        no_receipt["components"] = [{"name": "uninstall", "status": "skipped", "reason": "no valid bootstrap receipt"}]
        projected = bootstrap._public_receipt(no_receipt)
        self.assertEqual(projected["status"], "not-installed")
        self.assertEqual(projected["installation_status"], "not-installed")
        self.assertEqual(projected["acceptance_level"], "uninstalled")

        conflict = bootstrap._base_receipt("uninstall")
        conflict["components"] = [{"name": "uninstall", "status": "conflict", "reason": "receipt-owned state remains"}]
        projected = bootstrap._public_receipt(conflict)
        self.assertEqual(projected["status"], "conflict")
        self.assertEqual(projected["installation_status"], "conflict")
        self.assertEqual(projected["acceptance_level"], "preflight")
        self.assertEqual(projected["conflict_summary"], {"count": 1, "categories": ["receipt"]})
        self.assertEqual(projected["conflicts"], [{"name": "uninstall", "reason": "receipt"}])

    def test_darwin_execution_requires_launchspec_and_revalidates_identity(self):
        program = self.root / "darwin-program"
        program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        program.chmod(0o700)
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
            self.assertEqual(bootstrap._run_argv([str(program)], self.home, self.root), (-1, "", ""))
            with mock.patch.object(bootstrap, "_native_macho", return_value=True):
                spec = bootstrap._launch_spec(program, "test")
            self.assertIsNotNone(spec)
            with mock.patch.object(bootstrap, "_native_macho", return_value=True):
                self.assertEqual(bootstrap._run_argv([spec], self.home, self.root)[0], 0)
            replacement = self.root / "replacement"
            replacement.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            replacement.chmod(0o700)
            program.unlink()
            replacement.rename(program)
            with mock.patch.object(bootstrap, "_native_macho", return_value=True):
                self.assertEqual(bootstrap._run_argv([spec], self.home, self.root), (-1, "", ""))

    def test_managed_venv_python_uses_anchored_trust_and_rejects_replacement(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            home = pathlib.Path(temporary) / "codex-home"
            python = home / "venvs" / "allinluna" / "bin" / "python"
            python.parent.mkdir(parents=True, mode=0o700)
            for parent in (home, home / "venvs", home / "venvs" / "allinluna", python.parent):
                parent.chmod(0o700)
            canary = home / "canary"
            python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python.chmod(0o700)
            with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
                bootstrap, "_native_macho", return_value=True
            ):
                spec = bootstrap._python_launch_spec(python, home, venv=True)
                self.assertIsNotNone(spec)
                self.assertEqual(bootstrap._python_launch_spec(python, home, venv=False), None)
                self.assertEqual(bootstrap._run_argv([spec, "--version"], home, self.root)[0], 0)
                replacement = python.with_name("replacement")
                replacement.write_text("#!/bin/sh\necho replaced > %s\n" % canary, encoding="utf-8")
                replacement.chmod(0o700)
                python.unlink()
                replacement.rename(python)
                self.assertEqual(bootstrap._run_argv([spec, "--version"], home, self.root), (-1, "", ""))
                self.assertFalse(canary.exists())
                old_bin = python.parent.with_name("bin-old")
                python.parent.rename(old_bin)
                python.parent.mkdir(mode=0o700)
                python.write_text("#!/bin/sh\necho parent-replaced > %s\n" % canary, encoding="utf-8")
                python.chmod(0o700)
                self.assertEqual(bootstrap._run_argv([spec, "--version"], home, self.root), (-1, "", ""))
                self.assertFalse(canary.exists())

    def test_managed_venv_python_rejects_unsafe_internal_parent_and_setid(self):
        home = self.home
        python = home / "venvs" / "allinluna" / "bin" / "python"
        python.parent.mkdir(parents=True, mode=0o700)
        for parent in (home / "venvs", home / "venvs" / "allinluna", python.parent):
            parent.chmod(0o700)
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o700)
        python.parent.chmod(0o777)
        with mock.patch.object(bootstrap.platform, "system", return_value="Linux"):
            self.assertIsNone(bootstrap._python_launch_spec(python, home, venv=True))
        python.parent.chmod(0o700)
        python.chmod(0o4700)
        with mock.patch.object(bootstrap.platform, "system", return_value="Linux"):
            self.assertIsNone(bootstrap._python_launch_spec(python, home, venv=True))

    def test_darwin_full_flow_rejects_ambient_path_poison_and_uses_launchspecs(self):
        poison_dir = self.root / "ambient-poison"
        poison_dir.mkdir(mode=0o700)
        poison_canary = self.root / "ambient-poison-fired"
        argv0_log = self.root / "codex-argv0.log"
        for name in ("codex", "git", "python", "pip", "node", "npm"):
            poison = poison_dir / name
            poison.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' {shlex.quote(name)} >> {shlex.quote(str(poison_canary))}\n"
                "exit 91\n",
                encoding="utf-8",
            )
            poison.chmod(0o700)

        installed = self._entry(
            "codex-workflow-guardian",
            {"source": "local", "path": str(self.guardian_root)},
        )
        available = [
            self._entry(
                "allinluna",
                {
                    "source": "git-subdir",
                    "url": "https://github.com/zenx0x/allinluna.git",
                    "path": "./plugins/allinluna",
                    "sha": "723088a7c0d7342f077ad675c6ea72d7e3996536",
                },
                installed=False,
                enabled=False,
            ),
            self._entry(
                "ponytail",
                {
                    "source": "url",
                    "url": "https://github.com/DietrichGebert/ponytail.git",
                    "sha": "2ed6c52c9d7e5e56942508591085fd45dea277d3",
                },
                installed=False,
                enabled=False,
            ),
        ]
        installed_payload = json.dumps({"installed": [installed], "available": []})
        available_payload = json.dumps({"installed": [], "available": available})
        marketplace_payload = json.dumps({
            "marketplaces": [{
                "name": "onebigmoon-codex-workflows",
                "root": str(self.guardian_root),
                "marketplaceSource": {
                    "sourceType": "git",
                    "source": "https://github.com/OneBigMoon/codex-subagent-reconciler",
                },
            }],
        })
        codex = self.root / "trusted-codex"
        codex.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$0\" >> {shlex.quote(str(argv0_log))}\n"
            "for tool in codex git python pip node npm; do\n"
            "  \"$tool\" /dev/null >/dev/null 2>&1 || true\n"
            "done\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  printf '%s\\n' 'codex-cli 0.146.0'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"list\" ]; then\n"
            f"  if [ \"$3\" = \"--available\" ]; then printf '%s\\n' {shlex.quote(available_payload)}; else printf '%s\\n' {shlex.quote(installed_payload)}; fi\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ]; then\n"
            f"  printf '%s\\n' {shlex.quote(marketplace_payload)}\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        codex.chmod(0o700)
        original_run = bootstrap._run_argv
        seen = []

        def capture(argv, *args, **kwargs):
            self.assertTrue(argv)
            launch = argv[0]
            self.assertIsInstance(launch, bootstrap.LaunchSpec)
            self.assertTrue(launch.path.is_absolute())
            self.assertEqual(launch.path, pathlib.Path(os.path.realpath(launch.path)))
            seen.append(launch)
            return original_run(argv, *args, **kwargs)

        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), \
             mock.patch.object(bootstrap, "_native_macho", return_value=True), \
             mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True), \
             mock.patch.object(bootstrap, "_run_argv", side_effect=capture), \
             mock.patch.dict(os.environ, {"PATH": str(poison_dir)}, clear=False):
            receipt, code = bootstrap.run(
                "check",
                self.home,
                str(codex),
                guardian_ref=self.guardian_ref,
                git_bin=str(self.git),
            )
        self.assertGreater(len(seen), 0)
        self.assertTrue(argv0_log.exists())
        self.assertTrue(all(line == str(codex.resolve()) for line in argv0_log.read_text().splitlines()))
        self.assertFalse(poison_canary.exists())
        self.assertIn(receipt["status"], ("changes-required", "ready"), receipt)

    def test_darwin_program_trust_rejects_world_group_acl_and_setid(self):
        program = self.root / "trusted-program"
        program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap, "_has_acl", return_value=False
        ):
            program.chmod(0o700)
            self.assertIsNotNone(bootstrap._safe_program(program))
            program.chmod(0o702)
            self.assertIsNone(bootstrap._safe_program(program))
            program.chmod(0o720)
            self.assertIsNone(bootstrap._safe_program(program))
            program.chmod(0o4700)
            self.assertIsNone(bootstrap._safe_program(program))
            program.chmod(0o700)
            with mock.patch.object(bootstrap, "_has_acl", return_value=True):
                self.assertIsNone(bootstrap._safe_program(program))
            with mock.patch.object(bootstrap, "_homebrew_prefix", return_value=True), mock.patch.object(
                bootstrap, "_admin_gid", return_value=program.stat().st_gid
            ):
                program.chmod(0o720)
                self.assertIsNotNone(bootstrap._safe_program(program))
            with mock.patch.object(bootstrap, "_homebrew_prefix", return_value=True), mock.patch.object(
                bootstrap, "_admin_gid", return_value=program.stat().st_gid + 1
            ):
                self.assertIsNone(bootstrap._safe_program(program))

    def test_darwin_acl_api_failure_is_untrusted(self):
        program = self.root / "acl-api-failure"
        program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        program.chmod(0o700)
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap.ctypes, "CDLL", side_effect=AttributeError("acl API unavailable")
        ):
            self.assertTrue(bootstrap._has_acl(program))
            self.assertIsNone(bootstrap._safe_program(program))

    def test_darwin_real_extended_acl_rejects_program_and_ancestor(self):
        if sys.platform != "darwin":
            self.skipTest("requires macOS extended ACL support")
        parent = self.root / "acl-parent"
        program = parent / "trusted-program"
        plain = self.root / "acl-plain"
        parent.mkdir()
        program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        program.chmod(0o700)
        plain.write_text("plain\n", encoding="utf-8")
        plain.chmod(0o600)
        chmod = "/bin/chmod"
        applied = []
        for target in (program, parent):
            result = subprocess.run(
                [chmod, "+a", "everyone deny write", str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                for previous in reversed(applied):
                    subprocess.run([chmod, "-a", "everyone deny write", str(previous)], check=False)
                self.skipTest("chmod +a unavailable: " + result.stderr.strip())
            applied.append(target)
        try:
            with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
                self.assertFalse(bootstrap._has_acl(plain))
                self.assertTrue(bootstrap._has_acl(program))
                self.assertIsNone(bootstrap._safe_program(program))
                self.assertTrue(bootstrap._has_acl(parent))
                self.assertIsNone(bootstrap._safe_program(program))
        finally:
            for target in reversed(applied):
                subprocess.run([chmod, "-a", "everyone deny write", str(target)], check=False)

    def _make_hoisted_codex_wrapper(self, architecture, label=""):
        platform_tag = "darwin-arm64" if architecture == "arm64" else "darwin-x64"
        prefix = self.root / ("codex-hoisted-" + architecture + label)
        main_root = prefix / "node_modules" / "@openai" / "codex"
        wrapper_target = main_root / "bin" / "codex.js"
        wrapper = prefix / "node_modules" / ".bin" / "codex"
        wrapper_target.parent.mkdir(parents=True)
        wrapper.parent.mkdir(parents=True)
        wrapper_target.write_text("#!/bin/sh\n", encoding="utf-8")
        wrapper_target.chmod(0o700)
        wrapper.symlink_to("../@openai/codex/bin/codex.js")
        (main_root / "package.json").write_text(
            json.dumps({"name": "@openai/codex", "version": bootstrap.CODEX_VERSION}),
            encoding="utf-8",
        )
        return prefix, main_root, platform_tag, wrapper

    def _write_hoisted_platform(self, platform_root, platform_tag, architecture):
        npm_cpu = "arm64" if architecture == "arm64" else "x64"
        vendor_arch = "aarch64-apple-darwin" if architecture == "arm64" else "x86_64-apple-darwin"
        native = platform_root / "vendor" / vendor_arch / "bin" / "codex"
        native.parent.mkdir(parents=True, exist_ok=True)
        native.write_bytes(b"native")
        native.chmod(0o700)
        (platform_root / "package.json").write_text(
            json.dumps({
                "name": "@openai/codex",
                "version": bootstrap.CODEX_VERSION + "-" + platform_tag,
                "os": ["darwin"],
                "cpu": [npm_cpu],
            }),
            encoding="utf-8",
        )
        return native

    def test_darwin_codex_npm_wrapper_resolves_only_pinned_native_package(self):
        root = self.root / "codex-npm"
        tag = "darwin-arm64" if bootstrap._current_architecture() == "arm64" else "darwin-x64"
        wrapper = root / "bin" / "codex"
        native = root / "node_modules" / "@openai" / ("codex-" + tag) / "bin" / "codex"
        wrapper.parent.mkdir(parents=True)
        native.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
        wrapper.chmod(0o700)
        native.write_text("native", encoding="utf-8")
        native.chmod(0o700)
        (root / "package.json").write_text(json.dumps({"name": "@openai/codex", "version": "0.146.0"}), encoding="utf-8")
        native_package = native.parent.parent / "package.json"
        native_package.write_text(json.dumps({"name": "@openai/codex-" + tag, "version": "0.146.0"}), encoding="utf-8")
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == native.resolve()
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            spec = bootstrap._codex_launch_spec(str(wrapper), self.home)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.path, native.resolve())
        native_package.write_text(json.dumps({"name": "@openai/codex-" + tag, "version": "0.145.0"}), encoding="utf-8")
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == native.resolve()
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            self.assertIsNone(bootstrap._codex_launch_spec(str(wrapper), self.home))

    def test_darwin_codex_npm_wrapper_resolves_real_vendor_layout_and_rejects_metadata(self):
        root = self.root / "codex-real-npm"
        architecture = bootstrap._current_architecture()
        platform_tag = "darwin-arm64" if architecture == "arm64" else "darwin-x64"
        npm_cpu = "arm64" if architecture == "arm64" else "x64"
        vendor_arch = "aarch64-apple-darwin" if architecture == "arm64" else "x86_64-apple-darwin"
        wrapper = root / "bin" / "codex"
        platform_root = root / "node_modules" / "@openai" / ("codex-" + platform_tag)
        native = platform_root / "vendor" / vendor_arch / "bin" / "codex"
        wrapper.parent.mkdir(parents=True)
        native.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
        wrapper.chmod(0o700)
        native.write_bytes(b"real-native")
        native.chmod(0o700)
        (root / "package.json").write_text(json.dumps({"name": "@openai/codex", "version": "0.146.0"}), encoding="utf-8")

        def platform_metadata(**overrides):
            value = {
                "name": "@openai/codex",
                "version": "0.146.0-" + platform_tag,
                "os": ["darwin"],
                "cpu": [npm_cpu],
            }
            value.update(overrides)
            return value

        metadata_path = platform_root / "package.json"
        metadata_path.write_text(json.dumps(platform_metadata()), encoding="utf-8")
        native_is_valid = lambda path: pathlib.Path(path) == native.resolve()
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap, "_native_macho", side_effect=native_is_valid
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            spec = bootstrap._codex_launch_spec(str(wrapper), self.home)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.path, native.resolve())

        for field, invalid in (
            ("name", "@openai/not-codex"),
            ("version", "0.146.0-darwin-other"),
            ("os", ["linux"]),
            ("cpu", ["x64" if architecture == "arm64" else "arm64"]),
        ):
            with self.subTest(field=field):
                metadata_path.write_text(json.dumps(platform_metadata(**{field: invalid})), encoding="utf-8")
                with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
                    bootstrap, "_native_macho", side_effect=native_is_valid
                ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
                    self.assertIsNone(bootstrap._codex_launch_spec(str(wrapper), self.home))

        # A native binary in the wrong vendor/architecture directory must not
        # become a fallback candidate.
        metadata_path.write_text(json.dumps(platform_metadata()), encoding="utf-8")
        wrong_arch = "x86_64-apple-darwin" if architecture == "arm64" else "aarch64-apple-darwin"
        wrong_native = platform_root / "vendor" / wrong_arch / "bin" / "codex"
        wrong_native.parent.mkdir(parents=True)
        wrong_native.write_bytes(b"wrong-arch")
        wrong_native.chmod(0o700)
        native.unlink()
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == native.resolve()
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            self.assertIsNone(bootstrap._codex_launch_spec(str(wrapper), self.home))

    def test_darwin_codex_npm_wrapper_resolves_intel_vendor_cpu_alias(self):
        root = self.root / "codex-intel-npm"
        wrapper = root / "bin" / "codex"
        platform_root = root / "node_modules" / "@openai" / "codex-darwin-x64"
        native = platform_root / "vendor" / "x86_64-apple-darwin" / "bin" / "codex"
        wrapper.parent.mkdir(parents=True)
        native.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
        wrapper.chmod(0o700)
        native.write_bytes(b"intel-native")
        native.chmod(0o700)
        (root / "package.json").write_text(json.dumps({"name": "@openai/codex", "version": "0.146.0"}), encoding="utf-8")
        (platform_root / "package.json").write_text(
            json.dumps({"name": "@openai/codex", "version": "0.146.0-darwin-x64", "os": ["darwin"], "cpu": ["x64"]}),
            encoding="utf-8",
        )
        with mock.patch.object(bootstrap, "_current_architecture", return_value="x86_64"), mock.patch.object(
            bootstrap.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == native.resolve()
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            spec = bootstrap._codex_launch_spec(str(wrapper), self.home)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.path, native.resolve())

    def test_darwin_codex_npm_wrapper_resolves_exact_hoisted_vendor_for_arm_and_intel(self):
        for architecture in ("arm64", "x86_64"):
            with self.subTest(architecture=architecture):
                _, main_root, platform_tag, wrapper = self._make_hoisted_codex_wrapper(architecture)
                sibling_root = main_root.parent / ("codex-" + platform_tag)
                native = self._write_hoisted_platform(sibling_root, platform_tag, architecture)
                with mock.patch.object(bootstrap, "_current_architecture", return_value=architecture), mock.patch.object(
                    bootstrap.platform, "system", return_value="Darwin"
                ), mock.patch.object(
                    bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == native.resolve()
                ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
                    spec = bootstrap._codex_launch_spec(str(wrapper), self.home)
                self.assertIsNotNone(spec)
                self.assertEqual(spec.path, native.resolve())

    def test_darwin_codex_npm_wrapper_rejects_sibling_when_main_root_is_not_exact(self):
        architecture = "arm64"
        platform_tag = "darwin-arm64"
        main_root = self.root / "codex-nonexact" / "lib" / "@openai" / "codex"
        wrapper = main_root / "bin" / "codex"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
        wrapper.chmod(0o700)
        (main_root / "package.json").write_text(
            json.dumps({"name": "@openai/codex", "version": bootstrap.CODEX_VERSION}),
            encoding="utf-8",
        )
        sibling = main_root.parent / ("codex-" + platform_tag)
        native = self._write_hoisted_platform(sibling, platform_tag, architecture)
        with mock.patch.object(bootstrap, "_current_architecture", return_value=architecture), mock.patch.object(
            bootstrap.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == native.resolve()
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            self.assertIsNone(bootstrap._codex_launch_spec(str(wrapper), self.home))

    def test_darwin_codex_npm_wrapper_nested_invalid_never_falls_back_to_hoisted_sibling(self):
        architecture = "arm64"
        _, main_root, platform_tag, wrapper = self._make_hoisted_codex_wrapper(architecture)
        nested_root = main_root / "node_modules" / "@openai" / ("codex-" + platform_tag)
        nested_root.mkdir(parents=True)
        (nested_root / "package.json").write_text(
            json.dumps({"name": "@openai/not-codex", "version": bootstrap.CODEX_VERSION}),
            encoding="utf-8",
        )
        sibling_root = main_root.parent / ("codex-" + platform_tag)
        sibling_native = self._write_hoisted_platform(sibling_root, platform_tag, architecture)
        with mock.patch.object(bootstrap, "_current_architecture", return_value=architecture), mock.patch.object(
            bootstrap.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == sibling_native.resolve()
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            self.assertIsNone(bootstrap._codex_launch_spec(str(wrapper), self.home))

    def test_darwin_codex_npm_wrapper_rejects_hoisted_platform_root_and_native_escape(self):
        architecture = "arm64"
        platform_tag = "darwin-arm64"
        for escape_kind in ("platform-root", "native"):
            with self.subTest(escape_kind=escape_kind):
                _, main_root, _, wrapper = self._make_hoisted_codex_wrapper(architecture, "-" + escape_kind)
                sibling_root = main_root.parent / ("codex-" + platform_tag)
                if escape_kind == "platform-root":
                    outside_root = self.root / "outside-platform-root"
                    native = self._write_hoisted_platform(outside_root, platform_tag, architecture)
                    sibling_root.symlink_to(outside_root, target_is_directory=True)
                else:
                    native = self._write_hoisted_platform(sibling_root, platform_tag, architecture)
                    outside_native = self.root / "outside-native"
                    outside_native.write_bytes(b"escaped")
                    outside_native.chmod(0o700)
                    native.unlink()
                    native.symlink_to(outside_native)
                with mock.patch.object(bootstrap, "_current_architecture", return_value=architecture), mock.patch.object(
                    bootstrap.platform, "system", return_value="Darwin"
                ), mock.patch.object(
                    bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) == native.resolve()
                ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
                    self.assertIsNone(bootstrap._codex_launch_spec(str(wrapper), self.home))

    def test_darwin_codex_npm_wrapper_prefers_nested_platform_over_sibling(self):
        architecture = "arm64"
        _, main_root, platform_tag, wrapper = self._make_hoisted_codex_wrapper(architecture)
        nested_root = main_root / "node_modules" / "@openai" / ("codex-" + platform_tag)
        nested_native = self._write_hoisted_platform(nested_root, platform_tag, architecture)
        sibling_root = main_root.parent / ("codex-" + platform_tag)
        sibling_native = self._write_hoisted_platform(sibling_root, platform_tag, architecture)
        valid_natives = {nested_native.resolve(), sibling_native.resolve()}
        with mock.patch.object(bootstrap, "_current_architecture", return_value=architecture), mock.patch.object(
            bootstrap.platform, "system", return_value="Darwin"
        ), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path) in valid_natives
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            spec = bootstrap._codex_launch_spec(str(wrapper), self.home)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.path, nested_native.resolve())

    def test_macho_prefix_reader_accepts_large_thin_and_fat_files(self):
        thin = self.root / "large-thin-mach-o"
        thin.write_bytes(b"\xfe\xed\xfa\xcf" + struct.pack(">I", 0x0100000C) + b"\0" * 8192)
        fat = self.root / "large-fat-mach-o"
        fat_header = b"\xca\xfe\xba\xbf" + struct.pack(">I", 2)
        fat_entry = lambda cputype: struct.pack(">IIIII", cputype, 0, 4096, 4096, 2) + b"\0" * 12
        fat.write_bytes(fat_header + fat_entry(0x01000007) + fat_entry(0x0100000C) + b"\0" * 8192)
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
            self.assertEqual(bootstrap._macho_architectures(thin), {"arm64"})
            self.assertEqual(bootstrap._macho_architectures(fat), {"x86_64", "arm64"})

    def test_codex_launch_hash_limit_accepts_signed_native_over_plugin_tree_limit(self):
        native = self.root / "large-codex-native"
        with native.open("wb") as handle:
            handle.write(b"\xfe\xed\xfa\xcf" + struct.pack(">I", 0x0100000C))
            handle.truncate(bootstrap.MAX_PLUGIN_TREE_BYTES + 1)
        native.chmod(0o700)
        self.assertGreater(bootstrap.MAX_CODEX_EXECUTABLE_BYTES, bootstrap.MAX_PLUGIN_TREE_BYTES)
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap, "_native_macho", return_value=True
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            spec = bootstrap._launch_spec(native, "codex")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.path, native.resolve())

    def test_macho_prefix_reader_rejects_path_and_metadata_races(self):
        path = self.root / "racing-mach-o"
        path.write_bytes(b"\xfe\xed\xfa\xcf" + struct.pack(">I", 0x01000007) + b"\0" * 8192)
        replacement = self.root / "racing-mach-o-replacement"
        replacement.write_bytes(path.read_bytes())
        original_open = bootstrap.os.open

        def replace_after_open(name, flags, *args, **kwargs):
            descriptor = original_open(name, flags, *args, **kwargs)
            if pathlib.Path(name) == path:
                path.unlink()
                replacement.rename(path)
            return descriptor

        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap.os, "open", side_effect=replace_after_open
        ):
            self.assertIsNone(bootstrap._macho_architectures(path))

        path.write_bytes(b"\xfe\xed\xfa\xcf" + struct.pack(">I", 0x01000007) + b"\0" * 8192)
        original_read = bootstrap.os.read

        def truncate_during_read(descriptor, size):
            chunk = original_read(descriptor, size)
            if chunk:
                os.truncate(path, 1)
            return chunk

        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap.os, "read", side_effect=truncate_during_read
        ):
            self.assertIsNone(bootstrap._macho_architectures(path))

    def test_darwin_real_launchspecs_are_checked_when_host_tools_exist(self):
        if sys.platform != "darwin":
            self.skipTest("requires macOS Mach-O tools")
        wrapper_env = os.environ.get("GUARDIAN_REAL_CODEX_WRAPPER")
        explicit_wrapper = wrapper_env is not None
        if wrapper_env is not None:
            wrapper = pathlib.Path(wrapper_env)
            if not wrapper.is_absolute() or not wrapper.exists() or not os.access(wrapper, os.X_OK):
                self.fail(f"invalid GUARDIAN_REAL_CODEX_WRAPPER={wrapper_env!r}; expected an absolute executable path")
        else:
            wrapper = pathlib.Path("/opt/homebrew/bin/codex")
            if not wrapper.exists():
                wrapper = pathlib.Path("/usr/local/bin/codex")
            if not wrapper.exists():
                self.skipTest("native Codex npm wrapper is unavailable")
        git = pathlib.Path("/usr/bin/git")
        if explicit_wrapper and not git.exists():
            self.fail("/usr/bin/git is required for explicit GUARDIAN_REAL_CODEX_WRAPPER mode")
        if not git.exists():
            self.skipTest("/usr/bin/git is unavailable")
        python_candidates = [pathlib.Path(value) for value in bootstrap.MACOS_PYTHON_CANDIDATES if pathlib.Path(value).exists()]
        if explicit_wrapper and not python_candidates:
            self.fail("a Python >=3.11 candidate is required in explicit wrapper mode")
        if explicit_wrapper and not pathlib.Path("/usr/bin/codesign").exists():
            self.fail("/usr/bin/codesign is required in explicit wrapper mode")
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"):
            self.assertIsNotNone(bootstrap._git_launch_spec(git))
            if python_candidates:
                self.assertIsNotNone(bootstrap._python_launch_spec(python_candidates[0], self.home))
            spec = bootstrap._codex_launch_spec(str(wrapper), self.home, git)
        self.assertIsNotNone(spec)
        native = spec.path
        self.assertEqual(bootstrap.CODEX_TEAM_ID, "2DC432GLL2")
        real_home = self.root / "real-codex-home"
        real_cwd = self.root / "real-codex-cwd"
        real_home.mkdir(mode=0o700)
        real_cwd.mkdir(mode=0o700)
        code, stdout, stderr = bootstrap._run_argv([spec, "--version"], real_home, real_cwd)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout.strip(), "codex-cli 0.146.0")
        self.assertEqual(bootstrap._version_tuple(stdout), (0, 146, 0))

        # Keep the real positive probe read-only.  Exercise the symlink escape
        # on a disposable package-shaped copy, never inside the user's npm
        # installation directory.
        architecture = bootstrap._current_architecture()
        platform_tag = "darwin-arm64" if architecture == "arm64" else "darwin-x64"
        vendor_arch = "aarch64-apple-darwin" if architecture == "arm64" else "x86_64-apple-darwin"
        escape_root = self.root / "codex-real-escape-fixture"
        escape_wrapper = escape_root / "bin" / "codex"
        escape_native = escape_root / "node_modules" / "@openai" / ("codex-" + platform_tag) / "vendor" / vendor_arch / "bin" / "codex"
        escape_wrapper.parent.mkdir(parents=True)
        escape_native.parent.mkdir(parents=True)
        shutil.copy2(wrapper, escape_wrapper)
        (escape_root / "package.json").write_text(
            json.dumps({"name": "@openai/codex", "version": bootstrap.CODEX_VERSION}),
            encoding="utf-8",
        )
        (escape_native.parents[3] / "package.json").write_text(
            json.dumps({
                "name": "@openai/codex",
                "version": bootstrap.CODEX_VERSION + "-" + platform_tag,
                "os": ["darwin"],
                "cpu": ["arm64" if platform_tag == "darwin-arm64" else "x64"],
            }),
            encoding="utf-8",
        )
        escaped = self.root / "escaped-codex"
        escaped.write_bytes(b"escaped")
        escaped.chmod(0o700)
        escape_native.symlink_to(escaped)
        with mock.patch.object(bootstrap.platform, "system", return_value="Darwin"), mock.patch.object(
            bootstrap, "_native_macho", side_effect=lambda path: pathlib.Path(path).resolve() != escape_wrapper.resolve()
        ), mock.patch.object(bootstrap, "_verify_codex_signature", return_value=True):
            self.assertIsNone(bootstrap._codex_launch_spec(str(escape_wrapper), self.home))

    def test_lock_contains_canonical_allinluna_tree_vector(self):
        component = bootstrap._load_lock()["components"]["allinluna"]
        self.assertEqual(component["content_tree_algorithm"], bootstrap.PLUGIN_TREE_ALGORITHM)
        self.assertEqual(component["content_tree_sha256"], "914039551abc0cf67607cfd487940bcdcacced727fd47d8a7ddb5b8743c47b4f")
        self.assertEqual(
            tuple(component[key] for key in ("content_tree_entries", "content_tree_directories", "content_tree_files", "content_tree_symlinks", "content_tree_file_bytes")),
            (104, 16, 88, 0, 1380223),
        )

    def test_wheel_pin_requires_exact_safe_basename_and_url_basename(self):
        lock = bootstrap._load_lock()
        pypi = lock["components"]["allinluna"]["pypi"]
        wheel = pypi["wheel"]
        self.assertEqual(bootstrap._validated_wheel_filename(wheel, pypi), wheel["filename"])
        for filename, url in (
            ("../allinluna-2.0.0rc3-py3-none-any.whl", wheel["url"]),
            ("allinluna-2.0.0rc3-py3-none-any.whl.bak", wheel["url"]),
            ("other-2.0.0rc3-py3-none-any.whl", wheel["url"]),
            (wheel["filename"], wheel["url"].replace(wheel["filename"], "other.whl")),
            ("allinluna-2.0.0rc3-py3-none-any.whl", wheel["url"] + "?download=1"),
        ):
            mutated = dict(wheel, filename=filename, url=url)
            self.assertIsNone(bootstrap._validated_wheel_filename(mutated, pypi), (filename, url))

    def test_lock_stdout_projection_unknown_fields_and_schema_are_rejected(self):
        original = json.loads(bootstrap.LOCK_PATH.read_text(encoding="utf-8"))
        for mutation in (
            lambda value: value["receipts"]["stdout_projection"].update({"unexpected": True}),
            lambda value: value["receipts"]["stdout_projection"].update({"schema": "wrong/v1"}),
            lambda value: value["receipts"]["stdout_projection"].update({"public": False}),
        ):
            value = json.loads(json.dumps(original))
            mutation(value)
            path = self.root / "invalid-receipt-lock.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(mutation=mutation), mock.patch.object(bootstrap, "LOCK_PATH", path):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._load_lock()

    def test_lock_public_acceptance_level_is_exact_four_value_contract(self):
        value = bootstrap._load_lock()
        projection = value["receipts"]["stdout_projection"]
        expected = ["preflight", "installed", "uninstalled", "transaction-recovery"]
        self.assertEqual(projection["acceptance_levels"], expected)
        self.assertIn("acceptance_level preflight/installed/uninstalled/transaction-recovery", projection["contains"])
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["interface"]["capabilities"], ["Interactive", "Read", "Write", "Network"])
        for mutation in (
            lambda item: item["receipts"]["stdout_projection"].update({"acceptance_levels": ["preflight", "installed"]}),
            lambda item: item["receipts"]["stdout_projection"].update({"acceptance_levels": ["preflight", "installed", "uninstalled", "transaction-recovery", "other"]}),
            lambda item: item["receipts"]["stdout_projection"]["contains"].remove("acceptance_level preflight/installed/uninstalled/transaction-recovery"),
        ):
            invalid = json.loads(json.dumps(value))
            mutation(invalid)
            path = self.root / "invalid-acceptance-lock.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.subTest(mutation=mutation), mock.patch.object(bootstrap, "LOCK_PATH", path):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._load_lock()

    def test_lock_accepts_exact_guardian_bundled_skill_classification(self):
        value = bootstrap._load_lock()
        self.assertIsInstance(value["classifications"]["bundled-with-guardian-plugin"], str)
        for name in ("codex-workflow-guardian", "reconcile-codex-subagents", "setup-codex-workflow-guardian"):
            self.assertEqual(value["skills"][name]["classification"], "bundled-with-guardian-plugin")

    def test_lock_host_capability_is_detection_only_and_strictly_shaped(self):
        value = bootstrap._load_lock()
        host = value["host_capability"]
        self.assertEqual(host["classification"], "detect-then-separately-authorize")
        self.assertEqual(host["installation"], "not-installed-by-setup")
        self.assertEqual(
            host["dispatch_ownership"],
            {
                "pre_dispatch_unowned": {
                    "requires": ["no_durable_run", "fresh_zero_match", "no_issued_host_action"],
                    "result": "BOUNDED_NATIVE_MANUAL",
                    "preserve_owner": False,
                    "fallback_allowed": True,
                },
                "relay_unproven_owner_preserved": {
                    "requires": ["active_run_or_issued_host_action", "relay_unproven"],
                    "result": "ACTION_RELAY_REQUIRED",
                    "preserve_owner": True,
                    "fallback_allowed": False,
                },
                "exact_tool_absent_owner_preserved": {
                    "requires": ["active_run_or_issued_host_action", "fresh_exact_tool_absence"],
                    "result": "HOST_CAPABILITY_BLOCKED",
                    "preserve_owner": True,
                    "fallback_allowed": False,
                },
            },
        )
        self.assertEqual(host["portable_full_apply"], "installed only; no HostAdapter callable-capability claim")
        self.assertEqual(set(host["support_matrix"]), {"codex_desktop", "codex_cli", "ide"})
        for entry in host["support_matrix"].values():
            self.assertEqual(entry["status_without_fresh_receipt"], "configured-unverified")
            self.assertTrue(entry["permission"].startswith("required"))
        for mutation in (
            lambda item: item["host_capability"].update({"unexpected": True}),
            lambda item: item["host_capability"].update({"classification": "available"}),
            lambda item: item["host_capability"].update({"unavailable_action": "ACTION_RELAY_REQUIRED"}),
            lambda item: item["host_capability"].pop("dispatch_ownership"),
            lambda item: item["host_capability"]["dispatch_ownership"].update({"unexpected": True}),
            lambda item: item["host_capability"]["dispatch_ownership"].pop("relay_unproven_owner_preserved"),
            lambda item: item["host_capability"]["dispatch_ownership"]["pre_dispatch_unowned"].update({"fallback": "native-manual"}),
            lambda item: item["host_capability"]["dispatch_ownership"]["pre_dispatch_unowned"].update({"fallback_allowed": False}),
        ):
            invalid = json.loads(json.dumps(value))
            mutation(invalid)
            path = self.root / "invalid-host-capability-lock.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.subTest(mutation=mutation), mock.patch.object(bootstrap, "LOCK_PATH", path):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._load_lock()

    def test_lock_zero_match_evidence_is_exact_and_fail_closed(self):
        value = bootstrap._load_lock()
        evidence = value["host_capability"]["zero_match_evidence"]
        self.assertEqual(evidence["schema"], "guardian-zero-match/v1")
        self.assertEqual(
            evidence["canonical_store"],
            "<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/allinluna-runtime.db",
        )
        self.assertEqual(
            evidence["run_identity_registry"],
            {
                "path": "<canonical GUARDIAN_CODEX_HOME>/workflow-guardian/run-identities/<intent_id>.json",
                "protocol": "guardian-run/v1",
                "entry": "one exact intent_id",
                "safe_path": True,
                "no_symlink_following": True,
                "directory_mode": "0700",
                "mode": "0600",
                "atomic_persist_before_dispatch": True,
            },
        )
        self.assertEqual(evidence["runtime_available_proof"]["integrity_failure"], "PROTOCOL_INTEGRITY_FAILURE")
        self.assertEqual(evidence["core_only_proof"]["result"], "FRESH_ZERO_MATCH")
        self.assertEqual(evidence["blocked_proof"]["result"], "OWNER_LOOKUP_BLOCKED")
        self.assertFalse(evidence["blocked_proof"]["fallback_allowed"])
        for mutation in (
            lambda item: item["host_capability"]["zero_match_evidence"].update({"unexpected": True}),
            lambda item: item["host_capability"]["zero_match_evidence"].pop("core_only_proof"),
            lambda item: item["host_capability"]["zero_match_evidence"]["run_identity_registry"].update({"mode": "0644"}),
            lambda item: item["host_capability"]["zero_match_evidence"]["core_only_proof"].update({"exactly_once": True}),
            lambda item: item["host_capability"]["zero_match_evidence"]["blocked_proof"].update({"fallback_allowed": True}),
            lambda item: item["host_capability"]["zero_match_evidence"]["runtime_available_proof"].update({"integrity_failure": "ignored"}),
        ):
            invalid = json.loads(json.dumps(value))
            mutation(invalid)
            path = self.root / "invalid-zero-match-lock.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.subTest(mutation=mutation), mock.patch.object(bootstrap, "LOCK_PATH", path):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap._load_lock()

    def test_lock_profile_graph_rejects_missing_inherited_component_profile(self):
        value = json.loads(bootstrap.LOCK_PATH.read_text(encoding="utf-8"))
        value["components"]["codex_cli"]["required_profiles"].remove("machine-integration")
        path = self.root / "invalid-lock.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with mock.patch.object(bootstrap, "LOCK_PATH", path):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._load_lock()

    def test_lock_profile_graph_rejects_cycle(self):
        value = json.loads(bootstrap.LOCK_PATH.read_text(encoding="utf-8"))
        value["profiles"]["core"]["extends"] = ["portable-full"]
        path = self.root / "invalid-lock.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with mock.patch.object(bootstrap, "LOCK_PATH", path):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._load_lock()

    def test_lock_profile_graph_rejects_unknown_parent(self):
        value = json.loads(bootstrap.LOCK_PATH.read_text(encoding="utf-8"))
        value["profiles"]["core"]["extends"] = ["unknown-profile"]
        path = self.root / "invalid-lock.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with mock.patch.object(bootstrap, "LOCK_PATH", path):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._load_lock()

    def test_lock_profile_graph_rejects_unknown_requirement_token(self):
        for profile_name in ("core", "account-dependent"):
            with self.subTest(profile=profile_name):
                value = json.loads(bootstrap.LOCK_PATH.read_text(encoding="utf-8"))
                value["profiles"][profile_name]["requires"].append("typo-component")
                path = self.root / f"invalid-{profile_name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with mock.patch.object(bootstrap, "LOCK_PATH", path):
                    with self.assertRaises(bootstrap.BootstrapError):
                        bootstrap._load_lock()

    def test_lock_roles_profiles_match_profile_closure(self):
        value = json.loads(bootstrap.LOCK_PATH.read_text(encoding="utf-8"))
        value["roles"]["profiles"] = []
        path = self.root / "invalid-roles.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with mock.patch.object(bootstrap, "LOCK_PATH", path):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._load_lock()

    def test_home_is_explicit_private_owned_and_environment_is_ignored(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "wrong")}, clear=False):
            receipt, code = self._run_on_darwin("check", self.home, str(self.codex), guardian_ref=self.guardian_ref, git_bin=str(self.git))
        self.assertNotEqual(receipt["components"][0]["name"], "codex-home")
        self.assertNotEqual(code, 2)
        missing, missing_code = self._run_on_darwin("check", self.root / "missing", str(self.codex), guardian_ref=self.guardian_ref, git_bin=str(self.git))
        self.assertEqual(missing_code, 2)
        self.assertEqual(missing["components"][0]["name"], "codex-home")

    def test_receipt_nested_unknown_fields_are_rejected(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0)
        plugin = next(item for item in receipt["components"] if item["name"] == "codex-plugin-command")
        plugin["hits"][0]["spoof"] = "unexpected"
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._assert_private_receipt(receipt)

    def test_broad_and_ancestor_symlink_homes_refuse(self):
        broad, broad_code = self._run_on_darwin("check", pathlib.Path("/"), str(self.codex), guardian_ref=self.guardian_ref, git_bin=str(self.git))
        self.assertEqual(broad_code, 2)
        self.assertEqual(broad["components"][0]["name"], "codex-home")
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        link_parent = self.root / "link-parent"
        link_parent.symlink_to(outside, target_is_directory=True)
        linked, linked_code = self._run_on_darwin("check", link_parent / "home", str(self.codex), guardian_ref=self.guardian_ref, git_bin=str(self.git))
        self.assertEqual(linked_code, 2)
        self.assertEqual(linked["components"][0]["name"], "codex-home")

    def test_exact_child_env_and_python_isolated_probe(self):
        probe = self.root / "env-probe"
        probe.write_text("#!/usr/bin/env python3\nimport json, os, sys\nprint(json.dumps({'env': dict(os.environ), 'isolated': sys.flags.isolated}))\n", encoding="utf-8")
        probe.chmod(0o700)
        with mock.patch.dict(os.environ, {"UNSAFE_BOOTSTRAP_TOKEN": "secret", "CODEX_HOME": "wrong", "PATH": os.environ.get("PATH", "")}, clear=False):
            code, stdout, _ = bootstrap._run_argv([str(probe)], self.home, self.root)
        self.assertEqual(code, 0)
        value = json.loads(stdout)
        self.assertNotIn("UNSAFE_BOOTSTRAP_TOKEN", value["env"])
        self.assertEqual(value["env"]["CODEX_HOME"], str(self.home))
        seen = []
        with mock.patch.object(bootstrap, "_run_argv", side_effect=lambda argv, *args, **kwargs: (seen.append(list(argv)) or (0, "Python 3.11.0\n", ""))):
            bootstrap._allinluna_python({}, None, self.home)
        self.assertTrue(all("-I" in argv for argv in seen))

    def test_combined_output_overflow_kills_child_before_marker(self):
        marker = self.root / "late-marker"
        started = self.root / "overflow-started"
        script = self.root / "overflow"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys, time\n"
            "pathlib.Path(%r).write_text('started')\n"
            "sys.stderr.write('x' * 1000000); sys.stderr.flush()\n"
            "time.sleep(3.0); pathlib.Path(%r).write_text('late')\n"
            % (str(started), str(marker)),
            encoding="utf-8",
        )
        script.chmod(0o700)
        old = bootstrap.MAX_COMMAND_OUTPUT
        bootstrap.MAX_COMMAND_OUTPUT = 1024
        try:
            result = bootstrap._run_argv([str(script)], self.home, self.root, timeout=2)
        finally:
            bootstrap.MAX_COMMAND_OUTPUT = old
        self.assertEqual(result, (-1, "", ""))
        self.assertTrue(started.exists())
        time.sleep(3.8)
        self.assertFalse(marker.exists())

    def test_allinluna_runtime_rejects_requires_dist_dependencies(self):
        root = self.home / "venvs" / "allinluna"
        (root / "bin").mkdir(parents=True)
        for name in ("python", "allinluna"):
            path = root / "bin" / name
            path.write_text(name, encoding="utf-8")
            path.chmod(0o700)
        probe = {"python": [3, 11, 0], "name": "allinluna", "version": "2.0.0rc3", "dependencies": ["requests>=2"]}
        with mock.patch.object(bootstrap, "_run_argv", return_value=(0, json.dumps(probe), "")) as run:
            self.assertIsNone(bootstrap._verify_allinluna_runtime(root, self.home, "2.0.0rc3"))
        self.assertIn("Requires-Dist", run.call_args.args[0][-1])

    def test_allinluna_runtime_accepts_empty_requires_dist_dependencies(self):
        root = self.home / "venvs" / "allinluna"
        (root / "bin").mkdir(parents=True)
        for name in ("python", "allinluna"):
            path = root / "bin" / name
            path.write_text(name, encoding="utf-8")
            path.chmod(0o700)
        probe = {"python": [3, 11, 0], "name": "allinluna", "version": "2.0.0rc3", "dependencies": []}
        with mock.patch.object(bootstrap, "_run_argv", return_value=(0, json.dumps(probe), "")):
            self.assertEqual(bootstrap._verify_allinluna_runtime(root, self.home, "2.0.0rc3"), [])

    def test_real_git_clean_positive_and_negative_matrix(self):
        ok, reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "verified")
        nested_git = self.guardian_root / "nested" / ".git"
        nested_git.mkdir(parents=True)
        (nested_git / "config").write_text("nested", encoding="utf-8")
        nested_ok, nested_reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertFalse(nested_ok)
        self.assertEqual(nested_reason, "Guardian checkout is not clean")
        shutil.rmtree(self.guardian_root / "nested")
        dirty = self.guardian_root / "dirty.txt"
        dirty.write_text("dirty")
        dirty_ok, dirty_reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertFalse(dirty_ok)
        self.assertEqual(dirty_reason, "Guardian checkout is not clean")
        dirty.unlink()
        ignored = self.guardian_root / "ignored.txt"
        (self.guardian_root / ".git" / "info" / "exclude").write_text("ignored.txt\n", encoding="utf-8")
        ignored.write_text("ignored")
        ignored_ok, ignored_reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertFalse(ignored_ok)
        self.assertEqual(ignored_reason, "Guardian checkout is not clean")
        ignored.unlink()
        origin_ok, origin_reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertTrue(origin_ok, origin_reason)
        self.assertEqual(origin_reason, "verified")
        subprocess.run([str(self.git), "-C", str(self.guardian_root), "remote", "set-url", "origin", "https://evil.invalid/repo.git"], check=True)
        origin_ok, origin_reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertFalse(origin_ok)
        self.assertEqual(origin_reason, "Git origin is not the canonical Guardian repository")

    def test_git_revalidation_does_not_rebaseline_replaced_root(self):
        valid, reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertTrue(valid, reason)
        root_key = str(bootstrap._absolute_lexical(self.guardian_root))
        old_root_proof = dict(bootstrap._VALIDATED_ROOT_PROOFS[root_key])
        replacement = self.root / "replacement-marketplace-root"
        moved_original = self.root / "original-marketplace-root"
        shutil.copytree(self.guardian_root, replacement)
        self.guardian_root.rename(moved_original)
        replacement.rename(self.guardian_root)
        try:
            replaced_valid, replaced_reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        finally:
            self.guardian_root.rename(replacement)
            moved_original.rename(self.guardian_root)
        self.assertFalse(replaced_valid, replaced_reason)
        self.assertEqual(bootstrap._VALIDATED_ROOT_PROOFS[root_key], old_root_proof)

    def test_git_replacement_ref_and_concealed_index_cannot_validate_tampered_content(self):
        head = subprocess.check_output(
            [str(self.git), "-C", str(self.guardian_root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        replacement_blob = subprocess.check_output(
            [str(self.git), "-C", str(self.guardian_root), "hash-object", "-w", "--stdin"],
            input=b"replacement-content\n",
        ).decode("ascii").strip()
        replacement_tree = subprocess.check_output(
            [str(self.git), "-C", str(self.guardian_root), "mktree"],
            input=(f"100644 blob {replacement_blob}\treplacement.txt\n").encode("utf-8"),
        ).decode("ascii").strip()
        replacement_commit = subprocess.check_output(
            [str(self.git), "-C", str(self.guardian_root), "commit-tree", replacement_tree],
            input=b"replacement\n",
        ).decode("ascii").strip()
        subprocess.run([str(self.git), "-C", str(self.guardian_root), "replace", head, replacement_commit], check=True)
        try:
            expected_tree = subprocess.check_output(
                [str(self.git), "-C", str(self.guardian_root), "--no-replace-objects", "cat-file", "-p", f"{head}^{{tree}}"],
                text=True,
            ).strip()
            observed_tree = bootstrap._git_output(
                self.git,
                self.home,
                self.guardian_root,
                ["cat-file", "-p", f"{head}^{{tree}}"],
            )
            self.assertEqual(observed_tree, expected_tree)
            valid, reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
            self.assertFalse(valid)
            self.assertEqual(reason, "Guardian checkout contains replacement refs")
        finally:
            subprocess.run([str(self.git), "-C", str(self.guardian_root), "replace", "-d", head], check=True)

        tracked = self.guardian_root / "skills" / "setup-codex-workflow-guardian" / "SKILL.md"
        original = tracked.read_bytes()
        for flag, clear_flag, marker in (
            ("--assume-unchanged", "--no-assume-unchanged", "h"),
            ("--skip-worktree", "--no-skip-worktree", "S"),
        ):
            with self.subTest(flag=flag):
                try:
                    relative = str(tracked.relative_to(self.guardian_root))
                    subprocess.run([str(self.git), "-C", str(self.guardian_root), "update-index", flag, relative], check=True)
                    tracked.write_bytes(original + b"tampered\n")
                    states = subprocess.check_output(
                        [str(self.git), "-C", str(self.guardian_root), "ls-files", "-v"],
                        text=True,
                    )
                    self.assertTrue(any(line.startswith(marker + " ") for line in states.splitlines()))
                    valid, reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
                    self.assertFalse(valid)
                    self.assertEqual(reason, "Guardian checkout contains concealed or non-normal index entries")
                finally:
                    subprocess.run([str(self.git), "-C", str(self.guardian_root), "update-index", clear_flag, relative], check=True)
                    tracked.write_bytes(original)

    def test_git_validation_ignores_local_fsmonitor_and_preserves_clean_dirty_results(self):
        marker = self.root / "fsmonitor-marker"
        hook = self.root / "fsmonitor-canary"
        hook.write_text(
            "#!/bin/sh\n"
            "printf '%%s' hit > %s\n"
            "printf '%%s\\n' canary-token\n"
            % shlex.quote(str(marker)),
            encoding="utf-8",
        )
        hook.chmod(0o700)
        subprocess.run([str(self.git), "-C", str(self.guardian_root), "config", "core.fsmonitor", str(hook)], check=True)

        subprocess.run(
            [str(self.git), "-C", str(self.guardian_root), "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertTrue(marker.exists())
        marker.unlink()

        ok, reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertTrue(ok, reason)
        self.assertFalse(marker.exists())

        dirty = self.guardian_root / "fsmonitor-dirty.txt"
        dirty.write_text("dirty", encoding="utf-8")
        self.assertFalse(bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)[0])
        self.assertFalse(marker.exists())

    def test_git_validation_does_not_execute_required_checkout_clean_filter(self):
        marker = self.root / "required-filter-fired"
        filter_script = self.guardian_root / "required-filter-canary"
        filter_script.write_text(
            "#!/bin/sh\n"
            "printf '%%s' hit > %s\n"
            "cat\n"
            % shlex.quote(str(marker)),
            encoding="utf-8",
        )
        filter_script.chmod(0o700)
        attributes = self.guardian_root / ".gitattributes"
        attributes.write_text(
            "skills/setup-codex-workflow-guardian/SKILL.md filter=required-canary\n",
            encoding="utf-8",
        )
        subprocess.run([str(self.git), "-C", str(self.guardian_root), "add", ".gitattributes", "required-filter-canary"], check=True)
        subprocess.run([str(self.git), "-C", str(self.guardian_root), "commit", "-qm", "required filter fixture"], check=True)
        ref = subprocess.check_output([str(self.git), "-C", str(self.guardian_root), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run([str(self.git), "-C", str(self.guardian_root), "config", "filter.required-canary.clean", str(filter_script)], check=True)
        subprocess.run([str(self.git), "-C", str(self.guardian_root), "config", "filter.required-canary.required", "true"], check=True)

        valid, reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, ref)
        self.assertTrue(valid, reason)
        self.assertFalse(marker.exists())

        subprocess.run(
            [str(self.git), "-C", str(self.guardian_root), "hash-object", "--path=skills/setup-codex-workflow-guardian/SKILL.md", "--stdin"],
            input=(self.guardian_root / "skills" / "setup-codex-workflow-guardian" / "SKILL.md").read_bytes(),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertTrue(marker.exists())
        marker.unlink()

    def test_git_validation_rejects_url_instead_of_rewrite_of_untrusted_origin(self):
        subprocess.run(
            [
                str(self.git),
                "-C",
                str(self.guardian_root),
                "remote",
                "set-url",
                "origin",
                "https://evil.invalid/codex-subagent-reconciler.git",
            ],
            check=True,
        )
        subprocess.run(
            [
                str(self.git),
                "-C",
                str(self.guardian_root),
                "config",
                "url.https://github.com/OneBigMoon/.insteadOf",
                "https://evil.invalid/",
            ],
            check=True,
        )
        rewritten = subprocess.check_output(
            [str(self.git), "-C", str(self.guardian_root), "remote", "get-url", "origin"],
            text=True,
        ).strip()
        self.assertEqual(rewritten, "https://github.com/OneBigMoon/codex-subagent-reconciler.git")
        self.assertFalse(bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)[0])

    def test_resolver_requires_real_marketplace_git(self):
        result, code = self._run("check")
        self.assertEqual(code, 1)
        bootstrap._assert_private_receipt(result)
        plugin = next(item for item in result["components"] if item["name"] == "codex-plugin-command")
        self.assertEqual(plugin["status"], "present")
        available_hits = [hit for hit in plugin["hits"] if hit["location"] == "available"]
        self.assertTrue(available_hits)
        self.assertTrue(all(not hit["installed"] and not hit["enabled"] for hit in available_hits))
        self._write_codex(corrupt=True)
        result, code = self._run("check")
        self.assertEqual(code, 1)
        self.assertEqual(next(item for item in result["components"] if item["name"] == "codex-plugin-command")["status"], "conflict")

    def test_apply_rejects_bootstrap_source_root_mismatch_before_plugin_add(self):
        self._write_codex()
        before = sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*"))
        wrong_script = self.root / "arbitrary-bootstrap.py"
        wrong_script.write_text("# foreign source\n", encoding="utf-8")
        with mock.patch.object(bootstrap, "SCRIPT_PATH", wrong_script), mock.patch.object(bootstrap, "_plugin_add") as add:
            result, code = self._run_on_darwin(
                "apply",
                self.home,
                str(self.codex),
                guardian_ref=self.guardian_ref,
                git_bin=str(self.git),
            )
        self.assertEqual(code, 1, result)
        self.assertEqual(next(item for item in result["components"] if item["name"] == "guardian-source")["status"], "conflict")
        add.assert_not_called()
        self.assertEqual(before, sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*")))

    def test_apply_selected_python_unavailable_is_zero_write_before_plugin_add(self):
        original_component = bootstrap._allinluna_component
        with mock.patch.object(bootstrap, "_allinluna_python", return_value=None), mock.patch.object(
            bootstrap, "_plugin_add"
        ) as add:
            result, code = self._run(
                "apply",
                allinluna_component=lambda *args, **kwargs: original_component(*args, **kwargs),
                allinluna_python=str(self.root / "python311"),
                create_fake_venv=False,
            )
        self.assertEqual(code, 1, result)
        allin = next(item for item in result["components"] if item["name"] == "allinluna")
        self.assertEqual(allin["status"], "unavailable")
        self.assertIn("Python", allin["reason"])
        add.assert_not_called()
        self.assertFalse((self.home / "plugin-state.json").exists())
        self.assertFalse((self.home / "agents").exists())
        self.assertFalse((self.home / bootstrap.MANAGED_VENV_RELATIVE).exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())

    def test_apply_wrong_wheel_hash_never_calls_pip_or_publishes_owned_state(self):
        wrong_wheel = b"wrong-wheel-for-top-level-hash"
        locked_wheel = b"different-locked-wheel"
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter", encoding="utf-8")
        interpreter.chmod(0o700)
        calls = []

        class Response:
            headers = {"Content-Length": str(len(wrong_wheel))}

            def __init__(self):
                self.stream = io.BytesIO(wrong_wheel)

            def settimeout(self, value):
                del value

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def mutate_lock(lock):
            wheel = lock["components"]["allinluna"]["pypi"]["wheel"]
            wheel["sha256"] = hashlib.sha256(locked_wheel).hexdigest()
            wheel["size"] = len(wrong_wheel)

        original_run = bootstrap._run_argv

        def fake_run(argv, *args, **kwargs):
            tokens = [str(item) for item in argv]
            calls.append(tokens)
            if "-m" in tokens and "pip" in tokens:
                return 99, "", "pip unexpectedly called"
            if "-m" in tokens and "venv" in tokens:
                staging = pathlib.Path(tokens[-1])
                python = staging / "bin" / "python"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("python", encoding="utf-8")
                python.chmod(0o700)
                return 0, "", ""
            return original_run(argv, *args, **kwargs)

        planned = {
            "name": "allinluna",
            "status": "planned",
            "path_relative": "venvs/allinluna/bin/allinluna",
            "python_relative": "venvs/allinluna/bin/python",
            "python_minimum": ">=3.11",
        }
        with mock.patch.object(bootstrap, "_allinluna_python", return_value=interpreter), mock.patch.object(
            bootstrap, "_run_argv", side_effect=fake_run
        ), mock.patch.object(bootstrap, "_open_exact_url", return_value=Response()):
            result, code = self._run(
                "apply",
                allinluna_component=planned,
                lock_mutator=mutate_lock,
                create_fake_venv=False,
            )

        self.assertEqual(code, 1, result)
        self.assertEqual(result["status"], "recovery-required")
        self.assertFalse(any("-m" in call and "pip" in call for call in calls))
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text(encoding="utf-8")), ["allinluna"])
        plugin = next(item for item in result["components"] if item["name"] == "codex-plugin-command")
        self.assertTrue(plugin["installed"]["allinluna"])
        self.assertEqual(result["owned_plugins"], [])
        self.assertTrue(any("installed but unowned" in note for note in result["notes"]))
        self.assertFalse(any(call[1:3] == ["plugin", "remove"] for call in calls if len(call) >= 3))
        for relative in bootstrap.MANAGED_ROLE_RELATIVES:
            self.assertFalse((self.home / relative).exists(), relative)
        self.assertFalse((self.home / bootstrap.MANAGED_VENV_RELATIVE).exists())
        staging = self.home / "venvs" / ".guardian-venv-allinluna"
        self.assertTrue(staging.is_dir())
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        self.assertTrue(any(
            item.get("action") == "preserved-unverified"
            and item.get("path") == "venvs/.guardian-venv-allinluna"
            for item in result["rollback"]["actions"]
        ))
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())

    def test_real_0146_installed_rows_without_installed_path_use_locked_coordinate(self):
        self._write_codex(omit_installed_path=True)
        (self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3").mkdir(parents=True)
        (self.home / "plugin-state.json").write_text(json.dumps(["allinluna"]))
        checked, check_code = self._run("check")
        self.assertEqual(check_code, 1, checked)
        self.assertEqual(checked["status"], "changes-required")
        self.assertEqual(next(item for item in checked["components"] if item["name"] == "codex-plugin-command")["status"], "present")
        with mock.patch.object(bootstrap, "_plugin_add") as add:
            applied, apply_code = self._run("apply")
        self.assertEqual(apply_code, 0, applied)
        add.assert_not_called()

    def test_real_0146_preexisting_row_without_path_is_reverified_without_add(self):
        self._write_codex(omit_installed_path=True)
        cache = self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3"
        cache.mkdir(parents=True)
        (self.home / "plugin-state.json").write_text(json.dumps(["allinluna"]))
        checked, check_code = self._run("check")
        self.assertEqual(check_code, 1)
        self.assertEqual(next(item for item in checked["components"] if item["name"] == "codex-plugin-command")["status"], "present")
        self.assertEqual(checked["status"], "changes-required")
        with mock.patch.object(bootstrap, "_plugin_add") as add:
            applied, apply_code = self._run("apply")
        self.assertEqual(apply_code, 0, applied)
        add.assert_not_called()
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text(encoding="utf-8")), ["allinluna"])

    def test_real_0147_missing_row_path_fails_closed_without_plugin_add(self):
        self._write_codex(omit_installed_path=True)
        self.codex.write_text(
            self.codex.read_text(encoding="utf-8").replace("codex-cli 0.146.0", "codex-cli 0.147.0"),
            encoding="utf-8",
        )
        self.codex.chmod(0o700)
        cache = self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3"
        cache.mkdir(parents=True)
        (self.home / "plugin-state.json").write_text(json.dumps(["allinluna"]))
        with mock.patch.object(bootstrap, "_plugin_add") as add:
            result, code = self._run("apply", create_fake_venv=False)
        self.assertEqual(code, 1, result)
        plugin = next(item for item in result["components"] if item["name"] == "codex-plugin-command")
        self.assertEqual(plugin["status"], "conflict")
        add.assert_not_called()

    def test_manual_plugin_removal_reinstall_is_not_receipt_owned(self):
        first, code = self._run("apply")
        self.assertEqual(code, 0, first)
        self.assertEqual(first["owned_plugins"], [])
        shutil.rmtree(self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3")
        (self.home / "plugin-state.json").write_text("[]", encoding="utf-8")
        reapplied, reapply_code = self._run("apply")
        self.assertEqual(reapply_code, 0, reapplied)
        self.assertNotIn("allinluna", [item["name"] for item in reapplied["owned_plugins"]])
        uninstalled, uninstall_code = self._run("uninstall")
        self.assertEqual(uninstall_code, 0, uninstalled)
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text(encoding="utf-8")), ["allinluna"])

    def test_legacy_owned_plugin_is_migrated_without_destructive_authority(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        plugin_component = next(item for item in receipt["components"] if item["name"] == "codex-plugin-command")
        plugin_path = self.home / plugin_component["installed_paths"]["allinluna"]
        info = plugin_path.lstat()
        receipt["owned_plugins"] = [{
            "selector": "allinluna@onebigmoon-codex-workflows",
            "name": "allinluna",
            "sha": plugin_component["installed_sha"]["allinluna"],
            "hooks": "none",
            "relative_path": plugin_component["installed_paths"]["allinluna"],
            "device": int(info.st_dev),
            "inode": int(info.st_ino),
        }]
        self.assertIsNone(bootstrap._write_receipt(self.home, receipt)[1])

        blocked, blocked_code = self._run("uninstall")
        self.assertEqual(blocked_code, 1, blocked)
        self.assertTrue(bootstrap._receipt_paths(self.home)[0].exists())

        migrated, migrated_code = self._run("apply")
        self.assertEqual(migrated_code, 0, migrated)
        self.assertEqual(migrated["owned_plugins"], [])
        self.assertTrue(any("legacy plugin ownership was discarded" in note for note in migrated["notes"]))
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text(encoding="utf-8")), ["allinluna"])

    def test_plugin_same_source_wrong_version_is_not_exact(self):
        lock = bootstrap._load_lock()
        entry = self._entry("ponytail", {"source": "url", "url": "https://github.com/DietrichGebert/ponytail.git"})
        self.assertTrue(bootstrap._plugin_source_matches("ponytail", entry, lock))
        entry["version"] = "9.9.9"
        self.assertFalse(bootstrap._plugin_source_matches("ponytail", entry, lock))

    def test_enabled_preinstalled_ponytail_is_optional_and_not_hook_trust(self):
        lock = bootstrap._load_lock()
        entry = self._entry("ponytail", {"source": "url", "url": "https://github.com/DietrichGebert/ponytail.git"}, enabled=True)
        self.assertTrue(bootstrap._plugin_policy_matches("ponytail", entry))
        component = {
            "installed": {"ponytail": True},
            "available": {},
            "hits": [{"plugin": "ponytail", "location": "installed", "installed": True}],
        }
        plan = next(item for item in bootstrap._plugin_plans(lock, component) if item["plugin_name"] == "ponytail")
        self.assertEqual(plan["status"], "present")
        self.assertFalse(plan["owned"])

    def test_newer_codex_meets_locked_minimum(self):
        lock = bootstrap._load_lock()
        with mock.patch.object(bootstrap, "_run_argv", return_value=(0, "codex-cli 0.147.0\n", "")):
            component, _ = bootstrap._codex_component(lock, self.home, str(self.codex))
        self.assertEqual(component["status"], "present")
        self.assertTrue(component["version_skew"])

    def test_python_discovery_skips_39_launcher_and_requires_venv_capability(self):
        canary = self.root / "ambient-python-canary"
        launcher = self.root / "python3.9"
        launcher.write_text(
            "#!/bin/sh\n"
            f"echo launched > {shlex.quote(str(canary))}\n"
            "echo 'Python 3.9.99'\n",
            encoding="utf-8",
        )
        launcher.chmod(0o700)
        trusted = self.root / "python3.14"
        trusted.write_text(
            "#!/bin/sh\n"
            "if [ \"$2\" = \"--version\" ]; then echo 'Python 3.14.1'; else echo guardian-python-capable; fi\n",
            encoding="utf-8",
        )
        trusted.chmod(0o700)
        lock = bootstrap._load_lock()
        with mock.patch.object(bootstrap, "MACOS_PYTHON_CANDIDATES", (str(trusted),)), \
             mock.patch.object(bootstrap.sys, "version_info", (3, 9, 0)):
            selected = bootstrap._allinluna_python(lock, str(launcher), self.home, self.guardian_root)
        self.assertEqual(selected, trusted.resolve())
        self.assertFalse(canary.exists())

    def test_journal_keeps_plugin_commit_separate_from_file_sha256(self):
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "PREPARED",
            "steps": [{
                "id": "plugin:ponytail",
                "kind": "plugin",
                "action": "add",
                "state": "intent",
                "relative_path": "",
                "sha256": None,
                "commit": "2ed6c52c9d7e5e56942508591085fd45dea277d3",
                "selector": "ponytail@onebigmoon-codex-workflows",
                "preexisting": False,
            }, {
                "id": "receipt", "kind": "receipt", "action": "write", "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(), "sha256": None,
                "commit": None, "selector": None,
            }],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        stored, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertEqual(stored["steps"][0]["commit"], journal["steps"][0]["commit"])
        journal["steps"][0]["sha256"] = journal["steps"][0]["commit"]
        self.assertIsNotNone(bootstrap._write_journal(self.home, journal))

    def test_first_check_on_0755_home_is_zero_write_and_projects_complete_plan(self):
        self.home.chmod(0o755)

        def snapshot():
            result = {}
            paths = [self.home, *sorted(self.home.rglob("*"), key=lambda item: item.as_posix())]
            for path in paths:
                relative = "." if path == self.home else path.relative_to(self.home).as_posix()
                info = path.lstat()
                digest = None
                link = None
                if stat.S_ISREG(info.st_mode):
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                elif stat.S_ISLNK(info.st_mode):
                    link = os.readlink(path)
                result[relative] = (
                    stat.S_IFMT(info.st_mode),
                    stat.S_IMODE(info.st_mode),
                    info.st_size,
                    getattr(info, "st_mtime_ns", 0),
                    getattr(info, "st_ctime_ns", 0),
                    digest,
                    link,
                )
            return result

        before = snapshot()
        planned_allinluna = {
            "name": "allinluna",
            "status": "planned",
            "path_relative": "venvs/allinluna/bin/allinluna",
            "python_relative": "venvs/allinluna/bin/python",
            "python_minimum": ">=3.11",
            "wheel_sha256": "0" * 64,
            "reason": "apply will fetch and verify the pinned wheel only",
        }
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned_allinluna):
            receipt, code = self._run_on_darwin(
                "check",
                self.home,
                str(self.codex),
                guardian_ref=self.guardian_ref,
                git_bin=str(self.git),
            )
        self.assertEqual(code, 1, receipt)
        projected = bootstrap._public_receipt(receipt)
        self.assertEqual(projected["existing_receipt"], "absent")
        self.assertEqual(projected["status"], "changes-required")
        planned = projected["planned"]["components"]
        names = [item["name"] for item in planned]
        self.assertEqual(names.count("plugin:allinluna"), 1)
        self.assertEqual(names.count("allinluna"), 1)
        self.assertEqual(
            sorted(name.split(":", 1)[1] for name in names if name.startswith("agent:")),
            sorted(bootstrap.ROLE_TEMPLATE_NAMES),
        )
        self.assertEqual(projected["planned"]["count"], 16)
        self.assertEqual(snapshot(), before)
        self.assertFalse((self.home / "workflow-guardian").exists())

    def test_apply_publishes_without_clobber_and_receipt_has_no_absolute_path(self):
        journals = []
        original_write_journal = bootstrap._write_journal

        def capture_journal(home, journal):
            journals.append(json.loads(json.dumps(journal)))
            return original_write_journal(home, journal)

        with mock.patch.object(bootstrap, "_write_journal", side_effect=capture_journal):
            receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["schema"], bootstrap.SCHEMA)
        self.assertEqual(len(receipt["owned_paths"]), 14)
        self.assertEqual(receipt["owned_plugins"], [])
        self.assertTrue(any("installed but unowned" in note for note in receipt["notes"]))
        prepared = next(item for item in journals if item["phase"] == "PREPARED")
        self.assertNotIn("plugin:ponytail", [step["id"] for step in prepared["steps"]])
        self.assertNotIn("ponytail", [item["name"] for item in receipt["owned_plugins"]])
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertNotIn(str(self.root), json.dumps(receipt))
        target = self.home / "agents" / "coder.toml"
        inode, content = target.stat().st_ino, target.read_bytes()
        second, second_code = self._run("apply")
        self.assertEqual(second_code, 0)
        self.assertEqual((target.stat().st_ino, target.read_bytes()), (inode, content))
        self.assertEqual(second["generation"], receipt["generation"])

    def test_build_managed_targets_rejects_foreign_same_bytes_created_proof(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        created = next(item for item in receipt["owned_paths"] if item["relative_path"] == "agents/coder.toml")
        target = self.home / created["relative_path"]
        original = target.read_bytes()
        replacement = target.with_name(".foreign-created-target")
        replacement.write_bytes(original)
        replacement.replace(target)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._build_managed_targets(self.home, receipt, [{"path": target, **created}])
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(target.exists())

    def test_apply_check_uninstall_reject_receipt_owned_identical_inode_replacement(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        owned = next(item for item in receipt["owned_paths"] if item["relative_path"] == "agents/coder.toml")
        target = self.home / owned["relative_path"]
        original_inode = target.stat().st_ino
        replacement = target.with_name(".identical-replacement")
        replacement.write_bytes(target.read_bytes())
        replacement.replace(target)
        self.assertNotEqual(target.stat().st_ino, original_inode)

        applied, apply_code = self._run("apply")
        self.assertEqual(apply_code, 1)
        self.assertNotEqual(applied.get("status"), "ready")
        self.assertTrue(any(
            item.get("reason", "").startswith("receipt-owned content was replaced")
            for item in applied.get("conflicts", [])
        ))
        stored, stored_error = bootstrap._read_existing_receipt(self.home)
        self.assertIsNone(stored_error)
        self.assertEqual(stored["owned_paths"], receipt["owned_paths"])

        checked, check_code = self._run("check")
        self.assertEqual(check_code, 1)
        self.assertEqual(checked["status"], "changes-required")
        uninstalled, uninstall_code = self._run("uninstall")
        self.assertEqual(uninstall_code, 1)
        self.assertEqual(next(item for item in uninstalled["components"] if item["name"] == "uninstall")["status"], "conflict")
        self.assertTrue(target.exists())
        self.assertTrue(bootstrap._receipt_paths(self.home)[0].exists())

    def test_preflight_race_preserves_concurrent_inode_and_bytes(self):
        plans, conflicts = bootstrap._preflight_targets(self.home)
        self.assertFalse(conflicts)
        plan = next(item for item in plans if item["relative_path"] == "agents/coder.toml")
        plan["target"].parent.mkdir(mode=0o700, parents=True)
        plan["target"].write_bytes(b"concurrent")
        with self.assertRaises((bootstrap.BootstrapError, FileExistsError)):
            bootstrap._copy_one(plan)
        self.assertEqual(plan["target"].read_bytes(), b"concurrent")

    def test_frozen_source_proof_rejects_changed_or_rebased_role_asset(self):
        plans, conflicts = bootstrap._preflight_targets(self.home, self.guardian_root)
        self.assertFalse(conflicts)
        plan = next(item for item in plans if item["relative_path"] == "agents/coder.toml")
        source = pathlib.Path(plan["source"])
        original = source.read_bytes()
        replacement = source.with_name(".rebased-role")
        replacement.write_bytes(original)
        replacement.chmod(stat.S_IMODE(source.stat().st_mode))
        replacement.replace(source)
        try:
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._copy_one(plan)
        finally:
            source.write_bytes(original)
            source.chmod(0o644)

    def test_git_validation_freezes_role_proof_before_source_rebase(self):
        valid, reason = bootstrap._validate_git_checkout(self.guardian_root, self.home, self.git, self.guardian_ref)
        self.assertTrue(valid, reason)
        source = self.guardian_root / "skills/setup-codex-workflow-guardian/assets/agents/coder.toml"
        original = source.read_bytes()
        try:
            source.write_bytes(original + b"\n# rebased after validation\n")
            plans, conflicts = bootstrap._preflight_targets(self.home, self.guardian_root)
            self.assertTrue(any(item.get("name") == "agent:coder" for item in conflicts))
            self.assertEqual(next(item for item in plans if item["name"] == "agent:coder")["status"], "conflict")
        finally:
            source.write_bytes(original)

    def test_identical_role_under_untrusted_target_parent_or_file_is_conflict(self):
        plans, _ = bootstrap._preflight_targets(self.home, self.guardian_root)
        plan = next(item for item in plans if item["relative_path"] == "agents/coder.toml")
        target = pathlib.Path(plan["target"])
        target.parent.mkdir(mode=0o700, parents=True)
        target.write_bytes(pathlib.Path(plan["source"]).read_bytes())
        target.parent.chmod(0o777)
        untrusted_parent, conflicts = bootstrap._preflight_targets(self.home, self.guardian_root)
        self.assertTrue(any(item.get("relative_path") == "agents/coder.toml" for item in conflicts))
        self.assertEqual(next(item for item in untrusted_parent if item["relative_path"] == "agents/coder.toml")["status"], "conflict")
        target.parent.chmod(0o700)
        target.chmod(0o666)
        trusted_parent, conflicts = bootstrap._preflight_targets(self.home, self.guardian_root)
        self.assertTrue(any(item.get("relative_path") == "agents/coder.toml" for item in conflicts))
        self.assertEqual(next(item for item in trusted_parent if item["relative_path"] == "agents/coder.toml")["status"], "conflict")

    def test_untrusted_venv_parent_is_conflict_before_any_build(self):
        venvs = self.home / "venvs"
        venvs.mkdir(mode=0o700)
        venvs.chmod(0o777)
        component = bootstrap._allinluna_component(
            bootstrap._load_lock(), self.home, bootstrap._python_component(), None, self.guardian_root
        )
        self.assertEqual(component["status"], "conflict")

    def test_plugin_tree_digest_and_ownership_must_share_root_inode(self):
        plugin = self.home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3"
        plugin.mkdir(parents=True)
        proof = bootstrap._content_tree_proof(plugin, self.home)
        self.assertIsNotNone(proof)
        old = plugin.with_name(".allinluna-old")
        replacement = plugin.with_name(".allinluna-replacement")
        plugin.replace(old)
        replacement.mkdir()
        replacement.replace(plugin)
        old.rmdir()
        observed = {
            "status": "present",
            "installed": {"allinluna": True},
            "installed_paths": {"allinluna": "plugins/cache/onebigmoon-codex-workflows/allinluna/2.0.0-rc.3"},
            "installed_sha": {"allinluna": bootstrap._load_lock()["components"]["allinluna"]["commit"]},
            "installed_tree_sha256": {"allinluna": proof["tree_sha256"]},
            "installed_tree_proof": {"allinluna": proof},
        }
        self.assertIsNone(
            bootstrap._plugin_ownership_proof(
                observed,
                self.home,
                "allinluna",
                bootstrap._load_lock()["components"]["allinluna"]["commit"],
            )
        )

    def test_plugin_double_add_is_idempotent_but_never_receipt_owned(self):
        with mock.patch.object(bootstrap, "_plugin_add", wraps=bootstrap._plugin_add) as add:
            first, first_code = self._run("apply")
            second, second_code = self._run("apply")
        self.assertEqual(first_code, 0, first)
        self.assertEqual(second_code, 0, second)
        self.assertEqual(add.call_count, 1)
        self.assertEqual(first["owned_plugins"], [])
        self.assertEqual(second["owned_plugins"], [])

    def test_codex_home_requires_canonical_absolute_and_rejects_real_home_when_home_is_spoofed(self):
        relative, relative_code = self._run_on_darwin(
            "check", pathlib.Path("relative-codex-home"), str(self.codex), guardian_ref=self.guardian_ref, git_bin=str(self.git)
        )
        self.assertEqual(relative_code, 2)
        self.assertEqual(relative["components"][0]["name"], "codex-home")
        account_home = pathlib.Path(__import__("pwd").getpwuid(os.getuid()).pw_dir)
        with mock.patch.dict(os.environ, {"HOME": str(self.root / "spoofed-home")}, clear=False):
            real_home, real_code = self._run_on_darwin(
                "check", account_home, str(self.codex), guardian_ref=self.guardian_ref, git_bin=str(self.git)
            )
        self.assertEqual(real_code, 2)
        self.assertEqual(real_home["components"][0]["name"], "codex-home")

    def test_codex_home_broad_denylist_uses_passwd_home_children_and_parents(self):
        account_home = pathlib.Path(__import__("pwd").getpwuid(os.getuid()).pw_dir)
        # Keep HOME pointed at an unrelated directory so these assertions
        # prove that the denylist is not derived from the mutable environment.
        with mock.patch.dict(os.environ, {"HOME": str(self.root / "spoofed-home")}, clear=False):
            for candidate in (
                account_home / "Documents",
                account_home / "Desktop",
                account_home / "Downloads",
                account_home / "Library",
                account_home / "Public",
                account_home.parent,
                account_home.parent.parent,
            ):
                with self.subTest(candidate=candidate):
                    result, code = self._run_on_darwin(
                        "check",
                        candidate,
                        str(self.codex),
                        guardian_ref=self.guardian_ref,
                        git_bin=str(self.git),
                    )
                    self.assertEqual(code, 2, result)
                    self.assertEqual(result["components"][0]["name"], "codex-home")

    def test_copy_staging_replacement_is_preserved_on_failure(self):
        plans, conflicts = bootstrap._preflight_targets(self.home)
        self.assertFalse(conflicts)
        plan = next(item for item in plans if item["relative_path"] == "agents/coder.toml")
        stage = self.home / bootstrap._copy_staging_relative_path(plan["relative_path"])

        def replace_stage(staging_relative, identity, state, stage_hash):
            del identity, stage_hash
            if state != "staged":
                return
            self.assertEqual(staging_relative, stage.relative_to(self.home).as_posix())
            preserved = stage.with_name(".preserved-copy-stage")
            foreign = stage.with_name(".foreign-copy-stage")
            stage.rename(preserved)
            foreign.write_bytes(b"foreign-copy-stage")
            foreign.rename(stage)

        plan["codex_home"] = self.home
        plan["staging_relative_path"] = stage.relative_to(self.home).as_posix()
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._copy_one(plan, replace_stage)
        self.assertEqual(stage.read_bytes(), b"foreign-copy-stage")
        self.assertFalse(plan["target"].exists())

    def test_copy_publishing_same_bytes_replacement_is_preserved(self):
        plans, conflicts = bootstrap._preflight_targets(self.home)
        self.assertFalse(conflicts)
        plan = next(item for item in plans if item["relative_path"] == "agents/coder.toml")
        stage = self.home / bootstrap._copy_staging_relative_path(plan["relative_path"])

        def replace_publishing(staging_relative, identity, state, stage_hash):
            del identity, stage_hash
            if state != "publishing":
                return
            self.assertEqual(staging_relative, stage.relative_to(self.home).as_posix())
            preserved = stage.with_name(".preserved-publish-stage")
            foreign = stage.with_name(".foreign-publish-stage")
            stage.rename(preserved)
            foreign.write_bytes(preserved.read_bytes())
            foreign.rename(stage)

        plan["codex_home"] = self.home
        plan["staging_relative_path"] = stage.relative_to(self.home).as_posix()
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._copy_one(plan, replace_publishing)
        self.assertTrue(stage.exists())
        self.assertEqual(stage.read_bytes(), pathlib.Path(plan["source"]).read_bytes())
        self.assertFalse(plan["target"].exists())

    def test_plugin_add_exit_zero_noop_fails_post_query(self):
        self._write_codex(no_op_add=True)
        receipt, code = self._run("apply")
        self.assertEqual(code, 1)
        self.assertFalse((self.home / "workflow-guardian" / "bootstrap-receipt.json").exists())

    def test_semantic_receipt_drift_is_not_ready(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0)
        path, sidecar = bootstrap._receipt_paths(self.home)
        value = json.loads(path.read_text())
        value["managed_targets"][0]["sha256"] = "0" * 64
        raw = (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(raw)
        sidecar.write_text(hashlib.sha256(raw).hexdigest() + "\n")
        checked, check_code = self._run("check")
        self.assertEqual(check_code, 1)
        self.assertEqual(checked["status"], "changes-required")

    def test_interrupted_receipt_write_is_recovered_as_commit(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0)
        journal = self._journal_for_receipt(receipt, receipt_state="intent")
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertEqual(recovered["state_digest"], receipt["state_digest"])

    def test_started_receipt_requires_complete_live_pair_and_does_not_delete_on_drift(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        journal = self._journal_for_receipt(receipt, receipt_state="started")
        receipt_path, hash_path = bootstrap._receipt_paths(self.home)
        hash_bytes = hash_path.read_bytes()
        hash_path.unlink()
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        incomplete, incomplete_code = self._run("apply")
        self.assertEqual(incomplete_code, 1)
        self.assertEqual(incomplete["status"], "recovery-required")
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        self.assertFalse(receipt_path.exists())
        hash_path.write_bytes(hash_bytes)
        target = self.home / receipt["owned_paths"][0]["relative_path"]
        target.write_bytes(b"drift")
        drifted, drifted_code = self._run("apply")
        self.assertEqual(drifted_code, 1)
        self.assertEqual(drifted["status"], "recovery-required")
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        self.assertTrue(target.exists())

    def test_started_receipt_with_complete_attestation_commits(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        journal = self._journal_for_receipt(receipt, receipt_state="started")
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_interrupted_committing_apply_is_recovered(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0)
        journal = self._journal_for_receipt(receipt, phase="COMMITTING", receipt_state="done")
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        with mock.patch.object(bootstrap, "_remove_journal", return_value=False):
            pending, pending_code = self._run("apply")
        self.assertEqual(pending_code, 1)
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_preexisting_plugin_without_proof_blocks_apply_before_transaction(self):
        self._write_codex(omit_installed_path=True)
        (self.home / "plugin-cache" / "allinluna").mkdir(parents=True)
        (self.home / "plugin-state.json").write_text(json.dumps(["allinluna"]), encoding="utf-8")
        with mock.patch.object(bootstrap, "_plugin_add") as add:
            result, code = self._run("apply")
        self.assertEqual(code, 1, result)
        self.assertEqual(result["status"], "changes-required")
        add.assert_not_called()
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text(encoding="utf-8")), ["allinluna"])

    def test_receipt_write_then_commit_failure_rolls_back_receipt(self):
        original_write = bootstrap._write_journal

        def fail_commit(home, journal):
            if journal.get("phase") == "COMMITTING":
                return "forced commit journal failure"
            return original_write(home, journal)

        with mock.patch.object(bootstrap, "_write_journal", side_effect=fail_commit):
            receipt, code = self._run("apply")
        self.assertEqual(code, 1)
        self.assertTrue(receipt["rollback"]["performed"])
        self.assertFalse((self.home / "workflow-guardian" / "bootstrap-receipt.json").exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_repair_commit_journal_failure_keeps_new_receipt_and_recovers_without_orphan(self):
        prior, code = self._run("apply")
        self.assertEqual(code, 0, prior)
        missing = self.home / prior["owned_paths"][0]["relative_path"]
        if missing.is_dir():
            shutil.rmtree(missing)
        else:
            missing.unlink()
        original_write = bootstrap._write_journal

        def fail_committing(home, journal):
            if journal.get("phase") == "COMMITTING":
                return "forced COMMITTING journal failure"
            return original_write(home, journal)

        with mock.patch.object(bootstrap, "_write_journal", side_effect=fail_committing):
            pending, pending_code = self._run("apply")
        self.assertEqual(pending_code, 1)
        self.assertEqual(pending["status"], "recovery-required")
        self.assertTrue(bootstrap._receipt_paths(self.home)[0].exists())
        self.assertTrue(bootstrap._receipt_paths(self.home)[1].exists())
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        stored, stored_error = bootstrap._read_existing_receipt(self.home)
        self.assertIsNone(stored_error)
        self.assertIsInstance(stored, dict)
        for item in stored["owned_paths"]:
            self.assertTrue((self.home / item["relative_path"]).exists())

        recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        for item in stored["owned_paths"]:
            self.assertTrue((self.home / item["relative_path"]).exists())

    def test_rollback_reports_unsafe_replacement_and_keeps_recovery_journal(self):
        target = self.home / "agents" / "coder.toml"
        external = self.root / "rollback-target"

        def fail_receipt(home, candidate, before_replace=None):
            del home, candidate, before_replace
            target.unlink()
            target.symlink_to(external)
            return [], "forced receipt failure"

        external.write_bytes(b"preserved outside target")
        with mock.patch.object(bootstrap, "_write_receipt", side_effect=fail_receipt):
            receipt, code = self._run("apply")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "recovery-required")
        action = next(item for item in receipt["rollback"]["actions"] if item.get("path") == "agents/coder.toml")
        self.assertEqual(action["action"], "preserved-unsafe")
        self.assertTrue(target.is_symlink())
        self.assertTrue(bootstrap._journal_path(self.home).exists())

    def test_first_receipt_replace_baseexception_recovers_old_pair(self):
        class HardCrash(BaseException):
            pass

        first, code = self._run("apply")
        self.assertEqual(code, 0, first)
        owned = first["owned_paths"][0]
        target = self.home / owned["relative_path"]
        target.unlink()
        receipt_path, _ = bootstrap._receipt_paths(self.home)
        original_replace = bootstrap.os.replace
        crashed = False

        def replace(source, destination, *args, **kwargs):
            nonlocal crashed
            result = original_replace(source, destination, *args, **kwargs)
            if pathlib.Path(destination) == receipt_path and not crashed:
                crashed = True
                raise HardCrash()
            return result

        with mock.patch.object(bootstrap.os, "replace", side_effect=replace):
            with self.assertRaises(HardCrash):
                self._run("apply")
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        self.assertEqual(next(step for step in journal["steps"] if step["kind"] == "receipt")["state"], "started")
        recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertFalse(any(path.name.startswith((".guardian-receipt-old-", ".guardian-receipt-new")) for path in self.home.rglob("*")))

    def test_first_receipt_partial_replace_baseexception_preserves_unproven_pair(self):
        class HardCrash(BaseException):
            pass

        receipt_path, _ = bootstrap._receipt_paths(self.home)
        original_replace = bootstrap._rename_noreplace
        crashed = False

        def replace(source, destination, *args, **kwargs):
            nonlocal crashed
            result = original_replace(source, destination, *args, **kwargs)
            if pathlib.Path(destination) == receipt_path and not crashed:
                crashed = True
                raise HardCrash()
            return result

        with mock.patch.object(bootstrap, "_rename_noreplace", side_effect=replace):
            with self.assertRaises(HardCrash):
                self._run("apply")
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        self.assertEqual(next(step for step in journal["steps"] if step["kind"] == "receipt")["state"], "started")

        with mock.patch.object(bootstrap, "_plugin_add") as add:
            recovered, recovered_code = self._run("apply")
        self.assertEqual(recovered_code, 1, recovered)
        self.assertEqual(recovered["status"], "recovery-required")
        add.assert_not_called()
        self.assertTrue((self.home / "workflow-guardian" / ".guardian-receipt-new.sha256").exists())
        self.assertTrue(receipt_path.exists())
        self.assertTrue(bootstrap._journal_path(self.home).exists())

    def test_subprocess_exit_after_first_receipt_backup_recovers_existing_pair(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        candidate = json.loads(json.dumps(receipt))
        candidate["notes"] = ["backup-retry"]
        journal = self._receipt_only_journal()
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        child = textwrap.dedent(
            """
            import importlib.util
            import json
            import os
            import pathlib
            import sys
            spec = importlib.util.spec_from_file_location("bootstrap_macos", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            home = pathlib.Path(sys.argv[2])
            candidate = json.loads(sys.argv[3])
            journal = json.loads(sys.argv[4])
            step = journal["steps"][0]
            def prepare(data):
                step.update(data)
                step["state"] = "started"
                journal["generation"] += 1
                error = module._write_journal(home, journal)
                if error:
                    raise RuntimeError(error)
            original = module._receipt_backup_path
            def crash(codex_home, path, content, on_created=None):
                result = original(codex_home, path, content, on_created)
                if content is not None:
                    os._exit(73)
                return result
            module._receipt_backup_path = crash
            module._write_receipt(home, candidate, before_replace=prepare)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child, str(SCRIPT), str(self.home), json.dumps(candidate), json.dumps(journal)],
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 73, result.stderr)
        pending, pending_error = bootstrap._read_journal(self.home)
        self.assertIsNone(pending_error)
        self.assertIsNotNone(pending)
        receipt_step = next(step for step in pending["steps"] if step["id"] == "receipt")
        self.assertEqual(receipt_step["state"], "started")
        backup = self.home / receipt_step["prior_receipt_backup_relative_path"]
        self.assertTrue(backup.exists())
        self.assertEqual(bootstrap._receipt_pair_digests(self.home), (True, hashlib.sha256(bootstrap._receipt_paths(self.home)[0].read_bytes()).hexdigest(), True, hashlib.sha256(bootstrap._receipt_paths(self.home)[1].read_bytes()).hexdigest()))
        recovered, reason = bootstrap._recover_apply_journal(self.home, pending, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertFalse(any(path.name.startswith((".guardian-receipt-old-", ".guardian-receipt-new")) for path in self.home.rglob("*")))
        _, retry_error = bootstrap._write_receipt(self.home, candidate)
        self.assertIsNone(retry_error)
        stored, stored_error = bootstrap._read_existing_receipt(self.home)
        self.assertIsNone(stored_error)
        self.assertEqual(stored["notes"], ["backup-retry"])

    def test_subprocess_exit_entering_receipt_write_before_callback_keeps_planned_journal(self):
        original, code = self._run("apply")
        self.assertEqual(code, 0, original)
        candidate = json.loads(json.dumps(original))
        candidate["notes"] = ["callback-boundary"]
        journal = self._receipt_only_journal()
        journal["steps"][0]["state"] = "planned"
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        child = textwrap.dedent(
            """
            import importlib.util
            import json
            import os
            import pathlib
            import sys
            spec = importlib.util.spec_from_file_location("bootstrap_macos", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            home = pathlib.Path(sys.argv[2])
            candidate = json.loads(sys.argv[3])
            def crash(receipt):
                os._exit(76)
            module._assert_private_receipt = crash
            module._write_receipt(home, candidate)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child, str(SCRIPT), str(self.home), json.dumps(candidate)],
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 76, result.stderr)
        pending, pending_error = bootstrap._read_journal(self.home)
        self.assertIsNone(pending_error)
        self.assertIsNotNone(pending)
        step = next(step for step in pending["steps"] if step["id"] == "receipt")
        self.assertEqual(step["state"], "planned")
        self.assertIsNone(step["new_receipt_sha256"])
        self.assertIsNone(step["prior_receipt_backup_relative_path"])
        recovered, reason = bootstrap._recover_apply_journal(self.home, pending, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        _, retry_error = bootstrap._write_receipt(self.home, candidate)
        self.assertIsNone(retry_error)
        self.assertFalse(any(path.name.startswith((".guardian-receipt-old-", ".guardian-receipt-new")) for path in self.home.rglob("*")))

    def test_subprocess_exit_after_first_receipt_temp_recovers_without_old_pair(self):
        original, code = self._run("apply")
        self.assertEqual(code, 0, original)
        candidate = json.loads(json.dumps(original))
        candidate["notes"] = ["first-write-retry"]
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        receipt_path.unlink()
        sidecar_path.unlink()
        journal = self._receipt_only_journal()
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        child = textwrap.dedent(
            """
            import importlib.util
            import json
            import os
            import pathlib
            import sys
            spec = importlib.util.spec_from_file_location("bootstrap_macos", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            home = pathlib.Path(sys.argv[2])
            candidate = json.loads(sys.argv[3])
            journal = json.loads(sys.argv[4])
            step = journal["steps"][0]
            def prepare(data):
                step.update(data)
                step["state"] = "started"
                journal["generation"] += 1
                error = module._write_journal(home, journal)
                if error:
                    raise RuntimeError(error)
            original_open = module.os.open
            def crash(path, flags, *args, **kwargs):
                descriptor = original_open(path, flags, *args, **kwargs)
                if pathlib.Path(path).name == ".guardian-receipt-new.json":
                    os._exit(74)
                return descriptor
            module.os.open = crash
            module._write_receipt(home, candidate, before_replace=prepare)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child, str(SCRIPT), str(self.home), json.dumps(candidate), json.dumps(journal)],
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 74, result.stderr)
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())
        self.assertTrue((self.home / "workflow-guardian" / ".guardian-receipt-new.json").exists())
        pending, pending_error = bootstrap._read_journal(self.home)
        self.assertIsNone(pending_error)
        self.assertIsNotNone(pending)
        recovered, reason = bootstrap._recover_apply_journal(self.home, pending, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertFalse(recovered)
        self.assertIn("receipt", reason)
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        (self.home / "workflow-guardian" / ".guardian-receipt-new.json").unlink()
        recovered, reason = bootstrap._recover_apply_journal(self.home, pending, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertFalse(any(path.name.startswith((".guardian-receipt-old-", ".guardian-receipt-new")) for path in self.home.rglob("*")))
        _, retry_error = bootstrap._write_receipt(self.home, candidate)
        self.assertIsNone(retry_error)
        self.assertTrue(bootstrap._receipt_paths(self.home)[0].exists())
        self.assertTrue(bootstrap._receipt_paths(self.home)[1].exists())

    def test_apply_quarantine_baseexception_is_recovered_without_orphan(self):
        class HardCrash(BaseException):
            pass

        target = self.home / "agents" / "code-reviewer.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"owned")
        identity = bootstrap._identity(target.lstat())
        expected = bootstrap._tree_hash(target)
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [
                {"id": "file:agents/code-reviewer.toml", "kind": "file", "action": "publish", "state": "owned", "relative_path": "agents/code-reviewer.toml", "sha256": expected, "commit": None, "selector": None, "device": identity["device"], "inode": identity["inode"], "staging_relative_path": bootstrap._copy_staging_relative_path("agents/code-reviewer.toml"), "resolver_digest": None},
                {"id": "receipt", "kind": "receipt", "action": "write", "state": "intent", "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(), "sha256": None, "commit": None, "selector": None},
            ],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        original_rename = bootstrap._rename_noreplace_at
        crashed = False

        def rename(directory_fd, source_name, target_name):
            nonlocal crashed
            if target_name.startswith(".guardian-remove-") and not crashed:
                original_rename(directory_fd, source_name, target_name)
                crashed = True
                raise HardCrash()
            if crashed and source_name.startswith(".guardian-remove-"):
                raise HardCrash()
            return original_rename(directory_fd, source_name, target_name)

        with mock.patch.object(bootstrap, "_rename_noreplace_at", side_effect=rename):
            with self.assertRaises(HardCrash):
                bootstrap._remove_owned(target, expected, anchor=self.home, ownership=identity, quarantine_callback=bootstrap._journal_quarantine_callback(self.home, journal, "file:agents/code-reviewer.toml"))
        self.assertTrue(any(path.name.startswith(".guardian-remove-") for path in self.home.rglob("*")))
        recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git)
        self.assertTrue(recovered, reason)
        self.assertFalse(any(path.name.startswith(".guardian-remove-") for path in self.home.rglob("*")))
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_quarantine_restore_after_delete_failure_retries_by_identity_and_hash(self):
        stage, target, identity, expected, journal = self._publish_crash_journal(
            "file", "agents/quarantine-restore.txt", "publishing"
        )
        step_id = "file:" + bootstrap.MANAGED_ROLE_RELATIVES[0]
        original_unlink = bootstrap.os.unlink

        def fail_quarantine_unlink(name, *args, **kwargs):
            if os.fsdecode(name).startswith(".guardian-remove-"):
                raise OSError("forced quarantine deletion failure")
            return original_unlink(name, *args, **kwargs)

        with mock.patch.object(bootstrap.os, "unlink", side_effect=fail_quarantine_unlink):
            removed = bootstrap._remove_staged_artifact(
                stage,
                {**identity, "sha256": expected},
                self.home,
                quarantine_callback=bootstrap._journal_quarantine_callback(self.home, journal, step_id),
            )
        self.assertFalse(removed)
        pending, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(pending)
        pending_step = next(item for item in pending["steps"] if item["id"] == step_id)
        self.assertEqual(pending_step["quarantine_state"], "quarantined")
        self.assertTrue(stage.exists())
        self.assertFalse(any(path.name.startswith(".guardian-remove-") for path in self.home.rglob("*")))

        recovered, reason = bootstrap._recover_apply_journal(
            self.home, pending, self.codex, bootstrap._load_lock(), self.guardian_ref, self.git
        )
        self.assertTrue(recovered, reason)
        self.assertFalse(stage.exists())
        self.assertFalse(target.exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_venv_recovery_requires_tree_hash_and_preserves_drift(self):
        root = self.home / "venvs" / "allinluna"
        root.mkdir(parents=True)
        (root / "marker").write_text("owned")
        expected = bootstrap._tree_hash(root)
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [
                {"id": "venv:allinluna", "kind": "venv", "action": "publish", "state": "intent", "relative_path": "venvs/allinluna", "staging_relative_path": "venvs/.guardian-venv-allinluna", "sha256": expected, "commit": None, "selector": None},
                {"id": "receipt", "kind": "receipt", "action": "write", "state": "intent", "relative_path": "workflow-guardian/bootstrap-receipt.json", "sha256": None, "commit": None, "selector": None},
            ],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        (root / "marker").write_text("modified")
        lock = bootstrap._load_lock()
        recovered, reason = bootstrap._recover_apply_journal(self.home, journal, self.codex, lock, self.guardian_ref, self.git)
        self.assertFalse(recovered)
        self.assertIn("modified", reason)
        self.assertTrue(root.exists())

    def test_allinluna_install_without_python_is_side_effect_free(self):
        lock = json.loads(json.dumps(bootstrap._load_lock()))
        planned = {"name": "allinluna", "status": "planned"}
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned), mock.patch.object(
            bootstrap, "_allinluna_python", return_value=None
        ), mock.patch.object(bootstrap, "_run_argv") as run_argv:
            component, root, created = bootstrap._allinluna_install(lock, self.home, None)
        self.assertEqual(component["status"], "unavailable")
        self.assertIsNone(root)
        self.assertEqual(created, [])
        run_argv.assert_not_called()
        self.assertFalse((self.home / "venvs").exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())

    def test_allinluna_install_wrong_wheel_hash_never_publishes_or_leaks_ownership(self):
        lock = json.loads(json.dumps(bootstrap._load_lock()))
        wrong_wheel = b"wrong-wheel-for-locked-sha"
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter", encoding="utf-8")
        interpreter.chmod(0o700)
        calls = []

        class Response:
            headers = {"Content-Length": str(len(wrong_wheel))}

            def __init__(self):
                self.stream = io.BytesIO(wrong_wheel)

            def settimeout(self, value):
                del value

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_run(argv, *args, **kwargs):
            del args, kwargs
            calls.append([str(item) for item in argv])
            if "venv" in argv:
                staging = pathlib.Path(argv[-1])
                python = staging / "bin" / "python"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("python", encoding="utf-8")
                python.chmod(0o700)
            return 0, "", ""

        planned = {"name": "allinluna", "status": "planned"}
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned), mock.patch.object(
            bootstrap, "_allinluna_python", return_value=interpreter
        ), mock.patch.object(bootstrap, "_run_argv", side_effect=fake_run), mock.patch.object(
            bootstrap, "_open_exact_url", side_effect=lambda *args, **kwargs: Response()
        ):
            component, root, created = bootstrap._allinluna_install(lock, self.home, None)
        self.assertEqual(component["status"], "unavailable")
        self.assertIsNone(root)
        self.assertEqual(len(created), 1)
        self.assertTrue(any("venv" in call for call in calls))
        self.assertFalse(any("pip" in call for call in calls))
        self.assertFalse((self.home / bootstrap.MANAGED_VENV_RELATIVE).exists())
        stage = self.home / "venvs" / ".guardian-venv-allinluna"
        self.assertTrue(stage.exists())
        self.assertIsNone(created[0]["sha256"])
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())

        # Resolve the unverified stage externally before retrying from the
        # corrected wheel coordinate.
        shutil.rmtree(stage)
        lock["components"]["allinluna"]["pypi"]["wheel"]["sha256"] = hashlib.sha256(wrong_wheel).hexdigest()
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned), mock.patch.object(
            bootstrap, "_allinluna_python", return_value=interpreter
        ), mock.patch.object(bootstrap, "_run_argv", side_effect=fake_run), mock.patch.object(
            bootstrap, "_open_exact_url", side_effect=lambda *args, **kwargs: Response()
        ), mock.patch.object(bootstrap, "_verify_allinluna_runtime", return_value=[]):
            retried, retried_root, retried_created = bootstrap._allinluna_install(lock, self.home, None)
        self.assertEqual(retried["status"], "installed")
        self.assertIsNotNone(retried_root)
        self.assertEqual(len(retried_created), 1)
        self.assertFalse((self.home / "venvs" / ".guardian-venv-allinluna").exists())

    def test_apply_check_reapply_and_uninstall_cover_real_managed_venv_ownership(self):
        wheel_bytes = b"integration-wheel"
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter", encoding="utf-8")
        interpreter.chmod(0o700)
        target = self.home / bootstrap.MANAGED_VENV_RELATIVE
        original_component = bootstrap._allinluna_component
        installer_calls = []
        plugin_mutations = []

        def component_state(lock, codex_home, python_component, requested_python, repository_root=None):
            if not target.exists():
                return {"name": "allinluna", "status": "planned"}
            return original_component(lock, codex_home, python_component, requested_python, repository_root)

        def mutate_lock(lock):
            lock["components"]["allinluna"]["pypi"]["wheel"]["sha256"] = hashlib.sha256(wheel_bytes).hexdigest()

        class Response:
            headers = {"Content-Length": str(len(wheel_bytes))}

            def __init__(self):
                self.stream = io.BytesIO(wheel_bytes)

            def settimeout(self, value):
                del value

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        original_run = bootstrap._run_argv

        def fake_run(argv, *args, **kwargs):
            tokens = [str(item) for item in argv]
            if len(tokens) >= 3 and tokens[1] == "plugin" and tokens[2] in ("add", "remove"):
                plugin_mutations.append(tuple(tokens[1:]))
            if "-m" in tokens and "venv" in tokens:
                installer_calls.append(tuple(tokens))
                staging = pathlib.Path(tokens[-1])
                python = staging / "bin" / "python"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("python", encoding="utf-8")
                python.chmod(0o700)
                return 0, "", ""
            if "-m" in tokens and "pip" in tokens:
                installer_calls.append(tuple(tokens))
                staging = pathlib.Path(tokens[-1]).parent
                (staging / "bin" / "allinluna").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                (staging / "bin" / "allinluna").chmod(0o700)
                (staging / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
                metadata = staging / "lib" / "python3.11" / "site-packages" / "allinluna-2.0.0rc3.dist-info"
                metadata.mkdir(parents=True, exist_ok=True)
                (metadata / "METADATA").write_text("Name: allinluna\nVersion: 2.0.0rc3\n", encoding="utf-8")
                (metadata / "WHEEL").write_text("Wheel-Version: 1.0\n", encoding="utf-8")
                (metadata / "RECORD").write_text("", encoding="utf-8")
                return 0, "", ""
            if "-c" in tokens and any("import importlib.metadata" in token for token in tokens):
                return 0, json.dumps({"python": [3, 11, 15], "name": "allinluna", "version": "2.0.0rc3", "dependencies": []}) + "\n", ""
            return original_run(argv, *args, **kwargs)

        with mock.patch.object(bootstrap, "_allinluna_python", return_value=interpreter), mock.patch.object(
            bootstrap, "_run_argv", side_effect=fake_run
        ), mock.patch.object(bootstrap, "_open_exact_url", return_value=Response()):
            first, first_code = self._run(
                "apply",
                allinluna_component=component_state,
                create_fake_venv=False,
                lock_mutator=mutate_lock,
            )
            self.assertEqual(first_code, 0, first)
            self.assertEqual(first["status"], "ready")
            stored, stored_error = bootstrap._read_existing_receipt(self.home)
            self.assertIsNone(stored_error)
            self.assertIsNotNone(stored)
            receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
            receipt_pair_before = (receipt_path.read_bytes(), sidecar_path.read_bytes())
            venv_owned = next(item for item in stored["owned_paths"] if item["relative_path"] == bootstrap.MANAGED_VENV_RELATIVE)
            stable = {key: venv_owned[key] for key in ("device", "inode", "sha256")}
            generation = stored["generation"]
            plugin_state = self.home / "plugin-state.json"
            self.assertTrue(plugin_state.exists())
            plugin_state_before = plugin_state.read_bytes()
            installer_count = len(installer_calls)
            plugin_mutation_count = len(plugin_mutations)

            checked, checked_code = self._run(
                "check",
                allinluna_component=component_state,
                create_fake_venv=False,
                lock_mutator=mutate_lock,
            )
            self.assertEqual(checked_code, 0, checked)
            self.assertEqual(checked["status"], "ready")
            self.assertEqual(bootstrap._public_receipt(checked)["existing_receipt"], "present")
            self.assertEqual(len(installer_calls), installer_count)
            self.assertEqual(len(plugin_mutations), plugin_mutation_count)
            checked_stored, checked_error = bootstrap._read_existing_receipt(self.home)
            self.assertIsNone(checked_error)
            self.assertIsNotNone(checked_stored)
            self.assertEqual(checked_stored["generation"], generation)
            checked_owned = next(item for item in checked_stored["owned_paths"] if item["relative_path"] == bootstrap.MANAGED_VENV_RELATIVE)
            self.assertEqual({key: checked_owned[key] for key in ("device", "inode", "sha256")}, stable)
            self.assertEqual(bootstrap._receipt_state_digest(checked_stored), checked_stored["state_digest"])
            self.assertEqual((receipt_path.read_bytes(), sidecar_path.read_bytes()), receipt_pair_before)

            reapplied, reapplied_code = self._run(
                "apply",
                allinluna_component=component_state,
                create_fake_venv=False,
                lock_mutator=mutate_lock,
            )
            self.assertEqual(reapplied_code, 0, reapplied)
            self.assertEqual(reapplied["generation"], generation)
            self.assertEqual(reapplied["state_digest"], stored["state_digest"])
            self.assertEqual(bootstrap._public_receipt(reapplied)["existing_receipt"], "present")
            self.assertEqual(len(installer_calls), installer_count)
            self.assertEqual(len(plugin_mutations), plugin_mutation_count)
            self.assertEqual((receipt_path.read_bytes(), sidecar_path.read_bytes()), receipt_pair_before)

            uninstalled, uninstall_code = self._run(
                "uninstall",
                allinluna_component=component_state,
                create_fake_venv=False,
                lock_mutator=mutate_lock,
            )
            self.assertEqual(uninstall_code, 0, uninstalled)
            self.assertEqual(bootstrap._public_receipt(uninstalled)["existing_receipt"], "present")
            self.assertFalse(target.exists())
            self.assertEqual(plugin_state.read_bytes(), plugin_state_before)
            self.assertFalse(any(tokens[:2] == ("plugin", "remove") for tokens in plugin_mutations))

    def test_all_modes_report_existing_receipt_when_guardian_source_gate_fails_without_mutation(self):
        first, code = self._run("apply")
        self.assertEqual(code, 0, first)
        before = self._home_snapshot()

        for mode in ("check", "apply", "uninstall"):
            with self.subTest(mode=mode), mock.patch.object(
                bootstrap,
                "_verified_guardian_root",
                return_value=(None, "forced guardian provenance gate failure"),
            ):
                blocked, blocked_code = self._run(mode, create_fake_venv=False)

            projected = bootstrap._public_receipt(blocked)
            self.assertEqual(blocked_code, 1, blocked)
            self.assertEqual(projected["status"], "conflict" if mode == "uninstall" else "changes-required")
            self.assertEqual(projected["existing_receipt"], "present")
            self.assertEqual(self._home_snapshot(), before)

    def test_workflow_guardian_directory_gate_precedes_receipt_observation(self):
        first, code = self._run("apply")
        self.assertEqual(code, 0, first)
        before = self._home_snapshot()

        with mock.patch.object(
            bootstrap,
            "_validate_existing_workflow_guardian_directory",
            side_effect=bootstrap.BootstrapError("forced unsafe workflow-guardian directory"),
        ), mock.patch.object(bootstrap, "_read_existing_receipt", wraps=bootstrap._read_existing_receipt) as read_receipt:
            blocked, blocked_code = self._run("check", create_fake_venv=False)

        read_receipt.assert_not_called()
        self.assertEqual(blocked_code, 1, blocked)
        self.assertEqual(bootstrap._public_receipt(blocked)["existing_receipt"], "absent")
        self.assertEqual(self._home_snapshot(), before)

    def test_apply_recovery_preserves_present_invocation_start_receipt_marker(self):
        installed, installed_code = self._run("apply")
        self.assertEqual(installed_code, 0, installed)
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        pair_before = (receipt_path.read_bytes(), sidecar_path.read_bytes())
        generation = installed["generation"]
        state_digest = installed["state_digest"]
        self.assertIsNone(bootstrap._write_journal(self.home, self._receipt_only_journal()))

        def recover_without_changing_receipt(*_args):
            self.assertTrue(bootstrap._remove_journal(self.home))
            return True, "recovered"

        with mock.patch.object(bootstrap, "_recover_apply_journal", side_effect=recover_without_changing_receipt):
            result, result_code = self._run("apply")

        self.assertEqual(result_code, 0, result)
        self.assertEqual(bootstrap._public_receipt(result)["existing_receipt"], "present")
        self.assertEqual(result["generation"], generation)
        self.assertEqual(result["state_digest"], state_digest)
        self.assertEqual((receipt_path.read_bytes(), sidecar_path.read_bytes()), pair_before)
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_apply_recoveries_preserve_absent_invocation_start_receipt_marker(self):
        installed, installed_code = self._run("apply")
        self.assertEqual(installed_code, 0, installed)
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        recovered_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        recovered_receipt["existing_receipt"] = "present"
        bootstrap._finalize_receipt(recovered_receipt)
        self.assertTrue(bootstrap._remove_receipt_pair(self.home))
        journal = self._receipt_only_journal()
        self.assertIsNone(bootstrap._write_journal(self.home, journal))

        def recover_and_restore(*_args):
            self.assertIsNone(bootstrap._write_receipt(self.home, recovered_receipt)[1])
            self.assertTrue(bootstrap._remove_journal(self.home))
            return True, "recovered"

        with mock.patch.object(bootstrap, "_recover_apply_journal", side_effect=recover_and_restore):
            result, result_code = self._run("apply")
        self.assertEqual(result_code, 0, result)
        self.assertEqual(bootstrap._public_receipt(result)["existing_receipt"], "absent")
        self.assertNotIn("existing_receipt", result)
        stored, stored_error = bootstrap._read_existing_receipt(self.home)
        self.assertIsNone(stored_error)
        self.assertIsNotNone(stored)
        self.assertTrue(receipt_path.exists())
        self.assertTrue(sidecar_path.exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_allinluna_install_uses_copies_staging_and_no_replace(self):
        lock = bootstrap._load_lock()
        lock = json.loads(json.dumps(lock))
        wheel_bytes = b"test-wheel"
        wheel = lock["components"]["allinluna"]["pypi"]["wheel"]
        wheel["sha256"] = hashlib.sha256(wheel_bytes).hexdigest()
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter")
        interpreter.chmod(0o700)
        calls = []

        class Response:
            headers = {"Content-Length": str(len(wheel_bytes))}

            def __init__(self):
                self.stream = io.BytesIO(wheel_bytes)

            def settimeout(self, value):
                calls.append(["socket-timeout", value])

            def read(self, size=-1):
                calls.append(["read", size])
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_run(argv, *args, **kwargs):
            calls.append(list(argv))
            if "venv" in argv:
                staging = pathlib.Path(argv[-1])
                (staging / "bin").mkdir(parents=True, exist_ok=True)
                python = staging / "bin" / "python"
                python.write_text("python")
                python.chmod(0o700)
            return 0, "", ""

        planned = {"name": "allinluna", "status": "planned", "wheel_sha256": wheel["sha256"]}
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned), mock.patch.object(bootstrap, "_allinluna_python", return_value=interpreter), mock.patch.object(bootstrap, "_run_argv", side_effect=fake_run), mock.patch.object(bootstrap, "_open_exact_url", return_value=Response()), mock.patch.object(bootstrap, "_verify_allinluna_runtime", return_value=[]):
            component, root, created = bootstrap._allinluna_install(lock, self.home, None)
        self.assertEqual(component["status"], "installed")
        self.assertIsNotNone(root)
        self.assertEqual(created[0]["relative_path"], "venvs/allinluna")
        self.assertTrue(any("--copies" in call for call in calls))
        pip_calls = [call for call in calls if "-m" in call and "pip" in call]
        self.assertTrue(pip_calls)
        self.assertEqual(pathlib.Path(pip_calls[0][-1]).name, wheel["filename"])
        self.assertFalse(pathlib.Path(pip_calls[0][-1]).name.startswith(".allinluna-"))
        read_indexes = [index for index, call in enumerate(calls) if call[0] == "read"]
        self.assertTrue(read_indexes)
        self.assertTrue(all(index > 0 and calls[index - 1][0] == "socket-timeout" for index in read_indexes))
        self.assertGreaterEqual(sum(call[0] == "socket-timeout" for call in calls), len(read_indexes))
        self.assertTrue(root.exists())
        marker = json.loads((root / "guardian-install.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["dependencies"], [])
        self.assertEqual(list((self.home / "venvs").glob(".guardian-venv-*")), [])

    def test_allinluna_install_rejects_exact_wheel_symlink_collision(self):
        lock = json.loads(json.dumps(bootstrap._load_lock()))
        wheel_bytes = b"collision-wheel"
        wheel = lock["components"]["allinluna"]["pypi"]["wheel"]
        wheel["sha256"] = hashlib.sha256(wheel_bytes).hexdigest()
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter", encoding="utf-8")
        interpreter.chmod(0o700)
        foreign = self.root / "foreign-wheel"
        foreign.write_bytes(b"foreign")
        pip_calls = []

        class Response:
            headers = {"Content-Length": str(len(wheel_bytes))}

            def __init__(self):
                self.stream = io.BytesIO(wheel_bytes)

            def settimeout(self, value):
                del value

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_run(argv, *args, **kwargs):
            del args, kwargs
            tokens = [str(item) for item in argv]
            if "venv" in tokens:
                staging = pathlib.Path(tokens[-1])
                (staging / "bin").mkdir(parents=True, exist_ok=True)
                python = staging / "bin" / "python"
                python.write_text("python", encoding="utf-8")
                python.chmod(0o700)
                (staging / wheel["filename"]).symlink_to(foreign)
            elif "pip" in tokens:
                pip_calls.append(tokens)
            return 0, "", ""

        planned = {"name": "allinluna", "status": "planned", "wheel_sha256": wheel["sha256"]}
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned), mock.patch.object(
            bootstrap, "_allinluna_python", return_value=interpreter
        ), mock.patch.object(bootstrap, "_run_argv", side_effect=fake_run), mock.patch.object(
            bootstrap, "_open_exact_url", return_value=Response()
        ):
            component, root, created = bootstrap._allinluna_install(lock, self.home, None)
        self.assertEqual(component["status"], "unavailable")
        self.assertIsNone(root)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["relative_path"], "venvs/.guardian-venv-allinluna")
        self.assertIsNone(created[0]["sha256"])
        self.assertFalse(pip_calls)
        self.assertEqual(foreign.read_bytes(), b"foreign")
        stage = self.home / "venvs" / ".guardian-venv-allinluna"
        before = (stage.stat().st_ino, foreign.read_bytes())
        step = {
            "kind": "venv",
            "state": "building",
            "relative_path": "venvs/allinluna",
            "staging_relative_path": "venvs/.guardian-venv-allinluna",
            "sha256": None,
            "device": stage.stat().st_dev,
            "inode": stage.stat().st_ino,
        }
        self.assertFalse(bootstrap._recover_started_publish(self.home, step))
        self.assertEqual((stage.stat().st_ino, foreign.read_bytes()), before)

    def test_allinluna_install_rejects_exact_wheel_regular_collision_and_journal_retry_preserves_foreign(self):
        lock = json.loads(json.dumps(bootstrap._load_lock()))
        wheel_bytes = b"collision-wheel"
        wheel = lock["components"]["allinluna"]["pypi"]["wheel"]
        wheel["sha256"] = hashlib.sha256(wheel_bytes).hexdigest()
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter", encoding="utf-8")
        interpreter.chmod(0o700)
        pip_calls = []

        class Response:
            headers = {"Content-Length": str(len(wheel_bytes))}

            def __init__(self):
                self.stream = io.BytesIO(wheel_bytes)

            def settimeout(self, value):
                del value

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_run(argv, *args, **kwargs):
            del args, kwargs
            tokens = [str(item) for item in argv]
            if "venv" in tokens:
                staging = pathlib.Path(tokens[-1])
                (staging / "bin").mkdir(parents=True, exist_ok=True)
                python = staging / "bin" / "python"
                python.write_text("python", encoding="utf-8")
                python.chmod(0o700)
                (staging / wheel["filename"]).write_bytes(b"foreign-wheel")
            elif "pip" in tokens:
                pip_calls.append(tokens)
            return 0, "", ""

        planned = {"name": "allinluna", "status": "planned", "wheel_sha256": wheel["sha256"]}
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned), mock.patch.object(
            bootstrap, "_allinluna_python", return_value=interpreter
        ), mock.patch.object(bootstrap, "_run_argv", side_effect=fake_run), mock.patch.object(
            bootstrap, "_open_exact_url", return_value=Response()
        ):
            component, root, created = bootstrap._allinluna_install(lock, self.home, None)
        self.assertEqual(component["status"], "unavailable")
        self.assertIsNone(root)
        self.assertEqual(len(created), 1)
        self.assertIsNone(created[0]["sha256"])
        self.assertEqual(pip_calls, [])

        stage = self.home / "venvs" / ".guardian-venv-allinluna"
        wheel_path = stage / wheel["filename"]
        before = (stage.stat().st_ino, wheel_path.stat().st_ino, wheel_path.read_bytes())
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [{
                "id": "venv:allinluna",
                "kind": "venv",
                "action": "publish",
                "state": "building",
                "relative_path": "venvs/allinluna",
                "staging_relative_path": "venvs/.guardian-venv-allinluna",
                "sha256": None,
                "commit": None,
                "selector": None,
                "device": stage.stat().st_dev,
                "inode": stage.stat().st_ino,
            }, {
                "id": "receipt",
                "kind": "receipt",
                "action": "write",
                "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(),
                "sha256": None,
                "commit": None,
                "selector": None,
            }],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        recovered, reason = bootstrap._recover_apply_journal(
            self.home, journal, self.codex, lock, self.guardian_ref, self.git
        )
        self.assertFalse(recovered, reason)
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        self.assertEqual((stage.stat().st_ino, wheel_path.stat().st_ino, wheel_path.read_bytes()), before)

    def test_allinluna_install_preserves_replaced_staging_on_failure(self):
        lock = json.loads(json.dumps(bootstrap._load_lock()))
        wheel_bytes = b"replacement-wheel"
        wheel = lock["components"]["allinluna"]["pypi"]["wheel"]
        wheel["sha256"] = hashlib.sha256(wheel_bytes).hexdigest()
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter")
        interpreter.chmod(0o700)

        class Response:
            headers = {"Content-Length": str(len(wheel_bytes))}

            def __init__(self):
                self.stream = io.BytesIO(wheel_bytes)

            def settimeout(self, value):
                del value

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_run(argv, *args, **kwargs):
            del args, kwargs
            if "venv" in argv:
                staging = pathlib.Path(argv[-1])
                (staging / "bin").mkdir(parents=True, exist_ok=True)
                python = staging / "bin" / "python"
                python.write_text("python")
                python.chmod(0o700)
            return 0, "", ""

        def replace_then_fail(staging, *args):
            del args
            preserved = staging.parent / ".preserved-original"
            external = staging.parent / ".external-occupant"
            staging.rename(preserved)
            external.mkdir()
            (external / "foreign").write_text("foreign", encoding="utf-8")
            external.rename(staging)
            raise bootstrap.BootstrapError("simulated verification failure")

        planned = {"name": "allinluna", "status": "planned", "wheel_sha256": wheel["sha256"]}
        with mock.patch.object(bootstrap, "_allinluna_component", return_value=planned), mock.patch.object(bootstrap, "_allinluna_python", return_value=interpreter), mock.patch.object(bootstrap, "_run_argv", side_effect=fake_run), mock.patch.object(bootstrap, "_open_exact_url", return_value=Response()), mock.patch.object(bootstrap, "_verify_allinluna_runtime", side_effect=replace_then_fail):
            component, root, created = bootstrap._allinluna_install(lock, self.home, None)
        self.assertEqual(component["status"], "unavailable")
        self.assertIsNone(root)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["relative_path"], "venvs/.guardian-venv-allinluna")
        self.assertIsNone(created[0]["sha256"])
        staging = self.home / "venvs" / ".guardian-venv-allinluna"
        self.assertTrue(staging.is_dir())
        self.assertEqual((staging / "foreign").read_text(encoding="utf-8"), "foreign")

    def test_allinluna_publish_same_tree_replacement_is_not_claimed(self):
        wheel_bytes = b"same-tree-wheel"
        wheel_sha256 = hashlib.sha256(wheel_bytes).hexdigest()
        interpreter = self.root / "python311"
        interpreter.write_text("interpreter")
        interpreter.chmod(0o700)

        class Response:
            headers = {"Content-Length": str(len(wheel_bytes))}

            def __init__(self):
                self.stream = io.BytesIO(wheel_bytes)

            def settimeout(self, value):
                del value

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        original_run = bootstrap._run_argv

        def fake_run(argv, *args, **kwargs):
            tokens = [str(item) for item in argv]
            if "venv" in tokens:
                staging = pathlib.Path(tokens[-1])
                (staging / "bin").mkdir(parents=True, exist_ok=True)
                python = staging / "bin" / "python"
                python.write_text("python")
                python.chmod(0o700)
                return 0, "", ""
            if "pip" in tokens:
                return 0, "", ""
            return original_run(argv, *args, **kwargs)

        original_identity = []
        preserved = []
        raced = [False]

        def race_before_final_hash(staging, *args):
            del args
            if raced[0]:
                return []
            raced[0] = True
            staging = pathlib.Path(staging)
            original_identity.append(bootstrap._identity(staging.lstat()))
            preserved_path = staging.with_name(".preserved-publish-venv")
            foreign_path = staging.with_name(".foreign-publish-venv")
            staging.rename(preserved_path)
            shutil.copytree(preserved_path, foreign_path)
            (foreign_path / "arbitrary-non-wheel.bin").write_bytes(b"foreign-race")
            foreign_path.rename(staging)
            preserved.append(preserved_path)
            return []

        planned = {"name": "allinluna", "status": "planned", "wheel_sha256": wheel_sha256}

        def mutate_lock(lock):
            lock["components"]["allinluna"]["pypi"]["wheel"]["sha256"] = wheel_sha256

        with mock.patch.object(bootstrap, "_allinluna_python", return_value=interpreter), mock.patch.object(
            bootstrap, "_run_argv", side_effect=fake_run
        ), mock.patch.object(bootstrap, "_open_exact_url", side_effect=lambda *args, **kwargs: Response()), mock.patch.object(
            bootstrap, "_verify_allinluna_runtime", side_effect=race_before_final_hash
        ):
            first, first_code = self._run(
                "apply",
                allinluna_component=planned,
                create_fake_venv=False,
                lock_mutator=mutate_lock,
            )
            self.assertEqual(first_code, 1, first)
            self.assertEqual(first["status"], "recovery-required")
            self.assertTrue(raced[0])
            self.assertTrue(original_identity)
            staging = self.home / "venvs" / ".guardian-venv-allinluna"
            self.assertTrue(staging.is_dir())
            foreign_inode = staging.stat().st_ino
            foreign_bytes = (staging / "arbitrary-non-wheel.bin").read_bytes()
            self.assertNotEqual(foreign_inode, original_identity[0]["inode"])
            self.assertEqual(foreign_bytes, b"foreign-race")
            self.assertTrue(any(
                item.get("action") == "preserved-unverified"
                and item.get("path") == "venvs/.guardian-venv-allinluna"
                for item in first["rollback"]["actions"]
            ))
            journal, error = bootstrap._read_journal(self.home)
            self.assertIsNone(error)
            self.assertIsNotNone(journal)
            step = next(item for item in journal["steps"] if item["id"] == "venv:allinluna")
            self.assertEqual(step["state"], "building")
            self.assertIsNone(step["sha256"])

            second, second_code = self._run(
                "apply",
                allinluna_component=planned,
                create_fake_venv=False,
                lock_mutator=mutate_lock,
            )
            self.assertEqual(second_code, 1, second)
            self.assertEqual(second["status"], "recovery-required")
            self.assertEqual(staging.stat().st_ino, foreign_inode)
            self.assertEqual((staging / "arbitrary-non-wheel.bin").read_bytes(), foreign_bytes)
            self.assertTrue(bootstrap._journal_path(self.home).exists())

            shutil.rmtree(staging)
            resolved_stage = preserved[0]
            for child in list(resolved_stage.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            resolved_stage.rename(staging)
            self.assertEqual(staging.stat().st_ino, original_identity[0]["inode"])
            self.assertEqual(list(staging.iterdir()), [])

            third, third_code = self._run(
                "apply",
                allinluna_component=planned,
                create_fake_venv=False,
                lock_mutator=mutate_lock,
            )
            self.assertEqual(third_code, 0, third)
            self.assertEqual(third["status"], "ready")
            self.assertFalse(bootstrap._journal_path(self.home).exists())
            self.assertTrue((self.home / "venvs" / "allinluna").is_dir())

    def test_unhashed_empty_staging_is_removed_without_recursive_delete(self):
        stage = self.home / "venvs" / ".guardian-venv-allinluna"
        stage.mkdir(parents=True)
        identity = bootstrap._identity(stage.lstat())
        with mock.patch.object(bootstrap, "_remove_tree_at", wraps=bootstrap._remove_tree_at) as remove_tree:
            self.assertTrue(bootstrap._remove_staged_artifact(stage, identity, self.home))
        remove_tree.assert_not_called()
        self.assertFalse(stage.exists())

    def test_uninstall_preflights_modified_paths_before_any_removal(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0)
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        receipt_pair_before = (receipt_path.read_bytes(), sidecar_path.read_bytes())
        target = self.home / receipt["owned_paths"][0]["relative_path"]
        target.write_text("modified")
        result, uninstall_code = self._run("uninstall")
        self.assertEqual(uninstall_code, 1)
        self.assertEqual(next(item for item in result["components"] if item["name"] == "uninstall")["status"], "conflict")
        projected = bootstrap._public_receipt(result)
        self.assertEqual(projected["existing_receipt"], "present")
        self.assertEqual(projected["conflict_summary"], {"count": 1, "categories": ["other"]})
        self.assertEqual(projected["conflicts"], [{"name": "uninstall", "reason": "other"}])
        self.assertTrue((self.home / "plugin-state.json").exists())
        self.assertEqual((receipt_path.read_bytes(), sidecar_path.read_bytes()), receipt_pair_before)

    def test_uninstall_serializes_and_recovers_receipt_owned_venv(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        venv_target = next(item for item in receipt["managed_targets"] if item["relative_path"] == bootstrap.MANAGED_VENV_RELATIVE)
        venv_target["ownership"] = "owned"
        venv_target["proof"] = "transaction-owned"
        receipt["owned_paths"].append({key: venv_target[key] for key in ("relative_path", "kind", "sha256", "device", "inode")})
        self.assertIsNone(bootstrap._write_receipt(self.home, receipt)[1])

        original_remove = bootstrap._remove_owned

        def fail_venv(path, *args, **kwargs):
            if path == self.home / bootstrap.MANAGED_VENV_RELATIVE:
                return "remove-failed"
            return original_remove(path, *args, **kwargs)

        with mock.patch.object(bootstrap, "_remove_owned", side_effect=fail_venv):
            pending, pending_code = self._run("uninstall")
        self.assertEqual(pending_code, 1)
        self.assertEqual(next(item for item in pending["components"] if item["name"] == "uninstall")["status"], "conflict")
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        venv_step = next(step for step in journal["steps"] if step["id"] == "venv:allinluna")
        self.assertEqual(venv_step["kind"], "venv")
        self.assertEqual(venv_step["relative_path"], bootstrap.MANAGED_VENV_RELATIVE)
        self.assertEqual(venv_step["state"], "intent")

        recovered, recovered_code = self._run("uninstall")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse((self.home / bootstrap.MANAGED_VENV_RELATIVE).exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_uninstall_removes_only_receipt_owned_state(self):
        _, code = self._run("apply")
        self.assertEqual(code, 0)
        with mock.patch.object(bootstrap, "_run_argv", wraps=bootstrap._run_argv) as run_argv:
            result, uninstall_code = self._run("uninstall")
        self.assertEqual(uninstall_code, 0, result)
        self.assertEqual(next(item for item in result["components"] if item["name"] == "uninstall")["status"], "present")
        plugin_remove_argvs = [
            list(call.args[0])
            for call in run_argv.call_args_list
            if call.args and "plugin" in call.args[0] and "remove" in call.args[0]
        ]
        self.assertEqual(plugin_remove_argvs, [])
        self.assertFalse((self.home / "workflow-guardian" / "bootstrap-receipt.json").exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertTrue((self.home / "agents").exists())
        self.assertEqual(list((self.home / "agents").iterdir()), [])

    def test_uninstall_completed_before_journal_clear_is_recovered(self):
        _, code = self._run("apply")
        self.assertEqual(code, 0)
        with mock.patch.object(bootstrap, "_remove_journal", return_value=False):
            pending, pending_code = self._run("uninstall")
        self.assertEqual(pending_code, 1)
        self.assertEqual(next(item for item in pending["components"] if item["name"] == "uninstall")["status"], "recovery-required")
        recovered, recovered_code = self._run("uninstall")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_uninstall_missing_owned_items_persist_done_and_close(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        for item in receipt["owned_paths"]:
            path = self.home / item["relative_path"]
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        self._write_codex()
        (self.home / "plugin-state.json").write_text(json.dumps([]), encoding="utf-8")
        result, uninstall_code = self._run("uninstall")
        self.assertEqual(uninstall_code, 0, result)
        self.assertFalse((self.home / "workflow-guardian" / "bootstrap-receipt.json").exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_uninstall_quarantine_baseexception_is_recovered_without_orphan(self):
        class HardCrash(BaseException):
            pass

        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        original_rename = bootstrap._rename_noreplace_at
        crashed = False

        def rename(directory_fd, source_name, target_name):
            nonlocal crashed
            if target_name.startswith(".guardian-remove-") and not crashed:
                original_rename(directory_fd, source_name, target_name)
                crashed = True
                raise HardCrash()
            if crashed and source_name.startswith(".guardian-remove-"):
                raise HardCrash()
            return original_rename(directory_fd, source_name, target_name)

        with mock.patch.object(bootstrap, "_rename_noreplace_at", side_effect=rename):
            with self.assertRaises(HardCrash):
                self._run("uninstall")
        self.assertTrue(any(path.name.startswith(".guardian-remove-") for path in self.home.rglob("*")))
        recovered, recovered_code = self._run("uninstall")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(any(path.name.startswith(".guardian-remove-") for path in self.home.rglob("*")))
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertFalse(bootstrap._receipt_paths(self.home)[0].exists())

    def test_uninstall_receipt_half_delete_baseexception_recovers_sidecar(self):
        class HardCrash(BaseException):
            pass

        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        original_unlink = bootstrap._unlink_at_owned
        crashed = False

        def unlink(directory_fd, name, ownership, digest=None):
            nonlocal crashed
            result = original_unlink(directory_fd, name, ownership, digest)
            if name == receipt_path.name and not crashed:
                crashed = True
                raise HardCrash()
            return result

        with mock.patch.object(bootstrap, "_unlink_at_owned", new=unlink):
            with self.assertRaises(HardCrash):
                self._run("uninstall")
        self.assertFalse(receipt_path.exists())
        self.assertTrue(sidecar_path.exists())
        recovered, recovered_code = self._run("uninstall")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())
        self.assertFalse(bootstrap._journal_path(self.home).exists())

    def test_uninstall_crash_after_remove_before_done_is_recovered(self):
        _, code = self._run("apply")
        self.assertEqual(code, 0)
        original_step = bootstrap._journal_step

        def fail_after_file_remove(home, journal, step_id, state):
            if step_id == "file:agents/verifier.toml" and state == "done":
                return "simulated process interruption"
            return original_step(home, journal, step_id, state)

        with mock.patch.object(bootstrap, "_journal_step", side_effect=fail_after_file_remove):
            pending, pending_code = self._run("uninstall")
        self.assertEqual(pending_code, 1)
        self.assertEqual(next(item for item in pending["components"] if item["name"] == "uninstall")["status"], "conflict")
        self.assertEqual(json.loads((self.home / "plugin-state.json").read_text(encoding="utf-8")), ["allinluna"])
        journal, error = bootstrap._read_journal(self.home)
        self.assertIsNone(error)
        self.assertIsNotNone(journal)
        self.assertEqual(next(step for step in journal["steps"] if step["id"] == "file:agents/verifier.toml")["state"], "intent")
        recovered, recovered_code = self._run("uninstall")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertFalse((self.home / "workflow-guardian" / "bootstrap-receipt.json").exists())

    def test_uninstall_first_remove_crash_keeps_original_receipt_and_skips_rewrite(self):
        class HardCrash(BaseException):
            pass

        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        before_pair = (receipt_path.read_bytes(), sidecar_path.read_bytes())
        original_remove = bootstrap._remove_owned
        crashed = False

        def remove_then_crash(path, *args, **kwargs):
            nonlocal crashed
            result = original_remove(path, *args, **kwargs)
            if result == "removed" and not crashed:
                crashed = True
                raise HardCrash()
            return result

        with mock.patch.object(bootstrap, "_remove_owned", side_effect=remove_then_crash), mock.patch.object(
            bootstrap, "_write_receipt", wraps=bootstrap._write_receipt
        ) as write_receipt:
            with self.assertRaises(HardCrash):
                self._run("uninstall")
        self.assertEqual((receipt_path.read_bytes(), sidecar_path.read_bytes()), before_pair)
        write_receipt.assert_not_called()
        self.assertTrue(bootstrap._journal_path(self.home).exists())

        recovered, recovered_code = self._run("uninstall")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())

    def test_uninstall_subprocess_exit_after_first_remove_keeps_original_receipt(self):
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        before_pair = (receipt_path.read_bytes(), sidecar_path.read_bytes())
        child = textwrap.dedent(
            """
            import importlib.util
            import os
            import pathlib
            import sys
            spec = importlib.util.spec_from_file_location("bootstrap_macos", sys.argv[1])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.platform.system = lambda: "Linux"
            home = pathlib.Path(sys.argv[2])
            guardian_root = module._resolver_marketplace_root(home, module._resolve_codex(sys.argv[3]))
            lock = module._load_lock(guardian_root)
            version = lock["components"]["guardian_plugin"]["version"]
            module.SCRIPT_PATH = home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "codex-workflow-guardian" / version / module.GUARDIAN_SCRIPT_RELATIVE
            original_load_lock = module._load_lock
            def fixture_lock(repository_root=None, expected_source_proofs=None, expected_root_proof=None):
                lock = original_load_lock(
                    repository_root,
                    expected_source_proofs=expected_source_proofs,
                    expected_root_proof=expected_root_proof,
                )
                plugin_path = home / "plugins" / "cache" / "onebigmoon-codex-workflows" / "allinluna" / "2.0.0-rc.3"
                proof = module._content_tree_proof(plugin_path, home)
                if proof is None:
                    proof = {"tree_sha256": module._sha256_bytes(module.PLUGIN_TREE_ALGORITHM.encode("ascii") + b"\\0"), "entries": 0, "directories": 0, "files": 0, "symlinks": 0, "file_bytes": 0}
                component = lock["components"]["allinluna"]
                component["content_tree_sha256"] = proof["tree_sha256"]
                component["content_tree_entries"] = proof["entries"]
                component["content_tree_directories"] = proof["directories"]
                component["content_tree_files"] = proof["files"]
                component["content_tree_symlinks"] = proof["symlinks"]
                component["content_tree_file_bytes"] = proof["file_bytes"]
                return lock
            module._load_lock = fixture_lock
            module._allinluna_component = lambda *args, **kwargs: {"name": "allinluna", "status": "present", "path_relative": "venvs/allinluna/bin/allinluna", "wheel_sha256": "0" * 64}
            original = module._remove_owned
            def crash(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if result == "removed":
                    os._exit(75)
                return result
            module._remove_owned = crash
            platform_calls = [0]
            def gate_then_linux():
                platform_calls[0] += 1
                return "Darwin" if platform_calls[0] <= 2 else "Linux"
            module.platform.system = gate_then_linux
            raise SystemExit(module.run(
                "uninstall",
                home,
                sys.argv[3],
                guardian_ref=sys.argv[4],
                git_bin=sys.argv[5],
            )[1])
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child, str(SCRIPT), str(self.home), str(self.codex), self.guardian_ref, str(self.git)],
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual((receipt_path.read_bytes(), sidecar_path.read_bytes()), before_pair)
        self.assertTrue(bootstrap._journal_path(self.home).exists())
        recovered, recovered_code = self._run("uninstall")
        self.assertEqual(recovered_code, 0, recovered)
        self.assertFalse(bootstrap._journal_path(self.home).exists())
        self.assertFalse(receipt_path.exists())
        self.assertFalse(sidecar_path.exists())

    def test_redirect_refusal(self):
        with mock.patch.object(bootstrap.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = bootstrap.BootstrapError("redirect")
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._open_exact_url("https://files.pythonhosted.org/x.whl", 1)

    def test_existing_venv_canary_is_not_executed_before_receipt_proof(self):
        root = self.home / "venvs" / "allinluna"
        (root / "bin").mkdir(parents=True)
        canary = root / "bin" / "python"
        canary.write_text("#!/bin/sh\nexit 0\n")
        canary.chmod(0o700)
        (root / "bin" / "allinluna").write_text("#!/bin/sh\nexit 0\n")
        (root / "bin" / "allinluna").chmod(0o700)
        (root / "guardian-bootstrap-owner.json").write_bytes(bootstrap.OWNER_MARKER_BYTES)
        (root / "guardian-install.json").write_text(json.dumps({"component": "allinluna", "dependencies": [], "version": "2.0.0rc3", "wheel_sha256": "0" * 64}))
        result = bootstrap._allinluna_component({"components": {"allinluna": {"pypi": {"wheel": {"sha256": "0" * 64}, "version": "2.0.0rc3"}}}}, self.home, {}, None)
        self.assertEqual(result["status"], "conflict")

    def test_existing_venv_stale_root_inode_never_executes_python(self):
        lock = bootstrap._load_lock()
        expected_hash = lock["components"]["allinluna"]["pypi"]["wheel"]["sha256"]
        root = self.home / "venvs" / "allinluna"
        metadata_dir = root / "lib" / "python3.11" / "site-packages" / "allinluna-2.0.0rc3.dist-info"
        metadata_dir.mkdir(parents=True)
        (root / "bin").mkdir(parents=True)
        for name in ("python", "allinluna"):
            path = root / "bin" / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o700)
        (root / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
        (root / "guardian-bootstrap-owner.json").write_bytes(bootstrap.OWNER_MARKER_BYTES)
        (root / "guardian-install.json").write_text(json.dumps({"component": "allinluna", "dependencies": [], "version": "2.0.0rc3", "wheel_sha256": expected_hash}), encoding="utf-8")
        (metadata_dir / "METADATA").write_text("Name: allinluna\nVersion: 2.0.0rc3\n", encoding="utf-8")
        (metadata_dir / "WHEEL").write_text("Wheel-Version: 1.0\n", encoding="utf-8")
        (metadata_dir / "RECORD").write_text("", encoding="utf-8")
        receipt, code = self._run("apply")
        self.assertEqual(code, 0, receipt)
        venv_target = next(item for item in receipt["managed_targets"] if item["relative_path"] == "venvs/allinluna")
        venv_target["ownership"] = "owned"
        venv_target["proof"] = "transaction-owned"
        receipt["owned_paths"].append({key: venv_target[key] for key in ("relative_path", "kind", "sha256", "device", "inode")})
        self.assertIsNone(bootstrap._write_receipt(self.home, receipt)[1])
        with mock.patch.object(bootstrap, "_verify_allinluna_runtime") as runtime:
            verified = bootstrap._allinluna_component(lock, self.home, {}, None)
        self.assertEqual(verified["status"], "present")
        runtime.assert_not_called()
        replacement = self.home / "venvs" / "replacement"
        shutil.copytree(root, replacement)
        shutil.rmtree(root)
        replacement.rename(root)
        with mock.patch.object(bootstrap, "_verify_allinluna_runtime") as runtime:
            result = bootstrap._allinluna_component(lock, self.home, {}, None)
        self.assertEqual(result["status"], "conflict")
        runtime.assert_not_called()

    def test_pending_journal_check_reports_recovery_without_mutation(self):
        journal = {"schema": bootstrap.JOURNAL_SCHEMA, "generation": 1, "digest": "", "mode": "apply", "phase": "APPLYING", "steps": [{"id": "receipt", "kind": "receipt", "action": "write", "state": "intent", "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(), "sha256": None, "commit": None, "selector": None}]}
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        before = sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*"))
        receipt, code = self._run("check")
        after = sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*"))
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "recovery-required")
        self.assertEqual(before, after)

    def test_pending_journal_check_reports_valid_prior_receipt(self):
        installed, installed_code = self._run("apply")
        self.assertEqual(installed_code, 0, installed)
        receipt_path, sidecar_path = bootstrap._receipt_paths(self.home)
        receipt_pair_before = (receipt_path.read_bytes(), sidecar_path.read_bytes())
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "apply",
            "phase": "APPLYING",
            "steps": [{
                "id": "receipt",
                "kind": "receipt",
                "action": "write",
                "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(),
                "sha256": None,
                "commit": None,
                "selector": None,
            }],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        before = sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*"))
        result, code = self._run("check")
        after = sorted(path.relative_to(self.home).as_posix() for path in self.home.rglob("*"))
        self.assertEqual(code, 1, result)
        self.assertEqual(result["status"], "recovery-required")
        self.assertEqual(bootstrap._public_receipt(result)["existing_receipt"], "present")
        self.assertEqual((receipt_path.read_bytes(), sidecar_path.read_bytes()), receipt_pair_before)
        self.assertEqual(before, after)

    def test_apply_rejects_pending_uninstall_before_any_new_transaction(self):
        journal = {
            "schema": bootstrap.JOURNAL_SCHEMA,
            "generation": 1,
            "digest": "",
            "mode": "uninstall",
            "phase": "UNINSTALLING",
            "steps": [{
                "id": "receipt",
                "kind": "receipt",
                "action": "remove",
                "state": "intent",
                "relative_path": bootstrap.RECEIPT_RELATIVE.as_posix(),
                "sha256": None,
                "commit": None,
                "selector": None,
            }],
        }
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        journal_path = bootstrap._journal_path(self.home)
        before = journal_path.read_bytes()
        receipt, code = self._run("apply")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "recovery-required")
        self.assertIn("does not match", receipt["recovery"])
        self.assertEqual(journal_path.read_bytes(), before)

    def test_remove_journal_preserves_unproven_temporary(self):
        journal = self._receipt_only_journal()
        self.assertIsNone(bootstrap._write_journal(self.home, journal))
        journal_path = bootstrap._journal_path(self.home)
        temporary = journal_path.parent / ".guardian-journal.tmp"
        temporary.write_bytes(b"")
        before = journal_path.read_bytes()
        self.assertFalse(bootstrap._remove_journal(self.home))
        self.assertEqual(journal_path.read_bytes(), before)
        self.assertTrue(temporary.exists())


if __name__ == "__main__":
    unittest.main()
