import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline


class TestPipelineRecovery(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root = Path(self.temp_dir.name)
        self.job_dir = self.root / "jobs"
        self.upload_dir = self.root / "uploads"

        self.job_dir.mkdir()
        self.upload_dir.mkdir()

        self.video_id = "a" * 32
        self.job_path = self.job_dir / self.video_id
        self.job_path.mkdir()

        self.video_path = (
            self.upload_dir / f"{self.video_id}.mp4"
        )
        self.video_path.write_bytes(b"mock-video")

        self.generate_calls = 0

        # 每个 Patch 只启动一次，并注册自动清理。
        self.patches = [
            patch.object(
                pipeline,
                "JOB_DIR",
                self.job_dir,
            ),
            patch.object(
                pipeline,
                "has_audio_stream",
                return_value=True,
            ),
            patch.object(
                pipeline.subprocess,
                "run",
                side_effect=self.fake_ffmpeg,
            ),
            patch.object(
                pipeline,
                "extract_frames",
                side_effect=self.fake_frames,
            ),
            patch.object(
                pipeline,
                "describe_frames",
                side_effect=self.fake_describe,
            ),
            patch.object(
                pipeline,
                "fuse_multimodal",
                side_effect=self.fake_fuse,
            ),
            patch.object(
                pipeline,
                "transcribe_audio",
                side_effect=self.fake_transcribe,
            ),
            patch.object(
                pipeline,
                "generate_chapters",
                side_effect=self.fake_generate,
            ),
            patch.dict(
                "os.environ",
                {
                    "PIPELINE_PARALLEL": "0",
                    "VISION_WORKERS": "1",
                    "LLM_MODEL": "test-model",
                },
            ),
        ]

        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

        pipeline.update_status(
            self.video_id,
            "queued",
            "等待分析",
        )

    def read_status(self):
        return json.loads(
            (
                self.job_path / "status.json"
            ).read_text(encoding="utf-8")
        )

    def fake_ffmpeg(self, *args, **kwargs):
        audio_path = Path(args[0][-1])
        audio_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        audio_path.write_bytes(b"mock-wav")

        return type(
            "Completed",
            (),
            {"stdout": "", "stderr": ""},
        )()

    def fake_transcribe(
        self,
        audio_path,
        output_path,
        video_id=None,
        metrics=None,
    ):
        Path(output_path).write_text(
            json.dumps(
                {
                    "language": "zh",
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 3.0,
                            "text": "今天去海边旅行",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def fake_generate(self, timeline_path, output_path):
        self.generate_calls += 1

        if self.generate_calls == 1:
            raise RuntimeError("模拟 LLM 超时")

        Path(output_path).write_text(
            json.dumps(
                {
                    "chapters": [
                        {
                            "start": 0.0,
                            "title": "海边旅行",
                            "summary": "介绍海边旅行内容",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def fake_frames(
        self,
        video_path,
        frames_dir,
        count=6,
    ):
        frames_dir = Path(frames_dir)
        frames_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        manifest = {
            "video_duration": 60.0,
            "frames": [],
        }

        for index in range(count):
            name = f"frame_{index:03d}.jpg"
            (frames_dir / name).write_bytes(b"mock-jpeg")

            manifest["frames"].append({
                "frame_id": index,
                "timestamp": float(index * 10),
                "image_path": name,
            })

        (frames_dir / "frames.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        return manifest

    def fake_describe(
        self,
        frames_dir,
        limit=6,
        max_workers=1,
    ):
        frames_dir = Path(frames_dir)
        output_path = (
            frames_dir / "visual_descriptions.json"
        )

        frames = [
            {
                "frame_id": index,
                "timestamp": float(index * 10),
                "description": f"可见海边场景 {index}",
                "scene": "海边",
                "visible_objects": ["海"],
            }
            for index in range(limit)
        ]

        result = {
            "model": "test-vision",
            "frames": frames,
        }

        output_path.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return result

    def fake_fuse(
        self,
        transcript_path,
        visual_path,
        output_path,
        video_duration=None,
    ):
        transcript = json.loads(
            Path(transcript_path).read_text(
                encoding="utf-8"
            )
        )

        visual = json.loads(
            Path(visual_path).read_text(
                encoding="utf-8"
            )
        )

        events = []

        for segment in transcript["segments"]:
            events.append({
                "start": segment["start"],
                "end": segment["end"],
                "source": "audio",
                "text": segment["text"],
            })

        for frame in visual["frames"]:
            events.append({
                "start": frame["timestamp"],
                "end": frame["timestamp"],
                "source": "visual",
                "text": frame["description"],
            })

        events.sort(
            key=lambda event: event["start"]
        )

        for index, event in enumerate(events):
            event["event_id"] = index

        result = {
            "video_duration": video_duration,
            "events": events,
        }

        Path(output_path).write_text(
            json.dumps(
                result,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return result

    def test_retry_reuses_transcript(self):
        """
        第一次 LLM 失败；
        第二次恢复时必须跳过 Whisper。
        """

        # 第一次执行：模拟 LLM 超时。
        pipeline.process_video(
            self.video_id,
            self.video_path,
        )

        self.assertEqual(
            self.read_status()["status"],
            "failed",
        )

        self.assertTrue(
            (
                self.job_path / "checkpoint.json"
            ).is_file()
        )

        # 模拟用户重新提交失败任务。
        pipeline.update_status(
            self.video_id,
            "queued",
            "重新排队",
        )

        # 第二次执行：如果调用 Whisper，立即报错。
        with patch.object(
            pipeline,
            "transcribe_audio",
            side_effect=AssertionError(
                "重试时不应重新调用 Whisper"
            ),
        ):
            pipeline.process_video(
                self.video_id,
                self.video_path,
            )

        self.assertEqual(
            self.read_status()["status"],
            "completed",
        )

        self.assertEqual(
            self.generate_calls,
            2,
        )

        chapters = json.loads(
            (
                self.job_path / "chapters.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            len(chapters["chapters"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
