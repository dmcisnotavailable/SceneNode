# backend/evaluate_search_v2.py
import csv
import json
import time
from collections import defaultdict
from pathlib import Path

from search import search_segments

BASE = Path(__file__).resolve().parent
DATA = BASE / "eval_data"

transcript = json.loads(
    (DATA / "transcript.json").read_text(encoding="utf-8")
)

dataset = json.loads(
    (DATA / "cases.json").read_text(encoding="utf-8")
)

segments = transcript["segments"]
cases = dataset["cases"]

# 当前测试数据中每个 start 唯一，用它作为片段 ID
valid_starts = {s["start"] for s in segments}

for case in cases:
    if not set(case["gold_starts"]).issubset(valid_starts):
        raise ValueError(
            f"{case['id']} 的人工标注不存在于转写文件中"
        )

records = []
grouped = defaultdict(list)

for case in cases:
    query = case["query"]
    gold = set(case["gold_starts"])

    t0 = time.perf_counter()
    results = search_segments(transcript, query)
    elapsed = (time.perf_counter() - t0) * 1000

    retrieved = [r["start"] for r in results]

    def recall_at(k):
        if not gold:
            return None
        return len(set(retrieved[:k]) & gold) / len(gold)

    def precision_at(k):
        if not gold:
            return None
        top = retrieved[:k]
        return len(set(top) & gold) / k

    if gold:
        hit = bool(set(retrieved) & gold)
        full_recall = len(set(retrieved) & gold) / len(gold)
        correct = len(set(retrieved) & gold)
        precision_all = correct / len(retrieved) if retrieved else 0
    else:
        hit = False
        full_recall = None
        precision_all = None

    # 对负例而言，只有返回空结果才算通过
    passed = hit if gold else len(retrieved) == 0

    record = {
        "id": case["id"],
        "type": case["type"],
        "query": query,
        "gold_count": len(gold),
        "result_count": len(retrieved),
        "hit_at_1": (
            int(bool(set(retrieved[:1]) & gold))
            if gold else None
        ),
        "recall_at_1": recall_at(1),
        "recall_at_5": recall_at(5),
        "precision_at_5": precision_at(5),
        "full_recall": full_recall,
        "precision_all": precision_all,
        "negative_correct": (
            int(len(retrieved) == 0)
            if not gold else None
        ),
        "passed": passed,
        "latency_ms": round(elapsed, 4)
    }

    records.append(record)
    grouped[case["type"]].append(record)

    status = "PASS" if passed else "FAIL"
    print(
        f"[{status}] {case['id']:3s} "
        f"{case['type']:15s} "
        f"{query}"
    )


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


print("\n========== 分类别评测 ==========")

for kind, items in grouped.items():
    positives = [r for r in items if r["gold_count"] > 0]
    negatives = [r for r in items if r["gold_count"] == 0]

    if positives:
        score = mean([r["recall_at_5"] for r in positives])
        label = "Recall@5"
    else:
        score = mean([r["negative_correct"] for r in negatives])
        label = "负例拒绝率"

    print(
        f"{kind:16s} "
        f"数量={len(items):2d} "
        f"{label}={score:.2%}"
    )


positive = [r for r in records if r["gold_count"] > 0]
negative = [r for r in records if r["gold_count"] == 0]

summary = {
    "total_cases": len(records),
    "positive_cases": len(positive),
    "negative_cases": len(negative),
    "macro_recall_at_1": mean(
        [r["recall_at_1"] for r in positive]
    ),
    "macro_recall_at_5": mean(
        [r["recall_at_5"] for r in positive]
    ),
    "macro_precision_at_5": mean(
        [r["precision_at_5"] for r in positive]
    ),
    "negative_rejection_rate": mean(
        [r["negative_correct"] for r in negative]
    ),
    "average_latency_ms": mean(
        [r["latency_ms"] for r in records]
    )
}

print("\n========== 总体指标 ==========")
for key, value in summary.items():
    print(f"{key}: {value}")

report = DATA / "report.csv"

with report.open(
    "w", encoding="utf-8-sig", newline=""
) as f:
    writer = csv.DictWriter(
        f, fieldnames=records[0].keys()
    )
    writer.writeheader()
    writer.writerows(records)

(DATA / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(f"\n评测报告：{report}")
