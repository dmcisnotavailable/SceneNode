import sqlite3
import time
from uuid import uuid4

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from task_state import TaskStatus, validate_transition


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
