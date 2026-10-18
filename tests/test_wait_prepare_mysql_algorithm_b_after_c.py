from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import wait_prepare_mysql_algorithm_b_after_c_v1 as waiter


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _launch_fixture(tmp_path: Path) -> Path:
    state = tmp_path / "formal_run/run_state.json"
    receipt = tmp_path / "formal_run/launch_receipt.json"
    result = tmp_path / "launch_result.json"
    _write(receipt, {
        "status": "STARTED",
        "execution_class": "FORMAL_MYSQL_ONLY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": True,
        "accepted_state_credit_prohibited": False,
        "active_scenes": waiter.SCENES,
        "run_state_path": str(state),
        "systemd_unit": "formal.service",
    })
    _write(result, {
        "status": "STARTED",
        "launch_receipt_path": str(receipt),
        "run_state_path": str(state),
        "systemd_unit": "formal.service",
    })
    return result


def test_validates_exact_formal_launch_receipt(tmp_path: Path) -> None:
    result = _launch_fixture(tmp_path)
    bound = waiter.validate_launch_result(result)
    assert bound is not None
    assert bound["run_state_path"] == tmp_path / "formal_run/run_state.json"


def test_rejects_nonformal_launch_receipt(tmp_path: Path) -> None:
    result = _launch_fixture(tmp_path)
    payload = json.loads(result.read_text(encoding="utf-8"))
    receipt = Path(payload["launch_receipt_path"])
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["formal_evidence_eligible"] = False
    _write(receipt, value)
    with pytest.raises(RuntimeError, match="authority differs"):
        waiter.validate_launch_result(result)


def test_prepare_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "formal_run"
    run_root.mkdir()
    output = tmp_path / "handoff_output"
    calls = {"checkpoint": 0, "release": 0}

    def build_checkpoint(path: Path) -> dict:
        assert path == run_root
        calls["checkpoint"] += 1
        return {"status": "PREPARED", "checkpoint": True}

    def verify_checkpoint(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def build_release(path: Path) -> dict:
        calls["release"] += 1
        return {"status": "PREPARED", "release_sha256": "a" * 64}

    def verify_release(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    monkeypatch.setattr(waiter.checkpointmod, "build_checkpoint", build_checkpoint)
    monkeypatch.setattr(waiter.checkpointmod, "verify_checkpoint", verify_checkpoint)
    monkeypatch.setattr(waiter.handoff, "build_release", build_release)
    monkeypatch.setattr(waiter.handoff, "verify_release", verify_release)
    first = waiter.prepare(run_root, output)
    second = waiter.prepare(run_root, output)
    assert first["checkpoint"] == second["checkpoint"]
    assert first["release"] == second["release"]
    assert calls == {"checkpoint": 1, "release": 1}
