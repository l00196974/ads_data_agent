// 日期计算辅助函数
function addDays(dateStr, days) {
  const date = new Date(dateStr);
  date.setDate(date.getDate() + days);
  return date.toISOString().split('T')[0];
}

class DSLBuilder {
  build({ indicators, dimensions, filters, startDate, endDate, timeMode }) {
    let dateTimeFilter, filterConditions, finalDimensions, timingDimension;

    // 处理现有的过滤条件
    const existingFilters = Object.values(filters || {}).map((filterConfig) => ({
      oper: filterConfig.oper,
      source: filterConfig.source,
      targetValue: filterConfig.targetValue,
    }));

    if (timeMode === 'request') {
      // 广告请求时间口径
      // dateTimeFilter 扩展90天，确保延迟回传的转化数据不丢失
      const extendedEnd = addDays(endDate, 90);
      dateTimeFilter = [{ start: startDate, end: extendedEnd }];

      // 用 filterConditions 限制 reqDay 范围
      filterConditions = [
        {
          oper: 'BETWEEN',
          source: 'reqDay',
          targetValue: [startDate, endDate],
        },
        ...existingFilters,
      ];

      // dimensions 里加 reqDay（如果用户没传）
      const dimensionsArray = Array.isArray(dimensions) ? dimensions : [];
      finalDimensions = dimensionsArray.includes('reqDay') ? dimensionsArray : ['reqDay', ...dimensionsArray];
      timingDimension = null; // 请求时间口径不用 timingDimension

    } else {
      // 事件发生时间口径（event）
      dateTimeFilter = [{ start: startDate, end: endDate }];
      filterConditions = existingFilters;
      const rawDimensions = Array.isArray(dimensions) ? dimensions : [];

      // day/week/month 是时间粒度标记，映射到 timingDimension 参数，
      // 不是 API 维度——必须从 dimensions 里移除。
      // API 会在返回结果中自动增加 date 字段。
      const timingKeys = ['day', 'week', 'month'];
      const detectedTiming = timingKeys.find(k => rawDimensions.includes(k));
      timingDimension = detectedTiming || null;

      // 从 dimensions 中移除时间粒度标记，保留真正的业务维度
      finalDimensions = rawDimensions.filter(d => !timingKeys.includes(d));
    }

    return {
      pageSize: null,
      pageNum: null,
      top: null,
      timingDimension,
      filterConditions,
      dateTimeFilter,
      orderBy: null,
      indicators: (indicators || []).map((indicatorKey) => ({ indicatorKey })),
      dimensions: finalDimensions.length > 0 ? finalDimensions : null,
      calcFlag: null,
    };
  }
}

module.exports = {
  DSLBuilder,
};
