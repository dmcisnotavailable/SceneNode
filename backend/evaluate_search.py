# backend/evaluate_search.py

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

from search import search_segments


BASE_DIR = Path(__file__).resolve().parent
JOB_DIR = BASE_DIR / "jobs"
CASES_PATH = BASE_DIR / "search_eval_cases.json"
REPORT_DIR = BASE_DIR / "eval_reports"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_transcript(data):
    if not isinstance(data, dict):
        raise ValueError("转写文件必须是 JSON 对象")

    segments = data.get("segments")

    if not isinstance(segments, list):
        raise ValueError("转写文件缺少 segments 数组")

    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"segment {index} 格式错误")

        if not isinstance(segment.get("text"), str):
            raise ValueError(f"segment {index} 缺少 text")

        for field in ("start", "end"):
            value = segment.get(field)

            if type(value) not in (int, float):
                raise ValueError(
                    f"segment {index} 的 {field} 无效"
                )

        if segment["end"] < segment["start"]:
            raise ValueError(
                f"segment {index} 时间范围错误"
            )


def overlaps(result, expected_start, expected_end):
    """判断搜索片段与人工标注区间是否有重叠。"""
    return (
        result["start"] < expected_end
        and result["end"] > expected_start
    )


def evaluate_case(transcript, case):
    query = case["query"].strip()
    case_type = case["type"]

    if not query:
        raise ValueError("测试关键词不能为空")

    start_time = time.perf_counter()

    results = search_segments(transcript, query)

    latency_ms = (
        time.perf_counter() - start_time
    ) * 1000

    if case_type == "negative":
        passed = len(results) == 0
        hit_at_1 = None
        hit_at_5 = None

    else:
        expected_start = float(case["expected_start"])
        expected_end = float(case["expected_end"])

        if expected_end <= expected_start:
            raise ValueError("标注的时间范围无效")

        def is_relevant(result):
            return overlaps(
                result,
                expected_start,
                expected_end
            )

        hit_at_1 = any(
            is_relevant(result)
            for result in results[:1]
        )

        hit_at_5 = any(
            is_relevant(result)
            for result in results[:5]
        )

        passed = any(
            is_relevant(result)
            for result in results
        )

    return {
        "id": case["id"],
        "type": case_type,
        "query": query,
        "passed": passed,
        "hit_at_1": hit_at_1,
        "hit_at_5": hit_at_5,
        "result_count": len(results),
        "latency_ms": round(latency_ms, 3),
        "first_result_start": (
            results[0]["start"] if results else None
        ),
        "first_result_text": (
            results[0]["text"] if results else ""
        )
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--video-id",
        required=True,
        help="需要评测的视频 ID"
    )

    args = parser.parse_args()
    video_id = args.video_id

    # 只允许 UUID hex 格式，避免路径穿越
    from uuid import UUID

    try:
        if UUID(hex=video_id).hex != video_id:
            raise ValueError()
    except (ValueError, AttributeError):
        raise SystemExit("无效的视频 ID")

    transcript_path = (
        JOB_DIR / video_id / "transcript.json"
    )

    if not transcript_path.is_file():
        raise SystemExit(
            f"找不到转写文件：{transcript_path}"
        )

    transcript = load_json(transcript_path)
    validate_transcript(transcript)

    config = load_json(CASES_PATH)

    if config.get("video_id") != video_id:
        raise SystemExit(
            "测试集 video_id 与命令行参数不一致"
        )

    cases = config.get("cases")

    if not isinstance(cases, list) or not cases:
        raise SystemExit("测试集不能为空")

    results = []
    valid_types = {"exact", "semantic", "negative"}

    for case in cases:
        try:
            if case.get("type") not in valid_types:
                raise ValueError("未知测试类型")

            result = evaluate_case(transcript, case)
            results.append(result)

            status = "PASS" if result["passed"] else "FAIL"

            print(
                f"[{status}] {result['id']} "
                f"| {result['query']} "
                f"| 匹配数={result['result_count']} "
                f"| {result['latency_ms']:.3f}ms"
            )

        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"测试用例 {case.get('id')} 无效：{exc}"
            )

    positive = [
        r for r in results
        if r["type"] != "negative"
    ]

    negative = [
        r for r in results
        if r["type"] == "negative"
    ]

    def ratio(items, key):
        if not items:
            return None

        return round(
            sum(bool(item[key]) for item in items)
            / len(items),
            4
        )

    summary = {
        "video_id": video_id,
        "total_cases": len(results),
        "positive_cases": len(positive),
        "negative_cases": len(negative),
        "recall_at_1": ratio(positive, "hit_at_1"),
        "recall_at_5": ratio(positive, "hit_at_5"),
        "positive_hit_rate": ratio(
            positive, "passed"
        ),
        "negative_rejection_rate": ratio(
            negative, "passed"
        ),
        "average_latency_ms": round(
            sum(r["latency_ms"] for r in results)
            / len(results),
            3
        )
    }

    print("\n========== 评测汇总 ==========")

    for key, value in summary.items():
        print(f"{key}: {value}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    json_path = REPORT_DIR / f"eval_{timestamp}.json"
    csv_path = REPORT_DIR / f"eval_{timestamp}.csv"

    json_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "results": results
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(results[0].keys())
        )

        writer.writeheader()
        writer.writerows(results)

    print(f"\nJSON 报告：{json_path}")
    print(f"CSV 报告：{csv_path}")


if __name__ == "__main__":
    main()
