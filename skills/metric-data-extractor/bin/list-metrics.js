#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { parse } = require('csv-parse/sync');
const { parseArgs } = require('../lib/arg-parser');

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const format = args.format || 'table'; // 默认表格格式，支持 'json' 格式

    const metricsPath = path.resolve(__dirname, '../config/metrics.csv');
    let content = fs.readFileSync(metricsPath, 'utf-8');

    // 移除BOM字符（如果存在）
    if (content.charCodeAt(0) === 0xFEFF) {
      content = content.slice(1);
    }

    const records = parse(content, {
      columns: true,
      skip_empty_lines: true,
    });

    if (format === 'json') {
      // JSON格式输出（用于Agent系统）
      const result = {
        total: records.length,
        metrics: records.map((r) => ({
          code: r.metric_code,
          name: r.metric_name,
          description: r.metric_desc,
          category_level1: r.category_level1 || '',
          category_level2: r.category_level2 || '',
          aliases: r.metric_aliases ? r.metric_aliases.split(',').map(s => s.trim()) : [],
          supported_dimensions: r.supported_dimensions ? r.supported_dimensions.split(',').map(s => s.trim()) : []
        })),
      };
      console.log(JSON.stringify(result, null, 2));
    } else {
      // 表格格式输出（用于命令行）
      console.log(`## 可用指标列表 (共${records.length}个)`);
      console.log('');
      console.log('| 指标代码 | 指标名称 | 分类 | 说明 | 别名 |');
      console.log('|---------|---------|------|------|------|');

      records.forEach((r) => {
        const aliases = r.metric_aliases || '';
        const category = r.category_level1 || '';
        console.log(`| ${r.metric_code} | ${r.metric_name} | ${category} | ${r.metric_desc || ''} | ${aliases} |`);
      });
    }

  } catch (error) {
    if (args && args.format === 'json') {
      console.error(JSON.stringify({ error: error.message }, null, 2));
    } else {
      console.error(`错误: ${error.message}`);
    }
    process.exit(1);
  }
}

main();
