import json
import tempfile
import unittest
from pathlib import Path

from migrate_json_to_sqlite import migrate
from task_repository import TaskRepository


class TestMigration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        root = Path(self.temp_dir.name)

        self.job_dir = root / "jobs"
        self.upload_dir = root / "uploads"

        self.job_dir.mkdir()
        self.upload_dir.mkdir()

        self.repo = TaskRepository(root / "test.db")
        self.video_id = "a" * 32

        video_path = self.upload_dir / f"{self.video_id}.mp4"
        video_path.write_bytes(b"test-video")

        job_path = self.job_dir / self.video_id
        job_path.mkdir()

        (job_path / "status.json").write_text(
            json.dumps({
                "video_id": self.video_id,
                "status": "completed",
                "message": "分析完成",
            }),
            encoding="utf-8",
        )

        (job_path / "metadata.json").write_text(
            json.dumps({
                "filename": "旅行.mp4",
                "uploaded_at": "2026-10-28T10:00:00+00:00",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    def run_migration(self):
        return migrate(
            repository=self.repo,
            job_dir=self.job_dir,
            upload_dir=self.upload_dir,
        )

    def test_import_legacy_task(self):
        result = self.run_migration()

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["errors"], [])

        task = self.repo.get_task(self.video_id)

        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["filename"], "旅行.mp4")

    def test_import_is_idempotent(self):
        first = self.run_migration()
        second = self.run_migration()

        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["skipped"], 1)

        self.assertEqual(
            len(self.repo.list_tasks()),
            1,
        )

    def test_existing_task_not_overwritten(self):
        self.run_migration()

        # 数据库已经存在时，旧 JSON 不应覆盖它。
        status_path = (
            self.job_dir / self.video_id / "status.json"
        )
        status_path.write_text(
            json.dumps({
                "status": "failed",
                "message": "旧状态",
            }),
            encoding="utf-8",
        )

        self.run_migration()

        task = self.repo.get_task(self.video_id)
        self.assertEqual(task["status"], "completed")

    def test_invalid_status_is_reported(self):
        status_path = (
            self.job_dir / self.video_id / "status.json"
        )
        status_path.write_text(
            json.dumps({
                "status": "unknown",
                "message": "",
            }),
            encoding="utf-8",
        )

        result = self.run_migration()

        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIsNone(
            self.repo.get_task(self.video_id)
        )


if __name__ == "__main__":
    unittest.main()
