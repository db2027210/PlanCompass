from pathlib import Path

from scripts import monitor_mysql_xp_cba_cpu_a_slots_eight_worker_v4 as monitor


def test_cba_v4_uses_eight_worker_launchers_and_fixed_authorities() -> None:
    source = Path(monitor.__file__).read_text(encoding="utf-8")
    assert "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_scene_v1.py" in source
    assert "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_eight_worker_scene_v1.py" in source
    assert "v986_xp_cpu_eight_worker_cpu_a_slots_authority_20260828" in source
    assert "v987_xp_io_eight_worker_cpu_a_slots_authority_20260828" in source


def test_a_is_check_only_and_cannot_feed_b_or_c() -> None:
    source = Path(monitor.__file__).read_text(encoding="utf-8")
    assert '"algorithm_a_role": "independent_posthoc_check_only"' in source
    assert '"algorithm_a_feedback_to_b_or_c": False' in source
    assert '"algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c": False' in source


def test_a_launcher_is_not_given_source_authority_prior() -> None:
    expanded = monitor.text
    a_call = expanded.split('if b_done and not a_state', 1)[1]
    assert '"--source-authority", str(args.source_authority)' not in a_call
