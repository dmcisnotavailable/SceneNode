import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

def generate_chapters(transcript_path: Path, output_path: Path):
    load_dotenv()

    client = OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ["LLM_BASE_URL"],
    )

    model = os.environ["LLM_MODEL"]

    # 1. 读取 Day 2 的转写结果
    data = json.loads(
        transcript_path.read_text(encoding="utf-8")
    )

    segments = data["segments"]

    if not segments:
        raise ValueError("转写结果为空，无法生成章节")

    # 2. 为每个片段分配编号
    transcript = "\n".join(
        f"[{i}] {s['start']:.2f}s {s['text']}"
        for i, s in enumerate(segments)
    )

    # 3. 让模型根据内容划分章节
    prompt = f"""
    你是一个视频章节编辑。

    请根据下面的语音转写结果，将视频划分为有意义的章节。

    要求：
    1. 按照话题变化划分章节。
    2. 最多生成 6 个章节。
    3. 不要编造转写中不存在的信息。
    4. 每个章节有简短标题和一句话摘要。
    5. start_index 必须是转写片段的真实编号。
    6. 第一章从编号 0 开始，编号严格递增且不重复。
    7. 只返回 JSON，不要使用 Markdown 代码块。

    输出格式：
    {{
    "chapters": [
        {{
        "start_index": 0,
        "title": "章节标题",
        "summary": "章节摘要"
        }}
    ]
    }}

    转写内容：
    {transcript}
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
        raise ValueError("模型没有返回内容")

    result = json.loads(content)

    # 4. 校验编号，并映射回真实时间戳
    chapters = []
    previous_index = -1

    for item in result["chapters"]:
        index = item["start_index"]

        if type(index) is not int or not 0 <= index < len(segments):
            raise ValueError(f"无效的章节编号: {index}")

        if index <= previous_index:
            raise ValueError("章节编号必须严格递增")

        chapters.append({
            "start": segments[index]["start"],
            "title": item["title"],
            "summary": item["summary"]
        })

        previous_index = index

    if not chapters or chapters[0]["start"] != segments[0]["start"]:
        raise ValueError("第一章必须从第一个转写片段开始")

    # 5. 保存结果
    output = {"chapters": chapters}

    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"生成完成，共 {len(chapters)} 个章节")
    print("结果已保存至 chapters.json")

    return output
