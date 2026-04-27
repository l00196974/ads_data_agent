import asyncio


async def build_chart(chart_type: str, title: str, x_data: list, series: list) -> dict:
    """生成 ECharts 图表配置数据（模拟 300ms 渲染编排）"""
    await asyncio.sleep(0.3)
    return {
        "type": chart_type,
        "title": title,
        "x": x_data,
        "series": series,
    }
