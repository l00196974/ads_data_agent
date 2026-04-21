from skills.system.campaign_report import query_campaign_report
from skills.system.budget_analysis import analyze_budget
from skills.system.anomaly_detection import detect_anomaly
from skills.system.chart_skill import build_chart


def test_campaign_report_returns_data():
    result = query_campaign_report(date="2026-04-20", user_id="user_001")
    assert "campaigns" in result
    assert isinstance(result["campaigns"], list)


def test_budget_analysis_returns_summary():
    result = analyze_budget(user_id="user_001")
    assert "total_budget" in result
    assert "spend_rate" in result


def test_anomaly_detection_returns_list():
    result = detect_anomaly(date="2026-04-20", region="华南", user_id="user_001")
    assert "anomalies" in result
    assert isinstance(result["anomalies"], list)


def test_build_chart_line():
    result = build_chart(
        chart_type="line",
        title="测试折线图",
        x_data=["00:00", "01:00", "02:00"],
        series=[{"name": "点击量", "data": [10, 20, 15]}]
    )
    assert result["type"] == "line"
    assert result["title"] == "测试折线图"
