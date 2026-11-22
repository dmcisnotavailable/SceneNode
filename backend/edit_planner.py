"""剪辑规划计算工具。

负责从素材事实出发统计候选时长，
并按剪辑风格给出成片时长的初始估算。
这些比例只是初始参数，不代表行业通用规律，
后续需要根据用户真实剪辑结果校准。
"""


# 剪辑风格与候选素材保留比例（初始估算参数）。
# documentary: 记录型，保留更多旅行过程。
# concise: 精简型，重点事件为主。
# ambient: 氛围型，风景和转场为主。
STYLE_RATIOS = {
    "documentary": (0.35, 0.55),
    "concise": (0.15, 0.30),
    "ambient": (0.20, 0.40),
}


def calculate_story_statistics(clips):
    """统计片段列表的候选时长事实。

    参数：
        clips: 片段字典列表，包含 start_time
            和 end_time 字段（单位秒）。

    返回：
        clip_count: 片段数量。
        candidate_seconds: 去重后的候选总秒数。
        candidate_minutes: 换算成分钟并保留两位小数。

    说明：
        同一片段加入多个故事时会被重复传入，
        这里按 clip_id 去重，避免候选时长
        被重复计算。
    """

    seen_clip_ids = set()
    total_seconds = 0.0

    for clip in clips:
        clip_id = clip.get("clip_id")

        # 没有 clip_id 的记录（如临时手工输入）
        # 按内容计算，无法去重。
        if clip_id is not None:
            if clip_id in seen_clip_ids:
                continue

            seen_clip_ids.add(clip_id)

        duration = max(
            0.0,
            float(clip["end_time"]) - float(clip["start_time"]),
        )

        total_seconds += duration

    return {
        "clip_count": len(seen_clip_ids) if clips else 0,
        "candidate_seconds": round(total_seconds, 2),
        "candidate_minutes": round(total_seconds / 60, 2),
    }


def estimate_output_minutes(style, candidate_minutes):
    """按剪辑风格估算成片时长区间（单位分钟）。

    只做比例换算，不夸大为通用规律；
    用户确认的候选时长才是计算基础。
    """

    if style not in STYLE_RATIOS:
        raise ValueError(
            "style 必须是 documentary、concise 或 ambient"
        )

    if not isinstance(candidate_minutes, (int, float)):
        raise ValueError("candidate_minutes 必须是数字")

    if candidate_minutes < 0:
        raise ValueError("candidate_minutes 不能为负数")

    min_ratio, max_ratio = STYLE_RATIOS[style]

    return {
        "style": style,
        "min_minutes": round(
            candidate_minutes * min_ratio, 2
        ),
        "max_minutes": round(
            candidate_minutes * max_ratio, 2
        ),
    }


def build_edit_outline(stories):
    """按故事主题生成剪辑大纲。

    参数：
        stories: 故事字典列表，每个元素包含
            story_id、title 和 clips 字段。

    返回：
        大纲条目列表，每条包含故事信息、
        片段数和去重后的候选分钟数。
    """

    outline = []

    for story in stories:
        stats = calculate_story_statistics(
            story.get("clips", [])
        )

        outline.append({
            "story_id": story["story_id"],
            "title": story["title"],
            "clip_count": stats["clip_count"],
            "candidate_minutes": stats["candidate_minutes"],
        })

    return outline
