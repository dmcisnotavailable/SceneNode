import json
import threading
from pathlib import Path

from faster_whisper import WhisperModel
from perf_utils import measure

_model = None
_model_lock = threading.Lock()

# 同一进程内限制模型推理并发，
# 避免多个视频同时推理导致 CPU 资源竞争
_infer_lock = threading.Lock()


def get_model(video_id=None, metrics=None):
    global _model

    with _model_lock:
        if _model is None:
            with measure(
                "whisper_init",
                video_id,
                metrics,
            ):
                _model = WhisperModel(
                    "small",
                    device="cpu",
                    compute_type="int8",
                )
        else:
            if metrics is not None:
                metrics["whisper_init"] = {
                    "seconds": 0.0,
                    "result": "cache_hit",
                    "error": None,
                }

    return _model


def transcribe_audio(
    audio_path: str,
    output_path: str,
    video_id=None,
    metrics=None,
):
    print(f"开始转写：{audio_path}")

    model = get_model(video_id, metrics)

    with _infer_lock:
        with measure(
            "whisper_infer",
            video_id,
            metrics,
        ):
            segments, info = model.transcribe(
                audio_path,
                language=None,
                task="transcribe",
                beam_size=1,
                vad_filter=True,
            )

            results = []

            for segment in segments:
                results.append({
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": segment.text.strip(),
                })

    output = {
        "language": info.language,
        "segments": results,
    }

    with measure(
        "transcript_save",
        video_id,
        metrics,
    ):
        Path(output_path).write_text(
            json.dumps(
                output,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"转写完成，保存到：{output_path}")

    return output


if __name__ == "__main__":
    transcribe_audio(
        "audio_short.wav",
        "transcript.json",
    )
