from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02"
    / "formal_job_outer_replay_v53.py"
)


def import_bridge():
    spec = importlib.util.spec_from_file_location("job_v53_network_bridge_test", BRIDGE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import JOB v53 bridge")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"psycopg2": mock.Mock()}):
        spec.loader.exec_module(module)
    return module


bridge = import_bridge()


class JobV53NetworkBridgeTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
