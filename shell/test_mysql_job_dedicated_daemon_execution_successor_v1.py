#!/usr/bin/env python3
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE=Path(__file__).with_name("build_mysql_job_dedicated_daemon_execution_successor_v1.py")
spec=importlib.util.spec_from_file_location("builder",SOURCE); builder=importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)

class TestDedicatedDaemonExecutionPatch(unittest.TestCase):
    def test_authority_has_explicit_socket_host_network_and_server_port(self):
        source=(builder.PARENT/"qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py").read_text()
        patched=builder.patch_python("qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",source)
        self.assertIn('"docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock",',patched)
        self.assertIn('"--network",\n        "host",',patched)
        self.assertIn('"--port=%d" % MYSQL_PORT',patched)
        self.assertNotIn('"127.0.0.1:%d:3306" % MYSQL_PORT',patched)
        self.assertIn('host.get("NetworkMode") != "host"',patched)
    def test_collector_rejects_legacy_server_port(self):
        source=(builder.PARENT/"qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_text()
        patched=builder.patch_python("qualification/mysql_job_distributed_live_readback_qualification_v2.py",source)
        self.assertIn("server_port != 65102",patched)
        self.assertIn("database_counter_transport, port, 65102",patched)
        self.assertIn('"docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock",',patched)
    def test_materializer_is_fresh_attempt_only(self):
        source=builder.materializer_source().decode()
        self.assertIn("attempt_token=None",source)
        self.assertIn("ISSUED_NOT_EXECUTED",source)
        self.assertIn("formal_launch_authorized\":False",source)
        self.assertIn("accepted_round_authorized\":False",source)
    def test_materializer_e2e_token_renderer_validate_plan_and_sidecars(self):
        bundle=builder.OUTPUT/"8cc69a3cc762fe26b0ba4c61254c7d9a7b1802f5d7cec5c4cab9b7ea81289678"
        self.assertTrue((bundle/"bundle_manifest.json").is_file())
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); token=root/"token"; token.write_text("fixture-token"); os.chmod(token,0o600)
            result=subprocess.run([sys.executable,"-B",str(bundle/"qualification/mysql_job_dedicated_daemon_materializer_v2.py"),"--bundle-manifest",str(bundle/"bundle_manifest.json"),"--mysql-image-reference","mysql:8.0","--mysql-image-id","sha256:"+"a"*64,"--client-image-reference","mysql:8.0","--client-image-id","sha256:"+"b"*64,"--main-agent-token-file",str(token),"--output-root",str(root/"out")],text=True,capture_output=True,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
            self.assertEqual(result.returncode,0,result.stderr)
            record=__import__("json").loads(result.stdout); material=Path(record["materials"])
            for name in ("bootstrap_spec.json","command_plan.json","materials.json"):
                self.assertTrue((material/name).is_file()); self.assertTrue((material/(name+".sha256")).is_file())

if __name__=="__main__": unittest.main()
