import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    FastAPI,
    BackgroundTasks,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pipeline import process_video, JOB_DIR, update_status
from fastapi import Query
from search import search_chunks

app = FastAPI()

# 允许本地 React 开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOB_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 200 * 1024 * 1024


# ---------- 公共工具函数 ----------

def validate_video_id(video_id: str):
    """校验 video_id，防止非法路径访问。"""
    try:
        parsed = UUID(hex=video_id)
        if parsed.hex != video_id:
            raise ValueError("Invalid ID")
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail="无效的视频 ID"
        )


def read_json(path: Path):
    """安全读取 JSON；文件不存在或损坏时返回 None。"""
    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: dict):
    """写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ---------- 上传视频 ----------

@app.post("/api/videos")
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    if (
        not file.filename
        or Path(file.filename).suffix.lower() != ".mp4"
    ):
        raise HTTPException(
            status_code=400,
            detail="目前仅支持 MP4 文件"
        )

    video_id = uuid4().hex
    save_path = UPLOAD_DIR / f"{video_id}.mp4"

    size = 0

    try:
        with save_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)

                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="视频不能超过 200 MB"
                    )

                output.write(chunk)

    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    finally:
        await file.close()

    # 新增：保存原始文件元数据
    metadata = {
        "video_id": video_id,
        "filename": Path(
            file.filename.replace("\\", "/")
        ).name,
        "size": size,
        "uploaded_at": datetime.now(
            timezone.utc
        ).isoformat()
    }

    metadata_path = JOB_DIR / video_id / "metadata.json"
    write_json(metadata_path, metadata)

    # 保留原有的任务状态和分析流程
    update_status(video_id, "queued", "等待分析")

    background_tasks.add_task(
        process_video,
        video_id,
        save_path
    )

    return {
        "video_id": video_id,
        "filename": metadata["filename"],
        "size": size,
        "status": "queued"
    }


# ---------- 历史视频列表 ----------

@app.get("/api/videos")
def list_videos():
    videos = []

    # 从已保存的视频文件出发
    for video_path in UPLOAD_DIR.glob("*.mp4"):
        video_id = video_path.stem

        try:
            validate_video_id(video_id)
        except HTTPException:
            continue

        job_path = JOB_DIR / video_id

        metadata = read_json(
            job_path / "metadata.json"
        )

        status_data = read_json(
            job_path / "status.json"
        )

        # 兼容旧视频：旧版本没有 metadata.json
        if not isinstance(metadata, dict):
            metadata = {}

        if not isinstance(status_data, dict):
            status_data = {}

        uploaded_at = metadata.get("uploaded_at")

        if not uploaded_at:
            uploaded_at = datetime.fromtimestamp(
                video_path.stat().st_mtime,
                timezone.utc
            ).isoformat()

        videos.append({
            "video_id": video_id,
            "filename": metadata.get(
                "filename",
                video_path.name
            ),
            "size": video_path.stat().st_size,
            "uploaded_at": uploaded_at,
            "status": status_data.get(
                "status",
                "unknown"
            ),
            "message": status_data.get(
                "message",
                "暂无分析状态"
            ),
            "video_url": (
                f"/api/videos/{video_id}/file"
            )
        })

    # 最近上传的视频排在前面
    videos.sort(
        key=lambda item: item["uploaded_at"],
        reverse=True
    )

    return {
        "total": len(videos),
        "videos": videos
    }


# ---------- 查询单个任务状态 ----------

@app.get("/api/videos/{video_id}")
def get_video_status(video_id: str):
    validate_video_id(video_id)

    job_path = JOB_DIR / video_id
    status_path = job_path / "status.json"

    if not status_path.exists():
        raise HTTPException(
            status_code=404,
            detail="视频任务不存在"
        )

    result = read_json(status_path)

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=500,
            detail="任务状态文件损坏"
        )

    if result.get("status") == "completed":
        chapters_path = job_path / "chapters.json"
        chapters_data = read_json(chapters_path)

        if (
            not isinstance(chapters_data, dict)
            or not isinstance(
                chapters_data.get("chapters"),
                list
            )
        ):
            raise HTTPException(
                status_code=500,
                detail="章节数据不存在或已损坏"
            )

        result["chapters"] = chapters_data["chapters"]

    return result


# ---------- 读取原始视频 ----------

@app.get("/api/videos/{video_id}/file")
def get_video_file(video_id: str):
    validate_video_id(video_id)

    video_path = UPLOAD_DIR / f"{video_id}.mp4"

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="视频文件不存在"
        )

    return FileResponse(
        path=video_path,
        media_type="video/mp4"
    )


# ---------- 单视频关键词检索 ----------

@app.get("/api/videos/{video_id}/search")
def search_video(video_id: str, q: str = Query(..., max_length=100)):
    # 1. 校验视频 ID
    validate_video_id(video_id)

    # 2. 检查视频是否存在
    video_path = UPLOAD_DIR / f"{video_id}.mp4"

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="视频不存在"
        )

    # 3. 检查分析任务状态
    job_path = JOB_DIR / video_id
    status_path = job_path / "status.json"

    status_data = read_json(status_path)

    if not isinstance(status_data, dict):
        raise HTTPException(
            status_code=404,
            detail="视频分析任务不存在或状态文件不可读取"
        )

    if status_data.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail="视频尚未完成分析，暂时无法搜索"
        )

    # 4. 校验搜索关键词
    keyword = q.strip()

    if not keyword:
        raise HTTPException(
            status_code=400,
            detail="搜索关键词不能为空"
        )

    # 5. 读取转写文件
    transcript_path = job_path / "transcript.json"

    if not transcript_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="视频转写文件不存在"
        )

    transcript = read_json(transcript_path)

    if (
        not isinstance(transcript, dict)
        or not isinstance(transcript.get("segments"), list)
    ):
        raise HTTPException(
            status_code=500,
            detail="转写数据不存在或格式错误"
        )

    # 6. 检查 segment 数据结构
    for segment in transcript["segments"]:
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("text"), str)
            or type(segment.get("start")) not in (int, float)
            or type(segment.get("end")) not in (int, float)
        ):
            raise HTTPException(
                status_code=500,
                detail="转写片段数据格式错误"
            )

    # 7. 执行关键词搜索
    results = search_segments(transcript, keyword)

    # 8. 返回搜索结果
    return {
        "video_id": video_id,
        "query": keyword,
        "total": len(results),
        "results": results
    }

