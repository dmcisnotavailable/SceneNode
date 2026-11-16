"""media_scanner 模块测试。

测试思路：用 monkeypatch 把 FFprobe 调用替换为模拟函数，
不依赖真实视频文件。重点验证：
1. 文件格式过滤是否正确。
2. 子目录是否能够扫描。
3. 单个文件失败是否影响其他文件。
4. 不存在的目录是否正确报错。
"""

import pytest

import media_scanner


def test_scan_directory_only_scans_supported_files(
    tmp_path,
    monkeypatch,
):
    """只有支持格式的视频文件会被扫描，其余文件被跳过。"""

    # 准备测试目录：两个支持格式 + 一个不支持格式。
    (tmp_path / "video1.mp4").write_bytes(b"fake")
    (tmp_path / "video2.mov").write_bytes(b"fake")
    (tmp_path / "readme.txt").write_text("hello")

    def fake_probe(path):
        # 模拟 FFprobe，不需要真实视频。
        return {
            "duration": 60.0,
            "width": 1920,
            "height": 1080,
        }

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        fake_probe,
    )

    results = media_scanner.scan_directory(
        str(tmp_path)
    )

    assert len(results) == 2

    filenames = {item["filename"] for item in results}

    assert filenames == {"video1.mp4", "video2.mov"}


def test_scan_directory_supports_nested_folders(
    tmp_path,
    monkeypatch,
):
    """子目录中的视频文件同样会被扫描。"""

    nested = tmp_path / "day1" / "早上"
    nested.mkdir(parents=True)

    (tmp_path / "root.mp4").write_bytes(b"fake")
    (nested / "nested.mov").write_bytes(b"fake")

    def fake_probe(path):
        return {
            "duration": 12.5,
            "width": 3840,
            "height": 2160,
        }

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        fake_probe,
    )

    results = media_scanner.scan_directory(
        str(tmp_path)
    )

    assert len(results) == 2

    filenames = {item["filename"] for item in results}

    assert filenames == {"root.mp4", "nested.mov"}


def test_scan_directory_skips_invalid_video(
    tmp_path,
    monkeypatch,
):
    """单个文件元数据读取失败时跳过，不影响其他文件。"""

    (tmp_path / "broken.mp4").write_bytes(b"broken")
    (tmp_path / "good.mov").write_bytes(b"good")

    def fake_probe(path):
        if "broken" in str(path):
            raise ValueError("模拟损坏视频")

        return {
            "duration": 30.0,
            "width": 1280,
            "height": 720,
        }

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        fake_probe,
    )

    results = media_scanner.scan_directory(
        str(tmp_path)
    )

    assert len(results) == 1
    assert results[0]["filename"] == "good.mov"


def test_scan_directory_rejects_invalid_path(
    tmp_path,
    monkeypatch,
):
    """目录不存在时抛出 ValueError。"""

    def fake_probe(path):
        raise AssertionError("不应调用 FFprobe")

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        fake_probe,
    )

    with pytest.raises(ValueError):
        media_scanner.scan_directory(
            str(tmp_path / "不存在的目录")
        )
