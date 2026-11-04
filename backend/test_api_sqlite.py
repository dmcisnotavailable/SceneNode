import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
import pipeline
from task_repository import TaskRepository


class TestSQLiteAPI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root = Path(self.temp_dir.name)
        self.upload_dir = self.root / "uploads"
        self.job_dir = self.root / "jobs"

        self.upload_dir.mkdir()
        self.job_dir.mkdir()

        self.repo = TaskRepository(
            self.root / "test.db"
        )

        self.upload_patcher = patch.object(
            main,
            "UPLOAD_DIR",
            self.upload_dir,
        )
        self.upload_patcher.start()
        self.addCleanup(self.upload_patcher.stop)

        self.job_patcher = patch.object(
            main,
            "JOB_DIR",
            self.job_dir,
        )
        self.job_patcher.start()
        self.addCleanup(self.job_patcher.stop)

        self.main_repo_patcher = patch.object(
            main,
            "TASK_REPOSITORY",
            self.repo,
        )
        self.main_repo_patcher.start()
        self.addCleanup(self.main_repo_patcher.stop)

        self.pipeline_repo_patcher = patch.object(
            pipeline,
            "TASK_REPOSITORY",
            self.repo,
        )
        self.pipeline_repo_patcher.start()
        self.addCleanup(self.pipeline_repo_patcher.stop)

        self.process_patcher = patch.object(
            main,
            "process_video",
        )
        self.mock_process = self.process_patcher.start()
        self.addCleanup(self.process_patcher.stop)

        self.client = TestClient(main.app)

    def test_upload_creates_sqlite_records(self):
        response = self.client.post(
            "/api/videos",
            files={
                "file": (
                    "test.mp4",
                    b"mock-video",
                    "video/mp4",
                )
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()
        video_id = data["video_id"]

        task = self.repo.get_task(video_id)
        video = self.repo.get_video(video_id)

        self.assertEqual(task["status"], "queued")
        self.assertEqual(video["filename"], "test.mp4")
        self.assertTrue(
            Path(video["file_path"]).is_file()
        )

        self.mock_process.assert_called_once()

    def test_video_list_reads_sqlite(self):
        video_path = self.upload_dir / "test.mp4"
        video_path.write_bytes(b"mock-video")

        self.repo.create_video_task(
            video_id="a" * 32,
            filename="test.mp4",
            file_path=str(video_path),
            size=10,
        )

        response = self.client.get("/api/videos")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(
            response.json()["videos"][0]["status"],
            "queued",
        )

    def test_status_reads_sqlite(self):
        video_path = self.upload_dir / "test.mp4"
        video_path.write_bytes(b"mock-video")

        video_id = "b" * 32

        self.repo.create_video_task(
            video_id=video_id,
            filename="test.mp4",
            file_path=str(video_path),
            size=10,
        )

        response = self.client.get(
            f"/api/videos/{video_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["status"],
            "queued",
        )

    def test_retry_reads_sqlite(self):
        video_id = "c" * 32
        video_path = self.upload_dir / f"{video_id}.mp4"
        video_path.write_bytes(b"mock-video")

        self.repo.create_video_task(
            video_id=video_id,
            filename="test.mp4",
            file_path=str(video_path),
            size=10,
        )

        self.repo.update_status(video_id, "processing")
        self.repo.update_status(video_id, "failed")

        response = self.client.post(
            f"/api/videos/{video_id}/retry"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["status"],
            "queued",
        )

        self.assertEqual(
            self.repo.get_task(video_id)["status"],
            "queued",
        )

        self.mock_process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
