import os
import socket
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: str
    lease_seconds: int
    heartbeat_interval: int
    poll_interval: float
    idle_log_interval: int


def load_worker_config():
    """
    从环境变量读取 Worker 配置。

    默认值适合本地开发和单机 MVP。
    """

    worker_id = os.getenv(
        "WORKER_ID",
        f"{socket.gethostname()}-{uuid4().hex[:8]}",
    )

    lease_seconds = int(
        os.getenv("WORKER_LEASE_SECONDS", "60")
    )

    heartbeat_interval = int(
        os.getenv("WORKER_HEARTBEAT_INTERVAL", "15")
    )

    poll_interval = float(
        os.getenv("WORKER_POLL_INTERVAL", "2")
    )

    idle_log_interval = int(
        os.getenv("WORKER_IDLE_LOG_INTERVAL", "30")
    )

    if not worker_id.strip():
        raise ValueError("WORKER_ID 不能为空")

    if lease_seconds <= 0:
        raise ValueError("WORKER_LEASE_SECONDS 必须大于 0")

    if heartbeat_interval <= 0:
        raise ValueError(
            "WORKER_HEARTBEAT_INTERVAL 必须大于 0"
        )

    if heartbeat_interval >= lease_seconds:
        raise ValueError(
            "心跳间隔必须小于租约时间"
        )

    if poll_interval <= 0:
        raise ValueError("WORKER_POLL_INTERVAL 必须大于 0")

    if idle_log_interval <= 0:
        raise ValueError(
            "WORKER_IDLE_LOG_INTERVAL 必须大于 0"
        )

    return WorkerConfig(
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        heartbeat_interval=heartbeat_interval,
        poll_interval=poll_interval,
        idle_log_interval=idle_log_interval,
    )
