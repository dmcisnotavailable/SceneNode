import json
import threading
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

from pipeline import (
    process_video,
    JOB_DIR,
    TASK_REPOSITORY,
    update_status,
    is_video_active,
)
from fastapi import Query
from search import search_chunks
from semantic_service import semantic_search_video

app = FastAPI()
_retry_lock = threading.Lock()

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

    metadata = {
        "video_id": video_id,
        "filename": Path(
            file.filename.replace("\\", "/")
        ).name,
        "size": size,
        "uploaded_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    try:
        TASK_REPOSITORY.create_video_task(
            video_id=video_id,
            filename=metadata["filename"],
            file_path=str(save_path.resolve()),
            size=size,
        )
    except Exception:
        save_path.unlink(missing_ok=True)
        raise


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
    videos = TASK_REPOSITORY.list_videos(limit=500)

    result = []

    for video in videos:
        result.append({
            "video_id": video["video_id"],
            "filename": video["filename"],
            "size": video["size"],
            "uploaded_at": video["uploaded_at"],
            "status": video["status"],
            "message": video["message"],
            "attempt": video["attempt"],
            "video_url": (
                f"/api/videos/{video['video_id']}/file"
            ),
        })

    return {
        "total": len(result),
        "videos": result,
    }



# ---------- 查询单个任务状态 ----------

@app.get("/api/videos/{video_id}")

@app.get("/api/videos/{video_id}")
def get_video_status(video_id: str):
    validate_video_id(video_id)

    result = TASK_REPOSITORY.get_task(video_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="视频任务不存在",
        )

    if result["status"] == "completed":
        chapters_path = (
            JOB_DIR / video_id / "chapters.json"
        )

        chapters_data = read_json(chapters_path)

        if (
            not isinstance(chapters_data, dict)
            or not isinstance(
                chapters_data.get("chapters"),
                list,
            )
        ):
            raise HTTPException(
                status_code=500,
                detail="章节数据不存在或已损坏",
            )

        result["chapters"] = chapters_data["chapters"]

    return result



@app.post("/api/videos/{video_id}/retry")
def retry_video(
    video_id: str,
    background_tasks: BackgroundTasks,
):
    validate_video_id(video_id)

    video_path = UPLOAD_DIR / f"{video_id}.mp4"

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="原始视频文件不存在，无法重试",
        )

    with _retry_lock:
        if is_video_active(video_id):
            raise HTTPException(
                status_code=409,
                detail="该视频任务仍在运行",
            )

        task = TASK_REPOSITORY.get_task(video_id)

        if task is None:
            raise HTTPException(
                status_code=404,
                detail="视频任务不存在",
            )

        if task["status"] != "failed":
            raise HTTPException(
                status_code=409,
                detail="只有 failed 状态的任务可以重试",
            )

        try:
            update_status(
                video_id,
                "queued",
                "任务已重新排队，准备恢复处理",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=f"任务重试失败: {exc}",
            ) from exc

        background_tasks.add_task(
            process_video,
            video_id,
            video_path,
        )

    return {
        "video_id": video_id,
        "status": "queued",
        "message": "已重新排队，将尝试复用有效 Checkpoint",
    }



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
def search_video(
    video_id: str,
    q: str = Query(..., max_length=100),
    mode: str = Query("keyword", pattern="^(keyword|semantic)$"),
    top_k: int = Query(5, ge=1, le=20),
):
    validate_video_id(video_id)

    video_path = UPLOAD_DIR / f"{video_id}.mp4"

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="视频不存在",
        )

    job_path = JOB_DIR / video_id

    task = TASK_REPOSITORY.get_task(video_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="视频任务不存在",
        )

    if task["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail="视频尚未完成分析",
        )

    if not isinstance(status_data, dict):
        raise HTTPException(
            status_code=404,
            detail="视频任务不存在",
        )

    if status_data.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail="视频尚未完成分析",
        )

    query = q.strip()

    if not query:
        raise HTTPException(
            status_code=400,
            detail="搜索内容不能为空",
        )

    transcript = read_json(
        job_path / "transcript.json"
    )

    if (
        not isinstance(transcript, dict)
        or not isinstance(
            transcript.get("segments"),
            list,
        )
    ):
        raise HTTPException(
            status_code=500,
            detail="转写数据不存在或格式错误",
        )

    for segment in transcript["segments"]:
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("text"), str)
            or type(segment.get("start")) not in (int, float)
            or type(segment.get("end")) not in (int, float)
        ):
            raise HTTPException(
                status_code=500,
                detail="转写片段数据格式错误",
            )

    if mode == "keyword":
        results = search_chunks(
            transcript,
            query,
        )

        return {
            "video_id": video_id,
            "query": query,
            "mode": mode,
            "total": len(results),
            "results": results,
            "cache_hit": None,
            "latency_ms": None,
        }

    try:
        results, cache_hit, latency_ms = (
            semantic_search_video(
                transcript=transcript,
                job_path=job_path,
                query=query,
                top_k=top_k,
                min_score=None,
            )
        )

    except Exception as exc:
        print(
            f"语义搜索失败：{type(exc).__name__}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail="语义搜索失败，请查看后端日志",
        ) from exc

    return {
        "video_id": video_id,
        "query": query,
        "mode": mode,
        "total": len(results),
        "results": results,
        "cache_hit": cache_hit,
        "latency_ms": latency_ms,
        "experimental": True,
    }
