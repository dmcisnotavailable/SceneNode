import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from task_repository import TaskRepository


BASE_DIR = Path(__file__).resolve().parent
JOB_DIR = BASE_DIR / "jobs"
UPLOAD_DIR = BASE_DIR / "uploads"


def read_json(path):
    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def valid_video_id(video_id):
    try:
        return UUID(hex=video_id).hex == video_id
    except (ValueError, AttributeError):
        return False


def migrate(
    repository=None,
    job_dir=JOB_DIR,
    upload_dir=UPLOAD_DIR,
):
    repo = repository or TaskRepository()

    imported = 0
    skipped = 0
    errors = []

    for video_path in sorted(Path(upload_dir).glob("*.mp4")):
        video_id = video_path.stem

        if not valid_video_id(video_id):
            skipped += 1
            continue

        job_path = Path(job_dir) / video_id

        status_data = read_json(job_path / "status.json")
        metadata = read_json(job_path / "metadata.json")

        if not isinstance(status_data, dict):
            errors.append(f"{video_id}: 状态文件缺失或损坏")
            continue

        if not isinstance(metadata, dict):
            metadata = {}

        uploaded_at = metadata.get("uploaded_at")

        if not uploaded_at:
            uploaded_at = datetime.fromtimestamp(
                video_path.stat().st_mtime,
                timezone.utc,
            ).isoformat()

        try:
            result = repo.import_legacy_task(
                video_id=video_id,
                filename=metadata.get(
                    "filename", video_path.name
                ),
                file_path=str(video_path.resolve()),
                size=video_path.stat().st_size,
                uploaded_at=uploaded_at,
                status=status_data["status"],
                message=status_data.get("message", ""),
            )

            if result:
                imported += 1
            else:
                skipped += 1

        except (ValueError, KeyError) as exc:
            errors.append(f"{video_id}: {exc}")

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


if __name__ == "__main__":
    result = migrate()

    print("迁移完成")
    print(f"导入: {result['imported']}")
    print(f"跳过: {result['skipped']}")
    print(f"异常: {len(result['errors'])}")

    for error in result["errors"]:
        print(f"[ERROR] {error}")

    if result["errors"]:
        raise SystemExit(1)
