# backend/search.py

def search_segments(transcript: dict, query: str) -> list[dict]:
    """
    在 Whisper 转写结果中搜索关键词。

    transcript: transcript.json 读取出的字典
    query: 用户输入的关键词

    返回匹配片段，包含 start、end、text。
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
                "text": text
            })

    return results
