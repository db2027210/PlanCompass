from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02"
    / "formal_job_outer_replay_v54.py"
)


def import_bridge():
    spec = importlib.util.spec_from_file_location("job_v54_network_bridge_test", BRIDGE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import JOB v54 bridge")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"psycopg2": mock.Mock()}):
        spec.loader.exec_module(module)
    return module


bridge = import_bridge()


class JobV54NetworkBridgeTest(unittest.TestCase):
    def test_requires_dedicated_network_name(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"RA_JOB_DOCKER_NETWORK": "ra_job_v53_net"},
        ):
            self.assertEqual(bridge.job_docker_network(), "ra_job_v53_net")
        for value in ("", "bridge", "host", "none", " bad"):
            with self.subTest(value=value), mock.patch.dict(
                os.environ,
                {"RA_JOB_DOCKER_NETWORK": value},
            ):
                with self.assertRaisesRegex(RuntimeError, "dedicated network"):
                    bridge.job_docker_network()

    def test_point_container_uses_network_and_validates_before_claim(self) -> None:
        start_source = inspect.getsource(bridge.start_lane_container)
        main_source = inspect.getsource(bridge.main)
        self.assertIn('"--network", docker_network', start_source)
        self.assertIn('"docker_network": docker_network', start_source)
        self.assertLess(
            main_source.index("job_docker_network()"),
            main_source.index("claim_replay_accounting()"),
        )

    def test_zero_container_command_assembly_has_one_dedicated_network(self) -> None:
        resource_arguments = [
            "--cpuset-cpus",
            "12-17,48-53",
            "--network",
            "bridge",
            "--memory",
            "8g",
        ]
        filtered = bridge.dedicated_network_resource_arguments(resource_arguments)
        command = [
            "docker",
            "run",
            "--network",
            "ra_job_v53_net",
            *filtered,
            "postgres-image",
        ]
        bridge.validate_final_network_command(command, "ra_job_v53_net")
        self.assertEqual(command.count("--network"), 1)
        self.assertNotIn("bridge", command)
        self.assertNotIn("host", command)
        self.assertNotIn("none", command)

    def test_start_lane_assembles_one_network_without_running_container(self) -> None:
        class FakeEngine:
            FormalPgError = RuntimeError
            IMAGE = "postgres-image"
            RUNTIME_SHM_SIZE_BYTES = 1024

            def __init__(self) -> None:
                self.point_command = None

            def container_spec(self, kind):
                return bridge.POINT_CONTAINER_NAME, bridge.POINT_PORT

            def docker_resource_arguments(self, profile_name):
                return [
                    "--cpuset-cpus",
                    "12-17,48-53",
                    "--network",
                    "bridge",
                    "--memory",
                    "8g",
                ]

            def fixed_postgres_arguments(self, profile_name, cost_parameters):
                return ["postgres"]

            def run(self, command, **kwargs):
                if command == ["docker", "inspect", bridge.POINT_CONTAINER_NAME]:
                    return mock.Mock(returncode=1, stdout="")
                if command[:3] == ["docker", "run", "--detach"]:
                    self.point_command = list(command)
                    return mock.Mock(returncode=0, stdout="cid\n")
                if command == ["docker", "inspect", "cid"]:
                    host_config = {
                        field: [{"Path": "/dev/nvme2n1", "Rate": 134_217_728}]
                        for field in ("BlkioDeviceReadBps", "BlkioDeviceWriteBps")
                    }
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps([{"HostConfig": host_config}]),
                    )
                if command[:4] == ["docker", "inspect", "--format", "{{.State.Health.Status}}"]:
                    return mock.Mock(returncode=0, stdout="healthy\n")
                if command[:2] == ["docker", "exec"]:
                    return mock.Mock(returncode=0, stdout="")
                raise AssertionError(f"unexpected fake command: {command!r}")

        engine = FakeEngine()
        with tempfile.TemporaryDirectory(prefix="job_v54_assembly_") as raw, mock.patch.dict(
            os.environ,
            {"RA_JOB_DOCKER_NETWORK": "ra_job_v53_net"},
        ), mock.patch.object(
            bridge,
            "LANE_ROOT",
            Path(raw),
        ), mock.patch.object(
            bridge,
            "lane_run",
            return_value=mock.Mock(returncode=0, stdout=""),
        ):
            result = bridge.start_lane_container(
                engine,
                "point",
                Path(raw) / "pgdata",
            )
        self.assertIsNotNone(engine.point_command)
        self.assertEqual(engine.point_command.count("--network"), 1)
        network_index = engine.point_command.index("--network")
        self.assertEqual(engine.point_command[network_index + 1], "ra_job_v53_net")
        self.assertEqual(result["docker_network"], "ra_job_v53_net")

    def test_resource_network_shape_mismatch_fails_closed(self) -> None:
        invalid = (
            [],
            ["--network", "host"],
            ["--network", "bridge", "--network", "bridge"],
            ["--network=bridge"],
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(RuntimeError):
                    bridge.dedicated_network_resource_arguments(arguments)
        with self.assertRaisesRegex(RuntimeError, "network contract mismatch"):
            bridge.validate_final_network_command(
                [
                    "docker",
                    "run",
                    "--network",
                    "ra_job_v53_net",
                    "--network",
                    "bridge",
                ],
                "ra_job_v53_net",
            )


if __name__ == "__main__":
    unittest.main()
