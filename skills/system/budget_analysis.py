def analyze_budget(user_id: str) -> dict:
    """分析预算使用情况（Mock 数据）"""
    total_budget = 100000.0
    total_spend = 67500.0
    spend_rate = total_spend / total_budget
    return {
        "user_id": user_id,
        "total_budget": total_budget,
        "total_spend": total_spend,
        "spend_rate": spend_rate,
        "remaining": total_budget - total_spend,
        "warning": spend_rate > 0.8,
    }
