import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
JOB_DIR = BASE_DIR / "jobs"


def load_report(video_id):
    path = JOB_DIR / video_id / "performance.json"

    if not path.exists():
        raise FileNotFoundError(path)

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def get_seconds(report, stage):
    data = report["stages"].get(stage, {})
    return data.get("seconds", 0.0)


def main():
    if len(sys.argv) < 3:
        raise SystemExit(
            "用法: python compare_performance.py "
            "基线video_id 优化后video_id"
        )

    before = load_report(sys.argv[1])
    after = load_report(sys.argv[2])

    stages = [
        "audio_probe",
        "audio_extract",
        "whisper_init",
        "whisper_infer",
        "frame_extract",
        "vision_total",
        "processing_branches",
        "multimodal_fuse",
        "chapter_llm",
        "pipeline_total",
    ]

    print(
        f"{'Stage':<24}"
        f"{'Before':>12}"
        f"{'After':>12}"
        f"{'Change':>12}"
    )

    print("-" * 60)

    for stage in stages:
        b = get_seconds(before, stage)
        a = get_seconds(after, stage)

        if b == 0 and a == 0:
            continue

        change = (
            (b - a) / b * 100
            if b > 0 else 0
        )

        print(
            f"{stage:<24}"
            f"{b:>12.2f}"
            f"{a:>12.2f}"
            f"{change:>11.1f}%"
        )

    print("\n配置：")
    print("Before:", before["stages"].get("config"))
    print("After: ", after["stages"].get("config"))


if __name__ == "__main__":
    main()
