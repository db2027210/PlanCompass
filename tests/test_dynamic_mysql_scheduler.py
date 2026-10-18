import unittest

from dynamic_mysql_scheduler import GlobalTaskPool, Worker


class DynamicSchedulerTests(unittest.TestCase):
    def test_idle_worker_claims_oldest_compatible_global_task(self):
        pool = GlobalTaskPool([
            {"run_id": "a", "benchmark": "job"},
            {"run_id": "b", "benchmark": "hybench_ap"},
            {"run_id": "c", "benchmark": "hybench_tp"},
        ])
        hy = Worker("hy-1", frozenset({"hybench_ap", "hybench_tp"}))
        job = Worker("job-1", frozenset({"job"}))
        self.assertEqual(hy and pool.claim(hy)["run_id"], "b")
        self.assertEqual(pool.claim(job)["run_id"], "a")
        self.assertEqual(pool.claim(hy)["run_id"], "c")
        self.assertEqual(pool.remaining(), 0)

    def test_worker_claim_also_respects_bottleneck_profile(self):
        pool = GlobalTaskPool([
            {"run_id":"cpu", "benchmark":"hybench_xp", "bottleneck":"cpu"},
            {"run_id":"io", "benchmark":"hybench_xp", "bottleneck":"io"},
        ])
        worker = Worker("io-1", frozenset({"hybench_xp"}), frozenset({"io"}))
        self.assertEqual(pool.claim(worker)["run_id"], "io")

    def test_command_is_bound_after_worker_claim(self):
        worker = Worker.from_mapping({
            "worker_id": "hy-2",
            "benchmarks": ["hybench_ap"],
            "command_context": {"port": 13402, "pid": 4321},
        })
        command = worker.render_command(
            ["runner", "--port", "{port}", "--pid", "{pid}", "--run", "{run_id}",
             "--worker", "{worker_id}"],
            {"run_id": "A7-r1"},
        )
        self.assertEqual(command, [
            "runner", "--port", "13402", "--pid", "4321", "--run", "A7-r1",
            "--worker", "hy-2",
        ])


if __name__ == "__main__":
    unittest.main()
