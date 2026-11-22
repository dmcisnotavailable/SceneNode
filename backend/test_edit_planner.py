import tempfile
import unittest

from pathlib import Path

from edit_planner import (
    STYLE_RATIOS,
    build_edit_outline,
    calculate_story_statistics,
    estimate_output_minutes,
)
from project_search import search_project_assets
from task_repository import TaskRepository


class EditPlannerTestCase(unittest.TestCase):
    """v1.0.4 剪辑规划计算的纯逻辑测试。"""

    def test_calculate_story_statistics_deduplicates(self):
        """同一片段加入多个故事时时长不重复计算。"""

        clip_a = {
            "clip_id": "clip-a",
            "start_time": 0.0,
            "end_time": 60.0,
        }

        clip_b = {
            "clip_id": "clip-b",
            "start_time": 10.0,
            "end_time": 70.0,
        }

        # 同一片段出现在两个故事中。
        stats = calculate_story_statistics(
            [clip_a, clip_a, clip_b]
        )

        self.assertEqual(stats["clip_count"], 2)
        self.assertEqual(stats["candidate_seconds"], 120.0)
        self.assertEqual(stats["candidate_minutes"], 2.0)

    def test_estimate_output_minutes(self):
        """按剪辑风格估算成片时长区间。"""

        # 40 分钟候选素材。
        result = estimate_output_minutes(
            "documentary",
            40.0,
        )

        # 记录型 35%-55%：14-22 分钟。
        self.assertEqual(result["min_minutes"], 14.0)
        self.assertEqual(result["max_minutes"], 22.0)

        # 精简型 15%-30%：6-12 分钟。
        concise = estimate_output_minutes(
            "concise",
            40.0,
        )

        self.assertEqual(concise["min_minutes"], 6.0)
        self.assertEqual(concise["max_minutes"], 12.0)

        # 氛围型 20%-40%：8-16 分钟。
        ambient = estimate_output_minutes(
            "ambient",
            40.0,
        )

        self.assertEqual(ambient["min_minutes"], 8.0)
        self.assertEqual(ambient["max_minutes"], 16.0)

    def test_estimate_output_minutes_rejects_invalid_style(self):
        """非法剪辑风格直接报错。"""

        with self.assertRaises(ValueError):
            estimate_output_minutes("unknown", 40.0)

    def test_build_edit_outline(self):
        """按故事生成剪辑大纲。"""

        stories = [
            {
                "story_id": "story-1",
                "title": "山区风景",
                "clips": [
                    {
                        "clip_id": "clip-a",
                        "start_time": 0.0,
                        "end_time": 60.0,
                    },
                ],
            },
            {
                "story_id": "story-2",
                "title": "朋友互动",
                "clips": [],
            },
        ]

        outline = build_edit_outline(stories)

        self.assertEqual(len(outline), 2)
        self.assertEqual(outline[0]["title"], "山区风景")
        self.assertEqual(outline[0]["clip_count"], 1)
        self.assertEqual(
            outline[0]["candidate_minutes"],
            1.0,
        )

        self.assertEqual(outline[1]["clip_count"], 0)
        self.assertEqual(
            outline[1]["candidate_minutes"],
            0.0,
        )

    def test_search_project_assets_metadata(self):
        """跨视频搜索匹配文件名和描述。"""

        assets = [
            {
                "asset_id": "a1",
                "filename": "mountain.mp4",
                "scene": "山顶",
                "description": "日出时的山顶",
            },
            {
                "asset_id": "a2",
                "filename": "hotel.mp4",
                "scene": "",
                "description": "酒店大堂",
            },
        ]

        results = search_project_assets(assets, "山顶")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["asset_id"], "a1")
        self.assertEqual(
            results[0]["match_source"],
            "metadata",
        )

        # 空关键词返回空列表。
        self.assertEqual(
            search_project_assets(assets, ""),
            [],
        )

    def test_search_project_assets_annotations(self):
        """搜索匹配标签和故事主题附加字段。"""

        assets = [
            {
                "asset_id": "a1",
                "filename": "IMG_1024.mp4",
                "scene": "",
                "description": "",
                "tags_text": "天门山 location",
                "stories_text": "",
            },
        ]

        results = search_project_assets(assets, "天门山")

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0]["match_source"],
            "annotation",
        )


class SelectionAndPlanRepositoryTestCase(unittest.TestCase):
    """v1.0.4 筛选和剪辑规划的 Repository 测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.repo = TaskRepository(
            self.root / "test.db"
        )

        # 创建项目、素材和片段，供筛选表外键引用。
        self.project = self.repo.create_project("测试项目")

        self.repo.save_project_assets(
            self.project["project_id"],
            [{
                "filename": "video.mp4",
                "source_path": str(self.root / "video.mp4"),
                "file_size": 100,
                "file_mtime": 1000.0,
                "duration": 60.0,
                "width": 1920,
                "height": 1080,
                "recorded_at": None,
                "fingerprint": "fp-1",
            }],
        )

        self.assets = self.repo.list_project_assets(
            self.project["project_id"]
        )

        self.clip = self.repo.create_clip(
            self.assets[0]["asset_id"],
            0.0,
            30.0,
            "测试片段",
        )

    def test_selection_upsert_and_list(self):
        """筛选状态可以设置、更新和查询。"""

        selection = self.repo.upsert_selection(
            self.clip["clip_id"],
            "selected",
            "开头用",
        )

        self.assertEqual(selection["status"], "selected")
        self.assertEqual(selection["note"], "开头用")

        # 更新为备用状态，同一片段只保留一条记录。
        updated = self.repo.upsert_selection(
            self.clip["clip_id"],
            "backup",
        )

        self.assertEqual(updated["status"], "backup")
        self.assertEqual(updated["note"], "")

        selections = self.repo.list_selections(
            self.project["project_id"]
        )

        self.assertEqual(len(selections), 1)
        self.assertEqual(
            selections[0]["status"],
            "backup",
        )

        # 按状态过滤。
        selected_only = self.repo.list_selections(
            self.project["project_id"],
            status="selected",
        )

        self.assertEqual(len(selected_only), 0)

    def test_selection_rejected_keeps_clip(self):
        """rejected 只是不采用，片段和素材不受影响。"""

        self.repo.upsert_selection(
            self.clip["clip_id"],
            "rejected",
        )

        # 片段仍然存在。
        self.assertIsNotNone(
            self.repo.get_clip(self.clip["clip_id"])
        )

        # 素材也保持不变。
        assets = self.repo.list_project_assets(
            self.project["project_id"]
        )

        self.assertEqual(len(assets), 1)

    def test_delete_clip_cascades_selection(self):
        """删除片段时筛选记录级联清理。"""

        self.repo.upsert_selection(
            self.clip["clip_id"],
            "selected",
        )

        self.repo.delete_clip(self.clip["clip_id"])

        self.assertIsNone(
            self.repo.get_selection_by_clip(
                self.clip["clip_id"]
            )
        )

    def test_edit_plan_create_and_list(self):
        """剪辑规划可以创建、查询和删除。"""

        plan_json = {
            "outline": [
                {
                    "story_id": "story-1",
                    "title": "山区风景",
                    "clip_count": 1,
                }
            ]
        }

        plan = self.repo.create_edit_plan(
            self.project["project_id"],
            "张家界 Vlog 规划",
            "documentary",
            14.0,
            22.0,
            plan_json,
        )

        self.assertEqual(plan["style"], "documentary")
        self.assertEqual(plan["target_min_minutes"], 14.0)

        # plan_json 自动反序列化为字典。
        self.assertEqual(
            plan["plan_json"]["outline"][0]["title"],
            "山区风景",
        )

        plans = self.repo.list_edit_plans(
            self.project["project_id"]
        )

        self.assertEqual(len(plans), 1)

        self.assertTrue(
            self.repo.delete_edit_plan(plan["plan_id"])
        )

        self.assertIsNone(
            self.repo.get_edit_plan(plan["plan_id"])
        )

    def test_edit_plan_validates_input(self):
        """非法的剪辑规划输入被拒绝。"""

        with self.assertRaises(ValueError):
            self.repo.create_edit_plan(
                self.project["project_id"],
                "测试",
                "unknown-style",
                1.0,
                2.0,
                {},
            )

        with self.assertRaises(ValueError):
            self.repo.create_edit_plan(
                self.project["project_id"],
                "测试",
                "documentary",
                10.0,
                5.0,
                {},
            )


if __name__ == "__main__":
    unittest.main()
