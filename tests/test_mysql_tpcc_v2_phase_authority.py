from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/build_mysql_tpcc_v2_phase_authority.py"
BASE = (
    ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v2/"
    "ebbdfd33e9e98f45c689e0b72524ea3961428a3de252a24653e269ba093e488d"
)
READINESS_TEST = ROOT / "tests/test_mysql_tpcc_v2_formal10_readiness.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def authority_module():
    return load_module(MODULE_PATH, "tpcc_phase_authority")


def readiness_test_module():
    return load_module(READINESS_TEST, "tpcc_readiness_test_helpers")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def phase(name: str, index: int, attempt: str, bundle_id: str) -> dict:
    return {
        "status": "PASS",
        "attempt_id": attempt,
        "bundle_id": bundle_id,
        "db_container_id": "d" * 64,
        "db_pid": 4321,
        "db_process_start_ticks": 987654,
        "db_datadir": (
            "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
            "runtime_algorithm_c_successor_20260729/attempt/datadir"
        ),
        "db_device_major_minor": "259:2",
        "db_cpuset_cpus": "21-24,57-60",
        "db_cpuset_mems": "1",
        "mysql_port": 65106,
        "client_container_id": "c" * 64,
        "client_pid": 5432,
        "client_cpuset_cpus": "8-15,44-51",
        "client_cpuset_mems": "0",
        "live_readback_sha256": hashlib.sha256(name.encode()).hexdigest(),
        "observed_epoch_ms": 1000 + index,
        "client_target_device_io_delta": {
            "rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0,
            "dbytes": 0, "dios": 0,
        },
        "client_target_device_io_zero_verified": True,
    }


def evidence(
    tmp_path: Path, names: tuple[str, ...], status: str = "INCOMPLETE"
) -> tuple[dict[str, Path], str]:
    test_mod = readiness_test_module()
    verifier = test_mod.module()
    attempt = "tpcc-v2-real-attempt-0001"
    tmp_path.mkdir(parents=True)
    registry, _ = test_mod.production_registry(verifier, tmp_path / "registry", attempt)
    token = tmp_path / "token.txt"
    token.write_text(attempt + "\n", encoding="utf-8")
    datadir = (
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
        "runtime_algorithm_c_successor_20260729/attempt/datadir"
    )
    service = {
        "schema_version": "mysql_tpcc_node04_adopted_service_authority_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": BASE.name,
        "attempt_id": attempt,
        "handoff_token_sha256": hashlib.sha256(attempt.encode()).hexdigest(),
        "service_ready": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "database": {
            "container_id": "d" * 64, "pid": 4321,
            "process_start_ticks": 987654, "datadir": datadir,
            "device_major_minor": "259:2", "cpuset_cpus": "21-24,57-60",
            "cpuset_mems": "1", "mysql_port": 65106,
            "image_id": "sha256:" + "a" * 64,
        },
        "client": {
            "container_id": "c" * 64, "pid": 5432,
            "process_start_ticks": 999999, "cpuset_cpus": "8-15,44-51",
            "cpuset_mems": "0", "root_filesystem_read_only": True,
            "target_mysql_device_write_prohibited": True,
            "image_id": "sha256:" + "a" * 64,
        },
        "measurement_bridge": {"filesystem": "tmpfs"},
        "runtime_tree_readback": {
            "jdk": {"tree_content_sha256": "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c", "mounted_read_only": True},
            "python": {"tree_content_sha256": "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89", "mounted_read_only": True},
        },
    }
    service_path = tmp_path / "service.json"
    write_json(service_path, service)
    prefault = {
        "schema_version": "mysql_tpcc_client_target_device_prefault_v1",
        "status": "PASS_PREFAULT_ZERO_IO_BASELINE",
        "bundle_id": BASE.name, "attempt_id": attempt,
        "handoff_token_sha256": service["handoff_token_sha256"],
        "client_container_id": "c" * 64, "client_pid": 5432,
        "client_process_start_ticks": 999999,
        "target_device_major_minor": "259:2",
        "quiet_window_delta": {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0, "dbytes": 0, "dios": 0},
        "real_window_all_target_device_io_must_be_zero": True,
        "accepted_round_authorized": False,
    }
    prefault_path = tmp_path / "prefault.json"
    write_json(prefault_path, prefault)
    handoff = {
        "schema_version": "mysql_tpcc_same_live_container_formal10_handoff_v1",
        "status": status, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "attempt_id": attempt,
        "bundle_id": BASE.name, "db_container_id": "d" * 64,
        "db_pid": 4321, "db_process_start_ticks": 987654,
        "db_datadir": datadir, "container_recreated": False,
        "datadir_replaced": False, "database_restarted": False,
        "phases": {name: phase(name, index, attempt, BASE.name) for index, name in enumerate(names)},
    }
    handoff_path = tmp_path / "handoff.json"
    write_json(handoff_path, handoff)
    return {
        "registry": registry, "service": service_path, "prefault": prefault_path,
        "handoff": handoff_path, "token": token,
    }, attempt


def seal(module, root: Path, phase_key: str, items: dict[str, Path], prior: Path | None = None) -> Path:
    return module.seal(
        phase_key=phase_key, base_bundle=BASE, registry=items["registry"],
        service=items["service"], prefault=items["prefault"],
        handoff=items["handoff"], token_file=items["token"],
        prior_authority=prior, output_root=root,
    )


def test_phase_authorities_are_sequential_content_addressed_and_non_counting(tmp_path: Path) -> None:
    module = authority_module()
    fresh_items, _ = evidence(tmp_path / "fresh", ("qualification", "typed_capture"))
    fresh = seal(module, tmp_path / "out", "fresh-r0", fresh_items)
    fresh_manifest = module.verify(fresh)
    assert fresh_manifest["phase_authority"]["schema_version"] == "mysql_tpcc_fresh_r0_authority_v1"
    assert fresh_manifest["phase_authority"]["accepted_round_authorized"] is False

    debug_items, _ = evidence(tmp_path / "debug", ("qualification", "typed_capture", "fresh_r0"))
    debug = seal(module, tmp_path / "out", "debug", debug_items, fresh / "phase_authority.json")
    debug_manifest = module.verify(debug)
    assert debug_manifest["phase_authority"]["prior_authority_sha256"] == module.sha256(fresh / "phase_authority.json")

    formal_items, _ = evidence(
        tmp_path / "formal", ("qualification", "typed_capture", "fresh_r0", "debug"),
        status="PRELAUNCH_READY",
    )
    formal = seal(module, tmp_path / "out", "formal10", formal_items, debug / "phase_authority.json")
    formal_manifest = module.verify(formal)
    assert formal_manifest["phase_authority"]["formal_launch_authorized"] is True
    assert formal_manifest["phase_authority"]["accepted_round_authorized"] is False


def test_rejects_missing_real_phase_handoff_and_bad_prior_authority(tmp_path: Path) -> None:
    module = authority_module()
    items, _ = evidence(tmp_path / "bad", ("qualification", "typed_capture"))
    with pytest.raises(module.AuthorityError, match="phase set"):
        seal(module, tmp_path / "out", "debug", items, tmp_path / "missing.json")

    items, _ = evidence(tmp_path / "bad2", ("qualification", "typed_capture", "fresh_r0"))
    fake = tmp_path / "fake.json"
    write_json(fake, {"phase_authority": {"schema_version": "mysql_tpcc_fresh_r0_authority_v1", "status": "PASS"}})
    with pytest.raises(module.AuthorityError, match="prior authority"):
        seal(module, tmp_path / "out", "debug", items, fake)


def test_capture_only_base_cannot_be_edited_into_promotion(tmp_path: Path) -> None:
    module = authority_module()
    copied = tmp_path / "base-copy"
    copied.mkdir()
    (copied / "bundle_manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises((module.AuthorityError, FileNotFoundError)):
        module.verify_base(copied)
