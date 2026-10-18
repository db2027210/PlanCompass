from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "shell/mysql_tpcc_node04_adopted_service_v1.py"
PHASE_PATH = ROOT / "shell/mysql_tpcc_materialize_phase_command_v1.py"
BUNDLE = (
    ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v2/"
    "9ead1b6ade955d42baaedcddd3a51f3781730ffcf25e8f8e8223c0c28afe03b5"
)
CONFIG_MATERIALIZER_PATH = ROOT / "shell/mysql_tpcc_config_materialize_v1.py"
CONFIG_BUNDLE = (
    ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v10/"
    "2185b4b072121e9ab8d6bb6ed4ee0da0e9ebde955ca6a92e5a20f416287597fe"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def service_module():
    return load_module("tpcc_adopted_service_test", SERVICE_PATH)


def phase_module():
    return load_module("tpcc_phase_materializer_test", PHASE_PATH)


def fake_service(bundle_id: str, attempt: str) -> dict:
    service = service_module()
    value = {
        "schema_version": "mysql_tpcc_node04_adopted_service_authority_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle_id,
        "attempt_id": attempt,
        "handoff_token_sha256": service.sha_bytes(attempt.encode()),
        "host": "node04",
        "database": {
            "container_id": "d" * 64,
            "name": "db",
            "pid": 4321,
            "process_start_ticks": 987654,
            "datadir": service.RUNTIME_ROOT + f"/{attempt}/datadir",
            "device": service.DEVICE,
            "device_major_minor": service.DEVICE_MAJOR_MINOR,
            "cpuset_cpus": service.DB_CPUS,
            "cpuset_mems": service.DB_MEMS,
            "cpu_quota_cores": 4.0,
            "memory_bytes": service.MEMORY,
            "memory_swap_bytes": service.MEMORY,
            "pids_max": service.DB_PIDS,
            "mysql_port": service.PORT,
            "image_id": "sha256:" + "a" * 64,
        },
        "client": {
            "container_id": "c" * 64,
            "name": "client",
            "pid": 5432,
            "process_start_ticks": 987655,
            "cpuset_cpus": service.CLIENT_CPUS,
            "cpuset_mems": service.CLIENT_MEMS,
            "cpu_quota_cores": 16.0,
            "memory_bytes": service.MEMORY,
            "memory_swap_bytes": service.MEMORY,
            "pids_max": service.CLIENT_PIDS,
            "root_filesystem_read_only": True,
            "network_mode": "host",
            "target_mysql_device_write_prohibited": True,
            "image_id": "sha256:" + "a" * 64,
        },
        "measurement_bridge": {
            "host_path": f"/run/user/1000/tpcc/{attempt}",
            "container_path": "/measurement",
            "filesystem": "tmpfs",
        },
        "runtime_tree_readback": {
            "jdk": {
                "tree_content_sha256": service.JDK_TREE_SHA256,
                "mounted_read_only": True,
            },
            "python": {
                "tree_content_sha256": service.PYTHON_TREE_SHA256,
                "mounted_read_only": True,
            },
        },
        "same_live_database_required": True,
        "container_recreated": False,
        "database_restarted": False,
        "datadir_replaced": False,
        "service_ready": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    value["live_readback_sha256"] = service.sha_bytes(service.canonical(value))
    return value


def fake_prefault(authority: dict) -> dict:
    client = authority["client"]
    return {
        "schema_version": "mysql_tpcc_client_target_device_prefault_v1",
        "status": "PASS_PREFAULT_ZERO_IO_BASELINE",
        "bundle_id": authority["bundle_id"],
        "attempt_id": authority["attempt_id"],
        "handoff_token_sha256": authority["handoff_token_sha256"],
        "client_container_id": client["container_id"],
        "client_pid": client["pid"],
        "client_process_start_ticks": client["process_start_ticks"],
        "target_device_major_minor": "259:2",
        "quiet_window_delta": {
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        },
        "real_window_all_target_device_io_must_be_zero": True,
        "accepted_round_authorized": False,
    }


def test_plan_freezes_db_client_and_never_authorizes_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = service_module()
    monkeypatch.setattr(service, "_verify_attempt_config", lambda *unused: {"status": "PASS_CONFIG_MATERIALIZED"})
    attempt = "tpcc-attempt-0123456789abcdef"
    plan = service.build_plan(
        bundle=BUNDLE,
        image_id="sha256:" + "a" * 64,
        config_dir=tmp_path / "config",
        datadir=Path(service.RUNTIME_ROOT) / attempt / "datadir",
        measurement_bridge=tmp_path / "measurement",
        attempt_id=attempt,
        handoff_token=attempt,
    )
    db = plan["database"]["create_argv"]
    client = plan["client"]["create_argv"]
    assert ["--cpuset-cpus", service.DB_CPUS] == db[
        db.index("--cpuset-cpus"):db.index("--cpuset-cpus") + 2
    ]
    assert f"{service.DEVICE}:{service.IO_BPS}" in db
    assert "--read-only" in client
    assert ["--cap-drop", "ALL"] == client[
        client.index("--cap-drop"):client.index("--cap-drop") + 2
    ]
    assert "no-new-privileges" in client
    assert plan["measurement_bridge"]["filesystem"] == "tmpfs"
    assert plan["container_replacement_prohibited"] is True
    assert plan["accepted_round_authorized"] is False
    assert any(service.JDK_SOURCE in item for item in client)
    assert any(service.PYTHON_SOURCE in item for item in client)
    writable_mounts = [
        item for item in [*db, *client]
        if item.startswith("type=bind,")
        and item.endswith(("dst=/var/lib/mysql", "dst=/measurement"))
    ]
    assert writable_mounts == [
        f"type=bind,src={plan['database']['datadir']},dst=/var/lib/mysql",
        f"type=bind,src={plan['measurement_bridge']['host_path']},dst=/measurement",
    ]
    assert all(",rw" not in item for item in [*db, *client])
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert 'mount.get("RW") is not True' in source


def test_plan_fails_closed_when_attempt_config_is_missing(tmp_path: Path) -> None:
    service = service_module()
    attempt = "tpcc-attempt-0123456789abcdef"
    with pytest.raises(service.ServiceError, match="config directory is absent"):
        service.build_plan(
            bundle=BUNDLE,
            image_id="sha256:" + "a" * 64,
            config_dir=tmp_path / "missing-config",
            datadir=Path(service.RUNTIME_ROOT) / attempt / "datadir",
            measurement_bridge=tmp_path / "measurement",
            attempt_id=attempt,
            handoff_token=attempt,
        )


def test_sealed_config_materializer_creates_exact_regular_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    materializer = load_module("tpcc_config_materializer_test", CONFIG_MATERIALIZER_PATH)
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(materializer, "RUNTIME_ROOT", str(runtime_root))
    attempt = "tpcc-attempt-0123456789abcdef"
    attempt_root = runtime_root / attempt
    attempt_root.mkdir(parents=True, mode=0o700)
    attempt_root.chmod(0o700)
    password_file = attempt_root / "mysql-password"
    password_file.write_text("test-only-password\n", encoding="utf-8")
    password_file.chmod(0o600)
    value = materializer.materialize(
        CONFIG_BUNDLE, attempt, attempt_root / "config", password_file,
        attempt_root / "config_materialization.json",
    )
    assert value["status"] == "PASS_CONFIG_MATERIALIZED"
    assert value["qps_resource_input_prohibited"] is True
    assert value["password_source"] == "external_0600_file_not_logged_or_hashed"
    assert set(value["files"]) == {"tpcc.xml", "mysql.cnf"}
    for name in value["files"]:
        candidate = attempt_root / "config" / name
        assert candidate.is_file() and not candidate.is_symlink()
        assert candidate.stat().st_mode & 0o777 == 0o600
    assert "test-only-password" not in (attempt_root / "config_materialization.json").read_text()


def test_plan_rejects_token_or_datadir_drift(tmp_path: Path) -> None:
    service = service_module()
    attempt = "tpcc-attempt-0123456789abcdef"
    arguments = dict(
        bundle=BUNDLE,
        image_id="sha256:" + "a" * 64,
        config_dir=tmp_path / "config",
        measurement_bridge=tmp_path / "measurement",
        attempt_id=attempt,
    )
    with pytest.raises(service.ServiceError, match="identical"):
        service.build_plan(
            **arguments,
            datadir=Path(service.RUNTIME_ROOT) / attempt / "datadir",
            handoff_token="different-token-0123456789",
        )
    with pytest.raises(service.ServiceError, match="datadir must"):
        service.build_plan(
            **arguments,
            datadir=tmp_path / "wrong",
            handoff_token=attempt,
        )


def test_physical_copy_audit_rejects_shared_or_overlapping_extents() -> None:
    service = service_module()
    passed = service._verify_disjoint_extent_maps(
        {"source": [(0, 100, 0)]},
        {"target": [(200, 300, 0)]},
    )
    assert passed["source_target_physical_overlap_count"] == 0
    with pytest.raises(service.ServiceError, match="shared extent"):
        service._verify_disjoint_extent_maps(
            {"source": [(0, 100, service.FIEMAP_EXTENT_SHARED)]},
            {"target": [(200, 300, 0)]},
        )
    with pytest.raises(service.ServiceError, match="phys.*overlap"):
        service._verify_disjoint_extent_maps(
            {"source": [(0, 100, 0)]},
            {"target": [(50, 150, 0)]},
        )
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert '"cp", "-a", "--"' in source
    assert '"--reflink=' not in source
    assert "FIEMAP_EXTENT_SHARED" in source


def test_materializer_binds_dbworkload_and_gates_formal10() -> None:
    phase = phase_module()
    attempt = "tpcc-attempt-0123456789abcdef"
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    authority = fake_service(manifest["bundle_id"], attempt)
    qualification = phase.materialize(
        bundle=BUNDLE,
        service=authority,
        token=attempt,
        phase="qualification",
        config_in_container="/opt/mysql-tpcc-config/tpcc.xml",
        mysql_defaults_in_container="/opt/mysql-tpcc-config/mysql.cnf",
        phase_authority=None,
        readiness_audit=None,
        prefault_authority=fake_prefault(authority),
    )
    assert qualification["actual_main_class"] == "com.oltpbenchmark.DBWorkload"
    assert "com.oltpbenchmark.DBWorkload" in qualification["actual_java_argv"]
    assert qualification["accepted_round_authorized"] is False
    assert qualification["formal_authorized"] is False
    assert qualification["db_identity"]["container_id"] == "d" * 64
    with pytest.raises(phase.PhaseError, match="sealed phase authority"):
        phase.materialize(
            bundle=BUNDLE,
            service=authority,
            token=attempt,
            phase="formal10_launch",
            config_in_container="/opt/mysql-tpcc-config/tpcc.xml",
            mysql_defaults_in_container="/opt/mysql-tpcc-config/mysql.cnf",
            phase_authority=None,
            readiness_audit=None,
            prefault_authority=fake_prefault(authority),
        )


def test_handoff_ledger_is_ordered_and_same_database() -> None:
    phase = phase_module()
    attempt = "tpcc-attempt-0123456789abcdef"
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    authority = fake_service(manifest["bundle_id"], attempt)
    command = {
        "phase": "qualification",
        "attempt_id": attempt,
        "bundle_id": manifest["bundle_id"],
        "command_spec_sha256": "a" * 64,
    }
    ledger = phase.record_phase(
        Path("/tmp/not-written.json"),
        command=command,
        before=authority,
        after=authority,
        returncode=0,
        target_device_io_delta={
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        },
    )
    assert ledger["status"] == "INCOMPLETE"
    assert list(ledger["phases"]) == ["qualification"]
    assert ledger["accepted_round_authorized"] is False
    drift = json.loads(json.dumps(authority))
    drift["database"]["pid"] += 1
    with pytest.raises(phase.PhaseError, match="identity changed"):
        phase.record_phase(
            Path("/tmp/not-written.json"),
            command=command,
            before=authority,
            after=drift,
            returncode=0,
            target_device_io_delta={
                "rbytes": 0, "wbytes": 0, "rios": 0,
                "wios": 0, "dbytes": 0, "dios": 0,
            },
        )
