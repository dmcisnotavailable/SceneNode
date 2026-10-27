import csv
import json
import time
from pathlib import Path

from faster_whisper import WhisperModel

BASE_DIR = Path(__file__).resolve().parent

VIDEO_ID = "77d0943bb9bc450c9bc663c14ae62fcb"
AUDIO_PATH = BASE_DIR / "jobs" / VIDEO_ID / "audio.wav"
OUTPUT_DIR = BASE_DIR / "jobs" / VIDEO_ID / "benchmark"

EXPERIMENTS = [
    {
        "name": "small_beam5",
        "model": "small",
        "beam_size": 5,
        "vad_filter": True,
    },
    {
        "name": "small_beam1",
        "model": "small",
        "beam_size": 1,
        "vad_filter": True,
    },
    {
        "name": "base_beam5",
        "model": "base",
        "beam_size": 5,
        "vad_filter": True,
    },
    {
        "name": "small_no_vad",
        "model": "small",
        "beam_size": 5,
        "vad_filter": False,
    },
]


def run_experiment(config, model_cache):
    name = config["name"]
    model_name = config["model"]

    print(f"\n========== {name} ==========")

    # 同一进程内复用模型
    if model_name in model_cache:
        model = model_cache[model_name]
        load_seconds = 0.0
        cache_hit = True
    else:
        start = time.perf_counter()

        model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
        )

        load_seconds = time.perf_counter() - start
        model_cache[model_name] = model
        cache_hit = False

    print(f"模型加载: {load_seconds:.2f}s")
    print(f"模型缓存命中: {cache_hit}")

    start = time.perf_counter()

    segments, info = model.transcribe(
        str(AUDIO_PATH),
        language=None,
        task="transcribe",
        beam_size=config["beam_size"],
        vad_filter=config["vad_filter"],
    )

    results = []

    # 必须完整遍历生成器
    for segment in segments:
        results.append({
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "text": segment.text.strip(),
        })

    infer_seconds = time.perf_counter() - start

    output = {
        "language": info.language,
        "segments": results,
    }

    output_path = OUTPUT_DIR / f"{name}.json"

    output_path.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"推理耗时: {infer_seconds:.2f}s")
    print(f"片段数量: {len(results)}")

    return {
        "experiment": name,
        "model": model_name,
        "beam_size": config["beam_size"],
        "vad_filter": config["vad_filter"],
        "cache_hit": cache_hit,
        "load_seconds": round(load_seconds, 3),
        "infer_seconds": round(infer_seconds, 3),
        "total_seconds": round(
            load_seconds + infer_seconds, 3
        ),
        "segments": len(results),
    }


def main():
    if not AUDIO_PATH.exists():
        raise FileNotFoundError(
            f"找不到音频文件: {AUDIO_PATH}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    model_cache = {}
    records = []

    for config in EXPERIMENTS:
        record = run_experiment(
            config,
            model_cache,
        )
        records.append(record)

    csv_path = OUTPUT_DIR / "benchmark_results.csv"

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(records[0].keys()),
        )
        writer.writeheader()
        writer.writerows(records)

    print("\n========== 实验完成 ==========")

    for record in records:
        print(
            f"{record['experiment']}: "
            f"{record['infer_seconds']:.2f}s"
        )

    print(f"\n结果保存到: {csv_path}")


if __name__ == "__main__":
    main()
