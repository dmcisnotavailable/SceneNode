import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from task_repository import TaskRepository
from task_state import InvalidStateTransition
from worker import Worker
from worker_config import WorkerConfig


class TestWorker(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root = Path(self.temp_dir.name)

        self.db_path = self.root / "test.db"
        self.video_path = self.root / "test.mp4"

        self.video_path.write_bytes(b"mock-video")

        self.repo = TaskRepository(self.db_path)

        self.video_id = "a" * 32

        self.repo.create_video_task(
            video_id=self.video_id,
            filename="test.mp4",
            file_path=str(self.video_path),
            size=self.video_path.stat().st_size,
        )

        self.config = WorkerConfig(
            worker_id="worker-test",
            lease_seconds=60,
            heartbeat_interval=30,
            poll_interval=0.1,
            idle_log_interval=10,
        )

        self.worker = Worker(
            repository=self.repo,
            config=self.config,
        )

    def test_worker_claims_and_processes_task(self):
        """Worker 领取任务并执行 Pipeline。"""

        def fake_process_video(
            video_id,
            video_path,
            worker_id=None,
            fencing_token=None,
        ):
            self.assertEqual(video_id, self.video_id)
            self.assertEqual(worker_id, "worker-test")
            self.assertEqual(fencing_token, 1)

            self.repo.update_status(
                video_id,
                "fusing",
                "融合中",
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

            self.repo.update_status(
                video_id,
                "generating",
                "生成中",
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

            self.repo.update_status(
                video_id,
                "completed",
                "分析完成",
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

        with patch(
            "worker.process_video",
            side_effect=fake_process_video,
        ) as mock_process:

            result = self.worker.run_once()

        self.assertTrue(result)
        mock_process.assert_called_once()

        task = self.repo.get_task(self.video_id)

        self.assertEqual(task["status"], "completed")
        self.assertIsNone(task["worker_id"])
        self.assertIsNone(task["lease_expires_at"])
        self.assertEqual(task["fencing_token"], 1)

    def test_worker_returns_false_when_queue_empty(self):
        """没有待处理任务时不调用 Pipeline。"""

        self.repo.claim_next_task(
            "another-worker",
            lease_seconds=60,
        )

        with patch("worker.process_video") as mock_process:
            result = self.worker.run_once()

        self.assertFalse(result)
        mock_process.assert_not_called()

    def test_worker_marks_unexpected_error_as_failed(self):
        """Pipeline 意外抛出异常时，Worker 更新 failed。"""

        with patch(
            "worker.process_video",
            side_effect=RuntimeError("模拟 Worker 异常"),
        ):
            result = self.worker.run_once()

        self.assertTrue(result)

        task = self.repo.get_task(self.video_id)

        self.assertEqual(task["status"], "failed")
        self.assertIsNone(task["worker_id"])
        self.assertIsNone(task["lease_expires_at"])

    def test_worker_fails_when_video_missing(self):
        """原始视频文件丢失时，不执行 Pipeline。"""

        self.video_path.unlink()

        with patch("worker.process_video") as mock_process:
            result = self.worker.run_once()

        self.assertTrue(result)
        mock_process.assert_not_called()

        task = self.repo.get_task(self.video_id)

        self.assertEqual(task["status"], "failed")

    def test_worker_does_not_claim_same_task_twice(self):
        """任务完成后再次轮询，不会重复执行。"""

        def fake_process_video(
            video_id,
            video_path,
            worker_id=None,
            fencing_token=None,
        ):
            self.repo.update_status(
                video_id,
                "fusing",
                "融合中",
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

            self.repo.update_status(
                video_id,
                "generating",
                "生成中",
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

            self.repo.update_status(
                video_id,
                "completed",
                "完成",
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

        with patch(
            "worker.process_video",
            side_effect=fake_process_video,
        ) as mock_process:

            first = self.worker.run_once()
            second = self.worker.run_once()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(mock_process.call_count, 1)

    def test_expired_task_can_be_reclaimed(self):
        """Worker A 过期后，Worker B 可以领取。"""

        task_a = self.repo.claim_next_task(
            "worker-A",
            lease_seconds=10,
            now=100,
        )

        self.assertEqual(task_a["fencing_token"], 1)

        recovered = self.repo.recover_expired_tasks(
            now=111,
        )

        self.assertEqual(
            recovered,
            [self.video_id],
        )

        task_b = self.repo.claim_next_task(
            "worker-B",
            lease_seconds=60,
            now=112,
        )

        self.assertEqual(
            task_b["worker_id"],
            "worker-B",
        )

        self.assertEqual(
            task_b["fencing_token"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
