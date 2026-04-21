def detect_anomaly(date: str, region: str, user_id: str) -> dict:
    """检测投放异常（Mock 数据）"""
    return {
        "date": date,
        "region": region,
        "user_id": user_id,
        "anomalies": [
            {"campaign": "华南-移动端-关键词A", "metric": "CTR", "change": -0.42, "severity": "high"},
            {"campaign": "华南-PC端-品牌词", "metric": "CPC", "change": 0.35, "severity": "medium"},
        ],
    }
