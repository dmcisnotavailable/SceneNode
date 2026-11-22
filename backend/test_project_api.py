"""旅行项目与素材扫描 API 测试。

用临时数据库和临时素材目录隔离测试环境，
并把 FFprobe 调用替换为模拟函数。
"""

import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
import media_scanner


class TestProjectAPI(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)

        # 用独立的临时数据库替换全局 Repository，
        # 避免污染真实 scenenote.db。
        self.repo = main.TaskRepository(
            self.root / "test.db"
        )

        self.repo_patch = patch.object(
            main,
            "TASK_REPOSITORY",
            self.repo,
        )
        self.repo_patch.start()
        self.addCleanup(self.repo_patch.stop)

        # 模拟白名单目录。
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()

        self.allowed_patch = patch(
            "project_service.get_allowed_roots",
            return_value=[self.allowed.resolve()],
        )
        self.allowed_patch.start()
        self.addCleanup(self.allowed_patch.stop)

        # 模拟 FFprobe，测试不依赖真实视频。
        self.probe_patch = patch(
            "media_scanner.probe_video",
            return_value={
                "duration": 30.0,
                "width": 1920,
                "height": 1080,
                "recorded_at": None,
            },
        )
        self.probe_patch.start()
        self.addCleanup(self.probe_patch.stop)

        self.client = TestClient(main.app)

    def create_project(self, name="张家界毕业旅行"):
        """通过 API 创建一个旅行项目。"""

        response = self.client.post(
            "/api/projects",
            json={"name": name},
        )

        self.assertEqual(response.status_code, 200)

        return response.json()

    def test_create_project(self):
        """创建项目成功并返回完整记录。"""

        project = self.create_project()

        self.assertIn("project_id", project)
        self.assertEqual(project["name"], "张家界毕业旅行")
        self.assertIn("created_at", project)

    def test_create_project_rejects_blank_name(self):
        """空白项目名称被业务校验拒绝，返回 400。"""

        response = self.client.post(
            "/api/projects",
            json={"name": " "},
        )

        self.assertEqual(response.status_code, 400)

    def test_list_projects(self):
        """项目列表包含素材数量统计。"""

        self.create_project("旅行 A")

        response = self.client.get("/api/projects")

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["total"], 1)
        self.assertEqual(
            data["projects"][0]["name"],
            "旅行 A",
        )
        self.assertEqual(
            data["projects"][0]["asset_count"],
            0,
        )

    def test_scan_directory(self):
        """扫描白名单目录并保存素材索引。"""

        project = self.create_project()

        (self.allowed / "video1.mp4").write_bytes(b"v1")
        (self.allowed / "video2.mov").write_bytes(b"v2")
        (self.allowed / "note.txt").write_text("n")

        response = self.client.post(
            f"/api/projects/{project['project_id']}/scan",
            json={"directory": str(self.allowed)},
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["total"], 2)
        self.assertEqual(data["saved"], 2)
        self.assertEqual(data["errors"], [])

    def test_scan_directory_is_idempotent(self):
        """重复扫描同一目录不产生重复素材。"""

        project = self.create_project()

        (self.allowed / "video1.mp4").write_bytes(b"v1")

        for _ in range(2):
            response = self.client.post(
                f"/api/projects/{project['project_id']}/scan",
                json={"directory": str(self.allowed)},
            )

            self.assertEqual(response.status_code, 200)

        assets = self.repo.list_project_assets(
            project["project_id"]
        )

        self.assertEqual(len(assets), 1)

    def test_scan_rejects_directory_outside_roots(self):
        """白名单之外的目录返回 403。"""

        project = self.create_project()

        outside = self.root / "outside"
        outside.mkdir()

        response = self.client.post(
            f"/api/projects/{project['project_id']}/scan",
            json={"directory": str(outside)},
        )

        self.assertEqual(response.status_code, 403)

    def test_scan_missing_project_returns_404(self):
        """扫描不存在的项目返回 404。"""

        response = self.client.post(
            "/api/projects/missing/scan",
            json={"directory": str(self.allowed)},
        )

        self.assertEqual(response.status_code, 404)

    def test_scan_missing_directory_returns_400(self):
        """不存在的目录返回 400。"""

        project = self.create_project()

        response = self.client.post(
            f"/api/projects/{project['project_id']}/scan",
            json={
                "directory": str(
                    self.allowed / "不存在"
                )
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_scan_records_broken_video_errors(self):
        """损坏视频记录在 errors 中且不影响其他素材。"""

        project = self.create_project()

        (self.allowed / "broken.mp4").write_bytes(b"bad")
        (self.allowed / "good.mp4").write_bytes(b"ok")

        def fake_probe(path):
            if "broken" in str(path):
                raise ValueError("模拟损坏视频")

            return {
                "duration": 30.0,
                "width": 1920,
                "height": 1080,
                "recorded_at": None,
            }

        with patch(
            "media_scanner.probe_video",
            side_effect=fake_probe,
        ):
            response = self.client.post(
                f"/api/projects/{project['project_id']}/scan",
                json={"directory": str(self.allowed)},
            )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["total"], 1)
        self.assertEqual(data["saved"], 1)
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(
            data["errors"][0]["filename"],
            "broken.mp4",
        )

    def test_list_project_assets(self):
        """素材列表接口返回已保存的素材。"""

        project = self.create_project()

        (self.allowed / "video1.mp4").write_bytes(b"v1")

        self.client.post(
            f"/api/projects/{project['project_id']}/scan",
            json={"directory": str(self.allowed)},
        )

        response = self.client.get(
            f"/api/projects/{project['project_id']}/assets"
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["total"], 1)
        self.assertEqual(
            data["assets"][0]["filename"],
            "video1.mp4",
        )

    def test_list_assets_missing_project_returns_404(self):
        """查询不存在项目的素材返回 404。"""

        response = self.client.get(
            "/api/projects/missing/assets"
        )

        self.assertEqual(response.status_code, 404)

    def test_scan_does_not_create_worker_tasks(self):
        """扫描素材不会创建任何视频分析任务。"""

        project = self.create_project()

        (self.allowed / "video1.mp4").write_bytes(b"v1")

        self.client.post(
            f"/api/projects/{project['project_id']}/scan",
            json={"directory": str(self.allowed)},
        )

        tasks = self.repo.list_tasks()

        self.assertEqual(len(tasks), 0)

    def test_scan_does_not_modify_original_files(self):
        """扫描过程不修改原始素材文件。"""

        project = self.create_project()

        target = self.allowed / "video1.mp4"
        target.write_bytes(b"original")

        before = target.read_bytes()

        self.client.post(
            f"/api/projects/{project['project_id']}/scan",
            json={"directory": str(self.allowed)},
        )

        self.assertEqual(target.read_bytes(), before)

    def create_asset(self, project=None):
        """通过扫描创建一个素材并返回素材记录。"""

        project = project or self.create_project()

        (self.allowed / "video1.mp4").write_bytes(b"v1")

        self.client.post(
            f"/api/projects/{project['project_id']}/scan",
            json={"directory": str(self.allowed)},
        )

        assets = self.repo.list_project_assets(
            project["project_id"]
        )

        self.assertEqual(len(assets), 1)

        return project, assets[0]

    def test_get_asset_thumbnail(self):
        """已生成缩略图的素材返回图片文件。"""

        project, asset = self.create_asset()

        thumbnail_dir = (
            Path(main.ASSET_JOB_DIR)
            / asset["asset_id"]
        )
        thumbnail_dir.mkdir(parents=True)
        thumbnail_path = thumbnail_dir / "thumbnail.jpg"
        thumbnail_path.write_bytes(b"fake-jpeg")

        self.repo.update_asset_analysis(
            asset["asset_id"],
            thumbnail_path=str(thumbnail_path),
        )

        response = self.client.get(
            f"/api/projects/{project['project_id']}"
            f"/assets/{asset['asset_id']}/thumbnail"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"],
            "image/jpeg",
        )

    def test_get_thumbnail_returns_404_when_missing(self):
        """未生成缩略图的素材返回 404。"""

        project, asset = self.create_asset()

        response = self.client.get(
            f"/api/projects/{project['project_id']}"
            f"/assets/{asset['asset_id']}/thumbnail"
        )

        self.assertEqual(response.status_code, 404)

    def test_get_thumbnail_rejects_outside_path(self):
        """数据库中的越界缩略图路径被拒绝。"""

        project, asset = self.create_asset()

        outside = self.root / "outside.jpg"
        outside.write_bytes(b"fake")

        self.repo.update_asset_analysis(
            asset["asset_id"],
            thumbnail_path=str(outside),
        )

        response = self.client.get(
            f"/api/projects/{project['project_id']}"
            f"/assets/{asset['asset_id']}/thumbnail"
        )

        self.assertEqual(response.status_code, 403)

    def test_analyze_creates_asset_task(self):
        """分析入队接口创建 asset_tasks 记录。"""

        project, asset = self.create_asset()

        response = self.client.post(
            f"/api/projects/{project['project_id']}"
            f"/assets/{asset['asset_id']}/analyze"
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["status"], "queued")

        task = self.repo.get_asset_task(
            asset["asset_id"]
        )

        self.assertIsNotNone(task)

    def test_analyze_missing_asset_returns_404(self):
        """为不存在的素材入队返回 404。"""

        project = self.create_project()

        response = self.client.post(
            f"/api/projects/{project['project_id']}"
            f"/assets/missing/analyze"
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
