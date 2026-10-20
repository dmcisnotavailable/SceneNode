from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

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
async def upload_video(file: UploadFile = File(...)):
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

    return {
        "video_id": video_id,
        "filename": file.filename,
        "size": size,
        "status": "uploaded",
    }
