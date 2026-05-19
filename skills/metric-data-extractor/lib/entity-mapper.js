const { readCsv } = require('./csv-loader');

function normalize(value) {
  return String(value || '').trim().toLowerCase();
}

// 双向模糊匹配，但加非空 + 长度 guard：candidate 为空字符串 / 单字符时禁止
// 反向匹配（`target.includes(candidate)`）—— 否则 dimension-values.csv 里
// value_desc 留空的行会让所有 mapDimensionValue 返回首条空行，"问界M7" 被
// 错误匹配到"抖音"是真实暴露过的 bug。candidate 至少 2 字才参与反向。
function fuzzyContains(target, candidate) {
  const c = normalize(candidate);
  if (!c) return false;
  if (c.includes(target)) return true;
  if (c.length >= 2 && target.includes(c)) return true;
  return false;
}

class EntityMapper {
  constructor() {
    this.metricsConfig = readCsv('config/metrics.csv').filter((row) => row.metric_code);
    this.dimensionsConfig = readCsv('config/dimensions.csv').filter((row) => row.dimension_code);
    this.dimensionValuesConfig = readCsv('config/dimension-values.csv').filter((row) => row.dimension_code);
  }

  _isEnum(dimensionCode) {
    for (const row of this.dimensionsConfig) {
      if (row.dimension_code === dimensionCode) {
        return (row.dimension_type || '').trim().toLowerCase() === 'enum';
      }
    }
    return false;
  }

  // 只有 enum 和 semi-enum 需要 CSV 值校验。string/id/date/package 直接透传。
  _needsValueCheck(dimensionCode) {
    for (const row of this.dimensionsConfig) {
      if (row.dimension_code === dimensionCode) {
        const t = (row.dimension_type || '').trim().toLowerCase();
        return t === 'enum' || t === 'semi-enum';
      }
    }
    return true; // 未知维度保守校验
  }

  getAllDimensionValues(dimensionCode) {
    return this.dimensionValuesConfig
      .filter((row) => row.dimension_code === dimensionCode)
      .map((row) => `${row.value} (${row.value_desc || ''})`);
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
      // 支持维度和指标两种过滤：先试维度，再试指标
      let mappedKey = this.mapDimension(key);
      let isMetricFilter = false;
      if (mappedKey.error) {
        const mappedMetric = this.mapMetric(key);
        if (!mappedMetric.error) {
          mappedKey = mappedMetric;
          isMetricFilter = true;
        } else {
          result.errors.push({
            field: 'filter',
            value: key,
            message: `过滤条件 "${key}" 无法识别（不是维度也不是指标）`,
            suggestions: [
              ...this.getSuggestionsForDimension().map(s => `维度: ${s}`),
              ...this.getSuggestionsForMetric().map(s => `指标: ${s}`),
            ],
          });
          continue;
        }
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

      // 仅"精确值匹配"类运算符需要去 dimension-values.csv 校验值合法性。
      // LIKE/NOT LIKE 是通配符 pattern（`%foo%`）、GT/LT 是数值阈值、
      // START WITH/END WITH 是前后缀、IS NULL/NOT NULL 不需要值——
      // 这些走值校验只会全部失败（pattern / 数值不在合法值列表）。
      // 之前所有运算符无差别校验，导致用户文档里推荐的 `like:%新车%` /
      // `cost=gt:1000` 等查询全部被 mapper 当 invalid 拒绝。
      const EXACT_OPERS = new Set(['EQUAL', 'NOT EQUAL', 'IN', 'NOT IN']);
      const NULL_OPERS = new Set(['IS NULL', 'IS NOT NULL']);
      const needsValueCheck = EXACT_OPERS.has(inputOper);
      const isNullOper = NULL_OPERS.has(inputOper);

      let resolvedValues;
      if (needsValueCheck && !isMetricFilter && this._needsValueCheck(mappedKey.value)) {
        resolvedValues = [];
        for (const value of valueList) {
          const mappedValue = this.mapDimensionValue(mappedKey.value, value);
          if (mappedValue.error) {
            // enum 维度：值集合穷举，直接列全集；非 enum：先 fuzzy 再 top-6
            const suggestions = this._isEnum(mappedKey.value)
              ? this.getAllDimensionValues(mappedKey.value)
              : ((mappedValue.fuzzySuggestions && mappedValue.fuzzySuggestions.length)
                ? mappedValue.fuzzySuggestions
                : this.getSuggestionsForDimensionValue(mappedKey.value));
            result.errors.push({
              field: 'filter_value',
              dimension: mappedKey.value,
              value,
              message: `维度 "${mappedKey.value}" 的值 "${value}" 无法识别`,
              suggestions,
            });
            continue;
          }
          resolvedValues.push(mappedValue.valueCode);
        }
      } else {
        // pattern / 数值 / 空值：直接透传给 API，不做合法值校验
        resolvedValues = valueList.slice();
      }

      // 空值运算符即使 valueList 空也要保留——它的语义就是不需要值
      if (resolvedValues.length > 0 || isNullOper) {
        result.filters[mappedKey.value] = {
          source: mappedKey.value,
          oper: inputOper,
          targetValue: resolvedValues,
        };
      }
    }

    return result;
  }

  mapMetric(input) {
    const target = normalize(input);

    // 三遍扫描，优先级 code/name 精确 > alias > fuzzy。
    // alias 检查**必须晚于全表 code/name 精确扫**——否则 row 1 的 alias 会抢跑
    // row 2 的精确 code/name 匹配（参考 mapDimensionValue 注释里华为浏览器案例）。

    // 第 1 遍：metric_code / metric_name 精确匹配
    for (const row of this.metricsConfig) {
      if (normalize(row.metric_code) === target || normalize(row.metric_name) === target) {
        return { value: row.metric_code };
      }
    }

    // 第 2 遍：metric_aliases 匹配（CSV 列，逗号或全角逗号分隔——
    // 单一数据源，别名在 CSV 里更新即生效，不再依赖代码里 hardcoded 的 alias 表）
    for (const row of this.metricsConfig) {
      const aliases = String(row.metric_aliases || '')
        .split(/[,，]/)
        .map((a) => normalize(a))
        .filter(Boolean);
      if (aliases.includes(target)) {
        return { value: row.metric_code };
      }
    }

    // 第 3 遍：模糊匹配（精确 + alias 都未命中时启用）
    for (const row of this.metricsConfig) {
      if (fuzzyContains(target, row.metric_name)) {
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

    // pt_d / reqDay 是时间维度标识（事件时间 / 请求时间口径），由 DSLBuilder
    // 决定如何放进最终请求（pt_d 走 timingDimension；reqDay 直接进 dimensions
    // 数组）。粒度概念（week/month）已废——新 API 协议下时间维度只有这两个。
    const timingDimensions = ['pt_d', 'reqDay'];
    if (timingDimensions.includes(target)) {
      return { value: target };
    }

    const aliasMap = {
      日期: 'pt_d',
      天: 'pt_d',
      按天: 'pt_d',
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

    // 三遍扫描，优先级 code/name 精确 > dimension_aliases > fuzzy。
    // 第 1 遍：code / name 精确
    for (const row of this.dimensionsConfig) {
      if (normalize(row.dimension_code) === target || normalize(row.dimension_name) === target) {
        return { value: row.dimension_code };
      }
    }
    // 第 2 遍：dimension_aliases 列（避免 row 1 alias 抢跑 row 2 code/name 精确匹配）
    for (const row of this.dimensionsConfig) {
      const aliases = String(row.dimension_aliases || '')
        .split(/[,，]/)
        .map((a) => normalize(a))
        .filter(Boolean);
      if (aliases.includes(target)) {
        return { value: row.dimension_code };
      }
    }
    // 第 3 遍：fuzzy
    for (const row of this.dimensionsConfig) {
      if (fuzzyContains(target, row.dimension_name)) {
        return { value: row.dimension_code };
      }
    }

    return { error: true };
  }

  mapDimensionValue(dimensionCode, input) {
    const target = normalize(input);
    const candidates = this.dimensionValuesConfig.filter((row) => row.dimension_code === dimensionCode);

    // **职责单一：只做精确校验，不做别名翻译/群组扩展**。
    // 业务背景：脏数据下同一实体多名（华为浏览器 / HUAWEI Browser）的合并查询
    // **不在这里做**——它是 LLM 的策略选择，由 LLM 通过 search-dimension-values
    // 拿到候选别名后显式 `IN` 多值传入。query-metrics 做精确校验，校验不过时
    // 错误信息里用 fuzzy 把候选列出，给 LLM 自己纠错的钩子。
    //
    // 优先级 value/value_desc 精确 > alias 精确：
    // alias 必须晚于全表 value 精确扫——否则 row 1 的 alias 会抢跑 row 2 的
    // 精确 value 匹配（华为浏览器/HUAWEI Browser 案例）。
    const parseAliases = (raw) =>
      String(raw || '').split(/[,，]/).map((v) => normalize(v)).filter(Boolean);

    // 第 1 遍：value / value_desc 精确
    for (const row of candidates) {
      if (normalize(row.value) === target || normalize(row.value_desc) === target) {
        return { valueCode: row.value, valueName: row.value_desc };
      }
    }
    // 第 2 遍：value_aliases 精确
    for (const row of candidates) {
      if (parseAliases(row.value_aliases).includes(target)) {
        return { valueCode: row.value, valueName: row.value_desc };
      }
    }

    // 精确未命中——用 fuzzy 找候选返给 caller 当 suggestions
    const fuzzyMatches = [];
    for (const row of candidates) {
      if (fuzzyContains(target, row.value_desc) || fuzzyContains(target, row.value)) {
        fuzzyMatches.push(`${row.value} (${row.value_desc || ''})`);
        if (fuzzyMatches.length >= 6) break;
      }
    }

    return {
      error: true,
      fuzzySuggestions: fuzzyMatches,
    };
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
