import json
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi import BackgroundTasks
from pipeline import process_video, JOB_DIR, update_status

app = FastAPI()

# 允许本地 React 开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


@app.post("/api/videos")
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    if not file.filename or Path(file.filename).suffix.lower() != ".mp4":
        raise HTTPException(400, "目前仅支持 MP4 文件")

    video_id = uuid4().hex
    save_path = UPLOAD_DIR / f"{video_id}.mp4"

    size = 0

    try:
        with save_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)

                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, "视频不能超过 200 MB")

                output.write(chunk)

    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    finally:
        await file.close()

    # 创建初始任务状态
    update_status(video_id, "queued", "等待分析")

    # 注册后台分析任务
    background_tasks.add_task(
        process_video,
        video_id,
        save_path
    )

    return {
        "video_id": video_id,
        "filename": file.filename,
        "size": size,
        "status": "queued",
    }


from uuid import UUID


@app.get("/api/videos/{video_id}")
def get_video_status(video_id: str):
    # 校验 ID，避免任意路径访问
    try:
        UUID(hex=video_id)
    except ValueError:
        raise HTTPException(400, "无效的视频 ID")

    job_path = JOB_DIR / video_id
    status_path = job_path / "status.json"

    if not status_path.exists():
        raise HTTPException(404, "视频任务不存在")

    result = json.loads(
        status_path.read_text(encoding="utf-8")
    )

    if result["status"] == "completed":
        chapters_path = job_path / "chapters.json"

        result["chapters"] = json.loads(
            chapters_path.read_text(encoding="utf-8")
        )["chapters"]

    return result

