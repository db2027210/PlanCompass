from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v11 as subject,
)


def test_v11_state_binds_path_stable_controller(monkeypatch):
    monkeypatch.setattr(subject.alg, "sha", lambda _path: "0" * 64)
    document = subject.state_document_v11("parallel_221_path_test")
    assert document["technical_continuation"]["kind"].endswith("resume_v11")
    assert document["technical_continuation"]["v10_prestart_failure_started_workload"] is False
    assert document["controller_source_path"] == str(subject.Path(subject.__file__).resolve())
    assert document["formal_candidate_batches"] == [[1, 2], [3, 4], [5]]


def test_v11_entrypoint_has_project_root_on_sys_path():
    assert str(subject.ROOT) in subject.sys.path
