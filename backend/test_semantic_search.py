# backend/test_semantic_search.py

import json
import time
from pathlib import Path

from semantic_search import SemanticSearcher


BASE = Path(__file__).resolve().parent
DATA = BASE / "eval_data"

transcript = json.loads(
    (DATA / "transcript.json").read_text(
        encoding="utf-8"
    )
)

queries = [
    "第二天去哪看夕阳",
    "服务器内部故障",
    "向量化文本",
    "航班晚点",
    "数据库保存订单",
    "火星探测器",
]

searcher = SemanticSearcher()
searcher.build_index(transcript)

for query in queries:
    print("\n" + "=" * 60)
    print("查询：", query)

    start = time.perf_counter()

    results = searcher.search(
        query,
        top_k=5,
    )

    latency_ms = (
        time.perf_counter() - start
    ) * 1000

    print(f"查询耗时：{latency_ms:.2f} ms")

    for rank, item in enumerate(results, 1):
        print(
            f"{rank}. "
            f"score={item['score']:.4f} "
            f"time={item['start']}-{item['end']}"
        )
        print("   ", item["text"])
