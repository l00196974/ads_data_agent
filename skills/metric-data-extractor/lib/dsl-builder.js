// 纯字符串日期算法——避免 new Date('YYYY-MM-DD') 在 GMT+8 等非 UTC 环境
// 因"UTC 解析 + 本地 setDate + UTC 序列化"链路漂移 1 天（特别是月底 / 跨年）。
function addDays(dateStr, days) {
  const [y, m, d] = dateStr.split('-').map(Number);
  // 用 Date.UTC 在 UTC 上计算，再格式化回 YYYY-MM-DD —— 同一时区进出不会漂
  const ts = Date.UTC(y, m - 1, d) + days * 86400_000;
  const out = new Date(ts);
  const yy = out.getUTCFullYear();
  const mm = String(out.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(out.getUTCDate()).padStart(2, '0');
  return `${yy}-${mm}-${dd}`;
}

class DSLBuilder {
  build({
    indicators,
    dimensions,
    filters,
    startDate,
    endDate,
    timeMode,
    pageSize,
    sortBy,
    sortOrder,
  }) {
    let dateTimeFilter, filterConditions, finalDimensions, timingDimension;

    // 处理现有的过滤条件
    const existingFilters = Object.values(filters || {}).map((filterConfig) => ({
      oper: filterConfig.oper,
      source: filterConfig.source,
      targetValue: filterConfig.targetValue,
    }));

    if (timeMode === 'request') {
      // 广告请求时间口径
      // dateTimeFilter 扩展 90 天，确保延迟回传的转化数据不丢失
      const extendedEnd = addDays(endDate, 90);
      dateTimeFilter = [{ start: startDate, end: extendedEnd }];

      // 用 filterConditions 限制 reqDay 范围（reqDay 是 API 原始维度名，不转 snake_case）
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
      timingDimension = null; // 请求时间口径不用 timingDimension（reqDay 直接走 dimensions）

    } else {
      // 事件发生时间口径（event）
      dateTimeFilter = [{ start: startDate, end: endDate }];
      filterConditions = existingFilters;
      const rawDimensions = Array.isArray(dimensions) ? dimensions : [];

      // pt_d 是事件时间维度标识，剥离出来当 timingDimension 处理；api-client 内部
      // 再把它统一写回 dimensions 数组（保持向 v2/value API 的协议一致）。
      // 粒度概念（week/month）已废——新协议下只有 pt_d / req_day 两种时间维度。
      const timingKeys = ['pt_d'];
      const detectedTiming = timingKeys.find(k => rawDimensions.includes(k));
      timingDimension = detectedTiming || null;

      // 从 dimensions 移除时间标识，保留真正的业务维度（避免重复）
      finalDimensions = rawDimensions.filter(d => !timingKeys.includes(d));
    }

    // orderBy schema：华为广告 API 期望 [{colName, type}]。
    // colName 可以是维度代码（如 mediaName）或指标代码（如 cost）。
    // Wisedata 后端在 _transformToValueRequest 中会将维度代码映射为 en_name。
    const orderBy = sortBy
      ? [{ colName: sortBy, type: String(sortOrder || 'desc').toUpperCase() }]
      : null;

    return {
      pageSize: pageSize || null,
      pageNum: null,
      top: null,
      timingDimension,
      filterConditions,
      dateTimeFilter,
      orderBy,
      indicators: (indicators || []).map((indicatorKey) => ({ indicatorKey })),
      dimensions: finalDimensions.length > 0 ? finalDimensions : null,
      calcFlag: null,
    };
  }
}

module.exports = {
  DSLBuilder,
  addDays,  // export 给单测
};
