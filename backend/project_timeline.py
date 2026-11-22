"""项目时间线工具。

负责把素材按拍摄日期分组，形成时间线视图的数据源。
v1.0.3 阶段只做动态分组，project_days 表
负责保存用户手动调整的天标题。
"""

from datetime import datetime


# 无拍摄时间素材的分组键。
UNKNOWN_DAY_KEY = "unknown"


def _extract_day_key(recorded_at):
    """从 recorded_at 中解析 ISO 日期，无效或缺失时返回 unknown。"""

    if not recorded_at or not isinstance(recorded_at, str):
        return UNKNOWN_DAY_KEY

    # recorded_at 统一为 ISO 8601 字符串，
    # 兼容带 Z 后缀的 UTC 时间写法。
    try:
        parsed = datetime.fromisoformat(
            recorded_at.replace("Z", "+00:00")
        )

        return parsed.date().isoformat()
    except ValueError:
        return UNKNOWN_DAY_KEY


def group_assets_by_day(assets):
    """把素材列表按拍摄日期分组。

    参数：
        assets: 素材字典列表，每个元素至少包含
            asset_id 和 recorded_at 字段。

    返回：
        按日期升序排列的分组列表，每组包含：
        - day_key: ISO 日期字符串或 unknown
        - assets: 当天的素材列表
        无拍摄时间的素材统一归入 unknown 组并排在最后。
    """

    if not assets:
        return []

    buckets = {}

    for asset in assets:
        day_key = _extract_day_key(asset.get("recorded_at"))

        if day_key not in buckets:
            buckets[day_key] = []

        buckets[day_key].append(asset)

    # 日期组按字典序（即时间顺序）排列，
    # unknown 组没有可比日期，固定排在最后。
    day_keys = sorted(
        key
        for key in buckets
        if key != UNKNOWN_DAY_KEY
    )

    if UNKNOWN_DAY_KEY in buckets:
        day_keys.append(UNKNOWN_DAY_KEY)

    return [
        {
            "day_key": day_key,
            "assets": buckets[day_key],
        }
        for day_key in day_keys
    ]


def build_timeline(assets, project_days=None):
    """构建时间线视图数据，合并用户自定义的天标题。

    参数：
        assets: 项目素材列表。
        project_days: project_days 表记录列表，可选。

    返回：
        分组列表，每组在 group_assets_by_day
        结果基础上补充 day_number 和 title 字段。
        日期组按先后顺序编号 Day 1、Day 2 ...，
        unknown 组不编号。project_days 中保存的
        date 与分组 day_key 匹配时使用用户标题。
    """

    groups = group_assets_by_day(assets)

    # 以日期字符串为键建立用户天配置索引。
    day_config = {}

    for record in project_days or []:
        if record.get("date"):
            day_config[record["date"]] = record

    result = []
    day_number = 0

    for group in groups:
        entry = dict(group)

        if group["day_key"] == UNKNOWN_DAY_KEY:
            entry["day_number"] = None
            entry["title"] = "拍摄时间未知"
        else:
            day_number += 1
            entry["day_number"] = day_number

            # 用户手动设置的标题优先于默认的日期文本。
            custom = day_config.get(group["day_key"])

            if custom and custom.get("title"):
                entry["title"] = custom["title"]
            else:
                entry["title"] = group["day_key"]

        result.append(entry)

    return result
