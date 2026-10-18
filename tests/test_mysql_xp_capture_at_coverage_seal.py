from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from shell.build_mysql_xp_immutable_bundle_v8 import launch_contract_source
from shell.seal_mysql_xp_capture_at_coverage import build_seal


BUNDLE_ID = "b" * 64
AT_NAMES = ("AT1", "AT2", "AT3", "AT4", "AT5", "AT6")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any, *, sidecar: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if sidecar:
        path.with_name(path.name + ".sha256").write_text(
            f"{_sha256(path)}  {path.name}\n", encoding="ascii"
        )


def _fixture(tmp_path: Path) -> dict[str, Path]:
    bundle = tmp_path / BUNDLE_ID
    validator = bundle / "capture/offline_registry/xp_strict_registry.py"
    validator.parent.mkdir(parents=True)
    validator.write_text(
        "import json\n"
        "def validate_xp_capture_trace(trace, trace_manifest, "
        "runtime_authority, capture_authorization):\n"
        "    return json.loads(trace.read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "mysql_xp_v8_immutable_runtime_v1",
        "bundle_id": BUNDLE_ID,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_r0_authorized": False,
        "required_nonempty_subsets": 127,
        "fixed_replay_count": 5,
        "payload_files": {
            "capture/offline_registry/xp_strict_registry.py":
                _sha256(validator),
        },
    }
    _write_json(bundle / "manifest.json", manifest)

    spec = {
        "schema_version": "mysql_xp_fresh_local_capture_spec_v1",
        "runtime_bundle_id": BUNDLE_ID,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "capture_only": True,
        "outer_execution_authorized": False,
        "frozen_resource_envelope": {
            "io_device": "8:0",
            "io_read_bps": 1205862400,
            "io_write_bps": 1205862400,
        },
        "frozen_workload": {
            "workload": {
                "capture_schedule":
                    "deterministic_finite_ap13_at6_fixed_seed_binding_capture",
                "qps_latency_resource_results": "prohibited",
            },
        },
    }
    capture_spec = tmp_path / "capture_spec.json"
    _write_json(capture_spec, spec, sidecar=True)

    validation = {
        "capture_authorized": True,
        "execution_authorized": False,
        "reasons": [],
        "run_id": "xp-capture-test",
        "event_count": 6,
        "events": [
            {
                "event_type": "commit",
                "outcome": "success",
                "transaction_type": name,
            }
            for name in AT_NAMES
        ],
    }
    trace = tmp_path / "trace.json"
    _write_json(trace, validation)
    result = {
        "bundle": bundle,
        "trace": trace,
        "capture_spec": capture_spec,
    }
    for name in ("trace_manifest", "runtime_authority", "capture_authorization"):
        path = tmp_path / f"{name}.json"
        _write_json(path, {"fixture": name})
        result[name] = path
    return result


def _build(paths: dict[str, Path]) -> dict[str, Any]:
    return build_seal(
        bundle=paths["bundle"],
        trace=paths["trace"],
        trace_manifest=paths["trace_manifest"],
        runtime_authority=paths["runtime_authority"],
        capture_authorization=paths["capture_authorization"],
        capture_spec=paths["capture_spec"],
    )


def _load_launch_contract(tmp_path: Path):
    path = tmp_path / "launch_contract.py"
    path.write_text(launch_contract_source(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("xp_launch_contract_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_seal_is_r0_only_and_bundle_bound(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    seal = _build(paths)
    assert seal["schema_version"] == "mysql_xp_capture_at_coverage_seal_v1"
    assert seal["bundle_id"] == BUNDLE_ID
    assert seal["at_coverage"] == {name: True for name in AT_NAMES}
    assert seal["formal_r0_launch_authorized"] is True
    assert seal["outer_execution_authorized"] is False
    assert seal["algorithm_c_execution_authorized"] is False
    assert seal["accepted_round_authorized"] is False
    assert seal["capture_io_is_not_r0"] is True
    assert seal["qps_resource_input_prohibited"] is True
    assert seal["sources"]["capture_spec"]["schema_version"] == (
        "mysql_xp_fresh_local_capture_spec_v1"
    )


def test_build_seal_rejects_incomplete_at_coverage(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    validation = json.loads(paths["trace"].read_text(encoding="utf-8"))
    validation["events"] = validation["events"][:-1]
    _write_json(paths["trace"], validation)
    with pytest.raises(RuntimeError, match="AT1-AT6 coverage is incomplete"):
        _build(paths)


@pytest.mark.parametrize(
    ("field_path", "replacement"),
    [
        (("runtime_bundle_id",), "wrong-bundle"),
        (("outer_execution_authorized",), True),
        (
            ("frozen_workload", "workload", "qps_latency_resource_results"),
            "allowed",
        ),
        (("frozen_resource_envelope", "io_read_bps"), 29360128),
    ],
)
def test_build_seal_rejects_capture_spec_drift(
    tmp_path: Path,
    field_path: tuple[str, ...],
    replacement: Any,
) -> None:
    paths = _fixture(tmp_path)
    capture_spec = json.loads(paths["capture_spec"].read_text(encoding="utf-8"))
    target = capture_spec
    for name in field_path[:-1]:
        target = target[name]
    target[field_path[-1]] = replacement
    _write_json(paths["capture_spec"], capture_spec, sidecar=True)
    with pytest.raises(RuntimeError, match="capture spec|IO envelope"):
        _build(paths)


@pytest.mark.parametrize(
    ("key", "replacement"),
    [
        ("schema_version", "unknown"),
        ("bundle_id", "wrong-bundle"),
        ("formal_r0_launch_authorized", False),
        ("outer_execution_authorized", True),
        ("algorithm_c_execution_authorized", True),
        ("accepted_round_authorized", True),
        ("capture_io_is_not_r0", False),
        ("qps_resource_input_prohibited", False),
    ],
)
def test_formal_launch_contract_rejects_authority_drift(
    tmp_path: Path, key: str, replacement: Any
) -> None:
    paths = _fixture(tmp_path)
    seal = _build(paths)
    seal[key] = replacement
    seal_path = tmp_path / "at_seal.json"
    _write_json(seal_path, seal, sidecar=True)
    contract = _load_launch_contract(tmp_path)
    contract.BUNDLE = paths["bundle"]
    manifest = json.loads(
        (paths["bundle"] / "manifest.json").read_text(encoding="utf-8")
    )
    with pytest.raises(RuntimeError, match="R0-only authority"):
        contract.validate_formal_r0_seal(seal_path, manifest)


def test_formal_launch_contract_accepts_exact_r0_only_seal(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    seal_path = tmp_path / "at_seal.json"
    _write_json(seal_path, _build(paths), sidecar=True)
    contract = _load_launch_contract(tmp_path)
    contract.BUNDLE = paths["bundle"]
    manifest = json.loads(
        (paths["bundle"] / "manifest.json").read_text(encoding="utf-8")
    )
    validated = contract.validate_formal_r0_seal(seal_path, manifest)
    assert validated["formal_r0_launch_authorized"] is True


def test_formal_launch_contract_rejects_capture_spec_sidecar_drift(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    seal_path = tmp_path / "at_seal.json"
    _write_json(seal_path, _build(paths), sidecar=True)
    paths["capture_spec"].with_name(
        paths["capture_spec"].name + ".sha256"
    ).write_text("0" * 64 + "  capture_spec.json\n", encoding="ascii")
    contract = _load_launch_contract(tmp_path)
    contract.BUNDLE = paths["bundle"]
    manifest = json.loads(
        (paths["bundle"] / "manifest.json").read_text(encoding="utf-8")
    )
    with pytest.raises(RuntimeError, match="capture spec hash/schema"):
        contract.validate_formal_r0_seal(seal_path, manifest)
