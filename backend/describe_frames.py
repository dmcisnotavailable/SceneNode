import base64
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

MODEL = os.getenv("VISION_MODEL", "qwen3-vl-flash")


def image_to_data_url(image_path: Path) -> str:
    encoded = base64.b64encode(
        image_path.read_bytes()
    ).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"


def parse_description(content: str) -> dict:
    text = content.strip()

    # 兼容模型偶尔返回 Markdown 代码块
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    result = json.loads(text)

    required = ["scene", "description", "visible_objects"]

    if not isinstance(result, dict):
        raise ValueError("模型返回的不是 JSON 对象")

    for field in required:
        if field not in result:
            raise ValueError(f"缺少字段: {field}")

    if not isinstance(result["scene"], str):
        raise ValueError("scene 必须是字符串")

    if not isinstance(result["description"], str):
        raise ValueError("description 必须是字符串")

    if (
        not isinstance(result["visible_objects"], list)
        or not all(
            isinstance(x, str)
            for x in result["visible_objects"]
        )
    ):
        raise ValueError("visible_objects 格式错误")

    return result


def describe_one_frame(client, image_path: Path):
    prompt = """
你是一个旅行视频的画面分析助手。

请仅根据当前图片中实际可见的内容进行描述。

要求：
1. 识别主要场景，例如海边、街道、山林、建筑。
2. 描述可见的人物活动、物体和环境。
3. 不要猜测具体地名、人物身份或拍摄者意图。
4. 单张图片不能证明连续动作，不要编造事件经过。
5. 描述简洁，适合后续视频章节生成。
6. 只返回合法 JSON，不要 Markdown。

输出格式：
{
  "scene": "简短场景名称",
  "description": "一句话描述画面",
  "visible_objects": ["物体1", "物体2"]
}
"""

    start_time = time.perf_counter()

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_to_data_url(image_path)
                        },
                    },
                ],
            }
        ],
        temperature=0,
        max_tokens=300,
    )

    latency = time.perf_counter() - start_time

    content = response.choices[0].message.content

    if not isinstance(content, str) or not content:
        raise ValueError("模型没有返回文本内容")

    description = parse_description(content)

    usage = response.usage

    return {
        **description,
        "model": MODEL,
        "input_tokens": (
            usage.prompt_tokens if usage else None
        ),
        "output_tokens": (
            usage.completion_tokens if usage else None
        ),
        "latency_seconds": round(latency, 2),
    }


def describe_frames(frames_dir: str, limit: int = 1):
    directory = Path(frames_dir).resolve()
    manifest_path = directory / "frames.json"
    output_path = directory / "visual_descriptions.json"

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if not 1 <= limit <= 6:
        raise ValueError("本次实验 limit 必须在 1 到 6 之间")

    api_key = os.getenv("VISION_API_KEY")
    base_url = os.getenv("VISION_BASE_URL")

    if not api_key or not base_url:
        raise ValueError("请先配置 VISION_API_KEY 和 VISION_BASE_URL")

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=60.0,
        max_retries=0,
    )

    if output_path.exists():
        saved = json.loads(
            output_path.read_text(encoding="utf-8")
        )
    else:
        saved = {"model": MODEL, "frames": []}

    if saved.get("model") != MODEL:
        raise ValueError(
            "缓存模型与当前模型不同，请使用新输出目录"
        )

    completed = {
        item["frame_id"]
        for item in saved["frames"]
    }

    for frame in manifest["frames"][:limit]:
        frame_id = frame["frame_id"]

        if frame_id in completed:
            print(f"跳过已完成帧 {frame_id}")
            continue

        image_path = directory / frame["image_path"]

        print(f"正在分析帧 {frame_id}: {image_path.name}")

        try:
            result = describe_one_frame(
                client, image_path
            )
        except Exception as exc:
            print(f"分析失败: {type(exc).__name__}: {exc}")
            raise

        saved["frames"].append({
            "frame_id": frame_id,
            "timestamp": frame["timestamp"],
            "image_path": frame["image_path"],
            **result,
        })

        # 每成功一帧就保存，避免中途失败后重复计费
        temp_path = output_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(saved, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(output_path)

        completed.add(frame_id)

        print(
            f"完成：{result['scene']} | "
            f"输入 {result['input_tokens']} tokens | "
            f"输出 {result['output_tokens']} tokens"
        )

    return saved


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(
            "用法: python describe_frames.py 图片目录 [数量]"
        )

    limit = int(sys.argv[2]) if len(sys.argv) == 3 else 1

    describe_frames(sys.argv[1], limit)
