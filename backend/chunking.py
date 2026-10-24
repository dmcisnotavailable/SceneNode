# backend/chunking.py

from typing import Any


def build_chunks(
    transcript: dict,
    window_size: int = 2,
    stride: int = 1,
) -> list[dict[str, Any]]:
    """
    将 Whisper Segment 组织为滑动窗口 Chunk。

    window_size: 每个窗口包含多少个 Segment
    stride: 每次窗口向前移动多少个 Segment
    """

    if window_size < 1:
        raise ValueError("window_size 必须 >= 1")

    if stride < 1:
        raise ValueError("stride 必须 >= 1")

    segments = transcript["segments"]

    if not segments:
        return []

    chunks = []

    for i in range(0, len(segments), stride):
        group = segments[i:i + window_size]

        if not group:
            break

        # 合并文本。直接拼接，以支持跨 Segment 的连续中文短语。
        text = "".join(
            segment["text"] for segment in group
        )

        chunk = {
            "chunk_id": len(chunks),
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "text": text,
            "segment_indices": list(
                range(i, i + len(group))
            ),
        }

        chunks.append(chunk)

    return chunks
