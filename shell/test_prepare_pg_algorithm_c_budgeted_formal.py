#!/usr/bin/env python3
"""Unit tests for baseline-independent adaptive Algorithm C preparation."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from algorithm_c_budget_policy import CONTRACT
from shell import launch_pg_algorithm_c_budgeted_formal as launcher
from shell import prepare_pg_algorithm_c_budgeted_formal as preparer


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build_sources(config_root: Path, release_root: Path) -> None:
    for index, scene in enumerate(preparer.SCENES):
        scene_dir = config_root / scene
        config = {
            "algorithm_c": {
                "qps_noise_gate": {"enabled": True, "static_floor_pct": 5.0},
                "controller_client_cpuset": str(120 + index),
                "experience_table_path": str(scene_dir / "mixed_model.json"),
                "offline_resource_training_audit": {
                    "training_sources": ["algorithm_a", "algorithm_b", "algorithm_c"],
                },
                "runtime_candidate_prior_firewall": {
                    "algorithm_a_runtime_candidate_inputs_allowed": False,
                    "algorithm_b_runtime_candidate_inputs_allowed": False,
                },
                "budgeted_trial": {
                    "contract": CONTRACT,
                    "budget": {"max_search_replays": 10, "max_wall_s": 100.0},
                },
            },
            "binary_plan_tuning": {
                "output_dir": str(scene_dir),
                "binary_search": {
                    "outer_acceptance": {"qps_threshold_mode": "max_static_and_noise"},
                    "nested_loop": {"outer_acceptance": {
                        "qps_threshold_mode": "max_static_and_noise", "min_db_qps_gain_pct": 5.0,
                        "qps_reference_mode": "canonical_baseline",
                    }},
                },
                "validation": {
                    "docker_workers": {
                        "workers": [
                            {"container": f"{scene}_w1"},
                            {"container": f"{scene}_w2"},
                        ]
                    }
                },
            },
        }
        write_json(scene_dir / "optimizer_config.json", config)
        artifact = {
            "formal_admission": {"ready": True},
            "fit": {
                "source_audit": {
                    "source_counts": {
                        f"/disk1/resource_adaptive/pg_algorithm_c_campaign/{scene}/calibration.jsonl": {
                            "candidate": 10,
                        }
                    }
                }
            },
        }
        artifact["artifact_sha256"] = preparer.canonical_sha256(artifact)
        write_json(release_root / scene / "scope" / "table" / "experience_table.json", artifact)


def build_paper_abc_models(model_root: Path) -> None:
    for scene in preparer.SCENES:
        artifact = {
            "formal_admission": {"ready": True},
            "qps_used_for_inner_scoring": False,
            "training_boundary": {
                "allowed_training_source_algorithms": [
                    "algorithm_a", "algorithm_b", "algorithm_c",
                ],
                "observed_training_source_counts": {
                    "algorithm_a": 100,
                    "algorithm_b": 10,
                    "algorithm_c": 20,
                },
                "accept_reject_labels_used_for_resource_model": False,
                "historical_parameter_identity_used_as_model_feature": False,
                "historical_plan_fingerprint_used_as_model_feature": False,
                "qps_present_in_resource_training_rows": False,
                "qps_used_as_model_feature_or_target": False,
                "source_algorithm_used_as_model_feature": False,
            },
            "holdout": {
                "holdout_source_algorithm": "algorithm_c",
                "independent_from_fit": True,
                "qps_used_for_selection_prediction": False,
            },
            "runtime_candidate_prior_firewall": {
                "qps_used_as_model_feature_or_target": False,
                "qps_used_for_candidate_ranking": False,
                "qps_used_for_candidate_score": False,
                "qps_used_for_gradient": False,
                "source_algorithm_used_as_model_feature": False,
                "training_source_accept_reject_used_as_runtime_candidates": False,
                "training_source_parameters_used_as_runtime_candidates": False,
                "training_source_plan_fingerprints_used_as_runtime_candidates": False,
                "training_source_rankings_used_as_runtime_candidates": False,
            },
        }
        artifact["artifact_sha256"] = preparer.canonical_sha256(artifact)
        write_json(model_root / scene / preparer.PAPER_ABC_MODEL_FILENAME, artifact)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="algorithm-c-adaptive-prep-") as temporary:
        base = Path(temporary)
        source = base / "source"
        release = base / "release"
        build_sources(source, release)
        formal = base / "formal"
        manifest = preparer.prepare(source, formal, "adaptive_frontier_v1", release)
        assert manifest["campaign_role"] == preparer.FORMAL_ROLE
        assert manifest["algorithm_revision"] == "adaptive_frontier_v1"
        assert manifest["peer_overlap_mode"] == "hard_stop"
        assert len(manifest["algorithm_design_sha256"]) == 64
        assert set(manifest["formal_orchestration_sources"]) == {
            "shell/launch_pg_algorithm_c_budgeted_formal.py",
            "shell/launch_pg_algorithm_c_budgeted_trial.py",
            "shell/run_pg_algorithm_c_budgeted_support.py",
            "shell/run_pg_algorithm_c_cohort_support_supervisor.py",
        }
        assert set(manifest["source_sha256"]) == set(
            preparer.FORMAL_RUNTIME_SOURCES
        )
        assert manifest["external_baseline_runtime_inputs"] == []
        assert manifest[
            "historical_algorithm_c_baseline_resource_anchor_imported"
        ] is True
        assert manifest[
            "historical_algorithm_c_baseline_resource_anchor_source_sha256"
        ] == (
            preparer.sha256(preparer.ROOT / preparer.PAPER_C_RESOURCE_ANCHOR_SOURCE)
        )
        assert set(manifest["scenes"]) == set(preparer.SCENES)
        preparer.verify_no_external_baseline_information(manifest, "manifest")
        for scene in preparer.SCENES:
            config = json.loads(
                (formal / scene / "optimizer_config.json").read_text(encoding="utf-8")
            )
            policy = config["algorithm_c"]["budgeted_trial"]
            assert policy["execution_role"] == preparer.FORMAL_ROLE
            assert "budget" not in policy
            assert policy["final_validation_enabled"] is False
            assert policy["finalist_replays"] == policy["post_baseline_replays"] == 0
            assert isinstance(policy["adaptive_search"], dict)
            assert policy["baseline_repeatability_gate"] == {
                "enabled": True,
                "mode": "hard_stop",
                "maximum_worker_repeat_ratio": 1.10,
                "historical_qps_used": False,
            }
            assert policy["startup_warmup"]["enabled"] is False
            assert policy["baseline_repetition_policy"]["repetitions_per_worker"] == 2
            assert policy["baseline_repetition_policy"]["burn_in_repetitions"] == 0
            anchor = policy["inner_resource_anchor"]
            assert anchor["contract"] == preparer.INNER_RESOURCE_ANCHOR_CONTRACT
            assert anchor["qps_used"] is False
            assert anchor["old_candidate_parameters_imported"] is False
            assert anchor["old_accept_reject_labels_imported"] is False
            assert anchor["physical_validity_uses_actual_resources"] is True
            assert set(anchor["utilization_pct"]) == {"cpu", "memory", "io"}
            assert anchor == manifest["scenes"][scene]["inner_resource_anchor"]
            assert manifest["scenes"][scene]["config_sha256"] == (
                preparer.sha256(formal / scene / "optimizer_config.json")
            )
            assert config["binary_plan_tuning"]["binary_search"]["outer_acceptance"][
                "qps_threshold_mode"
            ] == "static_floor"
            assert config["algorithm_c"]["qps_noise_gate"]["enabled"] is False
            assert config["algorithm_c"]["qps_noise_gate"]["static_floor_pct"] == 0.0
            outer = config["binary_plan_tuning"]["binary_search"]["nested_loop"]["outer_acceptance"]
            assert outer["qps_reference_mode"] == "last_accepted"
            assert outer["min_db_qps_gain_pct"] == 0.0
            assert "operational_safety_max_replays" not in policy["adaptive_search"]
            assert "operational_safety_max_active_wall_s" not in policy["adaptive_search"]
            assert config["algorithm_c"]["offline_resource_training_audit"][
                "training_source_algorithm"
            ] == "algorithm_c"
            assert config["algorithm_c"]["controller_client_cpuset"] == (
                preparer.CONTROLLER_CPUSETS[scene]
            )
            preparer.verify_no_external_baseline_information(config, f"config:{scene}")

        configs = {
            scene: json.loads(
                (formal / scene / "optimizer_config.json").read_text(encoding="utf-8")
            )
            for scene in preparer.SCENES
        }
        base_receipt = {
            "passed": True,
            "errors": [],
            "trial_only_not_publication_comparison": True,
            "worker_names": [f"worker-{index}" for index in range(8)],
        }
        with mock.patch.object(
            launcher,
            "preflight",
            return_value=(manifest, configs, base_receipt),
        ):
            _, _, receipt = launcher.formal_preflight(formal)
        assert receipt["passed"] is True
        assert receipt["external_baseline_information_used"] is False
        assert receipt[
            "historical_algorithm_c_baseline_resource_anchor_used"
        ] is True
        assert receipt["historical_algorithm_c_qps_or_parameters_used"] is False
        assert receipt["normal_fixed_search_or_wall_budget"] is False

        portfolio_root = base / "portfolio"
        portfolio_manifest = preparer.prepare(source, portfolio_root, "portfolio_v1", release, plan_portfolio=True)
        assert portfolio_manifest["inner_search_contract"] == preparer.PORTFOLIO_CONTRACT
        for scene in preparer.SCENES:
            portfolio_config = json.loads((portfolio_root / scene / "optimizer_config.json").read_text())
            assert portfolio_config["algorithm_c"]["inner_search"]["enabled"] is True
            expected_binary = dict(configs[scene]["binary_plan_tuning"])
            expected_binary["output_dir"] = str(portfolio_root / scene)
            assert portfolio_config["binary_plan_tuning"] == expected_binary
            assert portfolio_config["algorithm_c"]["budgeted_trial"]["adaptive_search"] == (
                configs[scene]["algorithm_c"]["budgeted_trial"]["adaptive_search"]
            )

        paper_models = base / "paper-models"
        build_paper_abc_models(paper_models)
        paper_root = base / "paper-abc"
        paper_manifest = preparer.prepare(
            source,
            paper_root,
            "paper_abc_portfolio_v1",
            release,
            plan_portfolio=True,
            resource_model_mode=preparer.RESOURCE_MODEL_PAPER_ABC,
            paper_abc_model_root=paper_models,
            startup_warmup=True,
            baseline_repetitions_per_worker=3,
            baseline_repeatability_mode="advisory",
            peer_overlap_mode="record_only",
        )
        assert paper_manifest["resource_model_mode"] == preparer.RESOURCE_MODEL_PAPER_ABC
        assert paper_manifest["peer_overlap_mode"] == "record_only"
        assert set(paper_manifest["external_baseline_training_sources"]) == {
            "algorithm_a", "algorithm_b",
        }
        preparer.verify_paper_abc_manifest_boundary(paper_manifest, "paper_manifest")
        paper_configs = {}
        for scene in preparer.SCENES:
            paper_config = json.loads(
                (paper_root / scene / "optimizer_config.json").read_text(encoding="utf-8")
            )
            paper_configs[scene] = paper_config
            assert paper_config["algorithm_c"]["budgeted_trial"]["baseline_repeatability_gate"]["mode"] == "advisory"
            assert paper_config["algorithm_c"]["budgeted_trial"]["startup_warmup"] == {
                "contract": "algorithm_c_full_replay_startup_warmup_v1",
                "enabled": True,
                "repetitions_per_worker": 1,
                "qps_used_for_baseline_or_candidate_scoring": False,
            }
            assert paper_config["algorithm_c"]["budgeted_trial"]["baseline_repetition_policy"] == {
                "contract": "algorithm_c_fixed_predeclared_baseline_burn_in_v1",
                "repetitions_per_worker": 3,
                "burn_in_repetitions": 1,
                "selection_based_on_observed_qps": False,
            }
            audit = paper_config["algorithm_c"]["offline_resource_training_audit"]
            assert set(audit["training_sources"]) == {
                "algorithm_a", "algorithm_b", "algorithm_c",
            }
            assert audit["qps_used"] is False
            runtime_model = paper_config["algorithm_c"]["runtime_resource_model_policy"]
            assert runtime_model["resource_model_mode"] == preparer.RESOURCE_MODEL_PAPER_ABC
            assert runtime_model["external_baseline_runtime_inputs_allowed"] is False
            preparer.verify_paper_abc_config_boundary(
                paper_config, f"paper_config:{scene}"
            )
        with mock.patch.object(
            launcher,
            "preflight",
            return_value=(paper_manifest, paper_configs, base_receipt),
        ):
            _, _, paper_receipt = launcher.formal_preflight(paper_root)
        assert paper_receipt["passed"] is True, paper_receipt
        assert paper_receipt["external_baseline_information_used"] is True
        assert paper_receipt["external_baseline_resource_training_used"] is True
        assert paper_receipt[
            "external_baseline_runtime_or_candidate_information_used"
        ] is False
        assert paper_receipt["external_baseline_qps_or_parameters_used"] is False

        bad_artifact = {
            "formal_admission": {"ready": True},
            "fit": {
                "source_audit": {
                    "source_counts": {"/tmp/algorithm_b/replays.jsonl": {"candidate": 5}}
                }
            },
        }
        bad_artifact["artifact_sha256"] = preparer.canonical_sha256(bad_artifact)
        write_json(release / preparer.SCENES[0] / "new" / "bad" / "experience_table.json", bad_artifact)
        try:
            preparer.select_latest_c_only_model(release, preparer.SCENES[0])
        except RuntimeError:
            pass
        else:
            # An older valid model may be selected, but the invalid model itself
            # must never pass the source audit.
            try:
                preparer.c_only_model_source_audit(bad_artifact)
            except RuntimeError:
                pass
            else:
                raise AssertionError("external baseline training source was accepted")

    print("PASS: baseline-independent adaptive Algorithm C preparation", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
