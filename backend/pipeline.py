import json
import os
import subprocess
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from transcribe import transcribe_audio
from extract_frames import extract_frames
from describe_frames import describe_frames
from fuse_multimodal import fuse_multimodal
from generate_chapters import generate_chapters
from perf_utils import measure, save_metrics


BASE_DIR = Path(__file__).resolve().parent
JOB_DIR = BASE_DIR / "jobs"

_status_lock = threading.Lock()


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
):
    metrics = {}
    warnings = []

    try:
        with measure(
            "audio_probe", video_id, metrics
        ):
            audio_exists = has_audio_stream(video_path)

        if not audio_exists:
            warnings.append("视频没有音轨")
            save_empty_transcript(transcript_path)
            return metrics, warnings

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
        print(
            f"[AUDIO] {video_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        save_empty_transcript(transcript_path)

    return metrics, warnings


def run_visual_branch(
    video_id,
    video_path,
    frames_dir,
    max_workers,
):
    metrics = {}
    warnings = []
    video_duration = None

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

        with measure(
            "vision_total", video_id, metrics
        ):
            describe_frames(
                str(frames_dir),
                limit=6,
                max_workers=max_workers,
            )

    except Exception as exc:
        warnings.append(
            f"视觉处理失败: {type(exc).__name__}"
        )
        print(
            f"[VISION] {video_id}: "
            f"{type(exc).__name__}: {exc}"
        )

    return metrics, warnings, video_duration


def process_video(video_id, video_path):
    job_path = JOB_DIR / video_id
    job_path.mkdir(parents=True, exist_ok=True)

    audio_path = job_path / "audio.wav"
    transcript_path = job_path / "transcript.json"
    frames_dir = job_path / "frames"
    visual_path = frames_dir / "visual_descriptions.json"
    timeline_path = job_path / "multimodal_timeline.json"
    chapters_path = job_path / "chapters.json"

    metrics = {}
    warnings = []
    pipeline_start = time.perf_counter()

    # 测试开关：每次任务开始时读取
    parallel = (
        os.getenv("PIPELINE_PARALLEL", "1") == "1"
    )

    vision_workers = int(
        os.getenv("VISION_WORKERS", "2")
    )

    if not 1 <= vision_workers <= 6:
        raise ValueError(
            "VISION_WORKERS 必须在 1 到 6 之间"
        )

    try:
        update_status(
            video_id,
            "processing",
            "正在分析视频语音与画面",
        )

        # ---------- 1. ASR / 视觉处理 ----------
        with measure(
            "processing_branches",
            video_id,
            metrics,
        ):
            if parallel:
                with ThreadPoolExecutor(
                    max_workers=2
                ) as executor:
                    audio_future = executor.submit(
                        run_audio_branch,
                        video_id,
                        video_path,
                        audio_path,
                        transcript_path,
                    )

                    visual_future = executor.submit(
                        run_visual_branch,
                        video_id,
                        video_path,
                        frames_dir,
                        vision_workers,
                    )

                    audio_metrics, audio_warnings = (
                        audio_future.result()
                    )

                    (
                        visual_metrics,
                        visual_warnings,
                        video_duration,
                    ) = visual_future.result()

            else:
                audio_metrics, audio_warnings = (
                    run_audio_branch(
                        video_id,
                        video_path,
                        audio_path,
                        transcript_path,
                    )
                )

                (
                    visual_metrics,
                    visual_warnings,
                    video_duration,
                ) = run_visual_branch(
                    video_id,
                    video_path,
                    frames_dir,
                    vision_workers,
                )

        # 只有主线程合并指标
        metrics.update(audio_metrics)
        metrics.update(visual_metrics)

        warnings.extend(audio_warnings)
        warnings.extend(visual_warnings)

        # ---------- 2. 多模态融合 ----------
        update_status(
            video_id,
            "fusing",
            "正在融合语音与画面信息",
        )

        with measure(
            "multimodal_fuse",
            video_id,
            metrics,
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

        # ---------- 3. 章节生成 ----------
        update_status(
            video_id,
            "generating",
            "正在生成多模态章节",
        )

        with measure(
            "chapter_llm",
            video_id,
            metrics,
        ):
            generate_chapters(
                timeline_path,
                chapters_path,
            )

        # ---------- 4. 完成 ----------
        message = "多模态分析完成"

        if warnings:
            message += "（部分能力降级）"
            print(
                f"[WARN] {video_id}: "
                + "; ".join(warnings)
            )

        update_status(
            video_id,
            "completed",
            message,
        )

    except Exception as exc:
        print(
            f"[ERROR] {video_id}: "
            f"{type(exc).__name__}: {exc}"
        )

        update_status(
            video_id,
            "failed",
            "视频分析失败，请检查后端日志",
        )

    finally:
        metrics["pipeline_total"] = {
            "seconds": round(
                time.perf_counter() - pipeline_start,
                3,
            ),
            "result": "measured",
            "error": None,
        }

        metrics["config"] = {
            "pipeline_parallel": parallel,
            "vision_workers": vision_workers,
        }

        try:
            path = save_metrics(
                job_path,
                video_id,
                metrics,
            )
            print(
                f"[PERF] 性能报告：{path}"
            )
        except Exception as exc:
            print(
                f"[PERF] 保存失败：{exc}"
            )
