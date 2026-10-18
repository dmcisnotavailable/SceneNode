import json
from pathlib import Path
from faster_whisper import WhisperModel

# 1. 加载本地语音识别模型
print("正在加载 Whisper 模型...")

model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

# 2. 识别音频
print("开始语音转写...")

segments, info = model.transcribe(
    "audio_short.wav",
    language="en",
    task="transcribe",
    beam_size=5,
    vad_filter=True
)

# 3. 转换为结构化数据
results = []

for segment in segments:
    results.append({
        "start": round(segment.start, 2),
        "end": round(segment.end, 2),
        "text": segment.text.strip()
    })

# 4. 保存 JSON
output = {
    "language": info.language,
    "segments": results
}

Path("transcript1.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(f"转写完成，共 {len(results)} 个片段")
print("结果已保存至 transcript.json")
