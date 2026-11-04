import json
import os
import subprocess
import threading
import time

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from checkpoint import CheckpointManager
from task_repository import TaskRepository
from transcribe import transcribe_audio
from extract_frames import extract_frames
from describe_frames import describe_frames
from fuse_multimodal import fuse_multimodal
from generate_chapters import generate_chapters
from perf_utils import measure, save_metrics


BASE_DIR = Path(__file__).resolve().parent
JOB_DIR = BASE_DIR / "jobs"

# SQLite 是任务状态的唯一权威来源
TASK_REPOSITORY = TaskRepository()

_status_lock = threading.Lock()
_active_lock = threading.Lock()
_active_video_ids = set()


def is_video_active(video_id):
    with _active_lock:
        return video_id in _active_video_ids


def update_status(video_id, status, message):
    """统一通过 SQLite 更新任务状态。"""
    with _status_lock:
        return TASK_REPOSITORY.update_status(
            video_id,
            status,
            message,
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


def _video_fingerprint(video_path):
    stat = Path(video_path).stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _save_empty_transcript(path):
    Path(path).write_text(
        json.dumps(
            {"language": None, "segments": []},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run_audio_branch(
    video_id,
    video_path,
    audio_path,
    transcript_path,
    checkpoint,
):
    metrics = {}
    warnings = []

    video_config = _video_fingerprint(video_path)

    audio_config = {
        "sample_rate": 16000,
        "channels": 1,
        "codec": "pcm_s16le",
        "source": video_config,
    }

    transcript_config = {
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 1,
        "vad_filter": True,
    }

    if checkpoint.is_valid(
        "transcript",
        transcript_config,
        dependencies=["audio.wav"],
    ):
        print(
            f"[CHECKPOINT] {video_id}: "
            "复用 transcript.json"
        )
        return metrics, warnings

    audio_valid = checkpoint.is_valid(
        "audio",
        audio_config,
    )

    if not audio_valid:
        with measure("audio_probe", video_id, metrics):
            audio_exists = has_audio_stream(video_path)

        if not audio_exists:
            warnings.append("视频没有音轨")
            _save_empty_transcript(transcript_path)
            raise ValueError("视频没有音轨，无法生成转写")

        with measure("audio_extract", video_id, metrics):
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

        checkpoint.save("audio", audio_config)

    with measure("whisper_infer", video_id, metrics):
        transcribe_audio(
            str(audio_path),
            str(transcript_path),
            video_id=video_id,
            metrics=metrics,
        )

    checkpoint.save(
        "transcript",
        transcript_config,
        dependencies=["audio.wav"],
    )

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
        with measure("frame_extract", video_id, metrics):
            manifest = extract_frames(
                str(video_path),
                str(frames_dir),
                count=6,
            )

        video_duration = manifest.get("video_duration")

        with measure("vision_total", video_id, metrics):
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


def _process_video_impl(video_id, video_path):
    job_path = JOB_DIR / video_id
    job_path.mkdir(parents=True, exist_ok=True)

    audio_path = job_path / "audio.wav"
    transcript_path = job_path / "transcript.json"
    frames_dir = job_path / "frames"
    visual_path = frames_dir / "visual_descriptions.json"
    timeline_path = job_path / "multimodal_timeline.json"
    chapters_path = job_path / "chapters.json"

    checkpoint = CheckpointManager(job_path, video_id)

    metrics = {}
    warnings = []
    pipeline_start = time.perf_counter()

    parallel = os.getenv("PIPELINE_PARALLEL", "0") == "1"
    vision_workers = int(os.getenv("VISION_WORKERS", "2"))

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

        with measure(
            "processing_branches",
            video_id,
            metrics,
        ):
            if parallel:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    audio_future = executor.submit(
                        run_audio_branch,
                        video_id,
                        video_path,
                        audio_path,
                        transcript_path,
                        checkpoint,
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
                audio_metrics, audio_warnings = run_audio_branch(
                    video_id,
                    video_path,
                    audio_path,
                    transcript_path,
                    checkpoint,
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

        metrics.update(audio_metrics)
        metrics.update(visual_metrics)

        warnings.extend(audio_warnings)
        warnings.extend(visual_warnings)

        update_status(
            video_id,
            "fusing",
            "正在融合语音与画面信息",
        )

        timeline_config = {
            "fusion_version": 1,
        }

        timeline_dependencies = []

        if transcript_path.is_file():
            timeline_dependencies.append("transcript.json")

        if visual_path.is_file():
            timeline_dependencies.append(
                "frames/visual_descriptions.json"
            )

        if checkpoint.is_valid(
            "timeline",
            timeline_config,
            dependencies=timeline_dependencies,
        ):
            timeline = json.loads(
                timeline_path.read_text(encoding="utf-8")
            )
            print(
                f"[CHECKPOINT] {video_id}: "
                "复用多模态时间轴"
            )
        else:
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

            if not timeline.get("events"):
                raise ValueError(
                    "没有可用的语音或视觉内容"
                )

            checkpoint.save(
                "timeline",
                timeline_config,
                dependencies=timeline_dependencies,
            )

        if not timeline.get("events"):
            raise ValueError(
                "没有可用的语音或视觉内容"
            )

        update_status(
            video_id,
            "generating",
            "正在生成多模态章节",
        )

        chapter_config = {
            "model": os.getenv("LLM_MODEL", ""),
            "chapter_version": 1,
        }

        if checkpoint.is_valid(
            "chapters",
            chapter_config,
            dependencies=["multimodal_timeline.json"],
        ):
            print(
                f"[CHECKPOINT] {video_id}: "
                "复用 chapters.json"
            )
        else:
            with measure(
                "chapter_llm",
                video_id,
                metrics,
            ):
                generate_chapters(
                    timeline_path,
                    chapters_path,
                )

            checkpoint.save(
                "chapters",
                chapter_config,
                dependencies=["multimodal_timeline.json"],
            )

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

        try:
            task = TASK_REPOSITORY.get_task(video_id)

            if task and task["status"] not in (
                "failed",
                "completed",
            ):
                update_status(
                    video_id,
                    "failed",
                    "视频分析失败，请检查后端日志",
                )

        except Exception as status_exc:
            print(
                f"[ERROR] 更新失败状态也失败: "
                f"{type(status_exc).__name__}: {status_exc}"
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
            save_metrics(
                job_path,
                video_id,
                metrics,
            )
        except Exception as exc:
            print(f"[PERF] 保存失败：{exc}")


def process_video(video_id, video_path):
    """同一进程内同一视频只允许一个 Pipeline 实例执行。"""

    with _active_lock:
        if video_id in _active_video_ids:
            print(
                f"[TASK] 忽略重复任务: {video_id}"
            )
            return

        _active_video_ids.add(video_id)

    try:
        _process_video_impl(video_id, video_path)
    finally:
        with _active_lock:
            _active_video_ids.discard(video_id)
