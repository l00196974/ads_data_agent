#!/usr/bin/env node
const { readCsv } = require('../lib/csv-loader');

function main() {
  try {
    // 复用 lib/csv-loader，保证 BOM / 缺失文件错误处理 与 entity-mapper 等模块一致
    const records = readCsv('config/metrics.csv');

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
