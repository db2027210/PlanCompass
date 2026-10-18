#!/usr/bin/env python3
"""Positive and negative E2E tests for Unix listener inode authority."""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent


def import_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = import_path(
    "tpch_socket_identity_builder",
    HERE / "build_mysql_tpch_socket_identity_successor_v1.py",
)


class AuthorityError(RuntimeError):
    pass


def listener_helper():
    tree = ast.parse(builder.authority_payload().decode("utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_unix_listener_identity"
    )
    namespace = {
        "Any": object,
        "Path": Path,
        "os": os,
        "AuthorityError": AuthorityError,
    }
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[function], type_ignores=[])
            ),
            "_unix_listener_identity",
            "exec",
        ),
        namespace,
    )
    return namespace["_unix_listener_identity"]


def fixture_parts():
    fixture = builder.load(builder.SOCKET_FIXTURE)
    lines = fixture["stdout"].splitlines()
    unix_lines = [line for line in lines if line.startswith("ffff")]
    fd_targets = {
        key[2:]: value
        for line in lines if line.startswith("fd")
        for key, value in [line.split("=", 1)]
    }
    return fixture, "\n".join(unix_lines) + "\n", fd_targets


def run_helper(unix_text, fd_targets):
    helper = listener_helper()
    entries = [Path("/proc/97636/fd") / name for name in fd_targets]

    def readlink(path):
        return fd_targets[Path(path).name]

    with (
        mock.patch.object(Path, "read_text", return_value=unix_text),
        mock.patch.object(Path, "iterdir", return_value=entries),
        mock.patch.object(os, "readlink", side_effect=readlink),
    ):
        return helper("/run/ra-docker-mysql-tpch-v1.sock", 97636)


class SocketIdentityTests(unittest.TestCase):
    def test_real_fixture_selects_unique_listener_and_mainpid_fd(self):
        builder.verify_inputs()
        fixture, unix_text, fd_targets = fixture_parts()
        value = run_helper(unix_text, fd_targets)
        self.assertEqual(value["path_row_count"], 6)
        self.assertEqual(value["listener_count"], 1)
        self.assertEqual(value["listener_flags"], "00010000")
        self.assertEqual(value["listener_type"], "0001")
        self.assertEqual(value["listener_state"], "01")
        self.assertEqual(
            value["listener_kernel_inode"],
            fixture["listener_kernel_inode"],
        )
        self.assertEqual(value["main_pid_owner_fds"], ["3"])
        self.assertTrue(value["main_pid_holds_listener"])

    def test_no_listener_fails_closed(self):
        _, unix_text, fd_targets = fixture_parts()
        unix_text = unix_text.replace("00010000 0001 01", "00000000 0001 03")
        with self.assertRaisesRegex(
            AuthorityError, "listener cardinality differs"
        ):
            run_helper(unix_text, fd_targets)

    def test_multiple_listeners_fail_closed(self):
        _, unix_text, fd_targets = fixture_parts()
        listener = next(
            line for line in unix_text.splitlines()
            if "00010000 0001 01" in line
        )
        with self.assertRaisesRegex(
            AuthorityError, "listener cardinality differs"
        ):
            run_helper(unix_text + listener + "\n", fd_targets)

    def test_listener_not_held_by_mainpid_fails_closed(self):
        _, unix_text, fd_targets = fixture_parts()
        fd_targets = {
            fd: target for fd, target in fd_targets.items()
            if target != "socket:[571336812]"
        }
        with self.assertRaisesRegex(
            AuthorityError, "does not own listener inode"
        ):
            run_helper(unix_text, fd_targets)

    def test_filesystem_inode_is_never_used_as_kernel_socket_inode(self):
        authority = builder.authority_payload().decode("utf-8")
        collector = builder.collector_payload().decode("utf-8")
        for source in (authority, collector):
            self.assertIn("stat.S_ISSOCK(socket_state.st_mode)", source)
            self.assertIn(
                '"socket_filesystem_inode": socket_state.st_ino', source
            )
            self.assertNotIn(
                '"socket:[%d]" % socket_state.st_ino', source
            )
            self.assertIn(
                '"listener_kernel_inode": listener["inode"]', source
            )

    def test_543_attempt_is_zero_credit_and_no_rerun(self):
        invalid = builder.load(builder.INVALIDATION)
        self.assertFalse(invalid["container_started"])
        self.assertFalse(invalid["database_started"])
        self.assertFalse(invalid["workload_started"])
        self.assertEqual(invalid["accepted_rounds_credit"], 0)
        self.assertFalse(invalid["replay_or_rerun_authorized"])


if __name__ == "__main__":
    unittest.main()
