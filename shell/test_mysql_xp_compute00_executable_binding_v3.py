#!/usr/bin/env python3
"""Offline cross-file binding tests for the compute00 XP executable successor."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import unittest


sys.dont_write_bytecode = True

SCHEMA = "mysql_xp_compute00_executable_bound_successor_v3"
PARENT_ID = "b4bd3255634e952e33ea603bbd4ec4b5eb0493450987c041ca3a220414c8a7c4"
TEMPLATE = "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/template_v2"
RUNTIME = "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803"
DB_CPUS = "184-191,440-447"
CLIENT_CPUS = "192-199,448-455"
RESERVED_CPUS = "184-199,440-455"
PG_CPUS = "32-51,288-307"
DB = {
    "cpu_quota_cores": 5.3,
    "cpuset_cpus": DB_CPUS,
    "cpuset_mems": "1",
    "memory_bytes": 8589934592,
    "memory_swap_bytes": 8589934592,
    "pids_max": 1024,
    "io_read_bps": 29360128,
    "io_write_bps": 524288,
    "innodb_buffer_pool_bytes": 4294967296,
    "mysql_port": 65104,
}
CLIENT = {
    "cpu_quota_cores": 8,
    "cpuset_cpus": CLIENT_CPUS,
    "cpuset_mems": "1",
    "memory_bytes": 8589934592,
    "memory_swap_bytes": 8589934592,
    "pids_max": 1024,
}
ACTIVE_TEXT = (
    "launch/mysql_xp_compute00_adopted_service_v1.py",
    "launch/mysql_xp_fresh_r0_executor_v2.py",
    "launch/mysql_xp_formal_evidence_producer_v1.py",
    "launch/mysql_xp_compute00_formal10_materializer_v1.py",
    "qualification/mysql_xp_qualification_authority_v1.py",
    "qualification/mysql_distributed_live_readback_qualification_v1.py",
    "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
)


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_module(bundle, relative, name):
    path = bundle / relative
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load " + relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def actual_files(bundle):
    result = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise AssertionError("bundle symlink prohibited: " + str(path))
        if path.is_file():
            relative = path.relative_to(bundle).as_posix()
            if relative not in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
                result[relative] = sha(path)
    return result


class ExecutableBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default = Path(__file__).resolve().parents[1]
        cls.bundle = Path(os.environ.get("XP_BUNDLE_UNDER_TEST", str(default))).resolve()
        cls.manifest = json.loads((cls.bundle / "bundle_manifest.json").read_text())
        cls.contract = json.loads((cls.bundle / "xp_executable_launch_contract_v3.json").read_text())
        cls.runtime_contract = json.loads((cls.bundle / "distributed_runtime_contract.json").read_text())
        launch = str(cls.bundle / "launch")
        if launch not in sys.path:
            sys.path.insert(0, launch)
        cls.service = load_module(cls.bundle, "launch/mysql_xp_compute00_adopted_service_v1.py", "xp_bound_service")
        cls.executor = load_module(cls.bundle, "launch/mysql_xp_fresh_r0_executor_v2.py", "xp_bound_fresh")
        cls.preflight = load_module(cls.bundle, "preflight/mysql_xp_compute00_disk1_preflight_v2.py", "xp_bound_preflight")
        cls.authority = load_module(cls.bundle, "qualification/mysql_xp_qualification_authority_v1.py", "xp_bound_authority")

    def test_manifest_content_address_and_full_closure(self):
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], SCHEMA)
        self.assertEqual(manifest["status"], "SEALED_COMPUTE00_EXECUTABLE_BOUND_NOT_LAUNCHED")
        self.assertEqual(manifest["predecessor_bundle_id"], PARENT_ID)
        self.assertEqual(manifest["supersedes_bundle_id"], PARENT_ID)
        self.assertEqual(manifest["formal_scope"], "mysql_only")
        self.assertTrue(manifest["cross_engine_comparison_prohibited"])
        self.assertTrue(manifest["postgresql_mutation_prohibited"])
        self.assertFalse(manifest["formal_launch_authorized"])
        self.assertFalse(manifest["formal_r0_authorized"])
        self.assertFalse(manifest["accepted_round_authorized"])
        self.assertEqual(manifest["accepted_rounds_credit"], 0)
        unsigned = dict(manifest)
        bundle_id = unsigned.pop("bundle_id")
        self.assertEqual(hashlib.sha256(canonical(unsigned)).hexdigest(), bundle_id)
        expected_name = os.environ.get("XP_EXPECTED_BUNDLE_ID", self.bundle.name)
        self.assertEqual(bundle_id, expected_name)
        self.assertEqual(actual_files(self.bundle), manifest["files"])
        sidecar = self.bundle / "bundle_manifest.json.sha256"
        self.assertEqual(sidecar.read_text(encoding="ascii"), "%s  bundle_manifest.json\n" % sha(self.bundle / "bundle_manifest.json"))
        for path in [self.bundle, *self.bundle.rglob("*")]:
            if not path.is_symlink():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0)

    def _check_contract(self, contract):
        self.assertEqual(contract["formal_scope"], "mysql_only")
        self.assertTrue(contract["cross_engine_comparison_prohibited"])
        self.assertTrue(contract["postgresql_mutation_prohibited"])
        self.assertFalse(contract["formal_launch_authorized"])
        self.assertEqual(contract["database_resource_envelope"], DB)
        client = contract["client_resource_envelope"]
        for key, value in CLIENT.items():
            self.assertEqual(client[key], value)
        storage = contract["storage"]
        self.assertEqual(storage["template_path"], TEMPLATE)
        self.assertEqual(storage["runtime_root"], RUNTIME)
        self.assertEqual(storage["data_partition"], "/dev/nvme1n1")
        self.assertEqual(storage["data_partition_major_minor"], "259:6")
        self.assertEqual(storage["effective_cgroup_v1_throttle_major_minor"], "259:6")
        host = contract["distributed_host_contract"]
        self.assertEqual(host["host_id"], "compute00")
        self.assertEqual(host["database_cpuset_cpus"], DB_CPUS)
        self.assertEqual(host["database_cpuset_mems"], "1")
        self.assertEqual(host["client_cpuset_cpus"], CLIENT_CPUS)
        self.assertEqual(host["client_cpuset_mems"], "1")
        self.assertEqual(host["mysql_xp_reserved_cpuset"], RESERVED_CPUS)
        self.assertEqual(host["postgresql_reserved_cpuset"], PG_CPUS)
        self.assertFalse(host["postgresql_mysql_cpuset_overlap"])

    def test_contracts_bind_same_exact_envelope(self):
        self._check_contract(self.contract)
        self._check_contract(self.runtime_contract)
        binding = self.contract["executable_binding"]
        self.assertEqual(binding["bundle_schema"], SCHEMA)
        self.assertEqual(binding["template_path"], TEMPLATE)
        self.assertEqual(binding["runtime_root"], RUNTIME)
        self.assertEqual(binding["database"], DB)
        self.assertEqual(binding["client"], CLIENT)
        self.assertEqual(binding["reserved_cpuset"], RESERVED_CPUS)
        self.assertEqual(binding["postgresql_reserved_cpuset"], PG_CPUS)
        self.assertEqual(binding["device_major_minor"], "259:6")

    def test_executable_constants_match_contract(self):
        service = self.service
        self.assertEqual(service.TEMPLATE, TEMPLATE)
        self.assertEqual(service.RUNTIME_ROOT, RUNTIME)
        self.assertEqual(service.DB_CPU_QUOTA / service.DB_CPU_PERIOD, 5.3)
        self.assertEqual(service.DB_CPUS, DB_CPUS)
        self.assertEqual(service.DB_MEMS, "1")
        self.assertEqual(service.READ_BPS, 29360128)
        self.assertEqual(service.WRITE_BPS, 524288)
        self.assertEqual(service.CLIENT_CPU_QUOTA / service.CLIENT_CPU_PERIOD, 8)
        self.assertEqual(service.CLIENT_CPUS, CLIENT_CPUS)
        self.assertEqual(service.CLIENT_MEMS, "1")
        self.assertEqual(service.DATA_PARTITION_MAJOR_MINOR, "259:6")
        self.assertEqual(self.executor.FORMAL, DB)
        self.assertEqual(self.executor.CLIENT, CLIENT)
        self.assertEqual(self.authority.CLIENT_CPUSET, CLIENT_CPUS)
        self.assertEqual(self.authority.CLIENT_MEMSET, "1")

    def test_adopted_service_self_verifies_and_emits_fresh_r0_identity(self):
        observed = self.service.verify_bundle(self.bundle)
        self.assertEqual(observed["bundle_id"], self.bundle.name)
        identity = self.service._same_live_identity(
            {
                "database": {"datadir": RUNTIME + "/attempt/datadir"},
                "attempt_id": "xp-formal-test-attempt",
                "handoff_token_sha256": "a" * 64,
            },
            {"container_id": "b" * 64, "pid": 101, "pid_starttime_ticks": 1001},
            {"container_id": "c" * 64, "pid": 202, "pid_starttime_ticks": 2002},
        )
        self.assertEqual(identity, {
            "database_container_id": "b" * 64,
            "mysql_pid": 101,
            "mysql_starttime_ticks": 1001,
            "physical_datadir": RUNTIME + "/attempt/datadir",
            "client_container_id": "c" * 64,
            "client_init_pid": 202,
            "client_init_starttime_ticks": 2002,
            "attempt_id": "xp-formal-test-attempt",
            "handoff_token_sha256": "a" * 64,
        })

    def test_preflight_covers_same_binding(self):
        preflight = self.preflight
        self.assertEqual(str(preflight.TEMPLATE), TEMPLATE)
        self.assertEqual(str(preflight.RUNTIME), RUNTIME)
        self.assertEqual(preflight.XP_CPUS, RESERVED_CPUS)
        self.assertEqual(preflight.DB_CPUS, DB_CPUS)
        self.assertEqual(preflight.CLIENT_CPUS, CLIENT_CPUS)
        self.assertEqual(preflight.PG_CPUS, PG_CPUS)
        self.assertEqual(preflight.MEMS, "1")
        self.assertEqual(preflight.MAJOR_MINOR, "259:6")
        target = preflight.cpus(RESERVED_CPUS)
        result = preflight.validate(
            hostname="compute00",
            row={"source": "/dev/nvme1n1", "major_minor": "259:6", "filesystem": "ext4", "mount_options": ["rw"], "super_options": []},
            device_dev="259:6", device_numa=0, node1=target, online=target,
            conflicts=[], runtime_exists=False, template_exists=True,
            template_identity={"status": "PASS_EXACT_CANONICAL_TEMPLATE"},
            port_free=True,
        )
        self.assertEqual(result["status"], "PASS_READY_NOT_LAUNCHED")
        self.assertEqual(result["formal_envelope"], {"cpu_cores": 5.3, "read_bps": 29360128, "write_bps": 524288})

    def test_active_files_accept_final_schema_and_have_no_stale_binding(self):
        forbidden = (
            "/disk1/anonymous/resource_adaptive_mysql/hybench_xp/template_v2",
            "runtime_algorithm_c_successor_20260729",
            'CLIENT_CPU_QUOTA = 1800000',
            'CLIENT_MEMS = "0"',
            '"cpu_quota_cores": 18',
            '"cpuset_mems": "0"',
            'client.get("cpuset_mems") == "0"',
            'cgroup.get("cpu_quota_cores") == 18',
            'cgroup.get("mems_allowed_list") == "0"',
        )
        schema_consumers = (
            "launch/mysql_xp_compute00_adopted_service_v1.py",
            "launch/mysql_xp_compute00_formal10_materializer_v1.py",
            "qualification/mysql_distributed_live_readback_qualification_v1.py",
            "qualification/mysql_xp_qualification_authority_v1.py",
            "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
        )
        for relative in ACTIVE_TEXT:
            text = (self.bundle / relative).read_text(encoding="utf-8")
            for stale in forbidden:
                self.assertNotIn(stale, text, "%s contains %s" % (relative, stale))
        for relative in schema_consumers:
            self.assertIn(SCHEMA, (self.bundle / relative).read_text(encoding="utf-8"))

    def test_manifest_and_contract_hash_every_changed_entrypoint(self):
        files = self.manifest["files"]
        paths = {
            "adopted_service": "launch/mysql_xp_compute00_adopted_service_v1.py",
            "fresh_executor": "launch/mysql_xp_fresh_r0_executor_v2.py",
            "formal_evidence_producer": "launch/mysql_xp_formal_evidence_producer_v1.py",
            "promotion_materializer": "launch/mysql_xp_compute00_formal10_materializer_v1.py",
            "qualification_authority": "qualification/mysql_xp_qualification_authority_v1.py",
            "live_readback": "qualification/mysql_distributed_live_readback_qualification_v1.py",
            "preflight": "preflight/mysql_xp_compute00_disk1_preflight_v2.py",
        }
        for label, relative in paths.items():
            observed = sha(self.bundle / relative)
            self.assertEqual(files[relative], observed)
            self.assertEqual(self.contract["executable_binding"]["entrypoint_sha256"][label], observed)
        self.assertEqual(self.contract["adopted_service"]["sha256"], files[paths["adopted_service"]])
        self.assertEqual(self.contract["fresh_r0_execution"]["executor_sha256"], files[paths["fresh_executor"]])
        self.assertEqual(self.contract["formal_evidence_producer"]["sha256"], files[paths["formal_evidence_producer"]])
        self.assertEqual(self.contract["promotion_materializer"]["sha256"], files[paths["promotion_materializer"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
