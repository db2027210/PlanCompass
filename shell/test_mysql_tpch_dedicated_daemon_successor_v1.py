#!/usr/bin/env python3
"""Offline tests for the TPC-H dedicated-daemon successor."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


def import_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = import_path(
    "tpch_daemon_builder",
    HERE / "build_mysql_tpch_dedicated_daemon_successor_v1.py",
)


class DedicatedDaemonTests(unittest.TestCase):
    def test_contract_and_unit_are_exact_and_non_pg(self):
        builder.verify_inputs()
        contract = builder.load(builder.CONTRACT)
        self.assertEqual(contract["daemon_socket"], builder.SOCKET)
        self.assertEqual(contract["daemon_data_root"], builder.DATA_ROOT)
        self.assertEqual(contract["docker_root_device_major_minor"], "259:1")
        self.assertEqual(contract["graph_driver"], "overlay2")
        self.assertFalse(contract["iptables"])
        self.assertTrue(contract["postgresql_mutation_prohibited"])

    def test_all_sealed_runtime_docker_argv_are_explicit_host(self):
        predecessor = builder.verify_predecessor()
        payloads = builder.payloads(predecessor)
        for relative in (
            builder.AUTHORITY,
            builder.COLLECTOR,
            builder.BLKIO,
            builder.FRESH_R0,
        ):
            source = payloads[relative].decode("utf-8")
            tree = ast.parse(source)
            implicit = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.List)
                and node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "docker"
            ]
            self.assertEqual(implicit, [], relative)
            self.assertIn(
                'DOCKER_CLI = ("docker", "--host", DOCKER_SOCKET_URI)',
                source,
                relative,
            )
            self.assertIn("*DOCKER_CLI,", source, relative)

    def test_database_and_client_are_host_network_with_mysqld_65103(self):
        source = builder.authority_payload().decode("utf-8")
        db_start = source.index("db_argv = [")
        client_start = source.index("client_argv = [", db_start)
        db = source[db_start:client_start]
        client = source[client_start:source.index("collect_argv = [", client_start)]
        self.assertIn('"--network", "host"', db)
        self.assertNotIn('"--publish"', db)
        self.assertIn('f"--port={MYSQL_PORT}"', db)
        self.assertIn('"--bind-address=127.0.0.1"', db)
        self.assertIn('"--network", "host"', client)
        self.assertIn('db_host.get("NetworkMode") != "host"', source)
        self.assertIn('port_bindings != {}', source)

    def test_bootstrap_is_direct_host_transient_unit_and_token_not_argv(self):
        authority = builder.authority_payload().decode("utf-8")
        collector = builder.collector_payload().decode("utf-8")
        fresh = builder.fresh_r0_payload().decode("utf-8")
        for fragment in (
            '"mode": "direct_host_systemd_transient_unit"',
            '"ppid": 1',
            '"docker_ancestry_prohibited": True',
            'properties.get("Transient") != "yes"',
            'int(properties.get("MainPID") or 0) != pid',
            '"process_starttime_ticks": _proc_starttime_ticks(pid)',
            '"namespaces": namespaces',
            '"control_group": control_group',
            'if os.getppid() != 1',
            'Path("/.dockerenv").exists()',
        ):
            self.assertIn(fragment, authority)
        self.assertNotIn('parser.add_argument("--main-agent-token")', authority)
        self.assertNotIn('parser.add_argument("--main-agent-token")', collector)
        self.assertIn(
            'parser.add_argument("--main-agent-token-file", type=Path)',
            authority,
        )
        self.assertIn(
            'parser.add_argument("--main-agent-token-file", type=Path)',
            collector,
        )
        self.assertNotIn('"--main-agent-token",', fresh)
        self.assertIn('"--main-agent-token-file",', fresh)

    def test_readiness_evidence_and_fresh_r0_envelope_are_preserved(self):
        source = builder.authority_payload()
        self.assertIn(b"mysql_tpch_readiness_probe_trace_v1", source)
        self.assertIn(
            b"FAIL_FORMAL_DATABASE_TCP_READINESS_BEFORE_CLEANUP", source
        )
        fresh = builder.fresh_r0_payload().decode("utf-8")
        for fragment in (
            '0.30 <= utilization["cpu"] <= 0.50',
            '0.85 <= utilization["memory"] <= 0.95',
            '0.30 <= utilization["io"] <= 0.50',
        ):
            self.assertIn(fragment, fresh)
        runtime = json.loads(builder.runtime_payload())
        self.assertEqual(runtime["resource_envelope"]["cpu_quota_cores"], 4)
        self.assertEqual(runtime["resource_envelope"]["io_read_bps"], 100663296)
        self.assertEqual(runtime["resource_envelope"]["io_write_bps"], 100663296)

    def test_collector_daemon_readback_fails_closed_and_passes_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "collector.py"
            path.write_bytes(builder.collector_payload())
            qualification = builder.PREDECESSOR / "qualification"
            sys.path.insert(0, str(qualification))
            try:
                collector = import_path("tpch_daemon_collector", path)
            finally:
                sys.path.remove(str(qualification))
            original_run = collector.run
            original_stat = collector.os.stat

            def run(argv, timeout, label):
                if argv[0] == "systemctl":
                    return (
                        "ActiveState=active\nSubState=running\nMainPID=123\n"
                        "FragmentPath=/etc/systemd/system/"
                        "ra-docker-mysql-tpch-v1.service\n"
                        "ExecStart=/usr/bin/dockerd "
                        "--host=unix:///run/ra-docker-mysql-tpch-v1.sock "
                        "--data-root=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/"
                        "docker_ra_tpch_v1 --storage-driver=overlay2 "
                        "--bridge=none --iptables=false --ip-forward=false "
                        "--ip-masq=false --userland-proxy=false\n"
                    )
                if argv[0] == "docker":
                    self.assertEqual(
                        argv[:3],
                        [
                            "docker", "--host",
                            "unix:///run/ra-docker-mysql-tpch-v1.sock",
                        ],
                    )
                    return json.dumps({
                        "DockerRootDir": builder.DATA_ROOT,
                        "Driver": "overlay2",
                        "CgroupDriver": "cgroupfs",
                    })
                return "/dev/nvme0n1 ext4 /mnt/nvme0n1\n"

            def fake_stat(path, follow_symlinks=True):
                if str(path) == builder.SOCKET:
                    return SimpleNamespace(st_mode=stat.S_IFSOCK)
                return SimpleNamespace(st_dev=os.makedev(259, 1))

            try:
                collector.run = run
                collector.os.stat = fake_stat
                value = collector.dedicated_daemon_readback()
                self.assertEqual(
                    value["status"], "PASS_DEDICATED_DAEMON_EXACT_NON_PG"
                )
                self.assertFalse(value["iptables"])
                collector.run = lambda *args, **kwargs: (
                    "ActiveState=inactive\n"
                )
                with self.assertRaises(collector.QualificationError):
                    collector.dedicated_daemon_readback()
            finally:
                collector.run = original_run
                collector.os.stat = original_stat

    def test_generated_python_compiles_without_writing_bundle(self):
        predecessor = builder.verify_predecessor()
        payloads = builder.payloads(predecessor)
        for relative in (
            builder.AUTHORITY,
            builder.COLLECTOR,
            builder.BLKIO,
            builder.FRESH_R0,
        ):
            compile(payloads[relative], relative, "exec")


class PublisherTests(unittest.TestCase):
    def test_predecessor_and_closure(self):
        predecessor = builder.verify_predecessor()
        payloads = builder.payloads(predecessor)
        changed = {
            name for name, digest in predecessor["files"].items()
            if builder.sha_bytes(payloads[name]) != digest
        }
        self.assertEqual(
            changed,
            {
                builder.AUTHORITY, builder.COLLECTOR, builder.BLKIO,
                builder.FRESH_R0, builder.RUNTIME,
            },
        )
        for added in (
            builder.INVALIDATION_RELATIVE,
            builder.INTERMEDIATE_INVALIDATION_RELATIVE,
            builder.CONTRACT_RELATIVE,
            builder.UNIT_RELATIVE, builder.BUILDER_RELATIVE,
        ):
            self.assertIn(added, payloads)


if __name__ == "__main__":
    unittest.main()
