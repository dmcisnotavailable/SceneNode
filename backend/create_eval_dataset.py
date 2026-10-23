# backend/create_eval_dataset.py
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "eval_data"
OUT.mkdir(exist_ok=True)

# 每个片段：起始秒、结束秒、转写文本
rows = [
    (10, 18, "我们明天去海边看日落吧，下午四点出发。"),
    (28, 36, "酒店订在东京新宿，距离地铁站大约五分钟。"),
    (48, 56, "今天午饭在一家拉面店吃的，人均八十元。"),
    (68, 76, "明天早上先去便利店买早餐，然后坐车出发。"),
    (90, 98, "这次旅行的预算是三千元，住宿占了一半。"),

    (125, 134, "主持人问嘉宾第一次拍电影是什么感受。"),
    (145, 154, "嘉宾说拍摄现场最困难的是连续熬夜。"),
    (165, 174, "导演解释这场戏为什么选择长镜头。"),
    (185, 194, "他们讨论了电影上映后的观众反应。"),
    (205, 214, "主持人提到下一季节目可能更换拍摄地点。"),

    (245, 254, "Today we will start the Python tutorial."),
    (265, 274, "The database stores customer orders in MySQL."),
    (285, 294, "Redis is used to cache frequently accessed data."),
    (305, 314, "The server returns an HTTP 500 error."),
    (325, 334, "We use Docker to deploy the application."),

    (365, 374, "The flight to London was delayed by two hours."),
    (385, 394, "We had dinner at an Italian restaurant."),
    (405, 414, "The museum opens at nine in the morning."),
    (425, 434, "I bought a new camera for recording videos."),
    (445, 454, "The weather forecast says it will rain tomorrow."),

    (485, 494, "今天我们来讨论 Agent 的工具调用机制。"),
    (505, 514, "模型需要根据用户意图选择合适的 Tool。"),
    (525, 534, "RAG 系统通常包含检索和生成两个阶段。"),
    (545, 554, "Embedding 可以将文本映射为向量。"),
    (565, 574, "我们使用 FastAPI 构建后端接口。"),

    (605, 614, "这个餐厅的服务很好，但是上菜比较慢。"),
    (625, 634, "另一家餐厅的环境不错，价格也比较便宜。"),
    (645, 654, "晚饭结束以后，我们去了附近的电影院。"),

    # 跨 segment：完整查询在原文中连续，但不在单段中
    (685, 691, "我们计划第二天早上"),
    (691, 698, "去海洋馆参观企鹅。"),

    # ASR 噪声模拟：将 Kubernetes 误识别为近似读音
    (715, 724, "今天介绍库伯内提斯的容器调度机制。")
]

transcript = {
    "language": "mixed",
    "segments": [
        {"start": a, "end": b, "text": t}
        for a, b, t in rows
    ]
}

# 测试用例：
# gold 为人工指定的相关片段起始秒
# negative 表示应当没有相关片段
cases = [
    # 精确匹配：5
    ("E01", "exact", "海边", [10]),
    ("E02", "exact", "新宿", [28]),
    ("E03", "exact", "MySQL", [265]),
    ("E04", "exact", "FastAPI", [565]),
    ("E05", "exact", "电影上映", [185]),

    # 大小写与中英混合：3
    ("C01", "case", "PYTHON", [245]),
    ("C02", "case", "redis", [285]),
    ("C03", "case", "agent", [485]),

    # 语义改写：4
    ("S01", "semantic", "第二天去哪看夕阳", [10]),
    ("S02", "semantic", "服务器内部故障", [305]),
    ("S03", "semantic", "向量化文本", [545]),
    ("S04", "semantic", "航班晚点", [365]),

    # 跨语言：3
    ("L01", "cross_language", "航班延误", [365]),
    ("L02", "cross_language", "数据库保存订单", [265]),
    ("L03", "cross_language", "video recording equipment", [425]),

    # 多结果：2
    ("M01", "multiple", "餐厅", [605, 625]),
    ("M02", "multiple", "tomorrow", [445]),

    # 跨 segment：1
    ("B01", "boundary", "早上去海洋馆", [685, 691]),

    # ASR 错误：1
    ("A01", "asr_noise", "Kubernetes", [715]),

    # 真实语义负例：4
    ("N01", "negative", "火星探测器", []),
    ("N02", "negative", "量子计算机", []),
    ("N03", "negative", "football match", []),
    ("N04", "negative", "股票市场暴跌", []),

    # 有词但不相关：2
    ("H01", "hard_negative", "餐厅发生火灾", []),
    ("H02", "hard_negative", "Python 安全漏洞", [])
]

dataset = {
    "dataset_name": "SceneNote Synthetic Retrieval v1",
    "source": "synthetic",
    "description": "人工构造的检索测试数据，非真实视频",
    "cases": [
        {
            "id": case_id,
            "type": kind,
            "query": query,
            "gold_starts": gold
        }
        for case_id, kind, query, gold in cases
    ]
}

(OUT / "transcript.json").write_text(
    json.dumps(transcript, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

(OUT / "cases.json").write_text(
    json.dumps(dataset, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(f"已生成 {len(transcript['segments'])} 个转写片段")
print(f"已生成 {len(cases)} 条测试用例")
print(f"数据目录：{OUT}")
