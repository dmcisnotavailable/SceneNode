import logging
import signal
import threading

from pathlib import Path

from asset_analysis import analyze_asset
from task_repository import TaskRepository
from thumbnail_service import generate_thumbnail
from worker_config import load_worker_config


logger = logging.getLogger("scenenote.asset_worker")


# 素材分析产物根目录，与视频任务的 jobs/ 目录分开。
ASSET_JOB_DIR = Path(__file__).resolve().parent / "asset_jobs"


class AssetWorker:
    """
    素材分析 Worker。

    与视频分析 Worker 共用同一套可靠性设计：
    - 原子领取任务 + 租约
    - 心跳续租
    - Fencing Token 校验
    - 过期任务回收

    区别在于任务表独立（asset_tasks），
    不把 asset_id 伪装成 video_id。
    """

    def __init__(
        self,
        repository=None,
        config=None,
        asset_job_dir=None,
    ):
        self.repository = repository or TaskRepository()
        self.config = config or load_worker_config()
        self.asset_job_dir = (
            Path(asset_job_dir)
            if asset_job_dir
            else ASSET_JOB_DIR
        )

        self.stop_event = threading.Event()

    def stop(self, signum=None, frame=None):
        """请求 Worker 优雅退出。"""

        logger.info("收到停止信号，准备退出素材 Worker")
        self.stop_event.set()

    def _heartbeat_loop(
        self,
        asset_id,
        fencing_token,
        lease_lost,
        heartbeat_stop,
    ):
        """独立线程负责素材分析任务的心跳续租。"""

        while not heartbeat_stop.wait(
            self.config.heartbeat_interval
        ):
            try:
                success = (
                    self.repository.heartbeat_asset_task(
                        asset_id=asset_id,
                        worker_id=self.config.worker_id,
                        fencing_token=fencing_token,
                        lease_seconds=(
                            self.config.lease_seconds
                        ),
                    )
                )

                if not success:
                    logger.warning(
                        "素材任务租约丢失: asset_id=%s",
                        asset_id,
                    )
                    lease_lost.set()
                    return

                logger.debug(
                    "素材任务心跳续租成功: asset_id=%s",
                    asset_id,
                )

            except Exception:
                logger.exception(
                    "素材任务心跳续租异常: asset_id=%s",
                    asset_id,
                )
                lease_lost.set()
                return

    def process_task(self, task):
        """执行已经领取的素材分析任务。"""

        asset_id = task["asset_id"]
        video_path = Path(task["asset_source_path"])
        fencing_token = task["fencing_token"]
        worker_id = self.config.worker_id

        def _fail(message):
            """更新素材和任务为失败状态。"""

            self.repository.update_asset_analysis(
                asset_id,
                analysis_error=message,
                analysis_status="failed",
            )

            self.repository.update_asset_task_status(
                asset_id,
                "failed",
                message,
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

        # 原始素材文件可能已被移动或删除。
        if not video_path.is_file():
            logger.error(
                "原始素材文件不存在: %s",
                video_path,
            )
            _fail("原始素材文件不存在")
            return

        lease_lost = threading.Event()
        heartbeat_stop = threading.Event()

        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(
                asset_id,
                fencing_token,
                lease_lost,
                heartbeat_stop,
            ),
            name=f"asset-heartbeat-{asset_id}",
            daemon=True,
        )

        logger.info(
            "开始分析素材: asset_id=%s worker=%s token=%s",
            asset_id,
            worker_id,
            fencing_token,
        )

        heartbeat_thread.start()

        # 每个素材独立的产物目录：asset_jobs/{asset_id}/。
        job_dir = self.asset_job_dir / asset_id
        job_dir.mkdir(parents=True, exist_ok=True)

        try:
            if lease_lost.is_set():
                raise RuntimeError("任务租约已失效")

            self.repository.update_asset_analysis(
                asset_id,
                analysis_status="processing",
            )

            # 第一步：生成缩略图（不依赖视觉模型）。
            thumbnail_path = generate_thumbnail(
                str(video_path),
                str(job_dir / "thumbnail.jpg"),
            )

            if lease_lost.is_set():
                logger.warning(
                    "缩略图生成期间租约丢失: %s",
                    asset_id,
                )
                return

            self.repository.update_asset_analysis(
                asset_id,
                thumbnail_path=thumbnail_path,
            )

            # 第二步：抽帧并生成视觉概述。
            result = analyze_asset(
                str(video_path),
                str(job_dir),
            )

            if lease_lost.is_set():
                logger.warning(
                    "素材分析期间租约丢失: %s",
                    asset_id,
                )
                return

            self.repository.update_asset_analysis(
                asset_id,
                scene=result["scene"],
                description=result["description"],
                analysis_error="",
                analysis_status="completed",
            )

            self.repository.update_asset_task_status(
                asset_id,
                "completed",
                "素材分析完成",
                worker_id=worker_id,
                fencing_token=fencing_token,
            )

            logger.info(
                "素材分析完成: asset_id=%s",
                asset_id,
            )

        except Exception as exc:
            logger.exception(
                "素材 Worker 执行任务异常: %s",
                asset_id,
            )

            if not lease_lost.is_set():
                try:
                    _fail(f"素材分析失败: {exc}")
                except Exception:
                    logger.exception(
                        "更新素材失败状态异常: %s",
                        asset_id,
                    )

        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(
                timeout=self.config.heartbeat_interval + 1
            )

    def run_once(self):
        """
        尝试领取并执行一个素材分析任务。

        返回 True：领取到了任务。
        返回 False：当前没有任务。
        """

        recovered = (
            self.repository.recover_expired_asset_tasks()
        )

        if recovered:
            logger.warning(
                "回收过期素材任务: %s",
                recovered,
            )

        task = self.repository.claim_next_asset_task(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
        )

        if task is None:
            return False

        self.process_task(task)
        return True

    def run_forever(self):
        logger.info(
            "素材 Worker 启动: worker_id=%s",
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
                    logger.info("当前没有待处理的素材任务")

            except Exception:
                logger.exception("素材 Worker 主循环异常")

            self.stop_event.wait(
                self.config.poll_interval
            )

        logger.info(
            "素材 Worker 已退出: worker_id=%s",
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

    worker = AssetWorker()

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
