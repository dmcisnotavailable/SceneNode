import sqlite3
import time
from uuid import uuid4

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from task_state import (
    InvalidStateTransition,
    TaskStatus,
    validate_transition,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "scenenote.db"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class TaskRepository:

    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.init_db()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row

        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")

            yield conn
            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

    def init_db(self):
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    size INTEGER NOT NULL CHECK(size >= 0),
                    uploaded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    video_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0
                        CHECK(attempt >= 0),
                    FOREIGN KEY(video_id)
                        REFERENCES videos(video_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON tasks(status);
            """)

            # 兼容已经存在的 SQLite 数据库。
            existing_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(tasks)"
                ).fetchall()
            }

            migrations = {
                "worker_id": "TEXT",
                "lease_expires_at": "REAL",
                "heartbeat_at": "REAL",
                "fencing_token": (
                    "INTEGER NOT NULL DEFAULT 0"
                ),
            }

            for column, definition in migrations.items():
                if column not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE tasks ADD COLUMN "
                        f"{column} {definition}"
                    )

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_lease
                ON tasks(status, lease_expires_at)
            """)

            # ------------------------------------------------
            # v1.0.1 旅行项目与素材索引表
            # ------------------------------------------------
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_assets (
                    asset_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL
                        CHECK(file_size >= 0),
                    file_mtime REAL NOT NULL,
                    recorded_at TEXT,
                    duration REAL NOT NULL CHECK(duration > 0),
                    width INTEGER,
                    height INTEGER,
                    fingerprint TEXT NOT NULL,
                    analysis_status TEXT NOT NULL DEFAULT 'pending',
                    indexed_at TEXT NOT NULL,

                    FOREIGN KEY(project_id)
                        REFERENCES projects(project_id)
                        ON DELETE CASCADE,

                    UNIQUE(project_id, source_path)
                );

                CREATE INDEX IF NOT EXISTS idx_project_assets_project
                ON project_assets(project_id);

                CREATE INDEX IF NOT EXISTS idx_project_assets_recorded_at
                ON project_assets(project_id, recorded_at);
            """)

            # ------------------------------------------------
            # v1.0.2 project_assets 表迁移：缩略图与内容分析
            # ------------------------------------------------
            existing_asset_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(project_assets)"
                ).fetchall()
            }

            asset_migrations = {
                "thumbnail_path": "TEXT",
                "scene": "TEXT",
                "description": "TEXT",
                "analysis_error": "TEXT",
            }

            for column, definition in asset_migrations.items():
                if column not in existing_asset_columns:
                    conn.execute(
                        f"ALTER TABLE project_assets "
                        f"ADD COLUMN {column} {definition}"
                    )

            # ------------------------------------------------
            # v1.0.2 素材分析任务表（独立于视频分析任务）
            # ------------------------------------------------
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS asset_tasks (
                    task_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0
                        CHECK(attempt >= 0),
                    worker_id TEXT,
                    lease_expires_at REAL,
                    heartbeat_at REAL,
                    fencing_token INTEGER NOT NULL DEFAULT 0,

                    FOREIGN KEY(asset_id)
                        REFERENCES project_assets(asset_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_asset_tasks_status
                ON asset_tasks(status);

                CREATE INDEX IF NOT EXISTS idx_asset_tasks_lease
                ON asset_tasks(status, lease_expires_at);
            """)

            # ------------------------------------------------
            # v1.0.3 时间线与故事主题表
            # ------------------------------------------------
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS stories (
                    story_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id)
                        REFERENCES projects(project_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_stories_project
                ON stories(project_id);

                CREATE TABLE IF NOT EXISTS clips (
                    clip_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    start_time REAL NOT NULL
                        CHECK(start_time >= 0),
                    end_time REAL NOT NULL
                        CHECK(end_time > start_time),
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(asset_id)
                        REFERENCES project_assets(asset_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_clips_asset
                ON clips(asset_id);

                CREATE TABLE IF NOT EXISTS story_clips (
                    story_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL DEFAULT 'related',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(story_id, clip_id),
                    FOREIGN KEY(story_id)
                        REFERENCES stories(story_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(clip_id)
                        REFERENCES clips(clip_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_story_clips_clip
                ON story_clips(clip_id);

                CREATE TABLE IF NOT EXISTS tags (
                    tag_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL
                        CHECK(category IN (
                            'location', 'person', 'scene'
                        )),
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id)
                        REFERENCES projects(project_id)
                        ON DELETE CASCADE,
                    UNIQUE(project_id, name, category)
                );

                CREATE INDEX IF NOT EXISTS idx_tags_project
                ON tags(project_id);

                CREATE TABLE IF NOT EXISTS clip_tags (
                    clip_id TEXT NOT NULL,
                    tag_id TEXT NOT NULL,
                    confidence REAL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(clip_id, tag_id),
                    FOREIGN KEY(clip_id)
                        REFERENCES clips(clip_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(tag_id)
                        REFERENCES tags(tag_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_days (
                    day_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    date TEXT,
                    day_number INTEGER NOT NULL
                        CHECK(day_number >= 1),
                    title TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(project_id)
                        REFERENCES projects(project_id)
                        ON DELETE CASCADE,
                    UNIQUE(project_id, day_number)
                );

                CREATE INDEX IF NOT EXISTS idx_project_days_project
                ON project_days(project_id);
            """)

    # --------------------------------------------------
    # 基础查询
    # --------------------------------------------------

    def _get_task_with_connection(self, conn, video_id):
        row = conn.execute(
            """
            SELECT
                t.video_id,
                t.status,
                t.message,
                t.created_at,
                t.updated_at,
                t.attempt,
                t.worker_id,
                t.lease_expires_at,
                t.heartbeat_at,
                t.fencing_token,
                v.filename,
                v.file_path,
                v.size,
                v.uploaded_at
            FROM tasks AS t
            JOIN videos AS v
                ON t.video_id = v.video_id
            WHERE t.video_id = ?
            """,
            (video_id,),
        ).fetchone()

        return dict(row) if row else None

    def get_task(self, video_id):
        with self.connection() as conn:
            return self._get_task_with_connection(
                conn,
                video_id,
            )

    def get_video(self, video_id):
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    video_id,
                    filename,
                    file_path,
                    size,
                    uploaded_at
                FROM videos
                WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()

        return dict(row) if row else None

    def list_tasks(self, status=None, limit=100):
        if (
            not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
            raise ValueError(
                "limit 必须在 1 到 500 之间"
            )

        sql = """
            SELECT
                t.video_id,
                t.status,
                t.message,
                t.created_at,
                t.updated_at,
                t.attempt,
                t.worker_id,
                t.lease_expires_at,
                t.heartbeat_at,
                t.fencing_token,
                v.filename,
                v.file_path,
                v.size,
                v.uploaded_at
            FROM tasks AS t
            JOIN videos AS v
                ON t.video_id = v.video_id
        """

        params = []

        if status is not None:
            status = TaskStatus(status).value
            sql += " WHERE t.status = ?"
            params.append(status)

        sql += """
            ORDER BY v.uploaded_at DESC, t.video_id DESC
            LIMIT ?
        """

        params.append(limit)

        with self.connection() as conn:
            rows = conn.execute(
                sql,
                params,
            ).fetchall()

        return [dict(row) for row in rows]

    def list_videos(self, limit=100):
        return self.list_tasks(limit=limit)

    # --------------------------------------------------
    # 创建任务
    # --------------------------------------------------

    # --------------------------------------------------
    # v1.0.1 旅行项目与素材索引
    # --------------------------------------------------

    def create_project(self, name, description=""):
        """创建旅行项目。

        名称去除首尾空格后不能为空。
        返回新建项目的完整记录。
        """

        if not isinstance(name, str) or not name.strip():
            raise ValueError("项目名称不能为空")

        if not isinstance(description, str):
            raise ValueError("项目描述必须是字符串")

        project_id = uuid4().hex

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    project_id,
                    name,
                    description,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    name.strip(),
                    description.strip(),
                    utc_now(),
                ),
            )

            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()

        return dict(row)

    def get_project(self, project_id):
        """按 ID 查询旅行项目，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()

        return dict(row) if row else None

    def list_projects(self, limit=100):
        """查询项目列表，附带每个项目的素材数量。"""

        if not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1 到 500 之间")

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.*,
                    COUNT(a.asset_id) AS asset_count
                FROM projects p
                LEFT JOIN project_assets a
                    ON p.project_id = a.project_id
                GROUP BY p.project_id
                ORDER BY p.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def save_project_assets(self, project_id, assets):
        """批量保存扫描到的素材记录。

        以 (project_id, source_path) 为唯一键执行 upsert：
        - 新文件：插入记录。
        - 已存在且指纹变化：更新元数据并重置分析状态。
        - 已存在且指纹相同：跳过，保留 asset_id 和分析状态。

        返回 {"saved": 插入数, "updated": 更新数, "skipped": 跳过数}。
        """

        if not isinstance(assets, list):
            raise ValueError("assets 必须是列表")

        now = utc_now()
        saved = 0
        updated = 0
        skipped = 0

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            project = conn.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()

            if project is None:
                raise KeyError(f"项目不存在: {project_id}")

            for asset in assets:
                required = (
                    "filename", "source_path", "file_size",
                    "file_mtime", "duration", "fingerprint",
                )

                if any(key not in asset for key in required):
                    raise ValueError(
                        "素材记录缺少必要字段: "
                        + ", ".join(sorted(required))
                    )

                if asset["duration"] <= 0:
                    raise ValueError(
                        f"素材时长必须大于 0: {asset['source_path']}"
                    )

                existing = conn.execute(
                    """
                    SELECT asset_id, fingerprint, analysis_status
                    FROM project_assets
                    WHERE project_id = ?
                      AND source_path = ?
                    """,
                    (
                        project_id,
                        str(asset["source_path"]),
                    ),
                ).fetchone()

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO project_assets (
                            asset_id,
                            project_id,
                            filename,
                            source_path,
                            file_size,
                            file_mtime,
                            recorded_at,
                            duration,
                            width,
                            height,
                            fingerprint,
                            analysis_status,
                            indexed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                        """,
                        (
                            uuid4().hex,
                            project_id,
                            asset["filename"],
                            str(asset["source_path"]),
                            asset["file_size"],
                            asset["file_mtime"],
                            asset.get("recorded_at"),
                            asset["duration"],
                            asset.get("width"),
                            asset.get("height"),
                            asset["fingerprint"],
                            now,
                        ),
                    )
                    saved += 1

                elif existing["fingerprint"] == asset["fingerprint"]:
                    # 指纹相同：文件未变化，保留原记录和分析状态。
                    skipped += 1

                else:
                    # 指纹变化：文件被修改过，重置分析状态。
                    conn.execute(
                        """
                        UPDATE project_assets
                        SET
                            filename = ?,
                            file_size = ?,
                            file_mtime = ?,
                            recorded_at = ?,
                            duration = ?,
                            width = ?,
                            height = ?,
                            fingerprint = ?,
                            analysis_status = 'pending',
                            indexed_at = ?
                        WHERE asset_id = ?
                        """,
                        (
                            asset["filename"],
                            asset["file_size"],
                            asset["file_mtime"],
                            asset.get("recorded_at"),
                            asset["duration"],
                            asset.get("width"),
                            asset.get("height"),
                            asset["fingerprint"],
                            now,
                            existing["asset_id"],
                        ),
                    )
                    updated += 1

        return {
            "saved": saved,
            "updated": updated,
            "skipped": skipped,
        }

    def list_project_assets(self, project_id, limit=5000):
        """查询项目下的素材列表，按拍摄时间降序。"""

        if not 1 <= limit <= 5000:
            raise ValueError("limit 必须在 1 到 5000 之间")

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM project_assets
                WHERE project_id = ?
                ORDER BY
                    recorded_at IS NULL ASC,
                    recorded_at DESC,
                    indexed_at DESC
                LIMIT ?
                """,
                (
                    project_id,
                    limit,
                ),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_project_asset(self, project_id, asset_id):
        """查询单个素材记录，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM project_assets
                WHERE project_id = ?
                  AND asset_id = ?
                """,
                (
                    project_id,
                    asset_id,
                ),
            ).fetchone()

        return dict(row) if row else None

    def create_video_task(
        self,
        video_id,
        filename,
        file_path,
        size,
    ):
        now = utc_now()

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    video_id,
                    filename,
                    file_path,
                    size,
                    uploaded_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    filename,
                    str(file_path),
                    size,
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO tasks (
                    video_id,
                    status,
                    message,
                    created_at,
                    updated_at,
                    attempt,
                    fencing_token
                )
                VALUES (?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    video_id,
                    TaskStatus.QUEUED.value,
                    "等待分析",
                    now,
                    now,
                ),
            )

        return self.get_task(video_id)

    # --------------------------------------------------
    # 普通状态更新
    # --------------------------------------------------

    def update_status(
        self,
        video_id,
        next_status,
        message="",
        worker_id=None,
        fencing_token=None,
    ):
        next_status = TaskStatus(next_status).value

        if (
            (worker_id is None)
            != (fencing_token is None)
        ):
            raise ValueError(
                "worker_id 和 fencing_token 必须同时提供"
            )

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT
                    status,
                    attempt,
                    worker_id,
                    lease_expires_at,
                    fencing_token
                FROM tasks
                WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()

            if row is None:
                raise KeyError(
                    f"任务不存在: {video_id}"
                )

            current_status = row["status"]

            validate_transition(
                current_status,
                next_status,
            )

            # Worker 更新状态时必须验证所有权。
            if worker_id is not None:
                if (
                    row["worker_id"] != worker_id
                    or row["fencing_token"] != fencing_token
                    or row["lease_expires_at"] is None
                    or row["lease_expires_at"] <= time.time()
                ):
                    raise PermissionError(
                        "Worker 租约已失效或执行权已转移"
                    )

            attempt = row["attempt"]

            if (
                current_status == TaskStatus.FAILED.value
                and next_status == TaskStatus.QUEUED.value
            ):
                attempt += 1

            terminal = next_status in (
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
            )

            if terminal and worker_id is not None:
                conn.execute(
                    """
                    UPDATE tasks
                    SET
                        status = ?,
                        message = ?,
                        updated_at = ?,
                        attempt = ?,
                        worker_id = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL
                    WHERE video_id = ?
                    """,
                    (
                        next_status,
                        message,
                        utc_now(),
                        attempt,
                        video_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE tasks
                    SET
                        status = ?,
                        message = ?,
                        updated_at = ?,
                        attempt = ?
                    WHERE video_id = ?
                    """,
                    (
                        next_status,
                        message,
                        utc_now(),
                        attempt,
                        video_id,
                    ),
                )

        return self.get_task(video_id)

    # --------------------------------------------------
    # Worker 原子领取任务
    # --------------------------------------------------

    def claim_next_task(
        self,
        worker_id,
        lease_seconds=60,
        now=None,
    ):
        """
        原子领取一个 queued 任务。

        返回任务字典；没有任务时返回 None。

        领取时：
        1. queued -> processing
        2. 写入 worker_id
        3. 设置租约过期时间
        4. fencing_token + 1
        """

        if not worker_id or not isinstance(worker_id, str):
            raise ValueError("worker_id 必须是非空字符串")

        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")

        current_time = time.time() if now is None else now
        lease_until = current_time + lease_seconds

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT video_id
                FROM tasks
                WHERE status = ?
                ORDER BY created_at ASC, video_id ASC
                LIMIT 1
                """,
                (TaskStatus.QUEUED.value,),
            ).fetchone()

            if row is None:
                return None

            video_id = row["video_id"]

            cursor = conn.execute(
                """
                UPDATE tasks
                SET
                    status = ?,
                    message = ?,
                    updated_at = ?,
                    worker_id = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    fencing_token = fencing_token + 1
                WHERE video_id = ?
                  AND status = ?
                """,
                (
                    TaskStatus.PROCESSING.value,
                    f"Worker {worker_id} 正在处理",
                    utc_now(),
                    worker_id,
                    lease_until,
                    current_time,
                    video_id,
                    TaskStatus.QUEUED.value,
                ),
            )

            if cursor.rowcount != 1:
                return None

            return self._get_task_with_connection(
                conn,
                video_id,
            )

    # --------------------------------------------------
    # Worker 心跳续租
    # --------------------------------------------------

    def heartbeat(
        self,
        video_id,
        worker_id,
        fencing_token,
        lease_seconds=60,
        now=None,
    ):
        """
        只有当前租约持有者才能续租。

        已过期的租约不能复活。
        """
        if lease_seconds <= 0:
            raise ValueError(
                "lease_seconds 必须大于 0"
            )

        current_time = time.time() if now is None else now
        lease_until = current_time + lease_seconds

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            cursor = conn.execute(
                """
                UPDATE tasks
                SET
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    updated_at = ?
                WHERE video_id = ?
                  AND status = ?
                  AND worker_id = ?
                  AND fencing_token = ?
                  AND lease_expires_at > ?
                """,
                (
                    lease_until,
                    current_time,
                    utc_now(),
                    video_id,
                    TaskStatus.PROCESSING.value,
                    worker_id,
                    fencing_token,
                    current_time,
                ),
            )

            return cursor.rowcount == 1

    # --------------------------------------------------
    # 主动释放任务
    # --------------------------------------------------

    def release_task(
        self,
        video_id,
        worker_id,
        fencing_token,
    ):
        """
        当前 Worker 主动释放任务。

        任务重新进入 queued。
        只有当前租约持有者可以释放。

        这是调度器内部迁移，不增加 attempt。
        """
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            cursor = conn.execute(
                """
                UPDATE tasks
                SET
                    status = ?,
                    message = ?,
                    updated_at = ?,
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL
                WHERE video_id = ?
                  AND status = ?
                  AND worker_id = ?
                  AND fencing_token = ?
                  AND lease_expires_at > ?
                """,
                (
                    TaskStatus.QUEUED.value,
                    "Worker 已释放任务，等待重新领取",
                    utc_now(),
                    video_id,
                    TaskStatus.PROCESSING.value,
                    worker_id,
                    fencing_token,
                    time.time(),
                ),
            )

            return cursor.rowcount == 1

    # --------------------------------------------------
    # 回收过期任务
    # --------------------------------------------------

    def recover_expired_tasks(self, now=None):
        """
        回收租约已经过期的 processing 任务。

        返回被回收的 video_id 列表。

        注意：
        1. 不增加用户重试次数 attempt。
        2. 不修改 fencing_token。
        3. 下一次 claim 时 fencing_token 才递增。
        """
        current_time = time.time() if now is None else now

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            rows = conn.execute(
                """
                SELECT video_id
                FROM tasks
                WHERE status = ?
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at ASC
                """,
                (
                    TaskStatus.PROCESSING.value,
                    current_time,
                ),
            ).fetchall()

            video_ids = [
                row["video_id"]
                for row in rows
            ]

            if not video_ids:
                return []

            conn.execute(
                """
                UPDATE tasks
                SET
                    status = ?,
                    message = ?,
                    updated_at = ?,
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL
                WHERE status = ?
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (
                    TaskStatus.QUEUED.value,
                    "Worker 租约过期，任务等待重新领取",
                    utc_now(),
                    TaskStatus.PROCESSING.value,
                    current_time,
                ),
            )

            return video_ids

    # --------------------------------------------------
    # 旧 JSON 任务迁移
    # --------------------------------------------------

    def import_legacy_task(
        self,
        video_id,
        filename,
        file_path,
        size,
        uploaded_at,
        status,
        message,
    ):
        status = TaskStatus(status).value
        now = utc_now()

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            existing = conn.execute(
                """
                SELECT video_id
                FROM tasks
                WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()

            if existing is not None:
                return False

            conn.execute(
                """
                INSERT INTO videos (
                    video_id,
                    filename,
                    file_path,
                    size,
                    uploaded_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    filename,
                    str(file_path),
                    size,
                    uploaded_at,
                ),
            )

            conn.execute(
                """
                INSERT INTO tasks (
                    video_id,
                    status,
                    message,
                    created_at,
                    updated_at,
                    attempt,
                    fencing_token
                )
                VALUES (?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    video_id,
                    status,
                    message,
                    now,
                    now,
                ),
            )

        return True

    # --------------------------------------------------
    # v1.0.2 素材分析任务
    # --------------------------------------------------

    def create_asset_task(self, asset_id, db_conn=None):
        """
        为素材创建分析任务。

        同一素材同时只允许一个未完成任务，
        已存在未完成任务时直接返回已有任务。
        """

        now = utc_now()

        def _insert(conn):
            conn.execute(
                """
                INSERT OR IGNORE INTO asset_tasks (
                    task_id,
                    asset_id,
                    status,
                    message,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, 'queued', '等待素材分析', ?, ?)
                """,
                (
                    uuid4().hex,
                    asset_id,
                    now,
                    now,
                ),
            )

            return conn.execute(
                """
                SELECT *
                FROM asset_tasks
                WHERE asset_id = ?
                """,
                (asset_id,),
            ).fetchone()

        if db_conn is not None:
            row = _insert(db_conn)
        else:
            with self.connection() as conn:
                row = _insert(conn)

        return dict(row) if row else None

    def get_asset_task(self, asset_id):
        """查询单个素材的分析任务，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM asset_tasks
                WHERE asset_id = ?
                """,
                (asset_id,),
            ).fetchone()

        return dict(row) if row else None

    def claim_next_asset_task(
        self,
        worker_id,
        lease_seconds=60,
        now=None,
    ):
        """
        原子领取一个 queued 状态的素材分析任务。

        领取时：
        1. queued -> processing
        2. 写入 worker_id 并设置租约
        3. fencing_token + 1

        返回包含素材信息（asset 字段）的任务字典。
        """

        if not worker_id or not isinstance(worker_id, str):
            raise ValueError("worker_id 必须是非空字符串")

        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")

        current_time = time.time() if now is None else now
        lease_until = current_time + lease_seconds

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT task.asset_id
                FROM asset_tasks AS task
                WHERE task.status = 'queued'
                ORDER BY task.created_at ASC, task.asset_id ASC
                LIMIT 1
                """,
            ).fetchone()

            if row is None:
                return None

            asset_id = row["asset_id"]

            cursor = conn.execute(
                """
                UPDATE asset_tasks
                SET
                    status = 'processing',
                    message = ?,
                    updated_at = ?,
                    worker_id = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    fencing_token = fencing_token + 1
                WHERE asset_id = ?
                  AND status = 'queued'
                """,
                (
                    f"Worker {worker_id} 正在分析素材",
                    utc_now(),
                    worker_id,
                    lease_until,
                    current_time,
                    asset_id,
                ),
            )

            if cursor.rowcount != 1:
                return None

            task_row = conn.execute(
                """
                SELECT
                    t.*,
                    a.filename AS asset_filename,
                    a.source_path AS asset_source_path,
                    a.project_id AS asset_project_id
                FROM asset_tasks AS t
                JOIN project_assets AS a
                    ON t.asset_id = a.asset_id
                WHERE t.asset_id = ?
                """,
                (asset_id,),
            ).fetchone()

            return dict(task_row) if task_row else None

    def update_asset_task_status(
        self,
        asset_id,
        next_status,
        message="",
        worker_id=None,
        fencing_token=None,
    ):
        """
        更新素材分析任务状态。

        Worker 更新时必须提供 worker_id 和 fencing_token，
        且租约未过期，与视频任务的状态机约束一致。
        """

        valid_status = {
            "queued",
            "processing",
            "completed",
            "failed",
        }

        if next_status not in valid_status:
            raise ValueError(
                f"未知的素材任务状态: {next_status}"
            )

        if (
            (worker_id is None)
            != (fencing_token is None)
        ):
            raise ValueError(
                "worker_id 和 fencing_token 必须同时提供"
            )

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT
                    status,
                    attempt,
                    worker_id,
                    lease_expires_at,
                    fencing_token
                FROM asset_tasks
                WHERE asset_id = ?
                """,
                (asset_id,),
            ).fetchone()

            if row is None:
                raise KeyError(f"素材任务不存在: {asset_id}")

            # 校验状态迁移合法性：
            # queued 只能去 processing/failed；
            # processing 只能去 completed/failed/queued（释放）；
            # failed 只能回 queued（重试）；completed 是终态。
            allowed = {
                "queued": {"processing", "failed"},
                "processing": {
                    "completed",
                    "failed",
                    "queued",
                },
                "failed": {"queued"},
                "completed": set(),
            }

            if next_status not in allowed[row["status"]]:
                raise InvalidStateTransition(
                    f"不允许从 {row['status']!r} "
                    f"迁移到 {next_status!r}"
                )

            # Worker 更新状态时必须验证所有权。
            if worker_id is not None:
                if (
                    row["worker_id"] != worker_id
                    or row["fencing_token"] != fencing_token
                    or row["lease_expires_at"] is None
                    or row["lease_expires_at"] <= time.time()
                ):
                    raise PermissionError(
                        "Worker 租约已失效或执行权已转移"
                    )

            attempt = row["attempt"]

            # failed -> queued 表示重试，累计尝试次数。
            if (
                row["status"] == "failed"
                and next_status == "queued"
            ):
                attempt += 1

            terminal = next_status in ("completed", "failed")

            if terminal and worker_id is not None:
                conn.execute(
                    """
                    UPDATE asset_tasks
                    SET
                        status = ?,
                        message = ?,
                        updated_at = ?,
                        attempt = ?,
                        worker_id = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL
                    WHERE asset_id = ?
                    """,
                    (
                        next_status,
                        message,
                        utc_now(),
                        attempt,
                        asset_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE asset_tasks
                    SET
                        status = ?,
                        message = ?,
                        updated_at = ?,
                        attempt = ?
                    WHERE asset_id = ?
                    """,
                    (
                        next_status,
                        message,
                        utc_now(),
                        attempt,
                        asset_id,
                    ),
                )

        task = self.get_asset_task(asset_id)
        return task

    def heartbeat_asset_task(
        self,
        asset_id,
        worker_id,
        fencing_token,
        lease_seconds=60,
        now=None,
    ):
        """素材分析任务的心跳续租，与视频任务逻辑一致。"""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")

        current_time = time.time() if now is None else now
        lease_until = current_time + lease_seconds

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            cursor = conn.execute(
                """
                UPDATE asset_tasks
                SET
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    updated_at = ?
                WHERE asset_id = ?
                  AND status = 'processing'
                  AND worker_id = ?
                  AND fencing_token = ?
                  AND lease_expires_at > ?
                """,
                (
                    lease_until,
                    current_time,
                    utc_now(),
                    asset_id,
                    worker_id,
                    fencing_token,
                    current_time,
                ),
            )

            return cursor.rowcount == 1

    def recover_expired_asset_tasks(self, now=None):
        """回收租约已过期的素材分析任务。"""

        current_time = time.time() if now is None else now

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            rows = conn.execute(
                """
                SELECT asset_id
                FROM asset_tasks
                WHERE status = 'processing'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at ASC
                """,
                (current_time,),
            ).fetchall()

            asset_ids = [
                row["asset_id"]
                for row in rows
            ]

            if not asset_ids:
                return []

            conn.execute(
                """
                UPDATE asset_tasks
                SET
                    status = 'queued',
                    message = ?,
                    updated_at = ?,
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL
                WHERE status = 'processing'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (
                    "Worker 租约过期，任务等待重新领取",
                    utc_now(),
                    current_time,
                ),
            )

            return asset_ids

    def update_asset_analysis(
        self,
        asset_id,
        thumbnail_path=None,
        scene=None,
        description=None,
        analysis_error=None,
        analysis_status=None,
    ):
        """
        更新素材的分析结果。

        analysis_status 不传时根据参数自动判断：
        - analysis_error 非空 -> failed
        - scene/description 有值 -> completed
        - 否则保持不变
        """

        updates = []
        params = []

        if thumbnail_path is not None:
            updates.append("thumbnail_path = ?")
            params.append(str(thumbnail_path))

        if scene is not None:
            updates.append("scene = ?")
            params.append(scene)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        # analysis_error 传空字符串表示清空旧错误，
        # 传 None 表示不更新该字段。
        if analysis_error is not None:
            updates.append("analysis_error = ?")
            params.append(analysis_error)

        if analysis_status is not None:
            if analysis_status not in (
                "pending",
                "processing",
                "completed",
                "failed",
            ):
                raise ValueError(
                    f"未知的分析状态: {analysis_status}"
                )

            updates.append("analysis_status = ?")
            params.append(analysis_status)

        if not updates:
            raise ValueError("至少提供一个要更新的字段")

        with self.connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE project_assets
                SET {', '.join(updates)}
                WHERE asset_id = ?
                """,
                (*params, asset_id),
            )

            if cursor.rowcount != 1:
                raise KeyError(f"素材不存在: {asset_id}")

        return self.get_project_asset_by_id(asset_id)

    def get_project_asset_by_id(self, asset_id):
        """按 asset_id 查询素材，不校验项目归属。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM project_assets
                WHERE asset_id = ?
                """,
                (asset_id,),
            ).fetchone()

        return dict(row) if row else None

    # --------------------------------------------------
    # v1.0.3 故事主题
    # --------------------------------------------------

    def create_story(
        self,
        project_id,
        title,
        description="",
        source="user",
    ):
        """创建故事主题，标题必填且唯一性由业务校验。"""

        if not isinstance(title, str) or not title.strip():
            raise ValueError("故事标题不能为空")

        if len(title) > 100:
            raise ValueError("故事标题不能超过 100 字")

        if description is None:
            description = ""

        if len(description) > 1000:
            raise ValueError("故事描述不能超过 1000 字")

        if source not in ("user", "ai"):
            raise ValueError("source 必须是 user 或 ai")

        story_id = uuid4().hex
        now = utc_now()

        with self.connection() as conn:
            project = conn.execute(
                """
                SELECT project_id
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()

            if project is None:
                raise KeyError(f"项目不存在: {project_id}")

            conn.execute(
                """
                INSERT INTO stories (
                    story_id,
                    project_id,
                    title,
                    description,
                    source,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    story_id,
                    project_id,
                    title.strip(),
                    description,
                    source,
                    now,
                ),
            )

        return self.get_story(story_id)

    def get_story(self, story_id):
        """查询单个故事，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM stories
                WHERE story_id = ?
                """,
                (story_id,),
            ).fetchone()

        return dict(row) if row else None

    def list_stories(self, project_id):
        """查询项目下的故事列表，附带片段数量。"""

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.*,
                    COUNT(sc.clip_id) AS clip_count
                FROM stories AS s
                LEFT JOIN story_clips AS sc
                    ON s.story_id = sc.story_id
                WHERE s.project_id = ?
                GROUP BY s.story_id
                ORDER BY s.created_at DESC
                """,
                (project_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def update_story(
        self,
        story_id,
        title=None,
        description=None,
    ):
        """更新故事标题或描述。"""

        updates = []
        params = []

        if title is not None:
            if not title.strip():
                raise ValueError("故事标题不能为空")

            updates.append("title = ?")
            params.append(title.strip())

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if not updates:
            raise ValueError("至少提供一个要更新的字段")

        with self.connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE stories
                SET {', '.join(updates)}
                WHERE story_id = ?
                """,
                (*params, story_id),
            )

            if cursor.rowcount != 1:
                raise KeyError(f"故事不存在: {story_id}")

        return self.get_story(story_id)

    def delete_story(self, story_id):
        """
        删除故事。

        只删除故事及其片段关联（级联），
        不删除 clips 记录和原始素材。
        """

        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM stories WHERE story_id = ?",
                (story_id,),
            )

            if cursor.rowcount != 1:
                raise KeyError(f"故事不存在: {story_id}")

        return True

    def list_story_clips(self, story_id):
        """查询故事下的片段列表，附带素材信息。"""

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    sc.relation_type,
                    c.*,
                    a.filename AS asset_filename,
                    a.source_path AS asset_source_path,
                    a.recorded_at AS asset_recorded_at,
                    a.thumbnail_path AS asset_thumbnail_path,
                    a.project_id AS asset_project_id
                FROM story_clips AS sc
                JOIN clips AS c
                    ON sc.clip_id = c.clip_id
                JOIN project_assets AS a
                    ON c.asset_id = a.asset_id
                WHERE sc.story_id = ?
                ORDER BY
                    a.recorded_at IS NULL ASC,
                    a.recorded_at ASC,
                    c.start_time ASC
                """,
                (story_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    # --------------------------------------------------
    # v1.0.3 片段
    # --------------------------------------------------

    def create_clip(
        self,
        asset_id,
        start_time,
        end_time,
        description="",
    ):
        """在素材上创建片段，时间单位为秒。"""

        if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
            raise ValueError("start_time 和 end_time 必须是数字")

        if start_time < 0:
            raise ValueError("start_time 不能为负数")

        if end_time <= start_time:
            raise ValueError("end_time 必须大于 start_time")

        if description is None:
            description = ""

        if len(description) > 1000:
            raise ValueError("片段描述不能超过 1000 字")

        clip_id = uuid4().hex
        now = utc_now()

        with self.connection() as conn:
            asset = conn.execute(
                """
                SELECT asset_id
                FROM project_assets
                WHERE asset_id = ?
                """,
                (asset_id,),
            ).fetchone()

            if asset is None:
                raise KeyError(f"素材不存在: {asset_id}")

            conn.execute(
                """
                INSERT INTO clips (
                    clip_id,
                    asset_id,
                    start_time,
                    end_time,
                    description,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clip_id,
                    asset_id,
                    float(start_time),
                    float(end_time),
                    description,
                    now,
                ),
            )

        return self.get_clip(clip_id)

    def get_clip(self, clip_id):
        """查询单个片段，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM clips
                WHERE clip_id = ?
                """,
                (clip_id,),
            ).fetchone()

        return dict(row) if row else None

    def list_asset_clips(self, asset_id):
        """查询素材下的全部片段，按开始时间排序。"""

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM clips
                WHERE asset_id = ?
                ORDER BY start_time ASC
                """,
                (asset_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def delete_clip(self, clip_id):
        """
        删除片段。

        story_clips 和 clip_tags 通过外键级联删除，
        原始素材不受影响。
        """

        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM clips WHERE clip_id = ?",
                (clip_id,),
            )

            if cursor.rowcount != 1:
                raise KeyError(f"片段不存在: {clip_id}")

        return True

    # --------------------------------------------------
    # v1.0.3 故事与片段关联
    # --------------------------------------------------

    def add_clip_to_story(
        self,
        story_id,
        clip_id,
        relation_type="related",
    ):
        """把片段加入故事，重复添加时幂等处理。"""

        if relation_type not in ("related", "key", "opening", "ending"):
            raise ValueError("relation_type 不合法")

        with self.connection() as conn:
            story = conn.execute(
                """
                SELECT story_id
                FROM stories
                WHERE story_id = ?
                """,
                (story_id,),
            ).fetchone()

            if story is None:
                raise KeyError(f"故事不存在: {story_id}")

            clip = conn.execute(
                """
                SELECT clip_id
                FROM clips
                WHERE clip_id = ?
                """,
                (clip_id,),
            ).fetchone()

            if clip is None:
                raise KeyError(f"片段不存在: {clip_id}")

            # INSERT OR IGNORE 保证重复添加同一片段不会报错。
            conn.execute(
                """
                INSERT OR IGNORE INTO story_clips (
                    story_id,
                    clip_id,
                    relation_type,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    story_id,
                    clip_id,
                    relation_type,
                    utc_now(),
                ),
            )

        return True

    def remove_clip_from_story(self, story_id, clip_id):
        """把片段从故事中移除，不影响片段本身。"""

        with self.connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM story_clips
                WHERE story_id = ? AND clip_id = ?
                """,
                (story_id, clip_id),
            )

            if cursor.rowcount != 1:
                raise KeyError("片段不在该故事中")

        return True

    # --------------------------------------------------
    # v1.0.3 标签
    # --------------------------------------------------

    def create_tag(
        self,
        project_id,
        name,
        category,
        source="user",
    ):
        """创建项目标签，同名同分类的标签只允许存在一个。"""

        if not isinstance(name, str) or not name.strip():
            raise ValueError("标签名不能为空")

        name = name.strip()

        if len(name) > 50:
            raise ValueError("标签名不能超过 50 字")

        if category not in ("location", "person", "scene"):
            raise ValueError("category 必须是 location、person 或 scene")

        if source not in ("user", "ai"):
            raise ValueError("source 必须是 user 或 ai")

        tag_id = uuid4().hex
        now = utc_now()

        with self.connection() as conn:
            project = conn.execute(
                """
                SELECT project_id
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()

            if project is None:
                raise KeyError(f"项目不存在: {project_id}")

            try:
                conn.execute(
                    """
                    INSERT INTO tags (
                        tag_id,
                        project_id,
                        name,
                        category,
                        source,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tag_id,
                        project_id,
                        name,
                        category,
                        source,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # 同项目同名同分类的标签已存在，直接复用已有标签。
                existing = conn.execute(
                    """
                    SELECT tag_id
                    FROM tags
                    WHERE project_id = ?
                      AND name = ?
                      AND category = ?
                    """,
                    (project_id, name, category),
                ).fetchone()

                if existing is None:
                    raise exc

                tag_id = existing["tag_id"]

        return self.get_tag(tag_id)

    def get_tag(self, tag_id):
        """查询单个标签，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM tags
                WHERE tag_id = ?
                """,
                (tag_id,),
            ).fetchone()

        return dict(row) if row else None

    def list_tags(self, project_id):
        """查询项目下的全部标签，附带使用次数。"""

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.*,
                    COUNT(ct.clip_id) AS usage_count
                FROM tags AS t
                LEFT JOIN clip_tags AS ct
                    ON t.tag_id = ct.tag_id
                WHERE t.project_id = ?
                GROUP BY t.tag_id
                ORDER BY t.created_at ASC
                """,
                (project_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def delete_tag(self, tag_id):
        """
        删除标签。

        clip_tags 通过外键级联删除，
        片段和故事不受影响。
        """

        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM tags WHERE tag_id = ?",
                (tag_id,),
            )

            if cursor.rowcount != 1:
                raise KeyError(f"标签不存在: {tag_id}")

        return True

    def tag_clip(
        self,
        clip_id,
        tag_id,
        confidence=None,
        confirmed=False,
    ):
        """
        给片段打标签。

        已被用户确认的标签（confirmed=1）不会被
        AI 分析结果覆盖，保持用户的最终决定。
        """

        if confidence is not None:
            if not isinstance(confidence, (int, float)):
                raise ValueError("confidence 必须是数字")

            if not 0 <= confidence <= 1:
                raise ValueError("confidence 必须在 0 到 1 之间")

        with self.connection() as conn:
            clip = conn.execute(
                """
                SELECT clip_id
                FROM clips
                WHERE clip_id = ?
                """,
                (clip_id,),
            ).fetchone()

            if clip is None:
                raise KeyError(f"片段不存在: {clip_id}")

            tag = conn.execute(
                """
                SELECT tag_id
                FROM tags
                WHERE tag_id = ?
                """,
                (tag_id,),
            ).fetchone()

            if tag is None:
                raise KeyError(f"标签不存在: {tag_id}")

            existing = conn.execute(
                """
                SELECT confirmed
                FROM clip_tags
                WHERE clip_id = ? AND tag_id = ?
                """,
                (clip_id, tag_id),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO clip_tags (
                        clip_id,
                        tag_id,
                        confidence,
                        confirmed
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        clip_id,
                        tag_id,
                        confidence,
                        1 if confirmed else 0,
                    ),
                )
            else:
                # 已确认的标签保持原状态，AI 的低置信度
                # 更新不允许覆盖用户决定。
                if existing["confirmed"]:
                    return self.get_clip_tag(clip_id, tag_id)

                conn.execute(
                    """
                    UPDATE clip_tags
                    SET confidence = ?,
                        confirmed = ?
                    WHERE clip_id = ? AND tag_id = ?
                    """,
                    (
                        confidence,
                        1 if confirmed else 0,
                        clip_id,
                        tag_id,
                    ),
                )

        return self.get_clip_tag(clip_id, tag_id)

    def get_clip_tag(self, clip_id, tag_id):
        """查询单条片段标签关系，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM clip_tags
                WHERE clip_id = ? AND tag_id = ?
                """,
                (clip_id, tag_id),
            ).fetchone()

        return dict(row) if row else None

    def untag_clip(self, clip_id, tag_id):
        """移除片段上的标签。"""

        with self.connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM clip_tags
                WHERE clip_id = ? AND tag_id = ?
                """,
                (clip_id, tag_id),
            )

            if cursor.rowcount != 1:
                raise KeyError("该片段上不存在此标签")

        return True

    def confirm_clip_tag(self, clip_id, tag_id):
        """用户确认片段标签，确认后 AI 不再覆盖。"""

        with self.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE clip_tags
                SET confirmed = 1
                WHERE clip_id = ? AND tag_id = ?
                """,
                (clip_id, tag_id),
            )

            if cursor.rowcount != 1:
                raise KeyError("该片段上不存在此标签")

        return self.get_clip_tag(clip_id, tag_id)

    def list_clip_tags(self, clip_id):
        """查询片段上的全部标签，附带标签详情。"""

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    ct.confidence,
                    ct.confirmed,
                    t.*
                FROM clip_tags AS ct
                JOIN tags AS t
                    ON ct.tag_id = t.tag_id
                WHERE ct.clip_id = ?
                ORDER BY t.name ASC
                """,
                (clip_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    # --------------------------------------------------
    # v1.0.3 项目天数
    # --------------------------------------------------

    def upsert_project_day(
        self,
        project_id,
        day_number,
        date=None,
        title="",
    ):
        """新增或更新项目天数记录，用于手动调整 Day 归属。"""

        if not isinstance(day_number, int) or isinstance(day_number, bool):
            raise ValueError("day_number 必须是整数")

        if day_number < 1:
            raise ValueError("day_number 必须大于等于 1")

        if title is None:
            title = ""

        if len(title) > 100:
            raise ValueError("天数标题不能超过 100 字")

        day_id = uuid4().hex

        with self.connection() as conn:
            project = conn.execute(
                """
                SELECT project_id
                FROM projects
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()

            if project is None:
                raise KeyError(f"项目不存在: {project_id}")

            # 同一项目同一天数只保留一条记录，重复保存时更新。
            conn.execute(
                """
                INSERT INTO project_days (
                    day_id,
                    project_id,
                    date,
                    day_number,
                    title
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, day_number) DO UPDATE SET
                    date = excluded.date,
                    title = excluded.title
                """,
                (
                    day_id,
                    project_id,
                    date,
                    day_number,
                    title,
                ),
            )

        return self.get_project_day(project_id, day_number)

    def get_project_day(self, project_id, day_number):
        """查询单条项目天数记录，不存在时返回 None。"""

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM project_days
                WHERE project_id = ? AND day_number = ?
                """,
                (project_id, day_number),
            ).fetchone()

        return dict(row) if row else None

    def list_project_days(self, project_id):
        """查询项目下的全部天数记录，按天数排序。"""

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM project_days
                WHERE project_id = ?
                ORDER BY day_number ASC
                """,
                (project_id,),
            ).fetchall()

        return [dict(row) for row in rows]
