#!/usr/bin/env python3
"""Honor a validated replicated resource gate during formal R0 reuse."""

from __future__ import annotations

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v51 as v51


scene_checkpoint = v51.scene_checkpoint
_source = v51._source
_old = '''        paper_gate = paper_baseline_gate.validate_and_write(
            incumbents=incumbents,
            baseline_rows=baseline_rows,
            output=output / "paper_baseline_resource_gate.json",
        )
'''
_new = '''        reused_binding = state.get("formal_reused_incumbent_baseline") or {}
        if reused_binding.get("baseline_acceptance_mode") == "replicated_mean":
            replicated_source_path = Path(
                str(reused_binding.get("source_paper_gate_path") or "")
            ).resolve()
            replicated_source = alg.load(replicated_source_path)
            if (
                replicated_source.get("status") != "PASS"
                or replicated_source.get("candidate_search_authorized") is not True
                or replicated_source.get("schema_version")
                != "mysql_algorithm_c_replicated_paper_baseline_gate_v1"
                or alg.sha(replicated_source_path)
                != reused_binding.get("source_paper_gate_sha256")
            ):
                raise RuntimeError("replicated formal paper baseline gate differs")
            paper_gate = {
                **copy.deepcopy(replicated_source),
                "schema_version": "mysql_algorithm_c_reused_replicated_paper_baseline_gate_v1",
                "source_replicated_gate_path": str(replicated_source_path),
                "source_replicated_gate_sha256": alg.sha(replicated_source_path),
                "formal_r0_set_path": reused_binding.get("formal_r0_set_path"),
                "formal_r0_set_sha256": reused_binding.get("formal_r0_set_sha256"),
                "physical_baseline_remeasurement_performed": False,
                "current_candidate_search_authorized": True,
            }
            alg.atomic(output / "paper_baseline_resource_gate.json", paper_gate)
        else:
            paper_gate = paper_baseline_gate.validate_and_write(
                incumbents=incumbents,
                baseline_rows=baseline_rows,
                output=output / "paper_baseline_resource_gate.json",
            )
'''
if _source.count(_old) != 1:
    raise RuntimeError("Algorithm C v52 replicated-gate boundary differs")
_source = _source.replace(_old, _new)
exec(compile(_source, str(__file__), "exec"), globals())
