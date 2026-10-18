from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "shell/build_mysql_tpcc_distributed_successor_v3.py"
MATERIALIZER = ROOT / "shell/mysql_tpcc_materialize_phase_command_v2.py"


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def service() -> dict:
    return {
        "handoff_token_sha256": "token-hash",
        "database": {
            "container_id": "d" * 64, "pid": 1234,
            "process_start_ticks": 5678,
            "datadir": "/mnt/mysql_tpcc_nvme3n1/runtime/datadir",
            "device_major_minor": "259:2", "cpuset_cpus": "21-24,57-60",
            "cpuset_mems": "1", "mysql_port": 65106,
        },
        "client": {
            "container_id": "c" * 64, "pid": 2222,
            "cpuset_cpus": "8-15,44-51", "cpuset_mems": "0",
        },
        "live_readback_sha256": "a" * 64,
    }


def command(phase: str) -> dict:
    return {
        "phase": phase, "attempt_id": "tpcc-v3-attempt-0001",
        "bundle_id": "b" * 64, "command_spec_sha256": "e" * 64,
    }


def test_v3_successor_is_deterministic_immutable_and_has_detached_closure(tmp_path: Path) -> None:
    builder = module(BUILDER, "tpcc_v3_builder")
    first = builder.build(tmp_path / "one")
    second = builder.build(tmp_path / "two")
    assert first.name == second.name
    manifest = builder.verify_published(first)
    assert manifest["schema_version"] == "mysql_tpcc_typed_distributed_successor_bundle_v3"
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert builder.BASE_ID in manifest["invalidated_bundle_ids"]
    for relative in (
        "authority/mysql_tpcc_materialize_phase_command_v2.py",
        "authority/tpcc_v3_formal10_readiness_verifier.py",
        "authority/build_mysql_tpcc_v3_phase_authority.py",
    ):
        assert relative in manifest["files"]
        assert not (first / relative).stat().st_mode & 0o222


def test_v2_materializer_uses_observed_epoch_not_json_key_order(tmp_path: Path) -> None:
    materializer = module(MATERIALIZER, "tpcc_v3_materializer")
    before = service()
    after = service()
    ledger_path = tmp_path / "ledger.json"
    delta = {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0, "dbytes": 0, "dios": 0}
    ledger = None
    for phase in materializer.PHASES[:-1]:
        ledger = materializer.record_phase(
            ledger_path, command=command(phase), before=before, after=after,
            returncode=0, target_device_io_delta=delta,
        )
        materializer.atomic_json(ledger_path, ledger)
    assert ledger is not None and ledger["status"] == "PRELAUNCH_READY"
    # Atomic JSON sorts keys alphabetically, but resuming must still identify
    # qualification -> capture -> R0 -> debug by observed_epoch_ms.
    loaded = json.loads(ledger_path.read_text())
    assert list(loaded["phases"]) != list(materializer.PHASES[:-1])
    final = materializer.record_phase(
        ledger_path, command=command("formal10_launch"), before=before,
        after=after, returncode=0, target_device_io_delta=delta,
    )
    assert final["status"] == "PASS"


def test_v2_materializer_refuses_a_v1_key_order_ledger(tmp_path: Path) -> None:
    materializer = module(MATERIALIZER, "tpcc_v3_materializer_refusal")
    ledger = {
        "schema_version": "mysql_tpcc_same_live_container_formal10_handoff_v1",
        "phases": {},
    }
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    try:
        materializer.record_phase(
            ledger_path, command=command("qualification"), before=service(),
            after=service(), returncode=0,
            target_device_io_delta={"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0, "dbytes": 0, "dios": 0},
        )
    except materializer.PhaseError as exc:
        assert "v1 key-order ledger" in str(exc)
    else:
        raise AssertionError("v1 ledger resume must fail closed")
