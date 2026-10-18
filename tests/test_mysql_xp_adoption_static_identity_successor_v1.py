import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = (
    "31b220eaa7feda556f4179a9fa6558a8fd586cc9635555612c16a45acf20fc4a"
)
BUNDLE = ROOT / "artifacts/mysql_xp_formal10_launch_v3" / BUNDLE_ID


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


service = module(
    ROOT / "shell/mysql_xp_node02_adopted_service_v1.py",
    "xp_static_identity_service",
)
reconciliation = module(
    ROOT / "shell/mysql_xp_adoption_reconciliation_v1.py",
    "xp_static_identity_reconciliation",
)


def inspect_row(role="database"):
    return {
        "Id": role + "-container",
        "Name": "/" + role,
        "Image": "sha256:" + "a" * 64,
        "Created": "2026-07-31T00:00:00Z",
        "State": {"Running": True, "Pid": 101},
        "Config": {
            "Image": "sha256:" + "a" * 64,
            "Labels": {"ra.role": role},
            "Cmd": ["sleep", "infinity"],
            "ExposedPorts": {"3306/tcp": {}},
        },
        "HostConfig": {
            "NetworkMode": "host" if role == "client" else "default",
            "PortBindings": (
                {}
                if role == "client"
                else {"3306/tcp": [{
                    "HostIp": "127.0.0.1", "HostPort": "65104",
                }]}
            ),
            "CpusetCpus": (
                "3-11,39-47" if role == "client" else "21-33,57-69"
            ),
            "CpuQuota": 1800000 if role == "client" else 530000,
        },
        "Mounts": [{
            "Type": "bind",
            "Source": "/source/" + role,
            "Destination": "/destination/" + role,
            "Mode": "ro" if role == "client" else "",
            "RW": role != "client",
            "Propagation": "rprivate",
        }],
        "NetworkSettings": {
            "SandboxID": "dynamic-a",
            "SandboxKey": "/var/run/docker/netns/dynamic-a",
            "IPAddress": "192.0.2.63",
            "Ports": {"3306/tcp": [{
                "HostIp": "127.0.0.1", "HostPort": "65104",
            }]},
        },
    }


def snapshot(database, client):
    roles = {}
    for role, row in (("database", database), ("client", client)):
        roles[role] = {
            "container_id": row["Id"],
            "static_inspect_identity":
                service._static_inspect_identity(row),
            "dynamic_network_settings_observation":
                service._dynamic_network_observation(row),
        }
    return {
        "schema_version": "mysql_xp_adoption_read_only_snapshot_v1",
        "host_id": "node02",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "roles": roles,
    }


def test_dynamic_network_settings_are_observed_but_not_hashed():
    before = inspect_row()
    after = json.loads(json.dumps(before))
    after["NetworkSettings"]["SandboxID"] = "dynamic-b"
    after["NetworkSettings"]["IPAddress"] = "192.0.2.64"
    assert service._config_identity(before) == service._config_identity(after)
    assert (
        service._dynamic_network_observation(before)["sha256"]
        != service._dynamic_network_observation(after)["sha256"]
    )


def test_static_host_config_and_unknown_mount_fields_fail_closed():
    before = inspect_row()
    after = json.loads(json.dumps(before))
    after["HostConfig"]["CpuQuota"] = 530001
    assert service._config_identity(before) != service._config_identity(after)
    after = inspect_row()
    after["Mounts"][0]["UnclassifiedFutureField"] = "value"
    with pytest.raises(
        service.ServiceError, match="unclassified docker inspect Mounts"
    ):
        service._config_identity(after)


def test_two_snapshot_reconciliation_separates_dynamic_and_static_diff(
    tmp_path,
):
    database = inspect_row("database")
    client = inspect_row("client")
    before = snapshot(database, client)
    dynamic_database = json.loads(json.dumps(database))
    dynamic_database["NetworkSettings"]["SandboxID"] = "dynamic-b"
    dynamic = snapshot(dynamic_database, client)
    before_path = tmp_path / "before.json"
    dynamic_path = tmp_path / "dynamic.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    dynamic_path.write_text(json.dumps(dynamic), encoding="utf-8")
    result = reconciliation.reconcile(before_path, dynamic_path)
    assert result["status"] == "PASS_SAME_STATIC_IDENTITY"
    assert result["fail_closed_static_field_diff"] == []
    assert result["dynamic_network_settings_observed_diff"]

    changed_client = json.loads(json.dumps(client))
    changed_client["HostConfig"]["CpuQuota"] += 1
    static = snapshot(database, changed_client)
    static_path = tmp_path / "static.json"
    static_path.write_text(json.dumps(static), encoding="utf-8")
    result = reconciliation.reconcile(before_path, static_path)
    assert result["status"] == "FAIL_CLOSED_STATIC_IDENTITY_DIFF"
    assert result["fail_closed_static_field_diff"]


def test_bootstrap_rejects_nonroot_before_pristine_readback(monkeypatch):
    called = []
    monkeypatch.setattr(service.socket, "gethostname", lambda: "node02")
    monkeypatch.setattr(service.os, "geteuid", lambda: 1008)
    monkeypatch.setattr(
        service, "_validate_copy",
        lambda *args, **kwargs: called.append((args, kwargs)),
    )
    with pytest.raises(
        service.ServiceError,
        match="pristine datadir validation requires effective UID 0",
    ):
        service.bootstrap({}, Path("/unreadable"), Path("/unused"), "token")
    assert called == []


def test_client_mysql_image_volume_is_explicitly_masked_and_volume_rejected(
    tmp_path, monkeypatch,
):
    bundle = tmp_path / ("b" * 64)
    config = bundle / "qualification/config"
    config.mkdir(parents=True)
    for name in service.QUALIFICATION_CONFIG_FILES:
        (config / name).write_text("sealed\n", encoding="utf-8")
    mask = bundle / service.CLIENT_MYSQL_MASK_RELATIVE
    mask.mkdir(parents=True)
    (mask / service.CLIENT_MYSQL_MASK_MARKER).write_text(
        "sealed read-only mask for image-declared /var/lib/mysql\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        service, "verify_bundle",
        lambda unused: {"bundle_id": bundle.name},
    )
    monkeypatch.setattr(
        service, "verify_runtime_publication",
        lambda unused_bundle, unused_publication: {},
    )
    monkeypatch.setattr(
        service, "verify_attempt",
        lambda unused_attempt, unused_token: "a" * 64,
    )
    monkeypatch.setattr(
        service, "verify_short_lived_authority",
        lambda unused_created, unused_expires: {
            "expires_at_utc": unused_expires,
        },
    )
    attempt = "xp-client-mask-test-attempt"
    attempt_root = Path(service.RUNTIME_ROOT) / attempt
    image = "sha256:" + "c" * 64
    plan = service.build_plan(
        bundle, image, image, config, attempt_root / "datadir",
        attempt_root / "host_measurement_tmpfs", "/runtime/publication.json",
        attempt, attempt, "d" * 64,
        "2026-07-31T00:00:00+00:00", "2026-07-31T00:20:00+00:00",
    )
    client_create = plan["client"]["create_argv"]
    mask_mount = (
        "type=bind,src=%s,dst=/var/lib/mysql,readonly"
        % mask.resolve()
    )
    assert mask_mount in client_create

    labels = {
        "ra.formal_scope": "mysql_only",
        "ra.cross_engine_comparison_prohibited": "true",
        "ra.scenario": service.SCENARIO,
        "ra.bundle_id": plan["bundle_id"],
        "ra.attempt_id": plan["attempt_id"],
        "ra.handoff_token_sha256": plan["handoff_token_sha256"],
        "ra.main_agent_token_sha256": plan["main_agent_token_sha256"],
        "ra.authorization_expires_at_utc":
            plan["short_lived_authority"]["expires_at_utc"],
        "ra.role": "client",
    }
    sources = {
        service.BUNDLE_DESTINATION: str(bundle.resolve()),
        service.RUNTIME_DESTINATION: service.JDK_ROOT,
        service.CONFIG_DESTINATION: str(config.resolve()),
        service.CLIENT_MYSQL_MASK_DESTINATION: str(mask.resolve()),
    }
    row = {
        "Id": "client-container",
        "Image": image,
        "Created": "2026-07-31T00:00:00Z",
        "State": {"Running": True, "Pid": 333},
        "Config": {
            "Image": image, "Labels": labels,
            "Cmd": ["sleep", "infinity"],
        },
        "HostConfig": {
            "CpusetCpus": service.CLIENT_CPUS,
            "CpusetMems": service.CLIENT_MEMS,
            "CpuPeriod": service.CLIENT_CPU_PERIOD,
            "CpuQuota": service.CLIENT_CPU_QUOTA,
            "Memory": service.MEMORY,
            "MemorySwap": service.MEMORY,
            "PidsLimit": service.CLIENT_PIDS,
            "RestartPolicy": {"Name": "no"},
            "ReadonlyRootfs": True,
            "NetworkMode": "host",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "LogConfig": {"Type": "none"},
            "Tmpfs": {
                service.MEASUREMENT_DESTINATION:
                    "rw,nosuid,nodev,noexec,size=%d"
                    % service.TMPFS_BYTES,
            },
        },
        "Mounts": [
            {
                "Type": "bind", "Destination": destination,
                "Source": source, "RW": False,
            }
            for destination, source in sources.items()
        ],
        "NetworkSettings": {},
    }
    monkeypatch.setattr(service, "_proc_start_ticks", lambda unused: 1234)
    assert service._validate_container(row, plan, "client")["pid"] == 333
    row["Mounts"][-1] = {
        "Type": "volume",
        "Destination": "/var/lib/mysql",
        "Source": (
            "/mnt/nvme2n1/anonymous_resource_adaptive/docker/volumes/"
            "anonymous/_data"
        ),
        "RW": True,
    }
    with pytest.raises(
        service.ServiceError,
        match="anonymous volume or unexpected/PG-device mount",
    ):
        service._validate_container(row, plan, "client")


def test_published_successor_is_frozen_and_preserves_all_xp_gates():
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    contract = json.loads(
        (BUNDLE / "xp_executable_launch_contract_v3.json").read_text()
    )
    assert manifest["bundle_id"] == BUNDLE_ID
    assert manifest[
        "superseded_dynamic_adoption_identity_bundle_id"
    ] == "a0cc7b9fe8ac898ddb612b0c79be493e44aa333da5e14dfb14b6adb9bfbfac63"
    assert contract["database_resource_envelope"]["cpu_quota_cores"] == 5.3
    assert contract["database_resource_envelope"]["io_read_bps"] == 29360128
    assert contract["database_resource_envelope"]["io_write_bps"] == 524288
    assert contract[
        "capture_only_resource_envelope"
    ]["io_read_bps"] == 1205862400
    assert contract[
        "capture_only_resource_envelope"
    ]["io_write_bps"] == 1205862400
    assert contract["distributed_host_contract"]["host_id"] == "node02"
    assert contract[
        "distributed_host_contract"
    ]["database_cpuset_cpus"] == "21-33,57-69"
    assert contract["adoption_reconciliation"][
        "static_field_diff_policy"
    ] == "FAIL_CLOSED"
    assert contract["adopted_service"][
        "bootstrap_pristine_readback_effective_uid_required"
    ] == 0
    mask = contract["adopted_service"][
        "client_image_declared_mysql_volume_mask"
    ]
    assert mask["destination"] == "/var/lib/mysql"
    assert mask["read_only"] is True
    invalidated = json.loads(
        (
            BUNDLE / "invalidated_bed961_client_anonymous_volume.json"
        ).read_text()
    )
    assert invalidated["accepted_rounds_credit"] == 0
    assert invalidated["observed"][
        "stopped_before_workload_or_qualification"
    ] is True
    source = (
        BUNDLE
        / "predecessor/predecessor/source/src/main/java/com/hybench/HyBench.java"
    ).read_text()
    assert 'ConfigLoader.prop.getProperty("xapclient","-1")' in source
    assert "Future.isDone() is not success evidence" in source
    tp = (
        BUNDLE
        / "predecessor/predecessor/source/src/main/java/com/hybench/workload/TPClient.java"
    ).read_text()
    assert 'traceAT("AT4"' in tp
    assert 'traceAT("AT5"' in tp
    transition = (
        BUNDLE / "launch/mysql_xp_capture_transition_v1.py"
    ).read_text()
    assert transition.index("restore_required = True") < transition.index(
        '_write(read, CAPTURE["io_read_bps"])'
    )
    assert "finally:" in transition
    for path in [BUNDLE] + sorted(BUNDLE.rglob("*")):
        assert not path.is_symlink()
        assert path.stat().st_mode & 0o222 == 0
