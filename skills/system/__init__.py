from .campaign_report import query_campaign_report
from .budget_analysis import analyze_budget
from .anomaly_detection import detect_anomaly
from .chart_skill import build_chart

SYSTEM_SKILLS = [
    query_campaign_report,
    analyze_budget,
    detect_anomaly,
    build_chart,
]
