import sqlite3
import tempfile
import threading
import unittest

from concurrent.futures import ThreadPoolExecutor
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

    # --------------------------------------------------
    # 原有 Repository 测试
    # --------------------------------------------------

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

        another_repo = TaskRepository(self.db_path)

        task = another_repo.get_task(self.video_id)

        self.assertIsNotNone(task)

        self.assertEqual(
            task["status"],
            "queued",
        )

    # --------------------------------------------------
    # v0.9.2-A Worker 调度测试
    # --------------------------------------------------

    def test_claim_next_task(self):
        """Worker 能成功领取 queued 任务。"""

        self.create_task()

        task = self.repo.claim_next_task(
            worker_id="worker-A",
            lease_seconds=60,
        )

        self.assertIsNotNone(task)

        self.assertEqual(
            task["status"],
            "processing",
        )

        self.assertEqual(
            task["worker_id"],
            "worker-A",
        )

        self.assertEqual(
            task["fencing_token"],
            1,
        )

        self.assertIsNotNone(
            task["lease_expires_at"]
        )

    def test_no_queued_task(self):
        """没有 queued 任务时返回 None。"""

        self.create_task()

        self.repo.claim_next_task(
            worker_id="worker-A",
        )

        result = self.repo.claim_next_task(
            worker_id="worker-B",
        )

        self.assertIsNone(result)

    def test_two_workers_only_one_claims(self):
        """两个 Worker 竞争一个任务，只能有一个成功。"""

        self.create_task()

        barrier = threading.Barrier(2)

        def claim(worker_id):
            barrier.wait()

            return self.repo.claim_next_task(
                worker_id=worker_id,
                lease_seconds=60,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    claim,
                    "worker-A",
                ),
                executor.submit(
                    claim,
                    "worker-B",
                ),
            ]

            results = [
                future.result()
                for future in futures
            ]

        successful = [
            result
            for result in results
            if result is not None
        ]

        self.assertEqual(len(successful), 1)

        self.assertIn(
            successful[0]["worker_id"],
            ["worker-A", "worker-B"],
        )

    def test_heartbeat(self):
        """当前 Worker 可以续租。"""

        self.create_task()

        task = self.repo.claim_next_task(
            "worker-A",
            lease_seconds=60,
        )

        old_expiry = task["lease_expires_at"]

        result = self.repo.heartbeat(
            video_id=self.video_id,
            worker_id="worker-A",
            fencing_token=task["fencing_token"],
            lease_seconds=120,
        )

        self.assertTrue(result)

        updated = self.repo.get_task(self.video_id)

        self.assertGreater(
            updated["lease_expires_at"],
            old_expiry,
        )

    def test_wrong_worker_cannot_heartbeat(self):
        """其他 Worker 不能续租。"""

        self.create_task()

        task = self.repo.claim_next_task(
            "worker-A",
        )

        result = self.repo.heartbeat(
            self.video_id,
            "worker-B",
            task["fencing_token"],
        )

        self.assertFalse(result)

    def test_expired_lease_cannot_heartbeat(self):
        """已经过期的租约不能被续活。"""

        self.create_task()

        task = self.repo.claim_next_task(
            "worker-A",
            lease_seconds=10,
            now=100,
        )

        result = self.repo.heartbeat(
            self.video_id,
            "worker-A",
            task["fencing_token"],
            lease_seconds=60,
            now=111,
        )

        self.assertFalse(result)

    def test_recover_expired_task(self):
        """租约过期后可以回收任务。"""

        self.create_task()

        self.repo.claim_next_task(
            "worker-A",
            lease_seconds=10,
            now=100,
        )

        recovered = self.repo.recover_expired_tasks(
            now=111,
        )

        self.assertEqual(
            recovered,
            [self.video_id],
        )

        task = self.repo.get_task(self.video_id)

        self.assertEqual(
            task["status"],
            "queued",
        )

        self.assertIsNone(task["worker_id"])
        self.assertIsNone(task["lease_expires_at"])

        # 系统回收不等于用户手动重试。
        self.assertEqual(task["attempt"], 0)

    def test_fencing_token_blocks_old_worker(self):
        """
        Worker A 租约过期后，
        Worker B 重新领取并获得新 token。

        Worker A 不能继续更新任务。
        """

        self.create_task()

        task_a = self.repo.claim_next_task(
            "worker-A",
            lease_seconds=10,
            now=100,
        )

        self.repo.recover_expired_tasks(
            now=111,
        )

        task_b = self.repo.claim_next_task(
            "worker-B",
            lease_seconds=60,
            now=112,
        )

        self.assertEqual(
            task_b["fencing_token"],
            task_a["fencing_token"] + 1,
        )

        with self.assertRaises(PermissionError):
            self.repo.update_status(
                self.video_id,
                "fusing",
                "旧 Worker 尝试更新",
                worker_id="worker-A",
                fencing_token=task_a["fencing_token"],
            )

        current = self.repo.get_task(self.video_id)

        self.assertEqual(
            current["worker_id"],
            "worker-B",
        )

        self.assertEqual(
            current["status"],
            "processing",
        )

    def test_release_task(self):
        """当前 Worker 可以主动释放任务。"""

        self.create_task()

        task = self.repo.claim_next_task(
            "worker-A",
            lease_seconds=60,
        )

        result = self.repo.release_task(
            self.video_id,
            "worker-A",
            task["fencing_token"],
        )

        self.assertTrue(result)

        updated = self.repo.get_task(self.video_id)

        self.assertEqual(
            updated["status"],
            "queued",
        )

        self.assertIsNone(updated["worker_id"])

    def test_wrong_worker_cannot_release(self):
        """其他 Worker 不能释放当前任务。"""

        self.create_task()

        task = self.repo.claim_next_task(
            "worker-A",
        )

        result = self.repo.release_task(
            self.video_id,
            "worker-B",
            task["fencing_token"],
        )

        self.assertFalse(result)

        updated = self.repo.get_task(self.video_id)

        self.assertEqual(
            updated["worker_id"],
            "worker-A",
        )

    def test_invalid_lease_arguments(self):
        """租约参数必须合法。"""

        self.create_task()

        with self.assertRaises(ValueError):
            self.repo.claim_next_task(
                "worker-A",
                lease_seconds=0,
            )

        with self.assertRaises(ValueError):
            self.repo.claim_next_task(
                "",
                lease_seconds=60,
            )

    def test_old_database_schema_migration(self):
        """旧版数据库可以自动增加 Worker 字段。"""

        old_db = (
            Path(self.temp_dir.name) / "old.db"
        )

        conn = sqlite3.connect(old_db)

        conn.executescript("""
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL
            );

            CREATE TABLE tasks (
                video_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(video_id)
                    REFERENCES videos(video_id)
            );
        """)

        conn.close()

        migrated_repo = TaskRepository(old_db)

        with migrated_repo.connection() as conn:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(tasks)"
                ).fetchall()
            }

        self.assertIn("worker_id", columns)
        self.assertIn("lease_expires_at", columns)
        self.assertIn("heartbeat_at", columns)
        self.assertIn("fencing_token", columns)


if __name__ == "__main__":
    unittest.main()
