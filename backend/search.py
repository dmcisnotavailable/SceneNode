# backend/search.py

from chunking import build_chunks


def search_segments(
    transcript: dict,
    query: str,
) -> list[dict]:
    """
    v0.3 Baseline：逐个 Segment 关键词检索。
    """

    keyword = query.strip().casefold()
    results = []

    if not keyword:
        return results

    for segment in transcript["segments"]:
        text = segment["text"]

        if keyword in text.casefold():
            results.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": text,
            })

    return results


def search_chunks(
    transcript: dict,
    query: str,
    window_size: int = 2,
    stride: int = 1,
) -> list[dict]:
    """
    v0.4：在滑动窗口 Chunk 中搜索关键词。

    保留时间戳和原始 Segment 来源。
    对重叠窗口的重复命中进行合并。
    """

    keyword = query.strip().casefold()

    if not keyword:
        return []

    chunks = build_chunks(
        transcript,
        window_size=window_size,
        stride=stride,
    )

    matches = []

    for chunk in chunks:
        if keyword in chunk["text"].casefold():
            matches.append(chunk)

    if not matches:
        return []

    # 合并有重叠原始 Segment 的命中窗口。
    # 这样相邻窗口不会产生大量重复结果。
    merged = []

    for chunk in matches:
        if not merged:
            merged.append({
                "start": chunk["start"],
                "end": chunk["end"],
                "segment_indices": list(
                    chunk["segment_indices"]
                ),
            })
            continue

        previous = merged[-1]

        previous_indices = set(
            previous["segment_indices"]
        )
        current_indices = set(
            chunk["segment_indices"]
        )

        if previous_indices & current_indices:
            previous["start"] = min(
                previous["start"],
                chunk["start"],
            )
            previous["end"] = max(
                previous["end"],
                chunk["end"],
            )
            previous["segment_indices"] = sorted(
                previous_indices | current_indices
            )
        else:
            merged.append({
                "start": chunk["start"],
                "end": chunk["end"],
                "segment_indices": list(
                    chunk["segment_indices"]
                ),
            })

    # 使用原始 Segment 重建文本，避免重叠文本重复。
    segments = transcript["segments"]
    results = []

    for item in merged:
        text = "".join(
            segments[index]["text"]
            for index in item["segment_indices"]
        )

        results.append({
            "start": item["start"],
            "end": item["end"],
            "text": text,
            "segment_indices": item["segment_indices"],
        })

    return results
