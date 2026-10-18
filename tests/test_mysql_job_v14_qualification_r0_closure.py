from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve()
PACKAGE_ROOT = HERE.parents[1]
ROOT = next(candidate for candidate in HERE.parents
            if (candidate / "artifacts/mysql_job_raw_all_subset_three_level_successor_v13").is_dir())


def module(name: str, relative: str):
    path = ROOT / relative
    if (PACKAGE_ROOT / "controller").is_dir() and relative.startswith("shell/"):
        basename = Path(relative).name
        packaged = {
            "mysql_job_xp_shared_qualification_dispatch_v1.py": "qualification/" + basename,
            "mysql_job_v13_fresh_r0_runner_v1.py": "qualification/" + basename,
            "mysql_job_v13_qualification_authority_v2.py": "controller/" + basename,
            "mysql_job_xp_four_flow_controller_v1.py": "controller/" + basename,
            "mysql_content_addressed_bundle_publish_v1.py": "publisher/" + basename,
        }
        path = PACKAGE_ROOT / packaged.get(basename, relative)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class SharedDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dispatch = module(
            "job_xp_dispatch_test",
            "shell/mysql_job_xp_shared_qualification_dispatch_v1.py",
        )

    def test_both_backends_are_exactly_hash_bound(self):
        actual_paths = {
            "job": ROOT / ("artifacts/mysql_job_raw_all_subset_three_level_successor_v13/"
                           "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7/"
                           "qualification/mysql_job_distributed_live_readback_qualification_v2.py"),
            "xp": ROOT / ("artifacts/mysql_xp_node02_successor_v23/"
                          "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e/"
                          "qualification/mysql_distributed_live_readback_qualification_v1.py"),
        }
        for name, (_, expected) in self.dispatch.BACKENDS.items():
            path = actual_paths[name]
            self.assertTrue(path.is_file(), name)
            self.assertEqual(expected, self.dispatch.sha256(path), name)
            loaded = self.dispatch.load_backend(name, path)
            self.assertEqual(
                path.resolve(), Path(loaded.__file__).resolve(),
            )
            self.assertEqual(expected, loaded.sha256(path))
            manifest, runtime = loaded.verify_bundle(path.parents[1])
            self.assertEqual(path.parents[1].name, manifest["bundle_id"])
            self.assertIsInstance(runtime, dict)
            self.assertEqual(
                self.dispatch.sha256(ROOT / "shell/mysql_job_xp_shared_qualification_dispatch_v1.py"),
                loaded.sha256(path),
            )

    def test_xp_v18_through_current_v23_are_exactly_supported(self):
        for bundle_id, expected in self.dispatch.SUPPORTED_BACKEND_HASHES["xp"].items():
            root_name = ({
                self.dispatch.XP_V19_BUNDLE_ID: "mysql_xp_node02_successor_v19",
                self.dispatch.XP_V20_BUNDLE_ID: "mysql_xp_node02_successor_v20",
                self.dispatch.XP_V21_BUNDLE_ID: "mysql_xp_node02_successor_v21",
                self.dispatch.XP_V22_BUNDLE_ID: "mysql_xp_node02_successor_v22",
                self.dispatch.XP_V23_BUNDLE_ID: "mysql_xp_node02_successor_v23",
            }).get(bundle_id, "mysql_xp_node02_successor_v18")
            path = ROOT / "artifacts" / root_name / bundle_id / "qualification/mysql_distributed_live_readback_qualification_v1.py"
            self.assertEqual(expected, self.dispatch.sha256(path))
            loaded = self.dispatch.load_backend("xp", path)
            self.assertEqual(expected, loaded.sha256(path))


class QualificationAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = module(
            "job_qualification_authority_test",
            "shell/mysql_job_v13_qualification_authority_v2.py",
        )

    def test_peer_leaf_can_be_bound_before_leaf_exists(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            peer_id = "xp-paired-" + "a" * 32
            target = root / peer_id / "workload_metrics.jsonl"
            self.assertEqual(
                str(target),
                self.authority.safe_peer_metric(target, peer_id),
            )

    def test_peer_leaf_name_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            peer_id = "xp-paired-" + "b" * 32
            with self.assertRaises(self.authority.AuthorityError):
                self.authority.safe_peer_metric(root / peer_id / "wrong.jsonl", peer_id)


class TransactionalPublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.publisher = module(
            "job_support_publisher_test",
            "shell/mysql_content_addressed_bundle_publish_v1.py",
        )

    def test_publish_is_atomic_verified_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            payload = b"transactional publication fixture\n"
            unsigned = {
                "schema_version": "content_addressed_publish_test_v1",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "files": {"payload.txt": hashlib.sha256(payload).hexdigest()},
            }
            bundle_id = hashlib.sha256(self.publisher.canonical(unsigned)).hexdigest()
            source = root / "source" / bundle_id
            source.mkdir(parents=True)
            (source / "payload.txt").write_bytes(payload)
            manifest_payload = self.publisher.canonical(dict(unsigned, bundle_id=bundle_id))
            (source / "bundle_manifest.json").write_bytes(manifest_payload)
            (source / "bundle_manifest.json.sha256").write_text(
                "%s  bundle_manifest.json\n" % hashlib.sha256(manifest_payload).hexdigest(),
                encoding="ascii",
            )
            destination_root = root / "destination"
            destination_root.mkdir()
            first = self.publisher.publish(source, destination_root, bundle_id)
            second = self.publisher.publish(source, destination_root, bundle_id)
            self.assertEqual("PASS_ATOMICALLY_PUBLISHED", first["status"])
            self.assertEqual("PASS_ALREADY_PUBLISHED_IDENTICAL", second["status"])
            published = destination_root / bundle_id
            self.assertEqual(0o555, published.stat().st_mode & 0o777)
            self.publisher.verify(published, bundle_id)
            for path in sorted(published.rglob("*"), reverse=True):
                path.chmod(0o600 if path.is_file() else 0o700)
            published.chmod(0o700)


class FreshR0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r0 = module(
            "job_fresh_r0_test",
            "shell/mysql_job_v13_fresh_r0_runner_v1.py",
        )

    def bootstrap(self):
        inspect = {
            "Name": "/job_db",
            "Config": {},
            "HostConfig": {},
            "Mounts": [],
            "Image": "sha256:image",
            "Created": "2026-08-03T00:00:00Z",
        }
        return {
            "schema_version": "mysql_job_dedicated_daemon_bootstrap_evidence_v1",
            "status": "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY",
            "bundle_id": self.r0.TARGET_ID,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "qualification_only": True,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "attempt_token": "job-v2-20260803t011839z-" + "1" * 24,
            "database": {"container_id": "a" * 64, "pid": 101, "inspect": inspect},
            "client": {"container_id": "b" * 64, "pid": 102, "inspect": {}},
            "copy": {"destination": "/mnt/mysql_nvme1n1/job/attempt/datadir"},
        }

    def raw(self, bootstrap):
        return {
            "schema_version": "mysql_algorithm_c_debug_qualification_evidence_v2",
            "status": "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY",
            "mode": "qualification_solo",
            "bundle_id": self.r0.TARGET_ID,
            "scenario": "job_memory",
            "hostname": "node02",
            "debug_only": True,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "duration_seconds": 180,
            "minimum_samples": 181,
            "samples": [{} for _ in range(181)],
            "summary": {
                "sample_count": 181,
                "elapsed_seconds": 180.0,
                "resource_utilization": {
                    "cpu_usage_ratio": 0.4,
                    "memory_utilization_ratio": 0.9,
                    "device_utilization_ratio": 0.4,
                },
            },
            "initial_live_readback": {
                "container": {
                    "container_id": bootstrap["database"]["container_id"],
                    "pid": bootstrap["database"]["pid"],
                }
            },
            "accepted_round_authorized": False,
        }

    def test_r0_is_zero_credit_and_preserves_same_planner(self):
        bootstrap = self.bootstrap()
        gate = {"status": "PASS"}
        evidence = self.r0.build_evidence(bootstrap, gate, self.raw(bootstrap), "c" * 64)
        self.assertEqual(0, evidence["candidate_credit"])
        self.assertEqual(0, evidence["top5_replay_credit"])
        self.assertEqual(0, evidence["accepted_rounds_credit"])
        self.assertFalse(evidence["accepted_round_authorized"])
        self.assertEqual(
            bootstrap["database"]["container_id"],
            evidence["planner_identity"]["container_id"],
        )

    def test_out_of_band_profile_fails_closed(self):
        bootstrap = self.bootstrap()
        raw = self.raw(bootstrap)
        raw["summary"]["resource_utilization"]["memory_utilization_ratio"] = 0.2
        with self.assertRaises(self.r0.R0Error):
            self.r0.build_evidence(bootstrap, {"status": "PASS"}, raw, "d" * 64)

    def test_handoff_rejects_accepted_authority(self):
        bootstrap = self.bootstrap()
        gate = {
            "schema_version": "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
            "status": "PASS",
            "scenario": "job_memory",
            "host_id": "node02",
            "attempt_token": bootstrap["attempt_token"],
            "all_tolerances_passed": True,
            "ongoing_monitor_required": True,
            "database_counter_missing_samples": 0,
            "database_counter_timeout_count": 0,
            "accepted_round_authorized": False,
        }
        handoff = {
            "schema_version": "mysql_job_distributed_v2_same_live_planner_handoff_v1",
            "status": "READY_FRESH_R0_SAME_LIVE_PLANNER",
            "stage": "fresh_r0",
            "attempt_token": bootstrap["attempt_token"],
            "bundle_id": self.r0.TARGET_ID,
            "dynamic_gate_sha256": hashlib.sha256(self.r0.canonical(gate)).hexdigest(),
            "fresh_r0_authorized": True,
            "formal10_authorized": False,
            "target_accepted_rounds": 0,
            "outer_acceptance_authorized": False,
            "capture_or_r0_or_debug_countable_as_accepted": False,
            "accepted_round_authorized": True,
            "expires_at_utc": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "planner_identity": {"container_id": "a" * 64, "pid": 101},
            "client_identity": {"container_id": "b" * 64, "pid": 102},
        }
        command = {
            "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
            "bundle_id": self.r0.TARGET_ID,
            "mode": "qualification_solo",
            "debug_only": True,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "qualification_attempt_id": "job-r0-" + "x" * 32,
            "client_container": {"container_id": "b" * 64},
        }
        secret = "s" * 48
        authority = {
            "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
            "bundle_id": self.r0.TARGET_ID,
            "mode": "qualification_solo",
            "qualification_attempt_id": command["qualification_attempt_id"],
            "main_agent_token_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "accepted_round_authorized": False,
            "not_before_epoch": datetime.now(timezone.utc).timestamp() - 1,
            "expires_epoch": datetime.now(timezone.utc).timestamp() + 60,
        }
        with self.assertRaises(self.r0.R0Error):
            self.r0.validate_inputs(bootstrap, gate, handoff, command, authority, secret)


class FourFlowCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow = module(
            "job_xp_four_flow_test",
            "shell/mysql_job_xp_four_flow_controller_v1.py",
        )

    def test_paired_command_contains_peer_and_never_formal_flags(self):
        launch = {
            "scenario_backend": "job",
            "mode": "qualification_paired",
            "bundle": "/bundle",
            "container": "db",
            "mysql_client_container": "client",
            "mysql_defaults_file": "/private/mysql.cnf",
            "output": "/tmp/evidence.json",
            "main_agent_token_file": "/tmp/token",
            "main_agent_authority": "/tmp/authority.json",
            "command_spec": "/tmp/command.json",
            "peer_readback": "/tmp/peer.json",
            "peer_container": "peer-db",
        }
        argv = self.flow.command(Path("/shared.py"), launch)
        self.assertIn("--qualification-paired", argv)
        self.assertIn("--peer-readback", argv)
        self.assertNotIn("--formal10", argv)
        self.assertNotIn("--accepted-round", argv)


if __name__ == "__main__":
    unittest.main()
