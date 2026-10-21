import json
import subprocess
from pathlib import Path
from transcribe import transcribe_audio
from generate_chapters import generate_chapters

BASE_DIR = Path(__file__).resolve().parent
JOB_DIR = BASE_DIR / "jobs"


def update_status(video_id, status, message):
    job_path = JOB_DIR / video_id
    job_path.mkdir(parents=True, exist_ok=True)

    status_path = job_path / "status.json"

    status_path.write_text(
        json.dumps({
            "video_id": video_id,
            "status": status,
            "message": message
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def process_video(video_id, video_path):
    job_path = JOB_DIR / video_id
    job_path.mkdir(parents=True, exist_ok=True)

    audio_path = job_path / "audio.wav"
    transcript_path = job_path / "transcript.json"
    chapters_path = job_path / "chapters.json"

    try:
        # 1. 提取音频
        update_status(video_id, "extracting", "正在提取音频")

        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(audio_path)
        ], check=True, capture_output=True, timeout=600)

        # 2. Whisper 转写
        update_status(video_id, "transcribing", "正在语音转写")
        transcribe_audio(str(audio_path), str(transcript_path))

        # 3. LLM 生成章节
        update_status(video_id, "generating", "正在生成章节")
        generate_chapters(transcript_path, chapters_path)

        # 4. 完成
        update_status(video_id, "completed", "分析完成")

    except Exception as e:
        print(f"视频 {video_id} 分析失败: {e}")
        update_status(video_id, "failed", "视频分析失败，请检查后端日志")
