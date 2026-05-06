#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { parse } = require('csv-parse/sync');

function main() {
  try {
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

    // 直接输出表格格式
    console.log(`## 可用指标列表 (共${records.length}个)`);
    console.log('');
    console.log('| 指标代码 | 指标名称 | 分类 | 说明 | 别名 |');
    console.log('|---------|---------|------|------|------|');

    records.forEach((r) => {
      const aliases = r.metric_aliases || '';
      const category = r.category_level1 || '';
      console.log(`| ${r.metric_code} | ${r.metric_name} | ${category} | ${r.metric_desc || ''} | ${aliases} |`);
    });

  } catch (error) {
    console.error(`错误: ${error.message}`);
    process.exit(1);
  }
}

main();
