# backend/search.py

from chunking import build_chunks


def search_segments(transcript: dict, query: str) -> list[dict]:
    keyword = query.strip().casefold()

    if not keyword:
        return []

    results = []

    for segment in transcript["segments"]:
        if keyword in segment["text"].casefold():
            results.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
            })

    return results


def search_chunks(
    transcript: dict,
    query: str,
    window_size: int = 2,
    stride: int = 1,
    max_gap_seconds: float = 3.0,
) -> list[dict]:

    keyword = query.strip().casefold()

    if not keyword:
        return []

    chunks = build_chunks(
        transcript,
        window_size=window_size,
        stride=stride,
        max_gap_seconds=max_gap_seconds,
    )

    matches = [
        chunk for chunk in chunks
        if keyword in chunk["text"].casefold()
    ]

    if not matches:
        return []

    # 先保留每个命中窗口，再对重叠来源进行合并。
    merged = []

    for chunk in matches:
        current = set(chunk["segment_indices"])

        if merged and current & set(merged[-1]["segment_indices"]):
            previous = merged[-1]
            previous["segment_indices"] = sorted(
                set(previous["segment_indices"]) | current
            )
            previous["start"] = min(
                previous["start"], chunk["start"]
            )
            previous["end"] = max(
                previous["end"], chunk["end"]
            )
        else:
            merged.append({
                "start": chunk["start"],
                "end": chunk["end"],
                "segment_indices": list(chunk["segment_indices"]),
            })

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
