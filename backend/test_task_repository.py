import sqlite3
import tempfile
import unittest
from pathlib import Path

from task_repository import TaskRepository
from task_state import InvalidStateTransition


class TestTaskRepository(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.db_path = (
            Path(self.temp_dir.name) / "test.db"
        )
        self.repo = TaskRepository(self.db_path)

        self.video_id = "test-video-001"

    def create_task(self, video_id=None):
        return self.repo.create_video_task(
            video_id=video_id or self.video_id,
            filename="test.mp4",
            file_path="/tmp/test.mp4",
            size=1024,
        )

    def test_database_created(self):
        self.assertTrue(self.db_path.is_file())

    def test_create_video_task(self):
        task = self.create_task()

        self.assertEqual(
            task["video_id"],
            self.video_id,
        )
        self.assertEqual(
            task["status"],
            "queued",
        )
        self.assertEqual(
            task["attempt"],
            0,
        )

    def test_get_missing_task(self):
        self.assertIsNone(
            self.repo.get_task("missing")
        )

    def test_normal_lifecycle(self):
        self.create_task()

        for status in [
            "processing",
            "fusing",
            "generating",
            "completed",
        ]:
            self.repo.update_status(
                self.video_id,
                status,
                f"进入 {status}",
            )

        task = self.repo.get_task(self.video_id)

        self.assertEqual(
            task["status"],
            "completed",
        )

    def test_invalid_transition_rollback(self):
        self.create_task()

        with self.assertRaises(
            InvalidStateTransition
        ):
            self.repo.update_status(
                self.video_id,
                "completed",
                "跳过处理阶段",
            )

        task = self.repo.get_task(self.video_id)

        self.assertEqual(
            task["status"],
            "queued",
        )

    def test_retry_increases_attempt(self):
        self.create_task()

        self.repo.update_status(
            self.video_id,
            "processing",
        )
        self.repo.update_status(
            self.video_id,
            "failed",
        )
        self.repo.update_status(
            self.video_id,
            "queued",
        )

        task = self.repo.get_task(self.video_id)

        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["attempt"], 1)

    def test_duplicate_video_id(self):
        self.create_task()

        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            self.create_task()

        tasks = self.repo.list_tasks()
        self.assertEqual(len(tasks), 1)

    def test_list_tasks_by_status(self):
        self.create_task("video-a")
        self.create_task("video-b")

        self.repo.update_status(
            "video-a",
            "processing",
        )

        queued = self.repo.list_tasks(
            status="queued",
        )
        processing = self.repo.list_tasks(
            status="processing",
        )

        self.assertEqual(len(queued), 1)
        self.assertEqual(len(processing), 1)
        self.assertEqual(
            processing[0]["video_id"],
            "video-a",
        )

    def test_update_missing_task(self):
        with self.assertRaises(KeyError):
            self.repo.update_status(
                "missing",
                "processing",
            )

    def test_persistence_across_instances(self):
        self.create_task()

        # 模拟另一个进程重新打开同一个数据库。
        another_repo = TaskRepository(self.db_path)

        task = another_repo.get_task(self.video_id)

        self.assertIsNotNone(task)
        self.assertEqual(
            task["status"],
            "queued",
        )


if __name__ == "__main__":
    unittest.main()
