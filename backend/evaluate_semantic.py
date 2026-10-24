# backend/evaluate_semantic.py

import csv
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from search import search_segments, search_chunks
from semantic_search import SemanticSearcher


BASE = Path(__file__).resolve().parent
DATA = BASE / "eval_data"

transcript = json.loads(
    (DATA / "transcript.json").read_text(
        encoding="utf-8"
    )
)

dataset = json.loads(
    (DATA / "cases.json").read_text(
        encoding="utf-8"
    )
)

segments = transcript["segments"]
cases = dataset["cases"]

start_to_index = {
    segment["start"]: index
    for index, segment in enumerate(segments)
}

searcher = SemanticSearcher()
searcher.build_index(transcript)

records = []
groups = defaultdict(lambda: defaultdict(list))


def deduplicate_results(results, top_k=5):
    """
    对重叠 Chunk 进行简单去重。
    优先保留排名靠前的窗口。
    """
    selected = []
    used_indices = set()

    for item in results:
        indices = set(item["segment_indices"])

        if indices & used_indices:
            continue

        selected.append(item)
        used_indices.update(indices)

        if len(selected) >= top_k:
            break

    return selected


def evaluate_results(
    results,
    gold_starts,
    method,
):
    """
    gold_starts 是原始 Segment 的起始时间。

    对 Chunk 检索，利用 segment_indices
    映射到原始 Segment。

    对 Baseline，直接根据 start 映射。
    """

    gold_indices = {
        start_to_index[start]
        for start in gold_starts
    }

    retrieved_indices = set()

    for result in results:
        if method == "baseline":
            index = start_to_index.get(
                result["start"]
            )
            if index is not None:
                retrieved_indices.add(index)
        else:
            retrieved_indices.update(
                result["segment_indices"]
            )

    if not gold_indices:
        return {
            "recall_at_5": None,
            "hit_at_5": None,
            "negative_correct": int(
                len(results) == 0
            ),
        }

    correct = len(
        gold_indices & retrieved_indices
    )

    return {
        "recall_at_5": (
            correct / len(gold_indices)
        ),
        "hit_at_5": int(correct > 0),
        "negative_correct": None,
    }


methods = [
    "baseline",
    "chunk",
    "semantic",
]

for case in cases:
    query = case["query"]
    gold_starts = case["gold_starts"]

    for method in methods:
        start = time.perf_counter()

        if method == "baseline":
            results = search_segments(
                transcript,
                query,
            )[:5]

        elif method == "chunk":
            results = search_chunks(
                transcript,
                query,
            )[:5]

        else:
            # 多取一些候选，避免重叠窗口
            # 占满 Top 5。
            candidates = searcher.search(
                query,
                top_k=min(
                    len(searcher.chunks),
                    20,
                ),
            )

            results = deduplicate_results(
                candidates,
                top_k=5,
            )

        elapsed_ms = (
            time.perf_counter() - start
        ) * 1000

        metrics = evaluate_results(
            results,
            gold_starts,
            method,
        )

        record = {
            "case_id": case["id"],
            "type": case["type"],
            "query": query,
            "method": method,
            "result_count": len(results),
            "recall_at_5": metrics[
                "recall_at_5"
            ],
            "hit_at_5": metrics[
                "hit_at_5"
            ],
            "negative_correct": metrics[
                "negative_correct"
            ],
            "latency_ms": round(
                elapsed_ms, 3
            ),
        }

        records.append(record)
        groups[case["type"]][method].append(
            record
        )


def average(values):
    values = [
        value for value in values
        if value is not None
    ]

    if not values:
        return None

    return sum(values) / len(values)


print("\n========== 分类别检索对比 ==========")

for category, method_records in groups.items():
    print(f"\n[{category}]")

    for method in methods:
        items = method_records[method]

        recall = average([
            item["recall_at_5"]
            for item in items
        ])

        negative = average([
            item["negative_correct"]
            for item in items
        ])

        latency = average([
            item["latency_ms"]
            for item in items
        ])

        print(
            f"{method:10s} "
            f"Recall@5={str(round(recall, 3)) if recall is not None else 'N/A':5s} "
            f"Negative={str(round(negative, 3)) if negative is not None else 'N/A':5s} "
            f"Latency={latency:.2f}ms"
        )


output = DATA / "semantic_report.csv"

with output.open(
    "w",
    encoding="utf-8-sig",
    newline="",
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=records[0].keys(),
    )
    writer.writeheader()
    writer.writerows(records)

print("\n评测完成")
print("模型加载时间：", searcher.model_load_seconds)
print("索引构建时间：", searcher.index_seconds)
print("报告路径：", output)
