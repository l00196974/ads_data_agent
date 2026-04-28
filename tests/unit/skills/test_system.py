import pytest

from skills.system.anomaly_detection import detect_anomaly
from skills.system.budget_analysis import analyze_budget
from skills.system.campaign_report import query_campaign_report
from skills.system.chart_skill import build_chart


@pytest.mark.asyncio
async def test_campaign_report_returns_data():
    result = await query_campaign_report(date="2026-04-20", user_id="user_001")
    assert "campaigns" in result
    assert isinstance(result["campaigns"], list)


@pytest.mark.asyncio
async def test_budget_analysis_returns_summary():
    result = await analyze_budget(user_id="user_001")
    assert "total_budget" in result
    assert "spend_rate" in result


@pytest.mark.asyncio
async def test_anomaly_detection_returns_list():
    result = await detect_anomaly(date="2026-04-20", region="华南", user_id="user_001")
    assert "anomalies" in result
    assert isinstance(result["anomalies"], list)


@pytest.mark.asyncio
async def test_build_chart_line():
    result = await build_chart(
        chart_type="line",
        title="测试折线图",
        x_data=["00:00", "01:00", "02:00"],
        series=[{"name": "点击量", "data": [10, 20, 15]}],
    )
    assert result["type"] == "line"
    assert result["title"] == "测试折线图"
