#!/usr/bin/env python3
"""TPC-C v11 attempt credential provisioning and config materialization.

``provision-password`` is the only credential source: it creates a random
attempt-local 0600 file after a sealed, no-network temporary MySQL bootstrap
has reset root with ``--skip-grant-tables --skip-networking``.  No historical
or cross-host secret is read, logged, returned, or bundled.
"""
from __future__ import annotations
import argparse, json, os, secrets, stat, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_tpcc_config_materialize_v1 as base  # noqa: E402

CLIENT_UID = 1008
CLIENT_GID = 1008

def _require_host_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise base.ConfigError(
            "TPC-C credential/config materialization requires effective uid/gid 0"
        )

def _attempt_owner(root: Path) -> tuple[int, int]:
    value = root.stat()
    if (
        root.is_symlink()
        or not root.is_dir()
        or stat.S_IMODE(value.st_mode) != 0o700
    ):
        raise base.ConfigError("attempt root must be an exact 0700 directory")
    return value.st_uid, value.st_gid


def _assert_attempt_owned_regular(
    path: Path, uid: int, gid: int, label: str
) -> None:
    value = path.stat()
    if (
        path.is_symlink()
        or not path.is_file()
        or value.st_uid != uid
        or value.st_gid != gid
        or stat.S_IMODE(value.st_mode) != 0o600
    ):
        raise base.ConfigError(label + " ownership/mode differs")


def _load_authority(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise base.ConfigError("credential provisioning authority is absent")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise base.ConfigError(
            "credential provisioning authority is malformed"
        ) from error
    if not isinstance(value, dict):
        raise base.ConfigError("credential provisioning authority must be an object")
    return value


def _write_password_atomically(
    password_file: Path, password: str, uid: int, gid: int
) -> None:
    """Persist the recoverable attempt secret before any reset mutation.

    A failed reset must leave this 0600 attempt-local secret available for
    authorized recovery, but must not leave a provisioning authority record.
    """
    temporary = password_file.with_name(
        "." + password_file.name + "." + secrets.token_hex(12)
    )
    descriptor = os.open(
        str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(password + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.chown(temporary, uid, gid)
        os.replace(temporary, password_file)
        parent_descriptor = os.open(str(password_file.parent), os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def provision(bundle: Path, image_id: str, attempt_id: str, datadir: Path, password_file: Path, output: Path) -> dict:
    _require_host_root()
    manifest = base.verify_bundle(bundle)
    root = Path(base.RUNTIME_ROOT) / attempt_id
    expected = root / "datadir" / "datadir"
    if datadir.resolve() != expected.resolve() or password_file != root / "mysql-password" or output != root / "credential_provisioning.json":
        raise base.ConfigError("v11 credential paths must be attempt-owned inner datadir paths")
    if password_file.exists() or output.exists() or not (datadir / "ibdata1").is_file() or not image_id.startswith("sha256:"):
        raise base.ConfigError("credential provisioning preconditions differ")
    attempt_uid, attempt_gid = _attempt_owner(root)
    password = secrets.token_urlsafe(36)
    _write_password_atomically(password_file, password, attempt_uid, attempt_gid)
    _assert_attempt_owned_regular(
        password_file, attempt_uid, attempt_gid, "attempt password file"
    )
    name = "ra_mysql_tpcc_v11_credential_" + attempt_id
    create = ["docker", "run", "-d", "--name", name, "--network", "none", "--user", "999:999", "--cpuset-cpus", "21", "--cpuset-mems", "1", "--memory", "1073741824", "--memory-swap", "1073741824", "--pids-limit", "128", "--mount", f"type=bind,src={datadir},dst=/var/lib/mysql", image_id, "mysqld", "--skip-grant-tables=ON", "--skip-networking=ON", "--datadir=/var/lib/mysql", "--skip-log-bin"]
    started = False
    try:
        run = subprocess.run(create, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if run.returncode: raise base.ConfigError("sealed temporary credential bootstrap failed")
        started = True
        ready = False
        for _ in range(180):
            check = subprocess.run(["docker", "exec", name, "mysqladmin", "-uroot", "ping", "--silent"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if check.returncode == 0: ready = True; break
            time.sleep(1)
        if not ready: raise base.ConfigError("temporary credential bootstrap did not become ready")
        quoted_password = password.replace("'", "''")
        sql = (
            "FLUSH PRIVILEGES; "
            "ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password BY '"
            + quoted_password
            + "'; CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '"
            + quoted_password
            + "'; ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '"
            + quoted_password
            + "';"
        )
        reset = subprocess.run(
            ["docker", "exec", "-i", name, "mysql", "-uroot"],
            input=sql,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if reset.returncode:
            raise base.ConfigError("temporary root reset failed")
    finally:
        if started:
            subprocess.run(["docker", "stop", "--time", "120", name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            subprocess.run(["docker", "rm", name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    result = {"schema_version":"mysql_tpcc_attempt_credential_provisioning_v1","status":"PASS_SEALED_TEMPORARY_SKIP_GRANT_RESET","bundle_id":manifest["bundle_id"],"attempt_id":attempt_id,"host_effective_uid_gid":[0,0],"datadir":str(datadir),"credential_file":str(password_file),"credential_file_mode":"0600","secret_source":"new_random_attempt_secret_not_logged_or_hashed","temporary_bootstrap":{"network":"none","skip_grant_tables":True,"skip_networking":True,"removed_after_reset":True},"formal_launch_authorized":False,"accepted_round_authorized":False}
    base._atomic_write(output, base.canonical(result), 0o600)
    os.chown(output, attempt_uid, attempt_gid)
    os.chmod(output, 0o600)
    _assert_attempt_owned_regular(
        output, attempt_uid, attempt_gid, "credential provisioning authority"
    )
    return result


def materialize(
    bundle: Path, attempt_id: str, config_dir: Path,
    password_file: Path, output: Path,
) -> dict:
    _require_host_root()
    root = Path(base.RUNTIME_ROOT) / attempt_id
    uid, gid = _attempt_owner(root)
    credential_authority_path = root / "credential_provisioning.json"
    credential_authority = _load_authority(credential_authority_path)
    if (
        credential_authority.get("schema_version")
        != "mysql_tpcc_attempt_credential_provisioning_v1"
        or credential_authority.get("status")
        != "PASS_SEALED_TEMPORARY_SKIP_GRANT_RESET"
        or credential_authority.get("bundle_id")
        != base.verify_bundle(bundle).get("bundle_id")
        or credential_authority.get("attempt_id") != attempt_id
        or credential_authority.get("host_effective_uid_gid") != [0, 0]
        or credential_authority.get("credential_file") != str(password_file)
        or credential_authority.get("credential_file_mode") != "0600"
        or credential_authority.get("formal_launch_authorized") is not False
        or credential_authority.get("accepted_round_authorized") is not False
    ):
        raise base.ConfigError(
            "root credential provisioning authority differs"
        )
    _assert_attempt_owned_regular(
        password_file, uid, gid, "attempt password file"
    )
    _assert_attempt_owned_regular(
        credential_authority_path, uid, gid,
        "credential provisioning authority",
    )
    value = base.materialize(
        bundle, attempt_id, config_dir, password_file, output,
        target_uid=CLIENT_UID, target_gid=CLIENT_GID,
    )
    value = {
        **value,
        "host_effective_uid_gid": [0, 0],
        "credential_provisioning_authority_sha256":
            base.sha256(credential_authority_path),
    }
    base._atomic_write(
        output, base.canonical(value), 0o600, CLIENT_UID, CLIENT_GID
    )
    files = value.get("files")
    if (
        value.get("config_dir_uid") != CLIENT_UID
        or value.get("config_dir_gid") != CLIENT_GID
        or value.get("config_dir_mode") != "0700"
        or value.get("ownership_transactional_before_publish") is not True
        or output.stat().st_uid != CLIENT_UID
        or output.stat().st_gid != CLIENT_GID
        or stat.S_IMODE(output.stat().st_mode) != 0o600
        or not isinstance(files, dict)
        or set(files) != {"mysql.cnf", "tpcc.xml"}
        or any(
            evidence.get("uid") != CLIENT_UID
            or evidence.get("gid") != CLIENT_GID
            or evidence.get("mode") != "0600"
            for evidence in files.values()
        )
    ):
        raise base.ConfigError(
            "transactional client config ownership readback differs"
        )
    return value


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("mode", choices=("provision-password","materialize")); p.add_argument("--bundle",type=Path,required=True);p.add_argument("--image-id");p.add_argument("--attempt-id",required=True);p.add_argument("--datadir",type=Path);p.add_argument("--mysql-password-file",type=Path,required=True);p.add_argument("--config-dir",type=Path);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    if a.mode=="provision-password": value=provision(a.bundle,a.image_id,a.attempt_id,a.datadir,a.mysql_password_file,a.output)
    else:
        value=materialize(
            a.bundle,a.attempt_id,a.config_dir,a.mysql_password_file,a.output
        )
    print(json.dumps({"status":value["status"],"bundle_id":value["bundle_id"],"attempt_id":value["attempt_id"]},sort_keys=True));return 0
if __name__=="__main__":
    try: raise SystemExit(main())
    except (OSError,ValueError,base.ConfigError) as exc: sys.stderr.write("NOT_READY: "+str(exc)+"\n");raise SystemExit(2)
