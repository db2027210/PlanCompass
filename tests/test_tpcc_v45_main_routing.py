from __future__ import annotations

import json
import copy
from pathlib import Path
import sys

import pytest

from tools import run_tpcc_v45_autonomous_score_separation_loop as controller
from tools import run_tpcc_v45_plan_regime_score_separation_strict_serial as runner


PROJECT = Path(__file__).resolve().parents[1]
SELECTION = (
    PROJECT
    / "history/tpcc_v44_plan_regime_score_separation_package_20260803/top5.json"
)
BASELINE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "candidate_measurements/point_20260729_022349_1052396/summary.json"
)
LEDGER_NAME = "strict_top5_real_replay_round16_v45_ledger_v1.json"
LEDGER_CONTRACT = (
    "tpcc_round16_v45_plan_regime_score_separation_strict_serial_replay_v1"
)


def _copy_selection(tmp_path: Path) -> Path:
    target = tmp_path / "top5.json"
    target.write_text(SELECTION.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_controller_uses_v45_round16_paths() -> None:
    replay_root, ledger, events, summary = controller._paths(
        Path("/tmp/v45-routing/top5.json"), 1
    )
    assert replay_root.name == (
        "top5_real_replay_round16_v45_score_separation_diagnostic_02"
    )
    assert ledger.name == LEDGER_NAME
    assert events == replay_root / "events.jsonl"
    assert summary == replay_root / "summary.json"


def test_real_dynamic_main_rejects_round11_before_workload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _copy_selection(tmp_path)
    replay_root = tmp_path / "top5_real_replay_round11_must_fail"
    runtime = runner.load_runtime()
    workload_called = []
    runtime["run_workload"] = lambda *_args, **_kwargs: workload_called.append(True)
    monkeypatch.setattr(
        sys,
        "argv",
        ["v45-main", str(selection), str(replay_root), str(BASELINE)],
    )
    with pytest.raises(RuntimeError, match="top5_real_replay_round16"):
        runtime["main"]()
    assert workload_called == []
    assert not (tmp_path / LEDGER_NAME).exists()


def test_real_dynamic_main_accepts_v45_round16_and_uses_independent_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _copy_selection(tmp_path)
    replay_root = (
        tmp_path
        / "top5_real_replay_round16_v45_score_separation_diagnostic_02"
    )
    legacy_ledger = tmp_path / "strict_top5_real_replay_round16_ledger_v1.json"
    legacy_ledger.write_text('{"contract":"legacy-must-not-be-opened"}', encoding="utf-8")
    runtime = runner.load_runtime()
    workload_called = []

    def forbidden_workload(*_args: object, **_kwargs: object) -> None:
        workload_called.append(True)
        raise AssertionError("workload must not run in routing test")

    runtime["MAX_REAL_WORKLOAD_INVOCATIONS"] = 0
    runtime["restore_database"] = lambda: "offline-routing-test-noop"
    runtime["apply_params"] = lambda params: dict(params)
    runtime["run_workload"] = forbidden_workload
    monkeypatch.setattr(
        sys,
        "argv",
        ["v45-main", str(selection), str(replay_root), str(BASELINE)],
    )
    assert runtime["main"]() == 2
    assert workload_called == []
    ledger_path = tmp_path / LEDGER_NAME
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["contract"] == LEDGER_CONTRACT
    assert ledger["replay_root"] == str(replay_root)
    assert ledger["real_workload_invocation_count"] == 0
    assert ledger["maximum_real_workload_invocations"] == 0
    assert json.loads(legacy_ledger.read_text(encoding="utf-8")) == {
        "contract": "legacy-must-not-be-opened"
    }
    events = (replay_root / "events.jsonl").read_text(encoding="utf-8")
    assert "tpcc_gradient_real_replay_started" not in events


def test_v45_accepts_only_state_neutral_promoted_trusted3() -> None:
    analysis = json.loads(SELECTION.read_text(encoding="utf-8"))
    candidates = copy.deepcopy(analysis["top5"])
    promoted_total = 0
    for rank, candidate in enumerate(candidates, 1):
        trusted = rank <= 3
        candidate.update(
            {
                "selection_role": "trusted" if trusted else "uncertain_probe",
                "trusted": trusted,
                "uncertain_probe": not trusted,
                "exact_plan_regime_validated": trusted,
                "trusted_eligible_v43": trusted,
            }
        )
        candidate["plan_regime_calibration"]["trusted_eligible"] = trusted
        provenance = candidate["source_provenance"]
        promoted_rows = []
        if trusted:
            for batch in (1, 2):
                promoted_rows.append(
                    {
                        "calibration_trusted_eligible": True,
                        "calibration_trust_certificate": {
                            "contract": runner.v44.PROBE_PROMOTION_CONTRACT,
                            "exact_point_key": candidate["exact_point_key"],
                            "validated_plan": candidate[
                                "workload_plan_fingerprint"
                            ],
                            "independent_clean_probe_batch_count": 2,
                            "independent_batch_ids": ["batch-1", "batch-2"],
                            "source_selection_role_preserved": "uncertain_probe",
                            "formal_state_update_eligible": False,
                            "gradient_update_eligible": False,
                            "incumbent_update_eligible": False,
                            "range_update_eligible": False,
                            "this_batch_id": f"batch-{batch}",
                        },
                    }
                )
        provenance["trusted_observations"] = promoted_rows
        provenance[
            "v44_acquisition_probe_rows_used_to_establish_trust"
        ] = len(promoted_rows)
        promoted_total += len(promoted_rows)
    contract = analysis["v44_build_contract"]
    contract.update(
        {
            "mode": "trusted3_probe2",
            "unknown_plan_probe_only": True,
            "acquisition_probe_rows_used_to_establish_trust": promoted_total,
            "clean_evidence_routes_automatically": True,
            "roles": [
                "trusted",
                "trusted",
                "trusted",
                "uncertain_probe",
                "uncertain_probe",
            ],
            "fingerprint_ids": [
                candidate["three_level_fingerprint"]["id"]
                for candidate in candidates
            ],
        }
    )
    analysis["top5"] = candidates
    runner._validate_v44(analysis, candidates)
    tampered = copy.deepcopy(analysis)
    tampered["top5"][0]["source_provenance"]["trusted_observations"][0][
        "calibration_trust_certificate"
    ]["formal_state_update_eligible"] = True
    with pytest.raises(RuntimeError, match="promoted probe certificate"):
        runner._validate_v44(tampered, tampered["top5"])
