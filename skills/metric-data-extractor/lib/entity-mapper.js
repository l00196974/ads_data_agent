const { readCsv } = require('./csv-loader');

function normalize(value) {
  return String(value || '').trim().toLowerCase();
}

class EntityMapper {
  constructor() {
    this.metricsConfig = readCsv('config/metrics.csv').filter((row) => row.metric_code);
    this.dimensionsConfig = readCsv('config/dimensions.csv').filter((row) => row.dimension_code);
    this.dimensionValuesConfig = readCsv('config/dimension-values.csv').filter((row) => row.dimension_code);
  }

  map(input) {
    const result = {
      metrics: [],
      dimensions: [],
      filters: {},
      errors: [],
    };

    for (const metric of input.metrics || []) {
      const mapped = this.mapMetric(metric);
      if (mapped.error) {
        result.errors.push({
          field: 'metric',
          value: metric,
          message: `指标 "${metric}" 无法识别`,
          suggestions: this.getSuggestionsForMetric(),
        });
        continue;
      }
      result.metrics.push(mapped.value);
    }

    for (const dimension of input.dimensions || []) {
      const mapped = this.mapDimension(dimension);
      if (mapped.error) {
        result.errors.push({
          field: 'dimension',
          value: dimension,
          message: `维度 "${dimension}" 无法识别`,
          suggestions: this.getSuggestionsForDimension(),
        });
        continue;
      }
      result.dimensions.push(mapped.value);
    }

    for (const [key, rawValue] of Object.entries(input.filters || {})) {
      const mappedDimension = this.mapDimension(key);
      if (mappedDimension.error) {
        result.errors.push({
          field: 'filter',
          value: key,
          message: `过滤条件维度 "${key}" 无法识别`,
          suggestions: this.getSuggestionsForDimension(),
        });
        continue;
      }

      // query-metrics 把 --filters 解析成 `{oper, values}` 字典；
      // 但也兼容历史调用方传入数组或单值。三种形态都解出 (inputOper, valueList)。
      // 关键：必须保留用户传入的 oper（GT/LIKE/IN/BETWEEN 等），
      // 之前硬编码 'EQUAL' 会让 `--filters "cost=gt:1000"` 被改成 EQUAL 1000，是真实 bug。
      let inputOper = 'EQUAL';
      let valueList;
      if (Array.isArray(rawValue)) {
        valueList = rawValue;
      } else if (rawValue !== null && typeof rawValue === 'object' && 'values' in rawValue) {
        valueList = Array.isArray(rawValue.values) ? rawValue.values : [];
        if (rawValue.oper) inputOper = rawValue.oper;
      } else {
        valueList = [rawValue];
      }

      const resolvedValues = [];

      for (const value of valueList) {
        const mappedValue = this.mapDimensionValue(mappedDimension.value, value);
        if (mappedValue.error) {
          result.errors.push({
            field: 'filter_value',
            dimension: mappedDimension.value,
            value,
            message: `维度 "${mappedDimension.value}" 的值 "${value}" 无法识别`,
            suggestions: this.getSuggestionsForDimensionValue(mappedDimension.value),
          });
          continue;
        }
        resolvedValues.push(mappedValue.valueCode);
      }

      // 空值运算符（IS NULL / IS NOT NULL）即使 resolvedValues 空也要保留——
      // 它的语义就是不需要值。
      const isNullOper = inputOper === 'IS NULL' || inputOper === 'IS NOT NULL';
      if (resolvedValues.length > 0 || isNullOper) {
        result.filters[mappedDimension.value] = {
          source: mappedDimension.value,
          oper: inputOper,
          targetValue: resolvedValues,
        };
      }
    }

    return result;
  }

  mapMetric(input) {
    const target = normalize(input);

    for (const row of this.metricsConfig) {
      if (normalize(row.metric_code) === target || normalize(row.metric_name) === target) {
        return { value: row.metric_code };
      }

      // 读 CSV 的 metric_aliases 列（逗号或全角逗号分隔）—— 单一数据源，
      // 别名在 CSV 里更新即生效，不再依赖代码里 hardcoded 的 alias 表。
      const aliases = String(row.metric_aliases || '')
        .split(/[,，]/)
        .map((a) => normalize(a))
        .filter(Boolean);
      if (aliases.includes(target)) {
        return { value: row.metric_code };
      }

      if (normalize(row.metric_name).includes(target) || target.includes(normalize(row.metric_name))) {
        return { value: row.metric_code };
      }
    }

    // 旧的 hardcoded aliasMap 已删——它把"曝光→impressions"等错误映射成
    // metrics.csv 里**不存在**的 code，导致 LLM 传中文时 mapper 返回根本
    // 调不通的 metric。CSV 的 metric_aliases 列是唯一可信来源。
    return { error: true };
  }

  mapDimension(input) {
    const target = normalize(input);

    // day/week/month 是事件时间口径下的时间粒度标记，
    // 会被 DSLBuilder 映射到 timingDimension 参数（而非 API 维度）。
    // API 返回结果中会自动增加 date 字段。
    const timingGranularities = ['day', 'week', 'month'];
    if (timingGranularities.includes(target)) {
      return { value: target };
    }

    const aliasMap = {
      日期: 'day',
      天: 'day',
      按天: 'day',
      按周: 'week',
      周: 'week',
      按月: 'month',
      月: 'month',
      推广对象: 'promotionTarget',
      计划名称: 'promotionTarget',
      渠道: 'channel',
      媒体: 'channel',
      落地页: 'landingPage',
      创意: 'creative',
      设备: 'device',
    };

    if (aliasMap[input]) {
      return { value: aliasMap[input] };
    }

    for (const row of this.dimensionsConfig) {
      if (normalize(row.dimension_code) === target || normalize(row.dimension_name) === target) {
        return { value: row.dimension_code };
      }

      if (normalize(row.dimension_name).includes(target) || target.includes(normalize(row.dimension_name))) {
        return { value: row.dimension_code };
      }
    }

    return { error: true };
  }

  mapDimensionValue(dimensionCode, input) {
    const target = normalize(input);
    const candidates = this.dimensionValuesConfig.filter((row) => row.dimension_code === dimensionCode);

    for (const row of candidates) {
      if (normalize(row.value) === target || normalize(row.value_desc) === target) {
        return { valueCode: row.value, valueName: row.value_desc };
      }

      const aliases = String(row.value_aliases || '')
        .split(',')
        .map((value) => normalize(value))
        .filter(Boolean);

      if (aliases.includes(target)) {
        return { valueCode: row.value, valueName: row.value_desc };
      }

      if (normalize(row.value_desc).includes(target) || target.includes(normalize(row.value_desc))) {
        return { valueCode: row.value, valueName: row.value_desc };
      }
    }

    return { error: true };
  }

  getSuggestionsForMetric() {
    return this.metricsConfig.slice(0, 6).map((row) => `${row.metric_code} (${row.metric_name})`);
  }

  getSuggestionsForDimension() {
    return this.dimensionsConfig.slice(0, 6).map((row) => `${row.dimension_code} (${row.dimension_name})`);
  }

  getSuggestionsForDimensionValue(dimensionCode) {
    return this.dimensionValuesConfig
      .filter((row) => row.dimension_code === dimensionCode)
      .slice(0, 6)
      .map((row) => `${row.value} (${row.value_desc})`);
  }
}

module.exports = {
  EntityMapper,
};
