"""media_scanner 模块测试。

测试思路：用 monkeypatch 把 FFprobe 调用替换为模拟函数，
不依赖真实视频文件。重点验证：
1. 文件格式过滤是否正确。
2. 子目录是否能够扫描。
3. 单个文件失败是否被记录到 errors 且不影响其他文件。
4. 不存在的目录是否正确报错。
5. 拍摄时间提取逻辑是否正确。
"""

import pytest

import media_scanner


def make_fake_probe(**overrides):
    """构造模拟 FFprobe 返回值的工厂函数。"""

    def fake_probe(path):
        info = {
            "duration": 60.0,
            "width": 1920,
            "height": 1080,
            "recorded_at": None,
        }
        info.update(overrides)
        return info

    return fake_probe


def test_scan_directory_only_scans_supported_files(
    tmp_path,
    monkeypatch,
):
    """只有支持格式的视频文件会被扫描，其余文件被跳过。"""

    (tmp_path / "video1.mp4").write_bytes(b"fake")
    (tmp_path / "video2.mov").write_bytes(b"fake")
    (tmp_path / "readme.txt").write_text("hello")

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        make_fake_probe(),
    )

    result = media_scanner.scan_directory(str(tmp_path))

    assert result["total"] == 2
    assert len(result["assets"]) == 2
    assert result["errors"] == []

    filenames = {
        item["filename"] for item in result["assets"]
    }

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

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        make_fake_probe(duration=12.5),
    )

    result = media_scanner.scan_directory(str(tmp_path))

    assert result["total"] == 2

    filenames = {
        item["filename"] for item in result["assets"]
    }

    assert filenames == {"root.mp4", "nested.mov"}


def test_scan_directory_skips_invalid_video(
    tmp_path,
    monkeypatch,
):
    """单个文件失败时记录错误并继续，不影响其他文件。"""

    (tmp_path / "broken.mp4").write_bytes(b"broken")
    (tmp_path / "good.mov").write_bytes(b"good")

    def fake_probe(path):
        if "broken" in str(path):
            raise ValueError("模拟损坏视频")

        return {
            "duration": 30.0,
            "width": 1280,
            "height": 720,
            "recorded_at": None,
        }

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        fake_probe,
    )

    result = media_scanner.scan_directory(str(tmp_path))

    assert result["total"] == 1
    assert result["assets"][0]["filename"] == "good.mov"

    assert len(result["errors"]) == 1
    assert result["errors"][0]["filename"] == "broken.mp4"
    assert "模拟损坏视频" in result["errors"][0]["error"]


def test_scan_directory_rejects_invalid_path(
    tmp_path,
    monkeypatch,
):
    """目录不存在时抛出 ValueError。"""

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        make_fake_probe(),
    )

    with pytest.raises(ValueError):
        media_scanner.scan_directory(
            str(tmp_path / "不存在的目录")
        )


def test_assets_contain_fingerprint_and_metadata(
    tmp_path,
    monkeypatch,
):
    """素材记录包含指纹和完整元数据字段。"""

    target = tmp_path / "video.mp4"
    target.write_bytes(b"fake")

    monkeypatch.setattr(
        media_scanner,
        "probe_video",
        make_fake_probe(
            recorded_at="2026-11-16T08:30:00+00:00",
        ),
    )

    result = media_scanner.scan_directory(str(tmp_path))

    asset = result["assets"][0]

    assert asset["file_size"] == 4
    assert asset["file_mtime"] > 0
    assert asset["recorded_at"] == "2026-11-16T08:30:00+00:00"
    assert asset["duration"] == 60.0
    assert asset["width"] == 1920
    assert asset["height"] == 1080
    assert len(asset["fingerprint"]) == 64


def test_get_recorded_at_formats():
    """拍摄时间标签能被正确解析为 ISO 格式。"""

    data = {
        "format": {
            "tags": {
                "creation_time": "2026-11-16T08:30:00.000000Z",
            },
        },
        "streams": [],
    }

    assert media_scanner.get_recorded_at(data) == (
        "2026-11-16T08:30:00+00:00"
    )


def test_get_recorded_at_quicktime_fallback():
    """QuickTime 创建日期标签也能被提取。"""

    data = {
        "format": {
            "tags": {
                "com.apple.quicktime.creationdate":
                    "2026-11-16T08:30:00",
            },
        },
        "streams": [],
    }

    assert media_scanner.get_recorded_at(data) == (
        "2026-11-16T08:30:00"
    )


def test_get_recorded_at_invalid_value_returns_none():
    """无法解析的拍摄时间返回 None，不抛异常。"""

    data = {
        "format": {
            "tags": {
                "creation_time": "not-a-date",
            },
        },
        "streams": [],
    }

    assert media_scanner.get_recorded_at(data) is None
