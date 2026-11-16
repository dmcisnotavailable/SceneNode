"""本地素材目录扫描器。

本模块的职责只有一个：
给定一个本地目录，返回其中视频文件的结构化元数据。

设计约束（对应 v1.0 规划中的 Day 1 任务）：
1. 只读扫描：不复制、不修改、不删除任何原始素材。
2. 独立模块：不依赖 FastAPI、数据库和 Worker，方便单独测试和复用。
3. 失败容错：单个视频元数据读取失败时跳过该文件，不影响其他素材。
4. 不跟随符号链接：避免扫描过程意外离开用户指定的目录。

注意：本模块不负责限制可访问目录。接入 API 时必须增加
路径白名单校验，不能直接信任前端传来的绝对路径。
"""

import json
import os
import subprocess

from pathlib import Path

# 支持扫描的视频文件扩展名（统一小写比较）。
SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
}


def probe_video(video_path: Path) -> dict:
    """使用 FFprobe 读取视频基本信息。

    返回字段：
    - duration: 视频时长（秒）。
    - width:    视频宽度（像素）。
    - height:   视频高度（像素）。

    说明：这里只读取媒体容器元数据，不读取视频内容，
    因此不会复制或修改原始视频。
    """

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of", "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)

    # 从所有流中找到第一个视频流。
    video_streams = [
        stream
        for stream in data.get("streams", [])
        if stream.get("codec_type") == "video"
    ]

    if not video_streams:
        # 没有视频流的文件视为无效素材。
        raise ValueError(f"文件中没有视频流: {video_path}")

    video_stream = video_streams[0]

    duration = data.get("format", {}).get("duration")

    if duration is None:
        raise ValueError(f"无法读取视频时长: {video_path}")

    return {
        "duration": float(duration),
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
    }


def scan_directory(root: str) -> list:
    """扫描本地目录，返回视频素材的结构化元数据列表。

    输入是目录路径字符串；输出是素材记录字典列表，每条记录包含：
    - filename:    原始文件名。
    - source_path: 原始文件绝对路径。
    - file_size:   文件大小（字节）。
    - file_mtime:  文件系统修改时间（时间戳）。
    - duration:    视频时长（秒）。
    - width:       视频宽度（像素）。
    - height:      视频高度（像素）。

    行为约定：
    - 目录不存在时抛出 ValueError。
    - 单个文件元数据读取失败时跳过并继续扫描其他文件。
    """

    root_path = Path(root)

    if not root_path.is_dir():
        raise ValueError(f"扫描目录不存在或不是目录: {root}")

    results = []

    # followlinks=False：不跟随目录符号链接，
    # 避免重复扫描或者意外离开指定目录。
    for current_root, dirs, files in os.walk(
        root_path,
        followlinks=False,
    ):
        for filename in files:
            video_path = Path(current_root) / filename

            # 第一步：过滤不支持的文件格式。
            if video_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            try:
                # 第二步：读取文件系统元数据（大小、修改时间）。
                stat = video_path.stat()

                # 第三步：调用 FFprobe 读取视频元数据。
                video_info = probe_video(video_path)

            except (
                OSError,
                subprocess.CalledProcessError,
                ValueError,
                json.JSONDecodeError,
            ):
                # 单个文件失败不影响整体扫描。
                # 说明：损坏视频当前会被静默跳过。
                # 后续应该把失败原因作为结果返回，
                # 让用户知道哪些素材没有被索引。
                continue

            # 第四步：形成结构化素材记录。
            results.append({
                "filename": video_path.name,
                "source_path": str(video_path),
                "file_size": stat.st_size,
                "file_mtime": stat.st_mtime,
                "duration": video_info["duration"],
                "width": video_info["width"],
                "height": video_info["height"],
            })

    return results
