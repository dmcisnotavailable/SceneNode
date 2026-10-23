import math
import subprocess
from pathlib import Path


def get_video_duration(video_path: Path) -> float:
    """使用 ffprobe 获取原视频时长。"""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    duration = float(result.stdout.strip())

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法获取有效的视频时长")

    return duration


def export_clip(
    video_path: Path,
    output_path: Path,
    start: float,
    end: float,
) -> Path:
    """裁剪视频的 [start, end] 时间段。"""

    if not video_path.is_file():
        raise FileNotFoundError("原始视频不存在")

    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("时间必须是有限数字")

    if start < 0 or end <= start:
        raise ValueError("裁剪时间范围不合法")

    duration = get_video_duration(video_path)

    if end > duration + 0.01:
        raise ValueError(
            f"结束时间不能超过视频时长 {duration:.2f} 秒"
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    clip_duration = end - start

    command = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(clip_duration),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("裁剪失败：没有生成有效文件")

    return output_path
