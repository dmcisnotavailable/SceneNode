import logging
import signal
import threading
import time

from pathlib import Path

from pipeline import process_video, JOB_DIR
from task_repository import TaskRepository
from worker_config import load_worker_config


logger = logging.getLogger("scenenote.worker")


class Worker:

    def __init__(
        self,
        repository=None,
        config=None,
    ):
        self.repository = repository or TaskRepository()
        self.config = config or load_worker_config()

        self.stop_event = threading.Event()

    def stop(self, signum=None, frame=None):
        """请求 Worker 优雅退出。"""

        logger.info("收到停止信号，准备退出 Worker")
        self.stop_event.set()

    def _heartbeat_loop(
        self,
        video_id,
        fencing_token,
        lease_lost,
        heartbeat_stop,
    ):
        """
        独立线程负责续租。

        如果续租失败，说明 Worker 已经失去执行权。
        """
        while not heartbeat_stop.wait(
            self.config.heartbeat_interval
        ):
            try:
                success = self.repository.heartbeat(
                    video_id=video_id,
                    worker_id=self.config.worker_id,
                    fencing_token=fencing_token,
                    lease_seconds=self.config.lease_seconds,
                )

                if not success:
                    logger.warning(
                        "任务租约丢失: video_id=%s",
                        video_id,
                    )
                    lease_lost.set()
                    return

                logger.debug(
                    "心跳续租成功: video_id=%s",
                    video_id,
                )

            except Exception:
                logger.exception(
                    "心跳续租异常: video_id=%s",
                    video_id,
                )
                lease_lost.set()
                return

    def process_task(self, task):
        """执行已经领取的任务。"""

        video_id = task["video_id"]
        video_path = Path(task["file_path"])
        fencing_token = task["fencing_token"]

        if not video_path.is_file():
            logger.error(
                "原始视频文件不存在: %s",
                video_path,
            )

            self.repository.update_status(
                video_id,
                "failed",
                "原始视频文件不存在",
                worker_id=self.config.worker_id,
                fencing_token=fencing_token,
            )
            return

        lease_lost = threading.Event()
        heartbeat_stop = threading.Event()

        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(
                video_id,
                fencing_token,
                lease_lost,
                heartbeat_stop,
            ),
            name=f"heartbeat-{video_id}",
            daemon=True,
        )

        logger.info(
            "开始处理任务: video_id=%s worker=%s token=%s",
            video_id,
            self.config.worker_id,
            fencing_token,
        )

        heartbeat_thread.start()

        try:
            if lease_lost.is_set():
                raise RuntimeError("任务租约已失效")

            process_video(
                video_id,
                video_path,
                worker_id=self.config.worker_id,
                fencing_token=fencing_token,
            )

            if lease_lost.is_set():
                logger.warning(
                    "任务执行期间租约丢失: %s",
                    video_id,
                )
                return

            task_after = self.repository.get_task(video_id)

            if task_after is None:
                logger.error(
                    "任务执行后数据库记录不存在: %s",
                    video_id,
                )
                return

            logger.info(
                "任务执行结束: video_id=%s status=%s",
                video_id,
                task_after["status"],
            )

        except Exception:
            logger.exception(
                "Worker 执行任务异常: %s",
                video_id,
            )

            if not lease_lost.is_set():
                try:
                    self.repository.update_status(
                        video_id,
                        "failed",
                        "Worker 执行异常",
                        worker_id=self.config.worker_id,
                        fencing_token=fencing_token,
                    )
                except Exception:
                    logger.exception(
                        "Worker 更新失败状态异常: %s",
                        video_id,
                    )

        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(
                timeout=self.config.heartbeat_interval + 1
            )

    def run_once(self):
        """
        尝试领取并执行一个任务。

        返回 True：领取到了任务。
        返回 False：当前没有任务。
        """

        # 先回收其他 Worker 已经过期的任务。
        recovered = self.repository.recover_expired_tasks()

        if recovered:
            logger.warning(
                "回收过期任务: %s",
                recovered,
            )

        task = self.repository.claim_next_task(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
        )

        if task is None:
            return False

        self.process_task(task)
        return True

    def run_forever(self):
        logger.info(
            "Worker 启动: worker_id=%s",
            self.config.worker_id,
        )

        idle_count = 0

        while not self.stop_event.is_set():
            try:
                processed = self.run_once()

                if processed:
                    idle_count = 0
                    continue

                idle_count += 1

                if (
                    idle_count
                    % self.config.idle_log_interval
                    == 0
                ):
                    logger.info("当前没有待处理任务")

            except Exception:
                logger.exception("Worker 主循环异常")

            self.stop_event.wait(
                self.config.poll_interval
            )

        logger.info(
            "Worker 已退出: worker_id=%s",
            self.config.worker_id,
        )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s "
            "%(message)s"
        ),
    )

    worker = Worker()

    signal.signal(
        signal.SIGINT,
        worker.stop,
    )

    if hasattr(signal, "SIGTERM"):
        signal.signal(
            signal.SIGTERM,
            worker.stop,
        )

    worker.run_forever()


if __name__ == "__main__":
    main()
