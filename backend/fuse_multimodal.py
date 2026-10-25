import json
from pathlib import Path


def read_json(path):
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def fuse_multimodal(
    transcript_path,
    visual_path,
    output_path,
    video_duration=None,
):
    transcript = read_json(transcript_path)
    visual = read_json(visual_path)

    timeline = []

    # 语音片段
    for segment in transcript.get("segments", []):
        text = segment.get("text", "").strip()
        if not text:
            continue

        timeline.append({
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "source": "audio",
            "text": text,
        })

    # 视觉片段：单帧只能证明采样时刻的画面
    for frame in visual.get("frames", []):
        description = frame.get("description", "").strip()
        if not description:
            continue

        timestamp = float(frame["timestamp"])

        timeline.append({
            "start": timestamp,
            "end": timestamp,
            "source": "visual",
            "text": description,
        })

    # 按原始视频时间排序
    timeline.sort(
        key=lambda x: (
            x["start"],
            0 if x["source"] == "audio" else 1
        )
    )

    for index, item in enumerate(timeline):
        item["event_id"] = index

    output = {
        "video_duration": video_duration,
        "events": timeline,
    }

    Path(output_path).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"融合完成，共 {len(timeline)} 条事件")
    return output
