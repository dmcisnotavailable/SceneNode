import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from task_repository import TaskRepository


class TestAssetTaskRepository(unittest.TestCase):
    """素材分析任务表的 Repository 方法测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.repo = TaskRepository(
            self.root / "test.db"
        )

        # 创建项目和素材，供任务表外键引用。
        self.project = self.repo.create_project("测试旅行")

        self.asset = self.repo.save_project_assets(
            self.project["project_id"],
            [{
                "filename": "video.mp4",
                "source_path": str(
                    self.root / "video.mp4"
                ),
                "file_size": 100,
                "file_mtime": 1000.0,
                "duration": 30.0,
                "width": 1920,
                "height": 1080,
                "recorded_at": None,
                "fingerprint": "abc",
            }],
        )

        self.assets = self.repo.list_project_assets(
            self.project["project_id"]
        )
        self.asset_id = self.assets[0]["asset_id"]

    def test_create_asset_task(self):
        """创建素材分析任务并进入 queued 状态。"""

        task = self.repo.create_asset_task(self.asset_id)

        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["attempt"], 0)
        self.assertEqual(
            task["fencing_token"],
            0,
        )

    def test_create_asset_task_is_idempotent(self):
        """重复为同一素材创建任务不会产生新任务。"""

        first = self.repo.create_asset_task(self.asset_id)
        second = self.repo.create_asset_task(self.asset_id)

        self.assertEqual(
            first["task_id"],
            second["task_id"],
        )

    def test_claim_and_complete_asset_task(self):
        """Worker 领取、分析、完成任务的全流程。"""

        self.repo.create_asset_task(self.asset_id)

        task = self.repo.claim_next_asset_task(
            "worker-A",
            lease_seconds=60,
        )

        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "processing")
        self.assertEqual(task["worker_id"], "worker-A")
        self.assertEqual(task["fencing_token"], 1)
        self.assertEqual(
            task["asset_source_path"],
            str(self.root / "video.mp4"),
        )

        self.repo.update_asset_task_status(
            self.asset_id,
            "completed",
            "素材分析完成",
            worker_id="worker-A",
            fencing_token=1,
        )

        final = self.repo.get_asset_task(self.asset_id)

        self.assertEqual(final["status"], "completed")
        self.assertIsNone(final["worker_id"])

    def test_stale_worker_cannot_update(self):
        """旧租约持有者不能再更新任务状态。"""

        self.repo.create_asset_task(self.asset_id)

        self.repo.claim_next_asset_task(
            "worker-A",
            lease_seconds=60,
            now=100,
        )

        # 模拟租约过期后任务被回收并被 Worker B 领取。
        self.repo.recover_expired_asset_tasks(now=200)

        task_b = self.repo.claim_next_asset_task(
            "worker-B",
            lease_seconds=60,
            now=201,
        )

        self.assertEqual(
            task_b["fencing_token"],
            2,
        )

        # Worker A 的旧 token 更新必须被拒绝。
        with self.assertRaises(PermissionError):
            self.repo.update_asset_task_status(
                self.asset_id,
                "completed",
                "过期更新",
                worker_id="worker-A",
                fencing_token=1,
            )

    def test_recover_expired_asset_tasks(self):
        """租约过期的任务被回收后可以重新领取。"""

        self.repo.create_asset_task(self.asset_id)

        self.repo.claim_next_asset_task(
            "worker-A",
            lease_seconds=10,
            now=100,
        )

        recovered = self.repo.recover_expired_asset_tasks(
            now=120,
        )

        self.assertEqual(recovered, [self.asset_id])

        task = self.repo.get_asset_task(self.asset_id)
        self.assertEqual(task["status"], "queued")

    def test_update_asset_analysis(self):
        """更新素材分析结果字段。"""

        self.repo.update_asset_analysis(
            self.asset_id,
            thumbnail_path="D:/x/thumbnail.jpg",
            scene="海边",
            description="海边日落",
            analysis_status="completed",
        )

        asset = self.repo.get_project_asset_by_id(
            self.asset_id
        )

        self.assertEqual(
            asset["thumbnail_path"],
            "D:/x/thumbnail.jpg",
        )
        self.assertEqual(asset["scene"], "海边")
        self.assertEqual(
            asset["description"],
            "海边日落",
        )
        self.assertEqual(
            asset["analysis_status"],
            "completed",
        )

    def test_update_asset_analysis_rejects_unknown(self):
        """未知素材更新时抛出 KeyError。"""

        with self.assertRaises(KeyError):
            self.repo.update_asset_analysis(
                "missing",
                scene="x",
            )


class TestAssetAnalysisService(unittest.TestCase):
    """素材分析服务的流程测试（模拟视觉模型）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)

    def test_analyze_asset_summarizes_frames(self):
        """分析服务汇总帧描述生成素材概述。"""

        import json

        from asset_analysis import analyze_asset

        fake_frames = [
            {
                "frame_id": 0,
                "timestamp": 2.5,
                "scene": "海边",
                "description": "海边的沙滩上有几个人",
            },
            {
                "frame_id": 1,
                "timestamp": 7.5,
                "scene": "海边",
                "description": "有人在海上冲浪",
            },
        ]

        def fake_describe(frames_dir, limit, max_workers):
            frames_dir = Path(frames_dir)
            frames_dir.mkdir(parents=True, exist_ok=True)

            saved = {
                "model": "test",
                "frames": fake_frames,
            }

            (frames_dir / "visual_descriptions.json").write_text(
                json.dumps(saved, ensure_ascii=False),
                encoding="utf-8",
            )

            return saved

        with patch(
            "asset_analysis.extract_frames",
            return_value={
                "video_duration": 15.0,
                "frames": [],
            },
        ), patch(
            "asset_analysis.describe_frames",
            side_effect=fake_describe,
        ):
            result = analyze_asset(
                str(self.root / "video.mp4"),
                str(self.root / "output"),
            )

        self.assertEqual(result["scene"], "海边")
        self.assertIn("沙滩", result["description"])
        self.assertIn("冲浪", result["description"])

        # 概述文件已保存到输出目录。
        summary = self.root / "output" / "asset_description.json"
        self.assertTrue(summary.is_file())


if __name__ == "__main__":
    unittest.main()
