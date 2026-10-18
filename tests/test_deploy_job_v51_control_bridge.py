from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/deploy_job_v51_control_bridge.py"


def import_module():
    spec = importlib.util.spec_from_file_location("job_v51_deploy_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import JOB v51 deploy helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = import_module()


class DeployJobV51ControlBridgeTest(unittest.TestCase):
    def test_atomic_backup_replace_and_idempotent_recheck(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_v51_deploy_") as raw:
            root = Path(raw)
            source = root / "source.py"
            target = root / "target.py"
            audit = root / "audit.json"
            source.write_text("new bridge\n", encoding="utf-8")
            target.write_text("old bridge\n", encoding="utf-8")
            old_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            new_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            with mock.patch.multiple(
                deploy,
                SOURCE=source,
                TARGET=target,
                AUDIT=audit,
                OLD_SHA256=old_sha,
                NEW_SHA256=new_sha,
            ):
                self.assertEqual(deploy.main(), 0)
                self.assertEqual(deploy.main(), 0)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), new_sha)
            payload = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(payload["workload_calls"], 0)
            self.assertEqual(payload["ordinal_claims"], 0)
            self.assertFalse(payload["mysql_touched"])
            backups = list(root.glob("target.py.pre_v51.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(hashlib.sha256(backups[0].read_bytes()).hexdigest(), old_sha)


if __name__ == "__main__":
    unittest.main()
