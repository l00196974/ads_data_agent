#!/usr/bin/env node
const path = require('path');
const fs = require('fs');
const { parseArgs, printJsonError } = require('../lib/arg-parser');
const { EntityMapper } = require('../lib/entity-mapper');
const { DSLBuilder } = require('../lib/dsl-builder');
const { HuaweiAdsClient } = require('../lib/api-client');
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

function toEchartsDataset(payload) {
  const rows = Array.isArray(payload.data) ? payload.data : [];
  if (rows.length === 0) {
    return { dataset: { dimensions: [], source: [] }, total: 0 };
  }
  const dimensions = Object.keys(rows[0]);
  const source = [dimensions, ...rows.map(row => dimensions.map(d => row[d]))];
  return { dataset: { dimensions, source }, total: payload.total || rows.length };
}

function parseFilters(rawFilters) {
  if (!rawFilters) {
    return {};
  }

  // 支持两种格式：
  // 1. JSON 格式（向后兼容）: '{"promotionTarget": ["问界M7"]}'
  // 2. 键值对格式（推荐）: 'promotionTarget=问界M7,问界M9;mediaName=抖音'

  // 尝试 JSON 格式
  if (rawFilters.trim().startsWith('{')) {
    try {
      return JSON.parse(rawFilters);
    } catch (_error) {
      throw new Error('filters JSON 格式错误');
    }
  }

  // 解析键值对格式: dimension1=value1,value2;dimension2=value3
  const filters = {};
  const pairs = rawFilters.split(';').map(s => s.trim()).filter(Boolean);

  for (const pair of pairs) {
    const [key, values] = pair.split('=').map(s => s.trim());
    if (!key || !values) {
      throw new Error(`filters 格式错误: ${pair}，正确格式: dimension=value1,value2`);
    }
    filters[key] = values.split(',').map(v => v.trim()).filter(Boolean);
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

  for (const error of valueErrors) {
    const candidates = await searcher.search(error.dimension, error.value, 1);
    if (candidates.length === 0 || candidates[0].similarity < 0.5) {
      return null;
    }

    const originalKey = Object.keys(fixedFilters).find((key) => key === error.dimension || key === error.dimension || key === '推广对象' || key === '渠道' || key === '落地页' || key === '设备');
    if (originalKey) {
      fixedFilters[originalKey] = candidates[0].value_code;
    }
  }

  return fixedFilters;
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

    const requestBody = new DSLBuilder().build({
      indicators: mappingResult.metrics,
      dimensions: mappingResult.dimensions,
      filters: mappingResult.filters,
      startDate: args.startDate,
      endDate: args.endDate,
      timeMode: args.timeMode,
    });

    const cfg = loadConfig();
    const client = new HuaweiAdsClient({
      baseUrl: cfg.METRICS_API_URL,
      appId: cfg.HUAWEI_ADS_APP_ID,
      secret: cfg.HUAWEI_ADS_SECRET,
    });

    const response = await client.query(requestBody);
    const payload = response.data.data;

    if (Array.isArray(payload.data) && payload.data.length > 1000) {
      payload.data = payload.data.slice(0, 1000);
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
