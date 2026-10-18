from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import audit_tpcc_v36_postrun_events as durable
from tools import run_tpcc_v45_plan_regime_score_separation_strict_serial as strict_runner
from tools import tpcc_v46_zero_call_postrun_absorption as v46


PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "history/tpcc_v45_plan_regime_score_separation_package_20260803"
SELECTION = PACKAGE / "top5.json"
LEDGER = PACKAGE / "strict_top5_real_replay_round16_v45_ledger_v1.json"
REPLAY = PACKAGE / "top5_real_replay_round16_v45_score_separation_diagnostic_02"
EVENTS = REPLAY / "events.jsonl"
SUMMARY = REPLAY / "summary.json"
FORMAL = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"


def _arguments(output_root: Path) -> dict:
    return {
        "selection_path": SELECTION,
        "selection_sha256": v46._sha(SELECTION),
        "ledger_path": LEDGER,
        "ledger_sha256": v46._sha(LEDGER),
        "events_path": EVENTS,
        "events_sha256": v46._sha(EVENTS),
        "summary_path": SUMMARY,
        "summary_sha256": v46._sha(SUMMARY),
        "formal_state_path": FORMAL,
        "formal_state_sha256": v46._sha(FORMAL),
        "output_root": output_root,
    }


def test_probe5_durable_audit_has_no_trusted_division_by_zero() -> None:
    result = durable.audit(
        SELECTION,
        LEDGER,
        EVENTS,
        SUMMARY,
        FORMAL,
        v46._sha(FORMAL),
    )
    assert result["accounting"]["physical_calls"] == 5
    assert result["accounting"]["valid_replays"] == 5
    assert result["accounting"]["technical_failures"] == 0
    assert result["quality"]["trusted_ranks"] == []
    assert result["quality"]["trusted_score_mae"] is None
    assert result["quality"]["trusted_resource_mae_pp"] == {
        "cpu": None,
        "io": None,
        "memory": None,
    }
    assert result["formal_entry_eligible"] is False


def test_v46_absorbs_existing_probe5_and_builds_fresh_3t2p(
    tmp_path: Path,
) -> None:
    formal_before = v46._sha(FORMAL)
    output_root = tmp_path / "v46"
    result = v46.absorb(**_arguments(output_root))
    assert result["workload_calls_added"] == 0
    assert result["formal_state_mutated"] is False
    assert result["roles"] == [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]
    assert v46._sha(FORMAL) == formal_before

    successor = json.loads(
        (output_root / "next_top5_trusted3_probe2.json").read_text(
            encoding="utf-8"
        )
    )
    strict_runner._validate_v44(successor, successor["top5"])
    assert successor["state_mutation_contract"] == v46.STATE_MUTATION_NONE
    assert [row["selection_role"] for row in successor["top5"]] == result["roles"]
    assert len({row["three_level_fingerprint"]["id"] for row in successor["top5"]}) == 5
    assert all(
        v46.REQUIRED_CANDIDATE_FIELDS.issubset(row) for row in successor["top5"]
    )
    absorption = successor["v46_zero_call_absorption"]
    assert absorption["source_physical_calls"] == 5
    assert absorption["source_valid_replays"] == 5
    assert absorption["source_technical_failures"] == 0
    assert absorption["workload_calls_added"] == 0
    assert absorption["formal_start_authorized"] is False
    gate_pin = successor["v43_build_contract"]["sealed_v42_authority"]["gate"]
    assert gate_pin == {
        "path": str((output_root / "probe5_postrun_gate.json").resolve()),
        "sha256": v46._sha(output_root / "probe5_postrun_gate.json"),
    }


def test_v46_sha_mismatch_fails_before_output(tmp_path: Path) -> None:
    output_root = tmp_path / "must_not_exist"
    arguments = _arguments(output_root)
    arguments["events_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="sealed SHA mismatch: events"):
        v46.absorb(**arguments)
    assert not output_root.exists()


def test_v46_existing_output_root_fails_closed(tmp_path: Path) -> None:
    output_root = tmp_path / "existing"
    output_root.mkdir()
    with pytest.raises(FileExistsError, match="output root already exists"):
        v46.absorb(**_arguments(output_root))
