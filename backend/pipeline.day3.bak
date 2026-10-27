import json
import subprocess
import time

from pathlib import Path

from transcribe import transcribe_audio
from extract_frames import extract_frames
from describe_frames import describe_frames
from fuse_multimodal import fuse_multimodal
from generate_chapters import generate_chapters
from perf_utils import measure, save_metrics
from concurrent.futures import ThreadPoolExecutor
import threading

_status_lock = threading.Lock()

BASE_DIR = Path(__file__).resolve().parent
JOB_DIR = BASE_DIR / "jobs"


def update_status(video_id, status, message):
    job_path = JOB_DIR / video_id
    job_path.mkdir(parents=True, exist_ok=True)

    status_path = job_path / "status.json"
    temp_path = job_path / "status.json.tmp"

    data = {
        "video_id": video_id,
        "status": status,
        "message": message,
    }

    with _status_lock:
        temp_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp_path.replace(status_path)



def save_empty_transcript(path):
    Path(path).write_text(
        json.dumps(
            {"language": None, "segments": []},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def has_audio_stream(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return bool(result.stdout.strip())


def run_audio_branch(
    video_id,
    video_path,
    audio_path,
    transcript_path,
    metrics,
):
    warnings = []

    update_status(
        video_id,
        "extracting",
        "正在提取音频",
    )

    with measure("audio_probe", video_id, metrics):
        audio_exists = has_audio_stream(video_path)

    if not audio_exists:
        save_empty_transcript(transcript_path)
        return ["视频没有音轨"]

    try:
        with measure(
            "audio_extract", video_id, metrics
        ):
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(video_path),
                    "-vn",
                    "-ac", "1",
                    "-ar", "16000",
                    "-c:a", "pcm_s16le",
                    str(audio_path),
                ],
                check=True,
                capture_output=True,
                timeout=600,
            )

        update_status(
            video_id,
            "transcribing",
            "正在进行 Whisper 语音转写",
        )

        transcribe_audio(
            str(audio_path),
            str(transcript_path),
            video_id=video_id,
            metrics=metrics,
        )

    except Exception as exc:
        warnings.append(
            f"语音处理失败: {type(exc).__name__}"
        )
        save_empty_transcript(transcript_path)

    return warnings


def run_visual_branch(
    video_id,
    video_path,
    frames_dir,
    metrics,
):
    warnings = []
    video_duration = None

    update_status(
        video_id,
        "extracting_frames",
        "正在提取视频画面",
    )

    try:
        with measure(
            "frame_extract", video_id, metrics
        ):
            manifest = extract_frames(
                str(video_path),
                str(frames_dir),
                count=6,
            )

        video_duration = manifest.get(
            "video_duration"
        )

        update_status(
            video_id,
            "describing",
            "正在分析视频画面",
        )

        with measure(
            "vision_total", video_id, metrics
        ):
            describe_frames(
                str(frames_dir),
                limit=6,
                max_workers=2,
            )

    except Exception as exc:
        warnings.append(
            f"视觉处理失败: {type(exc).__name__}"
        )
        print(
            f"视频 {video_id} 视觉分析失败: {exc}"
        )

    return video_duration, warnings


def process_video(video_id, video_path):
    job_path = JOB_DIR / video_id
    job_path.mkdir(parents=True, exist_ok=True)

    audio_path = job_path / "audio.wav"
    transcript_path = job_path / "transcript.json"
    frames_dir = job_path / "frames"
    visual_path = frames_dir / "visual_descriptions.json"
    timeline_path = job_path / "multimodal_timeline.json"
    chapters_path = job_path / "chapters.json"

    warnings = []
    metrics = {}
    pipeline_start = time.perf_counter()

    try:        
        # ---------- 1. 并行执行 ASR 与视觉 ----------
        update_status(
            video_id,
            "processing",
            "正在并行分析语音与画面",
        )

        with measure("parallel_branches", video_id, metrics):
            with ThreadPoolExecutor(max_workers=2) as executor:
                audio_future = executor.submit(
                    run_audio_branch,
                    video_id,
                    video_path,
                    audio_path,
                    transcript_path,
                    metrics,
                )

                visual_future = executor.submit(
                    run_visual_branch,
                    video_id,
                    video_path,
                    frames_dir,
                    metrics,
                )

                audio_warnings = audio_future.result()
                video_duration, visual_warnings = (
                    visual_future.result()
                )

        warnings.extend(audio_warnings)
        warnings.extend(visual_warnings)


        # ---------- 4. 多模态融合 ----------
        update_status(
            video_id,
            "fusing",
            "正在融合语音与画面信息",
        )

        with measure(
            "multimodal_fuse", video_id, metrics
        ):
            timeline = fuse_multimodal(
                transcript_path=transcript_path,
                visual_path=visual_path,
                output_path=timeline_path,
                video_duration=video_duration,
            )

        if not timeline["events"]:
            raise ValueError(
                "没有可用的语音或视觉内容"
            )

        # ---------- 5. 章节生成 ----------
        update_status(
            video_id,
            "generating",
            "正在生成多模态章节",
        )

        with measure(
            "chapter_llm", video_id, metrics
        ):
            generate_chapters(
                timeline_path,
                chapters_path,
            )

        # ---------- 6. 完成 ----------
        message = "多模态分析完成"

        if warnings:
            message += "（部分能力降级）"
            print(
                f"视频 {video_id} 警告: "
                + "; ".join(warnings)
            )

        update_status(
            video_id,
            "completed",
            message,
        )

    except Exception as exc:
        print(
            f"视频 {video_id} 分析失败: "
            f"{type(exc).__name__}: {exc}"
        )

        update_status(
            video_id,
            "failed",
            "视频分析失败，请检查后端日志",
        )

    finally:
        total = round(
            time.perf_counter() - pipeline_start,
            3,
        )

        metrics["pipeline_total"] = {
            "seconds": total,
            "result": "measured",
            "error": None,
        }

        try:
            path = save_metrics(
                job_path,
                video_id,
                metrics,
            )
            print(f"[PERF] 性能报告已保存：{path}")
        except Exception as exc:
            print(
                "[PERF] 保存性能报告失败："
                f"{type(exc).__name__}: {exc}"
            )
