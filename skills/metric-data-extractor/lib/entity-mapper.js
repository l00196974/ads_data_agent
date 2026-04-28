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

      const values = Array.isArray(rawValue) ? rawValue : [rawValue];
      const resolvedValues = [];

      for (const value of values) {
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

      if (resolvedValues.length > 0) {
        result.filters[mappedDimension.value] = {
          source: mappedDimension.value,
          oper: 'EQUAL',
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

      if (normalize(row.metric_name).includes(target) || target.includes(normalize(row.metric_name))) {
        return { value: row.metric_code };
      }
    }

    const aliasMap = {
      消耗: 'cost',
      花费: 'cost',
      成本: 'cost',
      线索: 'leads',
      曝光: 'impressions',
      展现: 'impressions',
      点击: 'clicks',
      点击率: 'ctr',
      转化率: 'cvr',
      转化: 'conversions',
    };

    if (aliasMap[input]) {
      return { value: aliasMap[input] };
    }

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
