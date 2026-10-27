import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("scenenote.perf")

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(message)s")
    )
    logger.addHandler(handler)

logger.setLevel(logging.INFO)
logger.propagate = False


@contextmanager
def measure(stage, video_id=None, metrics=None):
    start = time.perf_counter()
    error = None

    try:
        yield
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        elapsed = round(
            time.perf_counter() - start, 3
        )

        logger.info(
            "[PERF] video_id=%s stage=%s "
            "elapsed=%.3fs result=%s",
            video_id or "unknown",
            stage,
            elapsed,
            "failed" if error else "ok",
        )

        if metrics is not None:
            metrics[stage] = {
                "seconds": elapsed,
                "result": "failed" if error else "ok",
                "error": error,
            }


def save_metrics(job_path, video_id, metrics):
    path = Path(job_path) / "performance.json"
    temp_path = path.with_suffix(".json.tmp")

    data = {
        "video_id": video_id,
        "recorded_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "stages": metrics,
    }

    temp_path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp_path.replace(path)
    return path
