import ast
import builtins
import contextlib
import importlib.util
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOCTOR_PATH = SCRIPTS / "doctor.py"
POSTFLIGHT_PATH = SCRIPTS / "postflight.py"


_READ_ONLY_OPEN_FLAGS = frozenset(
    {
        "O_RDONLY",
        "O_CLOEXEC",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
        "O_NOCTTY",
        "O_BINARY",
        "O_NOINHERIT",
        "O_DSYNC",
        "O_RSYNC",
        "O_SYNC",
    }
)
_WRITE_OPEN_FLAGS = frozenset(
    {"O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_EXCL", "O_TRUNC", "O_TMPFILE"}
)
_FILESYSTEM_MUTATION_METHODS = frozenset(
    {
        "chmod",
        "chown",
        "copy",
        "copy2",
        "copyfile",
        "copyfileobj",
        "copytree",
        "fchmod",
        "fchown",
        "ftruncate",
        "hardlink_to",
        "lchmod",
        "lchown",
        "link",
        "link_to",
        "makedirs",
        "mkdir",
        "move",
        "mkdtemp",
        "mkstemp",
        "open",  # Path.open/io.open can select a write mode.
        "remove",
        "rename",
        "replace",
        "rmdir",
        "rmtree",
        "symlink",
        "symlink_to",
        "TemporaryFile",
        "NamedTemporaryFile",
        "touch",
        "truncate",
        "unlink",
        "utime",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
    }
)
_OS_PROCESS_EXECUTION_METHODS = frozenset(
    {
        "system",
        "popen",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "posix_spawn",
        "posix_spawnp",
    }
)
_MODE_BINDINGS = frozenset(
    {"os-open", "os-fdopen", "builtin-open", "open-method", "bound-open-method"}
)
_WRITE_MODE_MARKERS = frozenset("wax+")


def _literal_string(node):
    if isinstance(node, ast.Index):
        node = node.value
    value = getattr(node, "value", None)
    return value if isinstance(value, str) else None


def _call_mode(node, *, receiver_method=False):
    if receiver_method and node.args:
        return node.args[0]
    if len(node.args) >= 2:
        return node.args[1]
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


def _flag_status(node, bindings):
    """Return True for known read-only flags, False for write flags, else None."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            if node.value == 0:
                return True  # O_RDONLY is zero on the supported POSIX host.
            if node.value in {1, 2, 3} or node.value & (8 | 0x200 | 0x400 | 0x800):
                return False
        return None
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute):
        if node.attr in _WRITE_OPEN_FLAGS:
            return False
        if node.attr in _READ_ONLY_OPEN_FLAGS:
            return True
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _flag_status(node.left, bindings)
        right = _flag_status(node.right, bindings)
        if left is False or right is False:
            return False
        if left is True and right is True:
            return True
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        if len(node.args) >= 2:
            name = _literal_string(node.args[1])
            if name in _WRITE_OPEN_FLAGS:
                return False
            if name in _READ_ONLY_OPEN_FLAGS:
                return True
        return None
    return None


def _filesystem_mutations(source):
    """Find obvious stdlib filesystem mutations without executing source.

    Callable aliases are rejected when bound and when called; ``open``/
    ``fdopen`` and ``os.open`` stay mode/flags-sensitive so read-only controls
    remain valid.
    """
    tree = ast.parse(source)
    imported_os_names = {"os"}
    imported_io_names = {"io"}
    os_open_names = set()
    os_fdopen_names = set()
    builtin_open_names = {"open"}
    imported_mutators = {}

    violations = []

    reported = set()

    def report(node, code):
        key = (getattr(node, "lineno", -1), getattr(node, "col_offset", -1), code)
        if key not in reported:
            reported.add(key)
            violations.append((key[0], key[1], code))

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.bindings = {}
            self.mutation_bindings = dict(imported_mutators)
            self.os_aliases = set(imported_os_names)
            self.io_aliases = set(imported_io_names)
            self.os_open_names = set(os_open_names)
            self.os_fdopen_names = set(os_fdopen_names)
            self.builtin_open_names = set(builtin_open_names)
            self.callable_returns = {}
            self.dynamic_module_aliases = set()
            self.os_attribute_aliases = set()
            self.dynamic_attribute_aliases = set()
            self.return_bindings = None

        def _bind(self, target, status):
            if isinstance(target, ast.Starred):
                target = target.value
            if isinstance(target, ast.Name):
                self.bindings[target.id] = status
            elif isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    self._bind(item, None)

        def _bind_mutation(self, target, binding):
            if isinstance(target, ast.Starred):
                target = target.value
            if isinstance(target, ast.Name):
                self.mutation_bindings[target.id] = binding
            elif isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    self._bind_mutation(item, None)

        def _bind_os_alias(self, target, value):
            if isinstance(target, ast.Starred):
                target = target.value
            is_alias = isinstance(value, ast.Name) and value.id in self.os_aliases
            if isinstance(target, ast.Name):
                if is_alias:
                    self.os_aliases.add(target.id)
                else:
                    self.os_aliases.discard(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    self._bind_os_alias(item, ast.Constant(value=None))

        def _bind_io_alias(self, target, value):
            if isinstance(target, ast.Starred):
                target = target.value
            is_alias = isinstance(value, ast.Name) and value.id in self.io_aliases
            if isinstance(target, ast.Name):
                if is_alias:
                    self.io_aliases.add(target.id)
                else:
                    self.io_aliases.discard(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    self._bind_io_alias(item, ast.Constant(value=None))

        def _shadow_callable_aliases(self, target):
            if isinstance(target, ast.Starred):
                target = target.value
            if isinstance(target, ast.Name):
                self.os_open_names.discard(target.id)
                self.os_fdopen_names.discard(target.id)
                self.builtin_open_names.discard(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    self._shadow_callable_aliases(item)

        def _qualified_name(self, node):
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                parent = self._qualified_name(node.value)
                return parent + "." + node.attr if parent else None
            return None

        def _is_dynamic_module_value(self, node):
            qualified = self._qualified_name(node)
            if qualified in self.dynamic_module_aliases or qualified in self.dynamic_attribute_aliases:
                return True
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    return not self._is_os_module_value(node)
                if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                    return not self._is_os_module_value(node)
            return False

        def _bind_module_alias(self, target, value):
            is_os = self._is_os_module_value(value)
            is_dynamic = not is_os and self._is_dynamic_module_value(value)
            if isinstance(target, ast.Starred):
                target = target.value
            if isinstance(target, ast.Name):
                self.os_aliases.discard(target.id)
                self.dynamic_module_aliases.discard(target.id)
                if is_os:
                    self.os_aliases.add(target.id)
                elif is_dynamic:
                    self.dynamic_module_aliases.add(target.id)
            elif isinstance(target, ast.Attribute):
                qualified = self._qualified_name(target)
                if qualified:
                    self.os_attribute_aliases.discard(qualified)
                    self.dynamic_attribute_aliases.discard(qualified)
                    if is_os:
                        self.os_attribute_aliases.add(qualified)
                    elif is_dynamic:
                        self.dynamic_attribute_aliases.add(qualified)
            elif isinstance(target, (ast.Tuple, ast.List)):
                values = value.elts if isinstance(value, (ast.Tuple, ast.List)) else ()
                for index, item in enumerate(target.elts):
                    item_value = values[index] if index < len(values) else None
                    self._bind_module_alias(item, item_value)

        def _binding_needs_rejection(self, binding):
            return binding is not None and binding != "filesystem-mutation:dynamic"

        def _module_storage_binding(self, value):
            """Classify an os/dynamic module only when it is stored or passed."""
            qualified = self._qualified_name(value)
            if isinstance(value, ast.Name) and value.id in self.os_aliases:
                return "filesystem-mutation:dynamic"
            if qualified in self.os_attribute_aliases or qualified in self.dynamic_attribute_aliases:
                return "filesystem-mutation:dynamic"
            if isinstance(value, ast.Call):
                if isinstance(value.func, ast.Name) and value.func.id == "__import__":
                    return "filesystem-mutation:dynamic"
                if isinstance(value.func, ast.Attribute) and value.func.attr == "import_module":
                    return "filesystem-mutation:dynamic"
            if self._is_dynamic_module_value(value):
                return "filesystem-mutation:dynamic"
            return None

        def _report_stored_value(self, value):
            """Reject sensitive capabilities when they cross a storage boundary."""
            if value is None:
                return
            binding = self._binding_from_value(value)
            explicit_dynamic = (
                self._getattr_binding(value) == "filesystem-mutation:dynamic"
                or self._dunder_lookup_binding(value) == "filesystem-mutation:dynamic"
                or self._subscript_binding(value, include_dynamic=True) == "filesystem-mutation:dynamic"
                or (
                    isinstance(value, ast.Call)
                    and self._mapping_lookup_binding(value.func) == "filesystem-mutation:dynamic"
                )
            )
            if binding is not None and (
                binding != "filesystem-mutation:dynamic" or explicit_dynamic
            ):
                report(value, binding)
                return
            module_binding = self._module_storage_binding(value)
            if module_binding is not None:
                report(value, module_binding)
                return
            if isinstance(value, ast.NamedExpr):
                self._report_stored_value(value.value)
            elif isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
                for child in ast.iter_child_nodes(value):
                    self._report_stored_value(child)
            elif isinstance(value, ast.Subscript):
                self._report_stored_value(value.value)
                self._report_stored_value(value.slice)
            elif isinstance(value, ast.Attribute):
                # A direct os/path callable was handled above.  Recurse only
                # through computed receivers such as SimpleNamespace(...).m.
                if isinstance(value.value, (ast.Call, ast.Subscript, ast.Attribute)):
                    self._report_stored_value(value.value)
            elif isinstance(value, ast.Call):
                # Known getattr/dunder/lambda/function results were handled by
                # _binding_from_value; unknown call results are not guessed.
                if (
                    isinstance(value.func, ast.Name)
                    and value.func.id == "getattr"
                    and len(value.args) >= 2
                    and _literal_string(value.args[1]) in _READ_ONLY_OPEN_FLAGS | _WRITE_OPEN_FLAGS
                ):
                    return
                for argument in value.args:
                    self._report_stored_value(argument)
                for keyword in value.keywords:
                    self._report_stored_value(keyword.value)
            elif isinstance(value, ast.Lambda):
                for default in (*value.args.defaults, *value.args.kw_defaults):
                    if default is not None:
                        self._report_stored_value(default)
                self._report_stored_value(value.body)
            else:
                for child in ast.iter_child_nodes(value):
                    self._report_stored_value(child)

        def _bind_assignment(self, target, value):
            """Bind parallel assignments, including tuple destructuring aliases."""
            if isinstance(target, ast.Starred):
                target = target.value
            if isinstance(target, (ast.Tuple, ast.List)):
                values = value.elts if isinstance(value, (ast.Tuple, ast.List)) else ()
                for index, item in enumerate(target.elts):
                    item_value = values[index] if index < len(values) else None
                    self._bind_assignment(item, item_value)
                return
            self._shadow_callable_aliases(target)
            status = _flag_status(value, self.bindings) if value is not None else None
            mutation = self._binding_from_value(value, include_dynamic=True) if value is not None else None
            self._bind(target, status)
            self._bind_mutation(target, mutation)
            self._bind_os_alias(target, value if value is not None else ast.Constant(value=None))
            self._bind_io_alias(target, value if value is not None else ast.Constant(value=None))
            self._bind_module_alias(target, value)
            self._report_stored_value(value)
            if isinstance(target, ast.Name):
                return_binding = self._lambda_return_binding(value)
                if return_binding is None:
                    self.callable_returns.pop(target.id, None)
                else:
                    self.callable_returns[target.id] = return_binding
            deferred_dynamic = mutation == "filesystem-mutation:dynamic" and (
                isinstance(value, ast.Subscript)
                or (
                    isinstance(value, ast.Call)
                    and self._getattr_binding(value) is None
                    and self._dunder_lookup_binding(value) is None
                    and self._mapping_lookup_binding(value.func) is None
                    and self._lambda_return_binding(value) is None
                    and not (
                        isinstance(value.func, ast.Name)
                        and value.func.id in self.callable_returns
                    )
                )
            )
            if self._binding_needs_rejection(mutation) or mutation in _MODE_BINDINGS:
                if not deferred_dynamic:
                    report(target, mutation)
            if mutation == "filesystem-mutation:dynamic" and (
                self._is_os_module_value(value) or self._is_dynamic_module_value(value)
            ):
                report(target, mutation)
                report(target, mutation)

        def _shadow_name(self, name):
            if isinstance(name, str):
                target = ast.Name(id=name)
                self._bind_assignment(target, None)

        def _bind_import(self, node):
            for alias in node.names:
                if alias.name == "*":
                    report(node, "filesystem-mutation:dynamic")
                    continue
                root = alias.name.split(".", 1)[0]
                bound_name = alias.asname or root
                self._shadow_name(bound_name)
                if root == "os":
                    self.os_aliases.add(bound_name)
                elif root == "io":
                    self.io_aliases.add(bound_name)

        def _bind_import_from(self, node):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    report(node, "filesystem-mutation:dynamic")
                    continue
                bound_name = alias.asname or alias.name
                self._shadow_name(bound_name)
                if module == "os" and alias.name == "open":
                    self.os_open_names.add(bound_name)
                    report(node, "open-callable-alias")
                elif module == "os" and alias.name == "fdopen":
                    self.os_fdopen_names.add(bound_name)
                    report(node, "open-callable-alias")
                elif module == "os" and alias.name in _OS_PROCESS_EXECUTION_METHODS:
                    binding = "process-execution:" + alias.name
                    self.mutation_bindings[bound_name] = binding
                    report(node, binding)
                elif module in {"os", "shutil", "tempfile"} and alias.name in _FILESYSTEM_MUTATION_METHODS:
                    binding = "filesystem-mutation:" + alias.name
                    self.mutation_bindings[bound_name] = binding
                    report(node, binding)
                elif module in {"builtins", "io"} and alias.name == "open":
                    self.builtin_open_names.add(bound_name)
                    report(node, "open-callable-alias")

        def _bind_augassign(self, node):
            target = node.target
            if not isinstance(target, ast.Name):
                return
            self._shadow_callable_aliases(target)
            self.mutation_bindings[target.id] = None
            self.os_aliases.discard(target.id)
            self.io_aliases.discard(target.id)
            if isinstance(node.op, ast.BitOr):
                current = self.bindings.get(target.id)
                added = _flag_status(node.value, self.bindings)
                self.bindings[target.id] = (
                    False
                    if current is False or added is False
                    else True
                    if current is True and added is True
                    else None
                )
            else:
                self.bindings[target.id] = None

        def _is_os_module_value(self, node):
            qualified = self._qualified_name(node)
            if qualified in self.os_attribute_aliases:
                return True
            if qualified in self.dynamic_attribute_aliases:
                return False
            if isinstance(node, ast.Name):
                return node.id in self.os_aliases
            if isinstance(node, ast.Attribute):
                return self._is_os_module_value(node.value)
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    return bool(node.args) and _literal_string(node.args[0]) == "os"
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and node.args
                ):
                    return _literal_string(node.args[0]) == "os"
            return False

        def _dunder_lookup_binding(self, node):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                return None
            if node.func.attr not in {"__getattribute__", "__getattr__"}:
                return None
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "object":
                receiver = node.args[0] if len(node.args) >= 2 else None
                name = _literal_string(node.args[1]) if len(node.args) >= 2 else None
            else:
                receiver = node.func.value
                name = _literal_string(node.args[0]) if node.args else None
            if name == "open":
                if self._is_os_module_value(receiver):
                    return "os-open"
                return "bound-open-method"
            if name == "fdopen":
                return "os-fdopen" if self._is_os_module_value(receiver) else None
            if name in _OS_PROCESS_EXECUTION_METHODS and self._is_os_module_value(receiver):
                return "process-execution:" + name
            if name in _FILESYSTEM_MUTATION_METHODS:
                return "filesystem-mutation:" + name
            if name is None:
                return "filesystem-mutation:dynamic"
            if self._is_os_module_value(receiver):
                return "filesystem-mutation:dynamic"
            return None

        def _lambda_return_binding(self, value):
            if not isinstance(value, ast.Lambda):
                return None
            return self._binding_from_value(value.body, include_dynamic=True)

        def _attribute_binding(self, function):
            if not isinstance(function, ast.Attribute):
                return None
            value = function.value
            while isinstance(value, ast.Attribute):
                value = value.value
            root = value.id if isinstance(value, ast.Name) else None
            if function.attr == "open":
                if root in self.os_aliases:
                    return "os-open"
                if root in self.io_aliases:
                    return "builtin-open"
                if isinstance(value, ast.Name) and value.id[:1].isupper():
                    return "open-method"
                return "bound-open-method"
            if function.attr == "fdopen" and root in self.os_aliases:
                return "os-fdopen"
            if function.attr in _OS_PROCESS_EXECUTION_METHODS and self._is_os_module_value(function.value):
                return "process-execution:" + function.attr
            if function.attr in _FILESYSTEM_MUTATION_METHODS:
                return "filesystem-mutation:" + function.attr
            return None

        def _getattr_binding(self, node):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
            ):
                return None
            receiver = node.args[0]
            root = receiver.id if isinstance(receiver, ast.Name) else None
            name = _literal_string(node.args[1])
            if name == "open":
                if root in self.os_aliases:
                    return "os-open"
                if root in self.io_aliases:
                    return "builtin-open"
                if isinstance(receiver, ast.Name) and receiver.id[:1].isupper():
                    return "open-method"
                return "bound-open-method"
            if name == "fdopen":
                return "os-fdopen" if root in self.os_aliases else None
            if name in _OS_PROCESS_EXECUTION_METHODS and self._is_os_module_value(receiver):
                return "process-execution:" + name
            if name in _FILESYSTEM_MUTATION_METHODS:
                return "filesystem-mutation:" + name
            if name is None:
                return "filesystem-mutation:dynamic"
            if root not in self.os_aliases:
                return None
            if name not in _READ_ONLY_OPEN_FLAGS | _WRITE_OPEN_FLAGS:
                return "filesystem-mutation:dynamic"
            return None

        def _subscript_binding(self, node, *, include_dynamic=False):
            if not isinstance(node, ast.Subscript):
                return None
            key = node.slice
            name = _literal_string(key)
            if name in _OS_PROCESS_EXECUTION_METHODS and (
                self._is_os_module_value(node.value) or self._is_dunder_dict(node.value)
            ):
                return "process-execution:" + name
            if name in _FILESYSTEM_MUTATION_METHODS:
                return "bound-open-method" if name == "open" else "filesystem-mutation:" + name
            if name is None and include_dynamic:
                return "filesystem-mutation:dynamic"
            return None

        def _is_dunder_dict(self, node):
            if isinstance(node, ast.Attribute) and node.attr == "__dict__":
                return True
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and _literal_string(node.args[1]) == "__dict__"
            ):
                return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "vars":
                return True
            return False

        def _mapping_lookup_binding(self, node):
            if not isinstance(node, ast.Attribute):
                return None
            if node.attr in {"get", "pop", "setdefault", "__getitem__"} and self._is_dunder_dict(node.value):
                return "filesystem-mutation:dynamic"
            return None

        def _visit_definition_expressions(self, node):
            for decorator in node.decorator_list:
                self._report_stored_value(decorator)
                self.visit(decorator)
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    self._report_stored_value(default)
                    self.visit(default)
            arguments = (
                *getattr(node.args, "posonlyargs", ()),
                *node.args.args,
                *node.args.kwonlyargs,
            )
            if node.args.vararg is not None:
                arguments += (node.args.vararg,)
            if node.args.kwarg is not None:
                arguments += (node.args.kwarg,)
            for argument in arguments:
                if argument.annotation is not None:
                    self._report_stored_value(argument.annotation)
                    self.visit(argument.annotation)
            if node.returns is not None:
                self._report_stored_value(node.returns)
                self.visit(node.returns)

        def _binding_from_value(self, value, *, include_dynamic=False):
            if isinstance(value, ast.NamedExpr):
                return self._binding_from_value(value.value, include_dynamic=include_dynamic)
            binding = self._attribute_binding(value)
            if binding is not None:
                return binding
            binding = self._getattr_binding(value)
            if binding is not None:
                return binding
            binding = self._dunder_lookup_binding(value)
            if binding is not None:
                return binding
            binding = self._subscript_binding(value, include_dynamic=include_dynamic)
            if binding is not None:
                return binding
            if isinstance(value, ast.Call):
                binding = self._mapping_lookup_binding(value.func)
                if binding is not None:
                    return binding
                if isinstance(value.func, ast.Lambda):
                    binding = self._lambda_return_binding(value.func)
                    if binding is not None:
                        return binding
                if isinstance(value.func, ast.Name):
                    binding = self.callable_returns.get(value.func.id)
                    if binding is not None:
                        return binding
                if include_dynamic:
                    return "filesystem-mutation:dynamic"
            if isinstance(value, ast.Lambda):
                return self._lambda_return_binding(value)
            if isinstance(value, ast.Name):
                if value.id in self.os_open_names:
                    return "os-open"
                if value.id in self.os_fdopen_names:
                    return "os-fdopen"
                if value.id in self.builtin_open_names:
                    return "builtin-open"
                return self.mutation_bindings.get(value.id)
            return None

        def _report_bound_call(self, node, binding):
            if binding == "os-open":
                flags = node.args[1] if len(node.args) >= 2 else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "flags"), None
                )
                status = _flag_status(flags, self.bindings) if flags is not None else None
                if status is not True:
                    report(node, "os-open-write-flags" if status is False else "os-open-unknown-flags")
            elif binding in ("os-fdopen", "builtin-open", "open-method", "bound-open-method"):
                receiver_method = binding == "bound-open-method"
                mode = _call_mode(node, receiver_method=receiver_method)
                mode_text = _literal_string(mode) if mode is not None else "r"
                if mode is not None and (
                    mode_text is None or any(marker in mode_text for marker in _WRITE_MODE_MARKERS)
                ):
                    report(node, "open-write-mode" if mode_text is not None else "open-unknown-mode")
            else:
                report(node, binding)

        def visit_Assign(self, node):
            for target in node.targets:
                self._bind_assignment(target, node.value)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            self._bind_assignment(node.target, node.value)
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            self._bind_augassign(node)
            self.generic_visit(node)

        def visit_Import(self, node):
            self._bind_import(node)

        def visit_ImportFrom(self, node):
            self._bind_import_from(node)

        def visit_NamedExpr(self, node):
            self._bind_assignment(node.target, node.value)
            self.generic_visit(node)

        def visit_Delete(self, node):
            for target in node.targets:
                self._bind_assignment(target, None)
            self.generic_visit(node)

        def _bind_arguments(self, arguments):
            items = (
                *getattr(arguments, "posonlyargs", ()),
                *arguments.args,
                *arguments.kwonlyargs,
            )
            for argument in items:
                self._bind_assignment(ast.Name(id=argument.arg), None)
            if arguments.vararg is not None:
                self._bind_assignment(ast.Name(id=arguments.vararg.arg), None)
            if arguments.kwarg is not None:
                self._bind_assignment(ast.Name(id=arguments.kwarg.arg), None)

        def visit_FunctionDef(self, node):
            # Python evaluates decorators, then default expressions, before
            # entering the function body.  Keep those expressions in the
            # defining scope so mutators hidden there cannot evade the gate.
            self._visit_definition_expressions(node)
            self._shadow_name(node.name)
            saved = self.bindings
            saved_mutations = self.mutation_bindings
            saved_os_aliases = self.os_aliases
            saved_io_aliases = self.io_aliases
            saved_os_open_names = self.os_open_names
            saved_os_fdopen_names = self.os_fdopen_names
            saved_builtin_open_names = self.builtin_open_names
            saved_callable_returns = self.callable_returns
            saved_dynamic_module_aliases = self.dynamic_module_aliases
            saved_os_attribute_aliases = self.os_attribute_aliases
            saved_dynamic_attribute_aliases = self.dynamic_attribute_aliases
            saved_return_bindings = self.return_bindings
            self.bindings = dict(self.bindings)
            self.mutation_bindings = dict(self.mutation_bindings)
            self.os_aliases = set(self.os_aliases)
            self.io_aliases = set(self.io_aliases)
            self.os_open_names = set(self.os_open_names)
            self.os_fdopen_names = set(self.os_fdopen_names)
            self.builtin_open_names = set(self.builtin_open_names)
            self.callable_returns = dict(self.callable_returns)
            self.dynamic_module_aliases = set(self.dynamic_module_aliases)
            self.os_attribute_aliases = set(self.os_attribute_aliases)
            self.dynamic_attribute_aliases = set(self.dynamic_attribute_aliases)
            self.return_bindings = []
            self._bind_arguments(node.args)
            for statement in node.body:
                self.visit(statement)
            function_returns = tuple(self.return_bindings)
            self.bindings = saved
            self.mutation_bindings = saved_mutations
            self.os_aliases = saved_os_aliases
            self.io_aliases = saved_io_aliases
            self.os_open_names = saved_os_open_names
            self.os_fdopen_names = saved_os_fdopen_names
            self.builtin_open_names = saved_builtin_open_names
            self.callable_returns = saved_callable_returns
            self.dynamic_module_aliases = saved_dynamic_module_aliases
            self.os_attribute_aliases = saved_os_attribute_aliases
            self.dynamic_attribute_aliases = saved_dynamic_attribute_aliases
            self.return_bindings = saved_return_bindings
            if function_returns:
                unique_returns = set(function_returns)
                self.callable_returns[node.name] = (
                    unique_returns.pop()
                    if len(unique_returns) == 1
                    else "filesystem-mutation:dynamic"
                )

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    self._report_stored_value(default)
                    self.visit(default)
            saved = (
                self.bindings,
                self.mutation_bindings,
                self.os_aliases,
                self.io_aliases,
                self.os_open_names,
                self.os_fdopen_names,
                self.builtin_open_names,
                self.callable_returns,
                self.dynamic_module_aliases,
                self.os_attribute_aliases,
                self.dynamic_attribute_aliases,
            )
            self.bindings = dict(self.bindings)
            self.mutation_bindings = dict(self.mutation_bindings)
            self.os_aliases = set(self.os_aliases)
            self.io_aliases = set(self.io_aliases)
            self.os_open_names = set(self.os_open_names)
            self.os_fdopen_names = set(self.os_fdopen_names)
            self.builtin_open_names = set(self.builtin_open_names)
            self.callable_returns = dict(self.callable_returns)
            self.dynamic_module_aliases = set(self.dynamic_module_aliases)
            self.os_attribute_aliases = set(self.os_attribute_aliases)
            self.dynamic_attribute_aliases = set(self.dynamic_attribute_aliases)
            self._bind_arguments(node.args)
            self._report_stored_value(node.body)
            self.visit(node.body)
            (
                self.bindings,
                self.mutation_bindings,
                self.os_aliases,
                self.io_aliases,
                self.os_open_names,
                self.os_fdopen_names,
                self.builtin_open_names,
                self.callable_returns,
                self.dynamic_module_aliases,
                self.os_attribute_aliases,
                self.dynamic_attribute_aliases,
            ) = saved

        def visit_Return(self, node):
            self._report_stored_value(node.value)
            binding = self._binding_from_value(node.value)
            if binding is None:
                binding = self._module_storage_binding(node.value)
            if self.return_bindings is not None and binding is not None:
                self.return_bindings.append(binding)
            self.generic_visit(node)

        def visit_Call(self, node):
            function = node.func
            binding = self._binding_from_value(function)
            if binding is None and isinstance(function, ast.Subscript):
                binding = self._subscript_binding(function, include_dynamic=True)
            bound_name = isinstance(function, ast.Name) and self.mutation_bindings.get(function.id) == binding
            if isinstance(function, ast.NamedExpr):
                binding = self._binding_from_value(function.value)
            binding_only = binding not in {
                None,
                "os-open",
                "os-fdopen",
                "builtin-open",
                "open-method",
                "bound-open-method",
            }
            if binding is not None and not (
                binding_only
                and binding != "filesystem-mutation:dynamic"
                and (bound_name or isinstance(function, ast.NamedExpr))
            ):
                self._report_bound_call(node, binding)
            lookup_binding = self._getattr_binding(node) or self._dunder_lookup_binding(node)
            if lookup_binding is not None and lookup_binding not in {
                "filesystem-mutation:dynamic",
                *_MODE_BINDINGS,
            }:
                self._report_bound_call(node, lookup_binding)
            safe_flag_lookup = (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and _literal_string(node.args[1]) in _READ_ONLY_OPEN_FLAGS | _WRITE_OPEN_FLAGS
            )
            if lookup_binding is None and not safe_flag_lookup:
                for argument in node.args:
                    self._report_stored_value(argument)
                for keyword in node.keywords:
                    self._report_stored_value(keyword.value)
            if isinstance(function, ast.Subscript):
                self._report_stored_value(function)
            elif isinstance(function, ast.Call):
                self._report_stored_value(function)
            elif isinstance(function, ast.Attribute) and binding is None and lookup_binding is None:
                if isinstance(function.value, (ast.Call, ast.Subscript, ast.Attribute)):
                    self._report_stored_value(function.value)
            self.generic_visit(node)

    Visitor().visit(tree)
    return sorted(violations)


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


def v2_record(record_key="record-1", ui_state="active", live_state="running", events=None):
    return {
        "record_key": record_key,
        "ui_state": ui_state,
        "live_state": live_state,
        "events": list(events or []),
    }


def v2_snapshot(records, scope="macos:0123456789abcdef0123456789abcdef", active=None, done=None):
    if active is None:
        active = sum(doctor._lifecycle(item)["state"] == "running" for item in records)
    if done is None:
        done = sum(doctor._lifecycle(item)["state"] == "terminal" for item in records)
    return {
        "schema": doctor.SCHEMA_V2,
        "scope": scope,
        "ui_counts": {"active": active, "done": done},
        "records": records,
    }


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

    def test_per_record_ui_lifecycle_mismatch_cannot_cancel_in_aggregate(self):
        running_done = v2_record(
            "running-record",
            ui_state="done",
            live_state="running",
            events=[event(1, "running")],
        )
        terminal_active = v2_record(
            "terminal-record",
            ui_state="active",
            live_state="done",
            events=[event(1, "task_complete")],
        )
        report = doctor.analyze_snapshot(v2_snapshot([running_done, terminal_active], active=1, done=1))
        self.assertEqual(report["derived_counts"], {"active": 1, "done": 1})
        self.assertEqual(report["ui_row_counts"], {"active": 1, "done": 1})
        self.assertTrue(report["live_counts_match"])
        self.assertTrue(report["ui_rows_match"])
        self.assertFalse(report["record_correspondence_match"])
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["exit_code"], 1)

        before_target = v2_record(
            "target-record",
            events=[event(1, "running")],
        )
        before_other = v2_record(
            "other-record",
            ui_state="done",
            live_state="done",
            events=[event(1, "task_complete")],
        )
        after_target = v2_record(
            "target-record",
            ui_state="done",
            live_state="done",
            events=[event(1, "running"), event(2, "task_complete")],
        )
        after_other = v2_record(
            "other-record",
            ui_state="active",
            live_state="done",
            events=[event(1, "task_complete")],
        )
        result = postflight.reconcile(
            v2_snapshot([before_target, before_other], active=1, done=1),
            v2_snapshot([after_target, after_other], active=1, done=1),
            record_keys=["target-record"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("after-doctor-mismatch", {item["code"] for item in result["issues"]})

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

    def test_v2_unknown_event_kind_is_rejected_while_v1_stays_compatible(self):
        with self.assertRaises(doctor.SnapshotError) as caught:
            doctor.validate_snapshot(v2_snapshot([v2_record(events=[event(1, "made_up")])]))
        self.assertEqual(caught.exception.code, "unsupported-event")
        normalized = doctor.validate_snapshot(snapshot([agent(events=[event(1, "made_up")])], active=0, done=0))
        self.assertEqual(normalized["agents"][0]["events"][0]["kind"], "made_up")

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
            live_state="done",
        )
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["records"][0]["classification"], "confirmed-terminal")

    def test_declared_live_state_mismatch_cannot_cancel_in_aggregate(self):
        running_declared_terminal = v2_record(
            "running-record",
            ui_state="active",
            live_state="done",
            events=[event(1, "running")],
        )
        terminal_declared_running = v2_record(
            "terminal-record",
            ui_state="done",
            live_state="running",
            events=[event(1, "task_complete", "source-a"), event(2, "turn_completed", "source-b")],
        )
        report = doctor.analyze_snapshot(
            v2_snapshot([running_declared_terminal, terminal_declared_running], active=1, done=1)
        )
        self.assertTrue(report["live_counts_match"])
        self.assertTrue(report["ui_rows_match"])
        self.assertFalse(report["record_correspondence_match"])
        self.assertFalse(report["records"][0]["lifecycle_live_match"])
        self.assertFalse(report["records"][1]["lifecycle_live_match"])
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["exit_code"], 1)

    def test_default_output_redacts_identifiers(self):
        item = agent("secret-id", "private-name", events=[event(1, "running")])
        public = doctor.public_report(doctor.analyze_snapshot(snapshot([item])), False)
        encoded = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("secret-id", encoded)
        self.assertNotIn("private-name", encoded)
        self.assertIn("agent-1", encoded)
        shown = doctor.public_report(doctor.analyze_snapshot(snapshot([item])), True)
        self.assertNotIn("secret-id", json.dumps(shown))

    def test_public_reports_use_uniform_trust_and_claim_labels(self):
        item = agent(events=[event(1, "running")])
        public = doctor.public_report(doctor.analyze_snapshot(snapshot([item])))
        encoded = json.dumps(public)
        self.assertEqual(public["trust"], doctor.TRUST_LABEL)
        self.assertIn("snapshot-running-claim", encoded)
        self.assertNotIn("confirmed-running", encoded)
        result = postflight.reconcile(
            snapshot([item], active=1, done=0),
            snapshot([agent(events=[event(1, "running"), event(2, "task_complete")], ui_state="done", live_state="done")], active=0, done=1),
            targets=[item["id"]],
        )
        self.assertEqual(result["trust"], doctor.TRUST_LABEL)

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
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(raw)
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot(str(path))
            self.assertEqual(caught.exception.code, "duplicate-key")
        finally:
            path.unlink()

        nested = (
            '{"schema":"codex-subagent-snapshot/v1","ui_counts":{"active":0,"done":0,"active":0},'
            '"agents":[]}'
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(nested)
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot(str(path))
            self.assertEqual(caught.exception.code, "duplicate-key")
        finally:
            path.unlink()

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
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["exit_code"], 2)
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

    def test_postflight_rejects_lifecycle_sequence_rollback(self):
        before_item = agent(events=[event(10, "running")])
        after_item = agent(
            events=[event(5, "task_complete")],
            ui_state="done",
            live_state="done",
        )
        result = postflight.reconcile(
            snapshot([before_item], active=1, done=0),
            snapshot([after_item], active=0, done=1),
            targets=[before_item["id"]],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("target-sequence-rollback", {item["code"] for item in result["issues"]})

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
        self.assertEqual(result["status"], "fail")
        self.assertIn("target-no-transition", {item["code"] for item in result["issues"]})

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
        self.assertEqual(result["status"], "fail")
        self.assertIn("target-no-transition", {item["code"] for item in result["issues"]})

    def test_postflight_legitimate_resume_then_running_in_multi_record_pair_passes(self):
        resume_before = agent("resume-agent", events=[event(1, "task_complete")], ui_state="done", live_state="done")
        target_before = agent("target-agent", events=[event(1, "running")], ui_state="active", live_state="running")
        resume_after = agent(
            "resume-agent",
            events=[event(1, "task_complete"), event(2, "resumed"), event(3, "running")],
            ui_state="active",
            live_state="running",
        )
        target_after = agent(
            "target-agent",
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
        )
        result = postflight.reconcile(
            snapshot([resume_before, target_before], active=1, done=1),
            snapshot([resume_after, target_after], active=1, done=1),
            targets=["target-agent"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertNotIn("terminal-to-running-without-newer-start", {item["code"] for item in result["issues"]})

    def test_postflight_selected_stale_ui_candidate_cannot_pass_forged_aggregate(self):
        before_item = agent(events=[event(1, "running")], ui_state="active", live_state="running")
        after_item = agent(
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="active",
            live_state="done",
        )
        after = snapshot([after_item], active=0, done=1)
        report = doctor.analyze_snapshot(after)
        self.assertFalse(report["ui_rows_match"])
        result = postflight.reconcile(
            snapshot([before_item], active=1, done=0),
            after,
            targets=[before_item["id"]],
        )
        codes = {item["code"] for item in result["issues"]}
        self.assertEqual(result["status"], "fail")
        self.assertIn("after-doctor-mismatch", codes)
        self.assertIn("target-ui-not-done", codes)

    def test_postflight_replayed_higher_terminal_sequence_is_not_transition(self):
        before_item = agent(events=[event(1, "task_complete")], ui_state="done", live_state="done")
        after_item = agent(events=[event(1, "task_complete"), event(2, "task_complete")], ui_state="done", live_state="done")
        result = postflight.reconcile(
            snapshot([before_item], active=0, done=1),
            snapshot([after_item], active=0, done=1),
            targets=[before_item["id"]],
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("target-no-transition", {item["code"] for item in result["issues"]})

    def test_postflight_requires_target_directly(self):
        item = agent(events=[event(1, "running")])
        result = postflight.reconcile(snapshot([item], active=1, done=0), snapshot([item], active=1, done=0), targets=[])
        self.assertEqual(result, {
            "schema": "codex-subagent-postflight/v1",
            "status": "inconclusive",
            "exit_code": 2,
            "issues": [{"code": "target-required"}],
            "trust": doctor.TRUST_LABEL,
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

    def test_doctor_rejects_stdin_without_blocking(self):
        payload = json.dumps(snapshot([agent(events=[event(1, "running")])], active=1, done=0))
        result = subprocess.run(
            [sys.executable, str(DOCTOR_PATH), "--input", "-", "--json"],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            env={key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid-file", result.stdout)

    def test_json_integer_token_is_bounded_before_int_conversion(self):
        oversized = "9" * (doctor.MAX_INPUT_BYTES - 256)
        raw = (
            '{"schema":"codex-subagent-snapshot/v1",'
            f'"ui_counts":{{"active":{oversized},"done":0}},"agents":[]}}'
        )
        self.assertLessEqual(len(raw.encode("utf-8")), doctor.MAX_INPUT_BYTES)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(raw)
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot(str(path))
            self.assertEqual(caught.exception.code, "invalid-json")
        finally:
            path.unlink()

    def test_default_postflight_invocation_does_not_create_bytecode(self):
        before_item = agent(events=[event(1, "running")])
        after_item = agent(events=[event(1, "running"), event(2, "task_complete")], ui_state="done", live_state="done")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            scripts = root / "scripts"
            shutil.copytree(SCRIPTS, scripts)
            before_path = root / "before.json"
            after_path = root / "after.json"
            before_path.write_text(json.dumps(snapshot([before_item], active=1, done=0)), encoding="utf-8")
            after_path.write_text(json.dumps(snapshot([after_item], active=0, done=1)), encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
            result = subprocess.run(
                [sys.executable, str(scripts / "postflight.py"), "--before", str(before_path), "--after", str(after_path), "--target", before_item["id"]],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(list(root.rglob("__pycache__")), [])

    def test_copied_skill_bundle_runs_from_unrelated_cwd(self):
        before_item = agent(events=[event(1, "running")])
        after_item = agent(
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            skill_dir = root / "installed" / "reconcile-codex-subagents"
            shutil.copytree(ROOT, skill_dir)
            unrelated_cwd = root / "unrelated-cwd"
            unrelated_cwd.mkdir()
            before_path = root / "before.json"
            after_path = root / "after.json"
            before_path.write_text(json.dumps(snapshot([before_item], active=1, done=0)), encoding="utf-8")
            after_path.write_text(json.dumps(snapshot([after_item], active=0, done=1)), encoding="utf-8")
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            doctor_result = subprocess.run(
                [sys.executable, str(skill_dir / "scripts" / "doctor.py"), "--input", str(before_path), "--json"],
                cwd=unrelated_cwd,
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(doctor_result.returncode, 0, doctor_result.stdout + doctor_result.stderr)
            self.assertIn(doctor.TRUST_LABEL, doctor_result.stdout)
            postflight_result = subprocess.run(
                [
                    sys.executable,
                    str(skill_dir / "scripts" / "postflight.py"),
                    "--before",
                    str(before_path),
                    "--after",
                    str(after_path),
                    "--target",
                    before_item["id"],
                    "--json",
                ],
                cwd=unrelated_cwd,
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(postflight_result.returncode, 0, postflight_result.stdout + postflight_result.stderr)
            self.assertIn(doctor.TRUST_LABEL, postflight_result.stdout)
            self.assertEqual(list(root.rglob("__pycache__")), [])

    def test_isolated_forward_execution_ignores_shadow_and_sitecustomize(self):
        before_item = agent(events=[event(1, "running")])
        after_item = agent(
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
        )
        trusted_python = str(pathlib.Path(sys.executable).resolve())
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            unrelated_cwd = root / "unrelated-cwd"
            unrelated_cwd.mkdir()
            poison = root / "poison"
            poison.mkdir()
            canary = "SHADOW_DOCTOR_CANARY"
            (poison / "sitecustomize.py").write_text(f"raise SystemExit({canary!r})\n", encoding="utf-8")
            (poison / "doctor.py").write_text(f"raise SystemExit({canary!r})\n", encoding="utf-8")
            before_path = root / "before.json"
            after_path = root / "after.json"
            before_path.write_text(json.dumps(snapshot([before_item], active=1, done=0)), encoding="utf-8")
            after_path.write_text(json.dumps(snapshot([after_item], active=0, done=1)), encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
            env.update({"PYTHONPATH": str(poison), "PYTHONDONTWRITEBYTECODE": "0"})
            commands = [
                [trusted_python, "-I", "-B", str(DOCTOR_PATH), "--help"],
                [trusted_python, "-I", "-B", str(DOCTOR_PATH), "--input", str(before_path), "--json"],
                [trusted_python, "-I", "-B", str(POSTFLIGHT_PATH), "--help"],
                [
                    trusted_python,
                    "-I",
                    "-B",
                    str(POSTFLIGHT_PATH),
                    "--before",
                    str(before_path),
                    "--after",
                    str(after_path),
                    "--target",
                    before_item["id"],
                    "--json",
                ],
            ]
            for command in commands:
                result = subprocess.run(
                    command,
                    cwd=unrelated_cwd,
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                    timeout=2,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn(canary, result.stdout + result.stderr)
            self.assertEqual(list(root.rglob("__pycache__")), [])

    def test_sibling_loader_executes_bound_bytes_after_path_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            sibling_path = scripts / "doctor.py"
            postflight_path = scripts / "postflight.py"
            sibling_path.write_bytes(DOCTOR_PATH.read_bytes())
            postflight_path.write_bytes(POSTFLIGHT_PATH.read_bytes())
            replacement_path = root / "replacement.py"
            replacement_path.write_text("raise RuntimeError('replacement-canary')\n", encoding="utf-8")
            original_compile = builtins.compile
            replaced = False

            def compile_after_replacement(source, filename, mode, *args, **kwargs):
                nonlocal replaced
                if not replaced:
                    replaced = True
                    replacement_path.replace(sibling_path)
                return original_compile(source, filename, mode, *args, **kwargs)

            with mock.patch.object(postflight, "__file__", str(postflight_path)):
                with mock.patch("builtins.compile", side_effect=compile_after_replacement):
                    loaded = postflight._load_sibling_doctor()
            self.assertTrue(replaced)
            self.assertEqual(loaded.SCHEMA, doctor.SCHEMA)
            self.assertIn("replacement-canary", sibling_path.read_text(encoding="utf-8"))

    def test_sibling_loader_rejects_oversized_nonregular_symlink_and_invalid_source(self):
        for shape in ("oversized", "nonregular", "symlink", "invalid-source"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                scripts = root / "scripts"
                scripts.mkdir()
                sibling_path = scripts / "doctor.py"
                postflight_path = scripts / "postflight.py"
                postflight_path.write_bytes(POSTFLIGHT_PATH.read_bytes())
                if shape == "oversized":
                    sibling_path.write_bytes(b"#" * (postflight.MAX_DOCTOR_SOURCE_BYTES + 1))
                elif shape == "nonregular":
                    sibling_path.mkdir()
                elif shape == "symlink":
                    target = root / "shadow-doctor.py"
                    target.write_bytes(DOCTOR_PATH.read_bytes())
                    sibling_path.symlink_to(target)
                else:
                    sibling_path.write_bytes(b"not valid python !!!\n")
                with mock.patch.object(postflight, "__file__", str(postflight_path)):
                    with self.assertRaises(ImportError) as caught:
                        postflight._load_sibling_doctor()
                self.assertEqual(str(caught.exception), "doctor module unavailable")

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

    def test_fstat_rejects_deterministic_path_swap(self):
        payload = json.dumps(snapshot([agent(events=[event(1, "running")])], active=1, done=0))
        replacement = json.dumps(snapshot([agent(events=[event(1, "task_complete")], ui_state="done", live_state="done")], active=0, done=1))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "snapshot.json"
            other = pathlib.Path(directory) / "replacement.json"
            original = pathlib.Path(directory) / "original.json"
            path.write_text(payload, encoding="utf-8")
            other.write_text(replacement, encoding="utf-8")
            original_bytes = path.read_bytes()
            replacement_bytes = other.read_bytes()
            real_fdopen = doctor.os.fdopen

            def swap_path(fd, *args, **kwargs):
                handle = real_fdopen(fd, *args, **kwargs)
                path.replace(original)
                other.replace(path)
                return handle

            with mock.patch.object(doctor.os, "fdopen", side_effect=swap_path):
                with self.assertRaises(doctor.SnapshotError) as caught:
                    doctor.read_snapshot(str(path))
            self.assertEqual(caught.exception.code, "invalid-file")
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual(path.read_bytes(), replacement_bytes)

    def test_fstat_rejects_same_inode_mutation_after_read(self):
        payload = json.dumps(
            snapshot([agent(events=[event(1, "running", source="source-a")])], active=1, done=0)
        ).encode("utf-8")
        mutated = payload.replace(b'"source-a"', b'"source-b"')
        self.assertEqual(len(mutated), len(payload))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "snapshot.json"
            path.write_bytes(payload)
            real_fdopen = doctor.os.fdopen

            def mutate_after_read(fd, *args, **kwargs):
                handle = real_fdopen(fd, *args, **kwargs)
                real_read = handle.read

                def read_and_mutate(*read_args, **read_kwargs):
                    raw = real_read(*read_args, **read_kwargs)
                    path.write_bytes(mutated)
                    return raw

                handle.read = read_and_mutate
                return handle

            with mock.patch.object(doctor.os, "fdopen", side_effect=mutate_after_read):
                with self.assertRaises(doctor.SnapshotError) as caught:
                    doctor.read_snapshot(str(path))
            self.assertEqual(caught.exception.code, "invalid-file")
            self.assertEqual(path.read_bytes(), mutated)

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
        for forbidden in ("urllib", "requests", "socket", "sqlite", "subprocess", "os.remove", "os.unlink"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIn("os.fstat", source)
        self.assertIn("os.open", source)
        self.assertNotIn("read_stdin", source)

    def test_scripts_ast_policy_has_no_network_or_write_calls(self):
        forbidden_imports = {"socket", "sqlite3", "urllib", "requests", "subprocess"}
        for script in (DOCTOR_PATH, POSTFLIGHT_PATH):
            source = script.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue({alias.name.split(".")[0] for alias in node.names}.isdisjoint(forbidden_imports))
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0], forbidden_imports)
            self.assertEqual(_filesystem_mutations(source), [], script.name)

    def test_read_only_ast_gate_rejects_injected_filesystem_mutations(self):
        forbidden = {
            "os-open-wronly": ("import os\nos.open('snapshot.json', os.O_WRONLY)\n", "os-open-write-flags"),
            "os-open-create": ("import os\nos.open('snapshot.json', os.O_RDONLY | os.O_CREAT)\n", "os-open-write-flags"),
            "os-open-aliased-write": (
                "import os as fs\nflags = fs.O_RDONLY\nflags |= fs.O_TRUNC\nfs.open('snapshot.json', flags)\n",
                "os-open-write-flags",
            ),
            "builtin-open-write": ("open('snapshot.json', 'w', encoding='utf-8')\n", "open-write-mode"),
            "builtin-open-append": ("open('snapshot.json', mode='a+')\n", "open-write-mode"),
            "os-fdopen-write": ("import os\nos.fdopen(3, 'w')\n", "open-write-mode"),
            "path-open-write": ("from pathlib import Path\nPath('snapshot.json').open('w')\n", "open-write-mode"),
            "path-write-text": ("from pathlib import Path\nPath('snapshot.json').write_text('x')\n", "filesystem-mutation:write_text"),
            "path-write-bytes": ("from pathlib import Path\nPath('snapshot.json').write_bytes(b'x')\n", "filesystem-mutation:write_bytes"),
            "path-touch": ("from pathlib import Path\nPath('snapshot.json').touch()\n", "filesystem-mutation:touch"),
            "path-unlink": ("from pathlib import Path\nPath('snapshot.json').unlink()\n", "filesystem-mutation:unlink"),
            "path-rename": ("from pathlib import Path\nPath('snapshot.json').rename('other.json')\n", "filesystem-mutation:rename"),
            "path-replace": ("from pathlib import Path\nPath('snapshot.json').replace('other.json')\n", "filesystem-mutation:replace"),
            "os-remove": ("import os\nos.remove('snapshot.json')\n", "filesystem-mutation:remove"),
            "os-remove-alias": (
                "import os\nmutate = os.remove\nmutate('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-dynamic-getattr": (
                "import os\ngetattr(os, 'remove')('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-unresolved-getattr-alias": (
                "import os\noperation = get_operation()\nmutate = getattr(os, operation)\nmutate('snapshot.json')\n",
                "filesystem-mutation:dynamic",
            ),
            "os-remove-imported-mutator-name-alias": (
                "from os import remove as delete\nmutate = delete\nmutate('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-module-name-alias": (
                "import os\nfilesystem = os\ngetattr(filesystem, 'remove')('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-module-alias-chain": (
                "import os\nfilesystem = os\nfilesystem_alias = filesystem\n"
                "getattr(filesystem_alias, 'remove')('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-function-default": (
                "import os\ndef f(x=os.remove('snapshot.json')):\n    pass\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-function-decorator": (
                "import os\n@os.remove('snapshot.json')\ndef f():\n    pass\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-nested-closure": (
                "import os\nmutate = os.remove\n"
                "def outer():\n    def inner():\n        mutate('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-local-nested-closure": (
                "import os\ndef outer():\n    mutate = os.remove\n"
                "    def inner():\n        mutate('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-positional-annotation": (
                "import os\ndef f(value: os.remove('snapshot.json')):\n    pass\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-keyword-only-annotation": (
                "import os\ndef f(*, value: os.remove('snapshot.json')):\n    pass\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-vararg-annotation": (
                "import os\ndef f(*values: os.remove('snapshot.json')):\n    pass\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-kwarg-annotation": (
                "import os\ndef f(**values: os.remove('snapshot.json')):\n    pass\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-return-annotation": (
                "import os\ndef f() -> os.remove('snapshot.json'):\n    pass\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-returned-call-result": (
                "import os\ndef f():\n    return os.remove\n"
                "f()('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-system-returned-call-result": (
                "import os\ndef f():\n    return os.system\n"
                "f()('echo snapshot')\n",
                "process-execution:system",
            ),
            "os-remove-in-lambda-keyword-only-default": (
                "import os\n(lambda *, x=os.remove('snapshot.json'): None)()\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-simplenamespace-keyword": (
                "import os\nfrom types import SimpleNamespace\n"
                "SimpleNamespace(m=os.remove).m('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-in-dict-callable-storage": (
                "import os\n{'m': os.remove}['m']('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-module-in-simplenamespace-keyword": (
                "import os\nfrom types import SimpleNamespace\n"
                "SimpleNamespace(m=os).m.system('rm -f x')\n",
                "filesystem-mutation:dynamic",
            ),
            "builtin-open-forward-dict-get-alias": (
                "import builtins\ndef f():\n    opener('x', 'w')\n"
                "    opener = builtins.__dict__.get('open')\n"
                "f()\n",
                "filesystem-mutation:dynamic",
            ),
            "builtin-open-forward-vars-get-alias": (
                "import builtins\ndef f():\n    opener('x', 'w')\n"
                "    opener = vars(builtins).get('open')\n"
                "f()\n",
                "filesystem-mutation:dynamic",
            ),
            "path-getattr-write-text": (
                "from pathlib import Path\ngetattr(Path('snapshot.json'), 'write_text')('x')\n",
                "filesystem-mutation:write_text",
            ),
            "path-getattr-open-write": (
                "from pathlib import Path\ngetattr(Path('snapshot.json'), 'open')('w')\n",
                "open-write-mode",
            ),
            "path-named-open-write": (
                "from pathlib import Path\np = Path('snapshot.json')\np.open('w')\n",
                "open-write-mode",
            ),
            "shutil-imported-copy": (
                "from shutil import copy\ncopy('before.json', 'after.json')\n",
                "filesystem-mutation:copy",
            ),
            "tempfile-imported-mkstemp": (
                "from tempfile import mkstemp\nmkstemp()\n",
                "filesystem-mutation:mkstemp",
            ),
            "os-imported-fdopen-write": (
                "from os import fdopen\nfdopen(3, 'w')\n",
                "open-write-mode",
            ),
            "io-imported-open-write": (
                "from io import open\nopen('snapshot.json', 'w')\n",
                "open-write-mode",
            ),
            "os-imported-fdopen-alias-rejected-at-binding": (
                "from os import fdopen\nfdopen(3, 'rb')\n",
                "open-callable-alias",
            ),
            "io-imported-open-alias-rejected-at-binding": (
                "from io import open\nopen('snapshot.json', 'rb')\n",
                "open-callable-alias",
            ),
            "os-remove-walrus": (
                "import os\n(mutate := os.remove)('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-tuple-alias": (
                "import os\n(mutate,) = (os.remove,)\nmutate('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-module-forward-alias": (
                "import os\ndef earlier():\n    mutate('snapshot.json')\nmutate = os.remove\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-closure-forward-alias": (
                "import os\ndef outer():\n    def inner():\n        mutate('snapshot.json')\n    mutate = os.remove\n",
                "filesystem-mutation:remove",
            ),
            "builtin-open-forward-alias": (
                "def earlier():\n    opener('snapshot.json', 'w')\n"
                "opener = open\n",
                "builtin-open",
            ),
            "path-open-forward-alias": (
                "from pathlib import Path\ndef earlier():\n    opener('snapshot.json', 'w')\n"
                "opener = Path('snapshot.json').open\n",
                "bound-open-method",
            ),
            "path-open-closure-forward-alias": (
                "from pathlib import Path\ndef outer():\n    def inner():\n        opener('w')\n"
                "    opener = Path('snapshot.json').open\n",
                "bound-open-method",
            ),
            "os-open-forward-alias": (
                "import os\ndef earlier():\n    opener('snapshot.json', os.O_WRONLY)\n"
                "opener = os.open\n",
                "os-open",
            ),
            "os-remove-dynamic-subscript": (
                "import os\nvars(os)['remove']('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-remove-dunder-subscript": (
                "import os\ngetattr(os, '__dict__')['remove']('snapshot.json')\n",
                "filesystem-mutation:remove",
            ),
            "os-system": (
                "import os\nos.system('rm snapshot.json')\n",
                "process-execution:system",
            ),
            "os-popen": (
                "import os\nos.popen('rm snapshot.json')\n",
                "process-execution:popen",
            ),
            "os-spawn-alias": (
                "from os import spawnv\nspawnv(0, '/bin/rm', ['rm', 'snapshot.json'])\n",
                "process-execution:spawnv",
            ),
            "os-exec-alias": (
                "from os import execv\nexecv('/bin/rm', ['rm', 'snapshot.json'])\n",
                "process-execution:execv",
            ),
            "os-posix-spawn": (
                "import os\nos.posix_spawn('/bin/rm', ['rm', 'snapshot.json'], {})\n",
                "process-execution:posix_spawn",
            ),
            "os-system-getattr": (
                "import os\ngetattr(os, 'system')('rm snapshot.json')\n",
                "process-execution:system",
            ),
            "file-write": ("handle.write('mutation')\n", "filesystem-mutation:write"),
            "shutil-copy": ("import shutil\nshutil.copy('before.json', 'after.json')\n", "filesystem-mutation:copy"),
        }
        for name, (source, expected) in forbidden.items():
            with self.subTest(name=name):
                violations = _filesystem_mutations(source)
                self.assertIn(expected, {item[2] for item in violations})

        for method in sorted(_FILESYSTEM_MUTATION_METHODS):
            with self.subTest(path_getattr_method=method):
                mode = "'w'" if method == "open" else "'x'"
                source = (
                    "from pathlib import Path\n"
                    f"getattr(Path('snapshot.json'), {method!r})({mode})\n"
                )
                expected = "open-write-mode" if method == "open" else "filesystem-mutation:" + method
                self.assertIn(expected, {item[2] for item in _filesystem_mutations(source)})

        binding_only = {
            "from shutil import copy\n": "filesystem-mutation:copy",
            "from tempfile import mkstemp\n": "filesystem-mutation:mkstemp",
            "import os\nmutate = os.remove\n": "filesystem-mutation:remove",
            "import os\n(mutate,) = (os.remove,)\n": "filesystem-mutation:remove",
        }
        for source, expected in binding_only.items():
            with self.subTest(binding_only=source):
                self.assertIn(expected, {item[2] for item in _filesystem_mutations(source)})

        allowed = (
            "import os\nos.open('snapshot.json', os.O_RDONLY)\n",
            "import os\nos.open('snapshot.json', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)\n",
            "import os\nos.open('snapshot.json', os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))\n",
            "import os\nflags = os.O_RDONLY\nflags |= getattr(os, 'O_NOFOLLOW', 0)\nos.open('snapshot.json', flags)\n",
            "open('snapshot.json')\n",
            "open('snapshot.json', 'rb')\n",
            "import os\nos.fdopen(3, 'rb')\n",
            "import io\nio.open('snapshot.json', 'rb')\n",
            "from pathlib import Path\nPath('snapshot.json').open(mode='rb')\n",
        )
        for source in allowed:
            with self.subTest(allowed=source):
                self.assertEqual(_filesystem_mutations(source), [])

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

    def test_doctor_non_darwin_stops_before_reading_input(self):
        output = io.StringIO()
        with mock.patch.object(doctor.platform, "system", return_value="Linux"), mock.patch.object(
            doctor, "read_snapshot", side_effect=AssertionError("input opened")
        ) as read_snapshot, contextlib.redirect_stdout(output):
            exit_code = doctor.main(["--input", "/does/not/exist", "--json"])
        self.assertEqual(exit_code, 2)
        read_snapshot.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["error"], "unsupported-platform")
        self.assertEqual(report["trust"], doctor.TRUST_LABEL)

    def test_postflight_non_darwin_stops_before_reading_inputs(self):
        output = io.StringIO()
        with mock.patch.object(postflight.platform, "system", return_value="Linux"), mock.patch.object(
            postflight, "read_snapshot", side_effect=AssertionError("input opened")
        ) as read_snapshot, contextlib.redirect_stdout(output):
            exit_code = postflight.main(
                [
                    "--before",
                    "/does/not/exist-before",
                    "--after",
                    "/does/not/exist-after",
                    "--target",
                    "synthetic-agent-1",
                    "--json",
                ]
            )
        self.assertEqual(exit_code, 2)
        read_snapshot.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report, {
            "schema": "codex-subagent-postflight/v1",
            "status": "inconclusive",
            "exit_code": 2,
            "issues": [{"code": "unsupported-platform"}],
            "trust": doctor.TRUST_LABEL,
        })

    def test_postflight_non_darwin_subprocess_gate_precedes_loader_and_inputs(self):
        def run_entry(arguments):
            encoded_argv = json.dumps([str(POSTFLIGHT_PATH), *arguments])
            encoded_path = json.dumps(str(POSTFLIGHT_PATH))
            source = (
                "import os, platform, runpy, sys\n"
                "platform.system = lambda: 'Linux'\n"
                "def denied_open(*args, **kwargs):\n"
                "    raise OSError('loader capability unavailable')\n"
                "os.open = denied_open\n"
                f"sys.argv = {encoded_argv}\n"
                f"runpy.run_path({encoded_path}, run_name='__main__')\n"
            )
            return subprocess.run(
                [sys.executable, "-I", "-B", "-c", source],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
                env={"PYTHONDONTWRITEBYTECODE": "1"},
            )

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            unreadable = root / "DO_NOT_OPEN_unreadable.json"
            unreadable.write_text("not-json", encoding="utf-8")
            unreadable.chmod(0)
            missing = root / "DO_NOT_OPEN_missing.json"
            common = ["--before", str(unreadable), "--after", str(missing), "--target", "synthetic-agent-1"]
            cases = {
                "help": (["--help"], 0),
                "json": ([*common, "--json"], 2),
                "text": (common, 2),
            }
            try:
                for name, (arguments, expected_code) in cases.items():
                    with self.subTest(name=name):
                        result = run_entry(arguments)
                        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                        self.assertNotIn("loader capability unavailable", result.stdout + result.stderr)
                        if name == "help":
                            self.assertIn("usage:", result.stdout.lower())
                        elif name == "json":
                            report = json.loads(result.stdout)
                            self.assertEqual(report["issues"], [{"code": "unsupported-platform"}])
                        else:
                            self.assertIn("issue: unsupported-platform", result.stdout)
            finally:
                unreadable.chmod(0o600)

    def test_doctor_non_darwin_subprocess_gate_precedes_filesystem_and_inputs(self):
        def run_entry(arguments):
            encoded_argv = json.dumps([str(DOCTOR_PATH), *arguments])
            encoded_path = json.dumps(str(DOCTOR_PATH))
            source = (
                "import os, platform, runpy, sys\n"
                "platform.system = lambda: 'Linux'\n"
                "def denied(*args, **kwargs):\n"
                "    raise OSError('doctor filesystem capability unavailable')\n"
                "os.open = denied\n"
                "os.lstat = denied\n"
                f"sys.argv = {encoded_argv}\n"
                f"runpy.run_path({encoded_path}, run_name='__main__')\n"
            )
            return subprocess.run(
                [sys.executable, "-I", "-B", "-c", source],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
                env={"PYTHONDONTWRITEBYTECODE": "1"},
            )

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            unreadable = root / "DO_NOT_OPEN_unreadable.json"
            unreadable.write_text("not-json", encoding="utf-8")
            unreadable.chmod(0)
            missing = root / "DO_NOT_OPEN_missing.json"
            cases = {
                "help": (["--help"], 0),
                "json": (["--input", str(unreadable), "--json"], 2),
                "text": (["--input", str(missing)], 2),
            }
            try:
                for name, (arguments, expected_code) in cases.items():
                    with self.subTest(name=name):
                        result = run_entry(arguments)
                        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                        self.assertNotIn("doctor filesystem capability unavailable", result.stdout + result.stderr)
                        if name == "help":
                            self.assertIn("usage:", result.stdout.lower())
                        elif name == "json":
                            report = json.loads(result.stdout)
                            self.assertEqual(report["error"], "unsupported-platform")
                            self.assertEqual(report["exit_code"], 2)
                        else:
                            self.assertIn("error: unsupported-platform", result.stdout)
            finally:
                unreadable.chmod(0o600)

    def test_skill_metadata_and_openai_config(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(
            skill.split("\n\n", 1)[0],
            "\n".join(
                [
                    "---",
                    "name: reconcile-codex-subagents",
                    "description: >-",
                    "  Explicitly invoked, macOS-only, strictly diagnostic and read-only checks for",
                    "  sanitized Codex subagent snapshots, including ghost/stale rows, count",
                    "  contradictions, and compact/resume lifecycle evidence.",
                    "---",
                ]
            ),
        )
        config = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            config.splitlines(),
            [
                "interface:",
                '  display_name: "Codex Subagent Reconciler"',
                '  short_description: "Read-only macOS checks for Codex subagent snapshots"',
                '  default_prompt: "Use $reconcile-codex-subagents for read-only macOS checks on sanitized snapshots; fail closed on unsafe or ambiguous input."',
                "policy:",
                "  allow_implicit_invocation: false",
            ],
        )
        short_description = config.splitlines()[2].split('"', 2)[1]
        self.assertGreaterEqual(len(short_description), 25)
        self.assertLessEqual(len(short_description), 64)

    def test_guardian_plugin_manifest_and_explicit_policies(self):
        repo_root = ROOT.parent.parent
        manifest = json.loads((repo_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "codex-workflow-guardian")
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("apps", manifest)
        self.assertNotIn("mcpServers", manifest)
        self.assertLessEqual(len(manifest["interface"]["defaultPrompt"]), 3)
        guardian = (ROOT.parent / "codex-workflow-guardian" / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: false", guardian)
        self.assertNotIn("read-only", guardian)

        marketplace = json.loads(
            (repo_root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(marketplace["name"], "onebigmoon-codex-workflows")
        self.assertEqual(marketplace["interface"]["displayName"], "OneBigMoon Codex Workflows")
        self.assertEqual(len(marketplace["plugins"]), 3)
        entries = {item["name"]: item for item in marketplace["plugins"]}
        self.assertEqual(set(entries), {manifest["name"], "allinluna", "ponytail"})
        self.assertEqual(entries[manifest["name"]]["source"], {"source": "local", "path": "./"})
        self.assertEqual(entries["allinluna"]["source"], {
            "source": "git-subdir",
            "url": "https://github.com/zenx0x/allinluna.git",
            "sha": "723088a7c0d7342f077ad675c6ea72d7e3996536",
            "path": "./plugins/allinluna",
        })
        self.assertEqual(entries["ponytail"]["source"], {
            "source": "url",
            "url": "https://github.com/DietrichGebert/ponytail.git",
            "sha": "2ed6c52c9d7e5e56942508591085fd45dea277d3",
        })
        for entry in entries.values():
            self.assertEqual(entry["policy"], {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
        self.assertEqual(entries[manifest["name"]]["category"], "Developer Tools")

        guardian_selector = "codex-workflow-guardian@onebigmoon-codex-workflows"
        marketplace_selector = "onebigmoon-codex-workflows"
        readmes = [repo_root / name for name in ("README.md", "README.zh-CN.md", "README.ja.md", "README.es.md")]
        refs = []
        for readme in readmes:
            text = readme.read_text(encoding="utf-8")
            pin_matches = re.findall(
                r'(?m)^GUARDIAN_REF="(AUDITED_COMMIT_SHA|[0-9a-fA-F]{40})"(?:[ \t]+#.*)?$',
                text,
            )
            self.assertTrue(pin_matches, readme.name)
            self.assertEqual(len(set(pin_matches)), 1, readme.name)
            refs.append(pin_matches[0])
            shell_text = re.sub(r"\\[ \t]*\n[ \t]*", " ", text)
            self.assertRegex(
                shell_text,
                r'plugin\s+marketplace\s+add\s+OneBigMoon/codex-subagent-reconciler'
                r'\s+--ref\s+"\$GUARDIAN_REF"\s+--json',
                readme.name,
            )
            self.assertRegex(shell_text, rf"plugin\s+add\s+{re.escape(guardian_selector)}\s+--json", readme.name)
            self.assertRegex(shell_text, rf"plugin\s+remove\s+{re.escape(guardian_selector)}", readme.name)
            self.assertRegex(shell_text, rf"plugin\s+marketplace\s+remove\s+{re.escape(marketplace_selector)}", readme.name)
            self.assertIn("--path skills/codex-workflow-guardian skills/reconcile-codex-subagents", text)
            self.assertIn("RECONCILER_SKILL_DIR", text)
            self.assertNotIn("--ref main", text)
            for field in ("pluginId", "name", "marketplaceName", "version", "installedPath", "authPolicy"):
                self.assertIn(f"`{field}`", text, readme.name)
            self.assertNotIn("installed: true", text.lower(), readme.name)
            self.assertNotIn(".installed", text.lower(), readme.name)
        self.assertEqual(len(set(refs)), 1)
        self.assertTrue(refs[0] == "AUDITED_COMMIT_SHA" or re.fullmatch(r"[0-9a-fA-F]{40}", refs[0]))
        self.assertNotEqual(refs[0], "NEW_AUDITED_COMMIT_SHA")

    def test_epoch_terminal_corroboration_does_not_cross_resume(self):
        item = agent(
            events=[
                event(1, "task_started", "a"),
                event(2, "task_complete", "a"),
                event(3, "turn_completed", "b"),
                event(4, "resumed", "a"),
                event(5, "task_complete", "a"),
            ],
            ui_state="done",
            live_state="running",
        )
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["records"][0]["evidence"]["reason"], "insufficient-terminal-corroboration")

    def test_newer_not_found_is_not_ignored_by_older_lifecycle(self):
        item = agent(
            events=[event(1, "task_started"), event(2, "not_found")],
            ui_state="active",
            live_state="running",
        )
        report = doctor.analyze_snapshot(snapshot([item], active=1, done=0))
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["records"][0]["evidence"]["reason"], "not-found-newer")

    def test_arbitrary_source_labels_alone_do_not_promote_terminal(self):
        item = agent(
            events=[event(1, "task_complete", "arbitrary-a"), event(2, "task_complete", "arbitrary-b")],
            ui_state="done",
            live_state="running",
        )
        report = doctor.analyze_snapshot(snapshot([item], active=0, done=1))
        self.assertEqual(report["exit_code"], 2)

    def test_v2_before_mismatch_is_valid_repair_baseline(self):
        before_item = v2_record(events=[event(1, "running")], ui_state="active", live_state="running")
        after_item = v2_record(
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
        )
        result = postflight.reconcile(
            v2_snapshot([before_item], active=0, done=0),
            v2_snapshot([after_item], active=0, done=1),
            record_keys=[before_item["record_key"]],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["exit_code"], 0)

    def test_v2_unchanged_terminal_requires_real_transition(self):
        item = v2_record(events=[event(1, "task_complete")], ui_state="done", live_state="done")
        result = postflight.reconcile(
            v2_snapshot([item], active=0, done=1),
            v2_snapshot([item], active=0, done=1),
            record_keys=[item["record_key"]],
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("target-no-transition", {entry["code"] for entry in result["issues"]})

    def test_v2_scope_and_record_key_drift_fail_closed(self):
        before_item = v2_record(events=[event(1, "running")])
        after_item = v2_record(events=[event(1, "running"), event(2, "task_complete")])
        scope_drift = postflight.reconcile(
            v2_snapshot([before_item]),
            v2_snapshot([after_item], scope="macos:fedcba9876543210fedcba9876543210", active=0, done=1),
            record_keys=[before_item["record_key"]],
        )
        self.assertEqual(scope_drift["exit_code"], 2)
        self.assertIn("scope-drift", {entry["code"] for entry in scope_drift["issues"]})
        key_drift = postflight.reconcile(
            v2_snapshot([before_item]),
            v2_snapshot([v2_record("other-record", events=[event(1, "task_complete")], ui_state="done", live_state="done")], active=0, done=1),
            record_keys=[before_item["record_key"]],
        )
        self.assertEqual(key_drift["exit_code"], 2)
        self.assertIn("record-key-drift", {entry["code"] for entry in key_drift["issues"]})

    def test_v1_identity_and_target_mapping_drift_fail_closed(self):
        before_item = agent("one", events=[event(1, "running")], exact_target="target-one")
        after_item = agent(
            "two",
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
            exact_target="target-one",
        )
        identity = postflight.reconcile(
            snapshot([before_item], active=1, done=0),
            snapshot([after_item], active=0, done=1),
            targets=["target-one"],
        )
        self.assertEqual(identity["exit_code"], 2)
        self.assertIn("identity-drift", {entry["code"] for entry in identity["issues"]})
        mapping_after = agent(
            "one",
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="done",
            live_state="done",
            exact_target="target-two",
        )
        mapping = postflight.reconcile(
            snapshot([before_item], active=1, done=0),
            snapshot([mapping_after], active=0, done=1),
            targets=["one"],
        )
        self.assertEqual(mapping["exit_code"], 2)
        self.assertIn("target-mapping-drift", {entry["code"] for entry in mapping["issues"]})

    def test_v2_after_stale_ui_count_contradiction_cannot_pass(self):
        before_item = v2_record(events=[event(1, "running")])
        after_item = v2_record(
            events=[event(1, "running"), event(2, "task_complete")],
            ui_state="active",
            live_state="done",
        )
        result = postflight.reconcile(
            v2_snapshot([before_item]),
            v2_snapshot([after_item], active=1, done=0),
            record_keys=[before_item["record_key"]],
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("after-doctor-mismatch", {entry["code"] for entry in result["issues"]})

    def test_strict_unknown_fields_controls_and_duplicate_keys_are_rejected(self):
        raw = '{"schema":"codex-subagent-snapshot/v2","scope":"macos:0123456789abcdef",' \
              '"ui_counts":{"active":0,"done":0,"extra":1},"records":[]}'
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(raw)
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot(str(path))
            self.assertEqual(caught.exception.code, "invalid-ui-counts")
        finally:
            path.unlink()
        payload = v2_snapshot([])
        payload["records"] = [{"record_key": "record-1", "ui_state": "active", "live_state": "running", "events": [], "extra": True}]
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(payload)
        unsafe = v2_snapshot([v2_record(record_key="bad\x1bkey")])
        with self.assertRaises(doctor.SnapshotError) as caught:
            doctor.validate_snapshot(unsafe)
        self.assertIn(caught.exception.code, {"unsafe-text", "unsafe-identifier"})

    def test_strict_unknown_fields_apply_at_root_v1_and_event_levels(self):
        root_extra = v2_snapshot([])
        root_extra["extra"] = True
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(root_extra)
        v1_extra = snapshot([agent(events=[event(1, "running")])])
        v1_extra["agents"][0]["extra"] = True
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(v1_extra)
        event_extra = v2_snapshot([v2_record(events=[{**event(1, "running"), "extra": True}])])
        with self.assertRaises(doctor.SnapshotError):
            doctor.validate_snapshot(event_extra)
        raw = '{"schema": NaN}'
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(raw)
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(doctor.SnapshotError) as caught:
                doctor.read_snapshot(str(path))
            self.assertEqual(caught.exception.code, "invalid-json")
        finally:
            path.unlink()

    def test_v2_cli_uses_record_key_and_rejects_legacy_identifier_flag(self):
        result = subprocess.run(
            [sys.executable, str(DOCTOR_PATH), "--input", "snapshot.json", "--show-identifiers"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("show-identifiers", result.stdout)
        before_item = v2_record(events=[event(1, "running")])
        after_item = v2_record(events=[event(1, "running"), event(2, "task_complete")], ui_state="done", live_state="done")
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            before_path = root / "before.json"
            after_path = root / "after.json"
            before_path.write_text(json.dumps(v2_snapshot([before_item])), encoding="utf-8")
            after_path.write_text(json.dumps(v2_snapshot([after_item], active=0, done=1)), encoding="utf-8")
            accepted = subprocess.run(
                [sys.executable, str(POSTFLIGHT_PATH), "--before", str(before_path), "--after", str(after_path), "--record-key", "record-1", "--json"],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(accepted.returncode, 0)
            rejected = subprocess.run(
                [sys.executable, str(POSTFLIGHT_PATH), "--before", str(before_path), "--after", str(after_path), "--target", "record-1", "--json"],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("record-key-required", rejected.stdout)

    def test_v2_reports_redact_scope_and_record_key(self):
        item = v2_record("private-record", events=[event(1, "running")])
        report = doctor.public_report(doctor.analyze_snapshot(v2_snapshot([item])), True)
        encoded = json.dumps(report)
        self.assertNotIn("private-record", encoded)
        self.assertNotIn("0123456789abcdef", encoded)
        self.assertIn("record-1", encoded)


if __name__ == "__main__":
    unittest.main()
