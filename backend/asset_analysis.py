import json
from pathlib import Path

from describe_frames import describe_frames
from extract_frames import extract_frames


def analyze_asset(
    video_path: str,
    output_dir: str,
    frame_count: int = 6,
    max_workers: int = 2,
):
    """
    分析单个旅行素材，生成场景概述。

    复用现有的抽帧和视觉描述能力：
    1. extract_frames 等间隔抽取 frame_count 张画面。
    2. describe_frames 调用视觉模型生成每帧描述。
    3. 汇总帧描述生成素材级 scene / description。

    参数：
    - video_path：原始视频路径。
    - output_dir：分析产物输出目录（asset_jobs/{asset_id}/）。
    - frame_count：抽帧数量，1 到 6 之间。
    - max_workers：视觉描述并发数。

    返回：{"scene": 场景名, "description": 素材概述,
           "frames": 帧描述列表, "manifest": 抽帧清单}
    """

    if not 1 <= frame_count <= 6:
        raise ValueError("frame_count 必须在 1 到 6 之间")

    if not 1 <= max_workers <= 6:
        raise ValueError("max_workers 必须在 1 到 6 之间")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    frames_dir = output / "frames"

    # 第一步：抽取代表性画面。
    manifest = extract_frames(
        video_path,
        str(frames_dir),
        count=frame_count,
    )

    # 第二步：视觉模型逐帧描述。
    saved = describe_frames(
        str(frames_dir),
        limit=frame_count,
        max_workers=max_workers,
    )

    # 第三步：读取帧描述并汇总素材级概述。
    data = json.loads(
        (
            frames_dir / "visual_descriptions.json"
        ).read_text(encoding="utf-8")
    )

    frames = data.get("frames", [])

    if not frames:
        raise RuntimeError("没有可用的帧描述结果")

    # 场景名取第一帧的 scene，代表素材的主要场景。
    scene = frames[0].get("scene", "")

    # 概述拼接所有帧的一句话描述，用分号分隔。
    descriptions = [
        item.get("description", "").strip()
        for item in frames
        if item.get("description")
    ]

    if not descriptions:
        raise RuntimeError("帧描述内容为空")

    description = "；".join(descriptions)

    result = {
        "scene": scene,
        "description": description,
        "frames": frames,
        "manifest": manifest,
    }

    # 第四步：保存素材级概述，便于 Worker 断点恢复后直接读取。
    summary_path = output / "asset_description.json"

    summary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return result
