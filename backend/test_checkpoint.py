import json
import tempfile
import unittest
from pathlib import Path

from checkpoint import CheckpointManager


class TestCheckpoint(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.job_path = Path(self.temp_dir.name)
        self.manager = CheckpointManager(
            self.job_path,
            "test_video",
        )
        self.config = {
            "model": "small",
            "beam_size": 1,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_transcript(self, segments=None):
        if segments is None:
            segments = [
                {
                    "start": 0.0,
                    "end": 3.0,
                    "text": "今天我们去海边",
                }
            ]

        path = self.job_path / "transcript.json"

        path.write_text(
            json.dumps(
                {
                    "language": "zh",
                    "segments": segments,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return path

    def test_save_and_reuse(self):
        """有效产物保存后可以复用。"""

        self.write_transcript()

        self.manager.save(
            "transcript",
            self.config,
        )

        self.assertTrue(
            self.manager.is_valid(
                "transcript",
                self.config,
            )
        )

    def test_missing_artifact(self):
        """产物不存在时不能保存 Checkpoint。"""

        with self.assertRaises(ValueError):
            self.manager.save(
                "transcript",
                self.config,
            )

    def test_empty_transcript(self):
        """空转写结果不能作为成功产物。"""

        self.write_transcript(segments=[])

        with self.assertRaises(ValueError):
            self.manager.save(
                "transcript",
                self.config,
            )

    def test_modified_artifact(self):
        """产物被修改后，哈希校验失败。"""

        path = self.write_transcript()

        self.manager.save(
            "transcript",
            self.config,
        )

        path.write_text(
            path.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )

        self.assertFalse(
            self.manager.is_valid(
                "transcript",
                self.config,
            )
        )

    def test_config_changed(self):
        """模型配置变化时不复用旧产物。"""

        self.write_transcript()

        self.manager.save(
            "transcript",
            self.config,
        )

        self.assertFalse(
            self.manager.is_valid(
                "transcript",
                {
                    "model": "base",
                    "beam_size": 1,
                },
            )
        )

    def test_corrupted_checkpoint(self):
        """Checkpoint JSON 损坏时拒绝复用。"""

        self.write_transcript()

        self.manager.save(
            "transcript",
            self.config,
        )

        self.manager.checkpoint_path.write_text(
            "{invalid json",
            encoding="utf-8",
        )

        self.assertFalse(
            self.manager.is_valid(
                "transcript",
                self.config,
            )
        )

    def test_unknown_stage(self):
        """未知阶段必须报错。"""

        with self.assertRaises(ValueError):
            self.manager.save(
                "unknown",
                {},
            )

    def test_completed_stages(self):
        """能够读取已记录完成的阶段。"""

        self.write_transcript()

        self.manager.save(
            "transcript",
            self.config,
        )

        self.assertEqual(
            self.manager.get_completed_stages(),
            ["transcript"],
        )

    def test_chapters_validation(self):
        """章节产物结构正确时可以保存。"""

        chapters_path = self.job_path / "chapters.json"

        chapters_path.write_text(
            json.dumps(
                {
                    "chapters": [
                        {
                            "start": 0.0,
                            "title": "出发",
                            "summary": "开始旅行",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.manager.save(
            "chapters",
            {"model": "test-llm"},
        )

        self.assertTrue(
            self.manager.is_valid(
                "chapters",
                {"model": "test-llm"},
            )
        )


if __name__ == "__main__":
    unittest.main()
