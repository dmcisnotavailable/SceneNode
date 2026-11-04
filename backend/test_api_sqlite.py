import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
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

        self.repo_patcher = patch.object(
            main,
            "TASK_REPOSITORY",
            self.repo,
        )
        self.repo_patcher.start()
        self.addCleanup(self.repo_patcher.stop)

        self.client = TestClient(main.app)

    def create_video(self, video_id):
        video_path = (
            self.upload_dir / f"{video_id}.mp4"
        )
        video_path.write_bytes(b"mock-video")

        self.repo.create_video_task(
            video_id=video_id,
            filename="test.mp4",
            file_path=str(video_path.resolve()),
            size=10,
        )

        return video_path

    # --------------------------------------------------
    # 上传
    # --------------------------------------------------

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

        self.assertEqual(
            task["status"],
            "queued",
        )

        self.assertEqual(
            task["attempt"],
            0,
        )

        self.assertEqual(
            video["filename"],
            "test.mp4",
        )

        self.assertTrue(
            Path(video["file_path"]).is_file()
        )

        # API 进程不应该直接执行 Pipeline。
        self.assertFalse(
            hasattr(main, "process_video")
        )

        # 新任务不再创建 status.json。
        self.assertFalse(
            (
                self.job_dir
                / video_id
                / "status.json"
            ).exists()
        )

    def test_upload_rejects_non_mp4(self):
        response = self.client.post(
            "/api/videos",
            files={
                "file": (
                    "test.txt",
                    b"not-video",
                    "text/plain",
                )
            },
        )

        self.assertEqual(response.status_code, 400)

    # --------------------------------------------------
    # 视频列表
    # --------------------------------------------------

    def test_video_list_reads_sqlite(self):
        video_id = "a" * 32
        self.create_video(video_id)

        response = self.client.get("/api/videos")

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["total"], 1)

        self.assertEqual(
            data["videos"][0]["video_id"],
            video_id,
        )

        self.assertEqual(
            data["videos"][0]["status"],
            "queued",
        )

    # --------------------------------------------------
    # 单视频状态
    # --------------------------------------------------

    def test_status_reads_sqlite(self):
        video_id = "b" * 32
        self.create_video(video_id)

        response = self.client.get(
            f"/api/videos/{video_id}"
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.json()["status"],
            "queued",
        )

    def test_completed_status_reads_chapters_json(self):
        video_id = "e" * 32
        self.create_video(video_id)

        for status in (
            "processing",
            "fusing",
            "generating",
            "completed",
        ):
            self.repo.update_status(
                video_id,
                status,
                status,
            )

        job_path = self.job_dir / video_id
        job_path.mkdir(parents=True)

        chapters = {
            "chapters": [
                {
                    "start": 0,
                    "title": "旅行开始",
                    "summary": "开始旅行",
                }
            ]
        }

        (job_path / "chapters.json").write_text(
            json.dumps(chapters, ensure_ascii=False),
            encoding="utf-8",
        )

        response = self.client.get(
            f"/api/videos/{video_id}"
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.json()["status"],
            "completed",
        )

        self.assertEqual(
            response.json()["chapters"],
            chapters["chapters"],
        )

    # --------------------------------------------------
    # 重试：只入队，不执行 Pipeline
    # --------------------------------------------------

    def test_retry_only_requeues_task(self):
        video_id = "c" * 32
        self.create_video(video_id)

        self.repo.update_status(
            video_id,
            "processing",
        )

        self.repo.update_status(
            video_id,
            "failed",
            "模拟失败",
        )

        response = self.client.post(
            f"/api/videos/{video_id}/retry"
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(
            data["status"],
            "queued",
        )

        self.assertEqual(
            data["attempt"],
            1,
        )

        task = self.repo.get_task(video_id)

        self.assertEqual(
            task["status"],
            "queued",
        )

        self.assertEqual(
            task["attempt"],
            1,
        )

    def test_retry_rejects_non_failed_task(self):
        video_id = "d" * 32
        self.create_video(video_id)

        response = self.client.post(
            f"/api/videos/{video_id}/retry"
        )

        self.assertEqual(response.status_code, 409)

        self.assertEqual(
            self.repo.get_task(video_id)["status"],
            "queued",
        )

    def test_retry_rejects_missing_video_file(self):
        video_id = "f" * 32

        self.repo.create_video_task(
            video_id=video_id,
            filename="missing.mp4",
            file_path=str(
                self.upload_dir / f"{video_id}.mp4"
            ),
            size=10,
        )

        self.repo.update_status(
            video_id,
            "processing",
        )

        self.repo.update_status(
            video_id,
            "failed",
        )

        response = self.client.post(
            f"/api/videos/{video_id}/retry"
        )

        self.assertEqual(response.status_code, 404)

    # --------------------------------------------------
    # 搜索：状态来自 SQLite
    # --------------------------------------------------

    def test_search_uses_sqlite_status(self):
        video_id = "9" * 32
        self.create_video(video_id)

        for status in (
            "processing",
            "fusing",
            "generating",
            "completed",
        ):
            self.repo.update_status(
                video_id,
                status,
                status,
            )

        job_path = self.job_dir / video_id
        job_path.mkdir(parents=True)

        transcript = {
            "language": "zh",
            "segments": [
                {
                    "start": 0.0,
                    "end": 3.0,
                    "text": "今天去海边旅行",
                }
            ],
        }

        (job_path / "transcript.json").write_text(
            json.dumps(
                transcript,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch.object(
            main,
            "search_chunks",
            return_value=[
                {
                    "start": 0.0,
                    "end": 3.0,
                    "text": "今天去海边旅行",
                }
            ],
        ) as mock_search:

            response = self.client.get(
                f"/api/videos/{video_id}/search",
                params={
                    "q": "海边",
                    "mode": "keyword",
                },
            )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.json()["total"],
            1,
        )

        mock_search.assert_called_once()

    def test_search_rejects_uncompleted_task(self):
        video_id = "8" * 32
        self.create_video(video_id)

        response = self.client.get(
            f"/api/videos/{video_id}/search",
            params={"q": "海边"},
        )

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
