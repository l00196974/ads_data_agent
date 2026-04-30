#!/usr/bin/env python
"""演示 skill：写一份 artifact 端到端验证用。"""
import argparse
import json
import os
import pathlib
import sys
from datetime import datetime

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="示例分析报告")
    args = parser.parse_args()

    artifact_dir = os.environ.get("ADS_AGENT_ARTIFACT_DIR")
    artifact_id = os.environ.get("ADS_AGENT_ARTIFACT_ID")
    user_id = os.environ.get("ADS_AGENT_USER_ID")

    if not artifact_dir or not artifact_id:
        print("Error: 缺 ADS_AGENT_ARTIFACT_DIR / ADS_AGENT_ARTIFACT_ID 环境变量")
        sys.exit(1)

    root = pathlib.Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "insights").mkdir(exist_ok=True)
    (root / "charts").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)

    (root / "INDEX.md").write_text(
        f"""# {args.title}

本份 artifact 包含：
- `insights/summary.md` 执行摘要
- `charts/sample.echarts.json` 趋势折线图
- `data/sample.csv` 原始数据（10 行）

---

> 这份是 demo-artifact-writer 生成的示例 artifact。
""",
        encoding="utf-8",
    )

    (root / "insights" / "summary.md").write_text(
        """## 执行摘要

- 关键发现 1：示例数据的趋势呈现稳步上升
- 关键发现 2：周末数据波动较大
- 建议：进一步分析周末波动原因
""",
        encoding="utf-8",
    )

    chart_config = {
        "title": {"text": "示例趋势"},
        "xAxis": {"type": "category", "data": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
        "yAxis": {"type": "value"},
        "series": [{
            "name": "数据",
            "type": "line",
            "data": [120, 132, 145, 167, 189, 220, 198],
            "smooth": True,
        }],
    }
    (root / "charts" / "sample.echarts.json").write_text(
        json.dumps(chart_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (root / "data" / "sample.csv").write_text(
        "day,value,note\nMon,120,常规\nTue,132,常规\nWed,145,常规\nThu,167,常规\nFri,189,常规\nSat,220,周末高峰\nSun,198,周末\n",
        encoding="utf-8",
    )

    manifest = {
        "artifact_id": artifact_id,
        "title": args.title,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id or "unknown",
        "artifact_type": "report",
        "description": "demo-artifact-writer 生成的示例分析报告，含摘要 + 折线图 + 数据表。",
        "entry_files": ["INDEX.md"],
        "related_skills": ["demo-artifact-writer"],
    }
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    sys.stderr.write(f"<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id={artifact_id}>>>\n")
    sys.stderr.flush()

    print(f"已生成 artifact：{artifact_id}（含 5 个文件，主要看 INDEX.md）")


if __name__ == "__main__":
    main()
