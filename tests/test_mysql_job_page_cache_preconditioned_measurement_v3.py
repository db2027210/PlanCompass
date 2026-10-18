from scripts import mysql_job_page_cache_preconditioned_measurement_v3 as subject


def test_permission_safe_path_is_exactly_frozen():
    worker = {
        "worker_id": "job_w1",
        "datadir": "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/job_w1/datadir",
    }
    assert str(subject._database_dir(worker)).endswith("/job_w1/datadir/imdb_job")


def test_permission_safe_path_rejects_drift():
    worker = {"worker_id": "job_w1", "datadir": "/disk2/not-the-frozen-path"}
    try:
        subject._database_dir(worker)
    except RuntimeError as error:
        assert "unsafe JOB page-cache target" in str(error)
    else:
        raise AssertionError("drifted page-cache path was accepted")
