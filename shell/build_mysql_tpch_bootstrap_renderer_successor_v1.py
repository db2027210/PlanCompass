#!/usr/bin/env python3
"""Build a sealed controller bundle for the b235 TPC-H successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
TARGET_ID = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"
TARGET_MANIFEST_SHA256 = (
    "386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"
)
TARGET = (
    ROOT / "artifacts/mysql_tpch_deadline_shutdown_successor_v1" / TARGET_ID
)
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_bootstrap_renderer_successor_v1"
SOURCES = {
    "controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py":
        ROOT / "shell/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py",
    "controller/render_mysql_tpch_v23_recalibrated_bootstrap_spec_v3.py":
        ROOT / "shell/render_mysql_tpch_v23_recalibrated_bootstrap_spec_v3.py",
    "controller/render_mysql_tpch_v23_recalibrated_base_spec_v3.py":
        ROOT / "shell/render_mysql_tpch_v23_recalibrated_base_spec_v3.py",
    "launch/mysql_tpch_systemd_oneshot_materializer_v1.py":
        TARGET / "launch/mysql_tpch_systemd_oneshot_materializer_v1.py",
    "fixtures/node04_systemd_run_help_fixture_v1.json":
        TARGET / "fixtures/node04_systemd_run_help_fixture_v1.json",
}
BUILDER = "builder/build_mysql_tpch_bootstrap_renderer_successor_v1.py"
OFFLINE_TEST = "offline_tests/test_b235_bootstrap_controller_v1.py"
BINDING = "b235_bootstrap_target_binding_v1.json"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + relative)
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def verify_target() -> dict[str, Any]:
    manifest_path = TARGET / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != TARGET_ID
        or sha_file(manifest_path) != TARGET_MANIFEST_SHA256
        or sha_bytes(canonical(unsigned)) != TARGET_ID
        or relative_files(TARGET) != manifest.get("files")
        or manifest.get("schema_version")
        != "mysql_tpch_v23_recalibrated_successor_v3"
        or manifest.get("successor_generation")
        != "sql_native_completed_latency_contract_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or (manifest.get("component_hashes") or {}).get("collector")
        != "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"
        or manifest.get("shutdown_successor_generation")
        != "deadline_bounded_query_join_v1"
    ):
        raise BuildError("b235 target identity, closure, or authority differs")
    return manifest


def offline_test_payload() -> bytes:
    return b'''from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BUNDLE = Path(__file__).resolve().parents[1]
BINDING = json.loads((BUNDLE / "b235_bootstrap_target_binding_v1.json").read_text())
TARGET = BUNDLE / "target" / BINDING["target_bundle_id"]
RENDERER = BUNDLE / "controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
MATERIALIZER = BUNDLE / "launch/mysql_tpch_systemd_oneshot_materializer_v1.py"
FIXTURE = BUNDLE / "fixtures/node04_systemd_run_help_fixture_v1.json"
IMAGE_REF = "mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
ATTEMPT = "tpch-v2-offline-controller-b235d1ca"

def render(tmp_path, manifest=TARGET / "bundle_manifest.json"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    token = tmp_path / "token"
    token.write_text("T" * 64 + "\\n", encoding="ascii")
    token.chmod(0o600)
    output = tmp_path / "spec.json"
    command = [sys.executable, str(RENDERER), "--bundle-manifest", str(manifest),
               "--mysql-image-reference", IMAGE_REF, "--mysql-image-id", IMAGE_ID,
               "--client-image-reference", IMAGE_REF, "--client-image-id", IMAGE_ID,
               "--main-agent-token-file", str(token), "--attempt-token", ATTEMPT,
               "--valid-minutes", "25", "--output", str(output)]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(command, text=True, capture_output=True, check=False,
                          env=environment), output, token

def test_renderer_accepts_exact_b235_identity(tmp_path):
    completed, output, _ = render(tmp_path)
    assert completed.returncode == 0, completed.stderr
    spec = json.loads(output.read_text())
    assert spec["formal_scope"] == "mysql_only"
    assert spec["cross_engine_comparison_prohibited"] is True
    assert spec["bundle"]["bundle_id"] == BINDING["target_bundle_id"]
    assert spec["bundle"]["manifest_sha256"] == BINDING["target_manifest_sha256"]
    assert spec["qualification_only"] is True
    assert spec["formal_r0_authorized"] is False
    assert spec["formal_authorized"] is False
    assert spec["accepted_round_authorized"] is False

def test_materializer_emits_exact_nonblocking_oneshot(tmp_path):
    completed, spec_path, token = render(tmp_path)
    assert completed.returncode == 0, completed.stderr
    remote_root = "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729"
    remote_bundle = remote_root + "/bundles/" + BINDING["target_bundle_id"]
    remote_auth = remote_root + "/authorizations/" + ATTEMPT
    plan = tmp_path / "plan.json"
    command = [sys.executable, str(MATERIALIZER), "--spec", str(spec_path),
               "--bundle-path", remote_bundle,
               "--remote-spec", remote_auth + "/bootstrap_spec.json",
               "--remote-token-file", remote_auth + "/main_agent.token",
               "--remote-output", remote_root + "/attempts/" + ATTEMPT + "/bootstrap_evidence.json",
               "--help-fixture", str(FIXTURE), "--output", str(plan)]
    result = subprocess.run(command, text=True, capture_output=True, check=False,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert result.returncode == 0, result.stderr
    document = json.loads(plan.read_text())
    argv = document["argv"]
    assert document["status"] == "SEALED_PLAN_NOT_EXECUTED"
    assert "--service-type=oneshot" in argv and "--remain-after-exit" in argv
    assert "--wait" not in argv and "--collect" not in argv
    assert remote_bundle + "/mysql_tpch_distributed_v2_authority.py" in argv
    assert str(token) not in argv

def test_renderer_rejects_tampered_manifest_even_with_rebuilt_sidecar(tmp_path):
    copied = tmp_path / BINDING["target_bundle_id"]
    shutil.copytree(TARGET, copied)
    manifest = copied / "bundle_manifest.json"
    sidecar = copied / "bundle_manifest.json.sha256"
    manifest.chmod(0o600)
    sidecar.chmod(0o600)
    value = json.loads(manifest.read_text())
    value["formal_scope"] = "paired_cross_engine"
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\\n").encode()
    manifest.write_bytes(payload)
    sidecar.write_text(
        hashlib.sha256(payload).hexdigest() + "  bundle_manifest.json\\n")
    completed, _, _ = render(tmp_path / "render", manifest)
    assert completed.returncode == 2
    assert "NOT_READY" in completed.stderr

def test_controller_bundle_is_exact_and_read_only():
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); unsigned.pop("bundle_id")
    canonical = (json.dumps(unsigned, sort_keys=True, indent=2) + "\\n").encode()
    assert hashlib.sha256(canonical).hexdigest() == manifest["bundle_id"] == BUNDLE.name
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    for path in (BUNDLE, *BUNDLE.rglob("*")):
        assert not path.is_symlink()
        assert path.stat().st_mode & 0o222 == 0
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    target_manifest = verify_target()
    content: dict[str, bytes] = {
        relative: source.read_bytes() for relative, source in SOURCES.items()
    }
    content[BUILDER] = HERE.read_bytes()
    content[OFFLINE_TEST] = offline_test_payload()
    target_prefix = "target/" + TARGET_ID + "/"
    for path in sorted(TARGET.rglob("*")):
        if path.is_file():
            content[target_prefix + path.relative_to(TARGET).as_posix()] = path.read_bytes()
    content[BINDING] = canonical({
        "schema_version": "mysql_tpch_b235_bootstrap_target_binding_v1",
        "status": "PASS_OFFLINE_EXACT_BINDING_FRESH_BOOTSTRAP_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_MANIFEST_SHA256,
        "target_schema_version": target_manifest["schema_version"],
        "target_successor_generation": target_manifest["successor_generation"],
        "target_shutdown_successor_generation":
            target_manifest["shutdown_successor_generation"],
        "target_collector_sha256": target_manifest["component_hashes"]["collector"],
        "materializer_mode": "direct_host_systemd_transient_oneshot",
        "fresh_bootstrap_required": True,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    files = {relative: sha_bytes(payload) for relative, payload in sorted(content.items())}
    identity = {
        "schema_version": "mysql_tpch_bootstrap_renderer_successor_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_MANIFEST_SHA256,
        "target_schema_version": target_manifest["schema_version"],
        "target_successor_generation": target_manifest["successor_generation"],
        "target_shutdown_successor_generation":
            target_manifest["shutdown_successor_generation"],
        "target_collector_sha256": target_manifest["component_hashes"]["collector"],
        "renderer_sha256": files["controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"],
        "renderer_core_sha256": files["controller/render_mysql_tpch_v23_recalibrated_bootstrap_spec_v3.py"],
        "renderer_base_sha256": files["controller/render_mysql_tpch_v23_recalibrated_base_spec_v3.py"],
        "materializer_sha256": files["launch/mysql_tpch_systemd_oneshot_materializer_v1.py"],
        "self_contained_target_closure": True,
        "offline_regression_count": 4,
        "fresh_bootstrap_required": True,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".tpch-bootstrap-renderer-v1.", dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    unsigned = dict(manifest); unsigned.pop("bundle_id")
    if (
        relative_files(destination) != files
        or destination.name != bundle_id
        or sha_bytes(canonical(unsigned)) != bundle_id
    ):
        raise BuildError("published controller identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published controller is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_TPCH_BOOTSTRAP_RENDERER_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "target_bundle_id": TARGET_ID,
        "verified_files": len(files),
        "offline_regression_count": 4,
        "fresh_bootstrap_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, UnicodeError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
