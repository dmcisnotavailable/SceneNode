import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def generate_chapters(timeline_path, output_path):
    timeline_path = Path(timeline_path)
    output_path = Path(output_path)

    data = json.loads(
        timeline_path.read_text(encoding="utf-8")
    )

    events = data.get("events", [])

    if not events:
        raise ValueError("语音和视觉内容均为空，无法生成章节")

    client = OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ["LLM_BASE_URL"],
        timeout=120.0,
    )

    model = os.environ["LLM_MODEL"]

    content_lines = []

    for event in events:
        source = (
            "语音"
            if event["source"] == "audio"
            else "画面"
        )

        content_lines.append(
            f"[{event['event_id']}] "
            f"{event['start']:.2f}s "
            f"({source}) {event['text']}"
        )

    content_text = "\n".join(content_lines)

    prompt = f"""
你是一个视频章节编辑。

下面是视频中按时间排序的语音转写与画面描述。

请将视频整理成有意义的章节。

要求：
1. 根据语义或明显场景变化划分章节。
2. 最多生成 6 个章节。
3. 语音与画面可能同时存在，应综合理解。
4. 没有语音时，允许根据画面生成章节。
5. 不要编造人物身份、地名、动作或事件。
6. 单帧画面不代表整个时间段。
7. start_event_id 必须来自提供的真实事件编号。
8. 第一章必须从事件 0 开始。
9. 章节编号严格递增，不允许重复。
10. 只返回 JSON，不要 Markdown。

输出格式：
{{
  "chapters": [
    {{
      "start_event_id": 0,
      "title": "章节标题",
      "summary": "一句话摘要"
    }}
  ]
}}

视频内容：
{content_text}
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("LLM 没有返回内容")

    result = json.loads(content)

    raw_chapters = result.get("chapters", [])

    if not isinstance(raw_chapters, list):
        raise ValueError("chapters 必须是数组")

    if not 1 <= len(raw_chapters) <= 6:
        raise ValueError("章节数量必须为 1 到 6")

    chapters = []
    previous_index = -1

    for item in raw_chapters:
        index = item.get("start_event_id")

        if type(index) is not int:
            raise ValueError("start_event_id 必须是整数")

        if not 0 <= index < len(events):
            raise ValueError(f"无效事件编号: {index}")

        if index <= previous_index:
            raise ValueError("章节编号必须严格递增")

        title = item.get("title")
        summary = item.get("summary")

        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(summary, str)
            or not summary.strip()
        ):
            raise ValueError("章节标题或摘要无效")

        chapters.append({
            "start": events[index]["start"],
            "title": title.strip(),
            "summary": summary.strip(),
        })

        previous_index = index

    if raw_chapters[0]["start_event_id"] != 0:
        raise ValueError("第一章必须从事件 0 开始")

    output = {"chapters": chapters}

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"章节生成完成，共 {len(chapters)} 章")
    return output
