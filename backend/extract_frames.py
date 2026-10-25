import json
import subprocess
from pathlib import Path


def get_video_duration(video_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return float(result.stdout.strip())


def extract_frames(
    video_path: str,
    output_dir: str,
    count: int = 6,
):
    video = Path(video_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    if not video.is_file():
        raise FileNotFoundError(video)

    if count < 1:
        raise ValueError("count 必须大于 0")

    duration = get_video_duration(video)

    if duration <= 0:
        raise ValueError("视频时长无效")

    frames = []

    for i in range(count):
        # 取每个等分时间区间的中点
        timestamp = duration * (i + 0.5) / count

        filename = f"frame_{i:03d}.jpg"
        image_path = output / filename

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(timestamp),
                "-i", str(video),
                "-frames:v", "1",
                "-vf",
                "scale=640:640:force_original_aspect_ratio=decrease",
                "-q:v", "3",
                str(image_path),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )

        if not image_path.is_file():
            raise RuntimeError(f"抽帧失败: {filename}")

        frames.append({
            "frame_id": i,
            "timestamp": round(timestamp, 2),
            "image_path": filename,
        })

    result = {
        "video_duration": round(duration, 2),
        "frames": frames,
    }

    (output / "frames.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        raise SystemExit(
            "用法: python extract_frames.py 视频路径 输出目录"
        )

    result = extract_frames(sys.argv[1], sys.argv[2])
    print(json.dumps(result, ensure_ascii=False, indent=2))
