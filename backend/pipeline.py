import json
import subprocess
from pathlib import Path

from transcribe import transcribe_audio
from extract_frames import extract_frames
from describe_frames import describe_frames
from fuse_multimodal import fuse_multimodal
from generate_chapters import generate_chapters


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

    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
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

    try:
        # ---------- 1. 音频处理 ----------
        update_status(
            video_id, "extracting", "正在提取音频"
        )

        if has_audio_stream(video_path):
            try:
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
                )

            except Exception as exc:
                warnings.append(
                    f"语音处理失败: {type(exc).__name__}"
                )
                save_empty_transcript(transcript_path)
        else:
            warnings.append("视频没有音轨")
            save_empty_transcript(transcript_path)

        # ---------- 2. 视觉抽帧 ----------
        update_status(
            video_id,
            "extracting_frames",
            "正在提取视频画面",
        )

        visual_data = {"frames": []}
        video_duration = None

        try:
            manifest = extract_frames(
                str(video_path),
                str(frames_dir),
                count=6,
            )

            video_duration = manifest.get(
                "video_duration"
            )

            # ---------- 3. Qwen-VL ----------
            update_status(
                video_id,
                "describing",
                "正在分析视频画面",
            )

            visual_data = describe_frames(
                str(frames_dir),
                limit=6,
            )

        except Exception as exc:
            warnings.append(
                f"视觉处理失败: {type(exc).__name__}"
            )
            print(
                f"视频 {video_id} 视觉分析失败: {exc}"
            )

            # 保留已经成功保存的视觉描述
            if visual_path.exists():
                try:
                    visual_data = json.loads(
                        visual_path.read_text(
                            encoding="utf-8"
                        )
                    )
                except Exception:
                    visual_data = {"frames": []}

        # ---------- 4. 多模态融合 ----------
        update_status(
            video_id,
            "fusing",
            "正在融合语音与画面信息",
        )

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
