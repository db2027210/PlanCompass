from pathlib import Path

from scripts import build_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_release_v1 as release
from scripts import launch_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_scene_v1 as launcher


def test_b_release_is_eight_worker_qps_blind_to_a() -> None:
    source = Path(release.__file__).read_text(encoding="utf-8")
    assert '"candidate_batches": [[1, 2, 3, 4, 5]]' in source
    assert '"physical_replay_parallelism": 8' in source
    assert '"algorithm_a_candidate_injection": False' in source
    assert '"algorithm_a_qps_used_for_generation_or_ranking": False' in source


def test_launcher_requires_terminal_c_and_prepared_eight_lane_authority() -> None:
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert 'c_state.get("status") == "COMPLETE"' in source
    assert 'manifest.get("physical_replay_parallelism") != 8' in source
    assert 'state.get("physical_replay_parallelism") or 0) == 8' in source
    assert '"algorithm_a_candidate_or_qps_prior_used": False' in source
