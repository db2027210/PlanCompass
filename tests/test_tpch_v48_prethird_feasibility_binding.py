from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
CONTROLLER = PROJECT / "tools/tpch_v48_prethird_feasibility_binding.py"
REMOTE_FIXTURE = Path(
    "/tmp/tpch_v48_remote_feasibility_20260803/"
    "prethird_acceptance_feasibility.json"
)
REMOTE_FIXTURE_SHA = (
    "ec43a865b38cc9d329c631f0ad2244d1764813069ba8091a3034137c1ee57eb4"
)
FORMAL_SHA = "fc262cead29c831ca04c09a7c4d1b2ebf1481636f82e692cd0dcf3b2d3539bc8"


def import_controller():
    spec = importlib.util.spec_from_file_location("tpch_v48_feasibility", CONTROLLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def bound_remote_fixture(v48, tmp_path: Path):
    cycle = tmp_path / "cycle_004"
    repeat = cycle / "exact_repeat_top5.a.json"
    repeat_run = cycle / "diagnostic_exact_repeat"
    write_json(repeat, {"top5": [{"rank": rank} for rank in range(1, 6)]})
    write_json(repeat_run / "diagnostic_summary.json", {"successful": 5})
    payload = json.loads(REMOTE_FIXTURE.read_text(encoding="utf-8"))
    payload["selection_sha256"] = v48.sha256(repeat)
    payload["repeat_summary_sha256"] = v48.sha256(
        repeat_run / "diagnostic_summary.json"
    )
    write_json(cycle / "prethird_acceptance_feasibility.json", payload)
    return cycle, repeat, repeat_run


def test_remote_prethird_fixture_sha_schema_and_semantics(tmp_path):
    v48 = import_controller()
    assert REMOTE_FIXTURE.stat().st_size == 1932
    assert v48.sha256(REMOTE_FIXTURE) == REMOTE_FIXTURE_SHA
    source = v48.load(REMOTE_FIXTURE)
    assert source["contract"] == "tpch_bounded_four_observation_median_prethird_v1"
    assert source["complete_two_observation_binding"] is True
    assert source["resume_state_sha256"] == FORMAL_SHA
    assert source["feasible_ranks"] == [1, 2, 3, 4, 5]

    cycle, repeat, repeat_run = bound_remote_fixture(v48, tmp_path)
    audit = v48.validate_postclean5_feasibility(
        cycle,
        expected_formal_sha=FORMAL_SHA,
        repeat_selection=repeat,
        repeat_run=repeat_run,
        expected_primary_sha256=v48.sha256(
            cycle / "prethird_acceptance_feasibility.json"
        ),
    )
    assert audit["contract"] == v48.PRETHIRD_FEASIBILITY_CONTRACT
    assert audit["source"] == "live_prethird"
    assert audit["both_aliases_present"] is False
    assert audit["new_workload_calls"] == 0

    with pytest.raises(RuntimeError, match="feasibility SHA mismatch"):
        v48.validate_postclean5_feasibility(
            cycle,
            expected_formal_sha=FORMAL_SHA,
            repeat_selection=repeat,
            repeat_run=repeat_run,
            expected_primary_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract", "tampered"),
        ("complete_two_observation_binding", False),
        ("resume_state_sha256", "0" * 64),
        ("selection_sha256", "0" * 64),
        ("repeat_summary_sha256", "0" * 64),
        ("thresholds_relaxed", True),
    ],
)
def test_live_prethird_binding_tamper_fails_closed(tmp_path, field, value):
    v48 = import_controller()
    cycle, repeat, repeat_run = bound_remote_fixture(v48, tmp_path)
    path = cycle / "prethird_acceptance_feasibility.json"
    payload = v48.load(path)
    payload[field] = value
    write_json(path, payload)
    with pytest.raises(RuntimeError, match="prethird feasibility binding is invalid"):
        v48.validate_postclean5_feasibility(
            cycle,
            expected_formal_sha=FORMAL_SHA,
            repeat_selection=repeat,
            repeat_run=repeat_run,
        )


def test_live_prethird_candidate_formula_tamper_fails_closed(tmp_path):
    v48 = import_controller()
    cycle, repeat, repeat_run = bound_remote_fixture(v48, tmp_path)
    path = cycle / "prethird_acceptance_feasibility.json"
    payload = v48.load(path)
    payload["candidate_bounds"][0]["maximum_four_observation_median"] += 0.1
    write_json(path, payload)
    with pytest.raises(RuntimeError, match="prethird feasibility binding is invalid"):
        v48.validate_postclean5_feasibility(
            cycle,
            expected_formal_sha=FORMAL_SHA,
            repeat_selection=repeat,
            repeat_run=repeat_run,
        )


def test_legacy_name_remains_compatible_and_alias_mismatch_fails(tmp_path):
    v48 = import_controller()
    cycle = tmp_path / "cycle_004"
    repeat = cycle / "repeat.json"
    repeat_run = cycle / "repeat_run"
    write_json(repeat, {"top5": []})
    write_json(repeat_run / "diagnostic_summary.json", {"successful": []})
    legacy = cycle / "acceptance_feasibility.json"
    payload = {
        "passed": True,
        "physical_workload_calls": 0,
        "formal_state_mutated": False,
        "manual_candidate_injection": False,
        "thresholds_relaxed": False,
    }
    write_json(legacy, payload)
    audit = v48.validate_postclean5_feasibility(
        cycle,
        expected_formal_sha=FORMAL_SHA,
        repeat_selection=repeat,
        repeat_run=repeat_run,
    )
    assert audit["source"] == "legacy_postreseal_fixture"

    primary = cycle / "prethird_acceptance_feasibility.json"
    write_json(primary, {**payload, "contract": "tampered"})
    with pytest.raises(RuntimeError, match="aliases disagree"):
        v48.validate_postclean5_feasibility(
            cycle,
            expected_formal_sha=FORMAL_SHA,
            repeat_selection=repeat,
            repeat_run=repeat_run,
        )


def test_identical_aliases_are_allowed_but_primary_semantics_stay_strict(tmp_path):
    v48 = import_controller()
    cycle, repeat, repeat_run = bound_remote_fixture(v48, tmp_path)
    primary = cycle / "prethird_acceptance_feasibility.json"
    legacy = cycle / "acceptance_feasibility.json"
    legacy.write_bytes(primary.read_bytes())
    audit = v48.validate_postclean5_feasibility(
        cycle,
        expected_formal_sha=FORMAL_SHA,
        repeat_selection=repeat,
        repeat_run=repeat_run,
    )
    assert audit["source"] == "live_prethird"
    assert audit["both_aliases_present"] is True
    assert audit["aliases_byte_identical"] is True
