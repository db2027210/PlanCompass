from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
CONTRACT = "xp_executable_launch_contract_v3.json"
POSTCAPTURE_ID = (
    "e2b47734f1dcefab171b6ac8eefc3026ff9fac8adbe9d53832b0478084f2da98"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path, stem: str):
    spec = importlib.util.spec_from_file_location(stem + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old
    return module


def test_active_contract_manifest_and_service_have_one_identity(tmp_path: Path):
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    contract = json.loads((ROOT / CONTRACT).read_text())
    service_sha = sha(ROOT / SERVICE)
    assert manifest["schema_version"] == "mysql_xp_node02_successor_bundle_v24"
    assert manifest["active_executable_launch_contract"] == CONTRACT
    assert manifest["files"][SERVICE] == service_sha
    assert manifest["service_sha256"] == service_sha
    assert contract["adopted_service"] == {
        **contract["adopted_service"],
        "path": SERVICE,
        "sha256": service_sha,
    }

    generator = load_module(
        ROOT / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
        "xp_v24_materials",
    )
    result = generator.create_materials(ROOT, tmp_path / "materials", 10)
    plan = json.loads(Path(result["command_plan"]).read_text())
    assert plan["publication_validation"]["adopted_service_sha256"] == service_sha
    # The private files are intentionally neither opened nor copied by this test.
    assert Path(result["main_agent_token_file"]).is_file()


def test_embedded_postcapture_tool_is_self_contained_from_unrelated_cwd(
    tmp_path: Path,
):
    tool = ROOT / "postcapture_tool"
    manifest_path = tool / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    binding = json.loads((ROOT / "xp_postcapture_closure_binding_v1.json").read_text())
    assert manifest["bundle_id"] == POSTCAPTURE_ID
    assert binding["postcapture_tool_bundle_id"] == POSTCAPTURE_ID
    assert binding["postcapture_tool_manifest_sha256"] == sha(manifest_path)
    assert binding["relative_root"] == "postcapture_tool"
    actual = {
        path.relative_to(tool).as_posix(): sha(path)
        for path in tool.rglob("*")
        if path.is_file() and path != manifest_path
    }
    assert actual == manifest["payload_files"]

    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(tool / "shell/mysql_xp_postcapture_promotion_v1.py"),
            "verify-source",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS"
    assert result["data_plane_touched"] is False


def test_successor_is_immutable_and_zero_authority():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["remote_actions"] == 0
    for path in (ROOT, *ROOT.rglob("*")):
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0
