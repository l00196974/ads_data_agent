def query_campaign_report(date: str, user_id: str) -> dict:
    """查询广告活动报表（Mock 数据）"""
    return {
        "date": date,
        "user_id": user_id,
        "campaigns": [
            {"name": "华南-移动端-关键词A", "impressions": 12400, "clicks": 380, "ctr": 0.031, "spend": 1240.0},
            {"name": "华南-PC端-品牌词", "impressions": 8200, "clicks": 210, "ctr": 0.026, "spend": 820.0},
        ],
    }
