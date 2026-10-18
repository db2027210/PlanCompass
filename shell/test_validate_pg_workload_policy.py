#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import zipfile
from pathlib import Path

from validate_pg_workload_policy import validate_job, validate_xp


def write_props(path: Path, *, forbidden: bool = False) -> None:
    lines = [
        "xtpclient=16",
        "xapclient=8",
        "at1_percent=40",
        "at2_percent=30",
        "at3_percent=15",
        "at4_percent=5",
        "at5_percent=7",
        "at6_percent=3",
        "url=jdbc:postgresql://127.0.0.1:1/db",
    ]
    if forbidden:
        lines.append("ap_query_sequence=1,2")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "source.props"
        generated = root / "generated.props"
        statement = root / "stmt.toml"
        jar = root / "clean.jar"
        write_props(source)
        write_props(generated)
        statement.write_text("[IQ-1]\nsql=\"select 1\"\n", encoding="utf-8")
        class_bytes = b"execIQ1 execIQ2 execIQ3 execIQ4 execIQ5 execIQ6"
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("com/hybench/workload/APClient.class", class_bytes)
        import hashlib

        args = argparse.Namespace(
            source_props=str(source),
            generated_props=str(generated),
            statement_file=str(statement),
            statement_sha256=hashlib.sha256(statement.read_bytes()).hexdigest(),
            jar=str(jar),
        )
        assert validate_xp(args)["status"] == "PASS"
        write_props(generated, forbidden=True)
        try:
            validate_xp(args)
            raise AssertionError("forbidden selector was accepted")
        except ValueError:
            pass

        sql_dir = root / "job"
        sql_dir.mkdir()
        for index in range(1, 114):
            (sql_dir / f"{index}a.sql").write_text("select 1;\n", encoding="utf-8")
        job_result = validate_job(argparse.Namespace(sql_dir=str(sql_dir)))
        assert job_result["sql_count"] == 113
        (sql_dir / "113a.sql").unlink()
        try:
            validate_job(argparse.Namespace(sql_dir=str(sql_dir)))
            raise AssertionError("112-query JOB set was accepted")
        except ValueError:
            pass
    print("4/4 policy tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
