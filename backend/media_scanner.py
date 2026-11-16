"""本地素材目录扫描器。

本模块的职责只有一个：
给定一个本地目录，返回其中视频文件的结构化元数据。

设计约束（对应 v1.0.1 规划）：
1. 只读扫描：不复制、不修改、不删除任何原始素材。
2. 独立模块：不依赖 FastAPI、数据库和 Worker，方便单独测试和复用。
3. 失败容错：单个视频元数据读取失败时记录错误并继续，不静默丢弃。
4. 不跟随符号链接：避免扫描过程意外离开用户指定的目录。
5. 轻量指纹：用于检测文件变化和重复扫描，不追求密码学强度。

注意：本模块不负责限制可访问目录。接入 API 时必须通过
project_service 的路径白名单校验，不能直接信任前端传来的绝对路径。
"""

import hashlib
import json
import os
import subprocess

from datetime import datetime
from pathlib import Path

# 支持扫描的视频文件扩展名（统一小写比较）。
SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
}


def get_recorded_at(data):
    """从 FFprobe 输出中提取视频拍摄时间。

    查找顺序：
    1. 容器级 creation_time 标签。
    2. QuickTime 的创建日期标签（常见于 iPhone 拍摄的 MOV）。
    3. 各流（视频流、音频流）的 creation_time 标签。

    找不到时返回 None；解析失败也返回 None，
    不能因为拍摄时间缺失而影响其他元数据的读取。
    """

    tags = data.get("format", {}).get("tags", {})

    value = (
        tags.get("creation_time")
        or tags.get("com.apple.quicktime.creationdate")
    )

    if not value:
        for stream in data.get("streams", []):
            value = stream.get("tags", {}).get("creation_time")

            if value:
                break

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # 标签存在但格式无法解析时，宁可返回 None。
        return None

    if parsed.tzinfo is None:
        return parsed.isoformat()

    return parsed.isoformat()


def quick_fingerprint(video_path: Path, stat) -> str:
    """计算轻量文件指纹。

    组合 文件路径 + 大小 + 修改时间 生成 SHA-256 摘要。

    说明：这不是严格的文件内容哈希，只用于普通导入和
    变化检测的第一版方案，不能当成绝对不会碰撞的文件身份。
    """

    payload = (
        f"{str(video_path).lower()}"
        f"|{stat.st_size}"
        f"|{int(stat.st_mtime)}"
    )

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def probe_video(video_path: Path) -> dict:
    """使用 FFprobe 读取视频基本信息。

    返回字段：duration（秒）、width、height（像素）、
    recorded_at（ISO 8601 字符串，可为 None）。

    说明：这里只读取媒体容器元数据，不读取视频内容，
    因此不会复制或修改原始视频。
    """

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries",
            (
                "format=duration:format=tags:"
                "stream=codec_type,width,height,creation_time"
            ),
            "-of", "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)

    video_streams = [
        stream
        for stream in data.get("streams", [])
        if stream.get("codec_type") == "video"
    ]

    if not video_streams:
        raise ValueError(f"文件中没有视频流: {video_path}")

    video_stream = video_streams[0]

    duration = data.get("format", {}).get("duration")

    if duration is None:
        raise ValueError(f"无法读取视频时长: {video_path}")

    return {
        "duration": float(duration),
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "recorded_at": get_recorded_at(data),
    }


def scan_directory(root: str) -> dict:
    """扫描本地目录，返回结构化的扫描结果。

    输出结构包含三部分：
    total  —— 成功读取元数据的素材数量；
    assets —— 素材记录列表；
    errors —— 逐文件错误列表（filename、source_path、error）。

    行为约定：
    - 目录不存在时抛出 ValueError。
    - 单个文件失败时记录到 errors 并继续扫描。
    - 不复制、不修改、不删除任何原始文件。
    """

    root_path = Path(root)

    if not root_path.is_dir():
        raise ValueError(f"扫描目录不存在或不是目录: {root}")

    assets = []
    errors = []

    # followlinks=False：不跟随目录符号链接，
    # 避免重复扫描或者意外离开指定目录。
    for current_root, dirs, files in os.walk(
        root_path,
        followlinks=False,
    ):
        for filename in files:
            video_path = Path(current_root) / filename

            if video_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            try:
                stat = video_path.stat()
                video_info = probe_video(video_path)

            except (
                OSError,
                subprocess.CalledProcessError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                # 单个文件失败不影响整体扫描，
                # 但必须把失败原因返回给调用方。
                errors.append({
                    "filename": filename,
                    "source_path": str(video_path),
                    "error": str(exc),
                })
                continue

            assets.append({
                "filename": video_path.name,
                "source_path": str(video_path),
                "file_size": stat.st_size,
                "file_mtime": stat.st_mtime,
                "recorded_at": video_info["recorded_at"],
                "duration": video_info["duration"],
                "width": video_info["width"],
                "height": video_info["height"],
                "fingerprint": quick_fingerprint(
                    video_path,
                    stat,
                ),
            })

    return {
        "total": len(assets),
        "assets": assets,
        "errors": errors,
    }
