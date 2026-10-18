#!/usr/bin/env python3
"""Build the deterministic native-XP runner with a 300-second drain cap."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/java/com/hybench/HyBenchAdaptiveMain.java"
NATIVE = (
    ROOT / "artifacts/mysql_xp_native_iq1_iq7_successor_v2"
    / "e3e3a32e6b095e10d5e229be5ec211131f626374bc82764d98ad3f910344e017"
    / "runtime/HyBench-native-IQ1-IQ7-v2.jar"
)
DESTINATION = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime"
    / "hybench-adaptive-runner-v4.jar"
)
SOURCE_SHA256 = "efaae729d77fda26cd0867bd9a9560dc792576fd4a4d99bb5ec2cdefaad3e78d"
NATIVE_SHA256 = "c1c353eedf8bc0a4d4f9a3f92c46c6e2c31241991e789a10f800ab887bee50a3"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if sha(SOURCE) != SOURCE_SHA256 or sha(NATIVE) != NATIVE_SHA256:
        raise RuntimeError("HyBench v4 build predecessor binding differs")
    if DESTINATION.exists() or DESTINATION.is_symlink():
        raise FileExistsError(f"fresh HyBench v4 artifact required: {DESTINATION}")
    source = SOURCE.read_text(encoding="utf-8")
    old_gate = "gracefulQuiescenceSeconds > 120"
    old_message = "graceful quiescence in [30,120] seconds and stop-file"
    if source.count(old_gate) != 1 or source.count(old_message) != 1:
        raise RuntimeError("HyBench v3 Java drain gate differs")
    successor = source.replace(
        old_gate, "gracefulQuiescenceSeconds > 300",
    ).replace(
        old_message, "graceful quiescence in [30,300] seconds and stop-file",
    )
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mysql-hybench-v4-") as raw:
        temporary = Path(raw)
        java = temporary / "src/com/hybench/HyBenchAdaptiveMain.java"
        java.parent.mkdir(parents=True)
        java.write_text(successor, encoding="utf-8")
        classes = temporary / "classes"
        classes.mkdir()
        subprocess.run(
            ["/usr/bin/javac", "-cp", str(NATIVE), "-d", str(classes), str(java)],
            cwd=ROOT, check=True, timeout=120,
        )
        subprocess.run(
            [
                "/usr/bin/jar", "--create",
                "--date=2026-08-20T00:00:00Z",
                f"--file={DESTINATION}",
                "-C", str(classes), "com/hybench/HyBenchAdaptiveMain.class",
            ],
            cwd=ROOT, check=True, timeout=60,
        )
    print(f"{sha(DESTINATION)}  {DESTINATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
