import subprocess
import sys
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "shell/manual_mysql_sgd_r0_campaign.py"


def test_all_lanes_use_direct_keepalive_ssh() -> None:
    from shell.manual_mysql_sgd_r0_campaign import (
        LANES,
        remote_ssh_command,
        ssh_transport_options,
        tunnel_ssh_command,
    )

    expected = [
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=6",
    ]
    for lane in LANES.values():
        assert ssh_transport_options(lane) == expected
        command = remote_ssh_command(lane, "hostname")
        assert lane.host in command
        assert "-J" not in command
    job_tunnel = tunnel_ssh_command(LANES["job"])
    assert job_tunnel[1:5] == expected
    assert "-J" not in job_tunnel
    assert job_tunnel[-1] == LANES["job"].host
    assert "127.0.0.1:65102:127.0.0.1:65102" in job_tunnel


def test_database_counter_sampler_reuses_connections_and_preserves_scope() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert '"tpcc": ("Com_insert", "Com_update", "Com_delete")' in source
    assert '"tpch": ("Com_select",)' in source
    assert '"persistent_mysql_session"' in source
    assert '{"job", "tpch", "tpcc"}' in source
    assert "SHOW GLOBAL STATUS" in source
    assert "__RA_COUNTER_END__" not in source
    assert "SHOW SESSION STATUS LIKE 'Uptime'" in source
    assert "while not sentinel_seen" in source
    assert 'if self.lane.key == "xp":' in source
    assert "independent_process_per_second" in source


def test_resource_sampler_reconnects_without_bypassing_coverage_gate() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "resource sampler stream ended" in source
    assert "self.sampler = self._open_resource_sampler()" in source
    assert "resource sampler supervisor exited" in source
    assert 'stability.get("stable") is True' in source


def test_capture_mode_is_advertised_without_starting_workload():
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--capture-general-log" in result.stdout
    assert "900-second" in result.stdout


def test_capture_mode_contract_is_not_r0_stability():
    source = RUNNER.read_text(encoding="utf-8")
    start = source.index("def execute_capture_general_log")
    end = source.index("    def execute(self)", start)
    capture = source[start:end]
    assert "self.session.stop_after_measurement(" in capture
    assert "graceful_drain_seconds=600" in capture
    assert "stability_required=False" in capture
    assert "extract_tpch_production_bindings.py" in source
    assert "q1_q22_complete=True" in capture


def test_capture_mode_rejects_non_tpch_and_non_900():
    source = RUNNER.read_text(encoding="utf-8")
    assert "--capture-general-log is only supported for --scenario tpch" in source
    assert "--capture-general-log requires --measurement-seconds 900" in source
    assert "--capture-tpch-bindings is only supported for --scenario tpch" in source
    assert "--capture-tpch-bindings requires --measurement-seconds 900" in source


def test_slow_inflight_query_gets_bounded_drain_after_stop_request(tmp_path):
    sys.path.insert(0, str(ROOT))
    from formal_benchbase_continuous_session import FormalBenchBaseContinuousSession

    class SlowProcess:
        pid = 42
        returncode = None

        def poll(self):
            return None

        def wait(self, *, timeout):
            self.timeout = timeout
            raise subprocess.TimeoutExpired("benchbase", timeout)

    session = object.__new__(FormalBenchBaseContinuousSession)
    session.process = SlowProcess()
    published = []
    session._publish = lambda seconds: published.append(seconds)
    with pytest.raises(RuntimeError, match="graceful drain timeout after 600s"):
        session.request_stop_and_wait(900, 600)
    assert published == [900]
    assert session.process.timeout == 600


def test_capture_archives_before_tunnel_or_container_cleanup():
    source = RUNNER.read_text(encoding="utf-8")
    cleanup = source[
        source.index("    def cleanup"):
        source.index("    def capture_failure_diagnostics")
    ]
    assert cleanup.index("self.export_capture_general_log") < cleanup.index(
        "for process in (self.sampler, self.tunnel)"
    )
    assert "docker exec" in source
    assert "docker cp" in source
    assert "docker inspect --format" in source


def test_binding_capture_uses_one_production_transaction_at_a_time():
    source = RUNNER.read_text(encoding="utf-8")
    start = source.index("def _capture_one_tpch_binding")
    end = source.index("    def execute_tpch_binding_capture", start)
    binding = source[start:end]
    assert "self.tpch_session(" in binding
    assert "query_weights=\",\".join(weights)" in binding
    assert "terminals=1" in binding
    assert "os.killpg(process.pid, signal.SIGTERM)" in binding
    assert 'protocol["timing"]["maximum_measurement_seconds"] = query_timeout_seconds' in binding
    assert "maximum_measurement_seconds=query_timeout_seconds" in binding
    assert "submission_wait_seconds = 600 if query == 15 else 90" in binding
    assert "general_log_submission_evidence_final_extraction_fail_closed" in binding


def test_binding_capture_requires_all_22_queries_before_extract():
    source = RUNNER.read_text(encoding="utf-8")
    capture = source[
        source.index("def execute_tpch_binding_capture"):
        source.index("    def execute(self)", source.index("def execute_tpch_binding_capture"))
    ]
    assert "range(self.binding_start_query, 23)" in capture
    assert "range(1, self.binding_start_query)" in capture
    assert "q1_q22_complete=True" in capture
    assert "self.export_capture_general_log(require_complete=True)" in capture


def test_binding_capture_uses_frozen_evaluator_horizon_and_preserves_900s_contract():
    source = RUNNER.read_text(encoding="utf-8")
    start = source.index("def _capture_one_tpch_binding")
    end = source.index("    def execute_tpch_binding_capture", start)
    binding = source[start:end]
    assert "query_timeout_seconds = 600" in binding
    assert "maximum_measurement_seconds=query_timeout_seconds" in binding
    assert "--capture-tpch-bindings requires --measurement-seconds 900" in source


def test_unstable_r0_is_fail_closed_for_xp_and_algorithm_c():
    source = RUNNER.read_text(encoding="utf-8")
    gate = source[source.index("if not self.r0_gate_valid"):source.index("self.algorithm_c_authorized = True")]
    assert "algorithm_c_authorized=False" in gate
    assert "xap_authorized=False" in gate
    assert "INVALID_R0_UNSTABLE" in gate


def test_formal_resource_cv_thresholds_are_uniform_contract():
    protocol = json.loads(
        (ROOT / "preflight/frozen_formal_config_20260722_v9/measurement_protocol.json")
        .read_text(encoding="utf-8")
    )
    stability = protocol["stability"]
    assert {
        stability["cpu_cv_max"],
        stability["memory_cv_max"],
        stability["io_cv_max"],
    } == {0.10}


def test_r0_profile_gate_requires_target_and_non_target_bands():
    from shell.manual_mysql_sgd_r0_campaign import (
        LANES,
        evaluate_r0_profile,
    )

    def measurement(cpu, memory, io):
        return {
            "rolling_measurement_control": {
                "selected_observation": {
                    "resource_means": {
                        "cpu": cpu,
                        "memory": memory,
                        "io": io,
                    }
                }
            }
        }

    passed = evaluate_r0_profile(
        LANES["job"], measurement(cpu=0.4, memory=0.9, io=0.4)
    )
    failed = evaluate_r0_profile(
        LANES["job"], measurement(cpu=0.15, memory=0.74, io=0.48)
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert failed["checks"]["cpu"]["passed"] is False
    assert failed["checks"]["memory"]["passed"] is False


def test_tpcc_r0_uses_frozen_read_committed_isolation(tmp_path):
    from shell.manual_mysql_sgd_r0_campaign import Campaign, LANES

    protocol = json.loads(
        (ROOT / "preflight/frozen_formal_config_20260722_v9/measurement_protocol.json")
        .read_text(encoding="utf-8")
    )
    campaign = Campaign(
        LANES["tpcc"],
        tmp_path,
        "tpcc_isolation_contract",
        measurement_seconds=600,
    )

    campaign.tpcc_session(protocol)

    generated = ET.parse(tmp_path / "private/tpcc_660.xml")
    assert generated.getroot().findtext("isolation") == "TRANSACTION_READ_COMMITTED"
