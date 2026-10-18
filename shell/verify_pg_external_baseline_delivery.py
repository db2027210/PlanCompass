#!/usr/bin/env python3
"""Verify the complete PostgreSQL external-baseline research delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTERNAL_ROOT = Path(
    "/disk1/resource_adaptive/pg_external_four_scene_baselines_20260825/"
    "20260825_gptuner_aqetuner_four_r1"
)
DEFAULT_XPIO_ROOT = Path(
    "/disk1/resource_adaptive/pg_external_xpio_correct_domain_20260826/"
    "20260826_xpio_40cpu24g_r1"
)
DEFAULT_R4_ROOT = Path(
    "/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/"
    "20260826_four_scene_common_domain_r4_corrected_xpio"
)
DEFAULT_COMPARISON = ROOT / "docs/pg_abc_gptuner_aqetuner_final_comparison_20260826.json"
FIGURE_ROOT = ROOT / "figures/algorithm_abc_mysql_bc_refresh_20260825"
DEFAULT_OUTPUT = ROOT / "docs/pg_external_baseline_delivery_verification_20260826.json"

ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
METHODS = ("gptuner", "aqetuner")
METHOD_LABELS = {"gptuner": "GPTuner", "aqetuner": "AQETuner"}
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
ALL_METHOD_LABELS = (
    "Algorithm A",
    "Algorithm B",
    "Algorithm C",
    "GPTuner",
    "AQETuner",
)


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def verify_workload(scene: str, row: Dict[str, Any], prefix: str, errors: List[str]) -> None:
    workload = row.get("workload_completeness") or {}
    require(workload.get("passed") is True, f"{prefix}: workload completeness failed", errors)
    if scene == "job_memory":
        require(workload.get("workload") == "job", f"{prefix}: not JOB", errors)
        require(workload.get("observed_query_count") == 113, f"{prefix}: JOB count !=113", errors)
        require(workload.get("expected_query_count") == 113, f"{prefix}: JOB expected !=113", errors)
    elif scene == "tpch_memory":
        require(workload.get("workload") == "tpch", f"{prefix}: not TPC-H", errors)
        expected_queries = [f"Q{query_id}" for query_id in range(1, 23)]
        observed_queries = workload.get("observed_query_sequence") or workload.get("observed_lane_sequence") or []
        require(
            workload.get("expected_query_sequence") == expected_queries,
            f"{prefix}: TPC-H expected sequence is not Q1-Q22",
            errors,
        )
        require(
            len(observed_queries) == 22 and sorted(observed_queries) == sorted(expected_queries),
            f"{prefix}: TPC-H Q1-Q22 were not each observed exactly once",
            errors,
        )
        require(
            workload.get("non_unit_query_counts") == [],
            f"{prefix}: TPC-H contains repeated or missing queries",
            errors,
        )
        require(
            workload.get("unexpected_query_names") == [],
            f"{prefix}: TPC-H contains unexpected queries",
            errors,
        )
    else:
        expected = [1, 2, 3, 4, 5, 6]
        require(workload.get("workload") == "hybench_xp", f"{prefix}: not native XP", errors)
        require(workload.get("observed_iq") == expected, f"{prefix}: IQ coverage mismatch", errors)
        require(workload.get("observed_at") == expected, f"{prefix}: AT coverage mismatch", errors)
        require(
            workload.get("forbidden_query_selection_fields_present") == [],
            f"{prefix}: forbidden XP query selection",
            errors,
        )


def verify_external_authority(
    path: Path,
    method: str,
    scene: str,
    *,
    corrected_xpio: bool,
    errors: List[str],
) -> Dict[str, Any]:
    prefix = f"{method}/{scene}"
    require(path.is_file(), f"{prefix}: missing authority {path}", errors)
    if not path.is_file():
        return {}
    authority = load_json(path)
    require(
        authority.get("contract") == "pg_external_four_scene_baseline_authority_v2",
        f"{prefix}: authority contract mismatch",
        errors,
    )
    require(authority.get("phase") == "complete", f"{prefix}: not complete", errors)
    require(authority.get("method") == method, f"{prefix}: method mismatch", errors)
    require(authority.get("scene") == scene, f"{prefix}: scene mismatch", errors)
    require(authority.get("candidate_budget") == 35, f"{prefix}: budget !=35", errors)
    require(
        authority.get("candidate_physical_invocations") == 35,
        f"{prefix}: candidate physical count !=35",
        errors,
    )
    require(authority.get("replay_parallelism") == 2, f"{prefix}: replay parallelism !=2", errors)
    require(authority.get("parameter_names") == list(ACTIVE_PARAMS), f"{prefix}: knob set mismatch", errors)
    require(authority.get("manual_mutation_after_start") is False, f"{prefix}: manual mutation", errors)
    require(
        int(authority.get("physical_workload_invocations") or 0)
        == int(authority.get("valid_replays") or 0)
        + int(authority.get("technical_failures") or 0),
        f"{prefix}: physical/valid/technical accounting mismatch",
        errors,
    )
    require(
        isinstance(authority.get("clean_restore_receipt_sha256"), str)
        and len(authority["clean_restore_receipt_sha256"]) == 64,
        f"{prefix}: missing clean restore SHA",
        errors,
    )
    frozen = authority.get("frozen_search_champion") or {}
    validated = authority.get("validated_champion") or {}
    require(set(frozen.get("params") or {}) == set(ACTIVE_PARAMS), f"{prefix}: frozen params mismatch", errors)
    require(validated.get("params") == frozen.get("params"), f"{prefix}: validated champion differs", errors)
    require(validated.get("physical_valid") is True, f"{prefix}: champion invalid", errors)
    require(
        (validated.get("parallel_worker_aggregate") or {}).get("worker_count") == 2,
        f"{prefix}: champion is not w1+w2 aggregate",
        errors,
    )
    verify_workload(scene, validated, prefix, errors)
    if corrected_xpio:
        guard = authority.get("execution_domain_guard") or {}
        final_guard = authority.get("final_execution_domain_receipts") or {}
        require(
            guard.get("contract") == "pg_external_search_execution_domain_guard_v1",
            f"{prefix}: corrected-domain guard missing",
            errors,
        )
        require(
            final_guard.get("container_resource_semantic_sha256")
            == guard.get("container_resource_semantic_sha256"),
            f"{prefix}: final container fingerprint drift",
            errors,
        )
        require(
            final_guard.get("fixed_guc_semantic_sha256")
            == guard.get("fixed_guc_semantic_sha256"),
            f"{prefix}: final fixed-GUC fingerprint drift",
            errors,
        )
        initial_receipts = guard.get("initial_receipts") or {}
        container_receipt_path = Path(
            str(initial_receipts.get("container_resource_receipt_path") or "")
        )
        fixed_guc_receipt_path = Path(
            str(initial_receipts.get("fixed_guc_receipt_path") or "")
        )
        require(container_receipt_path.is_file(), f"{prefix}: container receipt missing", errors)
        require(fixed_guc_receipt_path.is_file(), f"{prefix}: fixed-GUC receipt missing", errors)
        if container_receipt_path.is_file():
            container_receipt = load_json(container_receipt_path)
            workers = (container_receipt.get("semantic") or {}).get("workers") or []
            expected_cpusets = {
                "w1": "32-39,88-95,132-133,184-189,196-197,228-241",
                "w2": "40-47,96-103,134-135,190-195,198-199,242-255",
            }
            require(container_receipt.get("passed") is True, f"{prefix}: container receipt failed", errors)
            require(len(workers) == 2, f"{prefix}: container receipt does not contain two workers", errors)
            for worker in workers:
                worker_key = str(worker.get("worker") or "")
                require(
                    worker.get("cpuset_cpus") == expected_cpusets.get(worker_key),
                    f"{prefix}/{worker_key}: CPU set is not the formal XP-I/O domain",
                    errors,
                )
                require(
                    worker.get("nano_cpus") == 40_000_000_000,
                    f"{prefix}/{worker_key}: CPU quota is not 40 cores",
                    errors,
                )
                require(
                    worker.get("memory_bytes") == 24 * 1024**3,
                    f"{prefix}/{worker_key}: memory limit is not 24 GiB",
                    errors,
                )
        if fixed_guc_receipt_path.is_file():
            fixed_receipt = load_json(fixed_guc_receipt_path)
            snapshots = fixed_receipt.get("snapshots") or []
            require(fixed_receipt.get("passed") is True, f"{prefix}: fixed-GUC receipt failed", errors)
            require(fixed_receipt.get("worker_count") == 2, f"{prefix}: fixed-GUC worker count !=2", errors)
            require(len(snapshots) == 2, f"{prefix}: fixed-GUC snapshots !=2", errors)
            for snapshot in snapshots:
                require(
                    snapshot.get("search_gucs_excluded") == list(ACTIVE_PARAMS),
                    f"{prefix}/{snapshot.get('worker')}: mutable GUC exclusion mismatch",
                    errors,
                )
                require(
                    int(snapshot.get("setting_count") or 0) >= 300,
                    f"{prefix}/{snapshot.get('worker')}: fixed-GUC coverage is too small",
                    errors,
                )
    return authority


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external-root", type=Path, default=DEFAULT_EXTERNAL_ROOT)
    parser.add_argument("--xpio-root", type=Path, default=DEFAULT_XPIO_ROOT)
    parser.add_argument("--r4-root", type=Path, default=DEFAULT_R4_ROOT)
    parser.add_argument("--comparison", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: List[str] = []
    external_authorities: Dict[str, Dict[str, str]] = {}
    for method in METHODS:
        external_authorities[method] = {}
        for scene in SCENES:
            base = args.xpio_root if scene == "xp_io" else args.external_root
            path = base / method / scene / "authority_manifest.json"
            verify_external_authority(
                path,
                method,
                scene,
                corrected_xpio=(scene == "xp_io"),
                errors=errors,
            )
            if path.is_file():
                external_authorities[method][scene] = file_sha256(path)

    require(args.comparison.is_file(), "missing final comparison JSON", errors)
    comparison = load_json(args.comparison) if args.comparison.is_file() else {}
    require(
        comparison.get("contract")
        == "pg_abc_external_four_scene_relative_own_baseline_comparison_v1",
        "comparison contract mismatch",
        errors,
    )
    require(
        comparison.get("comparison_metric")
        == "method_own_contemporaneous_baseline_qps_improvement_pct",
        "comparison metric mismatch",
        errors,
    )
    require(
        comparison.get("absolute_qps_cross_method_comparable") is False,
        "absolute-QPS comparison guard missing",
        errors,
    )
    rows = comparison.get("four_scene_results") or []
    require(len(rows) == 20, "comparison row count !=20", errors)
    require(
        {(row.get("scene"), row.get("method")) for row in rows}
        == {(scene, method) for scene in SCENES for method in ALL_METHOD_LABELS},
        "comparison scene/method matrix mismatch",
        errors,
    )
    for index, row in enumerate(rows):
        prefix = f"comparison[{index}]"
        require(set(row.get("params") or {}) == set(ACTIVE_PARAMS), f"{prefix}: params mismatch", errors)
        baseline_qps = float(row.get("baseline_qps") or 0.0)
        final_qps = float(row.get("final_qps") or 0.0)
        gain = float(row.get("qps_improvement_pct") or 0.0)
        require(baseline_qps > 0.0, f"{prefix}: baseline QPS <=0", errors)
        require(final_qps > 0.0, f"{prefix}: final QPS <=0", errors)
        require(math.isfinite(gain), f"{prefix}: gain is not finite", errors)
        if baseline_qps > 0.0:
            expected_gain = (final_qps / baseline_qps - 1.0) * 100.0
            require(
                math.isclose(gain, expected_gain, rel_tol=1e-9, abs_tol=1e-9),
                f"{prefix}: relative gain mismatch",
                errors,
            )
        require(
            row.get("comparison_domain")
            == "method_own_contemporaneous_baseline",
            f"{prefix}: comparison domain mismatch",
            errors,
        )
        require(
            row.get("absolute_qps_cross_method_comparable") is False,
            f"{prefix}: absolute-QPS guard missing",
            errors,
        )
    none_rows = comparison.get("representative_none_results") or []
    require(len(none_rows) == 2, "None result count !=2", errors)
    require({row.get("method") for row in none_rows} == {"Algorithm B", "Algorithm C"}, "None methods mismatch", errors)
    for row in none_rows:
        require(set(row.get("params") or {}) == set(ACTIVE_PARAMS), f"None/{row.get('method')}: params mismatch", errors)
        require(Path(str(row.get("source") or "")).is_file(), f"None/{row.get('method')}: source missing", errors)

    expected_figures = (
        "pg_fig07_five_method_qps_gain",
        "pg_fig08_external_gap_to_best_abc",
        "pg_fig09_none_bc",
        "mysql_fig07_four_method_qps_gain",
        "mysql_fig08_external_gap_to_bc",
    )
    figure_sha256: Dict[str, str] = {}
    for stem in expected_figures:
        for suffix in (".png", ".pdf"):
            path = FIGURE_ROOT / f"{stem}{suffix}"
            require(path.is_file() and path.stat().st_size > 1024, f"missing/empty figure: {path}", errors)
            if path.is_file():
                figure_sha256[path.name] = file_sha256(path)
    pptx = FIGURE_ROOT / "algorithm_abc_external_none_mysql_formal_results_20260826.pptx"
    presentation_report = FIGURE_ROOT / "presentation_verification_20260826.json"
    content_report = FIGURE_ROOT / "presentation_content_qa_20260826.json"
    visual_report = FIGURE_ROOT / "presentation_visual_qa_20260826.json"
    pipeline_state = FIGURE_ROOT / "final_artifact_pipeline_state.json"
    rendered_root = FIGURE_ROOT / "rendered_20260826"
    rendered_contact = FIGURE_ROOT / "rendered_20260826_contact.png"
    require(pptx.is_file() and pptx.stat().st_size > 1024, "missing/empty final PPTX", errors)
    rendered_slides = sorted(rendered_root.glob("slide-*.png")) if rendered_root.is_dir() else []
    require(len(rendered_slides) == 22, "approximate rendered slide count !=22", errors)
    require(
        rendered_contact.is_file() and rendered_contact.stat().st_size > 1024,
        "missing/empty presentation contact sheet",
        errors,
    )
    for path, label in (
        (presentation_report, "presentation structural QA"),
        (content_report, "presentation content QA"),
        (visual_report, "presentation visual QA"),
    ):
        require(path.is_file(), f"missing {label}", errors)
        if path.is_file():
            report = load_json(path)
            require(report.get("passed") is True, f"{label} did not pass", errors)
            if label == "presentation structural QA":
                require(report.get("warnings") == [], f"{label} contains warnings", errors)
    require(pipeline_state.is_file(), "missing final artifact pipeline state", errors)
    if pipeline_state.is_file():
        pipeline = load_json(pipeline_state)
        require(pipeline.get("phase") == "complete_visual_qa", "artifact pipeline is not complete_visual_qa", errors)
        require(pipeline.get("fix_and_reverify_cycle_completed") is True, "PPT fix/reverify cycle missing", errors)
        require(Path(str(pipeline.get("rendered_slides") or "")) == rendered_root, "rendered slide path mismatch", errors)
        require(Path(str(pipeline.get("contact_sheet") or "")) == rendered_contact, "contact sheet path mismatch", errors)

    qa_sha256 = {
        path.name: file_sha256(path)
        for path in (presentation_report, content_report, visual_report, pipeline_state)
        if path.is_file()
    }
    none_source_sha256 = {
        str(row.get("method")): file_sha256(Path(str(row.get("source"))))
        for row in none_rows
        if Path(str(row.get("source") or "")).is_file()
    }

    result = {
        "contract": "pg_external_baseline_complete_delivery_verification_v2",
        "passed": not errors,
        "errors": errors,
        "selected_external_baselines": ["GPTuner", "AQETuner"],
        "allowed_mutable_postgresql_gucs": list(ACTIVE_PARAMS),
        "external_authority_expected_count": 8,
        "external_authority_observed_count": sum(
            len(scene_hashes) for scene_hashes in external_authorities.values()
        ),
        "external_authority_sha256": external_authorities,
        "common_domain_diagnostic": comparison.get("common_domain_diagnostic"),
        "common_domain_used_for_final_comparison": False,
        "comparison_sha256": file_sha256(args.comparison) if args.comparison.is_file() else None,
        "none_source_sha256": none_source_sha256,
        "figure_sha256": figure_sha256,
        "presentation_sha256": file_sha256(pptx) if pptx.is_file() else None,
        "rendered_slide_count": len(rendered_slides),
        "rendered_contact_sha256": file_sha256(rendered_contact) if rendered_contact.is_file() else None,
        "qa_sha256": qa_sha256,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
