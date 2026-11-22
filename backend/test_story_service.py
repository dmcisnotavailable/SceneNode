import tempfile
import unittest

from pathlib import Path

from project_timeline import build_timeline, group_assets_by_day
from task_repository import TaskRepository


class StoryServiceTestCase(unittest.TestCase):
    """v1.0.3 时间线与故事主题的 Repository 和分组逻辑测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.repo = TaskRepository(
            self.root / "test.db"
        )

        # 创建项目和带拍摄时间的素材，供故事、
        # 片段和标签表外键引用。
        self.project = self.repo.create_project("张家界旅行")

        self.repo.save_project_assets(
            self.project["project_id"],
            [
                {
                    "filename": "day1.mp4",
                    "source_path": str(self.root / "day1.mp4"),
                    "file_size": 100,
                    "file_mtime": 1000.0,
                    "duration": 60.0,
                    "width": 1920,
                    "height": 1080,
                    "recorded_at": "2024-05-01T10:00:00",
                    "fingerprint": "fp-day1",
                },
                {
                    "filename": "day2.mp4",
                    "source_path": str(self.root / "day2.mp4"),
                    "file_size": 200,
                    "file_mtime": 2000.0,
                    "duration": 60.0,
                    "width": 1920,
                    "height": 1080,
                    "recorded_at": "2024-05-02T09:00:00",
                    "fingerprint": "fp-day2",
                },
            ],
        )

        self.assets = self.repo.list_project_assets(
            self.project["project_id"]
        )

        self.clip = self.repo.create_clip(
            self.assets[0]["asset_id"],
            10.0,
            30.0,
            "山顶合影",
        )

    def test_clip_belongs_to_multiple_stories(self):
        """同一片段可以关联多个故事。"""

        story_a = self.repo.create_story(
            self.project["project_id"],
            "山区风景",
        )

        story_b = self.repo.create_story(
            self.project["project_id"],
            "朋友互动",
        )

        self.repo.add_clip_to_story(
            story_a["story_id"],
            self.clip["clip_id"],
        )

        # 同一片段加入另一个故事不受影响。
        self.repo.add_clip_to_story(
            story_b["story_id"],
            self.clip["clip_id"],
        )

        clips_a = self.repo.list_story_clips(
            story_a["story_id"]
        )

        clips_b = self.repo.list_story_clips(
            story_b["story_id"]
        )

        self.assertEqual(len(clips_a), 1)
        self.assertEqual(len(clips_b), 1)

        # 重复添加同一片段幂等，不会产生重复记录。
        self.repo.add_clip_to_story(
            story_a["story_id"],
            self.clip["clip_id"],
        )

        self.assertEqual(
            len(self.repo.list_story_clips(story_a["story_id"])),
            1,
        )

    def test_delete_story_keeps_assets(self):
        """删除故事不会删除片段和原始素材。"""

        story = self.repo.create_story(
            self.project["project_id"],
            "缆车体验",
        )

        self.repo.add_clip_to_story(
            story["story_id"],
            self.clip["clip_id"],
        )

        self.repo.delete_story(story["story_id"])

        # 故事删除后片段仍在。
        self.assertIsNotNone(
            self.repo.get_clip(self.clip["clip_id"])
        )

        # 原始素材也保持不变。
        assets = self.repo.list_project_assets(
            self.project["project_id"]
        )

        self.assertEqual(len(assets), 2)

    def test_delete_clip_cascades_relations(self):
        """删除片段时故事关联和标签关系一并清理。"""

        story = self.repo.create_story(
            self.project["project_id"],
            "山区风景",
        )

        self.repo.add_clip_to_story(
            story["story_id"],
            self.clip["clip_id"],
        )

        tag = self.repo.create_tag(
            self.project["project_id"],
            "天门山",
            "location",
        )

        self.repo.tag_clip(
            self.clip["clip_id"],
            tag["tag_id"],
        )

        self.repo.delete_clip(self.clip["clip_id"])

        # 片段删除后故事关联消失，故事本身保留。
        self.assertEqual(
            self.repo.list_story_clips(story["story_id"]),
            [],
        )

        self.assertIsNotNone(
            self.repo.get_story(story["story_id"])
        )

        # 标签本身保留，但片段上的标签关系已清理。
        self.assertEqual(
            self.repo.get_clip_tag(
                self.clip["clip_id"],
                tag["tag_id"],
            ),
            None,
        )

    def test_confirmed_tag_not_overwritten(self):
        """用户确认的标签不会被后续 AI 分析覆盖。"""

        tag = self.repo.create_tag(
            self.project["project_id"],
            "天门山",
            "location",
            source="ai",
        )

        # AI 先打了一个低置信度标签。
        self.repo.tag_clip(
            self.clip["clip_id"],
            tag["tag_id"],
            confidence=0.4,
        )

        # 用户确认该标签。
        self.repo.confirm_clip_tag(
            self.clip["clip_id"],
            tag["tag_id"],
        )

        # 后续 AI 分析尝试覆盖置信度，应保持用户确认状态。
        self.repo.tag_clip(
            self.clip["clip_id"],
            tag["tag_id"],
            confidence=0.9,
        )

        relation = self.repo.get_clip_tag(
            self.clip["clip_id"],
            tag["tag_id"],
        )

        self.assertEqual(relation["confirmed"], 1)
        self.assertEqual(relation["confidence"], 0.4)

    def test_unknown_recorded_at_grouped(self):
        """没有拍摄时间的素材归入 unknown 组并正常浏览。"""

        self.repo.save_project_assets(
            self.project["project_id"],
            [
                {
                    "filename": "no-time.mp4",
                    "source_path": str(
                        self.root / "no-time.mp4"
                    ),
                    "file_size": 50,
                    "file_mtime": 3000.0,
                    "duration": 10.0,
                    "width": 1920,
                    "height": 1080,
                    "recorded_at": None,
                    "fingerprint": "fp-no-time",
                },
            ],
        )

        assets = self.repo.list_project_assets(
            self.project["project_id"]
        )

        groups = group_assets_by_day(assets)

        # 两天有效日期加一个 unknown 组。
        self.assertEqual(len(groups), 3)

        self.assertEqual(groups[-1]["day_key"], "unknown")
        self.assertEqual(len(groups[-1]["assets"]), 1)
        self.assertEqual(
            groups[-1]["assets"][0]["filename"],
            "no-time.mp4",
        )

        # 有效日期按时间先后排序。
        self.assertEqual(groups[0]["day_key"], "2024-05-01")
        self.assertEqual(groups[1]["day_key"], "2024-05-02")

    def test_build_timeline_applies_custom_titles(self):
        """时间线分组使用用户自定义的天标题。"""

        day = self.repo.upsert_project_day(
            self.project["project_id"],
            1,
            "2024-05-01",
            "抵达张家界",
        )

        timeline = build_timeline(
            self.assets,
            [day],
        )

        self.assertEqual(len(timeline), 2)

        first = timeline[0]

        self.assertEqual(first["day_number"], 1)
        self.assertEqual(first["title"], "抵达张家界")

        # 未设置标题的日期回退到日期文本。
        self.assertEqual(timeline[1]["title"], "2024-05-02")

    def test_update_and_delete_ai_story(self):
        """AI 建议的主题可以被修改和撤销。"""

        story = self.repo.create_story(
            self.project["project_id"],
            "AI 建议主题",
            source="ai",
        )

        updated = self.repo.update_story(
            story["story_id"],
            title="修改后的主题",
            description="用户修改过的描述",
        )

        self.assertEqual(updated["title"], "修改后的主题")

        # 撤销 AI 建议：直接删除故事。
        self.assertTrue(
            self.repo.delete_story(story["story_id"])
        )

        self.assertIsNone(
            self.repo.get_story(story["story_id"])
        )


if __name__ == "__main__":
    unittest.main()
