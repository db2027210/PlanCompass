from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "shell/mysql_tpcc_v4_bootstrap_spec.py"
BUNDLE = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v10" / "2185b4b072121e9ab8d6bb6ed4ee0da0e9ebde955ca6a92e5a20f416287597fe"
loader = importlib.util.spec_from_file_location("tpcc_v4_bootstrap_spec", SCRIPT)
module = importlib.util.module_from_spec(loader)
assert loader and loader.loader
loader.loader.exec_module(module)


def test_exact_bundle_yields_only_nonformal_static_plan() -> None:
    token = "tpcc-v4-20260731t000000z-0123456789abcdef"
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert module.BUNDLE_ID == manifest["bundle_id"] == BUNDLE.name
    assert module.BUNDLE_REMOTE == module.RUNTIME_ROOT + "/bundles/" + manifest["bundle_id"]
    assert module.RUNTIME_ROOT.startswith("/mnt/mysql_tpcc_nvme3n1/")
    assert not module.RUNTIME_ROOT.startswith("/mnt/nvme3n1/")
    python_manifest = BUNDLE / "toolchain/python-3.11.11-tree-manifest.json"
    assert module.sha256(python_manifest) == module.PYTHON_MANIFEST_SHA256
    spec = module.make_spec(BUNDLE / "bundle_manifest.json", token, token, now=datetime.now(timezone.utc))
    plan = module.command_plan(spec)
    assert plan["host_python_runtime"]["executable"] == module.HOST_PYTHON
    assert plan["host_python_runtime"]["tree_content_sha256"] == module.PYTHON_TREE
    assert plan["host_python_runtime"]["root"].startswith("/mnt/mysql_tpcc_nvme3n1/")
    assert "/mnt/nvme3n1/" not in json.dumps(plan)
    assert plan["commands_to_run_on_node04"]["prepare_physical_copy"][0] == module.HOST_PYTHON
    config_command = plan["commands_to_run_on_node04"]["materialize_attempt_config_before_service_plan"]
    assert config_command[0:3] == [module.HOST_PYTHON, module.CONFIG_MATERIALIZER_REMOTE, "materialize"]
    assert spec["attempt"]["mysql_password_file"] in config_command
    assert config_command.index("--output") < len(config_command)
    assert '"python3"' not in json.dumps(plan["commands_to_run_on_node04"])
    assert plan["formal_r0_authorized"] is False
    assert plan["formal_authorized"] is False
    assert plan["accepted_round_authorized"] is False
    assert plan["physical_copy_authority"]["full_physical_copy"] is True
    assert plan["measurement_bridge_contract"]["filesystem"] == "tmpfs"
    assert plan["client_mysql_cli_contract"]["binary"] == "/usr/bin/mysql"
    assert plan["commands_to_run_on_node04"]["prepare_physical_copy"][0:2] == [module.HOST_PYTHON, module.SERVICE_REMOTE]
    assert "--reflink=" not in json.dumps(plan)


def test_rejects_token_or_image_boundary_drift() -> None:
    token = "tpcc-v4-20260731t000000z-0123456789abcdef"
    spec = module.make_spec(BUNDLE / "bundle_manifest.json", token, token, now=datetime.now(timezone.utc))
    spec["attempt"]["handoff_token_sha256"] = hashlib.sha256(b"different").hexdigest()
    # A digest alone cannot be semantically matched locally, but must retain
    # the exact shape; mutate an image boundary to exercise the hard binding.
    spec["images"]["client"]["image_id"] = "sha256:" + "0" * 64
    with pytest.raises(module.SpecError):
        module.validate(spec)


def test_rejects_root_filesystem_mount_alias() -> None:
    token = "tpcc-v4-20260731t000000z-0123456789abcdef"
    spec = module.make_spec(BUNDLE / "bundle_manifest.json", token, token, now=datetime.now(timezone.utc))
    spec["runtime_python"]["root"] = "/mnt/nvme3n1/runtime/python311-" + module.PYTHON_TREE
    with pytest.raises(module.SpecError):
        module.validate(spec)


def test_renderer_writes_sidecar_without_a_second_raw_token_field(tmp_path: Path) -> None:
    token = "tpcc-v4-20260731t000000z-0123456789abcdef"
    token_file = tmp_path / "token"
    token_file.write_text(token + "\n", encoding="utf-8")
    token_file.chmod(0o600)
    output = tmp_path / "bootstrap_spec.json"
    completed = subprocess.run([
        sys.executable, str(SCRIPT), "--render", "--bundle-manifest", str(BUNDLE / "bundle_manifest.json"),
        "--attempt-id", token, "--main-agent-token-file", str(token_file), "--output", str(output),
    ], text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    document = json.loads(output.read_text(encoding="utf-8"))
    assert "handoff_token" not in document["attempt"]
    assert document["attempt"]["handoff_token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert output.with_name(output.name + ".sha256").is_file()
    emitted = subprocess.run([sys.executable, str(SCRIPT), "--emit-plan", str(output)], text=True, capture_output=True, check=False)
    assert emitted.returncode == 0, emitted.stderr
    assert json.loads(emitted.stdout)["status"] == "PLAN_NOT_EXECUTED_NOT_AUTHORITY"
