from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "shell/build_mysql_tpcc_distributed_successor_v5.py"


def test_v10_reseals_attempt_config_authority_and_denylists_v9(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("tpcc_v5_builder", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = module.build(tmp_path)
    manifest = module.verify_published(bundle)
    assert module.BASE_ID in manifest["invalidated_bundle_ids"]
    assert "/mnt/nvme3n1" not in (bundle / "authority/mysql_tpcc_node04_adopted_service_v1.py").read_text()
    assert 'MOUNT = "/mnt/mysql_tpcc_nvme3n1"' in (bundle / "authority/mysql_tpcc_node04_adopted_service_v1.py").read_text()
    for name in ("tpcc_v2_formal10_readiness_verifier.py", "tpcc_v3_formal10_readiness_verifier.py"):
        text = (bundle / "authority" / name).read_text()
        assert "/mnt/nvme3n1" not in text
        assert '"/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"' in text
    runtime = json.loads((bundle / "distributed_runtime_contract.json").read_text())
    toolchain = runtime["toolchain"]
    expected_root = (
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
        "runtime_algorithm_c_successor_20260729/runtime/python311-"
        "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
    )
    assert toolchain["python_runtime_root"] == expected_root
    assert toolchain["python_runtime"] == expected_root + "/bin/python3.11"
    assert toolchain["python_tree_content_sha256"] == expected_root.rsplit("-", 1)[1]
    assert toolchain["python_tree_manifest_sha256"] == module.sha256(
        bundle / "toolchain/python-3.11.11-tree-manifest.json"
    )
    assert runtime["storage"]["mount"] == "/mnt/mysql_tpcc_nvme3n1"
    assert runtime["storage"]["runtime_root"].startswith("/mnt/mysql_tpcc_nvme3n1/")
    assert runtime["client_runtime_mounts"]["python"]["source"] == expected_root
    service = (bundle / "authority/mysql_tpcc_node04_adopted_service_v1.py").read_text()
    assert "PYTHON_SOURCE = (" in service
    assert "RUNTIME_ROOT + \"/runtime/python311-\" + PYTHON_TREE_SHA256" in service
    assert 'MOUNT = "/mnt/mysql_tpcc_nvme3n1"' in service
    assert 'mount_root = Path(MOUNT)' in service
    assert "/mnt/nvme3n1" not in service
    assert ",rw" not in service
    assert 'mount.get("RW") is not True' in service
    profile = json.loads((bundle / "configuration/config_profile.json").read_text())
    assert profile["qps_resource_input_prohibited"] is True
    assert profile["password_handling"].startswith("external_0600_file")
    assert "303c0e082062f1456026dbf6b6906867bc82b3caba59b28a" not in json.dumps(profile)
    assert (bundle / "authority/mysql_tpcc_config_materialize_v1.py").is_file()
