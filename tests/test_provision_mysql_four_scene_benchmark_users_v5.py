from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/provision_mysql_four_scene_benchmark_users_v5.py"


def test_benchmark_account_provisioning_is_private_and_no_secret_output() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'BENCHMARK_USER = "ra_benchmark"' in source
    assert "secrets.token_hex(32)" in source
    assert '"passwords_emitted": False' in source
    assert "input=sql" in source
    assert '"mysql", f"--defaults-file={root_defaults}", "--host=127.0.0.1",' in source
    assert 'f"--port={worker[\'host_port\']}", "--connect-timeout=10", "-NB",' in source
    assert "input=sql" in source
    assert 'f"--port={worker[\'host_port\']}", "--connect-timeout=10", "-NBe",' in source
    assert '"docker", "exec"' not in source
    assert '"SELECT COUNT(*) FROM information_schema.processlist "' in source
    assert 'f"--defaults-file={benchmark_defaults}"' in source
    assert '"--rotate-all"' in source
    assert "not verify_existing and not rotate_all" in source
    assert "GRANT ALL PRIVILEGES ON" in source
    assert "benchmark.cnf" in source


def test_exposed_root_credential_is_rotated_for_all_workers() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "ALTER USER 'root'@'%'" in source
    assert "ALTER USER 'root'@'localhost'" in source
    assert '"root_credentials_rotated_after_local_diagnostic_exposure": True' in source
    assert '"workload_started": False' in source
    assert '"accepted_rounds": {scene: 0' in source
