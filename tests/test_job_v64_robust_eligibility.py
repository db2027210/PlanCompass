from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import job_fresh10_target10_autonomous_v64 as v64  # noqa: E402


PARAMETERS = (
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "cpu_tuple_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
    "random_page_cost",
    "seq_page_cost",
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def params(rank: int) -> dict[str, float]:
    return {name: float(rank + index / 100.0) for index, name in enumerate(PARAMETERS)}


def install_priors(tmp_path: Path, score_pairs: list[list[float]]) -> dict:
    selections = []
    summaries = []
    for observation in range(2):
        selection_path = tmp_path / f"prior_{observation + 1}/top5.json"
        summary_path = tmp_path / f"prior_{observation + 1}/summary.json"
        selection = {
            "top5": [
                {
                    "candidate_params": params(rank),
                    "selection_role": "trusted" if rank <= 3 else "probe",
                }
                for rank in range(1, 6)
            ]
        }
        summary = {
            "successful": [
                {
                    "rank": rank,
                    "candidate_binding": {"candidate_params": params(rank)},
                    "actual_score": score_pairs[rank - 1][observation],
                    "technical_failure": False,
                }
                for rank in range(1, 6)
            ]
        }
        write_json(selection_path, selection)
        write_json(summary_path, summary)
        selections.append(selection_path)
        summaries.append(summary_path)

    top5 = []
    roles = ["trusted", "trusted", "trusted", "probe", "probe"]
    for rank, role in enumerate(roles, 1):
        candidate = {
            "rank": rank,
            "selection_role": role,
            "candidate_params": params(rank),
            "incumbent_update_eligible": role == "trusted",
            "used_for_inner_incumbent_update": role == "trusted",
            "used_for_gradient": role == "trusted",
            "three_level_fingerprint": {
                "id": f"three-level-sha256:{rank:064x}",
                "valid": True,
            },
        }
        if role == "trusted":
            candidate["robust_acceptance_policy"] = {
                "contract": v64.PRIOR_POLICY_CONTRACT,
                "candidate_params_sha256": v64.canonical_sha256(params(rank)),
                "prior_observations": [
                    {
                        "ordinal": observation + 1,
                        "selection_path": str(selections[observation].resolve()),
                        "selection_sha256": v64.implementation.v49.sha256_file(
                            selections[observation]
                        ),
                        "summary_path": str(summaries[observation].resolve()),
                        "summary_sha256": v64.implementation.v49.sha256_file(
                            summaries[observation]
                        ),
                        "actual_score": score_pairs[rank - 1][observation],
                    }
                    for observation in range(2)
                ],
            }
        top5.append(candidate)
    return {"diagnostic_only": False, "top5": top5}


def test_median_mathematical_upper_bound() -> None:
    assert v64.maximum_attainable_three_score_median([4.1, 4.2]) == 4.2
    assert v64.maximum_attainable_three_score_median([4.7, 4.3]) == 4.7
    with pytest.raises(v64.implementation.v49.FailClosed):
        v64.maximum_attainable_three_score_median([4.1])


def test_only_trusted_with_possible_robust_improvement_are_eligible(tmp_path) -> None:
    selection = install_priors(
        tmp_path,
        [
            [4.55, 4.53],
            [4.54, 4.53],
            [4.60, 4.59],
            [9.0, 9.0],
            [9.0, 9.0],
        ],
    )
    result, audit = v64.build_eligibility_selection(
        selection,
        incumbent_score=4.542077,
        formal_state_sha256="a" * 64,
    )
    assert result["formal_replay_eligible_ranks"] == [1, 3]
    assert result["formal_zero_call_skip_ranks"] == [2, 4, 5]
    assert audit["maximum_physical_calls"] == 2
    assert audit["rows"][1]["reason"] == "median_improvement_impossible"
    assert audit["rows"][3]["reason"] == "probe_role"
    assert all(
        result["top5"][rank - 1]["formal_replay_eligible"] is False
        for rank in (2, 4, 5)
    )


def test_all_ineligible_is_zero_call_reexploration_not_permanent_stop(tmp_path) -> None:
    selection = install_priors(
        tmp_path,
        [[4.50, 4.51], [4.52, 4.53], [4.54, 4.53], [9, 9], [9, 9]],
    )
    _result, audit = v64.build_eligibility_selection(
        selection,
        incumbent_score=4.542077,
        formal_state_sha256="b" * 64,
    )
    assert audit["status"] == "zero_call_reexplore"
    assert audit["eligible_ranks"] == []
    assert audit["zero_call_skipped_ranks"] == [1, 2, 3, 4, 5]
    assert audit["new_workload_calls"] == 0
    assert audit["no_eligible_next_action"] == "automatic_new_fingerprint_exploration"


def test_prior_sha_tamper_fails_closed(tmp_path) -> None:
    selection = install_priors(tmp_path, [[5, 5]] * 5)
    policy = selection["top5"][0]["robust_acceptance_policy"]
    policy["prior_observations"][0]["summary_sha256"] = "0" * 64
    with pytest.raises(v64.implementation.v49.FailClosed, match="SHA"):
        v64.build_eligibility_selection(
            selection,
            incumbent_score=4.542077,
            formal_state_sha256="c" * 64,
        )


def test_no_eligible_prepare_raises_liveness_signal_after_zero_call_audit(
    tmp_path, monkeypatch
) -> None:
    formal = tmp_path / "formal"
    (formal / "package").mkdir(parents=True)
    monkeypatch.setattr(v64, "_PREPARE_V63", lambda *_args, **_kwargs: formal)
    monkeypatch.setattr(
        v64,
        "seal_formal_eligibility",
        lambda *_args, **_kwargs: {
            "eligible_ranks": [],
            "new_workload_calls": 0,
            "no_eligible_next_action": "automatic_new_fingerprint_exploration",
        },
    )
    with pytest.raises(v64.implementation.v49.NoveltyCollision, match="automatic"):
        v64.prepare_formal_v64(None, None, None, None, None, "d" * 64)


def test_v12_runner_filters_probes_and_ineligible_trusted_before_workload(
    tmp_path, monkeypatch
) -> None:
    selection_path = tmp_path / "selection.json"
    state_path = tmp_path / "state.json"
    summary_path = tmp_path / "replay/summary.json"
    roles = ["trusted", "trusted", "trusted", "probe", "probe"]
    top5 = []
    for rank, role in enumerate(roles, 1):
        eligible = rank == 1
        prior_scores = [4.55, 4.53] if rank == 1 else (
            [4.54, 4.53] if role == "trusted" else []
        )
        top5.append(
            {
                "selection_role": role,
                "formal_replay_eligible": eligible,
                "incumbent_update_eligible": eligible,
                "used_for_inner_incumbent_update": eligible,
                "robust_formal_eligibility": {
                    "contract": v64.ELIGIBILITY_CONTRACT,
                    "source_rank": rank,
                    "selection_role": role,
                    "eligible": eligible,
                    "reason": "eligible_trusted" if eligible else (
                        "probe_role" if role == "probe" else "median_improvement_impossible"
                    ),
                    "prior_actual_scores": prior_scores,
                    "maximum_attainable_three_score_median": (
                        max(prior_scores) if prior_scores else None
                    ),
                    "incumbent_threshold_before_rank": (
                        4.542077 if rank == 1 else 4.55
                    ),
                    "qps_used_for_inner_scoring": False,
                },
            }
        )
    write_json(state_path, {"incumbent_score": 4.542077})
    state_sha = v64.implementation.v49.sha256_file(state_path)
    write_json(
        selection_path,
        {
            "top5": top5,
            "robust_formal_eligibility_contract": v64.ELIGIBILITY_CONTRACT,
            "robust_formal_incumbent_score": 4.542077,
            "robust_formal_state_sha256": state_sha,
            "formal_replay_eligible_ranks": [1],
            "qps_used_for_inner_scoring": False,
        },
    )
    captured = {}

    fake_base = SimpleNamespace()
    fake_base.load_json = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    fake_base.validate_selection = lambda value: value["top5"]
    fake_base.preflight = lambda *_args: {"candidate_count": 5}
    fake_robust = SimpleNamespace(
        _validate_formal_selection=lambda _path: None,
        validate_policy=lambda candidate: candidate[
            "robust_formal_eligibility"
        ]["prior_actual_scores"],
    )
    fake_v10 = SimpleNamespace(base=fake_base, robust_v7=fake_robust)

    def fake_run(args):
        selected = fake_base.validate_selection(
            fake_base.load_json(args.selection)
        )
        captured["count"] = len(selected)
        write_json(summary_path, {})
        return {
            "summary_path": str(summary_path),
            "physical_invocation_count": len(selected),
        }

    fake_v11 = ModuleType(
        "run_job_round8_strict_serial_robust_v11_gradient_state_guard"
    )
    fake_v11.v10 = fake_v10
    fake_v11.run = fake_run
    monkeypatch.setitem(
        sys.modules,
        "run_job_round8_strict_serial_robust_v11_gradient_state_guard",
        fake_v11,
    )
    runner_path = TOOLS / "run_job_round8_strict_serial_robust_v12_eligibility.py"
    spec = importlib.util.spec_from_file_location("job_v12_filter_test", runner_path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    args = SimpleNamespace(selection=selection_path, state=state_path)
    result = runner.run(args)
    assert captured["count"] == 1
    assert result["physical_invocation_count"] == 1
    assert result["eligible_source_ranks"] == [1]
    assert result["zero_call_skipped_source_ranks"] == [2, 3, 4, 5]


def test_completed_v63_repeat_no_longer_requires_a_zero_call_boundary(
    tmp_path, monkeypatch
) -> None:
    v49 = v64.implementation.v49
    phase = tmp_path / "cycle_002/repeat_01"
    write_json(
        phase / "counters_before.json",
        {"global": 429, "formal_replay": 211, "accepted": 1},
    )
    write_json(phase / "v50_batch_sequence.json", {"sequence": 1})
    write_json(phase / "replay/summary.json", {"physical_invocation_count": 2})
    monkeypatch.setattr(v49, "ROOT", tmp_path)
    monkeypatch.setattr(
        v49,
        "state_pin",
        lambda: (
            "a" * 64,
            {"global_ordinal": 429, "formal_replay_count": 211, "accepted_count": 1},
        ),
    )
    counters = {
        v49.GLOBAL_COUNTER: 431,
        v49.FORMAL_REPLAY_COUNTER: 211,
        v49.ACCEPTED_COUNTER: 1,
    }
    monkeypatch.setattr(v49, "counter", lambda path: counters[path])
    v64.validate_pending_diagnostic_counters_v64(
        v64.implementation.HISTORICAL_INITIAL_FORMAL,
        2,
    )
    (phase / "replay/summary.json").unlink()
    with pytest.raises(v49.FailClosed, match="no sealed summary"):
        v64.validate_pending_diagnostic_counters_v64(
            v64.implementation.HISTORICAL_INITIAL_FORMAL,
            2,
        )


def test_v64_launcher_and_controller_pin_the_eligibility_runner() -> None:
    controller = (TOOLS / "job_fresh10_target10_autonomous_v64.py").read_text(
        encoding="utf-8"
    )
    launcher = (TOOLS / "launch_job_target10_autonomous_v64.sh").read_text(
        encoding="utf-8"
    )
    runner_sha = v64.implementation.v49.sha256_file(
        TOOLS / "run_job_round8_strict_serial_robust_v12_eligibility.py"
    )
    assert runner_sha in controller
    assert runner_sha in launcher
    assert "automatic_new_fingerprint_exploration" in controller
    assert "--preflight-only" in launcher
    assert "ssh " not in launcher
