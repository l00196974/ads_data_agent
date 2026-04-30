---
name: demo-artifact-writer
description: 演示用：生成一份带数据 + 图表 + 洞察的示例 artifact
---

## 用法

```
write-demo-report --title "<报告标题>"
```

写一份 artifact 到 `$ADS_AGENT_ARTIFACT_DIR`，含 INDEX.md / manifest.yaml / insights/summary.md / charts/sample.echarts.json / data/sample.csv，最后向 stderr 输出 `<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=$ADS_AGENT_ARTIFACT_ID>>>`。
