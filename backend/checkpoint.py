import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1

STAGE_ARTIFACTS = {
    "audio": "audio.wav",
    "transcript": "transcript.json",
    "vision": "frames/visual_descriptions.json",
    "timeline": "multimodal_timeline.json",
    "chapters": "chapters.json",
}

_checkpoint_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _dependency_hashes(job_path, dependencies):
    """计算上游依赖文件的内容指纹。"""

    if dependencies is None:
        dependencies = []

    if not isinstance(dependencies, (list, tuple)):
        raise ValueError("dependencies 必须是路径列表")

    root = Path(job_path).resolve()
    result = {}

    for relative_path in dependencies:
        if not isinstance(relative_path, str):
            raise ValueError("依赖路径必须是字符串")

        path = (root / relative_path).resolve()

        if not path.is_relative_to(root):
            raise ValueError("依赖路径不能超出任务目录")

        if not path.is_file():
            raise FileNotFoundError(
                f"上游依赖不存在: {relative_path}"
            )

        result[relative_path] = _sha256(path)

    return result


def _validate_artifact(stage, path):
    """检查阶段产物的基本结构。"""

    if not path.is_file() or path.stat().st_size == 0:
        return False

    if stage == "audio":
        # 这里只检查非空文件，不验证 WAV 编码。
        return True

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False

    if not isinstance(data, dict):
        return False

    if stage == "transcript":
        segments = data.get("segments")

        return (
            isinstance(segments, list)
            and len(segments) > 0
            and all(
                isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and isinstance(item.get("start"), (int, float))
                and not isinstance(item.get("start"), bool)
                and isinstance(item.get("end"), (int, float))
                and not isinstance(item.get("end"), bool)
                and 0 <= item["start"] < item["end"]
                for item in segments
            )
        )

    if stage == "vision":
        frames = data.get("frames")

        return (
            isinstance(frames, list)
            and len(frames) > 0
            and all(
                isinstance(item, dict)
                and isinstance(item.get("frame_id"), (str, int))
                and isinstance(item.get("timestamp"), (int, float))
                and not isinstance(item.get("timestamp"), bool)
                and isinstance(item.get("description"), str)
                and bool(item["description"].strip())
                for item in frames
            )
        )

    if stage == "timeline":
        events = data.get("events")

        return (
            isinstance(events, list)
            and len(events) > 0
            and all(
                isinstance(item, dict)
                and isinstance(item.get("start"), (int, float))
                and not isinstance(item.get("start"), bool)
                and isinstance(item.get("text"), str)
                for item in events
            )
        )

    if stage == "chapters":
        chapters = data.get("chapters")

        return (
            isinstance(chapters, list)
            and 1 <= len(chapters) <= 6
            and all(
                isinstance(item, dict)
                and isinstance(item.get("start"), (int, float))
                and not isinstance(item.get("start"), bool)
                and isinstance(item.get("title"), str)
                and bool(item["title"].strip())
                and isinstance(item.get("summary"), str)
                and bool(item["summary"].strip())
                for item in chapters
            )
        )

    return False


class CheckpointManager:
    """单个视频任务的阶段产物管理器。"""

    def __init__(self, job_path, video_id):
        self.job_path = Path(job_path).resolve()
        self.video_id = video_id
        self.checkpoint_path = (
            self.job_path / "checkpoint.json"
        )

    def _empty(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "video_id": self.video_id,
            "stages": {},
        }

    def _read(self):
        if not self.checkpoint_path.exists():
            return self._empty()

        try:
            data = json.loads(
                self.checkpoint_path.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Checkpoint 文件无法读取或 JSON 已损坏"
            ) from exc

        if (
            not isinstance(data, dict)
            or data.get("schema_version") != SCHEMA_VERSION
            or data.get("video_id") != self.video_id
            or not isinstance(data.get("stages"), dict)
        ):
            raise ValueError(
                "Checkpoint 版本、视频 ID 或结构不匹配"
            )

        return data

    def _write(self, data):
        self.job_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        temp_path = self.checkpoint_path.with_suffix(
            ".json.tmp"
        )

        temp_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temp_path.replace(self.checkpoint_path)

    def _artifact_path(self, stage):
        if stage not in STAGE_ARTIFACTS:
            raise ValueError(
                f"未知的 Checkpoint 阶段: {stage}"
            )

        return self.job_path / STAGE_ARTIFACTS[stage]

    
    def save(self, stage, config, dependencies=None):
        """
        保存成功阶段的 Checkpoint。

        dependencies 是相对于 job_path 的文件路径列表。
        """
        if not isinstance(config, dict):
            raise ValueError("config 必须是字典")

        artifact_path = self._artifact_path(stage)

        if not _validate_artifact(stage, artifact_path):
            raise ValueError(
                f"阶段 {stage} 的产物不存在或无效"
            )

        config_copy = json.loads(
            json.dumps(config, sort_keys=True)
        )

        with _checkpoint_lock:
            dependency_hashes = _dependency_hashes(
                self.job_path,
                dependencies,
            )

            data = self._read()

            data["stages"][stage] = {
                "status": "completed",
                "artifact": STAGE_ARTIFACTS[stage],
                "sha256": _sha256(artifact_path),
                "config": config_copy,
                "dependencies": dependency_hashes,
                "completed_at": _now(),
            }

            self._write(data)

        return True


    
    def is_valid(self, stage, config, dependencies=None):
        """校验产物、配置和上游依赖是否仍然有效。"""

        if not isinstance(config, dict):
            return False

        artifact_path = self._artifact_path(stage)

        with _checkpoint_lock:
            try:
                data = self._read()
                record = data["stages"].get(stage)

                current_dependencies = _dependency_hashes(
                    self.job_path,
                    dependencies,
                )
            except (
                ValueError,
                OSError,
                TypeError,
            ):
                return False

        if not isinstance(record, dict):
            return False

        if record.get("status") != "completed":
            return False

        if record.get("artifact") != STAGE_ARTIFACTS[stage]:
            return False

        if record.get("config") != config:
            return False

        if record.get("dependencies", {}) != current_dependencies:
            return False

        if not _validate_artifact(stage, artifact_path):
            return False

        try:
            return record.get("sha256") == _sha256(
                artifact_path
            )
        except OSError:
            return False


    def get_completed_stages(self):
        """返回已记录完成的阶段名称，不代表仍有效。"""

        with _checkpoint_lock:
            data = self._read()

        return [
            stage
            for stage, record in data["stages"].items()
            if isinstance(record, dict)
            and record.get("status") == "completed"
        ]
