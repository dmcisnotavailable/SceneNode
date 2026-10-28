import unittest

from task_state import (
    TaskStatus,
    InvalidStateTransition,
    validate_transition,
)


class TestTaskState(unittest.TestCase):

    def test_valid_transitions(self):
        """测试所有允许的状态迁移。"""

        valid_cases = [
            ("queued", "processing"),
            ("queued", "failed"),
            ("processing", "fusing"),
            ("processing", "failed"),
            ("fusing", "generating"),
            ("fusing", "failed"),
            ("generating", "completed"),
            ("generating", "failed"),
            ("failed", "queued"),
        ]

        for current, next_state in valid_cases:
            with self.subTest(
                current=current,
                next_state=next_state,
            ):
                self.assertIsNone(
                    validate_transition(
                        current,
                        next_state,
                    )
                )

    def test_invalid_transitions(self):
        """测试不允许跳过阶段或倒退。"""

        invalid_cases = [
            ("queued", "completed"),
            ("queued", "generating"),
            ("processing", "completed"),
            ("fusing", "processing"),
            ("generating", "fusing"),
            ("failed", "processing"),
            ("completed", "queued"),
        ]

        for current, next_state in invalid_cases:
            with self.subTest(
                current=current,
                next_state=next_state,
            ):
                with self.assertRaises(
                    InvalidStateTransition
                ):
                    validate_transition(
                        current,
                        next_state,
                    )

    def test_unknown_current_status(self):
        """当前状态不存在时应抛出异常。"""

        with self.assertRaisesRegex(
            InvalidStateTransition,
            "未知的当前状态",
        ):
            validate_transition(
                "unknown",
                "processing",
            )

    def test_unknown_next_status(self):
        """目标状态不存在时应抛出异常。"""

        with self.assertRaisesRegex(
            InvalidStateTransition,
            "未知的目标状态",
        ):
            validate_transition(
                "queued",
                "unknown",
            )

    def test_completed_cannot_restart(self):
        """已完成任务不能重新进入处理阶段。"""

        with self.assertRaises(
            InvalidStateTransition
        ):
            validate_transition(
                "completed",
                "processing",
            )

    def test_completed_cannot_fail(self):
        """已完成任务不能再变成失败。"""

        with self.assertRaises(
            InvalidStateTransition
        ):
            validate_transition(
                "completed",
                "failed",
            )

    def test_same_state_not_allowed(self):
        """暂不允许重复迁移到同一状态。"""

        for status in TaskStatus:
            with self.subTest(status=status.value):
                with self.assertRaises(
                    InvalidStateTransition
                ):
                    validate_transition(
                        status,
                        status,
                    )

    def test_enum_input(self):
        """支持直接传入 TaskStatus 枚举。"""

        self.assertIsNone(
            validate_transition(
                TaskStatus.QUEUED,
                TaskStatus.PROCESSING,
            )
        )

    def test_invalid_transition_message(self):
        """非法迁移的错误信息应包含状态。"""

        with self.assertRaises(
            InvalidStateTransition
        ) as context:
            validate_transition(
                "queued",
                "completed",
            )

        message = str(context.exception)

        self.assertIn("queued", message)
        self.assertIn("completed", message)
        self.assertIn("processing", message)


if __name__ == "__main__":
    unittest.main()
