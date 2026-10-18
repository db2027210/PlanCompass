from pathlib import Path

import prepare_mysql_tpcc_e1 as controller
import resume_mysql_tpcc_e1_publish as resume


def worker_text() -> str:
    manifest, _ = controller._load_manifest(controller.DEFAULT_MANIFEST)
    return controller._remote_worker_script(
        manifest,
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/.incoming/test",
        "test",
    ).decode("utf-8")


def test_publish_tree_checks_and_seal_run_inside_limited_helper() -> None:
    text = worker_text()
    helper = text[text.index("# The datadir is owned"):text.index('mv -T -- "$work/publish" "$template"')]
    assert "--network none" in helper
    assert "--cpus 1" in helper
    assert "--memory 268435456" in helper
    assert "--pids-limit 64" in helper
    assert "find /seal -xdev ! -type f ! -type d" in helper
    assert "find /seal -xdev -type f -exec chmod 0444" in helper
    assert "find /seal -xdev -type d -exec chmod 0555" in helper
    assert "find /seal -xdev -perm /0222" in helper
    assert 'find "$work/publish"' not in helper
    assert 'sudo -n mv -T -- "$work/publish" "$template"' in text


def test_post_shutdown_failure_preserves_work_checkpoint() -> None:
    text = worker_text()
    assert "clean_shutdown_checkpoint=0" in text
    assert "clean_shutdown_checkpoint=1" in text
    assert 'test "$success" != 1 && test "$clean_shutdown_checkpoint" != 1' in text


def test_worker_never_mutates_postgresql() -> None:
    text = worker_text()
    assert "docker inspect pg5_tpch_s04_20260722" in text
    assert "docker exec pg5_tpch_s04_20260722" not in text
    assert "docker stop pg5_tpch_s04_20260722" not in text
    assert "docker rm pg5_tpch_s04_20260722" not in text


def test_resume_is_publish_only_and_preserves_failed_manifest() -> None:
    text = resume.execute_script()
    assert "--create=true" not in text
    assert "--load=true" not in text
    assert "row_counts.json" in text and '"event":"clean_shutdown"' in text
    assert "retry6_invalid_zero_tree_manifest.json" in text
    assert "datadir_tree_after_seal.json" in text
    assert "count>0 and directories>0 and size>0" in text
    assert 'sudo -n mv -T -- "$publish" "$template"' in text


def test_resume_postgresql_access_is_inspect_only() -> None:
    text = resume.execute_script()
    assert "docker inspect pg5_tpch_s04_20260722" in text
    assert "docker exec pg5_tpch_s04_20260722" not in text
    assert "docker stop pg5_tpch_s04_20260722" not in text
    assert "docker rm pg5_tpch_s04_20260722" not in text
