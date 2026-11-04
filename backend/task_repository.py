import sqlite3
import time

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
