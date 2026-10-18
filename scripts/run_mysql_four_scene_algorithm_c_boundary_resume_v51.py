#!/usr/bin/env python3
"""Reuse SHA-bound scene-equivalent or replicated formal R0 evidence."""

from __future__ import annotations

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v50 as v50


scene_checkpoint = v50.scene_checkpoint
_source = v50._source
_parser_old = '    parser.add_argument("--formal-baseline", type=Path)'
_parser_new = _parser_old + '\n    parser.add_argument("--formal-r0-set", type=Path)'
if _source.count(_parser_old) != 1:
    raise RuntimeError("Algorithm C v51 formal-R0-set CLI boundary differs")
_source = _source.replace(_parser_old, _parser_new)

_start = _source.index('        if args.formal_baseline is not None:')
_end_marker = '        elif formal_partial is not None:'
_end = _source.index(_end_marker, _start)
_old = _source[_start:_end]
_new = '''        if args.formal_baseline is not None:
            baseline_path = args.formal_baseline.expanduser().resolve()
            r0_set_path = (
                args.formal_r0_set.expanduser().resolve()
                if args.formal_r0_set is not None else None
            )
            if (
                not baseline_path.is_file()
                or baseline_path.is_symlink()
                or baseline_path.name != "batch_result.json"
                or baseline_path.parent.name != "formal_incumbent_baseline"
                or r0_set_path is None
                or not r0_set_path.is_file()
                or r0_set_path.is_symlink()
            ):
                raise RuntimeError("regular formal baseline and R0-set artifacts required")
            r0_set = alg.load(r0_set_path)
            scene = next(iter(SCENES))
            r0_row = _mapping(
                _mapping(r0_set.get("scenes"), "formal R0 set scenes").get(scene),
                "formal R0 scene row",
            )
            equivalence = _mapping(
                r0_row.get("scene_resource_equivalence"),
                "scene resource equivalence",
            )
            source_root = baseline_path.parent.parent
            source_state_path = source_root / "run_state.json"
            source_state = alg.load(source_state_path)
            source_gate_path = Path(str(r0_row.get("paper_gate_path") or "")).resolve()
            source_gate = alg.load(source_gate_path)
            source_single_gate_path = Path(
                str(r0_row.get("source_single_run_gate_path") or "")
            ).resolve()
            source_single_gate = alg.load(source_single_gate_path)
            baseline_document = alg.load(baseline_path)
            baseline_rows = _mapping(
                baseline_document.get("measurements"),
                "reused formal baseline measurements",
            )
            formal_binding = _mapping(
                source_state.get("formal_incumbent_baseline"),
                "source formal incumbent baseline",
            )
            mode = r0_row.get("baseline_acceptance_mode")
            state_status_allowed = (
                mode == "single_run"
                and source_state.get("status") == "COMPLETE"
                and source_state.get("stage") == "BASELINE_ONLY_COMPLETE"
                and source_single_gate.get("status") == "PASS"
            ) or (
                mode == "replicated_mean"
                and source_state.get("status") == "FAILED_CLOSED"
                and source_single_gate.get("status") == "FAILED_CLOSED"
                and source_gate.get("schema_version")
                == "mysql_algorithm_c_replicated_paper_baseline_gate_v1"
            )
            if (
                r0_set.get("status") != "PASS"
                or r0_set.get("freeze_sha256") != alg.sha(alg.FREEZE)
                or r0_set.get("scene_local_resource_equivalence_required") is not True
                or Path(str(r0_row.get("batch_result_path") or "")).resolve()
                != baseline_path
                or r0_row.get("batch_result_sha256") != alg.sha(baseline_path)
                or Path(str(r0_row.get("run_state_path") or "")).resolve()
                != source_state_path
                or r0_row.get("run_state_sha256") != alg.sha(source_state_path)
                or r0_row.get("paper_gate_sha256") != alg.sha(source_gate_path)
                or r0_row.get("source_single_run_gate_sha256")
                != alg.sha(source_single_gate_path)
                or not state_status_allowed
                or set(source_state.get("active_scenes") or []) != set(SCENES)
                or formal_binding.get("status") != "PASS"
                or Path(str(formal_binding.get("path") or "")).resolve()
                != baseline_path
                or formal_binding.get("sha256") != alg.sha(baseline_path)
                or source_gate.get("status") != "PASS"
                or source_gate.get("candidate_search_authorized") is not True
                or equivalence.get("equal") is not True
                or equivalence.get("target_freeze_sha256") != alg.sha(alg.FREEZE)
                or equivalence.get("source_scene_projection_sha256")
                != equivalence.get("target_scene_projection_sha256")
                or baseline_document.get("status") != "PASS"
                or baseline_document.get("failures") not in ([], None)
            ):
                raise RuntimeError("scene-equivalent formal baseline lineage differs")
            state["formal_reused_incumbent_baseline"] = {
                "status": "PASS",
                "path": str(baseline_path),
                "sha256": alg.sha(baseline_path),
                "source_run_state_path": str(source_state_path),
                "source_run_state_sha256": alg.sha(source_state_path),
                "source_paper_gate_path": str(source_gate_path),
                "source_paper_gate_sha256": alg.sha(source_gate_path),
                "formal_r0_set_path": str(r0_set_path),
                "formal_r0_set_sha256": alg.sha(r0_set_path),
                "baseline_acceptance_mode": mode,
                "scene_resource_equivalence": copy.deepcopy(equivalence),
                "formal_evidence_eligible": True,
                "physical_measurement_repeated": False,
                "accepted_state_credit_increment": 0,
            }
            for normalization_name in (
                "same_worker_qps_normalization",
                "same_worker_qps_normalization_path",
                "same_worker_qps_normalization_sha256",
                "outer_acceptance_uses_same_worker_normalized_client_qps",
            ):
                if normalization_name in source_state:
                    state[normalization_name] = copy.deepcopy(
                        source_state[normalization_name]
                    )
'''
_source = _source[:_start] + _new + _source[_end:]
exec(compile(_source, str(__file__), "exec"), globals())
