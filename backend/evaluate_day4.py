# backend/evaluate_day4.py

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

dataset = json.loads(
    (DATA / "cases.json").read_text(
        encoding="utf-8"
    )
)

segments = transcript["segments"]
cases = dataset["cases"]

searcher = SemanticSearcher()
searcher.build_index(transcript)

# 这里只观察候选阈值，不自动选择最佳值。
thresholds = [None, 0.2, 0.3, 0.4, 0.5, 0.6]

start_to_index = {
    s["start"]: i
    for i, s in enumerate(segments)
}


def evaluate(threshold):
    positive_hits = []
    negative_correct = []

    for case in cases:
        query = case["query"]

        results = searcher.search(
            query,
            top_k=5,
            min_score=threshold,
        )

        gold = {
            start_to_index[s]
            for s in case["gold_starts"]
        }

        retrieved = set()

        for result in results:
            retrieved.update(
                result["segment_indices"]
            )

        if gold:
            positive_hits.append(
                int(bool(gold & retrieved))
            )
        else:
            negative_correct.append(
                int(len(results) == 0)
            )

    hit_rate = (
        sum(positive_hits) / len(positive_hits)
        if positive_hits else 0
    )

    rejection_rate = (
        sum(negative_correct) / len(negative_correct)
        if negative_correct else 0
    )

    return hit_rate, rejection_rate


print("\n========== 阈值敏感性分析 ==========")

for threshold in thresholds:
    started = time.perf_counter()

    hit, rejection = evaluate(threshold)

    print(
        f"threshold={str(threshold):5s} "
        f"Positive Hit@5={hit:.2%} "
        f"Negative Rejection={rejection:.2%} "
        f"耗时={time.perf_counter()-started:.2f}s"
    )

print(
    "\n注意：当前数据已用于开发调试，"
    "结果仅用于诊断，不作为独立测试成绩。"
)
