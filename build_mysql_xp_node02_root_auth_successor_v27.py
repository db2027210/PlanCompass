#!/usr/bin/env python3
"""Build the immutable XP v27 root-auth bootstrap successor locally."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parent
PARENT_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v26" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v27"
SERVICE = Path("launch/mysql_xp_node02_adopted_service_v1.py")
MATERIALS = Path("test_support/mysql_xp_qualification_bootstrap_materials_v5.py")
CONTRACT = Path("xp_executable_launch_contract_v3.json")
BUILDER = Path("build_mysql_xp_node02_root_auth_successor_v27.py")
TEST = Path("tests/test_mysql_xp_root_auth_successor_v27.py")


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def replace_exact(text, old, new, count, label):
    if text.count(old) != count:
        raise BuildError("%s cardinality differs: %d" % (label, text.count(old)))
    return text.replace(old, new)


def patch_service(path):
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        'PRIVATE_MYSQL_DEFAULTS = "mysql-client.cnf"\n',
        'PRIVATE_MYSQL_DEFAULTS = "mysql-client.cnf"\n'
        'PRIVATE_ROOT_DEFAULTS = "mysql-root-bootstrap.cnf"\n'
        'CONTAINER_ROOT_DEFAULTS = "/run/mysql-xp-root-bootstrap.cnf"\n'
        'ROOT_READINESS_DIAGNOSTIC = "bootstrap_root_readiness_diagnostic.json"\n',
        1, "root credential constants",
    )
    text = replace_exact(
        text,
        '        != "mysql_xp_node02_successor_bundle_v26"\n',
        '        != "mysql_xp_node02_successor_bundle_v27"\n',
        1, "service bundle schema",
    )
    text = replace_exact(
        text,
        '''    files = {
        PRIVATE_MYSQL_DEFAULTS: directory / PRIVATE_MYSQL_DEFAULTS,
        PRIVATE_QUALIFICATION_CONFIG:
            directory / PRIVATE_QUALIFICATION_CONFIG,
    }
''',
        '''    files = {
        PRIVATE_MYSQL_DEFAULTS: directory / PRIVATE_MYSQL_DEFAULTS,
        PRIVATE_QUALIFICATION_CONFIG:
            directory / PRIVATE_QUALIFICATION_CONFIG,
        PRIVATE_ROOT_DEFAULTS: directory / PRIVATE_ROOT_DEFAULTS,
    }
''',
        1, "private credential closure",
    )
    marker = '''    properties = {}
    for line in files[PRIVATE_QUALIFICATION_CONFIG].read_text(
'''
    helper = '''    root_payload = files[PRIVATE_ROOT_DEFAULTS].read_bytes()
    try:
        root_lines = [
            line.strip() for line in root_payload.decode("utf-8").splitlines()
            if line.strip()
        ]
    except UnicodeDecodeError:
        raise ServiceError("private root defaults encoding differs")
    if (
        len(root_lines) != 3
        or root_lines[0] != "[client]"
        or root_lines[1] != "user=root"
        or not root_lines[2].startswith("password=")
        or len(root_lines[2].split("=", 1)[1]) < 1
        or len(root_lines[2].split("=", 1)[1]) > 256
        or any(ord(character) < 33 or ord(character) > 126
               for character in root_lines[2].split("=", 1)[1])
    ):
        raise ServiceError("private root defaults shape differs")
    properties = {}
    for line in files[PRIVATE_QUALIFICATION_CONFIG].read_text(
'''
    text = replace_exact(text, marker, helper, 1, "root defaults validation")
    text = replace_exact(
        text,
        '''        "_password": password,
        "_content_sha256": {
''',
        '''        "root_defaults": PRIVATE_ROOT_DEFAULTS,
        "_password": password,
        "_root_defaults_payload": root_payload,
        "_content_sha256": {
''',
        1, "private root payload return",
    )
    old_wait = '''def _wait_for_database_socket(plan):
    """Wait for an exact socket query while preserving fail-closed startup."""
    for _unused in range(DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS):
        readiness = _run(
            _docker_argv(
                "exec", plan["database"]["name"],
                "mysql", "--user=root", "--protocol=socket", "--batch", "--raw",
                "--skip-column-names", "--execute", "SELECT 1",
            ),
            check=False,
        )
        if readiness.returncode == 0 and readiness.stdout.strip() == "1":
            return readiness
        time.sleep(DATABASE_SOCKET_READINESS_POLL_SECONDS)
    raise ServiceError("database socket bootstrap readiness failed")


'''
    new_wait = '''def _root_mysql_error_category(completed):
    """Map private mysql stderr to a fixed, non-secret diagnostic category."""
    value = (completed.stderr or "").lower()
    if "access denied" in value or "using password" in value:
        return "access_denied"
    if "can't connect" in value or "cannot connect" in value:
        return "server_not_ready"
    if "no such file or directory" in value and "socket" in value:
        return "socket_missing"
    if "connection refused" in value:
        return "connection_refused"
    if "executable file not found" in value or "mysql: not found" in value:
        return "client_missing"
    return "other_redacted"


def _root_defaults_install(plan, payload):
    """Install the root defaults through stdin; no secret enters argv/output."""
    if not isinstance(payload, bytes) or not payload:
        raise ServiceError("private root defaults payload differs")
    completed = subprocess.run(
        _docker_argv(
            "exec", "-i", plan["database"]["name"], "sh", "-c",
            "umask 077; test ! -e %s && cat > %s && chmod 600 %s"
            % (CONTAINER_ROOT_DEFAULTS, CONTAINER_ROOT_DEFAULTS,
               CONTAINER_ROOT_DEFAULTS),
        ),
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ServiceError(
            "private root defaults installation failed (exit=%d)"
            % completed.returncode
        )


def _root_defaults_remove(plan):
    completed = _run(
        _docker_argv(
            "exec", plan["database"]["name"], "sh", "-c",
            "rm -f -- %s" % CONTAINER_ROOT_DEFAULTS,
        ),
        check=False,
    )
    if completed.returncode != 0:
        raise ServiceError(
            "private root defaults cleanup failed (exit=%d)"
            % completed.returncode
        )


def _write_root_readiness_diagnostic(plan, status, attempts, completed,
                                     category):
    path = (Path(RUNTIME_ROOT) / plan["attempt_id"]
            / ROOT_READINESS_DIAGNOSTIC)
    atomic_json(path, {
        "schema_version": "mysql_xp_root_socket_readiness_diagnostic_v1",
        "status": status,
        "scenario": SCENARIO,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": plan["bundle_id"],
        "attempt_id": plan["attempt_id"],
        "attempts": attempts,
        "last_mysql_returncode": completed.returncode,
        "last_error_category": category,
        "raw_stderr_recorded": False,
        "credential_content_recorded": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })


def _wait_for_database_socket(plan):
    """Wait using private root defaults and save only redacted diagnostics."""
    last = None
    category = "other_redacted"
    for attempt in range(1, DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS + 1):
        readiness = _run(
            _docker_argv(
                "exec", plan["database"]["name"], "mysql",
                "--defaults-extra-file=%s" % CONTAINER_ROOT_DEFAULTS,
                "--protocol=socket", "--batch", "--raw",
                "--skip-column-names", "--execute", "SELECT 1",
            ),
            check=False,
        )
        last = readiness
        category = _root_mysql_error_category(readiness)
        if readiness.returncode == 0 and readiness.stdout.strip() == "1":
            _write_root_readiness_diagnostic(
                plan, "PASS", attempt, readiness, "ready"
            )
            return readiness
        time.sleep(DATABASE_SOCKET_READINESS_POLL_SECONDS)
    _write_root_readiness_diagnostic(
        plan, "FAIL", DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS,
        last, category,
    )
    raise ServiceError(
        "database socket bootstrap readiness failed "
        "(last_mysql_returncode=%d,error_category=%s)"
        % (last.returncode, category)
    )


'''
    text = replace_exact(text, old_wait, new_wait, 1, "authenticated readiness")
    old_bootstrap = '''    _run(plan["database"]["start_argv"])
    _wait_for_database_socket(plan)
    user = credential_before["user"]
    account_host = credential_before["account_host"]
    password = credential_before["_password"]
    sql = _qualification_account_sql(
        user, account_host, password
    )
    _run_private_stdin(
        _docker_argv(
            "exec", "-i", plan["database"]["name"],
            "mysql", "--user=root", "--protocol=socket", "--batch", "--raw",
        ),
        sql,
    )
'''
    new_bootstrap = '''    _run(plan["database"]["start_argv"])
    _root_defaults_install(
        plan, credential_before["_root_defaults_payload"]
    )
    try:
        _wait_for_database_socket(plan)
        user = credential_before["user"]
        account_host = credential_before["account_host"]
        password = credential_before["_password"]
        sql = _qualification_account_sql(
            user, account_host, password
        )
        _run_private_stdin(
            _docker_argv(
                "exec", "-i", plan["database"]["name"], "mysql",
                "--defaults-extra-file=%s" % CONTAINER_ROOT_DEFAULTS,
                "--protocol=socket", "--batch", "--raw",
            ),
            sql,
        )
    finally:
        _root_defaults_remove(plan)
'''
    text = replace_exact(text, old_bootstrap, new_bootstrap, 1,
                         "authenticated provisioning")
    path.write_text(text, encoding="utf-8")


def patch_materials(path):
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "import stat\n",
        "import stat\nimport sys\n",
        1, "materials stdin import",
    )
    text = replace_exact(
        text,
        '    "mysql_xp_node02_successor_bundle_v26": V25_BUNDLE_ID,\n',
        '    "mysql_xp_node02_successor_bundle_v26": V25_BUNDLE_ID,\n'
        '    "mysql_xp_node02_successor_bundle_v27": "' + PARENT_ID + '",\n',
        1, "materials schema map",
    )
    text = replace_exact(
        text,
        'def create_materials(bundle_path, output_root, valid_minutes=25):\n',
        'def create_materials(bundle_path, output_root, valid_minutes=25, '
        'root_password=None):\n',
        1, "materials signature",
    )
    text = replace_exact(
        text,
        '''    credential_directory = output / "private_credentials"
    credential_directory.mkdir(mode=0o700)
''',
        '''    credential_directory = output / "private_credentials"
    credential_directory.mkdir(mode=0o700)
    if (
        not isinstance(root_password, str)
        or not 1 <= len(root_password) <= 256
        or any(ord(character) < 33 or ord(character) > 126
               for character in root_password)
    ):
        raise MaterialError("root password stdin material differs")
    write_private(
        credential_directory / "mysql-root-bootstrap.cnf",
        ("[client]\\nuser=root\\npassword=%s\\n" % root_password).encode("utf-8"),
    )
''',
        1, "root private material",
    )
    text = text.replace(
        '''                "mysql-client.cnf",
                "hybench_xp_qualification.properties",
''',
        '''                "mysql-client.cnf",
                "hybench_xp_qualification.properties",
                "mysql-root-bootstrap.cnf",
''',
    )
    text = replace_exact(
        text,
        '''                "private_credentials/hybench_xp_qualification.properties":
                    sha256(
                        credential_directory
                        / "hybench_xp_qualification.properties"
                    ),
''',
        '''                "private_credentials/hybench_xp_qualification.properties":
                    sha256(
                        credential_directory
                        / "hybench_xp_qualification.properties"
                    ),
                "private_credentials/mysql-root-bootstrap.cnf": sha256(
                    credential_directory / "mysql-root-bootstrap.cnf"
                ),
''',
        1, "materials manifest root hash closure",
    )
    text = replace_exact(
        text,
        '''                    "private_credentials/hybench_xp_qualification.properties":
                        sha256(
                            credential_directory
                            / "hybench_xp_qualification.properties"
                        ),
''',
        '''                    "private_credentials/hybench_xp_qualification.properties":
                        sha256(
                            credential_directory
                            / "hybench_xp_qualification.properties"
                        ),
                    "private_credentials/mysql-root-bootstrap.cnf": sha256(
                        credential_directory / "mysql-root-bootstrap.cnf"
                    ),
''',
        1, "authorization root hash closure",
    )
    text = replace_exact(
        text,
        '''    parser.add_argument("--valid-minutes", type=int, default=25)
    args = parser.parse_args()
    print(json.dumps(
        create_materials(args.bundle, args.output_root, args.valid_minutes),
''',
        '''    parser.add_argument("--valid-minutes", type=int, default=25)
    parser.add_argument("--root-password-stdin", action="store_true")
    args = parser.parse_args()
    if not args.root_password_stdin:
        raise MaterialError("--root-password-stdin is required")
    payload = sys.stdin.buffer.read(258)
    if len(payload) > 257:
        raise MaterialError("root password stdin is too large")
    try:
        root_password = payload.decode("utf-8").rstrip("\\n")
    except UnicodeDecodeError:
        raise MaterialError("root password stdin encoding differs")
    print(json.dumps(
        create_materials(args.bundle, args.output_root, args.valid_minutes,
                         root_password),
''',
        1, "materials stdin CLI",
    )
    path.write_text(text, encoding="utf-8")


def add_v27_schema(path):
    text = path.read_text(encoding="utf-8")
    needle = '            "mysql_xp_node02_successor_bundle_v26",\n'
    if needle in text and '"mysql_xp_node02_successor_bundle_v27"' not in text:
        text = text.replace(
            needle,
            needle + '            "mysql_xp_node02_successor_bundle_v27",\n',
        )
        path.write_text(text, encoding="utf-8")


def relative_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: %s" % path)
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
        elif not path.is_file() and not path.is_dir():
            raise BuildError("special file prohibited: %s" % path)
    return result


def seal_modes(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            executable = bool(stat.S_IMODE(path.stat().st_mode) & 0o111)
            path.chmod(0o555 if executable else 0o444)
        elif path.is_dir():
            path.chmod(0o555)
    # Keep the staging root owner-writable until the atomic directory rename;
    # children are already sealed and the published root is sealed afterward.
    root.chmod(0o755)


def build():
    if not PARENT.is_dir():
        raise BuildError("sealed v26 parent absent")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mysql-xp-v27-", dir=str(OUTPUT_ROOT)) as raw:
        stage = Path(raw) / "stage"
        shutil.copytree(PARENT, stage)
        stage.chmod(0o755)
        for path in stage.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)
        (stage / "bundle_manifest.json").unlink()
        (stage / "bundle_manifest.json.sha256").unlink()
        shutil.copy2(Path(__file__), stage / BUILDER)
        shutil.copy2(ROOT / TEST, stage / TEST)
        invalidation = {
            "schema_version": "mysql_xp_bundle_invalidation_v1",
            "status": "INVALIDATED_NEVER_LAUNCHED_NEVER_AUTHORIZED",
            "bundle_id": "8114aad3f11be7f5fc817f14fc53132d07ef18f46e25256a09a08a4517f85ebd",
            "reason": "initial local v27 build did not include its root-auth regression test in the sealed closure",
            "formal_r0_credit": 0,
            "accepted_round_credit": 0,
        }
        (stage / "invalidated_8114_unverified_root_auth_bundle.json").write_bytes(
            canonical(invalidation)
        )
        patch_service(stage / SERVICE)
        patch_materials(stage / MATERIALS)
        for relative in (
            "launch/mysql_xp_node02_formal10_materializer_v1.py",
            "qualification/mysql_distributed_live_readback_qualification_v1.py",
            "qualification/mysql_xp_qualification_authority_v1.py",
        ):
            add_v27_schema(stage / relative)
        service_sha = sha_file(stage / SERVICE)
        contract_path = stage / CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["adopted_service"]["sha256"] = service_sha
        contract["adopted_service"]["bootstrap_root_auth"] = {
            "attempt_private_file": "private_credentials/mysql-root-bootstrap.cnf",
            "directory_mode": "0700",
            "file_mode": "0600",
            "transport": "docker_exec_stdin_to_ephemeral_defaults_extra_file",
            "credential_literal_in_manifest_argv_stdout": False,
            "ephemeral_container_file_removed_after_provisioning": True,
            "redacted_last_mysql_returncode_and_error_category_required": True,
        }
        contract["private_credential_successor"]["bootstrap_root_defaults"] = {
            "filename": "mysql-root-bootstrap.cnf",
            "user": "root",
            "password_source": "private_stdin_only",
            "password_in_manifest_command_or_log": False,
        }
        contract_path.write_bytes(canonical(contract))
        files = relative_files(stage)
        parent_manifest = json.loads(
            (PARENT / "bundle_manifest.json").read_text(encoding="utf-8")
        )
        manifest = dict(parent_manifest)
        manifest.pop("bundle_id", None)
        manifest["schema_version"] = "mysql_xp_node02_successor_bundle_v27"
        manifest["predecessor_bundle_id"] = PARENT_ID
        manifest["supersedes_bundle_id"] = PARENT_ID
        manifest["invalidated_empty_root_bootstrap_bundle_id"] = PARENT_ID
        manifest["service_sha256"] = service_sha
        manifest["files"] = files
        bundle_id = sha_bytes(canonical(manifest))
        manifest["bundle_id"] = bundle_id
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha_file(manifest_path),
            encoding="ascii",
        )
        destination = OUTPUT_ROOT / bundle_id
        seal_modes(stage)
        if destination.exists():
            if sha_file(destination / "bundle_manifest.json") != sha_file(manifest_path):
                raise BuildError("existing destination identity differs")
            return destination
        stage.parent.chmod(0o700)
        OUTPUT_ROOT.chmod(0o755)
        os.replace(str(stage), str(destination))
        destination.chmod(0o555)
        return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({
        "status": "SEALED_LOCAL_ONLY_NOT_LAUNCHED",
        "bundle": str(result),
        "bundle_id": result.name,
        "manifest_sha256": sha_file(result / "bundle_manifest.json"),
    }, sort_keys=True))
