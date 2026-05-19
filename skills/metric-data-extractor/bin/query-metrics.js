#!/usr/bin/env node
const path = require('path');
const fs = require('fs');
const { parseArgs, printJsonError } = require('../lib/arg-parser');
const { EntityMapper } = require('../lib/entity-mapper');
const { DSLBuilder } = require('../lib/dsl-builder');
const { HuaweiAdsClient, WisedataApiClient } = require('../lib/api-client');
const { SemanticSearch } = require('../lib/semantic-search');

function loadConfig() {
  const configPath = path.join(__dirname, '..', 'config.json');
  try {
    return JSON.parse(fs.readFileSync(configPath, 'utf-8'));
  } catch (_err) {
    return {
      METRICS_API_URL: process.env.METRICS_API_URL || 'https://wo-drcn.dbankcloud.cn',
      HUAWEI_ADS_APP_ID: process.env.HUAWEI_ADS_APP_ID || '',
      HUAWEI_ADS_SECRET: process.env.HUAWEI_ADS_SECRET || '',
    };
  }
}

// 工具自动截断阈值——保护 LLM 上下文不被一次返回的大数据集爆掉。
// 上层 backend 还有 ToolOutputTruncationMiddleware（默认 5000 字节）做最终兜底，
// 但那是粗暴卸盘；这里在数据结构层级先截，能把"被截断"的信号语义化传给 LLM。
const MAX_ROWS_RETURNED = 1000;

function toEchartsDataset(payload) {
  const rows = Array.isArray(payload.data) ? payload.data : [];
  if (rows.length === 0) {
    return { dataset: { dimensions: [], source: [] }, total: 0 };
  }
  const dimensions = Object.keys(rows[0]);
  const source = [dimensions, ...rows.map(row => dimensions.map(d => row[d]))];
  const out = { dataset: { dimensions, source }, total: payload.total || rows.length };
  // 透传截断信号：让 LLM 知道"还有更多数据没拿到"，避免据此给出全量结论
  if (payload.truncated) {
    out.truncated = true;
    out.total_before_truncation = payload.total_before_truncation;
  }
  return out;
}

// 运算符映射表。
// 键必须是**单 token**（无空格）—— parseFilterValue 用 /^(\w+):(.*)$/ 提取 oper，
// `\w` 不匹配空格，所以 'not like' / 'not in' / 'not null' 这种带空格的 oper
// 名永远拿不到。统一用紧凑写法：notlike / notin / notnull。
const OPERATOR_MAP = {
  // 列表运算符
  'in': 'IN',
  'notin': 'NOT IN',
  // 模糊匹配运算符
  'like': 'LIKE',
  'notlike': 'NOT LIKE',
  'sw': 'START WITH',
  'ew': 'END WITH',
  'nsw': 'NOT START WITH',
  'new': 'NOT END WITH',
  // 比较运算符
  'eq': 'EQUAL',
  'neq': 'NOT EQUAL',
  'gt': 'GREATER THAN',
  'gte': 'GREATER THAN OR EQUAL',
  'lt': 'LESS THAN',
  'lte': 'LESS THAN OR EQUAL',
  // 空值运算符
  'null': 'IS NULL',
  'notnull': 'IS NOT NULL',
};

/**
 * 解析单个过滤条件值
 * 格式: dimension=oper:value 或 dimension=value1,value2
 */
function parseFilterValue(key, valueStr) {
  // 检查是否包含运算符前缀 (格式: oper:value 或 oper:)
  // 使用 .* 而不是 .+ 以支持空值 (null:)
  const operMatch = valueStr.match(/^(\w+):(.*)$/);

  if (operMatch) {
    const [, oper, valuesPart] = operMatch;
    const normalizedOper = oper.toLowerCase();
    const apiOper = OPERATOR_MAP[normalizedOper];

    if (!apiOper) {
      throw new Error(`filters 中 dimension "${key}" 的运算符 "${oper}" 不支持`);
    }

    // 特殊处理空值运算符
    if (apiOper === 'IS NULL' || apiOper === 'IS NOT NULL') {
      return {
        oper: apiOper,
        values: [],
      };
    }

    // 解析值（允许空字符串）
    const values = valuesPart.split(',').map(v => v.trim()).filter(Boolean);
    return {
      oper: apiOper,
      values,
    };
  }

  // 默认使用 EQUAL 运算符
  return {
    oper: 'EQUAL',
    values: valueStr.split(',').map(v => v.trim()).filter(Boolean),
  };
}

function parseFilters(rawFilters) {
  if (!rawFilters) {
    return {};
  }

  // 支持两种格式：
  // 1. JSON 格式（向后兼容）:
  //    '{"promotionTarget": ["问界M7"]}' - 简单值，等于 IN
  //    '{"promotionTarget": {"oper": "IN", "values": ["问界M7", "问界M9"]}}' - 完整格式
  // 2. 键值对格式（推荐）: 'promotionTarget=问界M7,问界M9;mediaName=in:抖音,快手'

  // 尝试 JSON 格式
  if (rawFilters.trim().startsWith('{')) {
    try {
      const parsed = JSON.parse(rawFilters);
      const result = {};

      for (const [key, value] of Object.entries(parsed)) {
        if (Array.isArray(value)) {
          // 简单数组格式: {"promotionTarget": ["问界M7", "问界M9"]}
          result[key] = {
            oper: 'IN',
            values: value,
          };
        } else if (typeof value === 'object' && value !== null) {
          // 完整对象格式: {"promotionTarget": {"oper": "IN", "values": [...]}}
          result[key] = {
            oper: value.oper || 'EQUAL',
            values: value.values || [],
          };
        } else {
          // 单值格式: {"promotionTarget": "问界M7"}
          result[key] = {
            oper: 'EQUAL',
            values: [value],
          };
        }
      }

      return result;
    } catch (_error) {
      throw new Error('filters JSON 格式错误');
    }
  }

  // 解析键值对格式: dimension1=value1,value2;dimension2=oper:value3
  const filters = {};
  const pairs = rawFilters.split(';').map(s => s.trim()).filter(Boolean);

  for (const pair of pairs) {
    const equalIndex = pair.indexOf('=');
    if (equalIndex === -1) {
      throw new Error(`filters 格式错误: ${pair}，正确格式: dimension=值 或 dimension=oper:值`);
    }

    const key = pair.substring(0, equalIndex).trim();
    const valueStr = pair.substring(equalIndex + 1).trim();

    if (!key || !valueStr) {
      throw new Error(`filters 格式错误: ${pair}，正确格式: dimension=值 或 dimension=oper:值`);
    }

    filters[key] = parseFilterValue(key, valueStr);
  }

  return filters;
}

function parseList(value) {
  if (!value) {
    return [];
  }

  return String(value)
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

async function trySemanticFix(errors, originalFilters) {
  const valueErrors = errors.filter((error) => error.field === 'filter_value');
  if (valueErrors.length === 0) {
    return null;
  }

  const fixedFilters = JSON.parse(JSON.stringify(originalFilters || {}));
  const searcher = new SemanticSearch();
  await searcher.initialize();

  const mapper = new EntityMapper();
  const codeToOriginalKey = {};
  for (const key of Object.keys(fixedFilters)) {
    const mapped = mapper.mapDimension(key);
    if (!mapped.error) codeToOriginalKey[mapped.value] = key;
  }

  let anyFixed = false;
  for (const error of valueErrors) {
    // enum 维度在 mapper 里已经给了全集，不需要再做语义搜索
    if (mapper.isEnum(error.dimension)) continue;

    // topK=5：既用于自动纠错，也用于丰富 error.suggestions
    const candidates = await searcher.search(error.dimension, error.value, 5);
    if (candidates.length === 0) continue;

    // 用语义搜索结果替换 dumb top-6，LLM 看到的就是跟输入相关的候选
    error.suggestions = candidates.map(
      (c) => `${c.value} (${c.value_desc || ''})`,
    );

    // 自动纠错：top-1 相似度 ≥ 0.5 则替换
    if (candidates[0].similarity >= 0.5) {
      const originalKey = codeToOriginalKey[error.dimension];
      if (originalKey && fixedFilters[originalKey] && Array.isArray(fixedFilters[originalKey].values)) {
        fixedFilters[originalKey].values = [candidates[0].value];
        anyFixed = true;
      }
    }
  }

  return anyFixed ? fixedFilters : null;
}

async function main() {
  try {
    const args = parseArgs(process.argv.slice(2));

    if (!args.metrics || !args.startDate || !args.endDate || !args.timeMode) {
      printJsonError('缺少必需参数', {
        usage: 'query-metrics --metrics <list> --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> --time-mode <event|request> [--dimensions <list>] [--filters <json>]',
      });
      process.exit(1);
    }

    if (args.timeMode !== 'event' && args.timeMode !== 'request') {
      printJsonError('--time-mode 参数必须是 event 或 request', {
        usage: 'query-metrics --metrics <list> --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> --time-mode <event|request> [--dimensions <list>] [--filters <json>]',
      });
      process.exit(1);
    }

    const mapper = new EntityMapper();
    const originalFilters = parseFilters(args.filters);
    let mappingResult = mapper.map({
      metrics: parseList(args.metrics),
      dimensions: parseList(args.dimensions),
      filters: originalFilters,
    });

    if (mappingResult.errors.length > 0) {
      const fixedFilters = await trySemanticFix(mappingResult.errors, originalFilters);
      if (fixedFilters) {
        mappingResult = mapper.map({
          metrics: parseList(args.metrics),
          dimensions: parseList(args.dimensions),
          filters: fixedFilters,
        });
      }
    }

    if (mappingResult.errors.length > 0) {
      const firstError = mappingResult.errors[0];
      printJsonError(firstError.message, {
        suggestions: firstError.suggestions || [],
        hint: '请使用建议的字段名重新查询，或使用 search-dimension-values 工具查找相似值',
      });
      process.exit(1);
    }

    // 优先使用 pageSize 参数，也兼容 topN 参数
    const pageSize = args.pageSize || args.topN;

    const requestBody = new DSLBuilder().build({
      indicators: mappingResult.metrics,
      dimensions: mappingResult.dimensions,
      filters: mappingResult.filters,
      startDate: args.startDate,
      endDate: args.endDate,
      timeMode: args.timeMode,
      pageSize: pageSize ? parseInt(pageSize, 10) : null,
      sortBy: args.sortBy || null,
      sortOrder: args.sortOrder || null,
    });

    const cfg = loadConfig();

    // 根据配置选择后端实现
    const backend = (cfg.METRICS_BACKEND || 'huawei-ads').toLowerCase();
    let client;

    if (backend === 'wisedata') {
      client = new WisedataApiClient({
        baseUrl: cfg.METRICS_API_URL,
      });
    } else {
      client = new HuaweiAdsClient({
        baseUrl: cfg.METRICS_API_URL,
        appId: cfg.HUAWEI_ADS_APP_ID,
        secret: cfg.HUAWEI_ADS_SECRET,
      });
    }

    // 调试日志：默认只打摘要（避免单次响应几千行 JSON 淹没 backend 日志 + tool_outputs 文件膨胀）
    // --debug 打开后输出完整 REQUEST/RESPONSE，排查问题时使用
    const debug = args.debug === true || args.debug === 'true' || args.debug === '1';
    if (debug) {
      console.error('=== REQUEST JSON ===');
      console.error(JSON.stringify(requestBody, null, 2));
    } else {
      console.error(`[query-metrics] indicators=${(requestBody.indicators || []).map(i => i.indicatorKey).join(',')} dims=${(requestBody.dimensions || []).join(',') || '-'} timing=${requestBody.timingDimension || '-'} filters=${(requestBody.filterConditions || []).length}`);
    }

    const response = await client.query(requestBody);
    const payload = response.data.data;

    if (debug) {
      console.error('=== RESPONSE JSON ===');
      console.error(JSON.stringify(payload, null, 2));
    } else {
      const rowCount = Array.isArray(payload.data) ? payload.data.length : 0;
      console.error(`[query-metrics] rows=${rowCount} total=${payload.total || rowCount}`);
    }

    if (Array.isArray(payload.data) && payload.data.length > MAX_ROWS_RETURNED) {
      payload.data = payload.data.slice(0, MAX_ROWS_RETURNED);
      payload.truncated = true;
      payload.total_before_truncation = response.data.data.total;
    }

    console.log(JSON.stringify(toEchartsDataset(payload), null, 2));
  } catch (error) {
    printJsonError(error.message);
    process.exit(1);
  }
}

main();
