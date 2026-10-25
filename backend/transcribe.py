import json
from pathlib import Path
from faster_whisper import WhisperModel


def transcribe_audio(audio_path: str, output_path: str):
    print(f"开始转写：{audio_path}")

    model = WhisperModel(
        "small",
        device="cpu",
        compute_type="int8"
    )

    segments, info = model.transcribe(
    audio_path,
    language=None,
    task="transcribe",
    beam_size=5,
    vad_filter=True,
)

    results = []

    for segment in segments:
        results.append({
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "text": segment.text.strip()
        })

    output = {
        "language": info.language,
        "segments": results
    }

    Path(output_path).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"转写完成，保存到：{output_path}")
    return output


if __name__ == "__main__":
    transcribe_audio(
        "audio_short.wav",
        "transcript.json"
    )
