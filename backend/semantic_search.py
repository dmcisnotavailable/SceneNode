# backend/semantic_search.py

import time

import numpy as np
from sentence_transformers import SentenceTransformer

from chunking import build_chunks


MODEL_NAME = (
    "sentence-transformers/"
    "paraphrase-multilingual-MiniLM-L12-v2"
)


class SemanticSearcher:
    def __init__(self, model_name=MODEL_NAME):
        print(f"正在加载 Embedding 模型：{model_name}")

        start = time.perf_counter()

        self.model = SentenceTransformer(
            model_name,
            device="cpu",
        )

        self.model_load_seconds = (
            time.perf_counter() - start
        )

        print(
            f"模型加载完成："
            f"{self.model_load_seconds:.2f} 秒"
        )

        self.chunks = []
        self.embeddings = None
        self.index_seconds = 0.0

    def build_index(
        self,
        transcript: dict,
        window_size: int = 2,
        stride: int = 1,
    ):
        """
        将转写文本转换为 Chunk，
        并批量生成 Embedding。
        """

        start = time.perf_counter()

        self.chunks = build_chunks(
            transcript,
            window_size=window_size,
            stride=stride,
        )

        if not self.chunks:
            self.embeddings = None
            self.index_seconds = 0.0
            return

        texts = [
            chunk["text"]
            for chunk in self.chunks
        ]

        self.embeddings = np.asarray(
            self.model.encode(
                texts,
                batch_size=16,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=True,
            ),
            dtype=np.float32,
        )

        self.index_seconds = (
            time.perf_counter() - start
        )

        print(
            f"索引构建完成："
            f"{len(self.chunks)} 个 Chunk，"
            f"耗时 {self.index_seconds:.2f} 秒"
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score=None,
    ) -> list[dict]:
        """
        将查询编码为向量，
        与 Chunk 向量计算余弦相似度。

        min_score=None 表示不进行阈值过滤。
        """

        query = query.strip()

        if not query:
            return []

        if top_k <= 0:
            return []

        if self.embeddings is None:
            return []

        query_vector = np.asarray(
            self.model.encode(
                [query],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )[0],
            dtype=np.float32,
        )

        # 向量已经 L2 归一化。
        # 点积等价于余弦相似度。
        scores = self.embeddings @ query_vector

        ranked_indices = np.argsort(
            -scores,
            kind="stable",
        )

        results = []

        for index in ranked_indices:
            score = float(scores[index])

            if (
                min_score is not None
                and score < min_score
            ):
                continue

            chunk = self.chunks[int(index)]

            results.append({
                "chunk_id": chunk["chunk_id"],
                "start": chunk["start"],
                "end": chunk["end"],
                "text": chunk["text"],
                "segment_indices": chunk[
                    "segment_indices"
                ],
                "score": round(score, 4),
            })

            if len(results) >= top_k:
                break

        return results
