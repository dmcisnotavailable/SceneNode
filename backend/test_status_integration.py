import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from task_state import InvalidStateTransition


class TestStatusIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.job_dir = Path(self.temp_dir.name)

        self.patcher = patch.object(
            pipeline,
            "JOB_DIR",
            self.job_dir,
        )
        self.patcher.start()

        self.video_id = "test_video"

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def read_status(self):
        path = (
            self.job_dir
            / self.video_id
            / "status.json"
        )
        return json.loads(
            path.read_text(encoding="utf-8")
        )

    def test_normal_lifecycle(self):
        stages = [
            "queued",
            "processing",
            "fusing",
            "generating",
            "completed",
        ]

        for stage in stages:
            pipeline.update_status(
                self.video_id,
                stage,
                f"进入 {stage}",
            )

        result = self.read_status()

        self.assertEqual(
            result["status"],
            "completed",
        )

    def test_invalid_transition_keeps_old_status(self):
        pipeline.update_status(
            self.video_id,
            "queued",
            "等待分析",
        )

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

    def test_initial_state_must_be_queued(self):
        with self.assertRaises(
            InvalidStateTransition
        ):
            pipeline.update_status(
                self.video_id,
                "processing",
                "直接开始",
            )

    def test_duplicate_status_rejected(self):
        pipeline.update_status(
            self.video_id,
            "queued",
            "等待分析",
        )

        with self.assertRaises(
            InvalidStateTransition
        ):
            pipeline.update_status(
                self.video_id,
                "queued",
                "再次排队",
            )

    def test_corrupted_status_file(self):
        job_path = self.job_dir / self.video_id
        job_path.mkdir(parents=True)

        (job_path / "status.json").write_text(
            "{invalid json",
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError):
            pipeline.update_status(
                self.video_id,
                "processing",
                "开始处理",
            )


if __name__ == "__main__":
    unittest.main()
