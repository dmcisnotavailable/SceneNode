# backend/semantic_service.py

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import numpy as np

from chunking import build_chunks
from semantic_search import MODEL_NAME, SemanticSearcher


WINDOW_SIZE = 2
STRIDE = 1
MAX_GAP_SECONDS = 3.0
CACHE_VERSION = 2

_model = None
_model_lock = threading.RLock()

# 仅拦截明显缺乏搜索意图的输入。
# 这不是完整的意图识别系统。
VAGUE_QUERIES = {
    "what", "why", "how", "who", "where",
    "什么", "为什么", "怎么", "哪里", "谁",
}


def is_vague_query(query: str) -> bool:
    normalized = re.sub(
        r"[^\w\u4e00-\u9fff]+",
        "",
        query.casefold(),
    )
    return normalized in VAGUE_QUERIES


def get_model():
    global _model

    with _model_lock:
        if _model is None:
            _model = SemanticSearcher()

        return _model


def fingerprint(transcript: dict) -> str:
    payload = {
        "model": MODEL_NAME,
        "cache_version": CACHE_VERSION,
        "window_size": WINDOW_SIZE,
        "stride": STRIDE,
        "max_gap_seconds": MAX_GAP_SECONDS,
        "segments": transcript["segments"],
    }

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def get_index(transcript: dict, job_path: Path):
    chunks = build_chunks(
        transcript,
        window_size=WINDOW_SIZE,
        stride=STRIDE,
        max_gap_seconds=MAX_GAP_SECONDS,
    )

    cache_path = job_path / "semantic_index.npz"
    expected_hash = fingerprint(transcript)

    if cache_path.exists():
        try:
            with np.load(
                cache_path,
                allow_pickle=False,
            ) as data:
                saved_hash = str(data["fingerprint"].item())
                vectors = data["vectors"].copy()

            if (
                saved_hash == expected_hash
                and vectors.ndim == 2
                and vectors.shape[0] == len(chunks)
                and np.isfinite(vectors).all()
            ):
                return chunks, vectors, True

        except (OSError, ValueError, KeyError):
            pass

    model = get_model().model

    if chunks:
        vectors = np.asarray(
            model.encode(
                [c["text"] for c in chunks],
                batch_size=16,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )
    else:
        vectors = np.empty((0, 0), dtype=np.float32)

    job_path.mkdir(parents=True, exist_ok=True)

    temp_path = job_path / "semantic_index.tmp.npz"

    np.savez_compressed(
        temp_path,
        fingerprint=np.array(expected_hash),
        vectors=vectors,
    )

    temp_path.replace(cache_path)

    return chunks, vectors, False


def semantic_search_video(
    transcript: dict,
    job_path: Path,
    query: str,
    top_k: int = 5,
    min_score: float | None = None,
):
    started = time.perf_counter()

    if is_vague_query(query):
        return [], None, 0.0

    with _model_lock:
        chunks, vectors, cache_hit = get_index(
            transcript, job_path
        )

        if not chunks or top_k <= 0:
            return [], cache_hit, 0.0

        model = get_model().model

        query_vector = np.asarray(
            model.encode(
                [query],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0],
            dtype=np.float32,
        )

        scores = vectors @ query_vector

    order = np.argsort(-scores, kind="stable")

    results = []
    used_indices = set()

    for index in order:
        score = float(scores[index])

        if min_score is not None and score < min_score:
            continue

        chunk = chunks[int(index)]
        indices = set(chunk["segment_indices"])

        if indices & used_indices:
            continue

        results.append({
            "start": chunk["start"],
            "end": chunk["end"],
            "text": chunk["text"],
            "segment_indices": chunk["segment_indices"],
            "score": round(score, 4),
        })

        used_indices.update(indices)

        if len(results) >= top_k:
            break

    elapsed_ms = (
        time.perf_counter() - started
    ) * 1000

    return results, cache_hit, round(elapsed_ms, 2)
