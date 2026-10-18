from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
V49 = TOOLS / "job_fresh10_target10_autonomous_v49.py"
V50 = TOOLS / "job_fresh10_target10_autonomous_v50.py"
V54 = TOOLS / "job_fresh10_target10_autonomous_v54.py"
BOOTSTRAP = TOOLS / "bootstrap_job_v54_fresh0.py"
LAUNCHER = TOOLS / "launch_job_target10_autonomous_v54_fresh0.sh"
BASE = TOOLS / "run_job_round8_strict_serial_base_v7_explicit_preclaim.py"
DEPLOY = TOOLS / "deploy_job_v54_network_bridge.py"
BRIDGE = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02"
    / "formal_job_outer_replay_v54.py"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_v54():
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("job_v54_binding_test", V54)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot import JOB v54")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(TOOLS):
            sys.path.pop(0)


class JobV54ControllerBindingTest(unittest.TestCase):
    def test_v54_uses_independent_root_and_campaign_contract(self) -> None:
        module = import_v54()
        implementation = module.implementation
        self.assertEqual(implementation.CONTROLLER_VERSION, "v54")
        self.assertEqual(
            implementation.FRESH_ROOT.name,
            "autonomous_target10_v54_fresh0",
        )
        self.assertEqual(
            implementation.CAMPAIGN_CONTRACT,
            "job_v54_fresh0_v53_network_conflict_excluded_v1",
        )
        self.assertEqual(implementation.v49.ROOT, implementation.FRESH_ROOT)
        self.assertEqual(implementation.v49.STATE, implementation.FRESH_STATE)

    def test_launcher_pins_new_root_initializer_and_zero_call_preflight(self) -> None:
        text = LAUNCHER.read_text(encoding="utf-8")
        for path in (V49, V50, V54, BOOTSTRAP, BASE, DEPLOY, BRIDGE):
            self.assertIn(sha(path), text)
        self.assertIn("autonomous_target10_v54_fresh0", text)
        self.assertIn("docker network create", text)
        self.assertIn("ra_job_v53_net", text)
        self.assertIn("--preflight-only", text)
        self.assertNotIn("bootstrap_job_v50_fresh0.py", text)
        self.assertIn("deploy_job_v54_network_bridge.py", text)
        self.assertNotIn("c53fbb20b401941ec83555bfd64211162", text)
        self.assertIn(
            "240235dac61731fc4355ba21ffc7dd701d3c2154da7b6e49933ea2f9e01f5b0a",
            text,
        )
        self.assertNotIn("ssh ", text)


if __name__ == "__main__":
    unittest.main()
