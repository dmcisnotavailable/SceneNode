# backend/chunking.py

from typing import Any


def build_chunks(
    transcript: dict,
    window_size: int = 2,
    stride: int = 1,
    max_gap_seconds: float = 3.0,
) -> list[dict[str, Any]]:
    """
    滑动窗口 Chunk。

    相邻 Segment 的间隔超过 max_gap_seconds
    时，不允许合并。
    """

    if window_size < 1 or stride < 1:
        raise ValueError("窗口大小和步长必须 >= 1")

    if max_gap_seconds < 0:
        raise ValueError("max_gap_seconds 不能小于 0")

    segments = transcript["segments"]
    chunks = []

    for i in range(0, len(segments), stride):
        indices = [i]

        for j in range(i + 1, min(i + window_size, len(segments))):
            previous = segments[j - 1]
            current = segments[j]

            gap = current["start"] - previous["end"]

            if gap > max_gap_seconds:
                break

            indices.append(j)

        group = [segments[j] for j in indices]

        # 空白处理：中文直接拼接，英文之间保留空格。
        text = ""

        for segment in group:
            part = segment["text"].strip()

            if not part:
                continue

            if (
                text
                and text[-1].isascii()
                and text[-1].isalnum()
                and part[0].isascii()
                and part[0].isalnum()
            ):
                text += " "

            text += part

        if not text:
            continue

        chunks.append({
            "chunk_id": len(chunks),
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "text": text,
            "segment_indices": indices,
        })

    return chunks
