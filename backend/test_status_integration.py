import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from task_repository import TaskRepository
from task_state import InvalidStateTransition


class TestStatusIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        root = Path(self.temp_dir.name)
        self.job_dir = root / "jobs"
        self.job_dir.mkdir()

        self.repo = TaskRepository(
            root / "test.db"
        )

        self.video_id = "test_video"

        self.repo.create_video_task(
            video_id=self.video_id,
            filename="test.mp4",
            file_path=str(root / "test.mp4"),
            size=100,
        )

        self.patcher = patch.object(
            pipeline,
            "TASK_REPOSITORY",
            self.repo,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

        self.job_patcher = patch.object(
            pipeline,
            "JOB_DIR",
            self.job_dir,
        )
        self.job_patcher.start()
        self.addCleanup(self.job_patcher.stop)

    def read_status(self):
        return self.repo.get_task(self.video_id)

    def test_normal_lifecycle(self):
        for status in (
            "processing",
            "fusing",
            "generating",
            "completed",
        ):
            pipeline.update_status(
                self.video_id,
                status,
                status,
            )

        self.assertEqual(
            self.read_status()["status"],
            "completed",
        )

    def test_invalid_transition_keeps_old_status(self):
        with self.assertRaises(
            InvalidStateTransition
        ):
            pipeline.update_status(
                self.video_id,
                "completed",
                "跳过所有阶段",
            )

        self.assertEqual(
            self.read_status()["status"],
            "queued",
        )

    def test_failed_can_be_requeued(self):
        pipeline.update_status(
            self.video_id,
            "processing",
            "正在分析",
        )
        pipeline.update_status(
            self.video_id,
            "failed",
            "分析失败",
        )
        pipeline.update_status(
            self.video_id,
            "queued",
            "重新排队",
        )

        self.assertEqual(
            self.read_status()["status"],
            "queued",
        )

    def test_failed_cannot_jump_to_processing(self):
        pipeline.update_status(
            self.video_id,
            "processing",
            "正在分析",
        )
        pipeline.update_status(
            self.video_id,
            "failed",
            "分析失败",
        )

        with self.assertRaises(
            InvalidStateTransition
        ):
            pipeline.update_status(
                self.video_id,
                "processing",
                "直接重启",
            )

    def test_completed_cannot_be_retried(self):
        for status in (
            "processing",
            "fusing",
            "generating",
            "completed",
        ):
            pipeline.update_status(
                self.video_id,
                status,
                status,
            )

        with self.assertRaises(
            InvalidStateTransition
        ):
            pipeline.update_status(
                self.video_id,
                "queued",
                "重新排队",
            )

    def test_retry_attempt_is_persisted(self):
        pipeline.update_status(
            self.video_id,
            "processing",
            "正在分析",
        )
        pipeline.update_status(
            self.video_id,
            "failed",
            "分析失败",
        )
        pipeline.update_status(
            self.video_id,
            "queued",
            "重新排队",
        )

        task = self.read_status()

        self.assertEqual(task["attempt"], 1)


if __name__ == "__main__":
    unittest.main()
