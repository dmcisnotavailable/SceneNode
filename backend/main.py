import json

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    Query,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from media_scanner import scan_directory
from project_service import validate_source_directory

from task_repository import TaskRepository
from search import search_chunks
from semantic_service import semantic_search_video


app = FastAPI(
    title="SceneNote API",
    version="0.9.2",
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
JOB_DIR = BASE_DIR / "jobs"
ASSET_JOB_DIR = BASE_DIR / "asset_jobs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOB_DIR.mkdir(parents=True, exist_ok=True)
ASSET_JOB_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 200 * 1024 * 1024


# --------------------------------------------------
# v1.0.1 旅行项目请求模型
# --------------------------------------------------

class CreateProjectRequest(BaseModel):
    """创建旅行项目的请求体。"""

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


class ScanDirectoryRequest(BaseModel):
    """扫描素材目录的请求体。"""

    directory: str = Field(min_length=1, max_length=1000)

# API 进程自己的 Repository 实例。
# Worker 进程会创建另一个实例，但连接同一个数据库。
TASK_REPOSITORY = TaskRepository()


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


# --------------------------------------------------
# 公共工具
# --------------------------------------------------

def validate_video_id(video_id: str):
    """校验视频 ID，防止非法路径访问。"""

    try:
        parsed = UUID(hex=video_id)

        if parsed.hex != video_id:
            raise ValueError("Invalid ID")

    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail="无效的视频 ID",
        )


def read_json(path: Path):
    """安全读取 JSON。"""

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )

    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        return None


# --------------------------------------------------
# 上传视频：只创建任务，不执行 Pipeline
# --------------------------------------------------

@app.post("/api/videos")
async def upload_video(
    file: UploadFile = File(...),
):
    if (
        not file.filename
        or Path(
            file.filename.replace("\\", "/")
        ).suffix.lower() != ".mp4"
    ):
        raise HTTPException(
            status_code=400,
            detail="目前仅支持 MP4 文件",
        )

    video_id = uuid4().hex
    save_path = UPLOAD_DIR / f"{video_id}.mp4"

    filename = Path(
        file.filename.replace("\\", "/")
    ).name

    size = 0

    try:
        with save_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                size += len(chunk)

                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="视频不能超过 200 MB",
                    )

                output.write(chunk)

    except Exception:
        save_path.unlink(missing_ok=True)
        raise

    finally:
        await file.close()

    try:
        # 一个数据库事务内创建视频记录和 queued 任务。
        TASK_REPOSITORY.create_video_task(
            video_id=video_id,
            filename=filename,
            file_path=str(save_path.resolve()),
            size=size,
        )

    except Exception:
        # 数据库创建失败时清理已经上传的文件。
        save_path.unlink(missing_ok=True)
        raise

    # 注意：
    # 这里不再调用 BackgroundTasks。
    # Worker 会自行从 SQLite 中领取 queued 任务。

    return {
        "video_id": video_id,
        "filename": filename,
        "size": size,
        "status": "queued",
        "message": "视频上传成功，已加入处理队列",
    }


# --------------------------------------------------
# 历史视频列表
# --------------------------------------------------

@app.get("/api/videos")
def list_videos():
    videos = TASK_REPOSITORY.list_videos(
        limit=500,
    )

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


# --------------------------------------------------
# 查询单个视频任务
# --------------------------------------------------

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


# --------------------------------------------------
# 重试任务：只重新入队
# --------------------------------------------------

@app.post("/api/videos/{video_id}/retry")
def retry_video(video_id: str):
    validate_video_id(video_id)

    task = TASK_REPOSITORY.get_task(video_id)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="视频任务不存在",
        )

    video_path = Path(task["file_path"]).resolve()

    # 数据库中的路径必须仍然指向 uploads 目录。
    try:
        video_path.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="视频文件路径不合法",
        )

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="原始视频文件不存在，无法重试",
        )

    if task["status"] != "failed":
        raise HTTPException(
            status_code=409,
            detail="只有 failed 状态的任务可以重试",
        )

    try:
        # SQLite 事务保证两个并发重试请求只有一个成功。
        # 不启动 Pipeline，不调用 Worker。
        updated = TASK_REPOSITORY.update_status(
            video_id,
            "queued",
            "任务已重新排队，等待 Worker 领取",
        )

    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=f"任务重试失败: {exc}",
        ) from exc

    return {
        "video_id": video_id,
        "status": updated["status"],
        "attempt": updated["attempt"],
        "message": "已重新加入处理队列",
    }


# --------------------------------------------------
# 读取原始视频
# --------------------------------------------------

@app.get("/api/videos/{video_id}/file")
def get_video_file(video_id: str):
    validate_video_id(video_id)

    video_path = UPLOAD_DIR / f"{video_id}.mp4"

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="视频文件不存在",
        )

    return FileResponse(
        path=video_path,
        media_type="video/mp4",
    )


# --------------------------------------------------
# 单视频关键词 / 语义搜索
# --------------------------------------------------

@app.get("/api/videos/{video_id}/search")
def search_video(
    video_id: str,
    q: str = Query(..., max_length=100),
    mode: str = Query(
        "keyword",
        pattern="^(keyword|semantic)$",
    ),
    top_k: int = Query(
        5,
        ge=1,
        le=20,
    ),
):
    validate_video_id(video_id)

    video_path = UPLOAD_DIR / f"{video_id}.mp4"

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="视频不存在",
        )

    # 任务状态统一从 SQLite 读取。
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

    query = q.strip()

    if not query:
        raise HTTPException(
            status_code=400,
            detail="搜索内容不能为空",
        )

    job_path = JOB_DIR / video_id

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
            f"语义搜索失败："
            f"{type(exc).__name__}: {exc}"
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
# --------------------------------------------------
# v1.0.1 旅行项目与素材索引
# --------------------------------------------------

@app.post("/api/projects")
def create_project(request: CreateProjectRequest):
    """创建旅行项目。"""

    try:
        project = TASK_REPOSITORY.create_project(
            request.name,
            request.description,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return project


@app.get("/api/projects")
def list_projects():
    """查询旅行项目列表。"""

    projects = TASK_REPOSITORY.list_projects()

    return {
        "total": len(projects),
        "projects": projects,
    }


@app.post("/api/projects/{project_id}/scan")
def scan_project_directory(
    project_id: str,
    request: ScanDirectoryRequest,
):
    """扫描本地素材目录并保存索引。

    说明：
    1. 只做只读扫描，不复制、不修改原始素材。
    2. 不创建任何 Worker 分析任务，扫描是轻量操作。
    3. 同步执行，适合几十到几百个本地文件的 MVP 场景。
    """

    project = TASK_REPOSITORY.get_project(project_id)

    if project is None:
        raise HTTPException(404, "旅行项目不存在")

    try:
        root = validate_source_directory(request.directory)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(400, f"目录无法访问: {exc}") from exc

    try:
        scan_result = scan_directory(str(root))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        result = TASK_REPOSITORY.save_project_assets(
            project_id,
            scan_result["assets"],
        )
    except KeyError as exc:
        raise HTTPException(404, "旅行项目不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "project_id": project_id,
        "total": scan_result["total"],
        "saved": result["saved"],
        "updated": result["updated"],
        "skipped": result["skipped"],
        "errors": scan_result["errors"],
    }


@app.get("/api/projects/{project_id}/assets")
def list_project_assets(project_id: str):
    """查询项目下的素材列表。"""

    project = TASK_REPOSITORY.get_project(project_id)

    if project is None:
        raise HTTPException(404, "旅行项目不存在")

    assets = TASK_REPOSITORY.list_project_assets(
        project_id,
        limit=5000,
    )

    return {
        "project_id": project_id,
        "total": len(assets),
        "assets": assets,
    }


@app.get(
    "/api/projects/{project_id}/assets/"
    "{asset_id}/thumbnail"
)
def get_asset_thumbnail(
    project_id: str,
    asset_id: str,
):
    """
    读取素材缩略图。

    安全约束：
    缩略图路径必须位于 SceneNote 自己的
    asset_jobs 目录内，不信任数据库中的任意路径。
    """

    asset = TASK_REPOSITORY.get_project_asset(
        project_id,
        asset_id,
    )

    if asset is None:
        raise HTTPException(404, "素材不存在")

    thumbnail_path = asset.get("thumbnail_path")

    if not thumbnail_path:
        raise HTTPException(404, "缩略图尚未生成")

    path = Path(thumbnail_path).resolve()

    # 路径越界校验：只允许读取 asset_jobs 目录内的文件。
    if not path.is_relative_to(ASSET_JOB_DIR.resolve()):
        raise HTTPException(403, "缩略图路径不合法")

    if not path.is_file():
        raise HTTPException(404, "缩略图文件不存在")

    return FileResponse(
        path,
        media_type="image/jpeg",
    )


@app.post(
    "/api/projects/{project_id}/assets/"
    "{asset_id}/analyze"
)
def analyze_project_asset(
    project_id: str,
    asset_id: str,
):
    """
    为素材创建分析任务。

    只创建 asset_tasks 记录并立即返回，
    实际分析由 AssetWorker 异步执行。
    """

    asset = TASK_REPOSITORY.get_project_asset(
        project_id,
        asset_id,
    )

    if asset is None:
        raise HTTPException(404, "素材不存在")

    task = TASK_REPOSITORY.create_asset_task(asset_id)

    if task is None:
        raise HTTPException(500, "分析任务创建失败")

    return {
        "asset_id": asset_id,
        "task_id": task["task_id"],
        "status": task["status"],
        "message": task["message"],
    }
