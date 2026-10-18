#!/usr/bin/env python3
"""Reuse a SHA-bound passing scene R0 before independent candidate search."""

from __future__ import annotations

from scripts import mysql_algorithm_c_paper_baseline_gate_v1 as paper_baseline_gate
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as v48


scene_checkpoint = v48.scene_checkpoint
_source = v48._source
_parser_old = '    parser.add_argument("--baseline-only", action="store_true")'
_parser_new = _parser_old + '\n    parser.add_argument("--formal-baseline", type=Path)'
if _source.count(_parser_old) != 1:
    raise RuntimeError("Algorithm C v49 formal-baseline CLI boundary differs")
_source = _source.replace(_parser_old, _parser_new)
_old = '        if formal_partial is not None:\n'
_new = '''        if args.formal_baseline is not None:
            baseline_path = args.formal_baseline.expanduser().resolve()
            if (
                not baseline_path.is_file()
                or baseline_path.is_symlink()
                or baseline_path.name != "batch_result.json"
                or baseline_path.parent.name != "formal_incumbent_baseline"
            ):
                raise RuntimeError("regular formal baseline batch artifact required")
            source_root = baseline_path.parent.parent
            source_state_path = source_root / "run_state.json"
            source_gate_path = source_root / "paper_baseline_resource_gate.json"
            source_state = alg.load(source_state_path)
            source_gate = alg.load(source_gate_path)
            baseline_document = alg.load(baseline_path)
            baseline_rows = _mapping(
                baseline_document.get("measurements"),
                "reused formal baseline measurements",
            )
            formal_binding = _mapping(
                source_state.get("formal_incumbent_baseline"),
                "source formal incumbent baseline",
            )
            gate_binding = _mapping(
                source_state.get("paper_baseline_resource_gate"),
                "source paper baseline gate",
            )
            if (
                source_state.get("status") != "COMPLETE"
                or source_state.get("stage") != "BASELINE_ONLY_COMPLETE"
                or source_state.get("baseline_only") is not True
                or source_state.get("freeze_sha256") != alg.sha(alg.FREEZE)
                or set(source_state.get("active_scenes") or []) != set(SCENES)
                or formal_binding.get("status") != "PASS"
                or Path(str(formal_binding.get("path") or "")).resolve() != baseline_path
                or formal_binding.get("sha256") != alg.sha(baseline_path)
                or gate_binding.get("status") != "PASS"
                or Path(str(gate_binding.get("path") or "")).resolve() != source_gate_path
                or gate_binding.get("sha256") != alg.sha(source_gate_path)
                or source_gate.get("status") != "PASS"
                or source_gate.get("candidate_search_authorized") is not True
                or baseline_document.get("status") != "PASS"
                or baseline_document.get("failures") not in ([], None)
            ):
                raise RuntimeError("SHA-bound passing formal baseline lineage differs")
            state["formal_reused_incumbent_baseline"] = {
                "status": "PASS",
                "path": str(baseline_path),
                "sha256": alg.sha(baseline_path),
                "source_run_state_path": str(source_state_path),
                "source_run_state_sha256": alg.sha(source_state_path),
                "source_paper_gate_path": str(source_gate_path),
                "source_paper_gate_sha256": alg.sha(source_gate_path),
                "formal_evidence_eligible": True,
                "physical_measurement_repeated": False,
                "accepted_state_credit_increment": 0,
            }
        elif formal_partial is not None:
'''
if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v49 formal-baseline reuse boundary differs")
_source = _source.replace(_old, _new)
exec(compile(_source, str(__file__), "exec"), globals())
