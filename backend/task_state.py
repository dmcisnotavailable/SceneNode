from enum import Enum


class TaskStatus(str, Enum):
    """视频分析任务状态。"""

    QUEUED = "queued"
    PROCESSING = "processing"
    FUSING = "fusing"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


# 状态迁移表
# key：当前状态
# value：允许进入的下一个状态
ALLOWED_TRANSITIONS = {
    TaskStatus.QUEUED: {
        TaskStatus.PROCESSING,
        TaskStatus.FAILED,
    },
    TaskStatus.PROCESSING: {
        TaskStatus.FUSING,
        TaskStatus.FAILED,
    },
    TaskStatus.FUSING: {
        TaskStatus.GENERATING,
        TaskStatus.FAILED,
    },
    TaskStatus.GENERATING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}


class InvalidStateTransition(ValueError):
    """任务状态迁移不合法。"""


def validate_transition(
    current_status: str | TaskStatus,
    next_status: str | TaskStatus,
) -> None:
    """
    校验任务状态迁移是否合法。

    合法：正常返回 None。
    非法：抛出 InvalidStateTransition。

    只负责校验，不修改状态，
    也不读取或写入 status.json。
    """

    try:
        current = TaskStatus(current_status)
    except ValueError as exc:
        raise InvalidStateTransition(
            f"未知的当前状态: {current_status!r}"
        ) from exc

    try:
        next_state = TaskStatus(next_status)
    except ValueError as exc:
        raise InvalidStateTransition(
            f"未知的目标状态: {next_status!r}"
        ) from exc

    allowed = ALLOWED_TRANSITIONS[current]

    if next_state not in allowed:
        allowed_names = sorted(
            state.value for state in allowed
        )

        raise InvalidStateTransition(
            f"不允许从 {current.value!r} "
            f"迁移到 {next_state.value!r}。"
            f"允许的目标状态: {allowed_names}"
        )
