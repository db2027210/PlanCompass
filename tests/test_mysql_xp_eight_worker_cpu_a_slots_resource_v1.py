from pathlib import Path

from scripts import build_mysql_xp_eight_worker_cpu_a_slots_resource_v1 as release


def test_release_has_exact_balanced_no_a_prior_mapping() -> None:
    assert len(release.EXPANSION) == 8
    source = Path(release.__file__).read_text(encoding="utf-8")
    assert '"algorithm_a_candidate_injection": False' in source
    assert '"algorithm_a_qps_used_for_generation_or_ranking": False' in source
    assert '"/disk1": 4, "/disk2": 4' in source
    assert '"disk1_total_workers": 8' in source
    assert '"disk2_total_workers": 8' in source
