#!/usr/bin/env node
const { readCsv } = require('../lib/csv-loader');

function main() {
  try {
    // 复用 lib/csv-loader，保证 BOM / 缺失文件错误处理 与 entity-mapper 等模块一致
    const records = readCsv('config/dimensions.csv');

    console.log(`## 可用维度列表 (共${records.length}个)`);
    console.log('');
    console.log('| 维度代码 | 维度名称 | 类型 | 说明 | 别名 |');
    console.log('|---------|---------|------|------|------|');

    records.forEach((r) => {
      const aliases = r.dimension_aliases || '';
      const type = r.dimension_type || 'string';
      console.log(`| ${r.dimension_code} | ${r.dimension_name} | ${type} | ${r.dimension_desc || ''} | ${aliases} |`);
    });

  } catch (error) {
    console.error(`错误: ${error.message}`);
    process.exit(1);
  }
}

main();
