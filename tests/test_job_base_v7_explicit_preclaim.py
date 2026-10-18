from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/run_job_round8_strict_serial_base_v7_explicit_preclaim.py"
FROZEN_BASE = Path("/tmp/job_v36_deps/run_job_round8_strict_serial.py")


def import_module():
    with mock.patch.dict(
        os.environ,
        {"JOB_ROUND8_FROZEN_BASE_RUNNER": str(FROZEN_BASE)},
        clear=False,
    ):
        spec = importlib.util.spec_from_file_location("job_base_v7_test", MODULE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot import JOB base-v7 adapter")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


adapter = import_module()


class JobBaseV7ExplicitPreclaimTest(unittest.TestCase):
    def test_workload_environment_seals_exact_candidate_costs(self) -> None:
        params = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.0005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 7000.0,
            "parallel_tuple_cost": 0.001,
        }
        adapter._ACTIVE_COSTS = params
        adapter._ACTIVE_PSQL = ["psql"]
        with mock.patch.object(
            adapter,
            "_frozen_workload_environment",
            return_value={"BASE": "1"},
        ), mock.patch.object(
            adapter,
            "_control_readiness",
            return_value={"status": "passed", "attempts_used": 3},
        ), mock.patch.object(
            adapter,
            "_docker_network_readiness",
            return_value={"status": "passed", "name": "ra_job_v53_net"},
        ):
            environment = adapter.workload_environment(
                Path("/tmp/point"), diagnostic_only=True
            )
        self.assertEqual(
            json.loads(environment[adapter.EXPLICIT_COST_ENV]), params
        )
        self.assertEqual(
            environment["RA_PG_CALIBRATION_SUBSET_ID"],
            "job_v53_dedicated_network_exact_seven_candidate_costs",
        )
        self.assertEqual(
            json.loads(environment["RA_PG_CONTROL_READINESS_JSON"])[
                "docker_network"
            ]["name"],
            "ra_job_v53_net",
        )

    def test_dedicated_network_is_bridge_with_zero_containers(self) -> None:
        payload = [
            {
                "Name": "ra_job_v53_net",
                "Driver": "bridge",
                "Scope": "local",
                "Internal": False,
                "Containers": {},
                "Options": {
                    "com.docker.network.bridge.name": "ra-job-v53",
                },
                "IPAM": {
                    "Config": [
                        {"Subnet": "192.0.2.49/24", "Gateway": "192.0.2.50"}
                    ]
                },
            }
        ]
        with mock.patch.dict(
            os.environ,
            {adapter.JOB_DOCKER_NETWORK_ENV: "ra_job_v53_net"},
        ), mock.patch.object(
            adapter.subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout=json.dumps(payload)),
        ):
            readiness = adapter._docker_network_readiness()
        self.assertEqual(readiness["attached_container_count"], 0)
        self.assertEqual(readiness["bridge_interface"], "ra-job-v53")

    def test_network_with_container_or_prohibited_name_fails_closed(self) -> None:
        payload = [
            {
                "Name": "ra_job_v53_net",
                "Driver": "bridge",
                "Scope": "local",
                "Internal": False,
                "Containers": {"abc": {"Name": "unexpected"}},
                "Options": {
                    "com.docker.network.bridge.name": "ra-job-v53",
                },
                "IPAM": {
                    "Config": [
                        {"Subnet": "192.0.2.49/24", "Gateway": "192.0.2.50"}
                    ]
                },
            }
        ]
        with mock.patch.dict(
            os.environ,
            {adapter.JOB_DOCKER_NETWORK_ENV: "ra_job_v53_net"},
        ), mock.patch.object(
            adapter.subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout=json.dumps(payload)),
        ):
            with self.assertRaisesRegex(RuntimeError, "contract mismatch"):
                adapter._docker_network_readiness()
        with mock.patch.dict(
            os.environ,
            {adapter.JOB_DOCKER_NETWORK_ENV: "bridge"},
        ):
            with self.assertRaisesRegex(RuntimeError, "prohibited"):
                adapter._docker_network_readiness()

    def test_readiness_requires_three_consecutive_successes_within_six(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="1|f\n"),
            mock.Mock(returncode=1, stdout="closed\n"),
            mock.Mock(returncode=0, stdout="1|f\n"),
            mock.Mock(returncode=0, stdout="1|f\n"),
            mock.Mock(returncode=0, stdout="1|f\n"),
        ]
        with mock.patch.object(adapter.subprocess, "run", side_effect=results), mock.patch.object(
            adapter.time,
            "sleep",
        ):
            readiness = adapter._control_readiness(["psql"])
        self.assertEqual(readiness["attempts_used"], 5)
        self.assertEqual(len(readiness["observations"]), 5)

    def test_failed_readiness_writes_preclaim_marker_without_bridge(self) -> None:
        params = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.0005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 7000.0,
            "parallel_tuple_cost": 0.001,
        }
        adapter._ACTIVE_COSTS = params
        adapter._ACTIVE_PSQL = ["psql"]
        with tempfile.TemporaryDirectory(prefix="job_readiness_") as raw:
            point_root = Path(raw) / "point"
            with mock.patch.object(
                adapter,
                "_frozen_workload_environment",
                return_value={},
            ), mock.patch.object(
                adapter,
                "_docker_network_readiness",
                return_value={"status": "passed", "name": "ra_job_v53_net"},
            ), mock.patch.object(
                adapter,
                "_control_readiness",
                side_effect=RuntimeError("six failures"),
            ):
                with self.assertRaisesRegex(RuntimeError, "ordinal was not claimed"):
                    adapter.workload_environment(point_root, diagnostic_only=True)
            marker = json.loads(
                (point_root / "workload.log").read_text(encoding="utf-8")
            )
        self.assertEqual(marker["status"], adapter.PRECLAIM_MARKER)
        self.assertTrue(marker["preclaim_no_ordinal_claim"])
        self.assertFalse(marker["bridge_invoked"])

    def test_preclaim_marker_removes_physical_invocation_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_preclaim_") as raw:
            replay = Path(raw)
            rank_root = replay / "rank_01"
            rank_root.mkdir()
            marker = {
                "status": adapter.PRECLAIM_MARKER,
                "preclaim_no_ordinal_claim": True,
                "technical_failure": True,
            }
            (rank_root / "workload.log").write_text(
                json.dumps(marker) + "\n", encoding="utf-8"
            )
            summary = {
                "physical_invocation_count": 1,
                "real_workload_invocation_count": 1,
                "successful_real_replay_count": 0,
                "technical_failure_count": 1,
                "invocations": [{"rank": 1}],
                "technical_failures": [{"rank": 1}],
            }
            summary_path = replay / "summary.json"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
            result = {
                "summary_path": str(summary_path),
                "physical_invocation_count": 1,
                "successful_real_replay_count": 0,
                "technical_failure_count": 1,
            }
            normalized = adapter._normalize_preclaim_failure(result, replay)
            observed = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(normalized["physical_invocation_count"], 0)
        self.assertEqual(observed["physical_invocation_count"], 0)
        self.assertEqual(observed["invocations"], [])
        self.assertEqual(observed["pre_invocation_technical_failure_ranks"], [1])

    def test_postclaim_failure_remains_physical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_postclaim_") as raw:
            replay = Path(raw)
            rank_root = replay / "rank_01"
            rank_root.mkdir()
            (rank_root / "workload.log").write_text(
                "post-claim crash\n", encoding="utf-8"
            )
            summary = {
                "physical_invocation_count": 1,
                "real_workload_invocation_count": 1,
                "successful_real_replay_count": 0,
                "technical_failure_count": 1,
                "invocations": [{"rank": 1}],
                "technical_failures": [{"rank": 1}],
            }
            summary_path = replay / "summary.json"
            summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
            result = {
                "summary_path": str(summary_path),
                "physical_invocation_count": 1,
            }
            normalized = adapter._normalize_preclaim_failure(result, replay)
        self.assertEqual(normalized["physical_invocation_count"], 1)


if __name__ == "__main__":
    unittest.main()
