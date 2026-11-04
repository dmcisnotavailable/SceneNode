import sqlite3
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
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row

        try:
            conn.execute("PRAGMA foreign_keys = ON")
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
                    size INTEGER NOT NULL
                        CHECK(size >= 0),
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

                CREATE INDEX IF NOT EXISTS
                    idx_tasks_status
                ON tasks(status);
            """)

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
                    attempt
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    TaskStatus.QUEUED.value,
                    "等待分析",
                    now,
                    now,
                    0,
                ),
            )

        return self.get_task(video_id)

    def get_task(self, video_id):
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    t.video_id,
                    t.status,
                    t.message,
                    t.created_at,
                    t.updated_at,
                    t.attempt,
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

    def list_tasks(self, status=None, limit=100):
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1 到 500 之间")

        sql = """
            SELECT
                t.video_id,
                t.status,
                t.message,
                t.created_at,
                t.updated_at,
                t.attempt,
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

    def update_status(
        self,
        video_id,
        next_status,
        message="",
    ):
        next_status = TaskStatus(next_status).value

        # BEGIN IMMEDIATE 在读取当前状态之前
        # 获取 SQLite 写事务锁，防止两个连接
        # 同时基于同一个旧状态完成迁移。
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT status, attempt
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

            attempt = row["attempt"]

            # 每次失败后重新排队，重试次数加一。
            if (
                current_status == TaskStatus.FAILED.value
                and next_status == TaskStatus.QUEUED.value
            ):
                attempt += 1

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

    def get_video(self, video_id):
        """查询单个视频的元数据。"""

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
        """
        导入旧 JSON 任务。

        只允许导入已知状态。
        已存在的任务不覆盖。
        """
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
                    attempt
                )
                VALUES (?, ?, ?, ?, ?, 0)
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

    def list_videos(self, limit=100):
        """返回视频及其当前任务状态。"""

        return self.list_tasks(limit=limit)
