"""Global compatible-worker scheduler for isolated MySQL experiment commands.

Workers never own a prebuilt queue.  Whenever one finishes, it atomically
claims the oldest still-pending compatible task from the shared pool.
"""

from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class Worker:
    worker_id: str
    benchmarks: frozenset[str]
    bottlenecks: frozenset[str] = field(default_factory=lambda: frozenset({"cpu", "memory", "io", "none"}))
    command_context: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Worker":
        worker_id = str(value.get("worker_id", "")).strip()
        benchmarks = frozenset(str(item).lower() for item in value.get("benchmarks", []))
        if not worker_id or not benchmarks:
            raise ValueError("each worker needs worker_id and at least one benchmark")
        bottlenecks = frozenset(str(item).lower() for item in value.get(
            "bottlenecks", ["cpu", "memory", "io", "none"]
        ))
        if not bottlenecks:
            raise ValueError("worker needs at least one bottleneck type")
        context = value.get("command_context", {})
        if not isinstance(context, Mapping):
            raise ValueError("worker command_context must be an object")
        return cls(worker_id, benchmarks, bottlenecks, dict(context))

    def render_command(self, command: Sequence[str], task: Mapping[str, Any]) -> list[str]:
        """Bind endpoint/PID only after this worker has claimed the task."""
        values = {str(key): value for key, value in task.items()}
        values.update({str(key): value for key, value in self.command_context.items()})
        values["worker_id"] = self.worker_id
        try:
            return [token.format_map(values) for token in command]
        except KeyError as exc:
            raise ValueError(f"missing command template value: {exc.args[0]}") from exc


class GlobalTaskPool:
    def __init__(self, tasks: Sequence[Mapping[str, Any]]) -> None:
        self._pending = deque(dict(task) for task in tasks)
        self._lock = threading.Lock()

    def claim(self, worker: Worker) -> dict[str, Any] | None:
        """Claim the oldest compatible task; no task is statically assigned."""
        with self._lock:
            for index, task in enumerate(self._pending):
                if (str(task.get("benchmark", "")).lower() in worker.benchmarks
                        and str(task.get("bottleneck", "none")).lower() in worker.bottlenecks):
                    del self._pending[index]
                    return task
        return None

    def remaining(self) -> int:
        with self._lock:
            return len(self._pending)


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def _runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), cwd=cwd, text=True, capture_output=True,
                          check=False)


def execute_dynamic(
    tasks: Sequence[Mapping[str, Any]], workers: Sequence[Worker], output: Path, *,
    runner: Runner = _runner,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    pool = GlobalTaskPool(tasks)
    results: list[dict[str, Any]] = []
    result_lock = threading.Lock()

    def worker_loop(worker: Worker) -> None:
        while True:
            task = pool.claim(worker)
            if task is None:
                return
            command = task.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                completed = subprocess.CompletedProcess([], 2, "", "invalid command argv")
                elapsed = 0.0
            else:
                started = time.monotonic()
                try:
                    bound_command = worker.render_command(command, task)
                    completed = runner(bound_command, Path(task.get("cwd", ".")).resolve())
                except ValueError as exc:
                    completed = subprocess.CompletedProcess(command, 2, "", str(exc))
                elapsed = time.monotonic() - started
            row = {
                "run_id": task.get("run_id"), "benchmark": task.get("benchmark"),
                "worker_id": worker.worker_id, "returncode": completed.returncode,
                "elapsed_s": elapsed, "stdout_tail": (completed.stdout or "")[-2000:],
                "stderr_tail": (completed.stderr or "")[-2000:],
            }
            with result_lock:
                results.append(row)
                with (output / "dispatch_results.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")

    with ThreadPoolExecutor(max_workers=len(workers)) as executor:
        futures = [executor.submit(worker_loop, worker) for worker in workers]
        for future in futures:
            future.result()
    summary = {
        "policy": "global_oldest_compatible_task_first_work_stealing",
        "task_count": len(tasks), "worker_count": len(workers),
        "completed": len(results), "failed": sum(row["returncode"] != 0 for row in results),
        "unclaimed": pool.remaining(), "results": results,
    }
    (output / "dispatch_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--workers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    tasks = json.loads(args.tasks.read_text())
    workers = [Worker.from_mapping(row) for row in json.loads(args.workers.read_text())]
    if not args.execute:
        print(json.dumps({"mode": "dry_run", "policy": "global_work_stealing",
                          "tasks": len(tasks), "workers": len(workers)}, indent=2))
        return 0
    print(json.dumps(execute_dynamic(tasks, workers, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
