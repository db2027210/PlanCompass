from __future__ import annotations

import json
from pathlib import Path

import pytest

from shell.prepare_mysql_tpcc_e1 import (
    CommandResult,
    DEFAULT_MANIFEST,
    TPCCE1Error,
    _remote_worker_script,
    prepare_e1,
)


PASS = json.dumps({"status": "PASS", "checks": {"all": True}, "failed": []}) + "\n"


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run(self, args, *, input_bytes=None):
        self.calls.append((tuple(args), input_bytes))
        if not self.results:
            raise AssertionError("unexpected command")
        return self.results.pop(0)


def test_default_dry_run_is_one_read_only_preflight() -> None:
    runner = FakeRunner([CommandResult(0, PASS, "")])
    result = prepare_e1(runner=runner)
    assert result["mode"] == "dry_run"
    assert result["mutations_performed"] is False
    assert result["formal_r0_authorized"] is False
    assert result["touches_postgresql"] is False
    assert len(runner.calls) == 1
    assert runner.calls[0][0][-1] == "python3 -"
    script = runner.calls[0][1].decode()
    assert "docker\",\"inspect\",\"pg5_tpch_s04_20260722" in script
    assert "docker\",\"stop" not in script
    assert "docker\",\"rm" not in script


def test_preflight_failure_is_fail_closed() -> None:
    runner = FakeRunner([CommandResult(1, '{"status":"FAIL"}\n', "")])
    with pytest.raises(TPCCE1Error, match="preflight failed"):
        prepare_e1(runner=runner)
    assert len(runner.calls) == 1


def test_execute_requires_all_literal_confirmations_after_read_only_audit() -> None:
    runner = FakeRunner([CommandResult(0, PASS, "")])
    with pytest.raises(TPCCE1Error, match="exact host/project/image"):
        prepare_e1(execute=True, runner=runner)
    assert len(runner.calls) == 1


def test_worker_has_frozen_isolation_secret_io_and_no_pg_mutation() -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    worker = _remote_worker_script(manifest, "/safe/stage", "run_1").decode()
    assert "--cpuset-cpus 21-24,57-60 --cpuset-mems 1 --cpus 4" in worker
    assert "--cpuset-cpus 25-28,61-64 --cpuset-mems 1" in worker
    assert "--memory 4294967296 --memory-swap 4294967296" in worker
    assert '-v "$remote_config:/benchbase/config:ro" -w /benchbase' in worker
    assert 'for i in (1,5,9):' in worker
    assert "-cp '/benchbase/benchbase.jar:/benchbase/lib/*' com.oltpbenchmark.DBWorkload" in worker
    assert "-jar /benchbase/benchbase.jar" not in worker
    assert "MYSQL_ROOT_PASSWORD_FILE=/run/secrets/root_password" in worker
    assert "MYSQL_ROOT_PASSWORD=" not in worker
    assert "MYSQL_PWD" not in worker
    assert "sed \"s/$secret" not in worker
    assert "--defaults-extra-file=/tmp/root.cnf" in worker
    assert "mysqladmin --defaults-extra-file" not in worker
    assert "authenticated_ready=1" in worker
    assert "-e 'SELECT 1'" in worker
    assert "openssl rand -hex 32" in worker
    assert "blkio.throttle.read_bps_device" in worker
    assert "blkio.throttle.write_bps_device" in worker
    assert "259:2 1205862400" in worker
    assert "while test ! -e /run/e1-control/go" in worker
    assert worker.index("grep -Fx '259:2 1205862400'") < worker.index('touch "$work/control/go"')
    assert "docker inspect pg5_tpch_s04_20260722" in worker
    for forbidden in (
        "docker stop pg5_tpch_s04_20260722",
        "docker rm pg5_tpch_s04_20260722",
        "kill 43775",
        "psql",
    ):
        assert forbidden not in worker


def test_worker_fixes_counts_clean_shutdown_hash_and_readonly_seal() -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    worker = _remote_worker_script(manifest, "/safe/stage", "run_2").decode()
    for literal in (
        '"warehouse":10', '"district":100', '"customer":300000',
        '"history":300000', '"oorder":300000', '"new_order":90000',
        '"item":100000', '"stock":1000000',
    ):
        assert literal in worker
    assert "1500000 <= d.get(\"order_line\",-1) <= 4500000" in worker
    assert "Shutdown complete" in worker
    assert "sha256_tree_v1" in worker
    assert "find /seal -xdev -type f -exec chmod 0444" in worker
    assert "find /seal -xdev -type d -exec chmod 0555" in worker
    assert 'sudo -n mv -- "$work/datadir" "$work/publish/datadir"' in worker
    assert "--network none" in worker
    assert '"formal_r0_authorized":False' in worker


def test_execute_uploads_assets_runs_worker_and_saves_evidence(tmp_path: Path) -> None:
    completion = json.dumps({
        "status": "PASS", "phase": "E1_TEMPLATE_COMPLETE",
        "template": "/safe/template", "manifest_sha256": "a" * 64,
        "formal_r0_authorized": False,
    }) + "\n"
    runner = FakeRunner([
        CommandResult(0, PASS, ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, completion, ""),
    ])
    evidence = tmp_path / "evidence"
    result = prepare_e1(
        execute=True,
        confirm_host="anonymous@192.0.2.7",
        confirm_project="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc",
        confirm_image="sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172",
        evidence_dir=evidence,
        runner=runner,
        run_id="fake_run",
    )
    assert result["phase"] == "E1_TEMPLATE_COMPLETE"
    assert result["mutations_performed"] is True
    assert len(runner.calls) == 5
    assert runner.calls[2][0][0] == "scp"
    assert runner.calls[3][0][0] == "scp"
    assert runner.calls[4][0][-1] == "bash -s"
    assert evidence.joinpath("tpcc_e1_result.json").read_text() == completion
    assert evidence.joinpath("tpcc_e1_result.json.sha256").is_file()


def test_worker_failure_does_not_publish_local_success_evidence(tmp_path: Path) -> None:
    runner = FakeRunner([
        CommandResult(0, PASS, ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(75, '{"status":"FAIL"}\n', "worker failed"),
    ])
    evidence = tmp_path / "evidence"
    with pytest.raises(TPCCE1Error, match="worker failed"):
        prepare_e1(
            execute=True,
            confirm_host="anonymous@192.0.2.7",
            confirm_project="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc",
            confirm_image="sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172",
            evidence_dir=evidence,
            runner=runner,
            run_id="fake_failure",
        )
    assert not evidence.exists()
