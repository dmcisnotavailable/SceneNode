"""项目内跨素材搜索。

v1.0.4 第一版使用轻量关键词匹配作为 Baseline，
搜索范围包括文件名、AI 内容概述、视觉描述、
标签和故事主题，不引入向量数据库。
"""


def search_project_assets(assets, query):
    """按关键词在项目素材中做跨视频搜索。

    参数：
        assets: 素材字典列表，来自 list_project_assets。
        query: 用户输入的关键词。

    返回：
        匹配的素材列表，每条附带 match_source
        说明命中了哪类信息（metadata/tag/story）。
        空关键词返回空列表。
    """

    query = (query or "").strip().casefold()

    if not query:
        return []

    results = []

    for asset in assets:
        # 素材自身的元数据字段。
        metadata_fields = [
            asset.get("filename", ""),
            asset.get("scene", ""),
            asset.get("description", ""),
        ]

        metadata_text = " ".join(
            value
            for value in metadata_fields
            if value
        ).casefold()

        if query in metadata_text:
            results.append(
                _build_result(
                    asset,
                    "metadata",
                )
            )

            continue

        # 附加信息字段：标签和故事主题由调用方
        # 合并进 tags / stories 键，避免本模块
        # 直接依赖数据库。
        extra_fields = [
            asset.get("tags_text", ""),
            asset.get("stories_text", ""),
        ]

        extra_text = " ".join(
            value
            for value in extra_fields
            if value
        ).casefold()

        if query in extra_text:
            results.append(
                _build_result(
                    asset,
                    "annotation",
                )
            )

    return results


def _build_result(asset, match_source):
    """构造单条搜索结果。"""

    return {
        "asset_id": asset["asset_id"],
        "filename": asset["filename"],
        "description": asset.get("description") or "",
        "scene": asset.get("scene") or "",
        "thumbnail_path": asset.get("thumbnail_path"),
        "recorded_at": asset.get("recorded_at"),
        "match_source": match_source,
    }
