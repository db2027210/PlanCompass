from pathlib import Path

from scripts import monitor_mysql_xp_cba_cpu_a_slots_eight_worker_v5 as monitor


def test_terminal_authority_precedes_b() -> None:
    source = Path(monitor.__file__).read_text(encoding="utf-8")
    assert "build_mysql_xp8_live_authority_from_c_terminal_v1.py" in source
    assert 'attempts: dict[str, list[dict[str, Any]]] = {"AUTHORITY": [], "B": [], "A": []}' in source
    expanded = monitor.text
    assert expanded.index('str(PYTHON), str(AUTHORITY_BUILDER)') < expanded.index('str(PYTHON), str(B_LAUNCHER)')
    assert '(b_authority_root / "expansion_manifest.json").is_file()' in expanded


def test_a_is_posthoc_check_only() -> None:
    source = Path(monitor.__file__).read_text(encoding="utf-8")
    assert '"algorithm_a_role": "independent_posthoc_check_only"' in source
    assert '"algorithm_a_feedback_to_b_or_c": False' in source
    assert '"algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c": False' in source
    a_call = monitor.text.split("if b_done and not a_state", 1)[1]
    assert '"--source-authority", str(args.source_authority)' not in a_call
