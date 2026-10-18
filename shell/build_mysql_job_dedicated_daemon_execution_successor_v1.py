#!/usr/bin/env python3
"""Seal the executable JOB dedicated-daemon successor from v36.

This is an offline content-addressed publisher.  It deliberately does not
contact Docker, systemd, or a remote host.  The published authority remains
qualification-only until its live daemon/readback gates pass.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "artifacts/mysql_job_v36_measurement_parent_successor_v1/a2766c83b0b3c36841e8825194cf89ef23c6e903b42db7e31fc7454a036adcd8"
OUTPUT = ROOT / "artifacts/mysql_job_dedicated_daemon_execution_successor_v1"
PARENT_MANIFEST = "4288dc347ee071fa62c167409397872a80cb2b105cf6be437277ef493a5edcaa"
SOCKET = "unix:///run/ra-docker-mysql-job-v1.sock"


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise RuntimeError("expected one %s patch site, got %d" % (label, source.count(old)))
    return source.replace(old, new)


def patch_python(relative, source):
    """Make every JOB Docker invocation choose the dedicated socket explicitly."""
    source = source.replace('"docker",', '"docker", "--host", "%s",' % SOCKET)
    # A small number of legacy helpers build a shell command rather than argv.
    source = re.sub(
        r"(?<![A-Za-z0-9_-])docker (?=(?:run|exec|inspect|ps|stop|rm|image|info)\b)",
        "docker --host %s " % SOCKET,
        source,
    )
    if relative.endswith("mysql_job_distributed_v2_authority_tmpfs_v2.py"):
        source = _replace_once(
            source,
            '        "--publish",\n        "127.0.0.1:%d:3306" % MYSQL_PORT,',
            '        "--network",\n        "host",',
            "database host-network",
        )
        source = _replace_once(
            source,
            '        "mysqld",\n        "--bind-address=0.0.0.0",',
            '        "mysqld",\n        "--port=%d" % MYSQL_PORT,\n        "--bind-address=0.0.0.0",',
            "mysqld port",
        )
        source = _replace_once(
            source,
            '''    port_rows = ((host.get("PortBindings") or {}).get("3306/tcp") or [])\n''',
            "",
            "authority port bindings variable",
        )
        source = _replace_once(
            source,
            '''        or {\n            (item.get("HostIp"), int(item.get("HostPort") or 0))\n            for item in port_rows\n        }\n        != {("127.0.0.1", MYSQL_PORT)}\n''',
            '''        or host.get("NetworkMode") != "host"\n        or host.get("PortBindings") not in ({}, None)\n''',
            "authority host-network readback",
        )
        source = source.replace('"mysql_job_distributed_v2_bootstrap_evidence_v1"', '"mysql_job_dedicated_daemon_bootstrap_evidence_v1"')
        helper = '''\n\ndef _dedicated_daemon_readback(database_inspect, client_inspect):\n    """Read the dedicated daemon and every overlay path before admitting bootstrap."""\n    from mysql_job_dedicated_daemon_contract_v1 import validate\n    raw = _run(["docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock", "info", "--format", "{{json .}}"], timeout=30).stdout\n    try:\n        info = json.loads(raw)\n    except ValueError as exc:\n        raise AuthorityError("dedicated daemon info is invalid JSON: %s" % exc)\n    unit = "ra-docker-mysql-job-v1.service"\n    props = {}\n    for line in _run(["systemctl", "show", unit, "--property=MainPID,ExecStart"], timeout=30).stdout.splitlines():\n        if "=" in line:\n            key, value = line.split("=", 1); props[key] = value\n    daemon_pid = int(props.get("MainPID") or 0)\n    if daemon_pid <= 0:\n        raise AuthorityError("dedicated daemon systemd PID is absent")\n    proc = Path("/proc") / str(daemon_pid)\n    try:\n        startticks = int((proc / "stat").read_text().split()[21])\n        cpuset = [line.split(":", 1)[1].strip() for line in (proc / "status").read_text().splitlines() if line.startswith("Cpus_allowed_list:")][0]\n        cmdline_sha256 = sha_bytes((proc / "cmdline").read_bytes())\n    except (IndexError, OSError, ValueError) as exc:\n        raise AuthorityError("dedicated daemon proc identity differs: %s" % exc)\n    mount_devices = {}\n    containers = {}\n    for role, row in (("database", database_inspect), ("client", client_inspect)):\n        state = row.get("State") or {}; data = ((row.get("GraphDriver") or {}).get("Data") or {})\n        paths = {}\n        for key in ("UpperDir", "WorkDir", "MergedDir", "LowerDir"):\n            path = data.get(key)\n            if not isinstance(path, str) or not path:\n                raise AuthorityError("dedicated daemon GraphDriver %s is absent" % key)\n            paths[key] = path\n            mount_devices[path] = _run(["findmnt", "-n", "-o", "MAJ:MIN", "--target", path], timeout=30).stdout.strip()\n        pid = int(state.get("Pid") or 0); process = Path("/proc") / str(pid)\n        try:\n            container_startticks = int((process / "stat").read_text().split()[21])\n        except (OSError, ValueError, IndexError) as exc:\n            raise AuthorityError("dedicated daemon container proc identity differs: %s" % exc)\n        containers[role] = {"network_mode": (row.get("HostConfig") or {}).get("NetworkMode"), "daemon_socket": "/run/ra-docker-mysql-job-v1.sock", "mysql_server_port": MYSQL_PORT, "container_id": row.get("Id"), "pid": pid, "startticks": container_startticks, "graphdriver_data": paths}\n    daemon = {"unit": unit, "socket": "/run/ra-docker-mysql-job-v1.sock", "cpuset_cpus": cpuset, "data_root_device": "259:3", "pid": daemon_pid, "startticks": startticks, "cmdline_sha256": cmdline_sha256}\n    return validate(info, daemon, containers, mount_devices)\n'''
        helper = helper.replace(
            '        startticks = int((proc / "stat").read_text().split()[21])\n        cpuset = [line.split(":", 1)[1].strip() for line in (proc / "status").read_text().splitlines() if line.startswith("Cpus_allowed_list:")][0]\n        cmdline_sha256 = sha_bytes((proc / "cmdline").read_bytes())',
            '        startticks = int((proc / "stat").read_text().split()[21])\n        status_text = (proc / "status").read_text()\n        cpuset = [line.split(":", 1)[1].strip() for line in status_text.splitlines() if line.startswith("Cpus_allowed_list:")][0]\n        ppid = int([line.split(":", 1)[1].strip() for line in status_text.splitlines() if line.startswith("PPid:")][0])\n        cmdline_sha256 = sha_bytes((proc / "cmdline").read_bytes())',
        ).replace(
            '    daemon = {"unit": unit, "socket": "/run/ra-docker-mysql-job-v1.sock", "cpuset_cpus": cpuset, "data_root_device": "259:3", "pid": daemon_pid, "startticks": startticks, "cmdline_sha256": cmdline_sha256}',
            '    daemon = {"unit": unit, "socket": "/run/ra-docker-mysql-job-v1.sock", "cpuset_cpus": cpuset, "data_root_device": "259:3", "pid": daemon_pid, "startticks": startticks, "cmdline_sha256": cmdline_sha256, "ppid": ppid, "cgroup": (proc / "cgroup").read_text(), "namespaces": {key: os.readlink(str(proc / "ns" / key)) for key in ("mnt", "pid", "net", "user")}, "docker_ancestry": False}',
        )
        source = _replace_once(source, "\ndef main():\n", helper + "\n\ndef main():\n", "dedicated daemon readback helper")
        source = _replace_once(source, '            "credential_binding",\n', '            "credential_binding",\n            "dedicated_daemon",\n', "dedicated evidence key")
        source = _replace_once(source, 'def validate_bootstrap_evidence(evidence, spec):\n    """Verify exact DB/client identity, physical copy, and collect-only output."""\n    spec = validate_spec(spec)\n', 'def validate_bootstrap_evidence(evidence, spec):\n    """Verify exact DB/client identity, physical copy, and collect-only output."""\n    spec = validate_spec(spec)\n    if (evidence.get("dedicated_daemon") or {}).get("status") != "PASS_DEDICATED_DAEMON_NON_PG_GRAPHDRIVER":\n        raise AuthorityError("dedicated daemon readback does not pass")\n', "dedicated evidence gate")
        source = _replace_once(source, '    live_path = measurement / "live_readback.json"\n', '    dedicated_daemon = _dedicated_daemon_readback(database_inspect, client_inspect)\n    live_path = measurement / "live_readback.json"\n', "dedicated runtime readback call")
        source = _replace_once(source, '        "credential_binding": {\n', '        "dedicated_daemon": dedicated_daemon,\n        "credential_binding": {\n', "dedicated evidence document")
        source = _replace_once(
            source,
            'def execute_bootstrap(spec, output, main_agent_token):\n',
            'def execute_bootstrap(spec, output, main_agent_token_file):\n',
            "token file signature",
        )
        source = _replace_once(
            source,
            '''    spec = validate_spec(spec)\n    if (\n        not isinstance(main_agent_token, str)\n        or not main_agent_token\n        or sha_bytes(main_agent_token.encode("utf-8"))\n        != spec["main_agent_token_sha256"]\n    ):\n        raise AuthorityError("main-agent bootstrap token differs")\n''',
            '''    spec = validate_spec(spec)\n    token_path = Path(main_agent_token_file)\n    if (token_path.is_symlink() or not token_path.is_file()\n            or stat.S_IMODE(token_path.stat().st_mode) & 0o077):\n        raise AuthorityError("main-agent bootstrap token file differs")\n    main_agent_token = token_path.read_text(encoding="utf-8").strip()\n    if (not main_agent_token or sha_bytes(main_agent_token.encode("utf-8"))\n            != spec["main_agent_token_sha256"]):\n        raise AuthorityError("main-agent bootstrap token differs")\n''',
            "token file validation",
        )
        source = _replace_once(
            source,
            '    parser.add_argument("--main-agent-token")\n',
            '    parser.add_argument("--main-agent-token-file", type=Path)\n',
            "token argv removal",
        )
        source = _replace_once(
            source,
            '        value = execute_bootstrap(spec, args.output, args.main_agent_token)\n',
            '        if args.main_agent_token_file is None:\n            raise AuthorityError("execute-bootstrap requires --main-agent-token-file")\n        value = execute_bootstrap(spec, args.output, args.main_agent_token_file)\n',
            "token file call",
        )
    if relative.endswith("mysql_job_distributed_v2_adopted_services.py"):
        source = _replace_once(
            source,
            '''    ports = ((host.get("PortBindings") or {}).get("3306/tcp") or [])\n''',
            "",
            "adopted port bindings variable",
        )
        source = _replace_once(
            source,
            '''        or {\n            (item.get("HostIp"), int(item.get("HostPort") or 0))\n            for item in ports\n        }\n        != {("127.0.0.1", PLANNER_PORT)}\n''',
            '''        or host.get("NetworkMode") != "host"\n        or host.get("PortBindings") not in ({}, None)\n''',
            "adopted host-network readback",
        )
    if relative.endswith("mysql_job_distributed_live_readback_qualification_v2.py"):
        source = source.replace("server_port != 3306", "server_port != 65102")
        source = source.replace("database_counter_transport, port, 3306", "database_counter_transport, port, 65102")
        source = _replace_once(source, '    parser.add_argument("--main-agent-token")\n', '    parser.add_argument("--main-agent-token-file", type=Path)\n', "collector token argv removal")
        source = _replace_once(source, '    args = parser.parse_args()\n', '''    args = parser.parse_args()\n    args.main_agent_token = None\n    if args.main_agent_token_file is not None:\n        token_path = args.main_agent_token_file\n        if (token_path.is_symlink() or not token_path.is_file()\n                or stat.S_IMODE(token_path.stat().st_mode) & 0o077):\n            raise QualificationError("main-agent token file differs")\n        args.main_agent_token = token_path.read_text(encoding="utf-8").strip()\n''', "collector token file read")
    if relative.endswith("render_mysql_job_v32_qualification_solo_tmpfs_v2.py"):
        source = source.replace('"--main-agent-token", "<read-from-0600-token-file>",', '"--main-agent-token-file", "<0600-token-file-path>",')
    if relative.endswith(("mysql_parameter_optimizer.py", "mysql_cost_adapter.py")):
        source = source.replace('config.get("port", 3306)', 'config.get("port", 65102)')
    return source


def materializer_source():
    return '''#!/usr/bin/env python3
"""Issue one fresh, unexecuted JOB dedicated-daemon bootstrap material set."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, secrets, stat
from pathlib import Path
def canonical(v): return (json.dumps(v, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)+"\\n").encode()
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load_module(path, name):
 s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def main():
 p=argparse.ArgumentParser(); p.add_argument("--bundle-manifest",type=Path,required=True); p.add_argument("--mysql-image-reference",required=True); p.add_argument("--mysql-image-id",required=True); p.add_argument("--client-image-reference",required=True); p.add_argument("--client-image-id",required=True); p.add_argument("--main-agent-token-file",type=Path,required=True); p.add_argument("--output-root",type=Path,required=True); p.add_argument("--valid-minutes",type=int,default=45); a=p.parse_args()
 mp=a.bundle_manifest.resolve(strict=True); m=json.loads(mp.read_text()); c=m.get("dedicated_daemon_contract") or {}
 if mp.parent.name!=m.get("bundle_id") or m.get("schema_version")!="mysql_job_dedicated_daemon_execution_successor_bundle_v1" or c.get("socket")!="/run/ra-docker-mysql-job-v1.sock" or c.get("db_client_network")!="host" or c.get("mysql_server_port")!=65102 or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False: raise SystemExit("NOT_READY: dedicated daemon bundle binding differs")
 token=a.main_agent_token_file.resolve(strict=True)
 if token.is_symlink() or stat.S_IMODE(token.stat().st_mode)&0o077 or not token.read_text().strip(): raise SystemExit("NOT_READY: main-agent token differs")
 root=Path(__file__).resolve().parents[1]; render=load_module(root/"controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py","render"); authority=load_module(root/"qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py","authority")
 ns=argparse.Namespace(bundle_manifest=mp,mysql_image_reference=a.mysql_image_reference,mysql_image_id=a.mysql_image_id,client_image_reference=a.client_image_reference,client_image_id=a.client_image_id,main_agent_token_file=token,attempt_token=None,valid_minutes=a.valid_minutes,output=Path("unused")); spec=render.render(ns); plan=authority.command_plan(spec)
 mid=hashlib.sha256(canonical({"bundle_id":m["bundle_id"],"attempt_token":spec["attempt_token"],"spec_sha256":hashlib.sha256(canonical(spec)).hexdigest()})).hexdigest(); out=a.output_root/mid; out.mkdir(parents=True,mode=0o700)
 for name,value,mode in (("bootstrap_spec.json",canonical(spec),0o400),("command_plan.json",canonical(plan),0o400),("materials.json",canonical({"schema_version":"mysql_job_dedicated_daemon_materials_v2","status":"ISSUED_NOT_EXECUTED","bundle_id":m["bundle_id"],"attempt_token":spec["attempt_token"],"socket":c["socket"],"formal_launch_authorized":False,"accepted_round_authorized":False}),0o400)):
  q=out/name
  if q.exists(): raise SystemExit("NOT_READY: immutable material collision")
  q.write_bytes(value); os.chmod(q,mode); (out/(name+".sha256")).write_text(digest(q)+"  "+name+"\\n"); os.chmod(out/(name+".sha256"),mode)
 print(json.dumps({"status":"ISSUED_NOT_EXECUTED","materials":str(out),"attempt_token":spec["attempt_token"],"formal_launch_authorized":False,"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__": main()
'''.encode()


def main():
    manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    if PARENT.name != manifest.get("bundle_id") or sha256(PARENT / "bundle_manifest.json") != PARENT_MANIFEST:
        raise RuntimeError("v36 parent manifest differs")
    payload = {}
    for relative in manifest["files"]:
        if relative == "closure.json":
            continue
        value = (PARENT / relative).read_bytes()
        if relative.endswith(".py"):
            value = patch_python(relative, value.decode()).encode()
        payload[relative] = value
    payload["qualification/mysql_job_dedicated_daemon_materializer_v2.py"] = materializer_source()
    payload["authority/mysql_job_dedicated_daemon_contract_v1.py"] = (ROOT / "shell/mysql_job_dedicated_daemon_contract_v1.py").read_bytes()
    invalidation = {"schema_version":"mysql_job_dedicated_daemon_ttl_material_invalidation_v1","status":"INVALIDATED_DO_NOT_USE","superseded_material_bundle_id":"49d8ff4b02a4e3aa88d87cc05638b0fb8504805213602209d113943cd3a91d21","unpublishable_successor_bundle_id":"49ff6a3e449f6977be1fcebff0dc373133865f5c159c075cb51eb1f3e90ad327","reason":"contract-only material never bound an executable explicit-socket authority; 49ff execution successor contains verification-generated Python bytecode and is zero-credit/unpublishable","accepted_round_credit":0,"formal_launch_authorized":False,"accepted_round_authorized":False}
    payload["authority/mysql_job_dedicated_daemon_ttl_material_invalidation_20260731.json"] = canonical(invalidation)
    files = {name: hashlib.sha256(value).hexdigest() for name, value in sorted(payload.items())}
    closure = json.loads((PARENT / "closure.json").read_text()); closure["files"] = files
    closure["dedicated_daemon_execution_contract"] = {"socket":"/run/ra-docker-mysql-job-v1.sock","docker_argv_prefix":["docker","--host",SOCKET],"db_client_network":"host","mysql_server_port":65102,"daemon_root":"/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/docker_ra_job_v1","device_major_minor":"259:3","graphdriver_fields":["UpperDir","WorkDir","MergedDir","LowerDir"],"old_ttl_material_invalidated":True}
    payload["closure.json"] = canonical(closure)
    files = {name: hashlib.sha256(value).hexdigest() for name, value in sorted(payload.items())}
    identity = {key:value for key,value in manifest.items() if key not in ("bundle_id","files")}
    identity.update({"schema_version":"mysql_job_dedicated_daemon_execution_successor_bundle_v1","predecessor_bundle_id":PARENT.name,"predecessor_manifest_sha256":PARENT_MANIFEST,"dedicated_daemon_contract":closure["dedicated_daemon_execution_contract"],"old_ttl_material_invalidation_sha256":hashlib.sha256(payload["authority/mysql_job_dedicated_daemon_ttl_material_invalidation_20260731.json"]).hexdigest(),"formal_launch_authorized":False,"accepted_round_authorized":False,"files":files})
    bundle_id = hashlib.sha256(canonical(identity)).hexdigest(); identity["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True); target=OUTPUT/bundle_id
    if target.exists():
        if json.loads((target/"bundle_manifest.json").read_text()) != identity: raise RuntimeError("existing bundle differs")
    else:
        stage=OUTPUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for name,value in payload.items():
                path=stage/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(value); os.chmod(path,0o444)
            raw=canonical(identity); (stage/"bundle_manifest.json").write_bytes(raw); (stage/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n"); os.rename(stage,target)
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            raise
    print(json.dumps({"status":"PASS_SEALED_EXECUTABLE_DEDICATED_DAEMON_SUCCESSOR","bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":sha256(target/"bundle_manifest.json"),"formal_launch_authorized":False,"accepted_round_authorized":False},sort_keys=True))


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc); raise SystemExit(2)
