#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { parse } = require('csv-parse/sync');

function main() {
  try {
    const dimensionsPath = path.resolve(__dirname, '../config/dimensions.csv');
    let content = fs.readFileSync(dimensionsPath, 'utf-8');

    // 移除BOM字符（如果存在）
    if (content.charCodeAt(0) === 0xFEFF) {
      content = content.slice(1);
    }

    const records = parse(content, {
      columns: true,
      skip_empty_lines: true,
    });

    // 直接输出表格格式
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
