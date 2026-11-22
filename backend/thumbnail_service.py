import subprocess
from pathlib import Path


# 缩略图最长边限制，保持纵横比缩放。
THUMBNAIL_MAX_SIZE = 480


def generate_thumbnail(
    video_path: str,
    output_path: str,
    timestamp: float = 1.0,
):
    """
    为视频生成一张 JPEG 缩略图。

    参数：
    - video_path：原始视频路径，必须真实存在。
    - output_path：缩略图输出路径，父目录会自动创建。
    - timestamp：抽帧时间点（秒），取前段画面避免全黑片头。

    返回：缩略图的绝对路径。

    异常：
    - FileNotFoundError：视频不存在。
    - subprocess.CalledProcessError：FFmpeg 执行失败。
    - subprocess.TimeoutExpired：FFmpeg 超时。
    """

    source = Path(video_path).resolve(strict=True)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    # 时间点不能为负。
    timestamp = max(0.0, float(timestamp))

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss", str(timestamp),
            "-i", str(source),
            "-frames:v", "1",
            "-vf", (
                f"scale={THUMBNAIL_MAX_SIZE}:{THUMBNAIL_MAX_SIZE}"
                ":force_original_aspect_ratio=decrease"
            ),
            "-q:v", "4",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )

    if not output.is_file():
        raise RuntimeError("FFmpeg 未生成缩略图文件")

    return str(output)
